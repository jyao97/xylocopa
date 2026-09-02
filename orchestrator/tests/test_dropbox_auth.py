"""Tests for dropbox_sync.auth — PKCE flow, token store, token provider."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import stat
import time

import httpx
import pytest

from dropbox_sync.auth import (
    APP_KEY_RE,
    DropboxTokenProvider,
    LinkError,
    LinkFlow,
    LinkStateError,
    NotLinkedError,
    TokenStore,
    build_authorize_url,
    make_pkce,
    unlink,
)


# ── PKCE ────────────────────────────────────────────────────────────────


class TestMakePkce:
    def test_verifier_length(self):
        verifier, _ = make_pkce()
        assert 43 <= len(verifier) <= 128

    def test_verifier_charset(self):
        verifier, _ = make_pkce()
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~" for c in verifier)

    def test_challenge_is_base64url_sha256_no_padding(self):
        verifier, challenge = make_pkce()
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert challenge == expected
        assert "=" not in challenge

    def test_uniqueness(self):
        pairs = [make_pkce() for _ in range(10)]
        verifiers = [v for v, _ in pairs]
        assert len(set(verifiers)) == 10


# ── build_authorize_url ─────────────────────────────────────────────────


class TestBuildAuthorizeUrl:
    def test_url_params(self):
        url = build_authorize_url("abc123test1", "CHAL", "STATE1")
        assert url.startswith("https://www.dropbox.com/oauth2/authorize?")
        assert "client_id=abc123test1" in url
        assert "response_type=code" in url
        assert "code_challenge=CHAL" in url
        assert "code_challenge_method=S256" in url
        assert "token_access_type=offline" in url
        assert "state=STATE1" in url
        assert "redirect_uri" not in url


# ── TokenStore ──────────────────────────────────────────────────────────


class TestTokenStore:
    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "token.json")
        store = TokenStore(path)
        data = {"access_token": "at", "refresh_token": "rt", "expires_at": 9999}
        store.save(data)
        loaded = store.load()
        assert loaded == data

    def test_save_chmod_0600(self, tmp_path):
        path = str(tmp_path / "token.json")
        store = TokenStore(path)
        store.save({"x": 1})
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600

    def test_save_dir_chmod_0700(self, tmp_path):
        sub = tmp_path / "sub"
        path = str(sub / "token.json")
        store = TokenStore(path)
        store.save({"x": 1})
        mode = os.stat(str(sub)).st_mode & 0o777
        assert mode == 0o700

    def test_atomic_write(self, tmp_path):
        path = str(tmp_path / "token.json")
        store = TokenStore(path)
        store.save({"version": 1})
        store.save({"version": 2})
        assert store.load()["version"] == 2

    def test_load_missing_returns_none(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        store = TokenStore(path)
        assert store.load() is None

    def test_load_corrupt_returns_none(self, tmp_path, caplog):
        path = str(tmp_path / "token.json")
        with open(path, "w") as f:
            f.write("{truncated")
        store = TokenStore(path)
        with caplog.at_level(logging.WARNING):
            result = store.load()
        assert result is None
        assert "Corrupt" in caplog.text or "corrupt" in caplog.text.lower()

    def test_load_non_dict_returns_none(self, tmp_path, caplog):
        path = str(tmp_path / "token.json")
        with open(path, "w") as f:
            json.dump([1, 2, 3], f)
        store = TokenStore(path)
        with caplog.at_level(logging.WARNING):
            result = store.load()
        assert result is None

    def test_delete(self, tmp_path):
        path = str(tmp_path / "token.json")
        store = TokenStore(path)
        store.save({"x": 1})
        store.delete()
        assert not os.path.exists(path)

    def test_delete_missing_is_noop(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        store = TokenStore(path)
        store.delete()  # should not raise

    def test_is_linked(self, tmp_path):
        path = str(tmp_path / "token.json")
        store = TokenStore(path)
        assert not store.is_linked
        store.save({"x": 1})
        assert store.is_linked

    def test_save_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "token.json")
        store = TokenStore(path)
        store.save({"nested": True})
        assert store.load() == {"nested": True}


# ── Mock transport helpers ──────────────────────────────────────────────


def _make_token_response(
    access_token: str = "sl.test-access-token",
    refresh_token: str = "test-refresh-token",
    expires_in: int = 14400,
    scope: str = "files.content.write files.content.read",
):
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "token_type": "bearer",
        "account_id": "dbid:AAH4f99T0taONIb-OurWxbNQ6ywGRopQngc",
        "uid": "12345",
        "scope": scope,
    }


def _make_account_response(
    account_id: str = "dbid:AAH4f99T0taONIb-OurWxbNQ6ywGRopQngc",
    display_name: str = "Jane Doe",
    email: str = "jane@example.com",
):
    return {
        "account_id": account_id,
        "name": {"display_name": display_name, "given_name": "Jane", "surname": "Doe"},
        "email": email,
    }


def _mock_transport(handler):
    """Wrap a handler function as an httpx async transport."""
    async def _handler(request: httpx.Request) -> httpx.Response:
        return handler(request)
    return httpx.MockTransport(_handler)


def _success_transport(
    token_resp=None,
    account_resp=None,
):
    token_resp = token_resp or _make_token_response()
    account_resp = account_resp or _make_account_response()

    def handler(request: httpx.Request):
        url = str(request.url)
        if "oauth2/token" in url:
            return httpx.Response(200, json=token_resp)
        if "get_current_account" in url:
            return httpx.Response(200, json=account_resp)
        return httpx.Response(404)

    return _mock_transport(handler)


# ── LinkFlow ────────────────────────────────────────────────────────────


class TestLinkFlow:
    def test_start_validates_app_key(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        flow = LinkFlow(store)
        with pytest.raises(ValueError):
            flow.start("short")
        with pytest.raises(ValueError):
            flow.start("has spaces!!")
        with pytest.raises(ValueError):
            flow.start("")

    def test_start_returns_url_and_state(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        flow = LinkFlow(store)
        result = flow.start("abcdefghij1234")
        assert "authorize_url" in result
        assert "state" in result
        assert result["state"] in result["authorize_url"]

    def test_start_twice_replaces_pending(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        flow = LinkFlow(store)
        r1 = flow.start("abcdefghij1234")
        r2 = flow.start("abcdefghij1234")
        assert r1["state"] != r2["state"]

    @pytest.mark.anyio
    async def test_complete_without_start_raises(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        flow = LinkFlow(store)
        with pytest.raises(LinkStateError):
            await flow.complete("some-code")

    @pytest.mark.anyio
    async def test_complete_success(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        http = httpx.AsyncClient(transport=_success_transport())
        flow = LinkFlow(store, http=http)
        flow.start("abcdefghij1234")
        result = await flow.complete("auth-code-123")

        assert result["account_id"] == "dbid:AAH4f99T0taONIb-OurWxbNQ6ywGRopQngc"
        assert result["name"] == "Jane Doe"
        assert result["email"] == "jane@example.com"

        # Token file should exist with correct permissions
        token_path = str(tmp_path / "token.json")
        assert os.path.exists(token_path)
        mode = os.stat(token_path).st_mode & 0o777
        assert mode == 0o600

        saved = store.load()
        assert saved["app_key"] == "abcdefghij1234"
        assert saved["refresh_token"] == "test-refresh-token"
        assert saved["access_token"] == "sl.test-access-token"
        assert "expires_at" in saved
        assert saved["account_id"] == "dbid:AAH4f99T0taONIb-OurWxbNQ6ywGRopQngc"
        assert saved["email"] == "jane@example.com"

    @pytest.mark.anyio
    async def test_complete_sends_correct_form_data(self, tmp_path):
        captured = {}

        def handler(request: httpx.Request):
            url = str(request.url)
            if "oauth2/token" in url:
                body = request.content.decode()
                captured["body"] = body
                return httpx.Response(200, json=_make_token_response())
            if "get_current_account" in url:
                return httpx.Response(200, json=_make_account_response())
            return httpx.Response(404)

        store = TokenStore(str(tmp_path / "token.json"))
        http = httpx.AsyncClient(transport=_mock_transport(handler))
        flow = LinkFlow(store, http=http)
        flow.start("abcdefghij1234")
        await flow.complete("my-code")

        body = captured["body"]
        assert "grant_type=authorization_code" in body
        assert "code=my-code" in body
        assert "client_id=abcdefghij1234" in body
        assert "code_verifier=" in body

    @pytest.mark.anyio
    async def test_complete_invalid_grant(self, tmp_path):
        def handler(request: httpx.Request):
            url = str(request.url)
            if "oauth2/token" in url:
                return httpx.Response(400, json={
                    "error": "invalid_grant",
                    "error_description": "The authorization code has expired.",
                })
            return httpx.Response(404)

        store = TokenStore(str(tmp_path / "token.json"))
        http = httpx.AsyncClient(transport=_mock_transport(handler))
        flow = LinkFlow(store, http=http)
        flow.start("abcdefghij1234")
        with pytest.raises(LinkError, match="authorization code has expired"):
            await flow.complete("bad-code")

    @pytest.mark.anyio
    async def test_complete_never_logs_token(self, tmp_path, caplog):
        store = TokenStore(str(tmp_path / "token.json"))
        http = httpx.AsyncClient(transport=_success_transport())
        flow = LinkFlow(store, http=http)

        with caplog.at_level(logging.DEBUG):
            flow.start("abcdefghij1234")
            await flow.complete("auth-code")

        # The refresh token string must never appear in logs
        assert "test-refresh-token" not in caplog.text

    @pytest.mark.anyio
    async def test_flow_expiry_after_10_minutes(self, tmp_path):
        """LinkFlow expires 10 minutes after start(); complete() raises LinkStateError."""
        store = TokenStore(str(tmp_path / "token.json"))
        http = httpx.AsyncClient(transport=_success_transport())

        current_time = [1000.0]
        flow = LinkFlow(store, http=http, now=lambda: current_time[0])

        flow.start("abcdefghij1234")

        # Advance time by just over 10 minutes (601 seconds)
        current_time[0] = 1000.0 + 601

        with pytest.raises(LinkStateError, match="flow expired"):
            await flow.complete("auth-code")

    @pytest.mark.anyio
    async def test_flow_within_10_minutes_succeeds(self, tmp_path):
        """LinkFlow within 10 minutes of start() completes successfully."""
        store = TokenStore(str(tmp_path / "token.json"))
        http = httpx.AsyncClient(transport=_success_transport())

        current_time = [1000.0]
        flow = LinkFlow(store, http=http, now=lambda: current_time[0])

        flow.start("abcdefghij1234")

        # Advance time by just under 10 minutes (599 seconds)
        current_time[0] = 1000.0 + 599

        result = await flow.complete("auth-code")
        assert result["account_id"] == "dbid:AAH4f99T0taONIb-OurWxbNQ6ywGRopQngc"


# ── DropboxTokenProvider ────────────────────────────────────────────────


class TestDropboxTokenProvider:
    @pytest.mark.anyio
    async def test_not_linked_raises(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        provider = DropboxTokenProvider(store)
        with pytest.raises(NotLinkedError):
            await provider.get_access_token()

    @pytest.mark.anyio
    async def test_returns_fresh_token(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        store.save({
            "app_key": "abcdefghij1234",
            "refresh_token": "rt",
            "access_token": "fresh-token",
            "expires_at": 2000.0,
        })
        provider = DropboxTokenProvider(store, now=lambda: 1000.0)
        token = await provider.get_access_token()
        assert token == "fresh-token"

    @pytest.mark.anyio
    async def test_refreshes_expired_token(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        store.save({
            "app_key": "abcdefghij1234",
            "refresh_token": "rt",
            "access_token": "old-token",
            "expires_at": 1000.0,
        })

        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "access_token": "new-token",
                "expires_in": 14400,
                "token_type": "bearer",
            })

        http = httpx.AsyncClient(transport=_mock_transport(handler))
        provider = DropboxTokenProvider(store, http=http, now=lambda: 1000.0)
        token = await provider.get_access_token()
        assert token == "new-token"

        saved = store.load()
        assert saved["access_token"] == "new-token"

    @pytest.mark.anyio
    async def test_refreshes_within_buffer(self, tmp_path):
        """Refresh when now >= expires_at - 300."""
        store = TokenStore(str(tmp_path / "token.json"))
        store.save({
            "app_key": "abcdefghij1234",
            "refresh_token": "rt",
            "access_token": "old-token",
            "expires_at": 1200.0,
        })

        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "access_token": "refreshed-token",
                "expires_in": 14400,
                "token_type": "bearer",
            })

        http = httpx.AsyncClient(transport=_mock_transport(handler))
        # now=950, expires_at=1200, buffer=300 → 950 >= 1200-300=900 → refresh
        provider = DropboxTokenProvider(store, http=http, now=lambda: 950.0)
        token = await provider.get_access_token()
        assert token == "refreshed-token"

    @pytest.mark.anyio
    async def test_no_refresh_when_fresh(self, tmp_path):
        """No refresh when token is still well within buffer."""
        store = TokenStore(str(tmp_path / "token.json"))
        store.save({
            "app_key": "abcdefghij1234",
            "refresh_token": "rt",
            "access_token": "valid-token",
            "expires_at": 2000.0,
        })
        refresh_called = False

        def handler(request: httpx.Request):
            nonlocal refresh_called
            refresh_called = True
            return httpx.Response(200, json={"access_token": "x", "expires_in": 14400})

        http = httpx.AsyncClient(transport=_mock_transport(handler))
        # now=1000, expires_at=2000, buffer=300 → 1000 < 2000-300=1700 → no refresh
        provider = DropboxTokenProvider(store, http=http, now=lambda: 1000.0)
        token = await provider.get_access_token()
        assert token == "valid-token"
        assert not refresh_called

    @pytest.mark.anyio
    async def test_force_refresh(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        store.save({
            "app_key": "abcdefghij1234",
            "refresh_token": "rt",
            "access_token": "old-token",
            "expires_at": 99999.0,
        })

        def handler(request: httpx.Request):
            return httpx.Response(200, json={
                "access_token": "forced-token",
                "expires_in": 14400,
                "token_type": "bearer",
            })

        http = httpx.AsyncClient(transport=_mock_transport(handler))
        provider = DropboxTokenProvider(store, http=http, now=lambda: 1000.0)
        token = await provider.get_access_token(force_refresh=True)
        assert token == "forced-token"

    @pytest.mark.anyio
    async def test_concurrent_refresh_single_call(self, tmp_path):
        """Concurrent get_access_token with expired token performs exactly one refresh."""
        store = TokenStore(str(tmp_path / "token.json"))
        store.save({
            "app_key": "abcdefghij1234",
            "refresh_token": "rt",
            "access_token": "old-token",
            "expires_at": 500.0,
        })
        refresh_count = 0
        call_count = 0

        def handler(request: httpx.Request):
            nonlocal refresh_count
            refresh_count += 1
            return httpx.Response(200, json={
                "access_token": "concurrent-token",
                "expires_in": 14400,
                "token_type": "bearer",
            })

        http = httpx.AsyncClient(transport=_mock_transport(handler))
        provider = DropboxTokenProvider(store, http=http, now=lambda: 1000.0)

        # Launch multiple concurrent calls
        results = await asyncio.gather(
            provider.get_access_token(),
            provider.get_access_token(),
            provider.get_access_token(),
        )

        # All should get the same refreshed token
        assert all(r == "concurrent-token" for r in results)
        # Only one actual refresh should have happened
        assert refresh_count == 1

    @pytest.mark.anyio
    async def test_sends_correct_refresh_form_data(self, tmp_path):
        store = TokenStore(str(tmp_path / "token.json"))
        store.save({
            "app_key": "myappkey1234567",
            "refresh_token": "my-refresh-tok",
            "access_token": "old",
            "expires_at": 500.0,
        })
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = request.content.decode()
            return httpx.Response(200, json={
                "access_token": "new",
                "expires_in": 14400,
            })

        http = httpx.AsyncClient(transport=_mock_transport(handler))
        provider = DropboxTokenProvider(store, http=http, now=lambda: 1000.0)
        await provider.get_access_token()

        body = captured["body"]
        assert "grant_type=refresh_token" in body
        assert "refresh_token=my-refresh-tok" in body
        assert "client_id=myappkey1234567" in body

    @pytest.mark.anyio
    async def test_corrupt_token_file_not_linked(self, tmp_path):
        path = str(tmp_path / "token.json")
        with open(path, "w") as f:
            f.write("not valid json{{{")
        store = TokenStore(path)
        provider = DropboxTokenProvider(store)
        with pytest.raises(NotLinkedError):
            await provider.get_access_token()


# ── unlink ──────────────────────────────────────────────────────────────


class TestUnlink:
    @pytest.mark.anyio
    async def test_unlink_deletes_token(self, tmp_path):
        path = str(tmp_path / "token.json")
        store = TokenStore(path)
        store.save({"x": 1})

        class FakeClient:
            async def revoke_token(self):
                pass

        await unlink(store, FakeClient())
        assert not os.path.exists(path)

    @pytest.mark.anyio
    async def test_unlink_with_failing_revoke_still_deletes(self, tmp_path):
        path = str(tmp_path / "token.json")
        store = TokenStore(path)
        store.save({"x": 1})

        class FailingClient:
            async def revoke_token(self):
                raise RuntimeError("network error")

        await unlink(store, FailingClient())
        assert not os.path.exists(path)

    @pytest.mark.anyio
    async def test_unlink_without_client(self, tmp_path):
        path = str(tmp_path / "token.json")
        store = TokenStore(path)
        store.save({"x": 1})
        await unlink(store, None)
        assert not os.path.exists(path)
