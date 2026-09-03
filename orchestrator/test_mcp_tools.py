#!/usr/bin/env python3
"""Standalone test for the xylocopa MCP server tools.

Sets up a temporary XYLOCOPA_ROOT with an empty DB + registry, imports the
MCP server module (which picks up the env var), exercises every tool's
happy path + at least one error path, and tears down the temp dir.

Run from the orchestrator directory:
    ../.venv/bin/python test_mcp_tools.py

Exits 0 on success, 1 on first failure (with traceback + summary).
"""
import json
import os
import shutil
import sys
import tempfile
import traceback

_FAILED: list[tuple[str, str]] = []
_PASSED: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        _PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        _FAILED.append((name, detail))
        print(f"  FAIL  {name}: {detail}")


def _expect_in(name: str, needle: str, haystack: str) -> None:
    _check(name, needle in haystack, f"expected `{needle!r}` in output, got: {haystack[:200]!r}")


def _expect_not_in(name: str, needle: str, haystack: str) -> None:
    _check(name, needle not in haystack, f"unexpected `{needle!r}` in output: {haystack[:200]!r}")


def main() -> int:
    # --- temp env setup (must happen BEFORE importing mcp_server) ---
    temp_root = tempfile.mkdtemp(prefix="xy-mcp-test-")
    print(f"# Temp XYLOCOPA_ROOT = {temp_root}")

    try:
        os.makedirs(os.path.join(temp_root, "data"))
        os.makedirs(os.path.join(temp_root, "project-configs"))
        os.makedirs(os.path.join(temp_root, "data", "display"))

        with open(os.path.join(temp_root, "project-configs", "registry.yaml"), "w") as f:
            f.write("projects: []\n")

        # Build empty DB schema
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sqlalchemy import create_engine
        from models import Base

        db_path = os.path.join(temp_root, "data", "orchestrator.db")
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        engine.dispose()

        os.environ["XYLOCOPA_ROOT"] = temp_root

        import mcp_server as mcp

        # Sanity: tool registry should match what we expect
        all_tools = sorted(mcp.server._tool_manager._tools.keys())
        expected = {
            # project (5)
            "project_list", "project_get", "project_create",
            "project_scaffold", "project_regenerate_claude_md",
            # task (6 new + 4 aliases)
            "task_list", "task_get", "task_counts",
            "task_create", "task_update", "task_dispatch",
            "create_task", "update_task", "dispatch_task", "list_tasks",
            # session (3 new + 2 aliases)
            "session_list", "session_read", "session_tail",
            "list_sessions", "read_session",
            # agent (2)
            "agent_list", "agent_get",
            # dropbox (2)
            "dropbox_get", "dropbox_count",
            # system (1)
            "system_health",
        }
        missing = expected - set(all_tools)
        _check(
            "registry: all expected tools present",
            not missing,
            f"missing: {missing}",
        )

        # ----- system_health (read-only) -----
        out = mcp.system_health()
        _expect_in("system_health: reports DB OK", "db: OK", out)
        _expect_in("system_health: reports projects count", "projects: 0", out)

        # ----- project_list / project_get on empty DB -----
        out = mcp.project_list()
        _expect_in("project_list: empty case", "No projects found", out)

        out = mcp.project_get(name="does-not-exist")
        _expect_in("project_get: error path on missing", "not found", out)

        # ----- project_create: happy + idempotent + invalid name -----
        proj_path = os.path.join(temp_root, "test-project")
        out = mcp.project_create(
            name="test-mcp-proj",
            path=proj_path,
            description="MCP test project",
        )
        _expect_in("project_create: happy", "Created project", out)
        _check(
            "project_create: dir created",
            os.path.isdir(proj_path),
            f"path {proj_path} not a directory",
        )
        _check(
            "project_create: CLAUDE.md scaffolded",
            os.path.isfile(os.path.join(proj_path, "CLAUDE.md")),
            "CLAUDE.md missing",
        )

        out2 = mcp.project_create(name="test-mcp-proj", path=proj_path)
        _expect_in("project_create: idempotent re-call", "already exists", out2)

        out3 = mcp.project_create(name="bad name with spaces")
        _expect_in("project_create: invalid name rejected", "Invalid project name", out3)

        # ----- project_list with content -----
        out = mcp.project_list()
        _expect_in("project_list: shows created project", "test-mcp-proj", out)

        # ----- project_get on real project -----
        out = mcp.project_get(name="test-mcp-proj")
        _expect_in("project_get: shows path", proj_path, out)
        _expect_in("project_get: shows agent counts", "agents: 0", out)
        _expect_in("project_get: shows task counts", "tasks: ", out)

        # ----- project_scaffold: idempotent (no change) -----
        out = mcp.project_scaffold(name="test-mcp-proj")
        _expect_in("project_scaffold: no-op when scaffolded", "no changes", out)

        out = mcp.project_scaffold(name="does-not-exist")
        _expect_in("project_scaffold: error on missing", "not found", out)

        # ----- project_regenerate_claude_md: regenerates -----
        out = mcp.project_regenerate_claude_md(name="test-mcp-proj")
        _expect_in("project_regenerate_claude_md: success", "Regenerated", out)

        out = mcp.project_regenerate_claude_md(name="does-not-exist")
        _expect_in("project_regenerate_claude_md: error path", "not found", out)

        # ----- task_create / task_get / task_list / task_update / task_dispatch / task_counts -----
        out = mcp.task_list()
        _expect_in("task_list: empty case", "No tasks found", out)

        out = mcp.task_create(
            title="Test MCP task",
            project="test-mcp-proj",
            description="hello world",
            priority=1,
        )
        _expect_in("task_create: happy", "Created task", out)
        # Extract task id from "Created task `<id>`"
        import re
        m = re.search(r"Created task `([^`]+)`", out)
        assert m, "no task id parsed"
        task_id = m.group(1)

        out = mcp.task_create(title="x", project="ghost-project")
        _expect_in("task_create: error on bad project", "not found", out)

        out = mcp.task_get(task_id=task_id)
        _expect_in("task_get: shows title", "Test MCP task", out)
        _expect_in("task_get: shows status", "INBOX", out)

        out = mcp.task_get(task_id="bogus")
        _expect_in("task_get: error path", "not found", out)

        out = mcp.task_list()
        _expect_in("task_list: shows new task", "Test MCP task", out)

        out = mcp.task_list(status="INBOX")
        _expect_in("task_list: filter by status", "Test MCP task", out)

        out = mcp.task_update(task_id=task_id, title="Renamed task")
        _expect_in("task_update: renames", "title", out)

        out = mcp.task_update(task_id=task_id)
        _expect_in("task_update: no-op", "no changes", out)

        out = mcp.task_update(task_id="bogus", title="x")
        _expect_in("task_update: error path", "not found", out)

        out = mcp.task_counts()
        _expect_in("task_counts: shows total", "INBOX: 1", out)

        out = mcp.task_counts(project="test-mcp-proj")
        _expect_in("task_counts: filter by project", "INBOX: 1", out)

        out = mcp.task_dispatch(task_id=task_id)
        _expect_in("task_dispatch: queues task", "queued for dispatch", out)

        out = mcp.task_dispatch(task_id=task_id)
        _expect_in("task_dispatch: idempotent on PENDING", "already PENDING", out)

        out = mcp.task_dispatch(task_id="bogus")
        _expect_in("task_dispatch: error path", "not found", out)

        # ----- session_list / session_read / session_tail (no sessions in test DB) -----
        out = mcp.session_list()
        _expect_in("session_list: empty case", "No sessions found", out)

        out = mcp.session_list(project="test-mcp-proj")
        _expect_in("session_list: empty with project filter", "No sessions found", out)

        out = mcp.session_read(session_id="ghost-session")
        _expect_in("session_read: error on missing", "No agent found", out)

        out = mcp.session_tail(session_id="ghost-session")
        _expect_in("session_tail: error on missing", "No agent found", out)

        # ----- agent_list / agent_get (no agents in test DB) -----
        out = mcp.agent_list()
        _expect_in("agent_list: empty case", "No agents found", out)

        out = mcp.agent_list(project="test-mcp-proj", status="RUNNING")
        _expect_in("agent_list: filtered empty", "No agents found", out)

        out = mcp.agent_get(agent_id="bogus")
        _expect_in("agent_get: error on missing", "No agent found", out)

        # ----- alias byte-equality (key contract) -----
        out_new = mcp.session_list()
        out_old = mcp.list_sessions()
        _check(
            "alias: list_sessions == session_list",
            out_new == out_old,
            "outputs differ",
        )

        out_new = mcp.task_list()
        out_old = mcp.list_tasks()
        _check(
            "alias: list_tasks == task_list",
            out_new == out_old,
            "outputs differ",
        )

        # task_create alias — outputs differ in task ID, so check structural similarity
        out_new = mcp.task_create(title="A1", project="test-mcp-proj")
        out_old = mcp.create_task(title="A2", project="test-mcp-proj")
        _check(
            "alias: create_task starts with same prefix",
            out_new.startswith("Created task ") and out_old.startswith("Created task "),
            f"new={out_new[:80]!r} old={out_old[:80]!r}",
        )

        # task_update / task_dispatch alias
        m = re.search(r"`([^`]+)`", out_old)
        alias_task = m.group(1)
        out_new_dispatch = mcp.task_dispatch(task_id=alias_task)
        # Reset the next created task to test old-name dispatch
        out2 = mcp.task_create(title="A3", project="test-mcp-proj")
        m2 = re.search(r"`([^`]+)`", out2)
        alias_task2 = m2.group(1)
        out_old_dispatch = mcp.dispatch_task(task_id=alias_task2)
        _check(
            "alias: dispatch_task behaves like task_dispatch",
            "queued for dispatch" in out_old_dispatch,
            out_old_dispatch,
        )

        out_new_update = mcp.task_update(task_id=alias_task, description="fresh")
        out_old_update = mcp.update_task(task_id=alias_task2, description="fresh")
        _check(
            "alias: update_task behaves like task_update",
            "description" in out_old_update,
            out_old_update,
        )

        # ----- dropbox_get: not configured (no config/state/token) -----
        out = mcp.dropbox_get()
        _expect_in("dropbox_get: not configured", "not configured", out)

        # ----- dropbox_get: with config + state (not linked) -----
        dropbox_dir = os.path.join(temp_root, "data", "dropbox")
        os.makedirs(dropbox_dir, exist_ok=True)

        # Write a minimal config.json
        with open(os.path.join(dropbox_dir, "config.json"), "w") as f:
            json.dump({"interval_minutes": 15, "paused": False}, f)

        # Create state.db via SyncState (allowed in the test script)
        from dropbox_sync.state import SyncState
        _state = SyncState(os.path.join(dropbox_dir, "state.db"))
        _state.close()

        out = mcp.dropbox_get()
        _expect_in("dropbox_get: not linked", "not linked", out)
        _expect_in("dropbox_get: shows interval", "15 min", out)

        # ----- dropbox_count: scan a temp project -----
        count_proj_path = os.path.join(temp_root, "count-proj")
        os.makedirs(os.path.join(count_proj_path, "src"), exist_ok=True)
        os.makedirs(os.path.join(count_proj_path, "node_modules", "pkg"), exist_ok=True)

        with open(os.path.join(count_proj_path, "README.md"), "w") as f:
            f.write("hello")
        with open(os.path.join(count_proj_path, "src", "main.py"), "w") as f:
            f.write("print('hi')")
        with open(os.path.join(count_proj_path, "node_modules", "pkg", "index.js"), "w") as f:
            f.write("module.exports = {}")

        out_create = mcp.project_create(name="count-test-proj", path=count_proj_path)
        _expect_in("dropbox_count: project created", "Created project", out_create)

        out = mcp.dropbox_count(project="count-test-proj")
        _expect_in("dropbox_count: shows src entry", "src", out)
        _expect_in("dropbox_count: shows root entry", ".", out)
        _expect_not_in("dropbox_count: excludes node_modules", "node_modules", out)
        _expect_in("dropbox_count: has total", "Total:", out)

        out = mcp.dropbox_count(project="nonexistent-proj")
        _expect_in("dropbox_count: error on missing project", "not found", out)

    except Exception:
        traceback.print_exc()
        _FAILED.append(("crash", "uncaught exception (see traceback above)"))
    finally:
        # cleanup
        shutil.rmtree(temp_root, ignore_errors=True)
        print(f"# Removed {temp_root}")

    print()
    print(f"# Summary: {len(_PASSED)} passed, {len(_FAILED)} failed")
    if _FAILED:
        print("# Failures:")
        for name, detail in _FAILED:
            print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
