"""Shared Open-Meteo HTTP client with retry, 429 handling, and quota tracking.

Phase C extraction: replaces duplicated httpx.get + retry logic in
hourly_instants_append, solar_append, and forecasts_append.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from src.data.openmeteo_quota import OpenMeteoQuotaTracker, quota_tracker

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


def request_identity(url: str, params: dict) -> str:
    """Return a stable identity for the exact executable HTTP request."""

    payload = json.dumps(
        {"url": url, "params": params},
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _endpoint_for_url(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.netloc}{parsed.path}"[:160]


def _retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return 0.0
        if retry_at.tzinfo is None:
            return 0.0
        return max(0.0, (retry_at - datetime.now(retry_at.tzinfo)).total_seconds())


def fetch(
    url: str,
    params: dict,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_sec: float = DEFAULT_BACKOFF_SEC,
    endpoint_label: str = "",
    fast_fail_429: bool = False,
    quota: OpenMeteoQuotaTracker | None = None,
    client: httpx.Client | None = None,
) -> dict:
    """GET an Open-Meteo endpoint with retries, 429 handling, and quota tracking.

    Returns the parsed JSON response dict.

    Raises:
        httpx.HTTPError: after all retries exhausted on transport errors.
        RuntimeError: if quota is exhausted.

    ``fast_fail_429`` is for callers with an independent transport fallback. They still mark
    the quota cooldown, but they receive the 429 immediately instead of sleeping inside this
    shared client and blocking the fallback ladder.
    """
    tracker = quota or quota_tracker
    request_id = request_identity(url, params)
    endpoint = _endpoint_for_url(url)
    job = endpoint_label or endpoint
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        allowed, reason, lease_id = tracker.acquire_request(
            request_id,
            endpoint=endpoint,
            job=job,
            lease_seconds=max(float(timeout) + 5.0, DEFAULT_TIMEOUT),
        )
        if not allowed:
            if reason and reason.startswith(
                ("request_retry_until=", "request_in_flight_until=")
            ):
                raise RuntimeError(f"Open-Meteo request embargoed ({reason})")
            raise RuntimeError(
                f"Open-Meteo quota exhausted ({tracker.calls_today()} calls today)"
            )
        try:
            get = client.get if client is not None else httpx.get
            resp = get(url, params=params, timeout=timeout)

            if resp.status_code == 429:
                wait = _retry_after_seconds(resp.headers.get("Retry-After"))
                if wait <= 0.0:
                    wait = DEFAULT_429_FALLBACK_WAIT * (attempt + 1)
                tracker.note_rate_limited(int(wait))
                tracker.record_request_retry(
                    request_id,
                    endpoint=endpoint,
                    job=job,
                    retry_after_seconds=wait,
                    lease_id=lease_id,
                )
                if fast_fail_429:
                    logger.warning(
                        "Open-Meteo 429 on attempt %d%s — fast-fail to fallback ladder; no client sleep",
                        attempt + 1,
                        f" [{endpoint_label}]" if endpoint_label else "",
                    )
                else:
                    logger.warning(
                        "Open-Meteo 429 on attempt %d%s — persisted cooldown; deferring retry",
                        attempt + 1,
                        f" [{endpoint_label}]" if endpoint_label else "",
                    )
                resp.raise_for_status()

            resp.raise_for_status()
            payload = resp.json()
            recorded = tracker.record_request_success(
                request_id,
                endpoint=endpoint,
                job=job,
                lease_id=lease_id,
            )
            if not recorded:
                raise RuntimeError(
                    "Open-Meteo request lease lost; discarded unowned response"
                )
            return payload

        except httpx.HTTPError as e:
            last_exc = e
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                raise
            retry_delay = tracker.record_request_retry(
                request_id,
                endpoint=endpoint,
                job=job,
                lease_id=lease_id,
            )
            if attempt < max_retries - 1:
                wait = max(backoff_sec * (attempt + 1), float(retry_delay))
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
