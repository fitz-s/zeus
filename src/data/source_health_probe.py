# Created: 2026-04-30
# Last reused/audited: 2026-05-31
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §2.1; PR #121 forecast-live OpenData-only source-health boundary; fix/edli-stage-readiness-2026-05-31 (gate-canonical generated_at)
"""Source health probe loop — Phase 2 ingest improvement.

Runs a 1-row-fetch + latency probe against each upstream data source.
Results are written atomically to state/source_health.json.

Designed to be called every 10 minutes by the ingest daemon scheduler.
Each source is probed with a short timeout (default 10s) to prevent one
degraded upstream from blocking the others.

Result schema per source:
  {
    "last_success_at": ISO8601 | null,
    "last_failure_at": ISO8601 | null,
    "consecutive_failures": int,
    "degraded_since": ISO8601 | null,
    "latency_ms": int | null,
    "error": str | null,
  }

Top-level file schema also includes "written_at" so consumers can detect
stuck files.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data.openmeteo_client import ARCHIVE_URL, fetch as _fetch_openmeteo

logger = logging.getLogger(__name__)

EXPECTED_SOURCES = [
    "open_meteo_archive",
    "wu_pws",
    "hko",
    "ogimet",
    "ecmwf_open_data",
    "noaa",
    "tigge_mars",
]

# Per-source lower bounds for reachability probes. The ingest scheduler passes a
# generic batch timeout, but slower daily-observation endpoints must not become
# false stale source truth when they routinely answer just outside that budget.
SOURCE_PROBE_TIMEOUT_MINIMUMS: dict[str, float] = {
    "ogimet": 30.0,
}

# tigge_mars is now actively probed by _probe_tigge_mars (2026-05-01) — it
# reads ~/.ecmwfapirc + scheduler_jobs_health.json to decide success/failure.
# Kept for backwards compatibility with the dispatch path; sources listed here
# return a manual-operator stub instead of running their probe function.
_MANUAL_OPERATOR_SOURCES: set[str] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_result(error: str | None = None) -> dict[str, Any]:
    return {
        "last_success_at": None,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "degraded_since": None,
        "latency_ms": None,
        "error": error,
    }


def _probe_open_meteo_archive(timeout: float) -> dict[str, Any]:
    """Probe open-meteo archive API with a minimal 1-day fetch."""
    start = time.monotonic()
    try:
        data = _fetch_openmeteo(
            ARCHIVE_URL,
            {
                "latitude": 51.51,
                "longitude": -0.13,
                "start_date": "2025-01-01",
                "end_date": "2025-01-01",
                # Probe BOTH extrema: HIGH markets settle on temperature_2m_max, LOW on
                # temperature_2m_min. A max-only probe would mask a LOW-endpoint outage
                # (#metric-crossing — different physical quantities, both must be healthy).
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "UTC",
            },
            timeout=timeout,
            max_retries=1,
            endpoint_label="source_health_open_meteo_archive",
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        # Both daily fields must be present and non-empty, else the source is degraded for
        # one metric even if the HTTP call succeeded.
        _daily = (data or {}).get("daily", {})
        if not _daily.get("temperature_2m_max") or not _daily.get("temperature_2m_min"):
            raise ValueError("open-meteo archive missing temperature_2m_max or temperature_2m_min")
        now = _now_iso()
        return {
            "last_success_at": now,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        now = _now_iso()
        return {
            "last_success_at": None,
            "last_failure_at": now,
            "consecutive_failures": 1,
            "degraded_since": now,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
        }


def _probe_wu_pws(timeout: float) -> dict[str, Any]:
    """Probe Weather Underground ICAO endpoint with a minimal fetch."""
    import httpx
    import os

    start = time.monotonic()
    try:
        api_key = os.environ.get("WU_API_KEY", "")
        if not api_key:
            return {
                "last_success_at": None,
                "last_failure_at": None,
                "consecutive_failures": 0,
                "degraded_since": None,
                "latency_ms": None,
                "error": "WU_API_KEY not set — skipping live probe",
            }
        # Minimal probe: 1-day history for London Heathrow
        resp = httpx.get(
            "https://api.weather.com/v1/location/EGLL:9:GB/observations/historical.json",
            params={
                "apiKey": api_key,
                "units": "m",
                "startDate": "20250101",
                "endDate": "20250101",
            },
            timeout=timeout,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        resp.raise_for_status()
        now = _now_iso()
        return {
            "last_success_at": now,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        now = _now_iso()
        return {
            "last_success_at": None,
            "last_failure_at": now,
            "consecutive_failures": 1,
            "degraded_since": now,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
        }


def _probe_hko(timeout: float) -> dict[str, Any]:
    """Probe Hong Kong Observatory data endpoint.

    Targets the HKO open-data climate API (the same endpoint daily_obs_append
    writes from). The previous probe URL — the legacy `cis/statClim` HTML page
    — was retired by HKO in early 2026 and now returns 404. The opendata API
    is the canonical, machine-readable replacement and is what the ingest
    pipeline already consumes; probing it keeps source-health aligned with
    actual data availability rather than a dead landing page.
    """
    import httpx

    today = datetime.now(timezone.utc)
    probe_url = (
        "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"
        f"?dataType=CLMMAXT&year={today.year}&month={today.month:02d}&station=HKO"
    )

    start = time.monotonic()
    try:
        resp = httpx.get(
            probe_url,
            timeout=timeout,
            follow_redirects=True,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        resp.raise_for_status()
        now = _now_iso()
        return {
            "last_success_at": now,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        now = _now_iso()
        return {
            "last_success_at": None,
            "last_failure_at": now,
            "consecutive_failures": 1,
            "degraded_since": now,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
        }


def _probe_ogimet(timeout: float) -> dict[str, Any]:
    """Probe Ogimet METAR endpoint reachability."""
    import httpx

    start = time.monotonic()
    try:
        resp = httpx.get(
            "https://www.ogimet.com/cgi-bin/getmetar",
            params={
                "icao": "EGLL",
                "begin": "202501010000",
                "end": "202501010100",
            },
            timeout=timeout,
            follow_redirects=True,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        # Ogimet returns 200 even on no-data; check it's reachable
        if resp.status_code >= 500:
            raise RuntimeError(f"HTTP {resp.status_code}")
        now = _now_iso()
        return {
            "last_success_at": now,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        now = _now_iso()
        return {
            "last_success_at": None,
            "last_failure_at": now,
            "consecutive_failures": 1,
            "degraded_since": now,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
        }


def _probe_ecmwf_open_data(timeout: float) -> dict[str, Any]:
    """Probe ECMWF Open Data API reachability (HEAD-style check)."""
    import httpx

    start = time.monotonic()
    try:
        resp = httpx.head(
            "https://data.ecmwf.int/forecasts/",
            timeout=timeout,
            follow_redirects=True,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        if resp.status_code >= 500:
            raise RuntimeError(f"HTTP {resp.status_code}")
        now = _now_iso()
        return {
            "last_success_at": now,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        now = _now_iso()
        return {
            "last_success_at": None,
            "last_failure_at": now,
            "consecutive_failures": 1,
            "degraded_since": now,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
        }


def _probe_noaa(timeout: float) -> dict[str, Any]:
    """Probe NOAA ISD reachability."""
    import httpx

    start = time.monotonic()
    try:
        resp = httpx.head(
            "https://www.ncei.noaa.gov/pub/data/noaa/",
            timeout=timeout,
            follow_redirects=True,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        if resp.status_code >= 500:
            raise RuntimeError(f"HTTP {resp.status_code}")
        now = _now_iso()
        return {
            "last_success_at": now,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        now = _now_iso()
        return {
            "last_success_at": None,
            "last_failure_at": now,
            "consecutive_failures": 1,
            "degraded_since": now,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
        }


def _probe_tigge_mars(timeout: float) -> dict[str, Any]:
    """Probe TIGGE/MARS readiness.

    Two-part probe:
      1. Credentials present and well-formed (~/.ecmwfapirc readable JSON).
      2. The latest ingest_tigge_daily job in scheduler_jobs_health.json must
         not be in FAILED state.

    Latency is the credentials parse time (cheap; we never hit MARS from the
    probe loop because MARS retrieval is minutes-scale and would dominate
    the 10-minute probe cadence).
    """
    start = time.monotonic()
    now = _now_iso()

    # Step 1: credential file.
    try:
        from src.data.tigge_pipeline import check_mars_credentials
        cred = check_mars_credentials()
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        return {
            "last_success_at": None,
            "last_failure_at": now,
            "consecutive_failures": 1,
            "degraded_since": now,
            "latency_ms": latency_ms,
            "error": f"tigge_pipeline import failed: {exc}"[:200],
        }
    if not cred.get("ok"):
        latency_ms = int((time.monotonic() - start) * 1000)
        return {
            "last_success_at": None,
            "last_failure_at": now,
            "consecutive_failures": 1,
            "degraded_since": now,
            "latency_ms": latency_ms,
            "error": str(cred.get("error", "tigge_mars credentials missing"))[:200],
        }

    # Step 2: scheduler_jobs_health.json — most recent ingest_tigge_daily job.
    try:
        from src.config import state_path
        health_path = Path(state_path("scheduler_jobs_health.json"))
        if not health_path.exists():
            # First-run / pre-deploy: not-yet-run is not a failure.
            latency_ms = int((time.monotonic() - start) * 1000)
            return {
                "last_success_at": now,
                "last_failure_at": None,
                "consecutive_failures": 0,
                "degraded_since": None,
                "latency_ms": latency_ms,
                "error": "credentials_ok; daemon not yet run ingest_tigge_daily",
            }
        data = json.loads(health_path.read_text())
        entry = data.get("ingest_tigge_daily", {})
        status = entry.get("status")
        if status == "FAILED":
            latency_ms = int((time.monotonic() - start) * 1000)
            return {
                "last_success_at": entry.get("last_success_at"),
                "last_failure_at": entry.get("last_failure_at") or now,
                "consecutive_failures": 1,
                "degraded_since": entry.get("last_failure_at") or now,
                "latency_ms": latency_ms,
                "error": str(entry.get("last_failure_reason", "ingest_tigge_daily FAILED"))[:200],
            }
    except Exception as exc:
        # Probe instrumentation failure should not mask credential success.
        latency_ms = int((time.monotonic() - start) * 1000)
        return {
            "last_success_at": now,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": latency_ms,
            "error": f"credentials_ok; scheduler_health probe error: {exc}"[:200],
        }

    latency_ms = int((time.monotonic() - start) * 1000)
    return {
        "last_success_at": now,
        "last_failure_at": None,
        "consecutive_failures": 0,
        "degraded_since": None,
        "latency_ms": latency_ms,
        "error": None,
    }


def _probe_source(source: str, timeout: float) -> dict[str, Any]:
    """Dispatch probe for one source. Returns result dict."""
    if source in _MANUAL_OPERATOR_SOURCES:
        return {
            "last_success_at": None,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": None,
            "error": "MANUAL_OPERATOR — no automated probe",
        }
    dispatch = {
        "open_meteo_archive": _probe_open_meteo_archive,
        "wu_pws": _probe_wu_pws,
        "hko": _probe_hko,
        "ogimet": _probe_ogimet,
        "ecmwf_open_data": _probe_ecmwf_open_data,
        "noaa": _probe_noaa,
        "tigge_mars": _probe_tigge_mars,
    }
    fn = dispatch.get(source)
    if fn is None:
        return {
            "last_success_at": None,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "degraded_since": None,
            "latency_ms": None,
            "error": f"ABSENT — no probe registered for source={source!r}",
        }
    requested_timeout = float(timeout)
    effective_timeout = max(
        requested_timeout,
        SOURCE_PROBE_TIMEOUT_MINIMUMS.get(source, requested_timeout),
    )
    return fn(effective_timeout)


def _apply_prior_failure_state(
    source: str,
    result: dict[str, Any],
    *,
    prior: dict[str, Any],
) -> dict[str, Any]:
    prev = prior.get(source, {})
    if result.get("error") and result.get("last_success_at") is None and source not in _MANUAL_OPERATOR_SOURCES:
        prev_consec = prev.get("consecutive_failures", 0) or 0
        error = str(result.get("error") or "").lower()
        quota_blocked = source == "open_meteo_archive" and (
            "open-meteo quota exhausted" in error
            or "open-meteo request embargoed" in error
            or ("429" in error and "too many requests" in error)
        )
        # An Open-Meteo quota denial/429 embargo proves this probe did not
        # execute. Do not retain a formerly-successful timestamp that a
        # freshness consumer could mistake for a current healthy probe.
        if prev.get("last_success_at") and not quota_blocked:
            result["last_success_at"] = prev.get("last_success_at")
        result["consecutive_failures"] = prev_consec + 1
        result["degraded_since"] = prev.get("degraded_since") or result.get("last_failure_at")
    else:
        result["consecutive_failures"] = 0
        result["degraded_since"] = None
    return result


def _probe_exception_error_text(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        text = exc.__class__.__name__
    return text[:200]


def probe_sources(
    sources: list[str] | tuple[str, ...] | frozenset[str],
    timeout_per_source_seconds: float = 10.0,
    *,
    _prior_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run health probes for an explicit source subset."""
    prior = _prior_state or {}
    results: dict[str, Any] = {}

    for source in sources:
        logger.debug("Probing source: %s", source)
        try:
            result = _probe_source(source, timeout_per_source_seconds)
        except Exception as exc:
            result = {
                "last_success_at": None,
                "last_failure_at": _now_iso(),
                "consecutive_failures": 1,
                "degraded_since": None,
                "latency_ms": None,
                "error": _probe_exception_error_text(exc),
            }
        result = _apply_prior_failure_state(source, result, prior=prior)
        results[source] = result
        logger.debug("Source %s: latency=%s error=%s", source, result.get("latency_ms"), result.get("error"))

    return results


def probe_all_sources(
    timeout_per_source_seconds: float = 10.0,
    *,
    _prior_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run 1-row-fetch + latency probe against all upstream sources.

    Returns mapping source_name → health_dict. Top-level key "written_at"
    is added by the caller (write_source_health).

    Uses _prior_state to accumulate consecutive_failures and degraded_since
    across calls (so the file on disk acts as the prior state on restart).
    """
    return probe_sources(
        tuple(EXPECTED_SOURCES),
        timeout_per_source_seconds,
        _prior_state=_prior_state,
    )


def write_source_health(
    results: dict[str, Any],
    *,
    state_dir: Path | None = None,
) -> Path:
    """Atomically write state/source_health.json.

    Returns the path written.
    """
    if state_dir is None:
        from src.config import state_path
        out_path = state_path("source_health.json")
    else:
        out_path = state_dir / "source_health.json"

    # written_at: legacy key kept for existing consumers.
    # generated_at: gate-canonical freshness key. The live-release gate's _fresh()
    # and main.evaluate_edli_stage_readiness recognize generated_at|updated_at|
    # observed_at|captured_at, NOT written_at — so without generated_at a genuinely
    # fresh source_health.json is read as missing_timestamp. Same instant as
    # written_at; honors the gate contract (no freshness-window relaxation).
    _stamp = _now_iso()
    payload = {
        "written_at": _stamp,
        "generated_at": _stamp,
        "sources": results,
    }
    tmp = Path(str(out_path) + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(out_path)
    logger.info("source_health.json written: %d sources", len(results))
    return out_path
