"""Dropbox OAuth PKCE link flow, token store, and token provider."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import httpx

from config import DROPBOX_APP_KEY

logger = logging.getLogger("orchestrator.dropbox.auth")

TOKEN_ENDPOINT = "https://api.dropboxapi.com/oauth2/token"
AUTHORIZE_URL = "https://www.dropbox.com/oauth2/authorize"
ACCOUNT_URL = "https://api.dropboxapi.com/2/users/get_current_account"

APP_KEY_RE = re.compile(r"^[A-Za-z0-9]{10,64}$")
PKCE_CHARSET_RE = re.compile(r"^[A-Za-z0-9\-._~]+$")

REFRESH_BUFFER_SECONDS = 300


# ── Exceptions ──────────────────────────────────────────────────────────


class NotLinkedError(Exception):
    """Raised when a token is required but no linked account exists."""


class LinkStateError(Exception):
    """Raised when complete() is called with no pending flow."""


class LinkError(Exception):
    """Raised when the Dropbox token exchange fails (e.g. invalid_grant)."""


# ── PKCE helpers ────────────────────────────────────────────────────────


def make_pkce() -> tuple[str, str]:
    """Generate a PKCE code verifier and S256 challenge.

    Returns (verifier, challenge) where verifier is 43-128 chars of
    [A-Za-z0-9-._~] and challenge is base64url(sha256(verifier)) without
    padding.
    """
    raw = secrets.token_urlsafe(96)
    # token_urlsafe produces [A-Za-z0-9_-] which is a subset of the PKCE charset
    verifier = raw[:128]
    if len(verifier) < 43:
        raise RuntimeError("PKCE verifier too short")

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(app_key: str, challenge: str, state: str,
                        redirect_uri: str | None = None) -> str:
    """Build the Dropbox OAuth2 authorization URL.

    When *redirect_uri* is given it is appended (percent-encoded) so
    Dropbox redirects back instead of showing a code.
    """
    from urllib.parse import quote
    params = (
        f"client_id={app_key}"
        f"&response_type=code"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
        f"&token_access_type=offline"
        f"&state={state}"
    )
    if redirect_uri is not None:
        params += f"&redirect_uri={quote(redirect_uri, safe='')}"
    return f"{AUTHORIZE_URL}?{params}"


# ── Token store ─────────────────────────────────────────────────────────


class TokenStore:
    """Persistent token storage at DROPBOX_SYNC_DIR/token.json."""

    def __init__(self, path: str) -> None:
        self._path = path

    def load(self) -> dict | None:
        """Load the saved token data, or None if missing/corrupt."""
        if not os.path.exists(self._path):
            return None
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("Token file is not a JSON object, treating as corrupt")
                return None
            return data
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("Corrupt token file %s: %s", self._path, exc)
            return None

    def save(self, data: dict) -> None:
        """Atomically write token data with restricted permissions."""
        dir_path = os.path.dirname(self._path)
        os.makedirs(dir_path, exist_ok=True)
        os.chmod(dir_path, 0o700)

        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self._path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def delete(self) -> None:
        """Remove the token file if it exists."""
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass

    @property
    def is_linked(self) -> bool:
        """True when a valid token file exists."""
        return self.load() is not None


# ── Link flow ───────────────────────────────────────────────────────────


LINK_FLOW_EXPIRY_SECONDS = 600  # 10 minutes


class LinkFlow:
    """One-at-a-time PKCE authorization flow."""

    def __init__(self, store: TokenStore, *, http: httpx.AsyncClient | None = None,
                 now: Any = time.time) -> None:
        self._store = store
        self._http = http
        self._now = now
        self._pending: dict[str, Any] | None = None

    def start(self, app_key: str, *, redirect_uri: str | None = None,
              return_to: str | None = None) -> dict:
        """Start a new link flow.

        Returns ``{"authorize_url", "state", "mode", "redirect_uri"}``.
        *mode* is ``"redirect"`` when *redirect_uri* is given, else ``"code"``.
        Validates the app key and replaces any prior pending flow.
        """
        if not APP_KEY_RE.match(app_key):
            raise ValueError(f"Invalid app key: must match {APP_KEY_RE.pattern}")

        verifier, challenge = make_pkce()
        state = secrets.token_urlsafe(32)
        url = build_authorize_url(app_key, challenge, state,
                                  redirect_uri=redirect_uri)

        mode = "redirect" if redirect_uri else "code"

        self._pending = {
            "app_key": app_key,
            "verifier": verifier,
            "state": state,
            "started_at": self._now(),
            "redirect_uri": redirect_uri,
            "return_to": return_to,
        }
        return {
            "authorize_url": url,
            "state": state,
            "mode": mode,
            "redirect_uri": redirect_uri,
        }

    async def complete(self, code: str, *, state: str | None = None) -> dict:
        """Exchange the authorization code for tokens.

        Returns {"account_id", "name", "email"}.
        When *state* is given it must equal the pending state (CSRF guard).
        Raises LinkStateError if no flow is pending, flow expired, or state mismatches.
        Raises LinkError if Dropbox rejects the code.
        """
        if self._pending is None:
            raise LinkStateError("No pending link flow — call start() first")

        if state is not None and state != self._pending["state"]:
            self._pending = None
            raise LinkStateError("state mismatch")

        elapsed = self._now() - self._pending["started_at"]
        if elapsed > LINK_FLOW_EXPIRY_SECONDS:
            self._pending = None
            raise LinkStateError("flow expired")

        pending = self._pending
        self._pending = None

        http = self._http or httpx.AsyncClient()
        own_http = self._http is None
        try:
            # Exchange code for tokens
            form_data: dict[str, str] = {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": pending["app_key"],
                "code_verifier": pending["verifier"],
            }
            if pending.get("redirect_uri"):
                form_data["redirect_uri"] = pending["redirect_uri"]

            resp = await http.post(
                TOKEN_ENDPOINT,
                data=form_data,
            )

            if resp.status_code == 400:
                body = resp.json()
                desc = body.get("error_description", body.get("error", "unknown error"))
                raise LinkError(desc)

            resp.raise_for_status()
            token_data = resp.json()

            # Get account info
            acct_resp = await http.post(
                ACCOUNT_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                content=b"null",
            )
            acct_resp.raise_for_status()
            acct = acct_resp.json()

            now = time.time()
            expires_in = token_data.get("expires_in", 14400)

            self._store.save({
                "app_key": pending["app_key"],
                "refresh_token": token_data["refresh_token"],
                "access_token": token_data["access_token"],
                "expires_at": now + expires_in,
                "account_id": acct["account_id"],
                "account_name": acct["name"]["display_name"],
                "email": acct["email"],
                "scope": token_data.get("scope", ""),
                "linked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

            return {
                "account_id": acct["account_id"],
                "name": acct["name"]["display_name"],
                "email": acct["email"],
            }
        finally:
            if own_http:
                await http.aclose()

    def pending_return_to(self) -> str | None:
        """Return the pending flow's ``return_to`` without clearing it."""
        if self._pending is None:
            return None
        return self._pending.get("return_to")


# ── Token provider ──────────────────────────────────────────────────────


@runtime_checkable
class Revocable(Protocol):
    async def revoke_token(self) -> None: ...


class DropboxTokenProvider:
    """Provides access tokens with automatic refresh."""

    def __init__(
        self,
        store: TokenStore,
        *,
        http: httpx.AsyncClient | None = None,
        now: Any = time.time,
    ) -> None:
        self._store = store
        self._http = http
        self._now = now
        self._lock = asyncio.Lock()

    async def get_access_token(self, force_refresh: bool = False) -> str:
        """Return a valid access token, refreshing if needed.

        Raises NotLinkedError when no token is stored.
        """
        data = self._store.load()
        if data is None:
            raise NotLinkedError("No Dropbox account linked")

        needs_refresh = (
            force_refresh
            or self._now() >= data.get("expires_at", 0) - REFRESH_BUFFER_SECONDS
        )

        if not needs_refresh:
            return data["access_token"]

        async with self._lock:
            # Re-check after acquiring lock (another caller may have refreshed)
            data = self._store.load()
            if data is None:
                raise NotLinkedError("No Dropbox account linked")

            if not force_refresh and self._now() < data.get("expires_at", 0) - REFRESH_BUFFER_SECONDS:
                return data["access_token"]

            app_key = data.get("app_key", DROPBOX_APP_KEY)
            http = self._http or httpx.AsyncClient()
            own_http = self._http is None
            try:
                resp = await http.post(
                    TOKEN_ENDPOINT,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": data["refresh_token"],
                        "client_id": app_key,
                    },
                )
                resp.raise_for_status()
                token_resp = resp.json()

                data["access_token"] = token_resp["access_token"]
                data["expires_at"] = self._now() + token_resp.get("expires_in", 14400)
                self._store.save(data)

                return data["access_token"]
            finally:
                if own_http:
                    await http.aclose()


# ── Unlink ──────────────────────────────────────────────────────────────


async def unlink(store: TokenStore, client: Any | None) -> None:
    """Revoke the token (best-effort) and delete local credentials."""
    if client is not None:
        try:
            await client.revoke_token()
        except Exception as exc:
            logger.warning("Token revocation failed (continuing): %s", exc)
    store.delete()
