"""Tests for per-project upload storage and git-exclude management."""

import asyncio
import os
import subprocess

import pytest
from sqlalchemy.orm import sessionmaker

from models import Project
from uploads import ensure_git_exclude


# ── Git-exclude helper (synchronous, real git repos in tmp_path) ───────


def test_exclude_added_to_git_repo(tmp_path):
    """After ensure_git_exclude, .git/info/exclude contains `.xylocopa/`."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    ensure_git_exclude(str(tmp_path))
    exclude = tmp_path / ".git" / "info" / "exclude"
    assert exclude.exists()
    lines = exclude.read_text().splitlines()
    assert ".xylocopa/" in lines


def test_exclude_idempotent(tmp_path):
    """Calling ensure_git_exclude twice does not duplicate the line."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    ensure_git_exclude(str(tmp_path))
    ensure_git_exclude(str(tmp_path))
    exclude = tmp_path / ".git" / "info" / "exclude"
    lines = exclude.read_text().splitlines()
    assert lines.count(".xylocopa/") == 1


def test_exclude_no_git(tmp_path):
    """No error when the project dir has no .git at all."""
    ensure_git_exclude(str(tmp_path))  # should silently return


def test_exclude_worktree(tmp_path):
    """Worktree-style .git FILE: exclude written in the main repo's git dir."""
    main = tmp_path / "main"
    main.mkdir()
    subprocess.run(["git", "init", str(main)], capture_output=True, check=True)
    # Need at least one commit to create a worktree
    subprocess.run(
        ["git", "-C", str(main), "commit", "--allow-empty", "-m", "init"],
        capture_output=True, check=True,
    )
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", str(wt), "-b", "wt-branch"],
        capture_output=True, check=True,
    )
    # wt/.git is a FILE, not a dir
    assert (wt / ".git").is_file()

    ensure_git_exclude(str(wt))

    # The exclude must be in the MAIN repo's git dir, not in the worktree
    main_git_dir = main / ".git"
    exclude = main_git_dir / "info" / "exclude"
    assert exclude.exists()
    lines = exclude.read_text().splitlines()
    assert ".xylocopa/" in lines


# ── Upload endpoint (ASGI client) ─────────────────────────────────────


@pytest.mark.anyio
async def test_upload_with_project(client, db_engine, tmp_path):
    """Upload with a valid project stores in <project>/.xylocopa/uploads/."""
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(
        name="upload-proj",
        display_name="Upload Project",
        path=str(tmp_path),
        max_concurrent=2,
        default_model="claude-opus-4-7",
    ))
    db.commit()
    db.close()

    resp = await client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        data={"project": "upload-proj"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["storage"] == "project"
    assert body["project"] == "upload-proj"
    assert body["original_name"] == "hello.txt"
    assert body["size"] == 11

    # File actually on disk in the project dir
    assert os.path.isfile(body["path"])
    assert body["path"].startswith(str(tmp_path))
    assert "/.xylocopa/uploads/" in body["path"]
    # Filename is sanitised (hex prefix)
    assert body["filename"] == os.path.basename(body["path"])
    assert "_" in body["filename"]  # <12hex>_<name>


@pytest.mark.anyio
async def test_upload_unknown_project_falls_back(client, db_engine):
    """Upload with an unknown project name falls back to global storage."""
    resp = await client.post(
        "/api/upload",
        files={"file": ("test.png", b"\x89PNG", "image/png")},
        data={"project": "no-such-project"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["storage"] == "global"
    assert body["project"] is None


@pytest.mark.anyio
async def test_upload_no_project_global(client, db_engine):
    """Upload without project field uses global storage."""
    resp = await client.post(
        "/api/upload",
        files={"file": ("test.txt", b"data", "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["storage"] == "global"
    assert body["project"] is None


@pytest.mark.anyio
async def test_upload_git_exclude_written(client, db_engine, tmp_path):
    """Upload to a git-init'd project adds .xylocopa/ to git exclude."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=True)
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(
        name="git-proj",
        display_name="Git Project",
        path=str(tmp_path),
        max_concurrent=2,
        default_model="claude-opus-4-7",
    ))
    db.commit()
    db.close()

    resp = await client.post(
        "/api/upload",
        files={"file": ("a.txt", b"a", "text/plain")},
        data={"project": "git-proj"},
    )
    assert resp.status_code == 200

    # Upload a second time to verify idempotency
    resp2 = await client.post(
        "/api/upload",
        files={"file": ("b.txt", b"b", "text/plain")},
        data={"project": "git-proj"},
    )
    assert resp2.status_code == 200

    # Give the background task a moment to run
    await asyncio.sleep(0.3)

    exclude = tmp_path / ".git" / "info" / "exclude"
    assert exclude.exists()
    lines = exclude.read_text().splitlines()
    assert ".xylocopa/" in lines
    assert lines.count(".xylocopa/") == 1


# ── Serving project uploads via /api/files/ ────────────────────────────


@pytest.mark.anyio
async def test_serve_project_upload(client, db_engine, tmp_path):
    """GET /api/files/<project>/.xylocopa/uploads/<name> returns the bytes."""
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(
        name="serve-proj",
        display_name="Serve Project",
        path=str(tmp_path),
        max_concurrent=2,
        default_model="claude-opus-4-7",
    ))
    db.commit()
    db.close()

    # Create the upload file manually
    upload_dir = tmp_path / ".xylocopa" / "uploads"
    upload_dir.mkdir(parents=True)
    upload_file = upload_dir / "abc123_photo.png"
    upload_file.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    resp = await client.get("/api/files/serve-proj/.xylocopa/uploads/abc123_photo.png")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG\r\n\x1a\nfake"


# ── Storage chart includes project uploads ─────────────────────────────


@pytest.mark.anyio
async def test_storage_counts_project_uploads(client, db_engine, tmp_path):
    """The Uploads category in /api/system/storage includes per-project uploads."""
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(
        name="storage-proj",
        display_name="Storage Project",
        path=str(tmp_path),
        max_concurrent=2,
        default_model="claude-opus-4-7",
    ))
    db.commit()
    db.close()

    # Create a file in the project's upload dir
    upload_dir = tmp_path / ".xylocopa" / "uploads"
    upload_dir.mkdir(parents=True)
    (upload_dir / "test_file.txt").write_bytes(b"x" * 100)

    resp = await client.get("/api/system/storage")
    assert resp.status_code == 200
    data = resp.json()
    uploads_cat = next(c for c in data["categories"] if c["name"] == "Uploads")
    # The count must include at least our one test file
    assert uploads_cat["file_count"] >= 1
    assert uploads_cat["size_bytes"] >= 100


# ── Project tree hides .xylocopa ───────────────────────────────────────


@pytest.mark.anyio
async def test_tree_hides_xylocopa_dir(client, db_engine, tmp_path):
    """.xylocopa/ must not appear in GET /api/projects/{name}/tree."""
    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(
        name="tree-proj",
        display_name="Tree Project",
        path=str(tmp_path),
        max_concurrent=2,
        default_model="claude-opus-4-7",
    ))
    db.commit()
    db.close()

    # Create the .xylocopa dir and a normal dir
    (tmp_path / ".xylocopa" / "uploads").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass")

    resp = await client.get("/api/projects/tree-proj/tree")
    assert resp.status_code == 200
    tree = resp.json()["tree"]
    names = [item["name"] for item in tree]
    assert ".xylocopa" not in names
    assert "src" in names


@pytest.mark.anyio
@pytest.mark.parametrize("sync_enabled", [True, False])
async def test_upload_requests_a_dropbox_check_only_for_synced_projects(client, db_engine, tmp_path, monkeypatch, sync_enabled):
    """An attachment in a synced project asks the Dropbox loop to check soon."""
    from dropbox_sync import engine

    Session = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    db = Session()
    db.add(Project(
        name="upload-sync-proj",
        display_name="Upload Sync Project",
        path=str(tmp_path),
        max_concurrent=2,
        default_model="claude-opus-4-7",
        dropbox_sync=sync_enabled,
    ))
    db.commit()
    db.close()

    calls = []
    monkeypatch.setattr(engine, "request_check", lambda delay_seconds=5.0: calls.append(delay_seconds))

    resp = await client.post(
        "/api/upload",
        files={"file": ("note.txt", b"x", "text/plain")},
        data={"project": "upload-sync-proj"},
    )
    assert resp.status_code == 200 and resp.json()["storage"] == "project"
    assert (len(calls) == 1) is sync_enabled
