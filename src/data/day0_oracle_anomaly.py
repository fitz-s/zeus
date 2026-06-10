# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator green-light 2026-06-10 item E (WU-vs-METAR
#   divergence detector, fail-closed family pause); Paris CDG sensor-tampering
#   incident April 2026 (/tmp/weather_source_research.md §5: trader manipulated
#   a sensor, Polymarket switched Paris settlement to Le Bourget).
"""Day0 settlement-oracle anomaly guard: WU vs METAR divergence.

The settlement reference (WU) and the fast lane (aviationweather.gov METAR)
read the SAME physical station. When their running extremes — compared over
the SAME observation window — diverge beyond conversion/rounding noise, one of
them is wrong: sensor tampering, feed injection, station swap, or a data bug
on our side. Every one of those is a reason NOT to trade the family on day0.

Semantics (relationship contract, tested in
tests/test_day0_fast_obs_lane.py::TestOracleAnomaly):
- The METAR extremes are TRUNCATED at WU's last observation time before
  comparison. METAR is fresher; an extreme that moved after WU's last report
  is normal latency, not an anomaly.
- Threshold is strict-> in settlement units and PER-CITY EMPIRICAL where
  measured (config/wu_metar_divergence.json: max(p99 |rounded delta| + 1
  quantum, 1.0) — 1.0 unit for the 21/22 cities whose feeds measured
  byte-identical post-rounding; 2.0 C for Seoul's real spread). Unmeasured
  cities fall back to the conservative pre-measurement defaults
  (1.5 F / 1.0 C). Provenance ('empirical' | 'default_guess') is recorded in
  every verdict detail. See divergence_threshold_for_city.
- Verdict NONE (no comparison) when either side has no samples in the window —
  absence of evidence is not an anomaly, and it must not pause trading.
- A flagged (city, target_date) pauses the day0 ENTRY lane fail-closed:
  src/engine/event_reactor_adapter._live_yes_probabilities raises
  DAY0_ORACLE_ANOMALY_PAUSED for that family's DAY0 events -> deterministic
  no-submit receipt (LIVE_INFERENCE_INPUTS_MISSING:DAY0_ORACLE_ANOMALY_PAUSED).
- The pause is DB-BACKED (world.day0_oracle_anomaly_flags) with the in-process
  registry as a read-through cache, so it SURVIVES daemon restarts (PR#404
  P1); TTL is enforced on read from the durable flagged_at.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

UTC = timezone.utc

#: Conservative DEFAULT divergence thresholds (settlement units) for cities
#: WITHOUT an empirical measurement. Pre-2026-06-10 these (guessed) values
#: applied to every city; measured cities now carry per-city empirical
#: thresholds from config/wu_metar_divergence.json (operator correction
#: 2026-06-10: 30d x 22 cities, same-station timestamp-matched — 21/22
#: byte-identical post-rounding -> threshold 1.0 [tight tamper detector];
#: Seoul RKSI shows REAL +-1C divergence on 4.5% of reports -> threshold 2.0
#: AND settlement_faithful=false).
DIVERGENCE_THRESHOLD = {"F": 1.5, "C": 1.0}

#: How long a flagged family stays paused without re-confirmation.
DEFAULT_PAUSE_TTL_HOURS = 24.0

_DIVERGENCE_MODEL_CACHE: dict[str, dict] = {}


def _divergence_model_path() -> "Path":
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "config" / "wu_metar_divergence.json"


def _load_divergence_model(path: "Optional[Path]" = None) -> dict:
    import json
    from pathlib import Path  # noqa: F401 — typing only

    path_str = str(path if path is not None else _divergence_model_path())
    cached = _DIVERGENCE_MODEL_CACHE.get(path_str)
    if cached is not None:
        return cached
    try:
        with open(path_str, "r", encoding="utf-8") as fh:
            model = json.load(fh)
        if not isinstance(model, dict) or not isinstance(model.get("cities"), dict):
            raise ValueError("wu_metar_divergence.json malformed: missing 'cities'")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "WU_METAR_DIVERGENCE_MODEL_UNAVAILABLE path=%s exc=%s — default thresholds",
            path_str, exc,
        )
        model = {"cities": {}}
    _DIVERGENCE_MODEL_CACHE[path_str] = model
    return model


def divergence_threshold_for_city(
    city_name: str, unit: str, *, path: "Optional[Path]" = None
) -> tuple[float, str]:
    """(threshold, provenance) for the WU-vs-METAR guard.

    Empirical per-city threshold — max(p99(|rounded delta|) + 1 quantum, 1.0),
    measured same-station timestamp-matched over 30d — when present with an
    adequate sample; otherwise the conservative pre-measurement default.
    Provenance is recorded in the guard verdict ('empirical' | 'default_guess').
    """
    entry = _load_divergence_model(path).get("cities", {}).get(str(city_name)) or {}
    threshold = entry.get("empirical_threshold")
    provenance = str(entry.get("threshold_provenance") or "")
    if threshold is not None and provenance == "empirical":
        try:
            value = float(threshold)
            if value > 0.0:
                return value, "empirical"
        except (TypeError, ValueError):
            pass
    return (
        DIVERGENCE_THRESHOLD.get(str(unit).upper(), DIVERGENCE_THRESHOLD["F"]),
        "default_guess",
    )


def city_metar_settlement_faithful(city_name: str, *, path: "Optional[Path]" = None) -> bool:
    """False when the measurement shows the METAR integer is NOT reliably WU's
    settlement integer for this station (Seoul/RKSI class: +-1C disagreement
    on ~4.5% of reports — WU's feed is not the METAR body there). Such a city
    must NOT have METAR drive day0 bin-kill decisions: the fast lane excludes
    it entirely (monotone-safe — absence of fast events never kills a bin).
    Unmeasured cities default to True (the guard threshold still covers them)."""
    entry = _load_divergence_model(path).get("cities", {}).get(str(city_name)) or {}
    verdict = entry.get("settlement_faithful")
    if verdict is None:
        return True
    return bool(verdict)


@dataclass(frozen=True)
class DivergenceVerdict:
    city: str
    target_date: str
    unit: str
    compared: bool
    diverged: bool
    high_delta: Optional[float] = None
    low_delta: Optional[float] = None
    wu_last_obs_time: Optional[str] = None
    metar_samples: int = 0
    detail: str = ""


@dataclass(frozen=True)
class _AnomalyRecord:
    flagged_at: datetime
    detail: str


_REGISTRY: dict[tuple[str, str], _AnomalyRecord] = {}
_REGISTRY_LOCK = threading.Lock()

#: PR#404 P1 (operator): a Paris-CDG-class anomaly is a settlement-authority
#: integrity event for the family, NOT a per-process warning — it must survive
#: a daemon restart (which is exactly when external data/daemons are most
#: likely unstable). The registry is therefore DB-BACKED (world DB) with the
#: in-process dict as a read-through cache. q construction, the hard-fact exit
#: lane, and the resting-order cancel sweep all consult is_day0_family_paused,
#: which falls through to the DB on a memory miss.
_FLAGS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS day0_oracle_anomaly_flags (
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    flagged_at TEXT NOT NULL,
    ttl_hours REAL NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (city, target_date)
)
"""
#: Keys confirmed ABSENT in the DB this process-lifetime (negative cache so the
#: hot paths don't re-query the DB every call). Cleared on flag/clear/reset.
_DB_MISS_CACHE: set[tuple[str, str]] = set()


def _persist_flag(
    city: str, target_date: str, *, flagged_at: datetime, ttl_hours: float,
    detail: str, conn=None,
) -> None:
    """Best-effort durable write (fail-soft: the in-memory pause already holds
    for this process; persistence failure is loud, never blocking)."""
    own = conn is None
    try:
        if own:
            from src.state.db import ZEUS_WORLD_DB_PATH, get_world_connection
            from src.state.db_writer_lock import WriteClass, db_writer_lock

            conn = get_world_connection(write_class=WriteClass.LIVE)
            lock_ctx = db_writer_lock(ZEUS_WORLD_DB_PATH, WriteClass.LIVE)
        else:
            from contextlib import nullcontext

            lock_ctx = nullcontext()
        with lock_ctx:
            conn.execute(_FLAGS_TABLE_DDL)
            conn.execute(
                "INSERT OR REPLACE INTO day0_oracle_anomaly_flags "
                "(city, target_date, flagged_at, ttl_hours, detail) VALUES (?,?,?,?,?)",
                (str(city), str(target_date), flagged_at.isoformat(), float(ttl_hours), str(detail)),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "DAY0_ORACLE_ANOMALY_PERSIST_FAILED city=%s date=%s exc=%s: %s "
            "(pause holds in-process; will NOT survive a restart)",
            city, target_date, type(exc).__name__, exc,
        )
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _load_flag_from_db(city: str, target_date: str, *, conn=None) -> Optional[_AnomalyRecord]:
    own = conn is None
    try:
        if own:
            from src.state.db import get_world_connection_read_only

            conn = get_world_connection_read_only()
        row = conn.execute(
            "SELECT flagged_at, ttl_hours, detail FROM day0_oracle_anomaly_flags "
            "WHERE city = ? AND target_date = ?",
            (str(city), str(target_date)),
        ).fetchone()
        if row is None:
            return None
        flagged_at = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        if flagged_at.tzinfo is None:
            flagged_at = flagged_at.replace(tzinfo=UTC)
        return _AnomalyRecord(flagged_at=flagged_at.astimezone(UTC), detail=str(row[2]))
    except Exception:  # noqa: BLE001 — missing table / locked DB -> no durable flag
        return None
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def flag_day0_oracle_anomaly(
    city: str, target_date: str, *, detail: str,
    now: Optional[datetime] = None,
    ttl_hours: float = DEFAULT_PAUSE_TTL_HOURS,
    conn=None,
) -> None:
    """Pause the day0 lane for (city, target_date). Loud by design; persisted
    to the world DB so the pause SURVIVES daemon restarts (PR#404 P1)."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    with _REGISTRY_LOCK:
        _REGISTRY[(str(city), str(target_date))] = _AnomalyRecord(flagged_at=moment, detail=str(detail))
        _DB_MISS_CACHE.discard((str(city), str(target_date)))
    logger.warning(
        "DAY0_ORACLE_ANOMALY_FLAGGED city=%s target_date=%s detail=%s — day0 entries PAUSED (fail-closed)",
        city, target_date, detail,
    )
    _persist_flag(
        city, target_date, flagged_at=moment, ttl_hours=ttl_hours, detail=detail, conn=conn,
    )


def clear_day0_oracle_anomaly(city: str, target_date: str, *, conn=None) -> bool:
    """Operator/cleanup hook. Returns True when a record was removed (memory
    or durable). Clears BOTH surfaces."""
    key = (str(city), str(target_date))
    with _REGISTRY_LOCK:
        removed = _REGISTRY.pop(key, None) is not None
        _DB_MISS_CACHE.add(key)
    own = conn is None
    try:
        if own:
            from src.state.db import ZEUS_WORLD_DB_PATH, get_world_connection
            from src.state.db_writer_lock import WriteClass, db_writer_lock

            conn = get_world_connection(write_class=WriteClass.LIVE)
            lock_ctx = db_writer_lock(ZEUS_WORLD_DB_PATH, WriteClass.LIVE)
        else:
            from contextlib import nullcontext

            lock_ctx = nullcontext()
        with lock_ctx:
            cur = conn.execute(
                "DELETE FROM day0_oracle_anomaly_flags WHERE city = ? AND target_date = ?",
                key,
            )
            conn.commit()
            removed = removed or (cur.rowcount or 0) > 0
    except Exception:  # noqa: BLE001 — table may not exist yet
        pass
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return removed


def is_day0_family_paused(
    city: str,
    target_date: str,
    *,
    now: Optional[datetime] = None,
    ttl_hours: float = DEFAULT_PAUSE_TTL_HOURS,
    conn=None,
) -> bool:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    key = (str(city), str(target_date))
    with _REGISTRY_LOCK:
        record = _REGISTRY.get(key)
        memory_miss = record is None
        cached_db_miss = key in _DB_MISS_CACHE
    if memory_miss and not cached_db_miss:
        # PR#404 P1: restart resilience — read-through to the durable flags.
        record = _load_flag_from_db(city, target_date, conn=conn)
        with _REGISTRY_LOCK:
            if record is not None:
                _REGISTRY[key] = record
            else:
                _DB_MISS_CACHE.add(key)
    if record is None:
        return False
    if moment - record.flagged_at > timedelta(hours=float(ttl_hours)):
        with _REGISTRY_LOCK:
            _REGISTRY.pop(key, None)
            _DB_MISS_CACHE.add(key)
        return False
    return True


def active_day0_anomalies() -> dict[tuple[str, str], str]:
    """Observability snapshot: {(city, target_date): detail}."""
    with _REGISTRY_LOCK:
        return {key: record.detail for key, record in _REGISTRY.items()}


#: Quarantined-print observability (adversarial review fix 4): counts per
#: (city, target_date). Quarantine is NOT a pause — it excludes single prints
#: pending corroboration; the counter gives the anomaly surface visibility.
_QUARANTINE_COUNTS: dict[tuple[str, str], int] = {}
_QUARANTINE_LOCK = threading.Lock()


def note_metar_quarantine(city: str, target_date: str, *, detail: str) -> None:
    """Record (and loudly log) a quarantined METAR print for observability."""
    key = (str(city), str(target_date))
    with _QUARANTINE_LOCK:
        _QUARANTINE_COUNTS[key] = _QUARANTINE_COUNTS.get(key, 0) + 1
        count = _QUARANTINE_COUNTS[key]
    logger.warning(
        "DAY0_METAR_QUARANTINE city=%s target_date=%s count=%d detail=%s",
        city, target_date, count, detail,
    )


def metar_quarantine_counts() -> dict[tuple[str, str], int]:
    with _QUARANTINE_LOCK:
        return dict(_QUARANTINE_COUNTS)


def _reset_registry_for_tests() -> None:
    with _REGISTRY_LOCK:
        _REGISTRY.clear()
        _DB_MISS_CACHE.clear()
    with _QUARANTINE_LOCK:
        _QUARANTINE_COUNTS.clear()
    with _WU_CHECK_MEMO_LOCK:
        _WU_CHECK_MEMO.clear()
        _WU_CHECK_FAILURE_MEMO.clear()


#: WU live-API anomaly checks are throttled per city (the comparison only
#: needs WU's cadence, not the reactor cycle cadence). SUCCESS and FAILURE
#: carry SEPARATE throttles (PR#404 P1): a WU outage must NOT consume the
#: 10-minute success memo — that silenced the cross-check for the full window
#: exactly while the fast lane kept emitting unvalidated. Failures retry on
#: the short throttle instead.
_WU_CHECK_INTERVAL_S = 600.0
_WU_CHECK_FAILURE_RETRY_S = 120.0
_WU_CHECK_MEMO: dict[str, float] = {}
_WU_CHECK_FAILURE_MEMO: dict[str, float] = {}
_WU_CHECK_MEMO_LOCK = threading.Lock()


def wu_metar_anomaly_check(city: Any, extremes: Any, metar_reports: list) -> None:
    """Throttled WU-vs-METAR divergence check; flags the registry on divergence.

    Signature matches Day0FastObsEmitter.prefetch(anomaly_check=...). Any
    WU-side failure is fail-SOFT for emission (the fast lane keeps running)
    but logged — absence of the cross-check is visibility loss, not an anomaly.
    Only a SUCCESSFUL check arms the 10-min memo; failures arm a short retry
    throttle (PR#404 P1).
    """
    import time as _time

    city_name = str(getattr(city, "name", "") or "")
    target_date = str(getattr(extremes, "target_date", "") or "")
    if not city_name or not target_date:
        return
    now_monotonic = _time.monotonic()
    with _WU_CHECK_MEMO_LOCK:
        last_success = _WU_CHECK_MEMO.get(city_name, 0.0)
        last_failure = _WU_CHECK_FAILURE_MEMO.get(city_name, 0.0)
        if now_monotonic - last_success < _WU_CHECK_INTERVAL_S:
            return
        if now_monotonic - last_failure < _WU_CHECK_FAILURE_RETRY_S:
            return

    from src.data.observation_client import get_current_observation

    try:
        wu_obs = get_current_observation(city, target_date=target_date)
    except Exception as exc:  # noqa: BLE001 — WU side fail-soft, loud
        with _WU_CHECK_MEMO_LOCK:
            _WU_CHECK_FAILURE_MEMO[city_name] = now_monotonic
        logger.warning(
            "DAY0_ORACLE_ANOMALY_WU_SIDE_UNAVAILABLE city=%s date=%s exc=%s: %s "
            "(retry in %ss; success memo NOT consumed)",
            city_name, target_date, type(exc).__name__, exc, _WU_CHECK_FAILURE_RETRY_S,
        )
        return
    with _WU_CHECK_MEMO_LOCK:
        _WU_CHECK_MEMO[city_name] = now_monotonic
        _WU_CHECK_FAILURE_MEMO.pop(city_name, None)
    wu_time_raw = getattr(wu_obs, "observation_time", None)
    try:
        wu_last_obs_time = (
            datetime.fromisoformat(str(wu_time_raw).replace("Z", "+00:00")) if wu_time_raw else None
        )
        if wu_last_obs_time is not None and wu_last_obs_time.tzinfo is None:
            wu_last_obs_time = None
    except (TypeError, ValueError):
        wu_last_obs_time = None
    verdict = check_wu_metar_divergence(
        city=city,
        target_date=target_date,
        metar_reports=metar_reports,
        wu_high_so_far=getattr(wu_obs, "high_so_far", None),
        wu_low_so_far=getattr(wu_obs, "low_so_far", None),
        wu_last_obs_time=wu_last_obs_time,
    )
    if verdict.compared and verdict.diverged:
        flag_day0_oracle_anomaly(city_name, target_date, detail=verdict.detail)


def check_wu_metar_divergence(
    *,
    city: Any,
    target_date: str,
    metar_reports: list,
    wu_high_so_far: Optional[float],
    wu_low_so_far: Optional[float],
    wu_last_obs_time: Optional[datetime],
) -> DivergenceVerdict:
    """Compare WU running extremes against METAR extremes over the SAME window.

    Caller supplies the WU side (from the existing settlement-bound WU obs
    context — high_so_far / low_so_far / observation_time). The METAR side is
    recomputed here truncated at wu_last_obs_time so latency cannot masquerade
    as divergence. Returns a verdict; flagging is the caller's choice (the
    fast-lane wiring flags + pauses on diverged=True).
    """
    from src.data.day0_fast_obs import running_extremes_for_local_day

    city_name = str(getattr(city, "name", "") or "")
    unit = str(getattr(city, "settlement_unit", "F") or "F").upper()
    if wu_last_obs_time is None or (wu_high_so_far is None and wu_low_so_far is None):
        return DivergenceVerdict(
            city=city_name, target_date=str(target_date), unit=unit,
            compared=False, diverged=False, detail="wu_side_unavailable",
        )
    truncated = running_extremes_for_local_day(
        metar_reports, city=city, target_date=target_date, as_of=wu_last_obs_time.astimezone(UTC)
    )
    if truncated.sample_count == 0:
        return DivergenceVerdict(
            city=city_name, target_date=str(target_date), unit=unit,
            compared=False, diverged=False, detail="metar_side_no_overlapping_samples",
        )
    threshold, threshold_provenance = divergence_threshold_for_city(city_name, unit)
    high_delta = (
        abs(float(wu_high_so_far) - float(truncated.high_so_far))
        if wu_high_so_far is not None and truncated.high_so_far is not None
        else None
    )
    low_delta = (
        abs(float(wu_low_so_far) - float(truncated.low_so_far))
        if wu_low_so_far is not None and truncated.low_so_far is not None
        else None
    )
    diverged = any(delta is not None and delta > threshold for delta in (high_delta, low_delta))
    detail = (
        f"unit={unit} threshold={threshold} threshold_provenance={threshold_provenance} "
        f"high_delta={high_delta} low_delta={low_delta} "
        f"wu_last_obs={wu_last_obs_time.isoformat()} metar_samples={truncated.sample_count}"
    )
    return DivergenceVerdict(
        city=city_name, target_date=str(target_date), unit=unit,
        compared=True, diverged=diverged,
        high_delta=high_delta, low_delta=low_delta,
        wu_last_obs_time=wu_last_obs_time.astimezone(UTC).isoformat(),
        metar_samples=truncated.sample_count, detail=detail,
    )
