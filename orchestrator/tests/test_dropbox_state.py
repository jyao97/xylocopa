"""Tests for dropbox_sync.state — SyncState, FileRecord, diff_entries."""

import os
import stat
import threading
from dataclasses import dataclass

import pytest

from dropbox_sync.state import FileRecord, SyncState, diff_entries


# Minimal duck type for ScanEntry (another agent writes scanner.py concurrently)
@dataclass(frozen=True)
class FakeScanEntry:
    rel_path: str
    size: int
    mtime_ns: int


# ── Schema ──


def test_schema_creation_idempotent(tmp_path):
    """Opening the same DB twice must not raise."""
    db = tmp_path / "state.db"
    s1 = SyncState(str(db))
    s1.close()
    s2 = SyncState(str(db))
    s2.close()


def test_directory_created_with_0700(tmp_path):
    nested = tmp_path / "sub" / "deep"
    db = nested / "state.db"
    s = SyncState(str(db))
    s.close()
    mode = stat.S_IMODE(os.stat(str(nested)).st_mode)
    assert mode == 0o700


def test_db_file_perms_0600(tmp_path):
    db = tmp_path / "state.db"
    s = SyncState(str(db))
    s.close()
    mode = stat.S_IMODE(os.stat(str(db)).st_mode)
    assert mode == 0o600


# ── files CRUD ──


def _make_state(tmp_path):
    return SyncState(str(tmp_path / "state.db"))


def test_upsert_get_files(tmp_path):
    s = _make_state(tmp_path)
    rec = FileRecord("proj", "a/b.py", 100, 999, "abc123", "rev1", "2026-01-01T00:00:00Z")
    s.upsert_files([rec])
    files = s.get_project_files("proj")
    assert "a/b.py" in files
    got = files["a/b.py"]
    assert got.project == "proj"
    assert got.size == 100
    assert got.mtime_ns == 999
    assert got.content_hash == "abc123"
    assert got.remote_rev == "rev1"
    s.close()


def test_upsert_replaces(tmp_path):
    s = _make_state(tmp_path)
    rec1 = FileRecord("proj", "f.py", 100, 1, "h1", None, "2026-01-01T00:00:00Z")
    s.upsert_files([rec1])
    rec2 = FileRecord("proj", "f.py", 200, 2, "h2", "r2", "2026-01-02T00:00:00Z")
    s.upsert_files([rec2])
    files = s.get_project_files("proj")
    assert files["f.py"].size == 200
    assert files["f.py"].content_hash == "h2"
    s.close()


def test_delete_files(tmp_path):
    s = _make_state(tmp_path)
    recs = [
        FileRecord("proj", "a.py", 10, 1, "h", None, "2026-01-01T00:00:00Z"),
        FileRecord("proj", "b.py", 20, 2, "h", None, "2026-01-01T00:00:00Z"),
    ]
    s.upsert_files(recs)
    s.delete_files("proj", ["a.py"])
    files = s.get_project_files("proj")
    assert "a.py" not in files
    assert "b.py" in files
    s.close()


def test_count_files(tmp_path):
    s = _make_state(tmp_path)
    assert s.count_files("proj") == (0, 0)
    recs = [
        FileRecord("proj", "a.py", 100, 1, "h", None, "2026-01-01T00:00:00Z"),
        FileRecord("proj", "b.py", 250, 2, "h", None, "2026-01-01T00:00:00Z"),
    ]
    s.upsert_files(recs)
    count, total_bytes = s.count_files("proj")
    assert count == 2
    assert total_bytes == 350
    s.close()


def test_forget_project(tmp_path):
    s = _make_state(tmp_path)
    s.upsert_files([FileRecord("proj", "f.py", 1, 1, "h", None, "2026-01-01T00:00:00Z")])
    s.pending_put("proj", "f.py", "sid", 0, 1, 1, "h")
    s.project_stats_update("proj", files_synced=1)
    s.forget_project("proj")
    assert s.get_project_files("proj") == {}
    assert s.pending_all("proj") == []
    assert s.project_stats_all() == {}
    s.close()


# ── diff_entries ──


def test_diff_new_entry():
    known: dict[str, FileRecord] = {}
    entries = [FakeScanEntry("new.py", 100, 999)]
    changed, deleted, unchanged = diff_entries(known, entries)
    assert len(changed) == 1
    assert changed[0].rel_path == "new.py"
    assert deleted == []
    assert unchanged == []


def test_diff_changed_size():
    known = {
        "f.py": FileRecord("p", "f.py", 100, 999, "h", None, "2026-01-01T00:00:00Z"),
    }
    entries = [FakeScanEntry("f.py", 200, 999)]
    changed, deleted, unchanged = diff_entries(known, entries)
    assert len(changed) == 1
    assert unchanged == []


def test_diff_changed_mtime():
    known = {
        "f.py": FileRecord("p", "f.py", 100, 999, "h", None, "2026-01-01T00:00:00Z"),
    }
    entries = [FakeScanEntry("f.py", 100, 1000)]
    changed, deleted, unchanged = diff_entries(known, entries)
    assert len(changed) == 1
    assert unchanged == []


def test_diff_unchanged():
    known = {
        "f.py": FileRecord("p", "f.py", 100, 999, "h", None, "2026-01-01T00:00:00Z"),
    }
    entries = [FakeScanEntry("f.py", 100, 999)]
    changed, deleted, unchanged = diff_entries(known, entries)
    assert changed == []
    assert deleted == []
    assert len(unchanged) == 1


def test_diff_deleted():
    known = {
        "old.py": FileRecord("p", "old.py", 50, 1, "h", None, "2026-01-01T00:00:00Z"),
    }
    entries: list[FakeScanEntry] = []
    changed, deleted, unchanged = diff_entries(known, entries)
    assert changed == []
    assert deleted == ["old.py"]
    assert unchanged == []


def test_diff_mixed():
    known = {
        "keep.py": FileRecord("p", "keep.py", 10, 1, "h", None, "2026-01-01T00:00:00Z"),
        "gone.py": FileRecord("p", "gone.py", 20, 2, "h", None, "2026-01-01T00:00:00Z"),
        "mod.py": FileRecord("p", "mod.py", 30, 3, "h", None, "2026-01-01T00:00:00Z"),
    }
    entries = [
        FakeScanEntry("keep.py", 10, 1),
        FakeScanEntry("mod.py", 30, 4),   # mtime changed
        FakeScanEntry("brand.py", 5, 5),  # new
    ]
    changed, deleted, unchanged = diff_entries(known, entries)
    changed_paths = {e.rel_path for e in changed}
    assert changed_paths == {"mod.py", "brand.py"}
    assert deleted == ["gone.py"]
    assert len(unchanged) == 1
    assert unchanged[0].rel_path == "keep.py"


# ── pending sessions ──


def test_pending_session_round_trip(tmp_path):
    s = _make_state(tmp_path)
    assert s.pending_get("proj", "f.py") is None
    s.pending_put("proj", "f.py", "sid123", 4096, 8192, 999, "hash1")
    got = s.pending_get("proj", "f.py")
    assert got is not None
    assert got["session_id"] == "sid123"
    assert got["offset"] == 4096
    assert got["size"] == 8192
    assert got["mtime_ns"] == 999
    assert got["content_hash"] == "hash1"

    all_pending = s.pending_all("proj")
    assert len(all_pending) == 1

    s.pending_delete("proj", "f.py")
    assert s.pending_get("proj", "f.py") is None
    assert s.pending_all("proj") == []
    s.close()


# ── runs ──


def test_run_lifecycle(tmp_path):
    s = _make_state(tmp_path)
    run_id = s.run_start("manual")
    assert isinstance(run_id, int)

    cur = s.run_current()
    assert cur is not None
    assert cur["id"] == run_id
    assert cur["status"] == "running"
    assert cur["trigger"] == "manual"

    s.run_update(run_id, project="myproj", files_scanned=10, files_uploaded=3, bytes_uploaded=5000)
    cur = s.run_current()
    assert cur["project"] == "myproj"
    assert cur["files_scanned"] == 10
    assert cur["files_uploaded"] == 3
    assert cur["bytes_uploaded"] == 5000

    s.run_finish(run_id, "ok")
    assert s.run_current() is None

    last = s.run_last()
    assert last is not None
    assert last["id"] == run_id
    assert last["status"] == "ok"
    assert last["finished_at"] is not None
    s.close()


def test_run_current_and_last_ordering(tmp_path):
    s = _make_state(tmp_path)
    r1 = s.run_start("schedule")
    s.run_finish(r1, "ok")
    r2 = s.run_start("manual")
    s.run_finish(r2, "error", error_sample="some error")
    r3 = s.run_start("schedule")

    cur = s.run_current()
    assert cur["id"] == r3

    last = s.run_last()
    assert last["id"] == r2
    assert last["error_sample"] == "some error"
    s.close()


def test_runs_recent_ordering(tmp_path):
    s = _make_state(tmp_path)
    ids = []
    for i in range(5):
        rid = s.run_start("schedule")
        s.run_finish(rid, "ok")
        ids.append(rid)

    recent = s.runs_recent(limit=3)
    assert len(recent) == 3
    assert recent[0]["id"] == ids[4]
    assert recent[1]["id"] == ids[3]
    assert recent[2]["id"] == ids[2]
    s.close()


def test_runs_mark_interrupted(tmp_path):
    s = _make_state(tmp_path)
    r1 = s.run_start("schedule")
    r2 = s.run_start("manual")
    count = s.runs_mark_interrupted()
    assert count == 2

    assert s.run_current() is None
    recent = s.runs_recent(limit=10)
    for r in recent:
        assert r["status"] == "interrupted"
        assert r["finished_at"] is not None
    s.close()


# ── project stats ──


def test_project_stats_update_and_merge(tmp_path):
    s = _make_state(tmp_path)
    s.project_stats_update("proj", files_synced=10, bytes_synced=5000)
    stats = s.project_stats_all()
    assert "proj" in stats
    assert stats["proj"]["files_synced"] == 10
    assert stats["proj"]["bytes_synced"] == 5000
    assert stats["proj"]["last_synced_at"] is None

    # Merge: update only some fields
    s.project_stats_update("proj", last_synced_at="2026-01-01T00:00:00Z")
    stats = s.project_stats_all()
    assert stats["proj"]["files_synced"] == 10  # unchanged
    assert stats["proj"]["last_synced_at"] == "2026-01-01T00:00:00Z"

    # Update existing field
    s.project_stats_update("proj", files_synced=20)
    stats = s.project_stats_all()
    assert stats["proj"]["files_synced"] == 20
    s.close()


# ── error log ──


def test_error_log_pruning(tmp_path):
    s = _make_state(tmp_path)
    for i in range(250):
        s.error_add("proj", f"file_{i}.py", f"error {i}")

    errors = s.errors_recent(limit=300)
    assert len(errors) <= 200

    # Most recent should be the last ones added
    assert errors[0]["message"] == "error 249"
    s.close()


def test_errors_recent_default(tmp_path):
    s = _make_state(tmp_path)
    for i in range(30):
        s.error_add("proj", None, f"msg {i}")
    recent = s.errors_recent()
    assert len(recent) == 20
    assert recent[0]["message"] == "msg 29"
    s.close()


# ── thread safety ──


def test_thread_safety_concurrent_upserts(tmp_path):
    s = _make_state(tmp_path)
    n_threads = 8
    n_per_thread = 50
    barrier = threading.Barrier(n_threads)
    errors: list[Exception] = []

    def worker(tid: int):
        try:
            barrier.wait(timeout=5)
            for i in range(n_per_thread):
                rec = FileRecord(
                    "proj",
                    f"t{tid}/file_{i}.py",
                    i * 10,
                    i,
                    f"hash_{tid}_{i}",
                    None,
                    "2026-01-01T00:00:00Z",
                )
                s.upsert_files([rec])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [], f"Thread errors: {errors}"
    files = s.get_project_files("proj")
    assert len(files) == n_threads * n_per_thread
    s.close()
