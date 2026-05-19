# Created: 2026-05-19
# Last reused/audited: 2026-05-19
# Authority basis: PR 6 WAVE_B_PR_3_6_FIELD_MAP.md row 16; pr36_scaffold.md BLOCKING REVISION 5
"""NTP-style clock skew probe vs Polymarket REST Date: header.

Estimates local host clock skew relative to the Polymarket API server by
sending a HEAD request to a cheap endpoint and reading the ``Date:`` response
header. The skew is the difference (local_time - venue_date_header) in
milliseconds, measured at response receipt.

Threshold semantics (per B4 Wave-B opus critic fix):
  - |skew| ≤ 100ms  → healthy, no signal emitted
  - 100ms < |skew| ≤ 200ms → "clock_drift_warning" (non-blocking observability)
  - |skew| > 200ms  → "excessive_clock_drift" (blocking integrity error)

The 200ms error threshold accommodates typical HTTPS RTT on a healthy network
(30–100ms to Polymarket's CDN). 100ms alone barely exceeds the noise floor,
which would produce false positives under normal latency variance.

Caches result for CACHE_TTL_S (60s) to avoid per-order overhead.

Dependencies: stdlib only (urllib.request, urllib.error, email.utils).
"""

import email.utils
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

# How long to cache the probe result before re-probing (seconds)
CACHE_TTL_S: int = 60

# HTTP HEAD request timeout (seconds)
_DEFAULT_TIMEOUT_S: float = 2.0

# Cache: url → (expires_at_unix_ts, skew_ms)
_CACHE: dict[str, tuple[float, Optional[int]]] = {}


def probe_clock_skew(
    polymarket_base_url: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> Optional[int]:
    """Return estimated clock skew in ms (local − venue). None if probe fails.

    Uses HEAD /markets (cheap endpoint) and reads the ``Date:`` response header.
    Positive value means local clock is ahead of venue. Negative means behind.

    Caches result for CACHE_TTL_S seconds to avoid per-order overhead.
    Cache key is the base URL; different venues get independent cache entries.

    Returns None on any network or parse failure — caller must treat None as
    "skew unknown" (not an error).
    """
    now = time.time()
    cached = _CACHE.get(polymarket_base_url)
    if cached is not None:
        expires_at, skew_ms = cached
        if now < expires_at:
            return skew_ms

    url = polymarket_base_url.rstrip("/") + "/markets"
    skew_ms: Optional[int] = None
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "Zeus/clock-skew-probe/1.0")
        local_before = time.time()
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            local_after = time.time()
            date_header = resp.getheader("Date")
        if date_header:
            # email.utils.parsedate_to_datetime handles RFC 2822 Date: headers
            venue_dt = email.utils.parsedate_to_datetime(date_header)
            venue_ts = venue_dt.timestamp()
            # Use midpoint of request as local reference (NTP-style)
            local_ts = (local_before + local_after) / 2.0
            skew_ms = int(round((local_ts - venue_ts) * 1000))
            logger.debug(
                "clock_skew_probe: url=%s skew_ms=%d rtt_ms=%d",
                url,
                skew_ms,
                int((local_after - local_before) * 1000),
            )
    except urllib.error.URLError as exc:
        logger.debug("clock_skew_probe: HEAD %s failed: %s", url, exc)
    except Exception as exc:
        logger.debug("clock_skew_probe: unexpected error probing %s: %s", url, exc)

    _CACHE[polymarket_base_url] = (now + CACHE_TTL_S, skew_ms)
    return skew_ms


def invalidate_cache(polymarket_base_url: Optional[str] = None) -> None:
    """Invalidate the cache for a specific URL, or all entries if URL is None."""
    if polymarket_base_url is None:
        _CACHE.clear()
    else:
        _CACHE.pop(polymarket_base_url, None)
