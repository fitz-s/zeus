"""Proxy health gate — bypass dead HTTP_PROXY / HTTPS_PROXY at process startup.

Created: 2026-04-21
Last reused/audited: 2026-04-21
Authority basis: Phase 10 post-DT-close — Gate F data backfill pre-flight.
User directive 2026-04-21: daemon should not be blocked by VPN absence;
prefer direct-first, fall back to proxy only on failure.

## Why

Zeus's launchd plists set ``HTTP_PROXY=http://localhost:7890`` +
``HTTPS_PROXY=http://localhost:7890`` for geo-routed trading (Polymarket CLOB).
When the VPN / proxy app is off (normal for data-only periods), all outbound
HTTP goes to a dead TCP port, every request raises ``Request exception``,
and the daemon fail-closes on the wallet check.

This module detects that condition **once at startup** and strips the dead
proxy env vars so the rest of the process runs with direct HTTP. When the
user turns the VPN back on and restarts the daemon, the probe passes and
env vars stay intact — the proxy is used as configured.

## Contract

- Runs once, early in ``main.py::main()`` before any HTTP client import.
- Never raises. Worst case: leaves env vars alone.
- Probe budget is 1 × TCP connect with 1.5 s timeout. Zero HTTP, zero DNS
  against the proxy.
- Emits a WARNING-level log + stamps ``state/scheduler_jobs_health.json``
  (proxy_bypass entry) so operators see that direct-mode is in effect.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import NamedTuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")

# Any probe that passes this TCP connect counts as a live proxy. We do NOT
# send HTTP because some proxies reject direct ``GET /`` with 400/403; the
# TCP-level reachability is sufficient.
_PROBE_TIMEOUT_SECONDS = 1.5


class ProxyHealthResult(NamedTuple):
    """Outcome of the startup proxy health probe."""

    checked_url: str | None
    alive: bool
    bypassed_vars: tuple[str, ...]


def _extract_proxy_url() -> str | None:
    """Return the first proxy URL set in env, or None if no proxy configured."""
    for var in _PROXY_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    return None


def _probe_proxy_tcp(url: str, *, timeout: float = _PROBE_TIMEOUT_SECONDS) -> bool:
    """Try a TCP connect to the proxy host:port. True = alive, False = dead."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def bypass_dead_proxy_env_vars() -> ProxyHealthResult:
    """If HTTP(S)_PROXY is set but unreachable, strip the vars from this
    process's env so subsequent HTTP goes direct.

    Idempotent + side-effect-bounded to ``os.environ``. Calling multiple
    times is safe (second call sees no proxy configured → no-op).
    """
    proxy_url = _extract_proxy_url()
    if not proxy_url:
        return ProxyHealthResult(checked_url=None, alive=False, bypassed_vars=())

    alive = _probe_proxy_tcp(proxy_url)
    if alive:
        logger.info("proxy_health: %s is reachable; proxy env vars retained", proxy_url)
        return ProxyHealthResult(checked_url=proxy_url, alive=True, bypassed_vars=())

    stripped: list[str] = []
    for var in _PROXY_ENV_VARS:
        if var in os.environ:
            stripped.append(var)
            del os.environ[var]
    logger.warning(
        "proxy_health: %s unreachable — stripping %s for this process. "
        "HTTP will go direct. Restart the daemon after turning the proxy back on "
        "to restore proxy routing.",
        proxy_url,
        ", ".join(stripped) or "(nothing)",
    )
    return ProxyHealthResult(
        checked_url=proxy_url, alive=False, bypassed_vars=tuple(stripped)
    )
