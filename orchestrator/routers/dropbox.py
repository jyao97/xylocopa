"""Dropbox sync API routes."""

import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from route_helpers import get_project_or_404

logger = logging.getLogger("orchestrator.dropbox.router")

router = APIRouter()

APP_KEY_RE = re.compile(r"^[A-Za-z0-9]{10,64}$")


# ── Status ───────────────────────────────────────────────────────────


@router.get("/api/dropbox/status")
async def get_status():
    from dropbox_sync.engine import get_status as engine_status
    return await asyncio.to_thread(engine_status)


@router.get("/api/projects/{name}/dropbox/status")
async def get_project_dropbox_status(name: str, db: Session = Depends(get_db)):
    proj = get_project_or_404(db, name)
    from dropbox_sync.engine import get_project_status
    # Build a project dict from the injected DB session so tests work
    pdict = {
        "name": proj.name,
        "display_name": proj.display_name,
        "path": proj.path,
        "dropbox_sync": bool(proj.dropbox_sync),
        "dropbox_folders": proj.dropbox_folders,
        "dropbox_ignore": proj.dropbox_ignore,
        "archived": bool(proj.archived),
    }
    return await asyncio.to_thread(get_project_status, name, pdict)


# ── Config ───────────────────────────────────────────────────────────


@router.put("/api/dropbox/config")
async def update_config(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    # Validate chunk_mb specifically
    if "chunk_mb" in body:
        v = body["chunk_mb"]
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="chunk_mb must be an integer")
        if v < 4 or v % 4 != 0 or v > 144:
            raise HTTPException(status_code=400, detail="chunk_mb must be a multiple of 4, between 4 and 144")

    from dropbox_sync.engine import update_runtime_config
    try:
        config = update_runtime_config(**body)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"detail": "ok", **config}


# ── Link flow ────────────────────────────────────────────────────────


@router.post("/api/dropbox/link/start")
async def link_start(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    app_key = body.get("app_key", "") if isinstance(body, dict) else ""
    if not app_key or not APP_KEY_RE.match(app_key):
        raise HTTPException(status_code=400, detail="Invalid app_key")

    from dropbox_sync.engine import get_token_store, get_link_flow
    store = get_token_store()
    if store.is_linked:
        raise HTTPException(status_code=409, detail="Already linked")

    flow = get_link_flow()
    result = flow.start(app_key)
    return result


@router.post("/api/dropbox/link/complete")
async def link_complete(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    code = body.get("code", "") if isinstance(body, dict) else ""
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    from dropbox_sync.engine import get_link_flow, refresh_account_info
    from dropbox_sync.auth import LinkStateError, LinkError

    flow = get_link_flow()
    try:
        account = await flow.complete(code)
    except LinkStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LinkError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Refresh account/space info
    try:
        await refresh_account_info()
    except Exception:
        pass

    from dropbox_sync.engine import _emit
    try:
        await _emit("linked")
    except Exception:
        pass

    return {"detail": "ok", "account": account}


@router.delete("/api/dropbox/link")
async def unlink(request: Request):
    from dropbox_sync.engine import get_token_store
    from dropbox_sync.auth import unlink as auth_unlink

    store = get_token_store()
    if not store.is_linked:
        raise HTTPException(status_code=404, detail="Not linked")

    # Try to get a client for revocation
    client = None
    try:
        from dropbox_sync.engine import _get_client
        client = await _get_client()
    except Exception:
        pass

    await auth_unlink(store, client)

    # Reset client so it gets recreated after re-link
    from dropbox_sync import engine
    engine._dbx_client = None

    from dropbox_sync.engine import _emit
    try:
        await _emit("unlinked")
    except Exception:
        pass

    return {"detail": "ok"}


# ── Sync trigger ─────────────────────────────────────────────────────


@router.post("/api/dropbox/sync")
async def trigger_sync(request: Request, db: Session = Depends(get_db)):
    from dropbox_sync.engine import get_token_store, request_sync, _queue

    store = get_token_store()
    if not store.is_linked:
        raise HTTPException(status_code=409, detail="Not linked")

    try:
        body = await request.json()
    except Exception:
        body = {}

    project = body.get("project") if isinstance(body, dict) else None

    if project is not None:
        proj = db.get(__import__("models").Project, project)
        if proj is None:
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
        if not proj.dropbox_sync:
            raise HTTPException(status_code=400, detail=f"Project '{project}' does not have Dropbox sync enabled")

    request_sync(project=project, trigger="manual")
    return {"detail": "queued", "queue": list(_queue)}


# ── Pause / Resume ───────────────────────────────────────────────────


@router.post("/api/dropbox/pause")
async def pause_sync():
    from dropbox_sync.engine import pause
    pause()
    return {"detail": "ok", "paused": True}


@router.post("/api/dropbox/resume")
async def resume_sync():
    from dropbox_sync.engine import resume
    resume()
    return {"detail": "ok", "paused": False}


# ── Dry-run ──────────────────────────────────────────────────────────


@router.post("/api/dropbox/dry-run")
async def start_dry_run(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    project_name = body.get("project")
    if not project_name:
        raise HTTPException(status_code=400, detail="Missing project name")

    proj = get_project_or_404(db, project_name)

    folders = body.get("folders")
    ignore = body.get("ignore")

    from dropbox_sync.engine import start_dry_run as engine_start_dry_run
    job_id = engine_start_dry_run(proj.name, proj.path, folders, ignore)
    return {"job_id": job_id}


@router.get("/api/dropbox/dry-run/{job_id}")
async def get_dry_run(job_id: str):
    from dropbox_sync.engine import get_dry_run as engine_get_dry_run
    result = engine_get_dry_run(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@router.delete("/api/dropbox/dry-run/{job_id}")
async def delete_dry_run(job_id: str):
    from dropbox_sync.engine import stop_dry_run
    if not stop_dry_run(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"detail": "ok"}


# ── Folder listing ───────────────────────────────────────────────────


@router.get("/api/projects/{name}/dropbox/folders")
async def get_folders(name: str, db: Session = Depends(get_db)):
    proj = get_project_or_404(db, name)

    folders_raw = proj.dropbox_folders
    selected = json.loads(folders_raw) if folders_raw else None

    from dropbox_sync.engine import list_folders
    entries = await asyncio.to_thread(list_folders, proj.path, selected)

    return {
        "project": proj.name,
        "path": proj.path,
        "remote_root": f"/{proj.name}",
        "entries": entries,
    }
