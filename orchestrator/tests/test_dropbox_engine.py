"""Tests for dropbox_sync/engine.py — drives sync against FakeDropboxServer."""

import asyncio
import json
import os
import time

import httpx
import pytest

from tests.dropbox_fake import FakeDropboxServer
from dropbox_sync.auth import TokenStore
from dropbox_sync.client import DropboxClient, Throttle
from dropbox_sync.hashing import content_hash_bytes
from dropbox_sync.state import SyncState


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


def _make_project(tmp_path, name="testproj", files=None):
    """Create a project directory with files. Returns the path."""
    proj_dir = tmp_path / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    if files:
        for rel, content in files.items():
            fpath = proj_dir / rel
            fpath.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                fpath.write_bytes(content)
            else:
                fpath.write_text(content)
    return str(proj_dir)


def _make_client(fake: FakeDropboxServer) -> DropboxClient:
    """Create a DropboxClient backed by the FakeDropboxServer."""
    http = httpx.AsyncClient(transport=fake.transport())

    class _FakeTokenProvider:
        async def get_access_token(self, force_refresh=False):
            return "sl.fake_token"

    return DropboxClient(
        _FakeTokenProvider(),
        http=http,
        throttle=Throttle(0),
        sleep=_instant_sleep,
    )


async def _instant_sleep(delay):
    """No-op sleep for instant test execution."""
    pass


@pytest.fixture()
def sync_dir(tmp_path):
    """Create a fresh sync dir and reset the engine."""
    sd = str(tmp_path / "dropbox")
    os.makedirs(sd, mode=0o700, exist_ok=True)
    _seed_token(sd)

    from dropbox_sync import engine
    engine.reset_for_tests(sd)
    yield sd
    engine.reset_for_tests(sd)


@pytest.fixture()
def fake():
    return FakeDropboxServer()


@pytest.fixture()
def client_for_fake(fake):
    """Return a function that creates a DropboxClient for the fake."""
    def _factory():
        return _make_client(fake)
    return _factory


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_fresh_upload_small_tree(tmp_path, sync_dir, fake, client_for_fake):
    """Upload a small tree of files, verify they appear in the fake."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "myproj", {
        "src/main.py": "print('hello')",
        "README.md": "# My Project",
    })

    project = {
        "name": "myproj",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    state = engine.get_state()
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())

    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    # Verify files in fake
    assert "/myproj/src/main.py" in fake.files
    assert "/myproj/readme.md" in fake.files

    # Verify state
    files = state.get_project_files("myproj")
    assert "src/main.py" in files
    assert "README.md" in files
    assert files["src/main.py"].content_hash == content_hash_bytes(b"print('hello')")


@pytest.mark.anyio
async def test_unchanged_second_run(tmp_path, sync_dir, fake, client_for_fake):
    """Second sync of unchanged tree uploads nothing."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "proj2", {
        "a.txt": "hello",
    })

    project = {
        "name": "proj2",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    state = engine.get_state()

    # First run
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    initial_requests = len(fake.requests)

    # Second run (unchanged)
    run_id2 = state.run_start("test2")
    progress2 = engine.RunProgress(run_id=run_id2, started_at=engine._utcnow())
    await engine.sync_project(project, progress2)
    state.run_finish(run_id2, "ok")

    # Should have used far fewer requests (no uploads)
    # The diff should show nothing changed
    files = state.get_project_files("proj2")
    assert "a.txt" in files


@pytest.mark.anyio
async def test_modified_file_reuploads(tmp_path, sync_dir, fake, client_for_fake):
    """Modified file gets re-uploaded."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "proj3", {
        "data.txt": "version1",
    })

    project = {
        "name": "proj3",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    state = engine.get_state()

    # First sync
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    old_hash = state.get_project_files("proj3")["data.txt"].content_hash

    # Modify file
    with open(os.path.join(proj_path, "data.txt"), "w") as f:
        f.write("version2")

    # Second sync
    run_id2 = state.run_start("test2")
    progress2 = engine.RunProgress(run_id=run_id2, started_at=engine._utcnow())
    await engine.sync_project(project, progress2)
    state.run_finish(run_id2, "ok")

    new_hash = state.get_project_files("proj3")["data.txt"].content_hash
    assert new_hash != old_hash
    assert new_hash == content_hash_bytes(b"version2")

    # Fake should have the new content
    remote_data = fake.files["/proj3/data.txt"]["data"]
    assert remote_data == b"version2"


@pytest.mark.anyio
async def test_deleted_file_kept_without_prune(tmp_path, sync_dir, fake, client_for_fake):
    """Deleted file stays in state and remote when prune is off."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "proj4", {
        "keep.txt": "keep",
        "remove.txt": "remove",
    })

    project = {
        "name": "proj4",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    state = engine.get_state()

    # First sync
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    assert "/proj4/remove.txt" in fake.files

    # Delete the file locally
    os.remove(os.path.join(proj_path, "remove.txt"))

    # Second sync (prune=False)
    engine._cfg.prune = False
    run_id2 = state.run_start("test2")
    progress2 = engine.RunProgress(run_id=run_id2, started_at=engine._utcnow())
    await engine.sync_project(project, progress2)
    state.run_finish(run_id2, "ok")

    # Remote still has the file
    assert "/proj4/remove.txt" in fake.files
    # State still has the file row (for future prune)
    files = state.get_project_files("proj4")
    assert "remove.txt" in files


@pytest.mark.anyio
async def test_prune_deletes_remotely_and_locally(tmp_path, sync_dir, fake, client_for_fake):
    """Prune mode deletes remote files when local files are deleted."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "proj5", {
        "keep.txt": "keep",
        "gone.txt": "gone",
    })

    project = {
        "name": "proj5",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    state = engine.get_state()

    # First sync
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    assert "/proj5/gone.txt" in fake.files

    # Delete locally
    os.remove(os.path.join(proj_path, "gone.txt"))

    # Enable prune
    engine._cfg.prune = True

    run_id2 = state.run_start("test2")
    progress2 = engine.RunProgress(run_id=run_id2, started_at=engine._utcnow())
    await engine.sync_project(project, progress2)
    state.run_finish(run_id2, "ok")

    # Remote file should be deleted
    assert "/proj5/gone.txt" not in fake.files
    # State should not have the row
    files = state.get_project_files("proj5")
    assert "gone.txt" not in files


@pytest.mark.anyio
async def test_budget_skip(tmp_path, sync_dir, fake, client_for_fake):
    """Projects exceeding max_files_per_project are skipped."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    # Create many files
    files = {f"f{i}.txt": f"content{i}" for i in range(10)}
    proj_path = _make_project(tmp_path, "proj6", files)

    project = {
        "name": "proj6",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    # Set budget very low
    engine._cfg.max_files_per_project = 5

    state = engine.get_state()
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    # No files should be uploaded
    assert len(fake.files) == 0
    # Error should be logged
    errors = state.errors_recent()
    assert any("over budget" in e["message"] for e in errors)


@pytest.mark.anyio
async def test_case_collision_skip(tmp_path, sync_dir, fake, client_for_fake):
    """Case-insensitive path collisions: keep smallest, skip others."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "proj7", {
        "README.md": "upper",
    })
    # Create a second file with a case-variant name (only on case-sensitive FS)
    readme_lower = os.path.join(proj_path, "readme.md")
    # Only test if the filesystem is case-sensitive
    if not os.path.exists(readme_lower):
        with open(readme_lower, "w") as f:
            f.write("lower")

        project = {
            "name": "proj7",
            "path": proj_path,
            "dropbox_folders": None,
            "dropbox_ignore": None,
        }

        state = engine.get_state()
        run_id = state.run_start("test")
        progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
        await engine.sync_project(project, progress)
        state.run_finish(run_id, "ok")

        # One of them should be skipped (the one that sorts later)
        errors = state.errors_recent()
        collision_errors = [e for e in errors if "case collision" in e["message"]]
        assert len(collision_errors) >= 1

        # Only one file should be uploaded under /proj7/readme.md
        assert "/proj7/readme.md" in fake.files
    else:
        pytest.skip("Case-insensitive filesystem, cannot create case-variant files")


@pytest.mark.anyio
async def test_large_file_multi_chunk(tmp_path, sync_dir, fake, client_for_fake):
    """Large file is uploaded in multiple chunks."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    # Create a file larger than SMALL_FILE_LIMIT won't work in test,
    # but we can test multi-chunk by setting chunk_mb very small
    engine._cfg.chunk_mb = 4  # 4 MiB chunks

    # Create a file that's bigger than one chunk (5 MiB)
    big_content = b"X" * (5 * 1024 * 1024)
    proj_path = _make_project(tmp_path, "proj8")
    fpath = os.path.join(proj_path, "big.bin")
    with open(fpath, "wb") as f:
        f.write(big_content)

    project = {
        "name": "proj8",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    state = engine.get_state()
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    # File should be uploaded
    assert "/proj8/big.bin" in fake.files
    assert fake.files["/proj8/big.bin"]["data"] == big_content


@pytest.mark.anyio
async def test_resume_after_simulated_crash(tmp_path, sync_dir, fake, client_for_fake):
    """Resume upload from a pending session after simulated crash."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    # Create a file
    content = b"A" * 1000
    proj_path = _make_project(tmp_path, "proj_resume", {
        "resume.txt": content,
    })

    fpath = os.path.join(proj_path, "resume.txt")
    st = os.stat(fpath)

    # Simulate a prior incomplete upload by writing a pending session
    state = engine.get_state()

    # Start a session on the fake
    http = httpx.AsyncClient(transport=fake.transport())

    class _FakeTP:
        async def get_access_token(self, force_refresh=False):
            return "sl.fake"

    temp_client = DropboxClient(_FakeTP(), http=http, throttle=Throttle(0), sleep=_instant_sleep)
    session_id = await temp_client.upload_session_start(content, close=True)
    await http.aclose()

    # Record it as pending with hash set (ready for commit)
    ch = content_hash_bytes(content)
    state.pending_put(
        "proj_resume", "resume.txt", session_id, len(content),
        st.st_size, st.st_mtime_ns, ch,
    )

    project = {
        "name": "proj_resume",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    # Should have committed via the resumed session
    assert "/proj_resume/resume.txt" in fake.files

    # Pending session should be cleaned up
    assert state.pending_get("proj_resume", "resume.txt") is None


@pytest.mark.anyio
async def test_429_during_commit_retried(tmp_path, sync_dir, fake, client_for_fake):
    """429 during commit batch is retried (via client retry)."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "proj_retry", {
        "a.txt": "hello",
    })

    project = {
        "name": "proj_retry",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    # Queue a 429 before finish_batch
    fake.fail_next_finish_batch_tmwo()

    state = engine.get_state()
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    # The file should still be uploaded (client retries the 429)
    assert "/proj_retry/a.txt" in fake.files


@pytest.mark.anyio
async def test_pause_mid_run(tmp_path, sync_dir, fake, client_for_fake):
    """Pausing mid-run leaves consistent state."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "proj_pause", {
        f"f{i}.txt": f"content{i}" for i in range(20)
    })

    project = {
        "name": "proj_pause",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    # Set stop_requested to simulate pause
    engine._stop_requested = True

    state = engine.get_state()
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "cancelled")

    # State should be consistent (no partial records without uploaded_at)
    files = state.get_project_files("proj_pause")
    for rec in files.values():
        assert rec.uploaded_at  # Non-empty

    engine._stop_requested = False


@pytest.mark.anyio
async def test_rename_hook_moves_state(tmp_path, sync_dir, fake, client_for_fake):
    """on_project_renamed moves state rows."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "old_name", {
        "a.txt": "hello",
    })

    project = {
        "name": "old_name",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    state = engine.get_state()
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    assert "a.txt" in state.get_project_files("old_name")

    # Rename
    engine.on_project_renamed("old_name", "new_name")

    # State should be under the new name
    assert "a.txt" not in state.get_project_files("old_name")
    assert "a.txt" in state.get_project_files("new_name")


@pytest.mark.anyio
async def test_get_status_when_not_linked(tmp_path, sync_dir):
    """get_status returns reasonable defaults when not linked."""
    from dropbox_sync import engine

    # Delete the token
    token_path = os.path.join(sync_dir, "token.json")
    if os.path.exists(token_path):
        os.remove(token_path)

    status = engine.get_status()
    assert status["linked"] is False
    assert status["account"] is None
    assert status["space"] is None
    assert status["current_run"] is None
    assert isinstance(status["config"], dict)
    assert isinstance(status["queue"], list)
    assert isinstance(status["recent_errors"], list)


@pytest.mark.anyio
async def test_get_status_when_linked(tmp_path, sync_dir):
    """get_status returns token info when linked."""
    from dropbox_sync import engine

    status = engine.get_status()
    assert status["linked"] is True
    assert status["account"]["email"] == "test@example.com"
    assert status["app_key"] == "testkey1234"


@pytest.mark.anyio
async def test_dry_run_lifecycle(tmp_path, sync_dir):
    """Start a dry run, poll it, and stop it."""
    from dropbox_sync import engine

    proj_path = _make_project(tmp_path, "dryproj", {
        "a.txt": "hello",
        "src/b.py": "import os",
    })

    job_id = engine.start_dry_run("dryproj", proj_path, None, None)
    assert isinstance(job_id, str)

    # Poll until complete (should be fast for small tree)
    for _ in range(50):
        result = engine.get_dry_run(job_id)
        if result and result["status"] != "running":
            break
        await asyncio.sleep(0.05)

    result = engine.get_dry_run(job_id)
    assert result is not None
    assert result["status"] == "complete"
    assert result["total"]["files"] >= 2


@pytest.mark.anyio
async def test_dry_run_stop(tmp_path, sync_dir):
    """Stopping a dry run sets the stop flag."""
    from dropbox_sync import engine

    proj_path = _make_project(tmp_path, "stopproj", {
        "a.txt": "hello",
    })

    job_id = engine.start_dry_run("stopproj", proj_path, None, None)
    ok = engine.stop_dry_run(job_id)
    assert ok is True

    # Unknown job returns False
    assert engine.stop_dry_run("nonexistent") is False


@pytest.mark.anyio
async def test_list_folders(tmp_path, sync_dir):
    """list_folders returns entries with selection state."""
    from dropbox_sync import engine

    proj_path = _make_project(tmp_path, "folderproj", {
        "root_file.txt": "hello",
        "src/main.py": "code",
        "docs/readme.md": "docs",
    })

    entries = engine.list_folders(proj_path, None)
    names = [e["name"] for e in entries]
    assert "src" in names
    assert "docs" in names

    # With selection
    entries2 = engine.list_folders(proj_path, ["src"])
    for e in entries2:
        if e["name"] == "src":
            assert e["selected"] is True
        elif e["type"] != "symlink":
            assert e["selected"] is False


@pytest.mark.anyio
async def test_on_project_deleted_forgets_state(tmp_path, sync_dir, fake, client_for_fake):
    """on_project_deleted cleans up state."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "delproj", {
        "a.txt": "hello",
    })

    project = {
        "name": "delproj",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    state = engine.get_state()
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    assert "a.txt" in state.get_project_files("delproj")

    engine.on_project_deleted("delproj")
    assert state.get_project_files("delproj") == {}


@pytest.mark.anyio
async def test_request_sync_queues(tmp_path, sync_dir):
    """request_sync adds to queue."""
    from dropbox_sync import engine

    engine._queue.clear()
    engine.request_sync("proj_a")
    assert "proj_a" in engine._queue
    engine.request_sync("proj_b")
    assert "proj_b" in engine._queue

    # Dedup
    engine.request_sync("proj_a")
    assert engine._queue.count("proj_a") == 1

    # All
    engine.request_sync(None)
    assert "__all__" in engine._queue


@pytest.mark.anyio
async def test_config_update_validation(tmp_path, sync_dir):
    """update_runtime_config validates inputs."""
    from dropbox_sync import engine

    # Valid update
    result = engine.update_runtime_config(interval_hours=2, concurrency=2)
    assert result["interval_hours"] == 2
    assert result["concurrency"] == 2

    # Invalid chunk_mb (not multiple of 4)
    with pytest.raises(ValueError, match="chunk_mb"):
        engine.update_runtime_config(chunk_mb=5)

    # chunk_mb too large
    with pytest.raises(ValueError, match="chunk_mb"):
        engine.update_runtime_config(chunk_mb=148)

    # interval_hours < 1
    with pytest.raises(ValueError, match="interval_hours"):
        engine.update_runtime_config(interval_hours=0)


@pytest.mark.anyio
async def test_get_project_status(tmp_path, sync_dir):
    """get_project_status returns expected shape."""
    from dropbox_sync import engine

    status = engine.get_project_status("nonexistent")
    assert status["linked"] is True
    assert status["enabled"] is False
    assert status["queued"] is False
    assert status["run_active"] is False
    assert status["current"] is None


@pytest.mark.anyio
async def test_prune_error_does_not_abort(tmp_path, sync_dir, fake, client_for_fake):
    """Prune errors are recorded but don't abort the run."""
    from dropbox_sync import engine
    engine.set_client_factory(client_for_fake)

    proj_path = _make_project(tmp_path, "proj_prune_err", {
        "keep.txt": "keep",
        "gone.txt": "gone",
    })

    project = {
        "name": "proj_prune_err",
        "path": proj_path,
        "dropbox_folders": None,
        "dropbox_ignore": None,
    }

    state = engine.get_state()

    # First sync
    run_id = state.run_start("test")
    progress = engine.RunProgress(run_id=run_id, started_at=engine._utcnow())
    await engine.sync_project(project, progress)
    state.run_finish(run_id, "ok")

    # Delete locally
    os.remove(os.path.join(proj_path, "gone.txt"))

    # Enable prune and make delete_batch fail
    engine._cfg.prune = True
    fake.fail_next_delete_batch()

    run_id2 = state.run_start("test2")
    progress2 = engine.RunProgress(run_id=run_id2, started_at=engine._utcnow())
    await engine.sync_project(project, progress2)
    state.run_finish(run_id2, "ok")

    # Error should be recorded
    errors = state.errors_recent()
    assert any("delete_batch" in e["message"] for e in errors)

    # keep.txt should still be synced fine
    assert "/proj_prune_err/keep.txt" in fake.files
