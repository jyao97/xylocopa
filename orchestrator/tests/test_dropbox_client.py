"""Tests for dropbox_sync.client — DropboxClient, Throttle and helpers."""

import asyncio
import json
import time

import httpx
import pytest

from dropbox_sync.client import (
    DropboxAuthError,
    DropboxClient,
    DropboxError,
    DropboxRateLimit,
    Throttle,
    api_arg_header,
    commit_info,
    dropbox_timestamp,
)
from tests.dropbox_fake import FakeDropboxServer


# ── Helpers ─────────────────────────────────────────────────────────────


class FakeTokenProvider:
    """Simple token provider for tests."""

    def __init__(self, token: str = "test-token"):
        self._token = token
        self.refresh_calls: list[bool] = []

    async def get_access_token(self, force_refresh: bool = False) -> str:
        self.refresh_calls.append(force_refresh)
        if force_refresh:
            self._token = f"refreshed-{len(self.refresh_calls)}"
        return self._token


def make_client(
    fake: FakeDropboxServer,
    *,
    max_retries: int = 6,
    throttle: Throttle | None = None,
    token: str = "test-token",
) -> tuple[DropboxClient, FakeTokenProvider, list]:
    """Build a DropboxClient wired to a FakeDropboxServer.

    Returns (client, token_provider, sleep_log).
    """
    tp = FakeTokenProvider(token)
    sleep_log: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_log.append(delay)

    transport = fake.transport()
    http = httpx.AsyncClient(transport=transport, base_url="https://api.dropboxapi.com")
    client = DropboxClient(
        tp,
        http=http,
        max_retries=max_retries,
        throttle=throttle,
        sleep=fake_sleep,
    )
    return client, tp, sleep_log


# ── Unit tests for helpers ──────────────────────────────────────────────


class TestApiArgHeader:
    def test_ascii_only_for_non_ascii_path(self):
        """Non-ASCII characters must be escaped as \\uXXXX."""
        arg = {"path": "/日本語/ファイル.txt"}
        header = api_arg_header(arg)
        assert header.isascii(), f"Header contains non-ASCII: {header!r}"
        assert "\\u" in header
        parsed = json.loads(header)
        assert parsed["path"] == "/日本語/ファイル.txt"

    def test_compact_separators(self):
        arg = {"path": "/hello", "mode": "overwrite"}
        header = api_arg_header(arg)
        assert " " not in header
        assert ": " not in header
        assert ", " not in header


class TestDropboxTimestamp:
    def test_format(self):
        ts = dropbox_timestamp(1436186096.0)
        assert ts == "2015-07-06T12:34:56Z"

    def test_epoch_zero(self):
        ts = dropbox_timestamp(0.0)
        assert ts == "1970-01-01T00:00:00Z"


class TestCommitInfo:
    def test_fields(self):
        mtime_ns = int(1436186096.0 * 1e9)
        ci = commit_info("/project/file.py", mtime_ns)
        assert ci["path"] == "/project/file.py"
        assert ci["mode"] == "overwrite"
        assert ci["autorename"] is False
        assert ci["mute"] is True
        assert ci["client_modified"] == "2015-07-06T12:34:56Z"


# ── Retry / auth tests ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_429_with_retry_after():
    """429 with Retry-After waits exactly that long then succeeds."""
    fake = FakeDropboxServer()
    fake.fail_next(1, status=429, retry_after=2.5)
    client, tp, sleep_log = make_client(fake)

    result = await client.get_current_account()
    assert result["account_id"] == "dbid:test123"
    assert len(sleep_log) == 1
    assert sleep_log[0] == 2.5

    await client.aclose()


@pytest.mark.anyio
async def test_5xx_backoff_sequence():
    """5xx triggers exponential backoff then succeeds."""
    fake = FakeDropboxServer()
    fake.fail_next(3, status=503)
    client, tp, sleep_log = make_client(fake)

    result = await client.get_space_usage()
    assert "used" in result
    assert len(sleep_log) == 3
    # Backoff: min(60, 2^attempt) + jitter (0..1)
    for i, delay in enumerate(sleep_log):
        base = min(60, 1 * (2 ** i))
        assert base <= delay <= base + 1.0, f"attempt {i}: delay {delay} outside [{base}, {base+1}]"

    await client.aclose()


@pytest.mark.anyio
async def test_max_retries_429_raises_rate_limit():
    """Exhausting max_retries on 429 raises DropboxRateLimit."""
    fake = FakeDropboxServer()
    fake.fail_next(4, status=429, retry_after=0.0)
    client, tp, sleep_log = make_client(fake, max_retries=3)

    with pytest.raises(DropboxRateLimit) as exc_info:
        await client.get_current_account()
    assert exc_info.value.status == 429

    await client.aclose()


@pytest.mark.anyio
async def test_max_retries_5xx_raises_dropbox_error():
    """Exhausting max_retries on 5xx raises DropboxError."""
    fake = FakeDropboxServer()
    fake.fail_next(4, status=500)
    client, tp, sleep_log = make_client(fake, max_retries=3)

    with pytest.raises(DropboxError) as exc_info:
        await client.get_current_account()
    assert exc_info.value.status == 500
    assert not isinstance(exc_info.value, DropboxRateLimit)

    await client.aclose()


@pytest.mark.anyio
async def test_401_force_refresh_and_retry():
    """401 triggers one force_refresh then retries; second 401 raises DropboxAuthError."""
    fake = FakeDropboxServer()
    fake.fail_next(1, status=401)
    client, tp, sleep_log = make_client(fake)

    result = await client.get_current_account()
    assert result["account_id"] == "dbid:test123"
    # Should have done one force_refresh
    assert True in tp.refresh_calls

    await client.aclose()


@pytest.mark.anyio
async def test_401_double_raises_auth_error():
    """Two consecutive 401s raise DropboxAuthError."""
    fake = FakeDropboxServer()
    fake.fail_next(2, status=401)
    client, tp, sleep_log = make_client(fake)

    with pytest.raises(DropboxAuthError) as exc_info:
        await client.get_current_account()
    assert exc_info.value.status == 401

    await client.aclose()


# ── Upload tests ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_upload_small_file_close_and_finish():
    """upload_session_start(close=True) + finish_batch commits with correct content."""
    fake = FakeDropboxServer()
    client, tp, sleep_log = make_client(fake)

    data = b"hello world"
    mtime_ns = int(1436186096.0 * 1e9)

    session_id = await client.upload_session_start(data, close=True)
    assert isinstance(session_id, str)

    ci = commit_info("/proj/hello.txt", mtime_ns)
    entries = [{
        "cursor": {"session_id": session_id, "offset": len(data)},
        "commit": ci,
    }]
    results = await client.upload_session_finish_batch(entries)

    assert len(results) == 1
    r = results[0]
    assert r[".tag"] == "success"
    assert r["size"] == len(data)

    # Verify the file in the fake
    stored = fake.files.get("/proj/hello.txt")
    assert stored is not None
    assert stored["data"] == data
    assert stored["client_modified"] == "2015-07-06T12:34:56Z"

    # Verify Dropbox-API-Arg was sent in the start request
    start_req = [r for r in fake.requests if "upload_session/start" in r["url"]]
    assert len(start_req) == 1
    api_arg_raw = start_req[0]["headers"].get("dropbox-api-arg", "")
    api_arg_parsed = json.loads(api_arg_raw)
    assert api_arg_parsed["close"] is True

    await client.aclose()


@pytest.mark.anyio
async def test_multi_chunk_append_and_finish():
    """Multi-chunk upload via start + append + finish_batch."""
    fake = FakeDropboxServer()
    client, tp, sleep_log = make_client(fake)

    chunk1 = b"A" * 100
    chunk2 = b"B" * 200
    chunk3 = b"C" * 50
    full_data = chunk1 + chunk2 + chunk3

    session_id = await client.upload_session_start(chunk1)
    await client.upload_session_append(session_id, len(chunk1), chunk2)
    await client.upload_session_append(session_id, len(chunk1) + len(chunk2), chunk3, close=True)

    mtime_ns = int(1700000000.0 * 1e9)
    ci = commit_info("/proj/multi.bin", mtime_ns)
    entries = [{
        "cursor": {"session_id": session_id, "offset": len(full_data)},
        "commit": ci,
    }]
    results = await client.upload_session_finish_batch(entries)

    assert len(results) == 1
    assert results[0][".tag"] == "success"
    assert results[0]["size"] == len(full_data)

    stored = fake.files.get("/proj/multi.bin")
    assert stored is not None
    assert stored["data"] == full_data

    await client.aclose()


@pytest.mark.anyio
async def test_async_finish_batch_polling():
    """Async finish_batch returns async_job_id, polls in_progress, then complete."""
    fake = FakeDropboxServer(async_mode=True)
    client, tp, sleep_log = make_client(fake)

    data = b"async content"
    session_id = await client.upload_session_start(data, close=True)
    ci = commit_info("/proj/async.txt", int(1700000000.0 * 1e9))
    entries = [{
        "cursor": {"session_id": session_id, "offset": len(data)},
        "commit": ci,
    }]
    results = await client.upload_session_finish_batch(entries)

    assert len(results) == 1
    assert results[0][".tag"] == "success"
    # Should have polled (1s sleeps for polling)
    poll_sleeps = [s for s in sleep_log if s == 1]
    assert len(poll_sleeps) >= 1

    await client.aclose()


# ── Delete tests ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_delete_batch_missing_path():
    """delete_batch with a missing path returns a failure entry."""
    fake = FakeDropboxServer()
    # Pre-populate one file
    fake.files["/proj/exists.txt"] = {
        "data": b"content",
        "rev": "abc123",
        "client_modified": "",
        "content_hash": "aaa",
    }
    client, tp, sleep_log = make_client(fake)

    results = await client.delete_batch(["/proj/exists.txt", "/proj/missing.txt"])
    assert len(results) == 2
    assert results[0][".tag"] == "success"
    assert results[1][".tag"] == "failure"
    assert "not_found" in json.dumps(results[1])
    # Verify the existing file was removed
    assert "/proj/exists.txt" not in fake.files

    await client.aclose()


# ── Throttle tests ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_throttle_delays_proportional_to_bytes():
    """Throttle(kbps) delays proportional to byte count."""
    sleep_log: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_log.append(delay)

    throttle = Throttle(kbps=1000)
    # 1000 kbps = 125000 bytes/sec → 1000 bytes ≈ 0.008s
    await throttle.acquire(1000, sleep=fake_sleep)
    assert len(sleep_log) == 1
    expected = 1000 / (1000 * 1024 / 8)
    assert abs(sleep_log[0] - expected) < 1e-9

    # Larger payload → proportionally larger delay
    sleep_log.clear()
    await throttle.acquire(10000, sleep=fake_sleep)
    assert len(sleep_log) == 1
    expected_large = 10000 / (1000 * 1024 / 8)
    assert abs(sleep_log[0] - expected_large) < 1e-9
    assert sleep_log[0] > expected * 9  # 10x bytes ≈ 10x delay


@pytest.mark.anyio
async def test_throttle_zero_kbps_never_sleeps():
    """kbps<=0 means no throttling."""
    sleep_log: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_log.append(delay)

    throttle = Throttle(kbps=0)
    await throttle.acquire(999999, sleep=fake_sleep)
    assert len(sleep_log) == 0

    throttle2 = Throttle(kbps=-100)
    await throttle2.acquire(999999, sleep=fake_sleep)
    assert len(sleep_log) == 0


@pytest.mark.anyio
async def test_throttle_on_upload():
    """Throttle is invoked during content_upload calls."""
    fake = FakeDropboxServer()
    throttle = Throttle(kbps=8000)
    client, tp, sleep_log = make_client(fake, throttle=throttle)

    data = b"x" * 10000
    await client.upload_session_start(data, close=True)

    # The sleep_log should contain the throttle delay
    throttle_sleeps = [s for s in sleep_log if s > 0]
    assert len(throttle_sleeps) >= 1
    expected = 10000 / (8000 * 1024 / 8)
    assert abs(throttle_sleeps[0] - expected) < 1e-6

    await client.aclose()


# ── List folder / pagination ────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_folder_pagination():
    """list_folder + list_folder_continue paginates through entries."""
    fake = FakeDropboxServer()
    # Populate 5 files
    for i in range(5):
        fake.files[f"/proj/file{i}.txt"] = {
            "data": f"content{i}".encode(),
            "rev": f"rev{i}",
            "client_modified": "",
            "content_hash": f"hash{i}",
        }
    client, tp, sleep_log = make_client(fake)

    # Request with limit=2 to force pagination
    result = await client.list_folder("/proj", limit=2)
    all_entries = list(result["entries"])
    assert len(result["entries"]) == 2
    assert result["has_more"] is True

    # Continue
    result2 = await client.list_folder_continue(result["cursor"])
    all_entries.extend(result2["entries"])
    assert len(result2["entries"]) == 2
    assert result2["has_more"] is True

    # Final page
    result3 = await client.list_folder_continue(result2["cursor"])
    all_entries.extend(result3["entries"])
    assert len(result3["entries"]) == 1
    assert result3["has_more"] is False

    # All 5 files collected
    assert len(all_entries) == 5

    await client.aclose()


# ── too_many_write_operations ───────────────────────────────────────────


@pytest.mark.anyio
async def test_tmwo_on_finish_batch():
    """too_many_write_operations on finish_batch is retried."""
    fake = FakeDropboxServer()
    fake.fail_next_finish_batch_tmwo()
    client, tp, sleep_log = make_client(fake)

    data = b"tmwo test"
    session_id = await client.upload_session_start(data, close=True)
    ci = commit_info("/proj/tmwo.txt", int(1700000000.0 * 1e9))
    entries = [{
        "cursor": {"session_id": session_id, "offset": len(data)},
        "commit": ci,
    }]
    results = await client.upload_session_finish_batch(entries)
    assert len(results) == 1
    assert results[0][".tag"] == "success"
    # The tmwo response is a 429, so it gets retried with a backoff sleep
    assert len(sleep_log) >= 1

    await client.aclose()
