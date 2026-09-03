"""Dropbox sync engine — runtime config, scheduler loop, per-project sync, dry-run jobs, status."""

import asyncio
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from config import DROPBOX_APP_KEY, DROPBOX_RELAY_URL, DROPBOX_SYNC_DIR, DROPBOX_USING_DEFAULT_APP

from .auth import (
    DropboxTokenProvider,
    LinkFlow,
    NotLinkedError,
    TokenStore,
    unlink as auth_unlink,
)
from .client import (
    SMALL_FILE_LIMIT,
    DropboxAuthError,
    DropboxClient,
    DropboxError,
    DropboxIncorrectOffset,
    DropboxSessionNotFound,
    Throttle,
    commit_info,
)
from .hashing import ContentHasher, content_hash_file
from .ignore import IgnoreRules
from .scanner import dry_run as scanner_dry_run, list_top_level, scan_project
from .state import FileRecord, SyncState, diff_entries

logger = logging.getLogger("orchestrator.dropbox.engine")

MiB = 1024 * 1024

# How old a pending session can be before we discard it (6 days)
_PENDING_MAX_AGE_S = 6 * 86400

# ── SyncConfig ────────────────────────────────────────────────────────


@dataclass
class SyncConfig:
    interval_hours: int = 1
    concurrency: int = 4
    chunk_mb: int = 8
    max_file_mb: int = 2048
    max_files_per_project: int = 300_000
    allowlist_mode: bool = False
    allowlist_exts: list[str] = field(default_factory=lambda: [
        ".py", ".sh", ".md", ".txt", ".tex", ".bib", ".json", ".yaml", ".yml",
        ".toml", ".ipynb", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
        ".c", ".cpp", ".h", ".hpp", ".cu", ".pdf", ".docx", ".pptx", ".xlsx",
        ".csv", ".svg", ".png", ".jpg", ".jpeg", "Makefile",
    ])
    prune: bool = False
    bandwidth_kbps: int = 0
    paused: bool = False


def _config_path() -> str:
    return os.path.join(DROPBOX_SYNC_DIR, "config.json")


def _load_config() -> SyncConfig:
    cfg = SyncConfig()
    p = _config_path()
    if os.path.isfile(p):
        try:
            with open(p) as f:
                data = json.load(f)
            for k in ("interval_hours", "concurrency", "chunk_mb", "max_file_mb",
                       "max_files_per_project", "bandwidth_kbps"):
                if k in data:
                    setattr(cfg, k, int(data[k]))
            for k in ("allowlist_mode", "prune", "paused"):
                if k in data:
                    setattr(cfg, k, bool(data[k]))
            if "allowlist_exts" in data and isinstance(data["allowlist_exts"], list):
                cfg.allowlist_exts = data["allowlist_exts"]
        except Exception:
            logger.warning("Failed to load config.json, using defaults", exc_info=True)
    return cfg


def _save_config(cfg: SyncConfig) -> None:
    os.makedirs(DROPBOX_SYNC_DIR, mode=0o700, exist_ok=True)
    data = {
        "interval_hours": cfg.interval_hours,
        "concurrency": cfg.concurrency,
        "chunk_mb": cfg.chunk_mb,
        "max_file_mb": cfg.max_file_mb,
        "max_files_per_project": cfg.max_files_per_project,
        "allowlist_mode": cfg.allowlist_mode,
        "allowlist_exts": cfg.allowlist_exts,
        "prune": cfg.prune,
        "bandwidth_kbps": cfg.bandwidth_kbps,
        "paused": cfg.paused,
    }
    p = _config_path()
    with open(p, "w") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


_cfg = _load_config()
_wake_event = asyncio.Event()
_stop_requested = False
_queue: list[str] = []  # project names for manual sync
_queue_trigger: str = "manual"

# ── In-flight run progress ────────────────────────────────────────────


@dataclass
class RunProgress:
    run_id: int = 0
    trigger: str = ""
    project: str | None = None
    phase: str = ""
    files_total: int = 0
    files_done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    errors: int = 0
    started_at: str = ""
    projects_total: int = 0
    projects_done: int = 0


_current: RunProgress | None = None

# ── Singletons (lazily created, clearable for tests) ──────────────────

_state: SyncState | None = None
_token_store: TokenStore | None = None
_http_client: httpx.AsyncClient | None = None
_dbx_client: DropboxClient | None = None
_throttle: Throttle | None = None
_link_flow: LinkFlow | None = None

# Injectable client factory for tests
_client_factory: Callable | None = None

# Cached space usage
_space_cache: dict | None = None
_space_cache_at: float = 0

# Cached account info
_account_cache: dict | None = None

# Dry-run jobs
_dry_run_jobs: dict[str, dict] = {}
_dry_run_cache: dict[str, dict] = {}  # project -> last complete result

# Next scheduled run time (for status display)
_next_run_at: str | None = None


def get_state() -> SyncState:
    global _state
    if _state is None:
        db_path = os.path.join(DROPBOX_SYNC_DIR, "state.db")
        _state = SyncState(db_path)
    return _state


def get_token_store() -> TokenStore:
    global _token_store
    if _token_store is None:
        _token_store = TokenStore(os.path.join(DROPBOX_SYNC_DIR, "token.json"))
    return _token_store


def get_link_flow() -> LinkFlow:
    global _link_flow
    if _link_flow is None:
        _link_flow = LinkFlow(get_token_store(), http=_get_http())
    return _link_flow


def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient()
    return _http_client


def _get_throttle() -> Throttle:
    global _throttle
    if _throttle is None:
        _throttle = Throttle(_cfg.bandwidth_kbps)
    return _throttle


async def _get_client() -> DropboxClient:
    global _dbx_client
    if _client_factory is not None:
        return _client_factory()
    if _dbx_client is None:
        tokens = DropboxTokenProvider(get_token_store(), http=_get_http())
        _dbx_client = DropboxClient(tokens, http=_get_http(), throttle=_get_throttle())
    return _dbx_client


def set_client_factory(fn: Callable | None) -> None:
    """Inject a client factory for tests."""
    global _client_factory, _dbx_client
    _client_factory = fn
    _dbx_client = None


def reset_for_tests(sync_dir: str) -> None:
    """Reset all singletons and point at a fresh sync_dir for test isolation."""
    global _state, _token_store, _http_client, _dbx_client, _throttle, _link_flow
    global _cfg, _current, _queue, _stop_requested, _space_cache, _space_cache_at
    global _account_cache, _dry_run_jobs, _dry_run_cache, _next_run_at, _client_factory

    if _state is not None:
        try:
            _state.close()
        except Exception:
            pass
    _state = None
    _token_store = None
    _link_flow = None
    _http_client = None
    _dbx_client = None
    _throttle = None
    _client_factory = None
    _cfg = SyncConfig()
    _current = None
    _queue = []
    _stop_requested = False
    _space_cache = None
    _space_cache_at = 0
    _account_cache = None
    _dry_run_jobs = {}
    _dry_run_cache = {}
    _next_run_at = None

    # Patch DROPBOX_SYNC_DIR at module level in config
    import config
    config.DROPBOX_SYNC_DIR = sync_dir
    # Re-import into our own namespace
    global DROPBOX_SYNC_DIR
    import importlib
    # just update the local reference
    globals()["DROPBOX_SYNC_DIR"] = sync_dir


# ── Public config API ────────────────────────────────────────────────


def get_runtime_config() -> dict:
    return {
        "interval_hours": _cfg.interval_hours,
        "concurrency": _cfg.concurrency,
        "chunk_mb": _cfg.chunk_mb,
        "max_file_mb": _cfg.max_file_mb,
        "max_files_per_project": _cfg.max_files_per_project,
        "allowlist_mode": _cfg.allowlist_mode,
        "allowlist_exts": _cfg.allowlist_exts,
        "prune": _cfg.prune,
        "bandwidth_kbps": _cfg.bandwidth_kbps,
        "paused": _cfg.paused,
    }


def update_runtime_config(**fields: Any) -> dict:
    """Validate and apply config changes. Returns the full config dict."""
    for k in ("interval_hours", "concurrency", "max_file_mb", "max_files_per_project"):
        if k in fields:
            v = int(fields[k])
            if v < 1:
                raise ValueError(f"{k} must be >= 1")
            setattr(_cfg, k, v)

    if "chunk_mb" in fields:
        v = int(fields["chunk_mb"])
        if v < 4 or v % 4 != 0 or v > 144:
            raise ValueError("chunk_mb must be a multiple of 4, between 4 and 144")
        _cfg.chunk_mb = v

    if "bandwidth_kbps" in fields:
        v = int(fields["bandwidth_kbps"])
        if v < 0:
            raise ValueError("bandwidth_kbps must be >= 0")
        _cfg.bandwidth_kbps = v
        _get_throttle().set_rate(v)

    for k in ("allowlist_mode", "prune", "paused"):
        if k in fields:
            setattr(_cfg, k, bool(fields[k]))

    if "allowlist_exts" in fields:
        v = fields["allowlist_exts"]
        if isinstance(v, list):
            _cfg.allowlist_exts = v

    _save_config(_cfg)
    _wake_event.set()
    return get_runtime_config()


def on_project_settings_changed(name: str) -> None:
    """Called when a project's dropbox_sync/dropbox_folders/dropbox_ignore changes."""
    logger.info("Project settings changed: %s", name)


def request_sync(project: str | None = None, trigger: str = "manual") -> None:
    """Enqueue a sync request. None means all enabled projects."""
    global _queue_trigger
    if project is not None:
        if project not in _queue:
            _queue.append(project)
    else:
        _queue.clear()
        _queue.append("__all__")
    _queue_trigger = trigger
    _wake_event.set()


def pause() -> None:
    _cfg.paused = True
    _save_config(_cfg)
    global _stop_requested
    _stop_requested = True
    _wake_event.set()


def resume() -> None:
    _cfg.paused = False
    _save_config(_cfg)
    global _stop_requested
    _stop_requested = False
    _wake_event.set()


# ── Helpers ──────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def remote_path(project_name: str, rel_path: str) -> str:
    return "/" + project_name + "/" + rel_path


def _active_projects_from_db() -> list[dict]:
    """Get all non-internal, non-archived projects with dropbox_sync enabled.

    Opens and closes its own SessionLocal inside the call.
    Returns plain dicts to avoid holding SQLAlchemy objects across awaits.
    """
    from database import SessionLocal
    from models import Project
    db = SessionLocal()
    try:
        projects = (
            db.query(Project)
            .filter(
                Project.dropbox_sync == True,  # noqa: E712
                Project.archived == False,  # noqa: E712
                ~Project.name.startswith("."),
            )
            .all()
        )
        return [
            {
                "name": p.name,
                "display_name": p.display_name,
                "path": p.path,
                "dropbox_folders": p.dropbox_folders,
                "dropbox_ignore": p.dropbox_ignore,
            }
            for p in projects
        ]
    finally:
        db.close()


def _all_projects_from_db() -> list[dict]:
    """Get all non-internal, non-archived projects for status display."""
    from database import SessionLocal
    from models import Project
    db = SessionLocal()
    try:
        projects = (
            db.query(Project)
            .filter(
                Project.archived == False,  # noqa: E712
                ~Project.name.startswith("."),
            )
            .all()
        )
        return [
            {
                "name": p.name,
                "display_name": p.display_name,
                "path": p.path,
                "dropbox_sync": bool(p.dropbox_sync),
                "dropbox_folders": p.dropbox_folders,
                "dropbox_ignore": p.dropbox_ignore,
            }
            for p in projects
        ]
    finally:
        db.close()


def _get_project_dict(name: str) -> dict | None:
    """Get a single project as a plain dict."""
    from database import SessionLocal
    from models import Project
    try:
        db = SessionLocal()
        try:
            p = db.get(Project, name)
            if p is None:
                return None
            return {
                "name": p.name,
                "display_name": p.display_name,
                "path": p.path,
                "dropbox_sync": bool(p.dropbox_sync),
                "dropbox_folders": p.dropbox_folders,
                "dropbox_ignore": p.dropbox_ignore,
                "archived": bool(p.archived),
            }
        finally:
            db.close()
    except Exception:
        return None


async def _emit(kind: str, project: str | None = None, **extra: Any) -> None:
    """Best-effort websocket broadcast."""
    try:
        from websocket import emit_dropbox_update
        await emit_dropbox_update(kind, project=project, **extra)
    except Exception:
        logger.debug("emit_dropbox_update failed", exc_info=True)


# ── Space / account cache ────────────────────────────────────────────


async def refresh_account_info() -> None:
    """Refresh cached account info and space usage."""
    global _account_cache, _space_cache, _space_cache_at
    try:
        client = await _get_client()
        acct = await client.get_current_account()
        _account_cache = {
            "account_id": acct.get("account_id"),
            "name": acct.get("name", {}).get("display_name"),
            "email": acct.get("email"),
        }
        space = await client.space_summary()
        _space_cache = {**space, "fetched_at": _utcnow()}
        _space_cache_at = time.monotonic()
    except Exception:
        logger.debug("refresh_account_info failed", exc_info=True)


def _account_from_token() -> dict | None:
    """Read account info from the stored token (no network call)."""
    if _account_cache is not None:
        return _account_cache
    store = get_token_store()
    data = store.load()
    if data is None:
        return None
    return {
        "account_id": data.get("account_id"),
        "name": data.get("account_name"),
        "email": data.get("email"),
    }


# ── Status ───────────────────────────────────────────────────────────


def _compute_link_mode() -> str:
    """Determine the link mode from config: ``relay``, ``direct``, or ``none``."""
    if not DROPBOX_APP_KEY:
        return "none"
    if DROPBOX_USING_DEFAULT_APP and DROPBOX_RELAY_URL:
        return "relay"
    if not DROPBOX_USING_DEFAULT_APP and not DROPBOX_RELAY_URL:
        return "direct"
    if not DROPBOX_USING_DEFAULT_APP and DROPBOX_RELAY_URL:
        return "relay"
    return "direct"


def get_status() -> dict:
    """Return the full sync status. Must be cheap (no network)."""
    store = get_token_store()
    linked = store.is_linked
    account = _account_from_token() if linked else None
    token_data = store.load() if linked else None
    app_key = (token_data or {}).get("app_key", DROPBOX_APP_KEY) if linked else DROPBOX_APP_KEY

    space = None
    if _space_cache is not None:
        space = dict(_space_cache)

    state = get_state()
    last_run = state.run_last()
    current_run_db = state.run_current()

    # Build current run from in-memory progress
    current_run = None
    if _current is not None and _current.run_id:
        current_run = {
            "id": _current.run_id,
            "trigger": _current.trigger,
            "started_at": _current.started_at,
            "project": _current.project,
            "phase": _current.phase,
            "files_total": _current.files_total,
            "files_done": _current.files_done,
            "bytes_total": _current.bytes_total,
            "bytes_done": _current.bytes_done,
            "errors": _current.errors,
            "projects_total": _current.projects_total,
            "projects_done": _current.projects_done,
        }

    # Build projects list from main DB + sync state stats
    try:
        all_proj = _all_projects_from_db()
    except Exception:
        all_proj = []

    sync_stats = state.project_stats_all()

    projects_list = []
    for p in all_proj:
        pname = p["name"]
        ps = sync_stats.get(pname, {})
        folders_raw = p.get("dropbox_folders")
        folders = json.loads(folders_raw) if folders_raw else None

        # Count pending prune (files in state that no longer exist on disk)
        files_in_state = state.count_files(pname)

        projects_list.append({
            "name": pname,
            "display_name": p.get("display_name", pname),
            "enabled": p.get("dropbox_sync", False),
            "folders": folders,
            "files_synced": ps.get("files_synced", 0) or 0,
            "bytes_synced": ps.get("bytes_synced", 0) or 0,
            "last_synced_at": ps.get("last_synced_at"),
            "last_error": ps.get("last_error"),
            "pending_prune": 0,
            "skipped_collisions": 0,
            "folder_total": 0,
        })

    recent_errors = state.errors_recent(limit=20)

    # next_run_at is null when not linked or paused
    next_run = _next_run_at if (linked and not _cfg.paused) else None

    return {
        "linked": linked,
        "account": account,
        "space": space,
        "app_key": app_key or None,
        "link_mode": _compute_link_mode(),
        "relay_url": DROPBOX_RELAY_URL or None,
        "config": get_runtime_config(),
        "next_run_at": next_run,
        "current_run": current_run,
        "last_run": last_run,
        "projects": projects_list,
        "queue": list(_queue),
        "recent_errors": recent_errors,
    }


def get_project_status(name: str, project_dict: dict | None = None) -> dict:
    """Return cheap per-project sync status."""
    store = get_token_store()
    linked = store.is_linked
    token_data = store.load() if linked else None
    app_key = (token_data or {}).get("app_key", DROPBOX_APP_KEY) if linked else DROPBOX_APP_KEY
    account_email = (token_data or {}).get("email") if linked else None

    pdict = project_dict or _get_project_dict(name)
    enabled = bool(pdict.get("dropbox_sync")) if pdict else False
    folders_raw = pdict.get("dropbox_folders") if pdict else None
    folders = json.loads(folders_raw) if folders_raw else None

    state = get_state()
    ps = {}
    try:
        all_stats = state.project_stats_all()
        ps = all_stats.get(name, {})
    except Exception:
        pass

    # Folder total from on-disk listing (cheap)
    folder_total = 0
    if pdict and pdict.get("path") and os.path.isdir(pdict["path"]):
        try:
            rules = IgnoreRules.build(pdict["path"], include_defaults=True, read_syncignore=False)
            entries = list_top_level(pdict["path"], rules)
            folder_total = sum(
                1 for e in entries
                if not e.get("default_ignored") and e.get("type") != "symlink"
            )
        except Exception:
            pass

    queued = name in _queue or "__all__" in _queue
    run_active = _current is not None and _current.run_id > 0
    current = None
    if run_active and _current is not None and _current.project == name:
        current = {
            "phase": _current.phase,
            "files_total": _current.files_total,
            "files_done": _current.files_done,
            "bytes_total": _current.bytes_total,
            "bytes_done": _current.bytes_done,
        }

    return {
        "linked": linked,
        "app_key": app_key or None,
        "link_mode": _compute_link_mode(),
        "account_email": account_email,
        "paused": _cfg.paused,
        "enabled": enabled,
        "folders": folders,
        "folder_total": folder_total,
        "files_synced": ps.get("files_synced", 0) or 0,
        "bytes_synced": ps.get("bytes_synced", 0) or 0,
        "last_synced_at": ps.get("last_synced_at"),
        "last_error": ps.get("last_error"),
        "pending_prune": 0,
        "skipped_collisions": 0,
        "queued": queued,
        "run_active": run_active,
        "current": current,
    }


# ── Dry-run jobs ─────────────────────────────────────────────────────

_DRY_RUN_IDLE_STOP_S = 30
_DRY_RUN_RETENTION_S = 600  # 10 min


def start_dry_run(project_name: str, project_path: str,
                  folders: list[str] | None, ignore: str | None) -> str:
    """Start a dry-run scan job. Returns job_id."""
    # Check cache
    cache_key = project_name
    cached = _dry_run_cache.get(cache_key)
    if cached is not None:
        cache_match = (
            cached.get("_folders") == folders
            and cached.get("_ignore") == ignore
            and time.monotonic() - cached.get("_completed_at", 0) < _DRY_RUN_RETENTION_S
        )
        if cache_match:
            job_id = cached["job_id"]
            cached["cached"] = True
            cached["_last_polled"] = time.monotonic()
            _dry_run_jobs[job_id] = cached
            return job_id

    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "job_id": job_id,
        "project": project_name,
        "status": "running",
        "entries": {},
        "done": [],
        "total": None,
        "error": None,
        "started_at": _utcnow(),
        "cached": False,
        "_folders": folders,
        "_ignore": ignore,
        "_last_polled": time.monotonic(),
        "_stop": False,
        "_completed_at": 0,
    }
    _dry_run_jobs[job_id] = job

    def _should_stop() -> bool:
        # Stop if not polled for 30s or explicitly stopped
        if job.get("_stop"):
            return True
        if time.monotonic() - job["_last_polled"] > _DRY_RUN_IDLE_STOP_S:
            return True
        return False

    def _on_entry_done(stats: Any) -> None:
        job["entries"][stats.name] = stats.as_dict()
        job["done"].append(stats.name)

    async def _run() -> None:
        try:
            allowlist_exts = set(_cfg.allowlist_exts) if _cfg.allowlist_mode else None
            rules = IgnoreRules.build(
                project_path,
                folders=folders,
                extra_rules=ignore,
                allowlist_exts=allowlist_exts,
            )
            result = await asyncio.to_thread(
                scanner_dry_run,
                project_path,
                rules,
                max_file_bytes=_cfg.max_file_mb * MiB,
                on_entry_done=_on_entry_done,
                should_stop=_should_stop,
            )
            job["entries"] = result["entries"]
            job["total"] = result["total"]
            job["status"] = "complete"
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)
            logger.warning("Dry-run job %s failed: %s", job_id, exc)
        finally:
            job["_completed_at"] = time.monotonic()
            # Cache the result for this project
            _dry_run_cache[project_name] = dict(job)

    asyncio.ensure_future(_run())
    return job_id


def get_dry_run(job_id: str) -> dict | None:
    """Get a dry-run job status. Returns None if not found."""
    # Clean up old jobs
    now = time.monotonic()
    to_remove = []
    for jid, j in _dry_run_jobs.items():
        if j["status"] != "running" and now - j.get("_completed_at", 0) > _DRY_RUN_RETENTION_S:
            to_remove.append(jid)
    for jid in to_remove:
        del _dry_run_jobs[jid]

    job = _dry_run_jobs.get(job_id)
    if job is None:
        return None
    job["_last_polled"] = now
    return {
        "job_id": job["job_id"],
        "project": job["project"],
        "status": job["status"],
        "entries": job["entries"],
        "done": job["done"],
        "total": job["total"],
        "error": job["error"],
        "started_at": job["started_at"],
        "cached": job.get("cached", False),
    }


def stop_dry_run(job_id: str) -> bool:
    """Stop a running dry-run job. Returns True if found."""
    job = _dry_run_jobs.get(job_id)
    if job is None:
        return False
    job["_stop"] = True
    return True


# ── Folder listing ───────────────────────────────────────────────────


def list_folders(project_path: str, selected: list[str] | None) -> list[dict]:
    """List top-level entries with selection state."""
    rules = IgnoreRules.build(project_path, include_defaults=True, read_syncignore=True)
    entries = list_top_level(project_path, rules)
    for e in entries:
        if selected is None:
            e["selected"] = not e.get("default_ignored") and e.get("type") != "symlink"
        else:
            e["selected"] = e["name"] in selected
    return entries


# ── Sync project ─────────────────────────────────────────────────────


async def sync_project(project: dict, run_progress: RunProgress) -> None:
    """Sync a single project to Dropbox."""
    name = project["name"]
    path = project["path"]
    state = get_state()

    run_progress.project = name
    run_progress.phase = "scan"
    await _emit("project_started", project=name)

    folders_raw = project.get("dropbox_folders")
    folders = json.loads(folders_raw) if folders_raw else None
    ignore_text = project.get("dropbox_ignore")

    allowlist_exts = set(_cfg.allowlist_exts) if _cfg.allowlist_mode else None
    rules = IgnoreRules.build(
        path, folders=folders, extra_rules=ignore_text,
        allowlist_exts=allowlist_exts,
    )

    # Scan in a thread
    entries, _, skipped = await asyncio.to_thread(
        scan_project, path, rules,
        max_file_bytes=_cfg.max_file_mb * MiB,
    )

    # Case-insensitive collision detection
    collision_count = 0
    by_lower: dict[str, list] = defaultdict(list)
    for e in entries:
        by_lower[e.rel_path.lower()].append(e)

    deduped_entries = []
    for group in by_lower.values():
        if len(group) == 1:
            deduped_entries.append(group[0])
        else:
            group.sort(key=lambda x: x.rel_path)
            kept = group[0]
            deduped_entries.append(kept)
            for skipped_e in group[1:]:
                collision_count += 1
                await asyncio.to_thread(
                    state.error_add, name, skipped_e.rel_path,
                    f"case collision with {kept.rel_path}: skipped",
                )
    entries = deduped_entries

    # Budget check
    if len(entries) > _cfg.max_files_per_project:
        msg = f"over budget: {len(entries)} files > {_cfg.max_files_per_project}"
        await asyncio.to_thread(state.project_stats_update, name, last_error=msg)
        await asyncio.to_thread(state.error_add, name, None, msg)
        run_progress.errors += 1
        logger.warning("Project %s: %s", name, msg)
        return

    run_progress.files_total = len(entries)
    run_progress.phase = "hash"

    # Diff against known state
    known = await asyncio.to_thread(state.get_project_files, name)
    changed, deleted, unchanged = diff_entries(known, entries)

    # Update run counters
    await asyncio.to_thread(
        state.run_update, run_progress.run_id,
        project=name, files_scanned=len(entries),
    )

    client = await _get_client()
    errors = 0
    files_uploaded = 0
    bytes_uploaded = 0

    # Resume pending sessions
    pending_all = await asyncio.to_thread(state.pending_all, name)
    pending_by_path: dict[str, dict] = {p["rel_path"]: p for p in pending_all}

    # Build a lookup for entries by rel_path
    entry_by_path = {e.rel_path: e for e in entries}

    # Collect commit-ready entries
    batch_entries: list[dict] = []

    # Handle resumed sessions that are already complete (hash set)
    for rel, pend in list(pending_by_path.items()):
        if _stop_requested:
            break
        entry = entry_by_path.get(rel)
        if entry is None:
            # File gone
            await asyncio.to_thread(state.pending_delete, name, rel)
            continue
        if pend["size"] != entry.size or pend["mtime_ns"] != entry.mtime_ns:
            # File changed
            await asyncio.to_thread(state.pending_delete, name, rel)
            continue
        # Check age
        created_at = pend.get("created_at", "")
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - created_dt).total_seconds()
            if age_s > _PENDING_MAX_AGE_S:
                await asyncio.to_thread(state.pending_delete, name, rel)
                continue
        except Exception:
            await asyncio.to_thread(state.pending_delete, name, rel)
            continue

        if pend["content_hash"] is not None:
            # Already uploaded, just needs commit
            batch_entries.append({
                "cursor": {"session_id": pend["session_id"], "offset": pend["size"]},
                "commit": commit_info(remote_path(name, rel), entry.mtime_ns, pend["content_hash"]),
                "_entry": entry,
                "_hash": pend["content_hash"],
            })
            # Remove from changed so we don't re-upload
            changed = [c for c in changed if c.rel_path != rel]

    # Process changed files with concurrency
    sem = asyncio.Semaphore(_cfg.concurrency)
    chunk_bytes = _cfg.chunk_mb * MiB

    async def _upload_file(entry: Any) -> dict | None:
        nonlocal errors
        if _stop_requested:
            return None

        rel = entry.rel_path
        fpath = os.path.join(path, rel.replace("/", os.sep))

        try:
            # Check for a pending session to resume
            pend = pending_by_path.get(rel)
            resume_sid = None
            resume_offset = 0
            if (pend is not None
                    and pend["content_hash"] is None
                    and pend["size"] == entry.size
                    and pend["mtime_ns"] == entry.mtime_ns):
                # Check age
                try:
                    created_dt = datetime.fromisoformat(
                        pend.get("created_at", "").replace("Z", "+00:00")
                    )
                    age_s = (datetime.now(timezone.utc) - created_dt).total_seconds()
                    if age_s <= _PENDING_MAX_AGE_S:
                        resume_sid = pend["session_id"]
                        resume_offset = pend["offset"]
                except Exception:
                    pass

            file_size = entry.size

            if file_size <= SMALL_FILE_LIMIT:
                # ── Small file: read whole, hash in memory ──
                file_data = await asyncio.to_thread(_read_file, fpath)
                if file_data is None:
                    errors += 1
                    await asyncio.to_thread(state.error_add, name, rel, "file read error")
                    return None

                hasher = ContentHasher()
                hasher.update(file_data)
                file_hash = hasher.hexdigest()

                # Fast path: hash matches known, just update mtime
                known_rec = known.get(rel)
                if known_rec is not None and known_rec.content_hash == file_hash:
                    rec = FileRecord(
                        project=name, rel_path=rel, size=entry.size,
                        mtime_ns=entry.mtime_ns, content_hash=file_hash,
                        remote_rev=known_rec.remote_rev, uploaded_at=_utcnow(),
                    )
                    await asyncio.to_thread(state.upsert_files, [rec])
                    return None

                # Re-stat before upload to detect changes during read
                try:
                    st = await asyncio.to_thread(os.stat, fpath)
                    if st.st_size != entry.size or st.st_mtime_ns != entry.mtime_ns:
                        logger.debug("File changed during read: %s", rel)
                        return None
                except OSError:
                    errors += 1
                    return None

                session_id = await client.upload_session_start(file_data, close=True)
                return {
                    "cursor": {"session_id": session_id, "offset": file_size},
                    "commit": commit_info(remote_path(name, rel), entry.mtime_ns, file_hash),
                    "_entry": entry,
                    "_hash": file_hash,
                }

            # ── Large file: stream hash, then stream upload from disk ──
            try:
                file_hash = await asyncio.to_thread(content_hash_file, fpath)
            except OSError:
                errors += 1
                await asyncio.to_thread(state.error_add, name, rel, "file read error")
                return None

            # Fast path: hash matches known, just update mtime
            known_rec = known.get(rel)
            if known_rec is not None and known_rec.content_hash == file_hash:
                rec = FileRecord(
                    project=name, rel_path=rel, size=entry.size,
                    mtime_ns=entry.mtime_ns, content_hash=file_hash,
                    remote_rev=known_rec.remote_rev, uploaded_at=_utcnow(),
                )
                await asyncio.to_thread(state.upsert_files, [rec])
                return None

            # Re-stat before upload to detect changes during hashing
            try:
                st = await asyncio.to_thread(os.stat, fpath)
                if st.st_size != entry.size or st.st_mtime_ns != entry.mtime_ns:
                    logger.debug("File changed during hash: %s", rel)
                    return None
            except OSError:
                errors += 1
                return None

            # Multi-chunk upload reading from disk
            offset = 0
            session_id = resume_sid

            if session_id is None:
                chunk = await asyncio.to_thread(_read_chunk, fpath, 0, chunk_bytes)
                is_last = file_size <= chunk_bytes
                session_id = await client.upload_session_start(chunk, close=is_last)
                offset = len(chunk)
                if not is_last:
                    await asyncio.to_thread(
                        state.pending_put, name, rel, session_id, offset,
                        entry.size, entry.mtime_ns, None,
                    )
            else:
                offset = resume_offset

            # Append remaining chunks
            while offset < file_size:
                if _stop_requested:
                    return None
                chunk = await asyncio.to_thread(_read_chunk, fpath, offset, chunk_bytes)
                is_last = (offset + len(chunk) >= file_size)
                try:
                    await client.upload_session_append(
                        session_id, offset, chunk, close=is_last,
                    )
                except DropboxIncorrectOffset as exc:
                    offset = exc.correct_offset
                    continue
                except DropboxSessionNotFound:
                    await asyncio.to_thread(state.pending_delete, name, rel)
                    # Retry from scratch next run
                    return None
                offset += len(chunk)
                if not is_last:
                    await asyncio.to_thread(
                        state.pending_put, name, rel, session_id, offset,
                        entry.size, entry.mtime_ns, None,
                    )

            # Re-stat after upload
            try:
                st = await asyncio.to_thread(os.stat, fpath)
                if st.st_size != entry.size or st.st_mtime_ns != entry.mtime_ns:
                    await asyncio.to_thread(state.pending_delete, name, rel)
                    await asyncio.to_thread(
                        state.error_add, name, rel, "changed during upload",
                    )
                    return None
            except OSError:
                await asyncio.to_thread(state.pending_delete, name, rel)
                return None

            # Save pending with hash (ready for commit)
            await asyncio.to_thread(
                state.pending_put, name, rel, session_id, file_size,
                entry.size, entry.mtime_ns, file_hash,
            )

            return {
                "cursor": {"session_id": session_id, "offset": file_size},
                "commit": commit_info(remote_path(name, rel), entry.mtime_ns, file_hash),
                "_entry": entry,
                "_hash": file_hash,
            }

        except DropboxAuthError:
            raise  # Let caller handle
        except DropboxError as exc:
            errors += 1
            await asyncio.to_thread(state.error_add, name, rel, exc.summary)
            return None
        except Exception as exc:
            errors += 1
            await asyncio.to_thread(state.error_add, name, rel, str(exc))
            return None

    # Upload changed files
    run_progress.phase = "upload"

    async def _bounded_upload(entry: Any) -> dict | None:
        async with sem:
            return await _upload_file(entry)

    tasks = [asyncio.create_task(_bounded_upload(e)) for e in changed]
    try:
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is not None:
                batch_entries.append(result)
                # Commit in batches of 100
                if len(batch_entries) >= 100:
                    if _stop_requested:
                        break
                    ok, commit_errs, commit_bytes = await _commit_batch(state, client, name, batch_entries, run_progress)
                    files_uploaded += ok
                    errors += commit_errs
                    bytes_uploaded += commit_bytes
                    batch_entries = []
    finally:
        # On an early exit (auth error, pause) stop the in-flight uploads and
        # collect their results so no task exception is left unretrieved.
        pending_tasks = [t for t in tasks if not t.done()]
        for t in pending_tasks:
            t.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        for t in tasks:
            if t.done() and not t.cancelled():
                t.exception()

    # Commit remaining
    if batch_entries and not _stop_requested:
        run_progress.phase = "commit"
        ok, commit_errs, commit_bytes = await _commit_batch(state, client, name, batch_entries, run_progress)
        files_uploaded += ok
        errors += commit_errs
        bytes_uploaded += commit_bytes

    # Prune
    if _cfg.prune and deleted and not _stop_requested:
        run_progress.phase = "prune"
        paths_to_delete = [remote_path(name, rp) for rp in deleted]
        for i in range(0, len(paths_to_delete), 1000):
            if _stop_requested:
                break
            batch = paths_to_delete[i:i + 1000]
            try:
                results = await client.delete_batch(batch)
                # Process results
                successful_deletes = []
                for j, res in enumerate(results):
                    rp = deleted[i + j]
                    tag = res.get(".tag", "")
                    if tag == "success":
                        successful_deletes.append(rp)
                    elif tag == "failure":
                        fail = res.get("failure", {})
                        if fail.get(".tag") == "path_lookup" and fail.get("path_lookup", {}).get(".tag") == "not_found":
                            successful_deletes.append(rp)
                        else:
                            errors += 1
                            await asyncio.to_thread(
                                state.error_add, name, rp,
                                f"delete failed: {fail.get('.tag', 'unknown')}",
                            )
                if successful_deletes:
                    await asyncio.to_thread(state.delete_files, name, successful_deletes)
            except DropboxAuthError:
                raise  # Propagate for proper abort
            except DropboxError as exc:
                errors += 1
                await asyncio.to_thread(state.error_add, name, None, f"delete_batch: {exc.summary}")
                # Don't abort on prune errors
    elif not _cfg.prune and deleted:
        # Don't delete state rows when prune is off
        pass

    # Update per-project stats
    files_count, bytes_count = await asyncio.to_thread(state.count_files, name)
    await asyncio.to_thread(
        state.project_stats_update, name,
        files_synced=files_count,
        bytes_synced=bytes_count,
        last_synced_at=_utcnow(),
        last_error=None if errors == 0 else f"{errors} error(s)",
    )

    # Update run counters
    await asyncio.to_thread(
        state.run_update, run_progress.run_id,
        project=name,
        files_uploaded=files_uploaded,
        bytes_uploaded=bytes_uploaded,
        files_deleted=len(deleted) if _cfg.prune else 0,
        errors=errors,
    )

    run_progress.files_done = len(entries)
    run_progress.errors += errors
    run_progress.projects_done += 1
    await _emit("project_done", project=name)


async def _commit_batch(state: SyncState, client: DropboxClient,
                        project: str, batch: list[dict],
                        progress: RunProgress) -> tuple[int, int, int]:
    """Commit a batch of upload sessions and record results.

    Returns (succeeded_count, failed_count, bytes_committed).
    """
    commit_entries = [
        {"cursor": b["cursor"], "commit": b["commit"]}
        for b in batch
    ]
    try:
        results = await client.upload_session_finish_batch(commit_entries)
    except DropboxAuthError:
        raise  # Propagate to sync_project -> run_sync_loop for proper abort
    except DropboxError as exc:
        # Record errors for all entries
        for b in batch:
            entry = b["_entry"]
            await asyncio.to_thread(
                state.error_add, project, entry.rel_path, f"commit: {exc.summary}",
            )
            await asyncio.to_thread(state.pending_delete, project, entry.rel_path)
        progress.errors += len(batch)
        return (0, len(batch), 0)

    upserts = []
    pending_deletes = []
    ok = 0
    errs = 0
    committed_bytes = 0
    for i, res in enumerate(results):
        b = batch[i]
        entry = b["_entry"]
        tag = res.get(".tag", "")

        if tag == "success":
            rec = FileRecord(
                project=project,
                rel_path=entry.rel_path,
                size=entry.size,
                mtime_ns=entry.mtime_ns,
                content_hash=b["_hash"],
                remote_rev=res.get("rev"),
                uploaded_at=_utcnow(),
            )
            upserts.append(rec)
            pending_deletes.append((project, entry.rel_path))
            progress.files_done += 1
            ok += 1
            committed_bytes += entry.size
        else:
            fail_tag = ""
            failure = res.get("failure", {})
            if isinstance(failure, dict):
                fail_tag = failure.get(".tag", "")
            await asyncio.to_thread(
                state.error_add, project, entry.rel_path,
                f"commit failure: {fail_tag or tag}",
            )
            pending_deletes.append((project, entry.rel_path))
            progress.errors += 1
            errs += 1

    if upserts or pending_deletes:
        await asyncio.to_thread(state.commit_batch_results, upserts, pending_deletes)

    return (ok, errs, committed_bytes)


def _read_file(fpath: str) -> bytes | None:
    """Read a file, returning None on error."""
    try:
        with open(fpath, "rb") as f:
            return f.read()
    except OSError:
        return None


def _read_chunk(fpath: str, offset: int, size: int) -> bytes:
    """Read a chunk from disk. For use inside asyncio.to_thread."""
    with open(fpath, "rb") as f:
        f.seek(offset)
        return f.read(size)


# ── Project lifecycle hooks ──────────────────────────────────────────


async def on_project_renamed(old: str, new: str) -> None:
    """Called after a project is renamed. Updates sync state and schedules remote move."""
    try:
        state = get_state()
        await asyncio.to_thread(state.rename_project, old, new)
    except Exception:
        logger.warning("Failed to rename sync state %s -> %s", old, new, exc_info=True)

    try:
        client = await _get_client()
        await client.move_v2("/" + old, "/" + new)
    except Exception:
        logger.warning("Remote move /%s -> /%s failed", old, new, exc_info=True)
        try:
            await asyncio.to_thread(get_state().error_add, new, None, f"remote rename from {old} failed")
        except Exception:
            pass


async def on_project_deleted(name: str) -> None:
    """Called after a project is deleted. Cleans up sync state."""
    try:
        state = get_state()
        await asyncio.to_thread(state.forget_project, name)
    except Exception:
        logger.warning("Failed to forget sync state for %s", name, exc_info=True)


# ── Main sync loop ──────────────────────────────────────────────────


def _enqueue_never_synced() -> list[str]:
    """Queue enabled projects that have no completed sync yet.

    A project enabled just before a restart (the queue is in memory) or
    before the account was linked would otherwise wait for the next
    scheduled tick. Returns the names queued.
    """
    global _queue_trigger
    if not get_token_store().is_linked:
        return []
    stats = get_state().project_stats_all()
    names = [
        p["name"] for p in _active_projects_from_db()
        if not (stats.get(p["name"]) or {}).get("last_synced_at")
    ]
    for n in names:
        if n not in _queue:
            _queue.append(n)
    if names:
        _queue_trigger = "manual"
    return names


async def run_sync_loop() -> None:
    """Main sync loop — started from lifespan, cancelled at shutdown."""
    global _current, _stop_requested, _next_run_at

    logger.info("Dropbox sync loop started")

    try:
        queued = await asyncio.to_thread(_enqueue_never_synced)
        if queued:
            logger.info("Queued first sync for %s", ", ".join(queued))
    except Exception:
        logger.warning("Failed to queue never-synced projects", exc_info=True)

    # Mark any interrupted runs from a previous crash
    try:
        count = await asyncio.to_thread(get_state().runs_mark_interrupted)
        if count:
            logger.info("Marked %d interrupted sync runs", count)
    except Exception:
        logger.warning("Failed to mark interrupted runs", exc_info=True)

    while True:
        try:
            interval = _cfg.interval_hours * 3600

            # Compute next run time for status
            _next_run_at = (
                datetime.now(timezone.utc)
                .replace(microsecond=0)
                .__add__(__import__("datetime").timedelta(seconds=interval))
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            )

            # Requests that arrived while a run was in progress are already in
            # _queue — serve them now instead of sleeping until the next tick.
            if not _queue:
                _wake_event.clear()
                try:
                    await asyncio.wait_for(_wake_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass

            # Check if linked and not paused (drop queued requests so the
            # loop cannot spin on them)
            store = get_token_store()
            if not store.is_linked:
                _queue.clear()
                continue
            if _cfg.paused:
                _queue.clear()
                await _emit("paused")
                continue

            _stop_requested = False

            # Determine projects to sync
            trigger = "schedule"
            if _queue:
                trigger = _queue_trigger
                if "__all__" in _queue:
                    projects = await asyncio.to_thread(_active_projects_from_db)
                else:
                    queued_names = list(_queue)
                    all_active = await asyncio.to_thread(_active_projects_from_db)
                    active_map = {p["name"]: p for p in all_active}
                    projects = [active_map[n] for n in queued_names if n in active_map]
                _queue.clear()
            else:
                projects = await asyncio.to_thread(_active_projects_from_db)

            if not projects:
                continue

            # Refresh space usage at run start
            try:
                await refresh_account_info()
            except Exception:
                logger.debug("Pre-run account refresh failed", exc_info=True)

            # Start a new run
            state = get_state()
            run_id = await asyncio.to_thread(state.run_start, trigger)

            _current = RunProgress(
                run_id=run_id,
                trigger=trigger,
                started_at=_utcnow(),
                projects_total=len(projects),
            )

            await _emit("run_started")

            error_sample = None
            run_status = "ok"

            try:
                for proj in projects:
                    if _stop_requested:
                        run_status = "cancelled"
                        break
                    try:
                        await sync_project(proj, _current)
                    except DropboxAuthError as exc:
                        run_status = "error"
                        error_sample = f"Dropbox rejected the request: {exc.summary}"[:500]
                        _current.errors += 1
                        try:
                            await asyncio.to_thread(
                                state.project_stats_update, proj["name"], last_error=error_sample,
                            )
                            await asyncio.to_thread(state.error_add, proj["name"], None, error_sample)
                        except Exception:
                            logger.debug("failed to record auth error", exc_info=True)
                        break
                    except Exception as exc:
                        logger.exception("sync_project %s failed", proj["name"])
                        error_sample = str(exc)[:200]
                        _current.errors += 1
            finally:
                await asyncio.to_thread(state.run_finish, run_id, run_status, error_sample)
                _current = None
                await _emit("run_finished")

        except asyncio.CancelledError:
            # Shutdown
            if _current is not None:
                try:
                    state = get_state()
                    await asyncio.to_thread(
                        state.run_finish, _current.run_id, "cancelled",
                    )
                except Exception:
                    pass
                _current = None
            logger.info("Dropbox sync loop stopped")
            return
        except Exception:
            logger.exception("Unexpected error in sync loop")
            await asyncio.sleep(30)
