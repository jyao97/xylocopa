"""Reusable fake Dropbox server for testing — httpx.MockTransport handler."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx


# ── Content hash (local re-implementation, not imported from dropbox_sync) ──

_BLOCK_SIZE = 4 * 1024 * 1024


def _content_hash(data: bytes) -> str:
    """Compute a Dropbox content_hash from raw bytes."""
    block_digests = b""
    offset = 0
    while offset < len(data):
        block = data[offset:offset + _BLOCK_SIZE]
        block_digests += hashlib.sha256(block).digest()
        offset += _BLOCK_SIZE
    if not data:
        block_digests = b""
    return hashlib.sha256(block_digests).hexdigest()


# ── Fake Dropbox server ────────────────────────────────────────────────


class FakeDropboxServer:
    """In-process fake that emulates Dropbox API and content endpoints.

    Attributes:
        files: dict mapping path_lower → {data, rev, client_modified, content_hash}
        requests: list of (method, url, headers, body) tuples recorded
    """

    def __init__(self, *, async_mode: bool = False):
        self.files: dict[str, dict[str, Any]] = {}
        self.requests: list[dict] = []
        self._sessions: dict[str, dict] = {}  # session_id → {data, closed}
        self._async_mode = async_mode
        self._async_jobs: dict[str, dict] = {}  # job_id → {type, result, polls}
        self._fail_queue: list[dict] = []  # [{status, retry_after, body}]
        self._tmwo_next = False  # too_many_write_operations on next finish_batch
        self._delete_batch_fail_next = False  # make next delete_batch return .tag=failed
        self._finish_batch_always_pending = False  # never complete (for timeout test)
        self._finish_batch_auth_fail_count = 0  # number of 401s to return on finish_batch
        self._oauth_codes: dict[str, dict] = {}  # code → token data
        self._last_token_form: dict[str, str] | None = None  # last POST /oauth2/token form
        self._list_folder_cursors: dict[str, dict] = {}
        self._verify_content_hash = True  # verify content_hash in commits when present
        self._space_usage: dict[str, Any] | None = None  # custom space usage response
        self._account = {
            "account_id": "dbid:test123",
            "name": {
                "given_name": "Test",
                "surname": "User",
                "familiar_name": "Test",
                "display_name": "Test User",
                "abbreviated_name": "TU",
            },
            "email": "test@example.com",
            "email_verified": True,
            "disabled": False,
            "locale": "en",
            "referral_link": "https://db.tt/test",
            "is_paired": False,
            "account_type": {".tag": "basic"},
        }

    def register_oauth_code(self, code: str, token_data: dict) -> None:
        """Pre-register an authorization code for the fake OAuth endpoint."""
        self._oauth_codes[code] = token_data

    def fail_next(self, n: int, *, status: int = 429, retry_after: float | None = None,
                  body: dict | None = None) -> None:
        """Queue *n* failures to be returned before the real response."""
        for _ in range(n):
            entry: dict[str, Any] = {"status": status}
            if retry_after is not None:
                entry["retry_after"] = retry_after
            if body is not None:
                entry["body"] = body
            self._fail_queue.append(entry)

    def fail_next_finish_batch_tmwo(self) -> None:
        """Make the next finish_batch return too_many_write_operations."""
        self._tmwo_next = True

    def fail_next_delete_batch(self) -> None:
        """Make the next delete_batch return .tag=failed."""
        self._delete_batch_fail_next = True

    def set_space_usage(self, usage: dict) -> None:
        """Override the default space_usage response."""
        self._space_usage = usage

    def set_finish_batch_always_pending(self) -> None:
        """Make finish_batch/check always return in_progress (for timeout tests)."""
        self._finish_batch_always_pending = True

    def fail_next_finish_batch_auth(self, count: int = 2) -> None:
        """Make the next *count* finish_batch calls return 401.

        The client retries once with a refreshed token, so count=2
        triggers DropboxAuthError.
        """
        self._finish_batch_auth_fail_count = count

    def invalidate_session(self, session_id: str) -> None:
        """Remove a session to simulate not_found on next use."""
        self._sessions.pop(session_id, None)

    def transport(self) -> httpx.MockTransport:
        """Return an httpx.MockTransport backed by this fake."""
        return httpx.MockTransport(self._handler)

    # ── Request handler ─────────────────────────────────────────────

    def _handler(self, request: httpx.Request) -> httpx.Response:
        # Record request
        body_bytes = request.read()
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                parsed_body = json.loads(body_bytes) if body_bytes else None
            except (json.JSONDecodeError, ValueError):
                parsed_body = None
            self.requests.append({
                "method": str(request.method),
                "url": str(request.url),
                "headers": dict(request.headers),
                "json": parsed_body,
            })
        else:
            self.requests.append({
                "method": str(request.method),
                "url": str(request.url),
                "headers": dict(request.headers),
                "bytes": body_bytes,
            })

        # Check fail queue
        if self._fail_queue:
            fail = self._fail_queue.pop(0)
            headers = {}
            if fail.get("retry_after") is not None:
                headers["Retry-After"] = str(fail["retry_after"])
            body = fail.get("body")
            if fail["status"] == 401:
                body = body or {"error_summary": "expired_access_token/...", "error": {".tag": "expired_access_token"}}
            elif fail["status"] == 429:
                body = body or {"error_summary": "too_many_requests/...", "error": {".tag": "too_many_requests"}}
            elif fail["status"] >= 500:
                body = body or {"error_summary": f"internal_server_error/{fail['status']}"}
            return httpx.Response(
                fail["status"],
                json=body or {},
                headers=headers,
            )

        # Route by URL path
        url = str(request.url)
        path = request.url.path

        # OAuth token endpoint
        if path == "/oauth2/token":
            return self._handle_oauth_token(request, body_bytes)

        # API host endpoints (RPC style)
        if path.startswith("/2/"):
            endpoint = path[3:]  # strip "/2/"
            return self._route_endpoint(endpoint, request, body_bytes)

        return httpx.Response(404, json={"error_summary": "not_found"})

    def _route_endpoint(self, endpoint: str, request: httpx.Request,
                        body_bytes: bytes) -> httpx.Response:
        handlers = {
            "users/get_current_account": self._handle_get_current_account,
            "users/get_space_usage": self._handle_get_space_usage,
            "files/upload_session/start": self._handle_upload_session_start,
            "files/upload_session/append_v2": self._handle_upload_session_append,
            "files/upload_session/finish_batch": self._handle_finish_batch,
            "files/upload_session/finish_batch/check": self._handle_finish_batch_check,
            "files/delete_batch": self._handle_delete_batch,
            "files/delete_batch/check": self._handle_delete_batch_check,
            "files/list_folder": self._handle_list_folder,
            "files/list_folder/continue": self._handle_list_folder_continue,
            "files/get_metadata": self._handle_get_metadata,
            "files/move_v2": self._handle_move_v2,
            "auth/token/revoke": self._handle_revoke,
        }
        handler = handlers.get(endpoint)
        if handler is None:
            return httpx.Response(404, json={"error_summary": f"unknown endpoint: {endpoint}"})
        return handler(request, body_bytes)

    # ── OAuth ───────────────────────────────────────────────────────

    def _handle_oauth_token(self, request: httpx.Request, body_bytes: bytes) -> httpx.Response:
        params = dict(p.split("=", 1) for p in body_bytes.decode().split("&") if "=" in p)
        self._last_token_form = params
        grant_type = params.get("grant_type")

        if grant_type == "authorization_code":
            code = params.get("code", "")
            if code not in self._oauth_codes:
                return httpx.Response(400, json={
                    "error": "invalid_grant",
                    "error_description": "Invalid authorization code.",
                })
            data = self._oauth_codes.pop(code)
            return httpx.Response(200, json=data)

        if grant_type == "refresh_token":
            return httpx.Response(200, json={
                "access_token": f"sl.new-{uuid.uuid4().hex[:8]}",
                "token_type": "bearer",
                "expires_in": 14400,
                "scope": params.get("scope", ""),
                "account_id": "dbid:test123",
            })

        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    # ── Users ───────────────────────────────────────────────────────

    def _handle_get_current_account(self, request: httpx.Request,
                                     body_bytes: bytes) -> httpx.Response:
        return httpx.Response(200, json=self._account)

    def _handle_get_space_usage(self, request: httpx.Request,
                                 body_bytes: bytes) -> httpx.Response:
        if self._space_usage is not None:
            return httpx.Response(200, json=self._space_usage)
        return httpx.Response(200, json={
            "used": 1024000,
            "allocation": {".tag": "individual", "allocated": 2199023255552},
        })

    # ── Upload sessions ─────────────────────────────────────────────

    def _handle_upload_session_start(self, request: httpx.Request,
                                      body_bytes: bytes) -> httpx.Response:
        api_arg = self._parse_api_arg(request)
        close = api_arg.get("close", False)
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = {"data": body_bytes, "closed": close}
        return httpx.Response(200, json={"session_id": session_id})

    def _handle_upload_session_append(self, request: httpx.Request,
                                       body_bytes: bytes) -> httpx.Response:
        api_arg = self._parse_api_arg(request)
        cursor = api_arg.get("cursor", {})
        session_id = cursor.get("session_id", "")
        offset = cursor.get("offset", 0)
        close = api_arg.get("close", False)

        session = self._sessions.get(session_id)
        if session is None:
            return httpx.Response(409, json={
                "error_summary": "lookup_failed/not_found/...",
                "error": {
                    ".tag": "lookup_failed",
                    "lookup_failed": {".tag": "not_found"},
                },
            })

        actual_offset = len(session["data"])
        if offset != actual_offset:
            return httpx.Response(409, json={
                "error_summary": "incorrect_offset/...",
                "error": {
                    ".tag": "incorrect_offset",
                    "correct_offset": actual_offset,
                },
            })

        session["data"] += body_bytes
        if close:
            session["closed"] = True
        return httpx.Response(200, json={})

    def _handle_finish_batch(self, request: httpx.Request,
                              body_bytes: bytes) -> httpx.Response:
        # Check auth error injection
        if self._finish_batch_auth_fail_count > 0:
            self._finish_batch_auth_fail_count -= 1
            return httpx.Response(401, json={
                "error_summary": "expired_access_token/...",
                "error": {".tag": "expired_access_token"},
            })

        # Check too_many_write_operations injection
        if self._tmwo_next:
            self._tmwo_next = False
            return httpx.Response(429, json={
                "error_summary": "too_many_write_operations/...",
                "error": {".tag": "too_many_write_operations"},
            })

        arg = json.loads(body_bytes)
        entries = arg.get("entries", [])
        results = self._commit_entries(entries)

        if self._async_mode:
            job_id = uuid.uuid4().hex
            self._async_jobs[job_id] = {
                "type": "finish_batch",
                "result": results,
                "polls": 0,
            }
            return httpx.Response(200, json={
                ".tag": "async_job_id",
                "async_job_id": job_id,
            })

        return httpx.Response(200, json={
            ".tag": "complete",
            "entries": results,
        })

    def _handle_finish_batch_check(self, request: httpx.Request,
                                    body_bytes: bytes) -> httpx.Response:
        arg = json.loads(body_bytes)
        job_id = arg.get("async_job_id", "")
        job = self._async_jobs.get(job_id)
        if job is None:
            return httpx.Response(404, json={"error_summary": "not_found"})

        if self._finish_batch_always_pending:
            return httpx.Response(200, json={".tag": "in_progress"})

        job["polls"] += 1
        if job["polls"] < 2:
            return httpx.Response(200, json={".tag": "in_progress"})

        del self._async_jobs[job_id]
        return httpx.Response(200, json={
            ".tag": "complete",
            "entries": job["result"],
        })

    def _commit_entries(self, entries: list[dict]) -> list[dict]:
        results = []
        for entry in entries:
            cursor = entry["cursor"]
            commit = entry["commit"]
            session_id = cursor["session_id"]
            session = self._sessions.get(session_id)
            if session is None:
                results.append({
                    ".tag": "failure",
                    "failure": {".tag": "lookup_failed", "session_id": session_id},
                })
                continue

            data = session["data"]
            path = commit["path"]
            path_lower = path.lower()
            rev = uuid.uuid4().hex[:9]
            ch = _content_hash(data)

            # Verify content_hash if provided in commit info
            expected_hash = commit.get("content_hash")
            if expected_hash is not None and self._verify_content_hash:
                if expected_hash != ch:
                    results.append({
                        ".tag": "failure",
                        "failure": {".tag": "content_hash_mismatch"},
                    })
                    continue

            self.files[path_lower] = {
                "data": data,
                "rev": rev,
                "client_modified": commit.get("client_modified", ""),
                "content_hash": ch,
            }
            del self._sessions[session_id]

            results.append({
                ".tag": "success",
                "name": path.rsplit("/", 1)[-1],
                "path_lower": path_lower,
                "path_display": path,
                "id": f"id:{uuid.uuid4().hex[:12]}",
                "client_modified": commit.get("client_modified", ""),
                "server_modified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "rev": rev,
                "size": len(data),
                "content_hash": ch,
            })
        return results

    # ── Delete batch ────────────────────────────────────────────────

    def _handle_delete_batch(self, request: httpx.Request,
                              body_bytes: bytes) -> httpx.Response:
        if self._delete_batch_fail_next:
            self._delete_batch_fail_next = False
            return httpx.Response(200, json={
                ".tag": "failed",
                "error_summary": "too_many_files/...",
            })

        arg = json.loads(body_bytes)
        entries = arg.get("entries", [])
        results = []
        for entry in entries:
            path = entry["path"]
            path_lower = path.lower()
            if path_lower in self.files:
                file_info = self.files.pop(path_lower)
                results.append({
                    ".tag": "success",
                    "metadata": {
                        ".tag": "file",
                        "name": path.rsplit("/", 1)[-1],
                        "path_lower": path_lower,
                        "path_display": path,
                    },
                })
            else:
                results.append({
                    ".tag": "failure",
                    "failure": {
                        ".tag": "path_lookup",
                        "path_lookup": {".tag": "not_found"},
                    },
                })

        if self._async_mode:
            job_id = uuid.uuid4().hex
            self._async_jobs[job_id] = {
                "type": "delete_batch",
                "result": results,
                "polls": 0,
            }
            return httpx.Response(200, json={
                ".tag": "async_job_id",
                "async_job_id": job_id,
            })

        return httpx.Response(200, json={
            ".tag": "complete",
            "entries": results,
        })

    def _handle_delete_batch_check(self, request: httpx.Request,
                                    body_bytes: bytes) -> httpx.Response:
        arg = json.loads(body_bytes)
        job_id = arg.get("async_job_id", "")
        job = self._async_jobs.get(job_id)
        if job is None:
            return httpx.Response(404, json={"error_summary": "not_found"})

        job["polls"] += 1
        if job["polls"] < 2:
            return httpx.Response(200, json={".tag": "in_progress"})

        del self._async_jobs[job_id]
        return httpx.Response(200, json={
            ".tag": "complete",
            "entries": job["result"],
        })

    # ── List folder ─────────────────────────────────────────────────

    def _handle_list_folder(self, request: httpx.Request,
                             body_bytes: bytes) -> httpx.Response:
        arg = json.loads(body_bytes)
        path_prefix = arg.get("path", "")
        limit = arg.get("limit", 2000)

        # Collect matching entries
        all_entries = []
        for plow, info in sorted(self.files.items()):
            if path_prefix == "" or plow.startswith(path_prefix.lower() + "/"):
                all_entries.append({
                    ".tag": "file",
                    "name": plow.rsplit("/", 1)[-1],
                    "path_lower": plow,
                    "path_display": plow,
                    "rev": info["rev"],
                    "size": len(info["data"]),
                    "content_hash": info["content_hash"],
                    "client_modified": info.get("client_modified", ""),
                    "server_modified": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

        page = all_entries[:limit]
        has_more = len(all_entries) > limit
        cursor = ""
        if has_more:
            cursor = uuid.uuid4().hex
            self._list_folder_cursors[cursor] = {
                "entries": all_entries[limit:],
                "limit": limit,
            }

        return httpx.Response(200, json={
            "entries": page,
            "cursor": cursor or uuid.uuid4().hex,
            "has_more": has_more,
        })

    def _handle_list_folder_continue(self, request: httpx.Request,
                                      body_bytes: bytes) -> httpx.Response:
        arg = json.loads(body_bytes)
        cursor = arg.get("cursor", "")
        state = self._list_folder_cursors.get(cursor)
        if state is None:
            return httpx.Response(200, json={
                "entries": [],
                "cursor": cursor,
                "has_more": False,
            })

        remaining = state["entries"]
        limit = state["limit"]
        page = remaining[:limit]
        rest = remaining[limit:]
        has_more = len(rest) > 0

        del self._list_folder_cursors[cursor]
        new_cursor = cursor
        if has_more:
            new_cursor = uuid.uuid4().hex
            self._list_folder_cursors[new_cursor] = {
                "entries": rest,
                "limit": limit,
            }

        return httpx.Response(200, json={
            "entries": page,
            "cursor": new_cursor,
            "has_more": has_more,
        })

    # ── Get metadata ────────────────────────────────────────────────

    def _handle_get_metadata(self, request: httpx.Request,
                              body_bytes: bytes) -> httpx.Response:
        arg = json.loads(body_bytes)
        path = arg.get("path", "")
        path_lower = path.lower()
        info = self.files.get(path_lower)
        if info is None:
            return httpx.Response(409, json={
                "error_summary": "path/not_found/...",
                "error": {".tag": "path", "path": {".tag": "not_found"}},
            })
        return httpx.Response(200, json={
            ".tag": "file",
            "name": path.rsplit("/", 1)[-1],
            "path_lower": path_lower,
            "path_display": path,
            "rev": info["rev"],
            "size": len(info["data"]),
            "content_hash": info["content_hash"],
        })

    # ── Move ───────────────────────────────────────────────────────

    def _handle_move_v2(self, request: httpx.Request,
                         body_bytes: bytes) -> httpx.Response:
        arg = json.loads(body_bytes)
        from_path = arg.get("from_path", "")
        to_path = arg.get("to_path", "")
        from_lower = from_path.lower()
        to_lower = to_path.lower()

        # Move all files whose path starts with from_path
        moved = {}
        to_remove = []
        for plow, info in list(self.files.items()):
            if plow == from_lower or plow.startswith(from_lower + "/"):
                new_path = to_lower + plow[len(from_lower):]
                moved[new_path] = info
                to_remove.append(plow)

        if not to_remove:
            return httpx.Response(409, json={
                "error_summary": "from_lookup/not_found/...",
                "error": {".tag": "from_lookup", "from_lookup": {".tag": "not_found"}},
            })

        for p in to_remove:
            del self.files[p]
        self.files.update(moved)

        return httpx.Response(200, json={
            "metadata": {
                ".tag": "folder",
                "name": to_path.rsplit("/", 1)[-1],
                "path_lower": to_lower,
                "path_display": to_path,
                "id": f"id:{uuid.uuid4().hex[:12]}",
            },
        })

    # ── Revoke ──────────────────────────────────────────────────────

    def _handle_revoke(self, request: httpx.Request,
                        body_bytes: bytes) -> httpx.Response:
        return httpx.Response(200)

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_api_arg(request: httpx.Request) -> dict:
        raw = request.headers.get("dropbox-api-arg", "{}")
        return json.loads(raw)
