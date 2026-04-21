"""Antibodies for proxy health gate (src/data/proxy_health.py).

Created: 2026-04-21
Last reused/audited: 2026-04-21
Authority basis: Phase 10 post-DT-close — Gate F data backfill pre-flight.

Structural relationships:
- "If HTTP_PROXY points at a dead TCP port, the process must strip it rather
  than fail-close downstream HTTP calls."
- "If HTTP_PROXY points at a live proxy, env vars must be preserved verbatim
  (operator intent respected when VPN is on)."

The runtime probe test uses an in-process socket server to simulate a live
proxy without relying on external network. The dead-proxy test binds to a
port and closes immediately to guarantee the TCP connect fails.
"""

from __future__ import annotations

import os
import socket
import threading
from contextlib import closing

import pytest

from src.data.proxy_health import (
    ProxyHealthResult,
    bypass_dead_proxy_env_vars,
)


_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


@pytest.fixture
def clean_proxy_env(monkeypatch):
    """Strip proxy vars before + after the test."""
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_no_proxy_configured_noop(clean_proxy_env):
    """When no proxy env var is set, function returns quickly and changes nothing."""
    result = bypass_dead_proxy_env_vars()
    assert result == ProxyHealthResult(checked_url=None, alive=False, bypassed_vars=())
    for var in _PROXY_ENV_VARS:
        assert var not in os.environ


def test_dead_proxy_stripped(clean_proxy_env, monkeypatch):
    """HTTP_PROXY pointing at a closed port → env vars stripped, bypass recorded."""
    port = _find_free_port()  # port allocated, listener closed immediately
    dead_url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("HTTP_PROXY", dead_url)
    monkeypatch.setenv("HTTPS_PROXY", dead_url)

    result = bypass_dead_proxy_env_vars()

    assert result.alive is False
    assert result.checked_url == dead_url
    assert "HTTP_PROXY" in result.bypassed_vars
    assert "HTTPS_PROXY" in result.bypassed_vars
    assert "HTTP_PROXY" not in os.environ
    assert "HTTPS_PROXY" not in os.environ


def test_live_proxy_preserved(clean_proxy_env, monkeypatch):
    """HTTP_PROXY pointing at a live listener → env vars preserved."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    live_url = f"http://127.0.0.1:{port}"

    def _accept_once():
        try:
            conn, _ = server.accept()
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=_accept_once, daemon=True)
    t.start()

    try:
        monkeypatch.setenv("HTTP_PROXY", live_url)
        monkeypatch.setenv("HTTPS_PROXY", live_url)

        result = bypass_dead_proxy_env_vars()

        assert result.alive is True
        assert result.checked_url == live_url
        assert result.bypassed_vars == ()
        assert os.environ.get("HTTP_PROXY") == live_url
        assert os.environ.get("HTTPS_PROXY") == live_url
    finally:
        server.close()
        t.join(timeout=1.0)


def test_idempotent_on_second_call(clean_proxy_env, monkeypatch):
    """Calling bypass twice after a dead-proxy strip is a no-op the second time."""
    port = _find_free_port()
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{port}")

    first = bypass_dead_proxy_env_vars()
    assert first.alive is False
    assert first.bypassed_vars == ("HTTP_PROXY",)

    second = bypass_dead_proxy_env_vars()
    assert second == ProxyHealthResult(checked_url=None, alive=False, bypassed_vars=())


def test_malformed_proxy_url_safe(clean_proxy_env, monkeypatch):
    """A malformed proxy URL must not crash the function; treated as dead + stripped."""
    monkeypatch.setenv("HTTP_PROXY", "not-a-url")

    result = bypass_dead_proxy_env_vars()

    assert result.alive is False
    assert "HTTP_PROXY" not in os.environ
