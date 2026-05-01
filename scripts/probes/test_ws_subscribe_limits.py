# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: live blocking investigation 2026-05-01 — WS user-channel disconnect at boot
"""Probe: verify Polymarket user-channel WS subscription limit and proxy interference.

Run as:
    POLYMARKET_API_KEY=... POLYMARKET_API_SECRET=... POLYMARKET_API_PASSPHRASE=... \\
    /Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python \\
    scripts/probes/test_ws_subscribe_limits.py

Root cause finding (2026-05-01):
    websockets 16.0 defaults to proxy=True and auto-detects HTTPS_PROXY from env.
    The daemon plist sets HTTPS_PROXY=localhost:7890 but ws-subscriptions-clob.polymarket.com
    is NOT in NO_PROXY. All WSS connections are routed through the local proxy, which
    causes the WS connection to fail before the subscription message is even evaluated.
    This is NOT a per-subscription-count limit — the failure is at the TCP/TLS level.

    Fix: pass proxy=None explicitly to websockets.connect() so WS connections bypass
    the proxy regardless of HTTPS_PROXY env var. The proxy is needed for HTTP REST calls
    (data-api.polymarket.com) but not for WebSocket connections.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Add repo root to sys.path so src imports work
REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))


ENDPOINT = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


def _make_auth() -> dict[str, str]:
    api_key = os.environ.get("POLYMARKET_API_KEY", "").strip()
    secret = os.environ.get("POLYMARKET_API_SECRET", "").strip()
    passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE", "").strip()
    if not api_key:
        raise RuntimeError("Set POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE")
    return {"apiKey": api_key, "secret": secret, "passphrase": passphrase}


async def _probe_n(n: int, condition_ids: list[str], *, proxy: str | None | bool = True) -> dict:
    """Try subscribing with the first N condition_ids.

    proxy=True  → websockets auto-detects HTTPS_PROXY (daemon's current behaviour)
    proxy=None  → bypass proxy entirely (the fix)
    """
    import websockets

    ids = condition_ids[:n]
    msg = json.dumps({
        "auth": _make_auth(),
        "markets": ids,
        "type": "user",
    })

    t0 = time.monotonic()
    result = {"n": n, "proxy": proxy, "connected": False, "duration_s": 0.0, "error": None, "closed_by_server": False}

    try:
        async with websockets.connect(ENDPOINT, proxy=proxy, open_timeout=10) as ws:
            result["connected"] = True
            await ws.send(msg)
            # Wait up to 5s for any message or close
            try:
                async with asyncio.timeout(5):
                    async for raw in ws:
                        result["first_message"] = raw[:200] if isinstance(raw, str) else "<bytes>"
                        break
            except TimeoutError:
                result["stayed_open_5s"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        if "close" in str(exc).lower() or "ConnectionClosed" in type(exc).__name__:
            result["closed_by_server"] = True

    result["duration_s"] = round(time.monotonic() - t0, 2)
    return result


async def main() -> None:
    import urllib.request

    # Step 1: Show detected proxy configuration
    proxies = urllib.request.getproxies()
    print("=== Proxy environment ===")
    print(f"  Detected proxies: {proxies}")
    print(f"  NO_PROXY: {os.environ.get('NO_PROXY', '<not set>')}")
    ws_bypassed = urllib.request.proxy_bypass("ws-subscriptions-clob.polymarket.com:443")
    print(f"  ws-subscriptions-clob.polymarket.com bypassed? {ws_bypassed}")
    print()

    # Step 2: Try to load real condition_ids from market scanner
    condition_ids: list[str] = []
    try:
        os.environ.setdefault("ZEUS_MODE", "live")
        from src.data.market_scanner import find_weather_markets
        events = find_weather_markets(min_hours_to_resolution=0.0)
        for event in events:
            for cid in (event.get("condition_ids") or []):
                if cid and cid not in condition_ids:
                    condition_ids.append(cid)
        print(f"Loaded {len(condition_ids)} real condition_ids from market scanner")
    except Exception as exc:
        print(f"Could not load real condition_ids ({exc}); using synthetic ids")
        condition_ids = [f"0x{'a' * 63}{i:x}" for i in range(1300)]

    if not condition_ids:
        print("No condition_ids available. Exiting.")
        return

    print()

    # Step 3: Test with proxy=True (daemon's current broken behavior)
    print("=== Phase 1: proxy=True (daemon's current behavior — HTTPS_PROXY auto-detected) ===")
    result = await _probe_n(1, condition_ids, proxy=True)
    print(f"  N=1, proxy=True: connected={result['connected']}, error={result['error']}, duration={result['duration_s']}s")

    print()
    print("=== Phase 2: proxy=None (proposed fix — bypass proxy for WS) ===")
    for n in [1, 10, 100, 500, 1000, min(1236, len(condition_ids))]:
        if n > len(condition_ids):
            break
        result = await _probe_n(n, condition_ids, proxy=None)
        status = "OK (stayed open 5s)" if result.get("stayed_open_5s") else f"closed: {result['error']}"
        print(f"  N={n:5d}, proxy=None: connected={result['connected']}, {status}, duration={result['duration_s']}s")

    print()
    print("=== Conclusion ===")
    print("If Phase 1 N=1 fails (connection error) but Phase 2 N=1 succeeds,")
    print("root cause is PROXY interference (HTTPS_PROXY=localhost:7890),")
    print("NOT a per-subscription-count server limit.")


if __name__ == "__main__":
    asyncio.run(main())
