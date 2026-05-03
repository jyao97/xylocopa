"""Xylocopa — FastAPI entry point."""

import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager

# Clear Claude Code nesting-detection vars from the orchestrator process
# so spawned agents don't refuse to start.
os.environ.pop("CLAUDECODE", None)
os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)

import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from sqlalchemy.orm import Session

from config import CORS_ORIGINS, PROJECT_CONFIGS_PATH, CC_MODEL, VALID_MODELS
from database import SessionLocal, get_db, init_db
from log_config import setup_logging
from models import Agent, Project, Task, TaskStatus, AgentStatus
from auth import get_jwt_secret, get_password_hash, verify_token

setup_logging()
logger = logging.getLogger("orchestrator")

# Frontend debug logger — writes to a dedicated file for easy tailing
_fe_handler = logging.FileHandler(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "frontend-debug.log")
)
_fe_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logging.getLogger("frontend.debug").addHandler(_fe_handler)
logging.getLogger("frontend.debug").setLevel(logging.DEBUG)


# ---- Registry loader ----

def load_registry(db: Session):
    """Load projects from registry.yaml into database."""
    registry_path = os.path.join(PROJECT_CONFIGS_PATH, "registry.yaml")
    if not os.path.exists(registry_path):
        logger.warning("registry.yaml not found at %s", registry_path)
        return

    with open(registry_path) as f:
        data = yaml.safe_load(f)

    projects = data.get("projects") or []
    if not projects:
        logger.info("No projects in registry.yaml")
        return

    _valid_name = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    for p in projects:
        pname = p.get("name", "")
        if not pname or not _valid_name.match(pname) or "/" in pname or "\\" in pname:
            logger.warning("Skipping project with invalid name: %r", pname)
            continue
        # Validate model name — fall back to global default if invalid
        raw_model = p.get("default_model", CC_MODEL)
        if raw_model not in VALID_MODELS:
            logger.warning(
                "Project %r has invalid default_model %r, using %s",
                pname, raw_model, CC_MODEL,
            )
            raw_model = CC_MODEL

        existing = db.get(Project, p["name"])
        if existing:
            existing.display_name = p.get("display_name", p["name"])
            existing.path = p.get("path", f'/projects/{p["name"]}')
            existing.git_remote = p.get("git_remote")
            existing.description = p.get("description")
            existing.max_concurrent = p.get("max_concurrent", 8)
            existing.default_model = raw_model
        else:
            db.add(Project(
                name=p["name"],
                display_name=p.get("display_name", p["name"]),
                path=p.get("path", f'/projects/{p["name"]}'),
                git_remote=p.get("git_remote"),
                description=p.get("description"),
                max_concurrent=p.get("max_concurrent", 8),
                default_model=raw_model,
            ))
    db.commit()
    logger.info("Loaded %d projects from registry.yaml", len(projects))


# ---- One-shot migration: pre_sent legacy rows ----

def _migrate_pre_sent_legacy():
    """Clean up pre-cutover DB rows that no longer belong in `messages`.

    Pre-Phase-2 code created DB rows for SENT/CANCELLED web/task/
    plan_continue messages. Post-Phase-2 those states live in the display
    file's pre-sent zone (no DB row) or, once dispatched, as COMPLETED
    rows. This migration reconciles residue.

    PENDING rows are pre-cleaned by database.py on enum load (the
    MessageStatus.PENDING value was removed); this function only
    handles SENT and CANCELLED residue.

    Rules:
      - SENT with delivered_at set: was actually delivered, legacy status
        is stale; flip to COMPLETED, keep row.
      - SENT without delivered_at (never confirmed): move to pre_sent
        zone; delete row. CC may have received the message but we have no
        UserPromptSubmit confirmation — user can re-send if needed.
      - CANCELLED with display_seq: was delivered then cancelled (historical
        quirk); flip to COMPLETED to honor the "DB only holds delivered"
        invariant; display-file tombstone already hides the bubble.
      - CANCELLED without display_seq: pure pre-sent cancel; display
        file already has the tombstone; just delete the row.

    Idempotent. Runs on every startup; a clean DB makes it a no-op.
    """
    import json
    from models import Message, MessageRole, MessageStatus
    from display_writer import pre_sent_create

    db = SessionLocal()
    migrated_pre = 0
    fixed_completed = 0
    deleted_cancelled = 0
    try:
        legacy = (
            db.query(Message)
            .filter(
                Message.source.in_(("web", "task", "plan_continue")),
                Message.status.in_((
                    MessageStatus.SENT,
                    MessageStatus.CANCELLED,
                )),
            )
            .all()
        )
        for msg in legacy:
            try:
                if msg.status == MessageStatus.CANCELLED:
                    if msg.display_seq is not None:
                        msg.status = MessageStatus.COMPLETED
                        if not msg.completed_at:
                            msg.completed_at = msg.delivered_at
                        fixed_completed += 1
                    else:
                        db.delete(msg)
                        deleted_cancelled += 1
                    continue

                # SENT
                # NOTE: removed "SENT + delivered_at + display_seq → COMPLETED"
                # branch (was here, deleted 2026-04-30). It paired with the
                # database.py blind delivered_at backfill to fabricate
                # delivered status for messages sync_engine never confirmed
                # via JSONL — produced 12 ghost-delivered messages over an
                # 8-day window. Verified zero call rate via GHOST_PROBE
                # telemetry across 52 restarts before deletion.
                #
                # All SENT rows now fall through to the pre_sent_zone rescue
                # below: the row goes back to the pre-sent file so the user
                # can see it as "queued" and re-send if needed. status=COMPLETED
                # is now exclusively written by sync_engine on JSONL match.

                # Move to pre_sent zone.
                entry_status = "scheduled" if msg.scheduled_at else "queued"
                metadata = None
                if msg.meta_json:
                    try:
                        metadata = json.loads(msg.meta_json)
                    except (ValueError, TypeError):
                        metadata = None
                entry = {
                    "id": msg.id,
                    "role": "USER",
                    "content": msg.content or "",
                    "source": msg.source,
                    "status": entry_status,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    "scheduled_at": (
                        msg.scheduled_at.isoformat() if msg.scheduled_at else None
                    ),
                    "metadata": metadata,
                }
                pre_sent_create(msg.agent_id, entry)
                db.delete(msg)
                migrated_pre += 1
            except Exception:
                logger.exception(
                    "Predelivery migration: failed for msg %s (agent %s)",
                    msg.id[:8], msg.agent_id[:8],
                )
        db.commit()
        if migrated_pre or fixed_completed or deleted_cancelled:
            logger.info(
                "Predelivery migration: moved=%d, completed=%d, cancelled-deleted=%d",
                migrated_pre, fixed_completed, deleted_cancelled,
            )
        else:
            logger.info("Predelivery migration: nothing to migrate")
    finally:
        db.close()


# ---- Lifespan ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    import socket

    port = int(os.environ.get("PORT", 8080))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            logger.error(
                "Port %d already in use — another instance may be running. "
                "Exiting to avoid conflicts.",
                port,
            )
            import sys
            sys.exit(1)

    logger.info("Xylocopa starting up...")
    _main_event_loop = asyncio.get_event_loop()

    # Anonymous daily heartbeat (opt-out). See orchestrator/telemetry.py.
    try:
        import telemetry
        telemetry.record_heartbeat()
    except Exception:
        logger.debug("Telemetry heartbeat failed (non-fatal)", exc_info=True)

    _check_frontend_dist_staleness()

    # One-time migration: rename legacy ~/.agenthive → ~/.xylocopa if needed
    try:
        _migrate_legacy_user_dirs()
    except Exception:
        logger.exception("Legacy path migration failed (non-fatal)")

    # Make the event loop available to routers that need it for background threads
    from routers import projects as _projects_router
    _projects_router._main_event_loop = _main_event_loop

    init_db()
    logger.info("Database initialized")

    # One-shot migration: move legacy pre-sent DB rows to pre_sent zone.
    # Pre-delivery web/task/plan_continue messages no longer own DB rows. Any
    # legacy PENDING/QUEUED/CANCELLED rows from before the cutover are
    # reconciled here. Idempotent — after the first successful run the SELECT
    # returns zero rows.
    try:
        _migrate_pre_sent_legacy()
    except Exception:
        logger.exception("Predelivery migration failed on startup")

    # Rebuild display files for active agents
    try:
        from display_writer import startup_rebuild_all
        startup_rebuild_all()
    except Exception:
        logger.exception("Failed to rebuild display files on startup")

    # Mark interrupted insight generations as failed
    try:
        from models import Agent
        _startup_db = SessionLocal()
        _stuck = _startup_db.query(Agent).filter(Agent.insight_status == "generating").all()
        for _a in _stuck:
            _a.insight_status = "failed"
            logger.info("Marked interrupted insight generation as failed for agent %s", _a.id)
        if _stuck:
            _startup_db.commit()
        _startup_db.close()
    except Exception:
        logger.exception("Failed to mark interrupted insight generations")

    # Prune zombie push subscriptions (never-acked + older than grace window)
    try:
        from routers.push import prune_zombie_subscriptions
        _push_db = SessionLocal()
        try:
            pruned = prune_zombie_subscriptions(_push_db)
            if pruned:
                logger.info("startup: pruned %d zombie push subscriptions", pruned)
        finally:
            _push_db.close()
    except Exception:
        logger.exception("startup: push-sub prune failed (non-fatal)")

    db = SessionLocal()
    try:
        load_registry(db)
        # Bootstrap the `.xylo-internal` placeholder project (host for AI
        # triage and other meta-agents).  Idempotent — creates the dir,
        # writes a self-contained .mcp.json + CLAUDE.md, and ensures the
        # DB row.  Must run before any meta-agent dispatch path resolves
        # its host project.
        try:
            from routers.projects import ensure_internal_project
            ensure_internal_project(db)
        except Exception:
            logger.exception("startup ensure_internal_project failed (non-fatal)")
        # Auto-replay any SessionStart events the hook stashed while we were
        # offline. Same logic the Agents page refresh button uses — surfaces
        # unlinked entries on backend restart without requiring a manual click.
        try:
            from routers.agents import _do_replay_pending_unlinked
            _do_replay_pending_unlinked(db)
        except Exception:
            logger.exception("startup replay_pending_unlinked failed (non-fatal)")
    finally:
        db.close()

    # Disable Claude Code session auto-cleanup
    from session_cache import ensure_cleanup_disabled
    ensure_cleanup_disabled()

    # Start dispatchers and git manager
    agent_dispatch_task = None
    backup_task = None
    session_cache_task = None
    from agent_dispatcher import AgentDispatcher
    from git_manager import GitManager
    from worker_manager import WorkerManager
    wm = WorkerManager()
    agent_dispatcher = AgentDispatcher(wm)
    gm = GitManager()
    from permissions import PermissionManager
    app.state.permission_manager = PermissionManager()
    app.state.agent_dispatcher = agent_dispatcher
    app.state.worker_manager = wm
    app.state.git_manager = gm
    agent_dispatch_task = asyncio.create_task(agent_dispatcher.run())
    logger.info("Dispatcher started")

    # Start session cache loop
    from session_cache import run_session_cache_loop
    session_cache_task = asyncio.create_task(
        run_session_cache_loop(agent_dispatcher.get_active_sessions)
    )

    # Install global SessionStart hook so ALL claude processes are detected
    from routers.agents import _write_global_session_hook
    _write_global_session_hook()

    # Refresh project-level hook configs (ensures new hook types are registered)
    from routers.agents import _write_agent_hooks_config, _write_mcp_config
    _db_hooks = SessionLocal()
    _project_paths = [
        p.path for p in _db_hooks.query(Project.path).distinct().all()
        if p.path and os.path.isdir(p.path)
    ]
    _db_hooks.close()
    for _pp in _project_paths:
        _write_agent_hooks_config(_pp)
        _write_mcp_config(_pp)
    if _project_paths:
        logger.info("Refreshed hook configs for %d projects", len(_project_paths))

    # Warm the per-project skills cache off-thread so the first /-trigger in
    # the picker doesn't pay disk-scan latency. Failures are non-fatal.
    def _warm_skills_cache():
        try:
            from skills import refresh_skills_cache
            n = refresh_skills_cache(_project_paths)
            logger.info("Warmed skills cache for %d entries", n)
        except Exception:
            logger.exception("Failed to warm skills cache (non-fatal)")

    import threading
    threading.Thread(target=_warm_skills_cache, name="skills-cache-warmup", daemon=True).start()

    # Recover tasks stuck in EXECUTING whose agent already stopped/errored
    from task_state import TaskStateMachine as _TSM
    _rdb = SessionLocal()
    try:
        _stuck_tasks = (
            _rdb.query(Task)
            .join(Agent, Task.agent_id == Agent.id)
            .filter(
                Task.status == TaskStatus.EXECUTING,
                Agent.status.in_([AgentStatus.STOPPED, AgentStatus.ERROR]),
            )
            .all()
        )
        for _st in _stuck_tasks:
            _agent = _rdb.get(Agent, _st.agent_id)
            if _agent and _agent.status == AgentStatus.ERROR:
                _TSM.transition(_st, TaskStatus.FAILED, strict=False)
            else:
                _TSM.transition(_st, TaskStatus.COMPLETE, strict=False)
        if _stuck_tasks:
            _rdb.commit()
            logger.info("Recovered %d stuck EXECUTING tasks at startup", len(_stuck_tasks))
    finally:
        _rdb.close()

    # Start backup loop
    from backup import run_backup_loop
    backup_task = asyncio.create_task(run_backup_loop())
    logger.info("Backup loop started")

    # Start WebSocket stale-connection pruning loop
    ws_prune_task = None
    from websocket import ws_manager

    async def _ws_prune_loop():
        while True:
            await asyncio.sleep(30)
            await ws_manager.prune_stale()

    ws_prune_task = asyncio.create_task(_ws_prune_loop())

    # Start session-viewing time-tracking loop
    from view_tracking import run_tick_loop as _view_tick
    view_track_task = asyncio.create_task(_view_tick())

    # Daily heartbeat for long-running orchestrators that never restart.
    # The Worker dedupes per-day for Discord, so exact alignment isn't needed.
    async def _daily_heartbeat_loop():
        import telemetry as _telemetry
        while True:
            await asyncio.sleep(86400)
            try:
                _telemetry.record_heartbeat()
            except Exception:
                logger.debug("Scheduled heartbeat failed (non-fatal)", exc_info=True)

    daily_heartbeat_task = asyncio.create_task(_daily_heartbeat_loop())

    # CC-session JSONL → cc_sessions reconcile.
    # One sweep at startup catches anything written while we were offline,
    # then a periodic background loop keeps the table in sync with on-disk
    # JSONL growth. Pure-additive: only inserts missing rows + bumps token
    # totals; never touches metadata.
    async def _cc_session_reconcile_loop():
        from cc_session_reconcile import reconcile_all
        # Initial sweep — runs in a thread so we don't stall the loop on
        # slow disk scans.
        try:
            totals = await asyncio.to_thread(reconcile_all)
            logger.info(
                "cc_session reconcile (startup): agents=%d disc=%d ins=%d upd=%d skp=%d",
                totals.get("agents", 0), totals.get("discovered", 0),
                totals.get("inserted", 0), totals.get("updated", 0),
                totals.get("skipped", 0),
            )
        except Exception:
            logger.exception("cc_session reconcile startup sweep failed (non-fatal)")
        # Periodic — every 30 minutes is plenty for a backstop reconcile;
        # the dispatcher writes rows live on rotation/end so this only
        # cleans up sessions the live writer missed.
        while True:
            await asyncio.sleep(1800)
            try:
                totals = await asyncio.to_thread(reconcile_all)
                if totals.get("inserted") or totals.get("updated"):
                    logger.info(
                        "cc_session reconcile: ins=%d upd=%d (disc=%d)",
                        totals["inserted"], totals["updated"],
                        totals.get("discovered", 0),
                    )
            except Exception:
                logger.exception("cc_session reconcile loop failed (non-fatal)")

    cc_session_reconcile_task = asyncio.create_task(_cc_session_reconcile_loop())

    yield

    # Shutdown
    for task in (agent_dispatch_task, backup_task, session_cache_task, ws_prune_task, view_track_task, daily_heartbeat_task, cc_session_reconcile_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Background task raised during shutdown")
    if agent_dispatch_task:
        agent_dispatcher.stop()
    logger.info("Xylocopa shutting down...")


def _check_frontend_dist_staleness():
    # If any frontend/src file is newer than dist/index.html, someone committed
    # but didn't rebuild — old build is still being served. This warning makes
    # that gap visible instead of silently shipping stale JS to every browser.
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    src = root / "frontend" / "src"
    dist_index = root / "frontend" / "dist" / "index.html"
    if not src.is_dir() or not dist_index.is_file():
        return
    try:
        src_mtime = max(p.stat().st_mtime for p in src.rglob("*") if p.is_file())
    except ValueError:
        return
    dist_mtime = dist_index.stat().st_mtime
    delta = src_mtime - dist_mtime
    if delta > 5:
        mins = int(delta // 60)
        logger.warning(
            "DIST STALE: frontend/src is %ds (%dm) newer than frontend/dist/index.html "
            "— rebuild with `cd frontend && npx vite build` or POST /api/system/restart",
            int(delta), mins,
        )


def _migrate_legacy_user_dirs():
    """Rename legacy ~/.agenthive → ~/.xylocopa on startup.

    Only runs if the new dir does not already exist. Safe to call repeatedly.
    """
    home = os.path.expanduser("~")
    old = os.path.join(home, ".agenthive")
    new = os.path.join(home, ".xylocopa")
    if os.path.isdir(old) and not os.path.exists(new):
        os.rename(old, new)
        logger.info("Migrated legacy %s → %s", old, new)


# ---- App creation ----

app = FastAPI(
    title="Xylocopa",
    description="Multi-instance Claude Code orchestration system",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Middleware ----

_TIMING_SLOW_MS = float(os.environ.get("API_TIMING_SLOW_MS", "100"))


@app.middleware("http")
async def api_timing_logger(request: Request, call_next):
    """Log request duration for /api/* calls, flag slow ones at WARNING."""
    path = request.url.path
    if not path.startswith("/api/") or path.startswith("/api/hooks/"):
        return await call_next(request)
    t0 = time.perf_counter()
    response = await call_next(request)
    dur_ms = (time.perf_counter() - t0) * 1000.0
    level = logging.WARNING if dur_ms >= _TIMING_SLOW_MS else logging.INFO
    logger.log(
        level,
        "API_TIMING: %s %s status=%d dur=%.1fms",
        request.method, path, response.status_code, dur_ms,
    )
    return response


@app.middleware("http")
async def hook_request_logger(request: Request, call_next):
    """Log EVERY request to /api/hooks/* for debugging."""
    if request.url.path.startswith("/api/hooks/"):
        agent_id = request.headers.get("X-Agent-Id", "<none>")
        hook_name = request.url.path.split("/api/hooks/")[-1]
        logger.info(
            "HOOK_HTTP_IN: %s agent=%s method=%s",
            hook_name, agent_id[:12] if agent_id != "<none>" else "<none>", request.method,
        )
    response = await call_next(request)
    if request.url.path.startswith("/api/hooks/") and response.status_code >= 400:
        agent_id = request.headers.get("X-Agent-Id", "<none>")
        hook_name = request.url.path.split("/api/hooks/")[-1]
        logger.error(
            "HOOK_HTTP_ERR: %s agent=%s status=%d",
            hook_name, agent_id[:12] if agent_id != "<none>" else "<none>", response.status_code,
        )
    return response


_AUTH_EXEMPT_PREFIXES = ("/api/auth/", "/api/health", "/api/cert", "/api/webclip", "/api/hooks/", "/api/debug/auth-diag", "/api/debug/clear-cache", "/api/push/ack")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Reject unauthenticated requests to protected endpoints."""
    # Allow DISABLE_AUTH=1 for development/testing
    if os.environ.get("DISABLE_AUTH", "").strip() in ("1", "true", "yes"):
        return await call_next(request)

    path = request.url.path

    # Skip auth for exempt paths and non-API static assets
    if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    if not path.startswith("/api/"):
        return await call_next(request)

    # Check for password — if none set, allow all requests (first-time setup)
    db = SessionLocal()
    try:
        pw_hash = get_password_hash(db)
        if pw_hash is None:
            return await call_next(request)

        # Verify bearer token (header) or query param (for <img src="..."> etc.)
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = request.query_params.get("token", "")

        if not token:
            # Skip noisy debug endpoints to keep logs clean
            if path != "/api/debug/frontend-state":
                logger.info("AUTH_REJECT no_token: %s %s", request.method, path)
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        jwt_secret = get_jwt_secret(db)
        if not verify_token(token, jwt_secret):
            logger.info("AUTH_REJECT bad_token: %s %s (token=%s…)", request.method, path, token[:16])
            return JSONResponse({"detail": "Token expired or invalid"}, status_code=401)
    finally:
        db.close()

    return await call_next(request)


# ---- Voice and WebSocket ----

from voice import router as voice_router
app.include_router(voice_router)

from websocket import websocket_endpoint
app.websocket("/ws/status")(websocket_endpoint)



# ---- Include all routers ----

from routers.auth import router as auth_router
from routers.system import router as system_router
from routers.projects import router as projects_router
from routers.tasks import router as tasks_router
from routers.hooks import router as hooks_router
from routers.agents import router as agents_router
from routers.git import router as git_router
from routers.files import router as files_router
from routers.push import router as push_router
from routers.workers import router as workers_router
from routers.logs import router as logs_router
from routers.skills import router as skills_router
from routers.stats import router as stats_router
from routers.bookmarks import router as bookmarks_router

app.include_router(auth_router)
app.include_router(system_router)
app.include_router(projects_router)
app.include_router(tasks_router)
app.include_router(hooks_router)
app.include_router(agents_router)
app.include_router(git_router)
app.include_router(files_router)
app.include_router(push_router)
app.include_router(workers_router)
app.include_router(logs_router)
app.include_router(skills_router)
app.include_router(stats_router)
app.include_router(bookmarks_router)
