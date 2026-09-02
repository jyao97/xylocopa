"""Async Dropbox HTTP client with retry, throttle and auth refresh."""

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Protocol

import httpx

logger = logging.getLogger("orchestrator.dropbox.client")

API_HOST = "https://api.dropboxapi.com"
CONTENT_HOST = "https://content.dropboxapi.com"
SMALL_FILE_LIMIT = 150 * 1024 * 1024


# ── Exceptions ──────────────────────────────────────────────────────────


class DropboxError(Exception):
    """Base Dropbox API error."""

    def __init__(self, status: int, summary: str, body: dict | None = None,
                 retry_after: float | None = None):
        self.status = status
        self.summary = summary
        self.body = body
        self.retry_after = retry_after
        super().__init__(summary)


class DropboxAuthError(DropboxError):
    """401 / expired or invalid access token."""


class DropboxRateLimit(DropboxError):
    """429 / too_many_requests / too_many_write_operations."""


class DropboxIncorrectOffset(DropboxError):
    """Raised when append/finish returns incorrect_offset."""

    def __init__(self, status: int, summary: str, correct_offset: int,
                 body: dict | None = None):
        super().__init__(status, summary, body)
        self.correct_offset = correct_offset


class DropboxSessionNotFound(DropboxError):
    """Raised when a session is not_found or lookup_failed with not_found/closed."""


# ── Token provider protocol ────────────────────────────────────────────


class TokenProvider(Protocol):
    async def get_access_token(self, force_refresh: bool = False) -> str: ...


# ── Throttle ────────────────────────────────────────────────────────────


class Throttle:
    """Bandwidth throttle. kbps <= 0 means unlimited."""

    def __init__(self, kbps: int):
        self._kbps = kbps

    async def acquire(self, nbytes: int, sleep=asyncio.sleep) -> None:
        if self._kbps <= 0 or nbytes <= 0:
            return
        delay = nbytes / (self._kbps * 1024 / 8)
        await sleep(delay)

    def set_rate(self, kbps: int) -> None:
        self._kbps = kbps


# ── Helpers ─────────────────────────────────────────────────────────────


def api_arg_header(arg: dict) -> str:
    """Serialize *arg* to a Dropbox-API-Arg header value (ASCII-safe)."""
    return json.dumps(arg, ensure_ascii=True, separators=(",", ":"))


def dropbox_timestamp(epoch_seconds: float) -> str:
    """Format *epoch_seconds* as a Dropbox timestamp string (UTC)."""
    dt = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def commit_info(path: str, mtime_ns: int, content_hash: str | None = None) -> dict:
    """Build a CommitInfo dict for an upload."""
    info: dict = {
        "path": path,
        "mode": "overwrite",
        "autorename": False,
        "mute": True,
        "client_modified": dropbox_timestamp(mtime_ns / 1e9),
    }
    if content_hash is not None:
        info["content_hash"] = content_hash
    return info


# ── Client ──────────────────────────────────────────────────────────────


def _is_retryable_error(status: int, body: dict | None) -> bool:
    """Return True when the response should be retried."""
    if status == 429:
        return True
    if status >= 500:
        return True
    if body and isinstance(body.get("error_summary"), str):
        s = body["error_summary"]
        if s.startswith("too_many_write_operations") or s.startswith("too_many_requests"):
            return True
    return False


def _is_rate_limit(status: int, body: dict | None) -> bool:
    if status == 429:
        return True
    if body and isinstance(body.get("error_summary"), str):
        s = body["error_summary"]
        if s.startswith("too_many_write_operations") or s.startswith("too_many_requests"):
            return True
    return False


class DropboxClient:
    """Async Dropbox API client with retry, throttle and auth refresh."""

    def __init__(
        self,
        tokens: TokenProvider,
        *,
        http: httpx.AsyncClient | None = None,
        max_retries: int = 6,
        throttle: Throttle | None = None,
        sleep=asyncio.sleep,
    ):
        self._tokens = tokens
        self._http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self._max_retries = max_retries
        self._throttle = throttle
        self._sleep = sleep

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ── Low-level request with retry ────────────────────────────────

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        json_body: dict | None = None,
        data: bytes | None = None,
        is_content: bool = False,
    ) -> httpx.Response:
        auth_refreshed = False
        attempt = 0

        while True:
            token = await self._tokens.get_access_token()
            req_headers = {"Authorization": f"Bearer {token}"}
            if headers:
                req_headers.update(headers)

            try:
                if data is not None:
                    resp = await self._http.request(
                        method, url, headers=req_headers, content=data,
                    )
                elif json_body is not None:
                    req_headers.setdefault("Content-Type", "application/json")
                    resp = await self._http.request(
                        method, url, headers=req_headers,
                        content=json.dumps(json_body).encode(),
                    )
                else:
                    req_headers.setdefault("Content-Type", "application/json")
                    resp = await self._http.request(
                        method, url, headers=req_headers,
                        content=b"null",
                    )
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise DropboxError(
                        status=0, summary=f"transport error: {exc}",
                    ) from exc
                delay = min(60, 1 * (2 ** attempt)) + random.random()
                logger.warning("Transport error (attempt %d): %s", attempt, exc)
                await self._sleep(delay)
                attempt += 1
                continue

            # 401 → force-refresh once
            if resp.status_code == 401:
                if not auth_refreshed:
                    auth_refreshed = True
                    await self._tokens.get_access_token(force_refresh=True)
                    continue
                body = self._try_json(resp)
                raise DropboxAuthError(
                    status=401,
                    summary=body.get("error_summary", "unauthorized") if body else "unauthorized",
                    body=body,
                )

            # Parse body for retryable-error check
            body = self._try_json(resp)
            retry_after = self._parse_retry_after(resp)

            if _is_retryable_error(resp.status_code, body):
                if attempt >= self._max_retries:
                    if _is_rate_limit(resp.status_code, body):
                        raise DropboxRateLimit(
                            status=resp.status_code,
                            summary=body.get("error_summary", "rate limited") if body else "rate limited",
                            body=body,
                            retry_after=retry_after,
                        )
                    raise DropboxError(
                        status=resp.status_code,
                        summary=body.get("error_summary", f"server error {resp.status_code}") if body else f"server error {resp.status_code}",
                        body=body,
                        retry_after=retry_after,
                    )
                if retry_after is not None:
                    delay = retry_after
                else:
                    delay = min(60, 1 * (2 ** attempt)) + random.random()
                logger.warning(
                    "Retryable %d (attempt %d, wait %.1fs)",
                    resp.status_code, attempt, delay,
                )
                await self._sleep(delay)
                attempt += 1
                continue

            # Non-retryable error
            if resp.status_code >= 400:
                raise DropboxError(
                    status=resp.status_code,
                    summary=body.get("error_summary", f"error {resp.status_code}") if body else f"error {resp.status_code}",
                    body=body,
                )

            return resp

    @staticmethod
    def _try_json(resp: httpx.Response) -> dict | None:
        try:
            return resp.json()
        except Exception:
            return None

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> float | None:
        val = resp.headers.get("Retry-After")
        if val is not None:
            try:
                return float(val)
            except ValueError:
                pass
        return None

    # ── RPC and content helpers ─────────────────────────────────────

    async def rpc(self, endpoint: str, arg: dict | None = None) -> dict:
        """POST to API_HOST/2/<endpoint> with JSON body."""
        url = f"{API_HOST}/2/{endpoint}"
        resp = await self._request("POST", url, json_body=arg)
        return resp.json()

    async def content_upload(self, endpoint: str, arg: dict, data: bytes) -> dict:
        """POST to CONTENT_HOST/2/<endpoint> with octet-stream body and Dropbox-API-Arg header."""
        url = f"{CONTENT_HOST}/2/{endpoint}"
        if self._throttle:
            await self._throttle.acquire(len(data), sleep=self._sleep)
        headers = {
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": api_arg_header(arg),
        }
        resp = await self._request("POST", url, headers=headers, data=data)
        return resp.json()

    # ── High-level methods ──────────────────────────────────────────

    async def get_current_account(self) -> dict:
        return await self.rpc("users/get_current_account")

    async def get_space_usage(self) -> dict:
        return await self.rpc("users/get_space_usage")

    async def upload_session_start(self, data: bytes, *, close: bool = False) -> str:
        """Start an upload session. Returns session_id."""
        result = await self.content_upload(
            "files/upload_session/start",
            {"close": close},
            data,
        )
        return result["session_id"]

    async def upload_session_append(
        self, session_id: str, offset: int, data: bytes, *, close: bool = False,
    ) -> None:
        """Append data to an upload session."""
        try:
            await self.content_upload(
                "files/upload_session/append_v2",
                {
                    "cursor": {"session_id": session_id, "offset": offset},
                    "close": close,
                },
                data,
            )
        except DropboxError as exc:
            self._check_session_error(exc)
            raise

    async def upload_session_finish_batch(self, entries: list[dict]) -> list[dict]:
        """Finish a batch of upload sessions. Polls until complete."""
        result = await self.rpc("files/upload_session/finish_batch", {"entries": entries})
        if result.get(".tag") == "complete":
            return result["entries"]
        job_id = result["async_job_id"]
        while True:
            await self._sleep(1)
            result = await self.rpc(
                "files/upload_session/finish_batch/check",
                {"async_job_id": job_id},
            )
            if result.get(".tag") == "complete":
                return result["entries"]

    async def delete_batch(self, paths: list[str]) -> list[dict]:
        """Delete a batch of paths. Polls until complete."""
        entries = [{"path": p} for p in paths]
        result = await self.rpc("files/delete_batch", {"entries": entries})
        if result.get(".tag") == "complete":
            return result["entries"]
        if result.get(".tag") == "failed":
            raise DropboxError(
                status=0,
                summary=result.get("error_summary", "delete_batch failed"),
                body=result,
            )
        job_id = result["async_job_id"]
        while True:
            await self._sleep(1)
            result = await self.rpc(
                "files/delete_batch/check",
                {"async_job_id": job_id},
            )
            if result.get(".tag") == "complete":
                return result["entries"]
            if result.get(".tag") == "failed":
                raise DropboxError(
                    status=0,
                    summary=result.get("error_summary", "delete_batch failed"),
                    body=result,
                )

    async def list_folder(self, path: str, *, recursive: bool = False, limit: int = 2000) -> dict:
        arg = {"path": path, "recursive": recursive, "limit": limit}
        return await self.rpc("files/list_folder", arg)

    async def list_folder_continue(self, cursor: str) -> dict:
        return await self.rpc("files/list_folder/continue", {"cursor": cursor})

    async def get_metadata(self, path: str) -> dict:
        return await self.rpc("files/get_metadata", {"path": path})

    @staticmethod
    def _check_session_error(exc: DropboxError) -> None:
        """Re-raise as DropboxIncorrectOffset or DropboxSessionNotFound if applicable."""
        body = exc.body
        if body is None:
            return
        error = body.get("error", {})
        if not isinstance(error, dict):
            return
        tag = error.get(".tag", "")

        if tag == "incorrect_offset":
            correct = error.get("correct_offset")
            if correct is None:
                inner = error.get("incorrect_offset", {})
                correct = inner.get("correct_offset", 0)
            raise DropboxIncorrectOffset(
                status=exc.status,
                summary=exc.summary,
                correct_offset=int(correct),
                body=body,
            ) from exc

        if tag in ("not_found", "closed"):
            raise DropboxSessionNotFound(
                status=exc.status,
                summary=exc.summary,
                body=body,
            ) from exc

        if tag == "lookup_failed":
            inner_tag = ""
            lookup = error.get("lookup_failed", error.get("not_found", {}))
            if isinstance(lookup, dict):
                inner_tag = lookup.get(".tag", "")
            if inner_tag in ("not_found", "closed"):
                raise DropboxSessionNotFound(
                    status=exc.status,
                    summary=exc.summary,
                    body=body,
                ) from exc

    async def move_v2(self, from_path: str, to_path: str) -> dict:
        """Move/rename a file or folder (files/move_v2)."""
        return await self.rpc("files/move_v2", {
            "from_path": from_path,
            "to_path": to_path,
            "autorename": False,
        })

    async def space_summary(self) -> dict:
        """Return {"used": int, "allocated": int | None}."""
        usage = await self.get_space_usage()
        used = usage.get("used", 0)
        allocation = usage.get("allocation", {})
        tag = allocation.get(".tag", "")
        allocated: int | None = None
        if tag == "individual":
            allocated = allocation.get("allocated")
        elif tag == "team":
            allocated = allocation.get("allocated")
        return {"used": used, "allocated": allocated}

    async def revoke_token(self) -> None:
        await self.rpc("auth/token/revoke")
