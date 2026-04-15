"""Shared Open-Meteo HTTP client with retry, 429 handling, and quota tracking.

Phase C extraction: replaces duplicated httpx.get + retry logic in
hourly_instants_append, solar_append, and forecasts_append.
"""

from __future__ import annotations

import logging
import time

import httpx

from src.data.openmeteo_quota import quota_tracker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical base URLs
# ---------------------------------------------------------------------------

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

# ---------------------------------------------------------------------------
# Defaults (can be overridden per-call)
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_SEC = 2.0
DEFAULT_429_FALLBACK_WAIT = 15.0


def fetch(
    url: str,
    params: dict,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_sec: float = DEFAULT_BACKOFF_SEC,
    endpoint_label: str = "",
) -> dict:
    """GET an Open-Meteo endpoint with retries, 429 handling, and quota tracking.

    Returns the parsed JSON response dict.

    Raises:
        httpx.HTTPError: after all retries exhausted on transport errors.
        RuntimeError: if quota is exhausted.
    """
    if not quota_tracker.can_call():
        raise RuntimeError(
            f"Open-Meteo quota exhausted ({quota_tracker.calls_today()} calls today)"
        )

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = httpx.get(url, params=params, timeout=timeout)

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = (
                    float(retry_after)
                    if retry_after
                    else DEFAULT_429_FALLBACK_WAIT * (attempt + 1)
                )
                quota_tracker.note_rate_limited(int(wait))
                logger.warning(
                    "Open-Meteo 429 on attempt %d%s — waiting %.0fs",
                    attempt + 1,
                    f" [{endpoint_label}]" if endpoint_label else "",
                    wait,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            quota_tracker.record_call(endpoint_label)
            return resp.json()

        except httpx.HTTPError as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = backoff_sec * (attempt + 1)
                logger.debug(
                    "Open-Meteo retry %d/%d%s: %s — waiting %.1fs",
                    attempt + 1,
                    max_retries,
                    f" [{endpoint_label}]" if endpoint_label else "",
                    e,
                    wait,
                )
                time.sleep(wait)
                continue

    raise last_exc or RuntimeError("Open-Meteo fetch exhausted retries")
