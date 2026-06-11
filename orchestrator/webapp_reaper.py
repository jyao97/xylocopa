"""Kill port-webapp server processes when their presenting agent stops.

Design assumption (June 2026): each presented port app belongs to exactly
one agent — the one that launched the server and called webapp_present.
Live ports are NOT shared across chats; a new agent launches its own
instance of the viewer code. So when an agent reaches STOPPED/ERROR,
every process listening on a port that agent registered is terminated.

Registry rows are kept — the webapps table is an append-only catalog and
proxy allowlist; only the OS processes die. Safety guards: never touch
infrastructure ports, never signal processes owned by another uid.
"""

import logging
import os
import threading

import psutil

from config import PORT

logger = logging.getLogger("orchestrator")

# Never kill listeners on infrastructure ports, no matter what the
# registry says: the orchestrator itself and the frontend server.
_PROTECTED_PORTS = {PORT, 3000}

_TERM_GRACE_SECONDS = 3


def _listener_procs(port: int) -> list[psutil.Process]:
    """Processes listening on `port` (pid resolvable, i.e. same-uid)."""
    try:
        conns = psutil.net_connections(kind="tcp")
    except psutil.Error as e:
        logger.warning("webapp reaper: net_connections failed: %s", e)
        return []
    pids = {c.pid for c in conns
            if c.status == psutil.CONN_LISTEN and c.laddr
            and c.laddr.port == port and c.pid}
    procs = []
    for pid in pids:
        try:
            procs.append(psutil.Process(pid))
        except psutil.Error:
            pass
    return procs


def _kill_port(port: int) -> list[str]:
    """SIGTERM same-uid listeners on `port` (and their children); SIGKILL
    stragglers after a grace period from a daemon thread so callers never
    block. Returns a description per signalled listener."""
    if port in _PROTECTED_PORTS:
        logger.warning("webapp reaper: refusing to touch protected port %d", port)
        return []
    me = os.getpid()
    my_uid = os.getuid()
    killed: list[str] = []
    victims: list[psutil.Process] = []
    for proc in _listener_procs(port):
        try:
            if proc.pid == me:
                continue
            if proc.uids().real != my_uid:
                logger.info(
                    "webapp reaper: port %d pid %d owned by uid %d — skipping",
                    port, proc.pid, proc.uids().real)
                continue
            group = [proc] + proc.children(recursive=True)
            desc = f"pid={proc.pid} {' '.join(proc.cmdline()[:4])[:120]}"
            for p in group:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
            victims.extend(group)
            killed.append(desc)
            logger.info("webapp reaper: SIGTERM port %d %s", port, desc)
        except psutil.Error as e:
            logger.info("webapp reaper: port %d inspection failed: %s", port, e)

    if victims:
        def _finish(procs=victims):
            _, alive = psutil.wait_procs(procs, timeout=_TERM_GRACE_SECONDS)
            for p in alive:
                try:
                    p.kill()
                    logger.info(
                        "webapp reaper: SIGKILL pid %d (ignored SIGTERM)", p.pid)
                except psutil.Error:
                    pass
        threading.Thread(target=_finish, daemon=True,
                         name=f"webapp-reaper-{port}").start()
    return killed


def _reap_rows(rows) -> list[int]:
    reaped = []
    for row in rows:
        try:
            port = int(row.target)
        except (TypeError, ValueError):
            continue
        if _kill_port(port):
            reaped.append(port)
    return reaped


def reap_agent_webapps(db, agent_id: str) -> list[int]:
    """Kill the port services registered by this agent.

    Called from the STOPPED/ERROR cleanup paths. Returns reaped ports.
    """
    from models import WebApp
    rows = db.query(WebApp).filter(
        WebApp.created_by_agent == agent_id,
        WebApp.kind == "port",
    ).all()
    reaped = _reap_rows(rows)
    if reaped:
        logger.info("webapp reaper: agent %s stopped — reaped ports %s",
                    agent_id[:8], reaped)
    return reaped


def reap_orphan_webapps(db) -> list[int]:
    """Startup sweep: kill port services whose creator agent is already
    STOPPED/ERROR (or unknown) — covers stop events missed while the
    server was down and apps presented before reaping existed.
    """
    from models import Agent, AgentStatus, WebApp
    rows = (
        db.query(WebApp)
        .outerjoin(Agent, Agent.id == WebApp.created_by_agent)
        .filter(WebApp.kind == "port")
        .filter(
            (Agent.id.is_(None))
            | (Agent.status.in_([AgentStatus.STOPPED, AgentStatus.ERROR]))
        )
        .all()
    )
    reaped = _reap_rows(rows)
    if reaped:
        logger.info("webapp reaper: startup sweep reaped orphan ports %s", reaped)
    return reaped
