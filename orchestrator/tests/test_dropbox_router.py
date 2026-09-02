"""Tests for routers/dropbox.py — ASGI client tests."""

import json
import os
import time

import httpx
import pytest

from tests.dropbox_fake import FakeDropboxServer
from dropbox_sync.auth import TokenStore
from dropbox_sync.client import DropboxClient, Throttle


# ── Helpers ──────────────────────────────────────────────────────────


def _seed_token(sync_dir: str) -> None:
    """Write a valid token.json into the sync dir."""
    token_path = os.path.join(sync_dir, "token.json")
    os.makedirs(sync_dir, mode=0o700, exist_ok=True)
    data = {
        "app_key": "testkey1234",
        "refresh_token": "rt_test",
        "access_token": "sl.test_access",
        "expires_at": time.time() + 36000,
        "account_id": "dbid:test123",
        "account_name": "Test User",
        "email": "test@example.com",
        "scope": "files.content.write",
        "linked_at": "2026-01-01T00:00:00Z",
    }
    with open(token_path, "w") as f:
        json.dump(data, f)
    os.chmod(token_path, 0o600)


async def _instant_sleep(delay):
    pass


def _make_client(fake: FakeDropboxServer) -> DropboxClient:
    http = httpx.AsyncClient(transport=fake.transport())

    class _FakeTP:
        async def get_access_token(self, force_refresh=False):
            return "sl.fake"

    return DropboxClient(_FakeTP(), http=http, throttle=Throttle(0), sleep=_instant_sleep)


@pytest.fixture()
def sync_dir(tmp_path):
    """Create a fresh sync dir and reset the engine."""
    sd = str(tmp_path / "dropbox")
    os.makedirs(sd, mode=0o700, exist_ok=True)
    from dropbox_sync import engine
    engine.reset_for_tests(sd)
    yield sd
    engine.reset_for_tests(sd)


@pytest.fixture()
def fake():
    return FakeDropboxServer()


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_status_not_linked(client, sync_dir):
    """GET /api/dropbox/status when not linked."""
    resp = await client.get("/api/dropbox/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["linked"] is False
    assert data["account"] is None
    assert data["space"] is None
    assert isinstance(data["config"], dict)
    assert isinstance(data["queue"], list)


@pytest.mark.anyio
async def test_status_linked(client, sync_dir):
    """GET /api/dropbox/status when linked."""
    _seed_token(sync_dir)
    resp = await client.get("/api/dropbox/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["linked"] is True
    assert data["account"]["email"] == "test@example.com"


@pytest.mark.anyio
async def test_link_start_validation(client, sync_dir):
    """POST /api/dropbox/link/start with invalid key returns 400."""
    resp = await client.post("/api/dropbox/link/start", json={"app_key": "short"})
    assert resp.status_code == 400

    resp2 = await client.post("/api/dropbox/link/start", json={"app_key": ""})
    assert resp2.status_code == 400


@pytest.mark.anyio
async def test_link_start_409_when_linked(client, sync_dir):
    """POST /api/dropbox/link/start returns 409 when already linked."""
    _seed_token(sync_dir)
    resp = await client.post(
        "/api/dropbox/link/start",
        json={"app_key": "abcdefghij1234"},
    )
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_link_complete_via_fake(client, sync_dir, fake):
    """Full link flow: start -> complete via FakeDropboxServer."""
    from dropbox_sync import engine

    # Set up fake OAuth
    fake.register_oauth_code("test_auth_code", {
        "access_token": "sl.linked_token",
        "token_type": "bearer",
        "expires_in": 14400,
        "refresh_token": "rt_linked",
        "scope": "files.content.write account_info.read",
    })

    # Override the link flow to use the fake's HTTP transport
    fake_http = httpx.AsyncClient(transport=fake.transport())
    from dropbox_sync.auth import LinkFlow
    store = engine.get_token_store()
    flow = LinkFlow(store, http=fake_http)
    engine._link_flow = flow

    # Start
    resp = await client.post(
        "/api/dropbox/link/start",
        json={"app_key": "abcdefghij1234"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "authorize_url" in data
    assert "state" in data

    # Complete
    resp2 = await client.post(
        "/api/dropbox/link/complete",
        json={"code": "test_auth_code"},
    )
    assert resp2.status_code == 200
    result = resp2.json()
    assert result["detail"] == "ok"
    assert result["account"]["email"] == "test@example.com"

    # Now linked
    assert store.is_linked

    await fake_http.aclose()


@pytest.mark.anyio
async def test_config_put_validation(client, sync_dir):
    """PUT /api/dropbox/config validates chunk_mb."""
    # chunk_mb must be multiple of 4
    resp = await client.put("/api/dropbox/config", json={"chunk_mb": 5})
    assert resp.status_code == 400

    resp2 = await client.put("/api/dropbox/config", json={"chunk_mb": 148})
    assert resp2.status_code == 400

    # Valid
    resp3 = await client.put("/api/dropbox/config", json={"chunk_mb": 8})
    assert resp3.status_code == 200
    assert resp3.json()["chunk_mb"] == 8

    # interval_hours < 1
    resp4 = await client.put("/api/dropbox/config", json={"interval_hours": 0})
    assert resp4.status_code == 400


@pytest.mark.anyio
async def test_sync_409_not_linked(client, sync_dir):
    """POST /api/dropbox/sync returns 409 when not linked."""
    resp = await client.post("/api/dropbox/sync", json={})
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_sync_404_unknown_project(client, sync_dir):
    """POST /api/dropbox/sync with unknown project returns 404."""
    _seed_token(sync_dir)
    resp = await client.post("/api/dropbox/sync", json={"project": "nonexistent"})
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_sync_400_not_enabled(client, sync_dir, db_session):
    """POST /api/dropbox/sync with non-enabled project returns 400."""
    _seed_token(sync_dir)

    from models import Project
    proj = Project(
        name="notsync",
        display_name="Not Sync",
        path="/tmp/notsync",
        max_concurrent=2,
        default_model="claude-opus-4-7",
        dropbox_sync=False,
    )
    db_session.add(proj)
    db_session.commit()

    resp = await client.post("/api/dropbox/sync", json={"project": "notsync"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_pause_resume(client, sync_dir):
    """POST /api/dropbox/pause and /api/dropbox/resume."""
    resp = await client.post("/api/dropbox/pause")
    assert resp.status_code == 200
    assert resp.json()["paused"] is True

    resp2 = await client.post("/api/dropbox/resume")
    assert resp2.status_code == 200
    assert resp2.json()["paused"] is False


@pytest.mark.anyio
async def test_dry_run_lifecycle(client, sync_dir, db_session):
    """POST dry-run, GET status, DELETE to stop."""
    from models import Project

    proj_dir = os.path.join(str(sync_dir), "proj_dry")
    os.makedirs(proj_dir, exist_ok=True)
    with open(os.path.join(proj_dir, "hello.txt"), "w") as f:
        f.write("hello")

    proj = Project(
        name="dryproj",
        display_name="Dry Project",
        path=proj_dir,
        max_concurrent=2,
        default_model="claude-opus-4-7",
    )
    db_session.add(proj)
    db_session.commit()

    # Start dry run
    resp = await client.post("/api/dropbox/dry-run", json={"project": "dryproj"})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # Poll until done
    import asyncio
    for _ in range(50):
        resp2 = await client.get(f"/api/dropbox/dry-run/{job_id}")
        assert resp2.status_code == 200
        data = resp2.json()
        if data["status"] != "running":
            break
        await asyncio.sleep(0.05)

    assert data["status"] in ("complete", "error")

    # Delete
    resp3 = await client.delete(f"/api/dropbox/dry-run/{job_id}")
    assert resp3.status_code == 200

    # Unknown job
    resp4 = await client.get("/api/dropbox/dry-run/nonexistent")
    assert resp4.status_code == 404


@pytest.mark.anyio
async def test_folders_for_project(client, sync_dir, db_session):
    """GET /api/projects/{name}/dropbox/folders."""
    from models import Project

    proj_dir = os.path.join(str(sync_dir), "folderproj")
    os.makedirs(os.path.join(proj_dir, "src"), exist_ok=True)
    with open(os.path.join(proj_dir, "readme.txt"), "w") as f:
        f.write("hello")

    proj = Project(
        name="folderproj",
        display_name="Folder Project",
        path=proj_dir,
        max_concurrent=2,
        default_model="claude-opus-4-7",
    )
    db_session.add(proj)
    db_session.commit()

    resp = await client.get("/api/projects/folderproj/dropbox/folders")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project"] == "folderproj"
    assert data["remote_root"] == "/folderproj"
    assert isinstance(data["entries"], list)
    names = [e["name"] for e in data["entries"]]
    assert "src" in names


@pytest.mark.anyio
async def test_project_dropbox_status(client, sync_dir, db_session):
    """GET /api/projects/{name}/dropbox/status."""
    _seed_token(sync_dir)

    from models import Project

    proj_dir = os.path.join(str(sync_dir), "statusproj")
    os.makedirs(proj_dir, exist_ok=True)

    proj = Project(
        name="statusproj",
        display_name="Status Project",
        path=proj_dir,
        max_concurrent=2,
        default_model="claude-opus-4-7",
        dropbox_sync=True,
    )
    db_session.add(proj)
    db_session.commit()

    resp = await client.get("/api/projects/statusproj/dropbox/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["linked"] is True
    assert data["enabled"] is True
    assert data["account_email"] == "test@example.com"


@pytest.mark.anyio
async def test_settings_patch_dropbox_folders(client, sync_dir, db_session):
    """PATCH project settings with dropbox_folders still works."""
    from models import Project

    proj_dir = os.path.join(str(sync_dir), "patchproj")
    os.makedirs(proj_dir, exist_ok=True)

    proj = Project(
        name="patchproj",
        display_name="Patch Project",
        path=proj_dir,
        max_concurrent=2,
        default_model="claude-opus-4-7",
    )
    db_session.add(proj)
    db_session.commit()

    resp = await client.patch(
        "/api/projects/patchproj/settings",
        json={"dropbox_folders": ["src", "docs"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert json.loads(data["dropbox_folders"]) == ["docs", "src"]  # sorted

    # Clear
    resp2 = await client.patch(
        "/api/projects/patchproj/settings",
        json={"dropbox_folders": None},
    )
    assert resp2.status_code == 200
    assert resp2.json()["dropbox_folders"] is None


@pytest.mark.anyio
async def test_unlink_404_when_not_linked(client, sync_dir):
    """DELETE /api/dropbox/link returns 404 when not linked."""
    resp = await client.delete("/api/dropbox/link")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_unlink_when_linked(client, sync_dir):
    """DELETE /api/dropbox/link deletes token."""
    _seed_token(sync_dir)
    from dropbox_sync import engine
    assert engine.get_token_store().is_linked

    resp = await client.delete("/api/dropbox/link")
    assert resp.status_code == 200
    assert resp.json()["detail"] == "ok"
    assert not engine.get_token_store().is_linked
