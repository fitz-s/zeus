"""Gamma API market scanner: discover active weather markets.

Queries Polymarket's Gamma API for temperature events.
Parses bin structure, token IDs, and prices from market data.
"""

import json
import logging
import os
import re
import sqlite3
import time
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Optional

import httpx

from src import config as runtime_config
from src.config import City, cities_by_name, state_path
from src.contracts.executable_market_snapshot_v2 import (
    FRESHNESS_WINDOW_DEFAULT,
    ExecutableMarketSnapshotV2,
    MarketSnapshotMismatchError,
    canonicalize_legacy_fee_rate_value,
    canonicalize_fee_details,
)
from src.state.snapshot_repo import insert_snapshot
from src.types import Bin
from src.types.market import BinTopologyError, validate_bin_topology

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"

# B017: data-provenance types. See also src/data/__init__.py note.
# Authority literal follows the house pattern established in
# src/contracts/observation_atom.py::ObservationAtom.authority.
ScanAuthority = Literal["VERIFIED", "STALE", "EMPTY_FALLBACK", "NEVER_FETCHED"]
SourceContractStatus = Literal[
    "MATCH",
    "MISSING",
    "AMBIGUOUS",
    "MISMATCH",
    "UNSUPPORTED",
]


@dataclass(frozen=True)
class MarketSnapshot:
    """A provenance-tagged snapshot of active weather events.

    The ``authority`` field explicitly distinguishes:
      - ``VERIFIED``       : fresh network fetch succeeded this call
      - ``STALE``          : network fetch failed, cached data returned
                             (``stale_age_seconds`` > 0, originally fetched
                             at ``fetched_at_utc``)
      - ``EMPTY_FALLBACK`` : network fetch failed AND no cache was
                             available (events == [])
      - ``NEVER_FETCHED``  : initial state before any fetch attempted

    Callers MAY treat the events as a plain ``list[dict]`` for backwards
    compatibility, but live-trading call paths SHOULD branch on
    ``authority`` before generating new BUY/SELL signals on potentially
    stale event data (Fitz methodology constraint #4: data provenance).
    """

    events: list[dict] = field(default_factory=list)
    authority: ScanAuthority = "NEVER_FETCHED"
    fetched_at_utc: datetime | None = None
    stale_age_seconds: float | None = None


@dataclass(frozen=True)
class SourceContractCheck:
    """Settlement-source proof extracted from Gamma resolution metadata."""

    status: SourceContractStatus
    reason: str
    resolution_sources: tuple[str, ...]
    source_family: str | None
    station_id: str | None
    configured_source_family: str
    configured_station_id: str | None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "resolution_sources": list(self.resolution_sources),
            "source_family": self.source_family,
            "station_id": self.station_id,
            "configured_source_family": self.configured_source_family,
            "configured_station_id": self.configured_station_id,
        }


@dataclass(frozen=True)
class MarketSupportTopology:
    """Complete settlement support plus aligned executable child metadata."""

    support_bins: list[Bin]
    executable_mask: tuple[bool, ...]
    token_payload_by_support_index: dict[int, dict]
    support_outcomes: list[dict]
    executable_outcomes: list[dict]
    topology_status: str
    provenance: dict

# Temperature keywords for event matching
TEMP_KEYWORDS = {"temperature", "highest temp", "°f", "°c", "fahrenheit", "celsius"}
_SOURCE_URL_RE = re.compile(
    r"https?://[^\s)>\]\"']+",
    re.IGNORECASE,
)

_LOW_METRIC_KEYWORDS = (
    "lowest temperature",
    "low temperature",
    "lowest temp",
    "minimum temperature",
    "minimum temp",
    "min temperature",
    "daily low",
    "overnight low",
    "coldest temperature",
)

# Tag slugs to search (in priority order)
TAG_SLUGS = ["temperature", "weather", "daily-temperature"]
_ACTIVE_EVENTS_CACHE: list[dict] | None = None
_ACTIVE_EVENTS_CACHE_AT: float = 0.0  # monotonic timestamp of last fetch
_ACTIVE_EVENTS_CACHE_AT_UTC: datetime | None = None  # wall-clock of last successful fetch
_ACTIVE_EVENTS_LAST_STATUS: ScanAuthority = "NEVER_FETCHED"  # B017 provenance flag
_ACTIVE_EVENTS_TTL: float = 300.0  # 5-minute TTL
SOURCE_CONTRACT_QUARANTINE_PATH_ENV = "ZEUS_SOURCE_CONTRACT_QUARANTINE_PATH"
SOURCE_CONTRACT_QUARANTINE_SCHEMA_VERSION = 1
SOURCE_CONTRACT_ALERT_STATUSES = frozenset({"AMBIGUOUS", "MISMATCH", "UNSUPPORTED"})
REQUIRED_SOURCE_CONVERSION_EVIDENCE = (
    "config_updated",
    "source_validity_updated",
    "backfill_completed",
    "settlements_rebuilt",
    "calibration_rebuilt",
    "verification_passed",
)
SOURCE_CONVERSION_EVIDENCE_DESCRIPTIONS = {
    "config_updated": "config/cities.json reflects the new settlement source contract.",
    "source_validity_updated": "docs/operations/current_source_validity.md records fresh source audit evidence.",
    "backfill_completed": "affected city/date/metric/source-role rows have been backfilled or explicitly declared not required.",
    "settlements_rebuilt": "affected settlement rows have been rebuilt or quarantined with row-level provenance.",
    "calibration_rebuilt": "affected calibration pairs and Platt calibration buckets have been rebuilt.",
    "verification_passed": "focused scanner/watch/rebuild/calibration verification has passed.",
}
PENDING_SOURCE_CONVERSIONS_CONFIG_KEY = "_source_contract_pending_conversions"


def source_contract_quarantine_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    override = os.environ.get(SOURCE_CONTRACT_QUARANTINE_PATH_ENV)
    if override:
        return Path(override)
    return state_path("source_contract_quarantine.json")


def _empty_source_contract_quarantine_payload() -> dict:
    return {
        "schema_version": SOURCE_CONTRACT_QUARANTINE_SCHEMA_VERSION,
        "updated_at": None,
        "cities": {},
        "transition_history": [],
    }


def _canonical_city_name(city_name: str) -> str:
    candidate = str(city_name or "").strip()
    if not candidate:
        raise ValueError("source-contract quarantine requires city_name")
    for configured_name in runtime_config.runtime_cities_by_name():
        if configured_name.lower() == candidate.lower():
            return configured_name
    return candidate


def load_source_contract_quarantines(path: str | Path | None = None) -> dict:
    quarantine_path = source_contract_quarantine_path(path)
    try:
        payload = json.loads(quarantine_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_source_contract_quarantine_payload()
    if not isinstance(payload, dict):
        raise ValueError(f"{quarantine_path} must contain a JSON object")
    cities = payload.get("cities")
    if not isinstance(cities, dict):
        raise ValueError(f"{quarantine_path} missing object field 'cities'")
    transition_history = payload.setdefault("transition_history", [])
    if not isinstance(transition_history, list):
        raise ValueError(f"{quarantine_path} field 'transition_history' must be a list")
    return payload


def _write_source_contract_quarantines(payload: dict, path: str | Path | None = None) -> Path:
    quarantine_path = source_contract_quarantine_path(path)
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = quarantine_path.with_name(f".{quarantine_path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(quarantine_path)
    return quarantine_path


def active_source_contract_quarantines(path: str | Path | None = None) -> dict[str, dict]:
    payload = load_source_contract_quarantines(path)
    active: dict[str, dict] = {}
    for city_name, entry in payload.get("cities", {}).items():
        if isinstance(entry, dict) and entry.get("status") == "active":
            active[str(city_name)] = dict(entry)
    return active


def _configured_pending_source_conversions() -> dict[str, dict]:
    try:
        payload = json.loads(
            (runtime_config.CONFIG_DIR / "cities.json").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return {}
    entries = payload.get(PENDING_SOURCE_CONVERSIONS_CONFIG_KEY, [])
    if not isinstance(entries, list):
        raise ValueError(
            f"config/cities.json field {PENDING_SOURCE_CONVERSIONS_CONFIG_KEY!r} must be a list"
        )
    pending: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(
                f"config/cities.json field {PENDING_SOURCE_CONVERSIONS_CONFIG_KEY!r} must contain objects"
            )
        city_name = str(entry.get("city") or "").strip()
        status = str(entry.get("status") or "").strip()
        if not city_name or status != "pending_release":
            continue
        pending[_canonical_city_name(city_name)] = dict(entry)
    return pending


def _source_conversion_release_complete(city_name: str, path: str | Path | None = None) -> bool:
    for record in source_contract_transition_history(city_name, path=path):
        completed = record.get("completed_release_evidence")
        if not isinstance(completed, dict):
            continue
        if all(
            isinstance(completed.get(key), dict)
            and completed[key].get("completed") is True
            and _evidence_ref_present(completed[key].get("evidence_ref"))
            for key in REQUIRED_SOURCE_CONVERSION_EVIDENCE
        ):
            return True
    return False


def pending_source_contract_conversion(
    city_name: str,
    path: str | Path | None = None,
) -> dict | None:
    canonical = _canonical_city_name(city_name)
    pending = _configured_pending_source_conversions().get(canonical)
    if pending is None:
        return None
    if _source_conversion_release_complete(canonical, path=path):
        return None
    return pending


def is_city_source_quarantined(city_name: str, path: str | Path | None = None) -> bool:
    try:
        canonical = _canonical_city_name(city_name)
        if canonical in active_source_contract_quarantines(path):
            return True
        return pending_source_contract_conversion(canonical, path=path) is not None
    except Exception as exc:
        logger.error(
            "Source-contract quarantine state unreadable; blocking new entries fail-closed: %s",
            exc,
        )
        return True


def _evidence_ref_present(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_evidence_ref_present(item) for item in value)
    if isinstance(value, dict):
        return any(
            _evidence_ref_present(value.get(key))
            for key in ("evidence_ref", "receipt", "path", "url", "command", "artifact")
        )
    return False


def missing_source_conversion_evidence(evidence: dict) -> list[str]:
    release_evidence = dict(evidence or {})
    evidence_refs = release_evidence.get("evidence_refs", {})
    if not isinstance(evidence_refs, dict):
        evidence_refs = {}
    missing: list[str] = []
    for key in REQUIRED_SOURCE_CONVERSION_EVIDENCE:
        if not release_evidence.get(key):
            missing.append(key)
            continue
        ref_value = evidence_refs.get(key)
        if not _evidence_ref_present(ref_value):
            missing.append(f"{key}:evidence_ref")
    return missing


def _sorted_unique(values) -> list[str]:
    normalized = {
        str(value).strip()
        for value in values
        if value is not None and str(value).strip()
    }
    return sorted(normalized)


def source_contract_transition_branch(entry: dict | None) -> str:
    """Classify the source-change branch represented by a quarantine entry."""
    if not isinstance(entry, dict):
        return "no_active_quarantine"
    events = ((entry.get("evidence") or {}).get("events") or [])
    statuses = set()
    observed_families = set()
    configured_families = set()
    observed_stations = set()
    configured_stations = set()
    for event in events:
        contract = event.get("source_contract") or {}
        if contract.get("status"):
            statuses.add(str(contract["status"]))
        if contract.get("source_family"):
            observed_families.add(str(contract["source_family"]))
        if contract.get("configured_source_family"):
            configured_families.add(str(contract["configured_source_family"]))
        if contract.get("station_id"):
            observed_stations.add(str(contract["station_id"]))
        if contract.get("configured_station_id"):
            configured_stations.add(str(contract["configured_station_id"]))
    if "UNSUPPORTED" in statuses:
        return "unsupported_source_requires_manual_provider_adapter_review"
    if "AMBIGUOUS" in statuses:
        return "ambiguous_source_requires_manual_market_attestation"
    if len(observed_families | configured_families) > 1:
        return "provider_family_change_requires_new_source_role"
    if observed_stations and configured_stations and observed_stations != configured_stations:
        return "same_provider_station_change"
    if "MISMATCH" in statuses:
        return "source_contract_mismatch"
    return "source_contract_review"


def _source_contract_transition_record(
    *,
    city: str,
    entry: dict,
    release_evidence: dict,
    released_at: str,
    released_by: str,
) -> dict:
    events = ((entry.get("evidence") or {}).get("events") or [])
    contracts = [
        event.get("source_contract") or {}
        for event in events
        if isinstance(event, dict)
    ]
    evidence_refs = release_evidence.get("evidence_refs", {})
    if not isinstance(evidence_refs, dict):
        evidence_refs = {}

    completed_evidence = {
        key: {
            "completed": bool(release_evidence.get(key)),
            "evidence_ref": evidence_refs.get(key),
        }
        for key in REQUIRED_SOURCE_CONVERSION_EVIDENCE
    }
    affected_dates = _sorted_unique(event.get("target_date") for event in events)
    event_ids = _sorted_unique(event.get("event_id") for event in events)
    resolution_sources = _sorted_unique(
        source
        for contract in contracts
        for source in (contract.get("resolution_sources") or [])
    )
    from_families = _sorted_unique(
        contract.get("configured_source_family") for contract in contracts
    )
    from_stations = _sorted_unique(
        contract.get("configured_station_id") for contract in contracts
    )
    to_families = _sorted_unique(contract.get("source_family") for contract in contracts)
    to_stations = _sorted_unique(contract.get("station_id") for contract in contracts)

    return {
        "schema_version": SOURCE_CONTRACT_QUARANTINE_SCHEMA_VERSION,
        "city": city,
        "status": "released",
        "reason": entry.get("reason"),
        "transition_branch": source_contract_transition_branch(entry),
        "detected_at": entry.get("first_seen_at"),
        "last_seen_at": entry.get("last_seen_at"),
        "released_at": released_at,
        "released_by": str(released_by or "unknown"),
        "affected_target_dates": affected_dates,
        "first_affected_target_date": affected_dates[0] if affected_dates else None,
        "last_affected_target_date": affected_dates[-1] if affected_dates else None,
        "event_ids": event_ids,
        "affected_event_count": len(event_ids),
        "from_source_contract": {
            "source_families": from_families,
            "station_ids": from_stations,
        },
        "to_source_contract": {
            "source_families": to_families,
            "station_ids": to_stations,
            "resolution_sources": resolution_sources,
        },
        "completed_release_evidence": completed_evidence,
    }


def source_contract_transition_history(
    city_name: str | None = None,
    *,
    path: str | Path | None = None,
) -> list[dict]:
    """Return recorded source-contract conversion history, optionally by city."""
    payload = load_source_contract_quarantines(path)
    history = [
        dict(record)
        for record in payload.get("transition_history", [])
        if isinstance(record, dict)
    ]
    if city_name is None:
        return history
    canonical = _canonical_city_name(city_name)
    return [
        record
        for record in history
        if str(record.get("city") or "").lower() == canonical.lower()
    ]


def upsert_source_contract_quarantine(
    city_name: str,
    *,
    reason: str,
    evidence: dict,
    observed_at: str | None = None,
    source: str = "watch_source_contract",
    path: str | Path | None = None,
) -> dict:
    canonical = _canonical_city_name(city_name)
    now = observed_at or datetime.now(timezone.utc).isoformat()
    payload = load_source_contract_quarantines(path)
    cities = payload.setdefault("cities", {})
    existing = cities.get(canonical, {}) if isinstance(cities.get(canonical), dict) else {}
    first_seen_at = (
        existing.get("first_seen_at")
        if existing.get("status") == "active"
        else now
    )
    entry = {
        "city": canonical,
        "status": "active",
        "reason": str(reason or "source_contract_mismatch"),
        "first_seen_at": first_seen_at,
        "last_seen_at": now,
        "source": str(source or "watch_source_contract"),
        "evidence": dict(evidence or {}),
    }
    cities[canonical] = entry
    payload["schema_version"] = SOURCE_CONTRACT_QUARANTINE_SCHEMA_VERSION
    payload["updated_at"] = now
    quarantine_path = _write_source_contract_quarantines(payload, path)
    return {
        "status": "written",
        "city": canonical,
        "path": str(quarantine_path),
        "entry": entry,
    }


def release_source_contract_quarantine(
    city_name: str,
    *,
    released_by: str,
    evidence: dict,
    released_at: str | None = None,
    path: str | Path | None = None,
) -> dict:
    canonical = _canonical_city_name(city_name)
    release_evidence = dict(evidence or {})
    missing = missing_source_conversion_evidence(release_evidence)
    if missing:
        return {
            "status": "blocked",
            "city": canonical,
            "missing_evidence": missing,
        }

    now = released_at or datetime.now(timezone.utc).isoformat()
    payload = load_source_contract_quarantines(path)
    cities = payload.setdefault("cities", {})
    entry = cities.get(canonical)
    if not isinstance(entry, dict) or entry.get("status") != "active":
        return {"status": "noop", "city": canonical, "reason": "not_active"}

    released_entry = dict(entry)
    transition_record = _source_contract_transition_record(
        city=canonical,
        entry=released_entry,
        release_evidence=release_evidence,
        released_at=now,
        released_by=str(released_by or "unknown"),
    )
    released_entry.update(
        {
            "status": "released",
            "released_at": now,
            "released_by": str(released_by or "unknown"),
            "release_evidence": release_evidence,
            "transition_record": transition_record,
        }
    )
    cities[canonical] = released_entry
    payload.setdefault("transition_history", []).append(transition_record)
    payload["schema_version"] = SOURCE_CONTRACT_QUARANTINE_SCHEMA_VERSION
    payload["updated_at"] = now
    quarantine_path = _write_source_contract_quarantines(payload, path)
    return {
        "status": "released",
        "city": canonical,
        "path": str(quarantine_path),
        "entry": released_entry,
        "transition_record": transition_record,
    }


def infer_temperature_metric(*text_surfaces: str) -> str:
    """Infer market metric from free text.

    Returns:
        "low" when text clearly describes daily lows; otherwise "high".
    """
    text = " ".join(str(surface or "") for surface in text_surfaces).lower()
    if any(keyword in text for keyword in _LOW_METRIC_KEYWORDS):
        return "low"
    return "high"


def _gamma_get(path: str, *, params: dict | None = None, timeout: float = 15.0, retries: int = 3) -> httpx.Response:
    """GET a Gamma API path with retries on transient connection errors.

    The proxy path to gamma-api.polymarket.com periodically returns
    'Connection reset by peer' (errno 54). Retrying with a short backoff
    recovers reliably without masking real failures — after `retries`
    attempts the last exception propagates.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.get(f"{GAMMA_BASE}{path}", params=params, timeout=timeout)
            return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    assert last_exc is not None
    raise last_exc


# Created: 2026-05-01
def _persist_market_events_to_db(results: list[dict], db_path: str | Path | None = None) -> int:
    """Upsert scanned market events into market_events_v2.

    Uses INSERT OR IGNORE so repeated scans are idempotent — existing rows
    keyed on (market_slug, condition_id) are never overwritten.

    Returns the count of newly inserted rows (ignored rows not counted).
    Fails silently on DB errors: logs a warning and returns 0 so that market
    scanning is never blocked by persistence failures.
    """
    if not results:
        return 0

    from src.state.db import ZEUS_FORECASTS_DB_PATH  # local import to avoid circular dependency

    resolved_path = Path(db_path) if db_path is not None else ZEUS_FORECASTS_DB_PATH
    inserted = 0
    try:
        conn = sqlite3.connect(str(resolved_path), timeout=30)
        try:
            for event in results:
                market_slug = event.get("slug", "")
                city_obj = event.get("city")
                city_name = city_obj.name if city_obj is not None else ""
                target_date = str(event.get("target_date", ""))
                temperature_metric = event.get("temperature_metric", "")
                created_at = event.get("created_at")
                for outcome in event.get("outcomes", []):
                    condition_id = outcome.get("condition_id", "")
                    token_id = outcome.get("token_id", "")
                    range_label = outcome.get("title", "")
                    range_low = outcome.get("range_low")
                    range_high = outcome.get("range_high")
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO market_events_v2
                            (market_slug, city, target_date, temperature_metric,
                             condition_id, token_id, range_label, range_low,
                             range_high, outcome, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            market_slug,
                            city_name,
                            target_date,
                            temperature_metric,
                            condition_id,
                            token_id,
                            range_label,
                            range_low,
                            range_high,
                            range_label,
                            created_at,
                        ),
                    )
                    inserted += cursor.rowcount
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("market_events_v2 persistence failed (non-fatal): %s", exc)
        return 0
    return inserted


def _dedupe_condition_ids(values) -> list[str]:
    """Order-preserving dedupe of condition_id strings.

    Drops empty/None entries (a non-executable child market may carry an empty
    condition_id; we must not subscribe to it).
    """
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def extract_executable_condition_ids(events: list[dict]) -> list[str]:
    """Flatten + dedupe executable condition_ids across a list of event dicts.

    Used by ``src/main.py::_start_user_channel_ingestor_if_enabled`` to derive
    the user-channel WS subscription set from the live scanner output instead
    of a hardcoded ``POLYMARKET_USER_WS_CONDITION_IDS`` plist value
    (operator directive 2026-05-01: "任何硬编码bankroll都是一次严重的结构性失误";
    same shape applies to hardcoded condition_id lists, which drift from
    on-chain truth as markets rotate).
    """
    all_ids: list[str] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        all_ids.extend(event.get("condition_ids") or [])
    return _dedupe_condition_ids(all_ids)


def find_weather_markets(
    min_hours_to_resolution: float = 6.0,
) -> list[dict]:
    """Find active weather temperature markets. Spec §6.2.

    Returns list of enriched event dicts with parsed city, date, outcomes.
    """
    events = _get_active_events()
    if not events:
        _mark_keyword_fallback_authority()
        events = _fetch_events_by_keyword("temperature")

    results = []
    now = datetime.now(timezone.utc)

    for event in events:
        parsed = _parse_event(event, now, min_hours_to_resolution)
        if parsed is not None:
            source_contract = parsed.get("source_contract", {})
            if source_contract.get("status") != "MATCH":
                logger.warning(
                    "Skipping Gamma market without matched settlement source contract: "
                    "city=%s status=%s reason=%s event=%s",
                    parsed.get("city").name if parsed.get("city") else "?",
                    source_contract.get("status"),
                    source_contract.get("reason"),
                    parsed.get("event_id"),
                )
                continue
            city = parsed.get("city")
            city_name = city.name if city else ""
            if city_name and is_city_source_quarantined(city_name):
                logger.warning(
                    "Skipping Gamma market while city source-contract quarantine is active: "
                    "city=%s event=%s",
                    city_name,
                    parsed.get("event_id"),
                )
                continue
            results.append(parsed)

    logger.info("Found %d active weather markets", len(results))
    _persist_market_events_to_db(results)
    return results


def get_current_yes_price(market_id: str) -> Optional[float]:
    """Fetch the current YES-side price for an active market via Gamma event data.

    Used during monitor cycles as the observable market price source when live
    CLOB VWMP is not available (e.g. non-CLOB positions).
    """
    events = _get_active_events()
    if not events:
        _mark_keyword_fallback_authority()
        events = _fetch_events_by_keyword("temperature")

    for event in events:
        for outcome in _extract_outcomes(event):
            if outcome.get("market_id") == market_id:
                if not outcome.get("executable"):
                    return None
                price = outcome.get("price")
                if price is None:
                    return None
                return float(price)
    return None


def get_sibling_outcomes(market_id: str) -> list[dict]:
    """Return ALL outcomes (bins) for the event containing market_id.

    S6: needed by monitor_refresh to build the full bin vector for
    calibrate_and_normalize() (same path as entry).
    """
    events = _get_active_events()
    if not events:
        _mark_keyword_fallback_authority()
        events = _fetch_events_by_keyword("temperature")

    for event in events:
        outcomes = _extract_outcomes(event)
        if any(o.get("market_id") == market_id for o in outcomes):
            return outcomes
    return []


def _get_active_events() -> list[dict]:
    """Return active events list (legacy API, backwards-compatible).

    Prefer ``_get_active_events_snapshot()`` when you need provenance
    metadata (B017). This wrapper unpacks the snapshot's events list so
    existing callers continue to work unchanged.
    """
    return list(_get_active_events_snapshot().events)


def _get_active_events_snapshot() -> MarketSnapshot:
    """Return a MarketSnapshot with explicit provenance (B017 / SD-H).

    On successful fetch: authority="VERIFIED", stale_age_seconds=0.0.
    On network failure with cache: authority="STALE", stale_age_seconds
        = seconds since last successful fetch.
    On network failure without cache: authority="EMPTY_FALLBACK",
        events=[].
    """
    global _ACTIVE_EVENTS_CACHE, _ACTIVE_EVENTS_CACHE_AT
    global _ACTIVE_EVENTS_CACHE_AT_UTC, _ACTIVE_EVENTS_LAST_STATUS
    now = time.monotonic()
    fresh_needed = (
        _ACTIVE_EVENTS_CACHE is None
        or (now - _ACTIVE_EVENTS_CACHE_AT) > _ACTIVE_EVENTS_TTL
    )
    if fresh_needed:
        try:
            _ACTIVE_EVENTS_CACHE = _fetch_events_by_tags()
            _ACTIVE_EVENTS_CACHE_AT = now
            _ACTIVE_EVENTS_CACHE_AT_UTC = datetime.now(timezone.utc)
            _ACTIVE_EVENTS_LAST_STATUS = "VERIFIED"
        except httpx.RequestError as e:
            if _ACTIVE_EVENTS_CACHE is not None:
                stale_age = now - _ACTIVE_EVENTS_CACHE_AT
                logger.error(
                    "Active events fetch failed, returning STALE cache: "
                    "error=%s stale_age_seconds=%.1f cache_ttl=%.1f",
                    e,
                    stale_age,
                    _ACTIVE_EVENTS_TTL,
                )
                _ACTIVE_EVENTS_LAST_STATUS = "STALE"
                return MarketSnapshot(
                    events=list(_ACTIVE_EVENTS_CACHE),
                    authority="STALE",
                    fetched_at_utc=_ACTIVE_EVENTS_CACHE_AT_UTC,
                    stale_age_seconds=stale_age,
                )
            logger.error(
                "Active events fetch failed and no cache available: %s", e
            )
            _ACTIVE_EVENTS_LAST_STATUS = "EMPTY_FALLBACK"
            return MarketSnapshot(
                events=[],
                authority="EMPTY_FALLBACK",
                fetched_at_utc=None,
                stale_age_seconds=None,
            )
    # Cache still valid (within TTL) -- treat as VERIFIED from the most
    # recent successful fetch. stale_age_seconds reflects elapsed time
    # since that fetch (informational only; within TTL it is not stale).
    _ACTIVE_EVENTS_LAST_STATUS = "VERIFIED"
    return MarketSnapshot(
        events=list(_ACTIVE_EVENTS_CACHE) if _ACTIVE_EVENTS_CACHE else [],
        authority="VERIFIED",
        fetched_at_utc=_ACTIVE_EVENTS_CACHE_AT_UTC,
        stale_age_seconds=0.0,
    )


def get_last_scan_authority() -> ScanAuthority:
    """Return the provenance authority of the most recent scan (B017).

    Dual-Track callers that need to fail-closed on stale market data may
    check this after calling ``find_weather_markets``/``get_current_yes_price``
    /``get_sibling_outcomes``. Returns ``"NEVER_FETCHED"`` before any
    scan has occurred.
    """
    return _ACTIVE_EVENTS_LAST_STATUS


def _mark_keyword_fallback_authority() -> None:
    """Mark keyword-search Gamma results as degraded provenance.

    The tag path is the authoritative discovery surface. Keyword search is a
    recovery fallback with weaker provenance, so live entry must not turn it
    into executable candidates without an explicit fail-closed gate.
    """

    global _ACTIVE_EVENTS_LAST_STATUS
    _ACTIVE_EVENTS_LAST_STATUS = "EMPTY_FALLBACK"


def _clear_active_events_cache() -> None:
    global _ACTIVE_EVENTS_CACHE, _ACTIVE_EVENTS_CACHE_AT
    global _ACTIVE_EVENTS_CACHE_AT_UTC, _ACTIVE_EVENTS_LAST_STATUS
    _ACTIVE_EVENTS_CACHE = None
    _ACTIVE_EVENTS_CACHE_AT = 0.0
    _ACTIVE_EVENTS_CACHE_AT_UTC = None
    _ACTIVE_EVENTS_LAST_STATUS = "NEVER_FETCHED"


def _fetch_events_by_tags() -> list[dict]:
    """Fetch events using tag slugs."""
    network_errors = 0
    all_events = []
    seen_ids = set()
    for tag_slug in TAG_SLUGS:
        try:
            # Resolve tag ID
            resp = _gamma_get(f"/tags/slug/{tag_slug}")
            if resp.status_code != 200:
                continue
            tag_data = resp.json()
            tag_id = tag_data.get("id")
            if not tag_id:
                continue

            # Fetch events with this tag
            events = []
            offset = 0
            while True:
                resp = _gamma_get("/events", params={
                    "tag_id": tag_id, "closed": "false", "limit": 50, "offset": offset
                })
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                events.extend(batch)
                if len(batch) < 50:
                    break
                offset += 50

            for event in events:
                event_id = event.get("id") or event.get("slug")
                if event_id not in seen_ids:
                    seen_ids.add(event_id)
                    event["_matched_tags"] = [tag_slug]
                    all_events.append(event)
                else:
                    for ex in all_events:
                        if (ex.get("id") or ex.get("slug")) == event_id:
                            ex.setdefault("_matched_tags", []).append(tag_slug)
                            break
        except httpx.HTTPError as e:
            logger.warning("Tag fetch failed for %s: %s", tag_slug, e)
            network_errors += 1
            continue

    if network_errors == len(TAG_SLUGS):
        raise httpx.RequestError(f"All {len(TAG_SLUGS)} tag fetches failed due to network errors")
    return all_events


def _fetch_events_by_keyword(keyword: str) -> list[dict]:
    """Fallback: fetch events by keyword search."""
    try:
        resp = _gamma_get("/events", params={
            "closed": "false", "limit": 100, "title": keyword
        })
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        logger.warning("Keyword fetch failed: %s", e)
        return []


def _parse_event(
    event: dict,
    now: datetime,
    min_hours: float,
) -> Optional[dict]:
    """Parse a Gamma event into Zeus format. Returns None if not a valid weather market."""
    title = (event.get("title") or "").lower()

    # Must be a temperature event
    if not any(kw in title for kw in TEMP_KEYWORDS):
        return None

    # Match city
    city = _match_city(title, event.get("slug", ""))
    if city is None:
        return None
    sanity_rejection = _market_city_sanity_rejection(event, city)
    if sanity_rejection is not None:
        logger.warning(
            "Rejecting Gamma market city mismatch: city=%s reason=%s event=%s",
            city.name,
            sanity_rejection,
            event.get("id") or event.get("slug"),
        )
        return None
    source_contract = _check_source_contract(event, city)
    if source_contract.status in {"AMBIGUOUS", "MISMATCH", "UNSUPPORTED"}:
        logger.warning(
            "Rejecting Gamma market source contract mismatch: city=%s status=%s "
            "reason=%s event=%s sources=%s",
            city.name,
            source_contract.status,
            source_contract.reason,
            event.get("id") or event.get("slug"),
            list(source_contract.resolution_sources),
        )
        return None

    # Parse target date from slug or end date
    target_date = _parse_target_date(event, city)
    if target_date is None:
        return None

    # Check time to resolution
    end_str = event.get("endDate") or event.get("end_date")
    if end_str:
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            hours_to_resolution = (end_dt - now).total_seconds() / 3600
            if hours_to_resolution < min_hours:
                return None
        except (ValueError, TypeError):
            logger.warning(
                "Unparseable endDate %r for event %s — skipping market",
                end_str,
                event.get("id") or event.get("slug"),
            )
            return None
    else:
        hours_to_resolution = None

    # Extract complete contract support from all Gamma child markets. The
    # executable subset is preserved as an aligned mask, not used as topology.
    try:
        support_topology = build_market_support_topology(event, unit=city.settlement_unit)
    except (BinTopologyError, ValueError, TypeError) as exc:
        logger.warning(
            "Rejecting Gamma market with invalid support topology: city=%s event=%s reason=%s",
            city.name,
            event.get("id") or event.get("slug"),
            exc,
        )
        return None
    outcomes = support_topology.support_outcomes
    if not outcomes or not support_topology.executable_outcomes:
        return None

    metric_surfaces = [
        event.get("title", ""),
        event.get("slug", ""),
        event.get("description", ""),
        event.get("groupItemTitle", ""),
        event.get("group_item_title", ""),
    ]
    for market in event.get("markets", []) or []:
        metric_surfaces.extend(
            [
                market.get("question", ""),
                market.get("title", ""),
                market.get("description", ""),
                market.get("groupItemTitle", ""),
                market.get("group_item_title", ""),
            ]
        )
    temperature_metric = infer_temperature_metric(*metric_surfaces)

    # Compute hours since market opened
    created_str = event.get("createdAt") or event.get("created_at")
    hours_since_open = 24.0
    if created_str:
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            hours_since_open = (now - created).total_seconds() / 3600
        except (ValueError, TypeError):
            pass

    # 2026-05-01: surface the deduped list of executable condition_ids on the
    # event dict so callers (e.g. user-channel WS auto-derive in src/main.py)
    # can subscribe to exactly the markets the scanner has accepted, without
    # re-walking outcomes / re-applying the executable-mask. Non-executable
    # children are excluded — they cannot accept orders and the WS server
    # will reject the subscription.
    executable_condition_ids = _dedupe_condition_ids(
        outcome.get("condition_id")
        for outcome in support_topology.executable_outcomes
    )
    return {
        "event_id": event.get("id") or event.get("slug"),
        "slug": event.get("slug", ""),
        "title": event.get("title", ""),
        "city": city,
        "target_date": target_date,
        "temperature_metric": temperature_metric,
        "hours_to_resolution": hours_to_resolution,
        "hours_since_open": hours_since_open,
        # P2 (PLAN_v3 §6.P2 stage 3 critic R3 ATTACK 8 fix, 2026-05-04):
        # surface Polymarket startDate / endDate verbatim onto the parent
        # market dict so ``market_phase_from_market_dict`` consumes the
        # explicit Gamma timestamps instead of always falling through to
        # the F1 12:00-UTC fallback. F1 is verified across 13 cities
        # (INVESTIGATION_EXTERNAL Q3 = 7 + CRITIC_REVIEW_R2 spot-check
        # = 6) but the design intent is "fallback when Gamma omits",
        # not "only path".
        "market_start_at": event.get("startDate") or event.get("start_date"),
        "market_end_at": event.get("endDate") or event.get("end_date"),
        "outcomes": outcomes,
        "condition_ids": executable_condition_ids,
        "support_topology": {
            "topology_status": support_topology.topology_status,
            "support_child_count": len(support_topology.support_outcomes),
            "executable_child_count": len(support_topology.executable_outcomes),
            "executable_mask": list(support_topology.executable_mask),
            "token_payload_by_support_index": support_topology.token_payload_by_support_index,
            "support_labels": [b.label for b in support_topology.support_bins],
            "support_bounds": [
                {"low": b.low, "high": b.high, "unit": b.unit}
                for b in support_topology.support_bins
            ],
            "provenance": support_topology.provenance,
        },
        "resolution_source": source_contract.resolution_sources[0]
        if source_contract.resolution_sources
        else "",
        "resolution_sources": list(source_contract.resolution_sources),
        "source_contract": source_contract.as_dict(),
    }


def _match_city(title: str, slug: str) -> Optional[City]:
    """Match event title/slug to a configured city using aliases from cities.json."""
    text = f"{title} {slug}".lower()
    slug_text = slug.lower()

    # Use boundary-aware aliases. Short aliases such as "LA" and "SF" must not
    # match inside longer city names like "Kuala Lumpur" or unrelated words.
    candidates: list[tuple[str, City, str]] = []
    for city in runtime_config.runtime_cities():
        candidates.extend((alias.lower(), city, "text") for alias in city.aliases)
        candidates.extend((slug_name.lower(), city, "slug") for slug_name in city.slug_names)

    for alias, city, surface in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        haystack = slug_text if surface == "slug" else text
        pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
        if re.search(pattern, haystack):
            return city

    return None


def _city_match_tokens(city: City) -> set[str]:
    tokens = {
        city.name,
        city.wu_station,
        city.airport_name,
        city.settlement_source,
        *city.aliases,
        *city.slug_names,
    }
    return {str(token).strip().lower() for token in tokens if str(token).strip()}


def _token_in_text(token: str, text: str) -> bool:
    if not token:
        return False
    normalized = token.lower()
    if "/" in normalized or "." in normalized:
        return normalized in text
    if "-" in normalized:
        return normalized in text or normalized.replace("-", " ") in text
    pattern = rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _market_city_sanity_rejection(event: dict, matched_city: City) -> str | None:
    """Reject Gamma events that explicitly identify a different configured city."""
    text_fields = [
        event.get("title", ""),
        event.get("slug", ""),
        event.get("description", ""),
        event.get("resolutionSource", ""),
        event.get("resolution_source", ""),
        event.get("groupItemTitle", ""),
        event.get("group_item_title", ""),
    ]
    for market in event.get("markets", []) or []:
        text_fields.extend([
            market.get("question", ""),
            market.get("slug", ""),
            market.get("description", ""),
            market.get("resolutionSource", ""),
            market.get("resolution_source", ""),
            market.get("groupItemTitle", ""),
            market.get("group_item_title", ""),
        ])
    combined = " ".join(str(field) for field in text_fields if field).lower()
    if not combined:
        return None

    matched_tokens = _city_match_tokens(matched_city)
    for city in runtime_config.runtime_cities():
        if city.name == matched_city.name:
            continue
        for token in sorted(_city_match_tokens(city), key=len, reverse=True):
            if token in matched_tokens:
                continue
            if _token_in_text(token, combined):
                return f"matched {matched_city.name} but text references {city.name} via {token!r}"
    return None


def _dedupe_resolution_sources(values: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        identity = normalized.lower()
        if identity not in seen:
            seen.add(identity)
            deduped.append(normalized)
    return tuple(deduped)


def _collect_structured_resolution_sources(event: dict) -> tuple[str, ...]:
    """Collect structured settlement source fields from a Gamma event payload."""
    values: list[str] = []
    source_keys = (
        "resolutionSource",
        "resolution_source",
        "resolutionSourceUrl",
        "resolution_source_url",
    )

    def add_value(value) -> None:
        if value is None:
            return
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                values.append(stripped)
            return
        if isinstance(value, dict):
            for key in ("url", "href", "source", "name", "title", "label"):
                add_value(value.get(key))
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                add_value(item)

    for key in source_keys:
        add_value(event.get(key))
    for market in event.get("markets", []) or []:
        for key in source_keys:
            add_value(market.get(key))

    return _dedupe_resolution_sources(values)


def _description_source_text_fields(event: dict) -> list[str]:
    text_fields = [
        event.get("description", ""),
        event.get("title", ""),
        event.get("slug", ""),
        event.get("groupItemTitle", ""),
        event.get("group_item_title", ""),
    ]
    for market in event.get("markets", []) or []:
        text_fields.extend(
            [
                market.get("description", ""),
                market.get("question", ""),
                market.get("slug", ""),
                market.get("groupItemTitle", ""),
                market.get("group_item_title", ""),
            ]
        )
    return [str(field) for field in text_fields if str(field or "").strip()]


def _collect_description_resolution_sources(event: dict) -> tuple[str, ...]:
    """Extract settlement-source proof from current market prose when Gamma's
    structured source fields are blank.

    This is deliberately narrower than arbitrary text inference: unsupported
    URLs are ignored here, and explicit structured source fields still win.
    """
    values: list[str] = []
    combined_text = "\n".join(_description_source_text_fields(event))
    for match in _SOURCE_URL_RE.finditer(combined_text):
        source = match.group(0).rstrip(".,;:")
        if _infer_source_family(source) is not None:
            values.append(source)
    if re.search(
        r"(?<![a-z0-9])hong kong observatory(?![a-z0-9])",
        combined_text,
        re.IGNORECASE,
    ):
        values.append("Hong Kong Observatory")
    return _dedupe_resolution_sources(values)


def _collect_resolution_sources(event: dict) -> tuple[str, ...]:
    """Collect settlement-source proof from Gamma.

    Structured ``resolutionSource`` fields are authoritative when present. If
    Gamma omits those fields, fall back to the current market description text,
    which Polymarket uses as the public settlement contract surface.
    """
    structured_sources = _collect_structured_resolution_sources(event)
    if structured_sources:
        return structured_sources
    return _collect_description_resolution_sources(event)


def _infer_source_family(source: str) -> str | None:
    text = source.lower()
    if "weather.gov.hk" in text or "hko.gov.hk" in text or "hong kong observatory" in text:
        return "hko"
    if "wunderground.com" in text or "weather underground" in text or "wunderground" in text:
        return "wu_icao"
    if "weather.gov/wrh/timeseries" in text or "api.weather.gov" in text:
        return "noaa"
    if "cwa.gov.tw" in text or "cwb.gov.tw" in text or "central weather administration" in text:
        return "cwa_station"
    if re.search(r"(?<![a-z0-9])noaa(?![a-z0-9])", text):
        return "noaa"
    return None


def _is_url_like_source(source: str) -> bool:
    text = source.lower()
    return "://" in text or text.startswith("www.") or re.search(r"\.[a-z]{2,}(/|$)", text) is not None


def _configured_station_id(city: City) -> str | None:
    station = city.wu_station
    if station is None:
        return None
    station = str(station).strip()
    return station.upper() if station else None


def _extract_station_id(source: str, city: City) -> str | None:
    text = source.strip()
    m = re.search(
        r"wunderground\.com/history/(?:daily|weekly|monthly)/[^?#\s]+/([A-Za-z0-9]{3,6})(?:[/?#\s]|$)",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).upper()

    m = re.search(r"[?&]site=([A-Za-z0-9]{3,6})(?:[&#\s]|$)", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()

    expected = _configured_station_id(city)
    if expected and _token_in_text(expected.lower(), text.lower()):
        return expected
    return None


def _check_source_contract(event: dict, city: City) -> SourceContractCheck:
    """Compare Gamma resolutionSource metadata against configured settlement source."""
    structured_sources = _collect_structured_resolution_sources(event)
    sources = structured_sources or _collect_description_resolution_sources(event)
    source_label = "resolutionSource" if structured_sources else "market description"
    expected_family = city.settlement_source_type or "wu_icao"
    expected_station = _configured_station_id(city)

    if not sources:
        return SourceContractCheck(
            status="MISSING",
            reason="Gamma payload has no resolutionSource field or supported description source proof",
            resolution_sources=(),
            source_family=None,
            station_id=None,
            configured_source_family=expected_family,
            configured_station_id=expected_station,
        )

    families: set[str] = set()
    stations: set[str] = set()
    unsupported: list[str] = []

    for source in sources:
        family = _infer_source_family(source)
        station = _extract_station_id(source, city)
        if _is_url_like_source(source) and family is None:
            unsupported.append(source)
            continue
        if family is None and station == expected_station:
            family = expected_family
        if family is not None:
            families.add(family)
        if station is not None:
            stations.add(station)

    if unsupported:
        return SourceContractCheck(
            status="UNSUPPORTED",
            reason="resolutionSource URL domain is not a supported settlement source",
            resolution_sources=sources,
            source_family=None,
            station_id=None,
            configured_source_family=expected_family,
            configured_station_id=expected_station,
        )
    if len(families) > 1:
        return SourceContractCheck(
            status="AMBIGUOUS",
            reason=f"multiple settlement source families observed: {sorted(families)}",
            resolution_sources=sources,
            source_family=None,
            station_id=next(iter(stations)) if len(stations) == 1 else None,
            configured_source_family=expected_family,
            configured_station_id=expected_station,
        )
    if len(stations) > 1:
        return SourceContractCheck(
            status="AMBIGUOUS",
            reason=f"multiple settlement stations observed: {sorted(stations)}",
            resolution_sources=sources,
            source_family=next(iter(families)) if len(families) == 1 else None,
            station_id=None,
            configured_source_family=expected_family,
            configured_station_id=expected_station,
        )

    source_family = next(iter(families)) if families else None
    station_id = next(iter(stations)) if stations else None
    if source_family is not None and source_family != expected_family:
        return SourceContractCheck(
            status="MISMATCH",
            reason=f"source family {source_family!r} != configured {expected_family!r}",
            resolution_sources=sources,
            source_family=source_family,
            station_id=station_id,
            configured_source_family=expected_family,
            configured_station_id=expected_station,
        )
    if expected_station and source_family is not None and station_id is None:
        return SourceContractCheck(
            status="UNSUPPORTED",
            reason="resolutionSource does not prove the configured settlement station",
            resolution_sources=sources,
            source_family=source_family,
            station_id=None,
            configured_source_family=expected_family,
            configured_station_id=expected_station,
        )
    if expected_station and station_id and station_id != expected_station:
        return SourceContractCheck(
            status="MISMATCH",
            reason=f"station {station_id!r} != configured {expected_station!r}",
            resolution_sources=sources,
            source_family=source_family,
            station_id=station_id,
            configured_source_family=expected_family,
            configured_station_id=expected_station,
        )
    if source_family is None and station_id is None:
        return SourceContractCheck(
            status="UNSUPPORTED",
            reason="resolutionSource has no supported provider or configured station proof",
            resolution_sources=sources,
            source_family=None,
            station_id=None,
            configured_source_family=expected_family,
            configured_station_id=expected_station,
        )

    return SourceContractCheck(
        status="MATCH",
        reason=f"{source_label} matches configured settlement source contract",
        resolution_sources=sources,
        source_family=source_family or expected_family,
        station_id=station_id,
        configured_source_family=expected_family,
        configured_station_id=expected_station,
    )


def _parse_target_date(event: dict, city: Optional["City"] = None) -> Optional[str]:
    """Extract target date from event slug or end date. Using city timezone if available."""
    slug = event.get("slug", "")

    # Try slug pattern: highest-temperature-in-{city}-on-{month}-{day}-{year}
    m = re.search(r"on-(\w+)-(\d+)-(\d{4})", slug)
    if m:
        month_name, day, year = m.group(1), m.group(2), m.group(3)
        try:
            from datetime import datetime as dt
            parsed = dt.strptime(f"{month_name} {day} {year}", "%B %d %Y")
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Fallback: use end date and city timezone
    end_str = event.get("endDate") or event.get("end_date")
    if end_str:
        try:
            if city and city.timezone:
                import pytz
                from datetime import datetime as dt
                end_dt = dt.fromisoformat(end_str.replace("Z", "+00:00"))
                tz = pytz.timezone(city.timezone)
                return end_dt.astimezone(tz).strftime("%Y-%m-%d")
            return end_str[:10]  # YYYY-MM-DD
        except (IndexError, TypeError, ValueError):
            pass

    return None


def _extract_outcomes(event: dict) -> list[dict]:
    """Extract all parseable bin outcomes from event markets.

    Contract support and executable surface are deliberately separate here.
    Closed/non-accepting child markets can still define the settlement
    partition, but they cannot provide executable token payloads downstream.
    """
    outcomes = []
    markets = event.get("markets", [])

    for market in markets:
        question = market.get("question", "")
        range_low, range_high = _parse_temp_range(question)
        child_is_tradable = _market_child_is_tradable(market)

        # Parse token IDs — may be JSON string or list
        clob_tokens = market.get("clobTokenIds", "[]")
        if isinstance(clob_tokens, str):
            try:
                clob_tokens = json.loads(clob_tokens)
            except (json.JSONDecodeError, TypeError):
                clob_tokens = []

        yes_token = clob_tokens[0] if len(clob_tokens) >= 1 else ""
        no_token = clob_tokens[1] if len(clob_tokens) >= 2 else ""
        token_map_valid = bool(yes_token and no_token)

        # K1/#43: Validate token→outcome label mapping instead of assuming
        # positional order.  Polymarket markets carry an "outcomes" list
        # (e.g. ["Yes", "No"]) whose indices correspond to clobTokenIds.
        outcome_labels = market.get("outcomes", "[]")
        if isinstance(outcome_labels, str):
            try:
                outcome_labels = json.loads(outcome_labels)
            except (json.JSONDecodeError, TypeError):
                outcome_labels = []
        if len(outcome_labels) >= 2:
            label_0 = str(outcome_labels[0]).strip().lower()
            label_1 = str(outcome_labels[1]).strip().lower()
            if label_0 == "no" and label_1 == "yes":
                # Tokens are reversed vs our assumption — swap.
                yes_token, no_token = no_token, yes_token
                _labels_swapped = True
            elif label_0 != "yes" or label_1 != "no":
                # Unrecognised outcome labels — support may still parse, but
                # executable token routing is not proven.
                token_map_valid = False
                _labels_swapped = False
            else:
                _labels_swapped = False
        else:
            _labels_swapped = False

        # Parse prices — may be JSON string or list
        prices = market.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except (json.JSONDecodeError, TypeError):
                logger.warning("outcomePrices parse failed for market %s, skipping",
                               market.get("questionID", "?"))
                prices = []
        if len(prices) < 2:
            logger.warning("outcomePrices has < 2 elements for market %s, skipping",
                           market.get("questionID", "?"))
            yes_price = None
            no_price = None
        else:
            try:
                yes_price = float(prices[0])
                no_price = float(prices[1])
            except (TypeError, ValueError):
                yes_price = None
                no_price = None
            if _labels_swapped:
                yes_price, no_price = no_price, yes_price

        condition_id = str(market.get("conditionId") or market.get("condition_id") or market.get("id", "") or "")
        question_id = str(market.get("questionID") or market.get("question_id") or "")
        gamma_market_id = str(market.get("id") or condition_id)
        executable = bool(
            child_is_tradable
            and token_map_valid
            and condition_id
            and yes_token
            and no_token
        )

        outcomes.append({
            "title": question,
            "token_id": yes_token,
            "no_token_id": no_token,
            "price": yes_price,
            "no_price": no_price,
            "range_low": range_low,
            "range_high": range_high,
            "market_id": condition_id,
            "condition_id": condition_id,
            "question_id": question_id,
            "gamma_market_id": gamma_market_id,
            "executable": executable,
            "active": _boolish_market_field(market, "active", "isActive"),
            "closed": _boolish_market_field(market, "closed", "isClosed"),
            "accepting_orders": _boolish_market_field(market, "acceptingOrders", "accepting_orders"),
            "enable_orderbook": _boolish_market_field(
                market,
                "enableOrderBook",
                "enable_orderbook",
                "orderbookEnabled",
            ),
            "rfqe": _boolish_market_field(market, "rfqe", "rfqEnabled", "rfq_enabled"),
            "market_start_at": _first_nonempty(
                market,
                event,
                "startDate",
                "start_date",
                "marketStartTime",
            ),
            "market_end_at": _first_nonempty(market, event, "endDate", "end_date"),
            "market_close_at": _first_nonempty(
                market,
                event,
                "closeDate",
                "close_date",
                "endDate",
                "end_date",
            ),
            "sports_start_at": _first_nonempty(
                market,
                event,
                "sportsStartTime",
                "sports_start_time",
            ),
            "token_map_raw": {
                "clobTokenIds": clob_tokens,
                "outcomes": outcome_labels,
                "labels_swapped": _labels_swapped,
                "token_map_valid": token_map_valid,
            },
            "raw_gamma_payload_hash": _sha256_json(market),
            "gamma_market_raw": market,
        })

    return outcomes


def build_market_support_topology(event: dict, *, unit: str) -> MarketSupportTopology:
    """Build the complete contract support topology for a Gamma event."""

    support_outcomes: list[dict] = []
    support_bins: list[Bin] = []
    executable_mask: list[bool] = []
    token_payload_by_support_index: dict[int, dict] = {}

    for outcome in _extract_outcomes(event):
        low, high = outcome.get("range_low"), outcome.get("range_high")
        if low is None and high is None:
            continue
        support_index = len(support_bins)
        support_outcome = dict(outcome)
        support_outcome["support_index"] = support_index
        support_outcomes.append(support_outcome)
        support_bins.append(Bin(low=low, high=high, label=outcome["title"], unit=unit))
        executable = bool(outcome.get("executable"))
        executable_mask.append(executable)
        if executable:
            token_payload_by_support_index[support_index] = {
                "token_id": outcome["token_id"],
                "no_token_id": outcome["no_token_id"],
                "market_id": outcome["market_id"],
                "condition_id": outcome.get("condition_id") or outcome.get("market_id"),
                "question_id": outcome.get("question_id", ""),
            }

    validate_bin_topology(support_bins)
    executable_outcomes = [
        outcome for outcome, executable in zip(support_outcomes, executable_mask) if executable
    ]
    return MarketSupportTopology(
        support_bins=support_bins,
        executable_mask=tuple(executable_mask),
        token_payload_by_support_index=token_payload_by_support_index,
        support_outcomes=support_outcomes,
        executable_outcomes=executable_outcomes,
        topology_status="complete",
        provenance={
            "event_id": event.get("id") or event.get("slug"),
            "support_child_count": len(support_outcomes),
            "executable_child_count": len(executable_outcomes),
        },
    )


def _market_child_is_tradable(market: dict) -> bool:
    """Return whether a Gamma child market is currently tradable.

    Gamma can return open parent events with closed or non-accepting children.
    Executability is an explicit child-market fact: missing active/orderbook/
    accepting flags are unknown, not tradable.
    """

    closed = _boolish_market_field(market, "closed", "isClosed")
    active = _boolish_market_field(market, "active", "isActive")
    accepting = _boolish_market_field(market, "acceptingOrders", "accepting_orders")
    orderbook = _boolish_market_field(market, "enableOrderBook", "enable_orderbook", "orderbookEnabled")

    return closed is False and active is True and accepting is True and orderbook is True


def _boolish_market_field(market: dict, *names: str) -> bool | None:
    for name in names:
        if name not in market:
            continue
        value = market.get(name)
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
            continue
        if isinstance(value, (int, float)):
            return bool(value)
    return None


class ExecutableSnapshotCaptureError(RuntimeError):
    """Raised when Gamma/CLOB facts cannot prove executable market identity."""


def capture_executable_market_snapshot(
    conn,
    *,
    market: dict,
    decision: Any,
    clob: Any,
    captured_at: datetime,
    scan_authority: str,
) -> dict[str, str | bool]:
    """Capture and persist an entry-only executable market snapshot.

    This is deliberately post-decision: the selected YES/NO token is known, so
    the stored orderbook hash and top-of-book facts describe the token that the
    executor will actually submit against.
    """

    if str(scan_authority or "").strip().upper() != "VERIFIED":
        raise ExecutableSnapshotCaptureError(
            f"executable snapshot requires VERIFIED Gamma authority, got {scan_authority!r}"
        )
    if clob is None:
        raise ExecutableSnapshotCaptureError("executable snapshot capture requires a CLOB client")

    tokens = dict(getattr(decision, "tokens", {}) or {})
    if not tokens:
        raise ExecutableSnapshotCaptureError("decision tokens are missing")
    outcome = _find_decision_outcome(market, tokens)
    if outcome is None:
        raise ExecutableSnapshotCaptureError("decision tokens do not match a scanned Gamma child market")

    yes_token = str(outcome.get("token_id") or tokens.get("token_id") or "")
    no_token = str(outcome.get("no_token_id") or tokens.get("no_token_id") or "")
    condition_id = str(outcome.get("condition_id") or outcome.get("market_id") or tokens.get("market_id") or "")
    question_id = str(outcome.get("question_id") or "")
    if not yes_token or not no_token or not condition_id or not question_id:
        raise ExecutableSnapshotCaptureError(
            "Gamma child market is missing condition_id/question_id/yes/no token facts"
        )

    direction = str(getattr(getattr(decision, "edge", None), "direction", "") or "").lower()
    if direction == "buy_no":
        selected_token = no_token
        outcome_label = "NO"
    elif direction == "buy_yes":
        selected_token = yes_token
        outcome_label = "YES"
    else:
        raise ExecutableSnapshotCaptureError(f"unsupported entry direction for snapshot capture: {direction!r}")

    gamma_market_raw = outcome.get("gamma_market_raw")
    if not isinstance(gamma_market_raw, dict):
        gamma_market_raw = _minimal_gamma_payload(market, outcome)

    active = _required_bool_fact((outcome, gamma_market_raw), ("active", "isActive"))
    closed = _required_bool_fact((outcome, gamma_market_raw), ("closed", "isClosed"))
    enable_orderbook = _required_bool_fact(
        (outcome, gamma_market_raw),
        ("enable_orderbook", "enableOrderBook", "orderbookEnabled"),
    )
    accepting_orders = _boolish_market_field(outcome, "accepting_orders", "acceptingOrders")
    if accepting_orders is None:
        accepting_orders = _boolish_market_field(gamma_market_raw, "acceptingOrders", "accepting_orders")
    if closed or not active or not enable_orderbook or accepting_orders is not True:
        raise ExecutableSnapshotCaptureError("Gamma child market is not currently tradable")

    raw_clob_market = _fetch_clob_market_info(clob, condition_id)
    raw_orderbook = _fetch_orderbook_snapshot(clob, selected_token)
    fee_details = _fetch_fee_details(clob, selected_token)
    _assert_clob_identity(
        raw_clob_market=raw_clob_market,
        raw_orderbook=raw_orderbook,
        condition_id=condition_id,
        selected_token=selected_token,
        yes_token=yes_token,
        no_token=no_token,
    )

    min_tick_size = _required_decimal_fact(
        (raw_orderbook, raw_clob_market),
        ("tick_size", "min_tick_size", "minimum_tick_size", "minTickSize"),
    )
    min_order_size = _required_decimal_fact(
        (raw_orderbook, raw_clob_market),
        ("min_order_size", "minimum_order_size", "minOrderSize"),
    )
    neg_risk = _required_bool_fact(
        (raw_orderbook, raw_clob_market),
        ("neg_risk", "negRisk", "negative_risk"),
    )
    top_bid = _top_book_decimal(raw_orderbook, "bids")
    top_ask = _top_book_decimal(raw_orderbook, "asks")
    if top_bid <= 0 or top_ask <= 0:
        raise ExecutableSnapshotCaptureError("CLOB top-of-book prices must be positive")
    if top_bid >= top_ask:
        raise ExecutableSnapshotCaptureError("CLOB orderbook is crossed")

    captured = _utc_datetime(captured_at, field_name="captured_at")
    snapshot = ExecutableMarketSnapshotV2(
        snapshot_id=_snapshot_id(
            condition_id=condition_id,
            selected_token=selected_token,
            captured_at=captured,
            raw_gamma_hash=str(outcome.get("raw_gamma_payload_hash") or _sha256_json(gamma_market_raw)),
            raw_clob_hash=_sha256_json(raw_clob_market),
            raw_orderbook_hash=_sha256_json(raw_orderbook),
        ),
        gamma_market_id=str(outcome.get("gamma_market_id") or gamma_market_raw.get("id") or condition_id),
        event_id=str(market.get("event_id") or market.get("id") or ""),
        event_slug=str(market.get("slug") or ""),
        condition_id=condition_id,
        question_id=question_id,
        yes_token_id=yes_token,
        no_token_id=no_token,
        selected_outcome_token_id=selected_token,
        outcome_label=outcome_label,
        enable_orderbook=enable_orderbook,
        active=active,
        closed=closed,
        accepting_orders=accepting_orders,
        market_start_at=_datetime_fact(outcome, "market_start_at"),
        market_end_at=_datetime_fact(outcome, "market_end_at"),
        market_close_at=_datetime_fact(outcome, "market_close_at"),
        sports_start_at=_datetime_fact(outcome, "sports_start_at"),
        min_tick_size=min_tick_size,
        min_order_size=min_order_size,
        fee_details=fee_details,
        token_map_raw=dict(outcome.get("token_map_raw") or {"YES": yes_token, "NO": no_token}),
        rfqe=_boolish_market_field(outcome, "rfqe"),
        neg_risk=neg_risk,
        orderbook_top_bid=top_bid,
        orderbook_top_ask=top_ask,
        orderbook_depth_jsonb=_canonical_json(raw_orderbook),
        raw_gamma_payload_hash=str(outcome.get("raw_gamma_payload_hash") or _sha256_json(gamma_market_raw)),
        raw_clob_market_info_hash=_sha256_json(raw_clob_market),
        raw_orderbook_hash=_sha256_json(raw_orderbook),
        authority_tier="CLOB",
        captured_at=captured,
        freshness_deadline=captured + FRESHNESS_WINDOW_DEFAULT,
    )
    insert_snapshot(conn, snapshot)
    return {
        "executable_snapshot_id": snapshot.snapshot_id,
        "executable_snapshot_min_tick_size": str(snapshot.min_tick_size),
        "executable_snapshot_min_order_size": str(snapshot.min_order_size),
        "executable_snapshot_neg_risk": snapshot.neg_risk,
    }


def _find_decision_outcome(market: dict, tokens: dict) -> dict | None:
    token_values = {
        str(value)
        for value in (
            tokens.get("market_id"),
            tokens.get("token_id"),
            tokens.get("no_token_id"),
        )
        if value not in (None, "")
    }
    for outcome in market.get("outcomes", []) or []:
        if not isinstance(outcome, dict):
            continue
        fields = {
            str(value)
            for value in (
                outcome.get("market_id"),
                outcome.get("condition_id"),
                outcome.get("token_id"),
                outcome.get("no_token_id"),
            )
            if value not in (None, "")
        }
        if token_values & fields:
            return outcome
    return None


def _fetch_clob_market_info(clob: Any, condition_id: str) -> dict:
    getter = getattr(clob, "get_clob_market_info", None)
    if not callable(getter):
        raise ExecutableSnapshotCaptureError("CLOB client lacks get_clob_market_info")
    raw = getter(condition_id)
    raw = getattr(raw, "raw", raw)
    if not isinstance(raw, dict) or not raw:
        raise ExecutableSnapshotCaptureError("CLOB market info response is empty or non-object")
    return dict(raw)


def _fetch_orderbook_snapshot(clob: Any, token_id: str) -> dict:
    getter = getattr(clob, "get_orderbook_snapshot", None)
    if not callable(getter):
        getter = getattr(clob, "get_orderbook", None)
    if not callable(getter):
        raise ExecutableSnapshotCaptureError("CLOB client lacks orderbook snapshot fetch")
    raw = getter(token_id)
    if not isinstance(raw, dict) or not raw:
        raise ExecutableSnapshotCaptureError("CLOB orderbook response is empty or non-object")
    return dict(raw)


def _fetch_fee_details(clob: Any, token_id: str) -> dict[str, Any]:
    details_getter = getattr(clob, "get_fee_rate_details", None)
    if callable(details_getter):
        try:
            return canonicalize_fee_details(
                details_getter(token_id),
                source="clob_fee_rate",
                token_id=token_id,
            )
        except MarketSnapshotMismatchError as exc:
            raise ExecutableSnapshotCaptureError("CLOB fee-rate response has invalid units") from exc
        except Exception as exc:
            raise ExecutableSnapshotCaptureError(f"CLOB fee-rate fetch failed: {exc}") from exc

    getter = getattr(clob, "get_fee_rate", None)
    if not callable(getter):
        raise ExecutableSnapshotCaptureError("CLOB client lacks fee-rate fetch")
    try:
        return canonicalize_legacy_fee_rate_value(
            getter(token_id),
            source="clob_fee_rate",
            token_id=token_id,
        )
    except MarketSnapshotMismatchError as exc:
        raise ExecutableSnapshotCaptureError("CLOB fee-rate response is not numeric") from exc
    except Exception as exc:
        raise ExecutableSnapshotCaptureError(f"CLOB fee-rate fetch failed: {exc}") from exc


def _assert_clob_identity(
    *,
    raw_clob_market: dict,
    raw_orderbook: dict,
    condition_id: str,
    selected_token: str,
    yes_token: str,
    no_token: str,
) -> None:
    clob_condition = _first_field(
        raw_clob_market,
        "condition_id",
        "conditionId",
        "conditionID",
        "market",
    )
    if clob_condition is not None and str(clob_condition) != str(condition_id):
        raise ExecutableSnapshotCaptureError("CLOB market condition_id does not match Gamma child")

    book_asset = _first_field(raw_orderbook, "asset_id", "assetId", "token_id", "tokenId")
    if book_asset is not None and str(book_asset) != str(selected_token):
        raise ExecutableSnapshotCaptureError("CLOB orderbook token_id does not match selected outcome token")

    clob_tokens = _market_token_strings_from_payload(raw_clob_market)
    if not clob_tokens:
        raise ExecutableSnapshotCaptureError("CLOB market token map is missing")
    if {str(yes_token), str(no_token)} - clob_tokens:
        raise ExecutableSnapshotCaptureError("CLOB market token map does not match Gamma child tokens")


def _first_field(surface: dict, *names: str) -> Any:
    for name in names:
        value = surface.get(name)
        if value not in (None, ""):
            return value
    return None


def _market_token_strings_from_payload(payload: Any) -> set[str]:
    tokens: set[str] = set()
    if isinstance(payload, dict):
        for key in ("tokens", "clobTokenIds", "clob_token_ids", "outcomeTokens", "t"):
            value = payload.get(key)
            tokens.update(_market_token_strings_from_payload(value))
        for key in (
            "token_id",
            "tokenId",
            "yes_token_id",
            "no_token_id",
            "yesTokenId",
            "noTokenId",
            "primary_token_id",
            "secondary_token_id",
            "primaryTokenId",
            "secondaryTokenId",
            "t",
        ):
            value = payload.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list, tuple)):
                tokens.add(str(value))
    elif isinstance(payload, str):
        stripped = payload.strip()
        if not stripped:
            return tokens
        if stripped[:1] in "[{":
            try:
                tokens.update(_market_token_strings_from_payload(json.loads(stripped)))
            except json.JSONDecodeError:
                tokens.add(stripped)
        else:
            tokens.add(stripped)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            tokens.update(_market_token_strings_from_payload(item))
    return tokens


def _required_decimal_fact(surfaces: tuple[dict, ...], names: tuple[str, ...]) -> Decimal:
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        for name in names:
            value = surface.get(name)
            if value in (None, ""):
                continue
            try:
                parsed = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise ExecutableSnapshotCaptureError(f"CLOB fact {name} is not decimal") from exc
            if parsed <= 0:
                raise ExecutableSnapshotCaptureError(f"CLOB fact {name} must be positive")
            return parsed
    raise ExecutableSnapshotCaptureError(f"CLOB fact missing: {'/'.join(names)}")


def _required_bool_fact(surfaces: tuple[dict, ...], names: tuple[str, ...]) -> bool:
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        value = _boolish_market_field(surface, *names)
        if value is not None:
            return value
    raise ExecutableSnapshotCaptureError(f"required boolean fact missing: {'/'.join(names)}")


def _book_row_price_size(row: Any, side: str) -> tuple[Decimal, Decimal]:
    if isinstance(row, dict):
        price_value = row.get("price")
        size_value = row.get("size")
    elif isinstance(row, (list, tuple)) and len(row) >= 2:
        price_value = row[0]
        size_value = row[1]
    else:
        price_value = None
        size_value = None
    if price_value in (None, ""):
        raise ExecutableSnapshotCaptureError(f"CLOB orderbook {side} price missing")
    if size_value in (None, ""):
        raise ExecutableSnapshotCaptureError(f"CLOB orderbook {side} size missing")
    try:
        price = Decimal(str(price_value))
        size = Decimal(str(size_value))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutableSnapshotCaptureError(f"CLOB orderbook {side} row is not decimal") from exc
    if not price.is_finite() or not size.is_finite():
        raise ExecutableSnapshotCaptureError(f"CLOB orderbook {side} row is not finite")
    if price <= 0 or price >= 1:
        raise ExecutableSnapshotCaptureError(f"CLOB orderbook {side} price is out of bounds")
    if size <= 0:
        raise ExecutableSnapshotCaptureError(f"CLOB orderbook {side} size must be positive")
    return price, size


def _top_book_level_decimal(orderbook: dict, side: str) -> tuple[Decimal, Decimal]:
    rows = orderbook.get(side)
    if not isinstance(rows, list) or not rows:
        raise ExecutableSnapshotCaptureError(f"CLOB orderbook missing {side}")
    parsed = [_book_row_price_size(row, side) for row in rows]
    if side == "bids":
        best_price = max(price for price, _ in parsed)
    elif side == "asks":
        best_price = min(price for price, _ in parsed)
    else:
        raise ExecutableSnapshotCaptureError(f"unsupported CLOB orderbook side {side!r}")
    best_size = sum((size for price, size in parsed if price == best_price), Decimal("0"))
    return best_price, best_size


def _top_book_decimal(orderbook: dict, side: str) -> Decimal:
    return _top_book_level_decimal(orderbook, side)[0]


def _datetime_fact(surface: dict, name: str) -> datetime | None:
    value = surface.get(name)
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _utc_datetime(value, field_name=name)
    try:
        return _utc_datetime(datetime.fromisoformat(str(value).replace("Z", "+00:00")), field_name=name)
    except ValueError as exc:
        raise ExecutableSnapshotCaptureError(f"Gamma datetime fact {name} is invalid") from exc


def _utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ExecutableSnapshotCaptureError(f"{field_name} must be datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _minimal_gamma_payload(market: dict, outcome: dict) -> dict:
    return {
        "event_id": market.get("event_id") or market.get("id") or "",
        "event_slug": market.get("slug") or "",
        "outcome": {
            key: value
            for key, value in outcome.items()
            if key not in {"gamma_market_raw"}
        },
    }


def _snapshot_id(
    *,
    condition_id: str,
    selected_token: str,
    captured_at: datetime,
    raw_gamma_hash: str,
    raw_clob_hash: str,
    raw_orderbook_hash: str,
) -> str:
    seed = _canonical_json(
        {
            "condition_id": condition_id,
            "selected_token": selected_token,
            "captured_at": captured_at.isoformat(),
            "raw_gamma_hash": raw_gamma_hash,
            "raw_clob_hash": raw_clob_hash,
            "raw_orderbook_hash": raw_orderbook_hash,
            "nonce": uuid.uuid4().hex,
        }
    )
    return "ems2-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]


def _first_nonempty(primary: dict, fallback: dict, *names: str) -> Any:
    for surface in (primary, fallback):
        for name in names:
            value = surface.get(name)
            if value not in (None, ""):
                return value
    return None


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _parse_temp_range(question: str) -> tuple[Optional[float], Optional[float]]:
    """Parse temperature range from market question text.

    Returns (range_low, range_high). None for open-ended.
    """
    q = question.strip()

    # "X-Y°F" or "X-Y °F" or "X–Y°F" (en-dash)
    m = re.search(r"(-?\d+\.?\d*)\s*[-–]\s*(-?\d+\.?\d*)\s*°[FfCc]", q)
    if m:
        return float(m.group(1)), float(m.group(2))

    # "X°F or below" / "X°C or below" / "X°F or lower"
    m = re.search(r"(-?\d+\.?\d*)\s*°[FfCc]\s+or\s+(below|lower)", q)
    if m:
        return None, float(m.group(1))

    # "X°F or higher" / "X°C or higher" / "X°F or above"
    m = re.search(r"(-?\d+\.?\d*)\s*°[FfCc]\s+or\s+(higher|above|more)", q)
    if m:
        return float(m.group(1)), None

    # "X°C" single degree (end-of-string anchored — matches canonical labels
    # like "17°C" produced by _canonical_bin_label).
    m = re.search(r"(-?\d+\.?\d*)\s*°[Cc]$", q)
    if m:
        val = float(m.group(1))
        return val, val

    # "X°F" single degree (end-of-string anchored) — parallel to °C case
    # for P-E / DR-33 canonical Fahrenheit point-bin labels.
    m = re.search(r"(-?\d+\.?\d*)\s*°[Ff]$", q)
    if m:
        val = float(m.group(1))
        return val, val

    # DR-33 / P-D §6.1 Gamma question point-bin form: "... be 17°C on April 15?"
    # — matches X°C/X°F followed by " on " date/etc. Explicitly NOT matching
    # "or higher/lower/below/above/more" fragments (handled by earlier branches
    # which run first). The " on " word-boundary anchor prevents matches on
    # intra-word occurrences.
    m = re.search(r"(-?\d+\.?\d*)\s*°[CcFf]\s+on\b", q)
    if m:
        val = float(m.group(1))
        return val, val

    return None, None


# S2.4 (2026-04-23, data-readiness-tail NH-E1 hardening): STRICT parser for
# canonical bin labels emitted by `src/execution/harvester.py::_canonical_bin_label`.
# Uses `re.fullmatch` so the ENTIRE input must match one of the 4 canonical
# shapes; trailing garbage / prefix garbage / unicode-shoulders are rejected.
#
# Use this for ROUND-TRIP verification (label emitted by writer must survive
# a strict reparse) and for any caller that receives a canonical label from
# within-system serialization. Do NOT use this for free-form Polymarket market
# questions — those need the tolerant `_parse_temp_range` above.
#
# Motivation (NH-E1 / closure-banner rule 15): P-E's critic-opus discovered
# that `re.search` on unanchored patterns silently accepts near-canonical but
# semantically-broken labels (e.g. "17°Cfoo" parses as 17.0 point bin, leaking
# trailing garbage into settlement authority).
_CANONICAL_BIN_LABEL_FULLMATCH = [
    # "X-Y°F" or "X-Y°C" — finite bounded range
    (re.compile(r"(-?\d+)-(-?\d+)°([FfCc])"),
     lambda m: (float(m.group(1)), float(m.group(2)))),
    # "X°F or below" / "X°C or below" — left-shoulder
    (re.compile(r"(-?\d+)°([FfCc])\s+or\s+below"),
     lambda m: (None, float(m.group(1)))),
    # "X°F or higher" / "X°C or higher" — right-shoulder
    (re.compile(r"(-?\d+)°([FfCc])\s+or\s+higher"),
     lambda m: (float(m.group(1)), None)),
    # "X°C" / "X°F" — point bin
    (re.compile(r"(-?\d+)°([FfCc])"),
     lambda m: (float(m.group(1)), float(m.group(1)))),
]


def _parse_canonical_bin_label(label: str) -> Optional[tuple[Optional[float], Optional[float]]]:
    """Strict parser for canonical bin labels.

    Returns (low, high) tuple on exact match against one of 4 canonical shapes
    ("X-Y°F", "X°F or below", "X°F or higher", "X°F"). Returns None if the
    input does NOT fully match any canonical shape — including near-matches
    with trailing/leading garbage, unicode shoulders (≥/≤), or float/non-integer
    degree values.

    This is the NH-E1 antibody companion to `_canonical_bin_label` in
    `src/execution/harvester.py`: every label that function emits MUST
    round-trip through this parser, and no non-canonical label can.
    """
    if not isinstance(label, str):
        return None
    for pattern, extractor in _CANONICAL_BIN_LABEL_FULLMATCH:
        m = pattern.fullmatch(label)
        if m:
            return extractor(m)
    return None
