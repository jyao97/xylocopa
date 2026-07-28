"""Tests for orchestrator/telemetry.py — daily heartbeat only."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def tele(tmp_path, monkeypatch):
    """Fresh telemetry module with filesystem redirected to tmp_path and a test
    endpoint set so _send() has work to do. threading.Thread is replaced with a
    synchronous stub so assertions against urlopen work immediately."""
    import telemetry as _t
    importlib.reload(_t)

    monkeypatch.setattr(_t, "TELEMETRY_DIR", tmp_path)
    monkeypatch.setattr(_t, "INSTALL_ID_FILE", tmp_path / "install_id")
    monkeypatch.setattr(_t, "CONFIG_FILE", tmp_path / "config.yaml")

    monkeypatch.delenv("XYLOCOPA_TELEMETRY", raising=False)
    monkeypatch.setenv("XYLOCOPA_TELEMETRY_ENDPOINT", "https://example.invalid/v1/event")

    class _SyncThread:
        def __init__(self, target=None, daemon=None, name=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(_t.threading, "Thread", _SyncThread)
    return _t


@pytest.fixture
def mock_urlopen(monkeypatch, tele):
    mock = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=MagicMock())
    ctx.__exit__ = MagicMock(return_value=False)
    mock.return_value = ctx
    monkeypatch.setattr(tele.urllib.request, "urlopen", mock)
    return mock


# ---- Heartbeat behavior ----

def test_first_heartbeat_creates_install_id_and_fires(tele, mock_urlopen):
    assert not tele.INSTALL_ID_FILE.exists()
    tele.record_heartbeat()
    assert mock_urlopen.call_count == 1
    assert tele.INSTALL_ID_FILE.exists()


def test_heartbeat_fires_unconditionally(tele, mock_urlopen):
    # Client no longer gates — Worker dedupes per-day for Discord. Every call
    # produces a POST; D1 keeps the full event stream.
    tele.record_heartbeat()
    tele.record_heartbeat()
    tele.record_heartbeat()
    assert mock_urlopen.call_count == 3


def test_install_id_is_stable_across_calls(tele, mock_urlopen):
    tele.record_heartbeat()
    first = tele.INSTALL_ID_FILE.read_text()
    tele.record_heartbeat()
    assert tele.INSTALL_ID_FILE.read_text() == first


# ---- Opt-out paths ----

def test_disabled_by_env_var(tele, mock_urlopen, monkeypatch):
    monkeypatch.setenv("XYLOCOPA_TELEMETRY", "0")
    tele.record_heartbeat()
    assert mock_urlopen.call_count == 0
    assert not tele.INSTALL_ID_FILE.exists()


@pytest.mark.parametrize("val", ["false", "False", "off", "no"])
def test_disabled_by_env_var_various_values(tele, mock_urlopen, monkeypatch, val):
    monkeypatch.setenv("XYLOCOPA_TELEMETRY", val)
    tele.record_heartbeat()
    assert mock_urlopen.call_count == 0


def test_disabled_by_config_file(tele, mock_urlopen):
    tele.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tele.CONFIG_FILE.write_text("telemetry: false\n")
    tele.record_heartbeat()
    assert mock_urlopen.call_count == 0
    assert not tele.INSTALL_ID_FILE.exists()


def test_empty_endpoint_skips_send(tele, mock_urlopen, monkeypatch):
    monkeypatch.setenv("XYLOCOPA_TELEMETRY_ENDPOINT", "")
    tele.record_heartbeat()
    assert mock_urlopen.call_count == 0
    assert tele.INSTALL_ID_FILE.exists()


# ---- Network resilience ----

def test_network_error_is_swallowed(tele, monkeypatch):
    def _boom(*args, **kwargs):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(tele.urllib.request, "urlopen", _boom)
    tele.record_heartbeat()
    # install_id is written before send is attempted
    assert tele.INSTALL_ID_FILE.exists()


# ---- Payload shape ----

def test_default_endpoint_used_when_env_unset(tele, mock_urlopen, monkeypatch):
    monkeypatch.delenv("XYLOCOPA_TELEMETRY_ENDPOINT", raising=False)
    tele.record_heartbeat()
    assert mock_urlopen.call_count == 1
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == tele.DEFAULT_ENDPOINT


def test_payload_shape_is_structured_json(tele, mock_urlopen):
    import json as _json
    import re

    tele.record_heartbeat()
    req = mock_urlopen.call_args[0][0]
    body = _json.loads(req.data.decode("utf-8"))

    assert set(body.keys()) == {"event", "install_id", "version", "platform", "timestamp"}
    assert body["event"] == "daily_heartbeat"
    assert re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        body["install_id"],
    )
    assert isinstance(body["version"], str) and len(body["version"]) > 0
    assert body["platform"] in ("darwin", "linux", "win32")
    assert body["timestamp"].endswith("Z")


def test_content_type_and_user_agent_set(tele, mock_urlopen):
    tele.record_heartbeat()
    req = mock_urlopen.call_args[0][0]
    assert req.headers.get("Content-type") == "application/json"
    ua = req.headers.get("User-agent", "")
    assert ua.startswith("Xylocopa/")


def test_payload_contains_no_user_content(tele, mock_urlopen):
    tele.record_heartbeat()
    req = mock_urlopen.call_args[0][0]
    body = req.data.decode("utf-8")
    for forbidden in [
        "/home/",
        "prompt",
        "session_name",
        "hostname",
        "agent_output",
        "cf-connecting-ip",
    ]:
        assert forbidden not in body, f"payload must not contain {forbidden!r}"


# ---- Status / toggle API (used by the Monitor page) ----

def test_get_status_default(tele):
    s = tele.get_status()
    assert s["enabled"] is True
    assert s["env_locked"] is False
    assert s["install_id"] is None  # nothing fired yet


def test_get_status_env_locked(tele, monkeypatch):
    monkeypatch.setenv("XYLOCOPA_TELEMETRY", "0")
    s = tele.get_status()
    assert s["enabled"] is False
    assert s["env_locked"] is True


def test_set_enabled_writes_config(tele):
    tele.set_enabled(False)
    assert tele.CONFIG_FILE.exists()
    s = tele.get_status()
    assert s["enabled"] is False

    tele.set_enabled(True)
    s = tele.get_status()
    assert s["enabled"] is True


# ---- Locale robustness ----
# Regression for the win32 install reporting "vunknown": on CJK-locale Windows,
# text-mode open() without an explicit encoding decodes as GBK/Shift-JIS, and
# the em-dash in frontend/package.json raised UnicodeDecodeError. All telemetry
# file IO must pin encoding="utf-8" so the host locale can never decide.

@pytest.fixture
def cjk_locale_open(monkeypatch):
    """Make text-mode open() default to cp936 (GBK) when no encoding is given,
    as it does on a Chinese-locale Windows box."""
    real_open = open

    def _open(file, mode="r", *args, **kwargs):
        if "b" not in str(mode) and "encoding" not in kwargs:
            kwargs["encoding"] = "cp936"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _open)


def test_version_survives_cjk_locale(tele, cjk_locale_open):
    import json as _json
    from pathlib import Path

    pkg = Path(tele.__file__).resolve().parent.parent / "frontend" / "package.json"
    expected = _json.loads(pkg.read_bytes())["version"]
    assert expected != "unknown"
    assert tele._load_version() == expected


def test_config_opt_out_survives_cjk_locale(tele, mock_urlopen, cjk_locale_open):
    # A non-ASCII comment in config.yaml must not break the opt-out — the
    # decode error was swallowed and telemetry kept sending despite
    # telemetry: false. Not all UTF-8 happens to trip GBK (CJK ideographs often
    # decode as mojibake without raising); an em-dash before a newline does,
    # matching the package.json failure. Guard that the content really trips it.
    raw = "# telemetry off —\ntelemetry: false\n".encode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("cp936")
    tele.CONFIG_FILE.write_bytes(raw)
    tele.record_heartbeat()
    assert mock_urlopen.call_count == 0
    assert not tele.INSTALL_ID_FILE.exists()
