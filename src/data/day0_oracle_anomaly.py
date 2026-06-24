# Created: 2026-06-10
# Last reused or audited: 2026-06-13
# Authority basis: operator green-light 2026-06-10 item E (WU-vs-METAR
#   divergence detector, fail-closed family pause); Paris CDG sensor-tampering
#   incident April 2026 (/tmp/weather_source_research.md §5: trader manipulated
#   a sensor, Polymarket switched Paris settlement to Le Bourget).
#   + 2026-06-13 WU-SIDE COVERAGE GATE (symmetric twin of the existing
#   METAR-side coverage gate): the per-city threshold was measured on
#   timestamp-MATCHED same-station readings, but the runtime compares each
#   feed's RUNNING EXTREME; when WU's live timeseries window never observed the
#   local-day extreme (first sample after the coverage grace window), its
#   running extreme is set by a different sample than METAR's -> a 2-9 unit
#   coverage gap that is NOT tampering -> 174 day0 families false-paused
#   (171/174 moved exactly ONE extreme = coverage; 3 moved both = real
#   tamper). The detector now refuses to conclude when WU coverage_status != OK.
#   Authority: docs/evidence/day0_oracle_false_pause_2026-06-13/diagnosis.md.
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

#: METAR-vs-WU coverage tolerance (PR#404 round-2 P0-2B): the METAR window
#: must reach WU's last obs time to within one report-matching tolerance
#: (mirrors the 6-min nearest-report tolerance in the divergence measurement)
#: before a divergence verdict may be concluded.
_METAR_WU_COVERAGE_TOLERANCE_S = 360.0

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
    # PR#404 round-2 P1-A: the TTL travels WITH the record (persisted in the
    # durable row), so a custom flag TTL survives restart and the pause check
    # honors the flag-time TTL — never the reader's call-site default.
    ttl_hours: float = DEFAULT_PAUSE_TTL_HOURS


@dataclass(frozen=True)
class Day0OracleAnomalyAction:
    action: str
    city: str
    target_date: str
    detail: str
    ttl_hours: float = DEFAULT_PAUSE_TTL_HOURS


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
#: Keys recently confirmed ABSENT in the DB -> {key: monotonic_checked_at}.
#: TTL'd (PR#404 round-2 P1-A): a PERMANENT negative cache would hide a flag
#: written later by the operator or another process until restart — defeating
#: the cross-process durability the DB backing exists for. Entries older than
#: _DB_MISS_TTL_S are re-checked against the DB.
_DB_MISS_TTL_S = 10.0
_DB_MISS_CACHE: dict[tuple[str, str], float] = {}


def _persist_flag(
    city: str, target_date: str, *, flagged_at: datetime, ttl_hours: float,
    detail: str, conn=None,
) -> None:
    """Durably write an anomaly pause so fail-closed state survives restart."""
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
            "(pause holds in-process; durable fail-closed write did not complete)",
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
        try:
            row_ttl = float(row[1])
        except (TypeError, ValueError):
            row_ttl = DEFAULT_PAUSE_TTL_HOURS
        return _AnomalyRecord(
            flagged_at=flagged_at.astimezone(UTC), detail=str(row[2]),
            ttl_hours=row_ttl if row_ttl > 0.0 else DEFAULT_PAUSE_TTL_HOURS,
        )
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
    persist: bool = True,
) -> None:
    """Pause the day0 lane for (city, target_date). Loud by design; persisted
    to the world DB so the pause SURVIVES daemon restarts (PR#404 P1)."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    with _REGISTRY_LOCK:
        _REGISTRY[(str(city), str(target_date))] = _AnomalyRecord(
            flagged_at=moment, detail=str(detail), ttl_hours=float(ttl_hours)
        )
        _DB_MISS_CACHE.pop((str(city), str(target_date)), None)
    logger.warning(
        "DAY0_ORACLE_ANOMALY_FLAGGED city=%s target_date=%s detail=%s — day0 entries PAUSED (fail-closed)",
        city, target_date, detail,
    )
    if persist:
        _persist_flag(
            city, target_date, flagged_at=moment, ttl_hours=ttl_hours, detail=detail, conn=conn,
        )


def clear_day0_oracle_anomaly(
    city: str,
    target_date: str,
    *,
    conn=None,
    persist: bool = True,
) -> bool:
    """Operator/cleanup hook. Returns True when a record was removed (memory
    or durable). Clears BOTH surfaces."""
    key = (str(city), str(target_date))
    import time as _time

    with _REGISTRY_LOCK:
        removed = _REGISTRY.pop(key, None) is not None
        _DB_MISS_CACHE[key] = _time.monotonic()
    if not persist:
        return removed
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
    import time as _time

    moment = (now or datetime.now(UTC)).astimezone(UTC)
    key = (str(city), str(target_date))
    monotonic_now = _time.monotonic()
    with _REGISTRY_LOCK:
        record = _REGISTRY.get(key)
        memory_miss = record is None
        miss_checked_at = _DB_MISS_CACHE.get(key)
        cached_db_miss = (
            miss_checked_at is not None
            and monotonic_now - miss_checked_at < _DB_MISS_TTL_S
        )
    if memory_miss and not cached_db_miss:
        # PR#404 P1: restart + cross-process resilience — read-through to the
        # durable flags; misses are cached only for _DB_MISS_TTL_S so a flag
        # written by another process becomes visible within seconds.
        record = _load_flag_from_db(city, target_date, conn=conn)
        with _REGISTRY_LOCK:
            if record is not None:
                _REGISTRY[key] = record
                _DB_MISS_CACHE.pop(key, None)
            else:
                _DB_MISS_CACHE[key] = monotonic_now
    if record is None:
        return False
    # The record's OWN TTL (persisted with the flag) is the authority; the
    # call-site ttl_hours is only a fallback for records without one
    # (PR#404 round-2 P1-A: a custom flag TTL must survive restart).
    effective_ttl = float(getattr(record, "ttl_hours", 0.0) or 0.0) or float(ttl_hours)
    if moment - record.flagged_at > timedelta(hours=effective_ttl):
        with _REGISTRY_LOCK:
            _REGISTRY.pop(key, None)
            _DB_MISS_CACHE[key] = monotonic_now
        _delete_expired_flag_best_effort(key[0], key[1], conn=conn)
        return False
    return True


def _delete_expired_flag_best_effort(city: str, target_date: str, *, conn=None) -> None:
    """Remove an expired durable flag row so restarts cannot re-hydrate it.
    Best-effort: any failure is silent (the TTL check rejects it anyway)."""
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
            conn.execute(
                "DELETE FROM day0_oracle_anomaly_flags WHERE city = ? AND target_date = ?",
                (str(city), str(target_date)),
            )
            conn.commit()
    except Exception:  # noqa: BLE001
        pass
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def active_day0_anomalies() -> dict[tuple[str, str], str]:
    """Observability snapshot: {(city, target_date): detail}."""
    with _REGISTRY_LOCK:
        return {key: record.detail for key, record in _REGISTRY.items()}


def apply_day0_oracle_anomaly_action(
    action: Day0OracleAnomalyAction,
    *,
    conn=None,
) -> None:
    """Persist a prefetch-phase anomaly action on the caller's write connection."""

    kind = str(action.action or "").strip().lower()
    if kind == "flag":
        flag_day0_oracle_anomaly(
            action.city,
            action.target_date,
            detail=action.detail,
            ttl_hours=action.ttl_hours,
            conn=conn,
        )
    elif kind == "clear":
        clear_day0_oracle_anomaly(action.city, action.target_date, conn=conn)
    else:
        raise ValueError(f"unknown day0 oracle anomaly action: {action.action!r}")


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


def wu_metar_anomaly_action(
    city: Any, extremes: Any, metar_reports: list
) -> Optional[Day0OracleAnomalyAction]:
    """Throttled WU-vs-METAR divergence check; returns a durable write action.

    Signature matches Day0FastObsEmitter.prefetch(anomaly_check=...). Any
    WU-side failure is fail-SOFT for emission (the fast lane keeps running)
    but logged — absence of the cross-check is visibility loss, not an anomaly.
    Only a CONCLUDED comparison arms the 10-min success memo. WU fetch success
    with an inconclusive comparison (for example METAR window stale for WU's
    last obs time) arms only the short retry throttle: the guard must re-check
    promptly once the METAR window catches up, instead of going dark for the
    full success interval.
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
            return None
        if now_monotonic - last_failure < _WU_CHECK_FAILURE_RETRY_S:
            return None

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
        return None
    wu_time_raw = getattr(wu_obs, "observation_time", None)
    try:
        wu_last_obs_time = (
            datetime.fromisoformat(str(wu_time_raw).replace("Z", "+00:00")) if wu_time_raw else None
        )
        if wu_last_obs_time is not None and wu_last_obs_time.tzinfo is None:
            wu_last_obs_time = None
    except (TypeError, ValueError):
        wu_last_obs_time = None
    # WU-side coverage status travels to the detector so it can refuse to
    # conclude when WU's live timeseries window never observed the local-day
    # extreme it would be compared on (diagnosis 2026-06-13). Only a genuinely
    # WU-sourced context carries a meaningful WU coverage claim; if the obs path
    # fell through to the METAR fast lane (source != "wu_api"), there is no WU
    # side to validate a tamper against -> treat as no WU coverage (inconclusive,
    # never a pause). The string is the existing Day0 classifier value.
    wu_source = str(getattr(wu_obs, "source", "") or "")
    wu_coverage_status = (
        str(getattr(wu_obs, "coverage_status", "") or "")
        if wu_source == "wu_api"
        else "NO_WU_SIDE"
    )
    verdict = check_wu_metar_divergence(
        city=city,
        target_date=target_date,
        metar_reports=metar_reports,
        wu_high_so_far=getattr(wu_obs, "high_so_far", None),
        wu_low_so_far=getattr(wu_obs, "low_so_far", None),
        wu_last_obs_time=wu_last_obs_time,
        wu_coverage_status=wu_coverage_status,
    )
    if not verdict.compared:
        with _WU_CHECK_MEMO_LOCK:
            _WU_CHECK_FAILURE_MEMO[city_name] = now_monotonic
        logger.warning(
            "DAY0_ORACLE_ANOMALY_COMPARISON_INCONCLUSIVE city=%s date=%s detail=%s "
            "(retry in %ss; success memo NOT consumed)",
            city_name, target_date, verdict.detail, _WU_CHECK_FAILURE_RETRY_S,
        )
        return None
    with _WU_CHECK_MEMO_LOCK:
        _WU_CHECK_MEMO[city_name] = now_monotonic
        _WU_CHECK_FAILURE_MEMO.pop(city_name, None)
    if verdict.diverged:
        flag_day0_oracle_anomaly(
            city_name,
            target_date,
            detail=verdict.detail,
            persist=False,
        )
        return Day0OracleAnomalyAction(
            action="flag",
            city=city_name,
            target_date=target_date,
            detail=verdict.detail,
        )
    else:
        # CLEAN-VERDICT CLEARS A STALE FLAG (false-pause TTL fix 2026-06-15). The
        # detector previously only ADDED flags and relied on the 24h TTL to expire,
        # so a morning IN-PROGRESS-high cadence flag (high_delta from two feeds
        # catching different provisional maxima before the daily high is reached)
        # blocked the WHOLE day — including the afternoon, when the high is
        # established and the same comparison comes back CLEAN. A compared & NOT
        # diverged verdict is positive same-window evidence the two settlement feeds
        # AGREE now; clear any stale flag so the afternoon sharp-edge day0 lane
        # reopens instead of waiting out the TTL. Tamper detection is fully
        # preserved: a genuine persistent sensor divergence diverges on EVERY check
        # and never reaches this branch; only a self-resolving (cadence/coverage)
        # divergence — the false-pause class — is cleared by a later clean read.
        clear_day0_oracle_anomaly(city_name, target_date, persist=False)
        return Day0OracleAnomalyAction(
            action="clear",
            city=city_name,
            target_date=target_date,
            detail=verdict.detail,
        )


def wu_metar_anomaly_check(city: Any, extremes: Any, metar_reports: list) -> None:
    """Throttled WU-vs-METAR divergence check; persists the resulting action."""

    action = wu_metar_anomaly_action(city, extremes, metar_reports)
    if action is not None:
        apply_day0_oracle_anomaly_action(action)


def check_wu_metar_divergence(
    *,
    city: Any,
    target_date: str,
    metar_reports: list,
    wu_high_so_far: Optional[float],
    wu_low_so_far: Optional[float],
    wu_last_obs_time: Optional[datetime],
    wu_coverage_status: Optional[str] = None,
) -> DivergenceVerdict:
    """Compare WU running extremes against METAR extremes over the SAME window.

    Caller supplies the WU side (from the existing settlement-bound WU obs
    context — high_so_far / low_so_far / observation_time / coverage_status).
    The METAR side is recomputed here truncated at wu_last_obs_time so latency
    cannot masquerade as divergence. Returns a verdict; flagging is the caller's
    choice (the fast-lane wiring flags + pauses on diverged=True).

    WU-SIDE COVERAGE GATE (matched-basis correction, diagnosis 2026-06-13):
    the per-city threshold (config/wu_metar_divergence.json) was measured on
    timestamp-MATCHED same-station readings — both feeds observed the SAME
    instants. The runtime instead compares each feed's RUNNING EXTREME. When
    WU's live timeseries.json window does NOT cover the local-day extreme it is
    being compared on (its first sample lands after local-midnight + the
    coverage grace window, so it never observed the pre-dawn LOW / the window
    is too sparse), WU's running extreme is set by a different sample than
    METAR's — a 2-9 unit cadence/coverage gap that is NOT tampering. The live
    proof: 171/174 false flags moved exactly ONE extreme with a clean integer
    gap (the un-observed one); only 3 moved both (the tamper signature). This
    is the SYMMETRIC twin of the existing METAR-side coverage gate below: a
    family may be paused only when WU actually observed the extreme it is
    compared on. ``wu_coverage_status`` is the EXISTING Day0 coverage classifier
    (observation_client._compute_day0_coverage_status): "OK" means the window
    reached local-day start with enough samples; "WINDOW_INCOMPLETE" /
    "LOW_COVERAGE" mean it did not -> NOT comparable -> NONE verdict (never a
    pause). ``None`` (caller did not thread it) preserves the legacy behavior.
    """
    from src.data.day0_fast_obs import running_extremes_for_local_day

    city_name = str(getattr(city, "name", "") or "")
    unit = str(getattr(city, "settlement_unit", "F") or "F").upper()
    if wu_last_obs_time is None or (wu_high_so_far is None and wu_low_so_far is None):
        return DivergenceVerdict(
            city=city_name, target_date=str(target_date), unit=unit,
            compared=False, diverged=False, detail="wu_side_unavailable",
        )
    # WU-side coverage gate (symmetric with the METAR-side gate below). When the
    # WU window did not observe the full local day, its running extreme is not a
    # comparable quantity — absence of evidence is not an anomaly, and it must
    # not pause trading (the module's NONE-verdict doctrine, header lines 27-28).
    if wu_coverage_status is not None and str(wu_coverage_status).strip().upper() != "OK":
        return DivergenceVerdict(
            city=city_name, target_date=str(target_date), unit=unit,
            compared=False, diverged=False,
            wu_last_obs_time=wu_last_obs_time.astimezone(UTC).isoformat(),
            detail=f"wu_side_insufficient_coverage (wu_coverage_status={wu_coverage_status})",
        )
    truncated = running_extremes_for_local_day(
        metar_reports, city=city, target_date=target_date, as_of=wu_last_obs_time.astimezone(UTC)
    )
    if truncated.sample_count == 0:
        return DivergenceVerdict(
            city=city_name, target_date=str(target_date), unit=unit,
            compared=False, diverged=False, detail="metar_side_no_overlapping_samples",
        )
    # METAR COVERAGE GATE (PR#404 round-2 P0-2B): truncating the METAR series
    # at WU's last obs time only removes FUTURE samples — it never proves the
    # METAR side actually REACHES that time. A METAR outage plus a fresh WU
    # update (e.g. METAR through 10:00, WU moved at 12:00) would compare a
    # 2-hour-stale METAR window against current WU and read as divergence ->
    # FALSE family pause (which gates entry q, hard-fact exits, and the cancel
    # sweep). The METAR window must cover WU's last obs time to within one
    # report-matching tolerance, else the comparison is NOT CONCLUDED.
    if (
        truncated.last_obs_time is None
        or truncated.last_obs_time
        < wu_last_obs_time.astimezone(UTC) - timedelta(seconds=_METAR_WU_COVERAGE_TOLERANCE_S)
    ):
        return DivergenceVerdict(
            city=city_name, target_date=str(target_date), unit=unit,
            compared=False, diverged=False,
            wu_last_obs_time=wu_last_obs_time.astimezone(UTC).isoformat(),
            metar_samples=truncated.sample_count,
            detail=(
                "metar_side_stale_for_wu_window "
                f"(metar_last_obs={truncated.last_obs_time.isoformat() if truncated.last_obs_time else None} "
                f"wu_last_obs={wu_last_obs_time.astimezone(UTC).isoformat()} "
                f"tolerance_s={_METAR_WU_COVERAGE_TOLERANCE_S})"
            ),
        )
    threshold, threshold_provenance = divergence_threshold_for_city(city_name, unit)
    # METAR-SIDE START-COVERAGE GATE (symmetric twin of the WU-side gate above and
    # of the METAR-END gate at 635; diagnosis 2026-06-14). The daily LOW forms in
    # the pre-dawn / early-morning window. When the METAR fast lane's window for
    # the local day STARTS late (first sample more than the coverage grace after
    # local midnight — the common case when the daemon booted mid-day), its
    # running min is a MIDDAY floor, not the daily low: it never observed the cold
    # extreme that WU's full-coverage window did. Comparing the two then reads a
    # pure coverage gap as divergence and false-pauses the family — the live
    # signature is high matched to <0.1 unit while low was off by 3-10 units (the
    # one-extreme COVERAGE signature, not the both-extreme TAMPER signature, exactly
    # as the WU-side gate's 171/174-vs-3 split). The low is a comparable quantity
    # ONLY when METAR's window also started at local-day onset; otherwise WU's
    # full-coverage low is authoritative and the cross-check simply could not run
    # on the low (module doctrine: absence of the cross-check is visibility loss,
    # not an anomaly). The HIGH stays compared — it forms within the covered late
    # window and the METAR-END gate vouches for it — so real high-side tampering is
    # still caught. Coverage notion reuses the SINGLE authority
    # observation_client._compute_day0_coverage_status (same grace as WU); only a
    # late START (WINDOW_INCOMPLETE) excludes the low, a thin-but-early window
    # (LOW_COVERAGE) still observed the dawn low and stays comparable.
    from zoneinfo import ZoneInfo

    from src.data.observation_client import _compute_day0_coverage_status

    metar_low_comparable = False
    metar_low_coverage = "WINDOW_INCOMPLETE"
    if truncated.first_obs_time is not None:
        try:
            _metar_first_local = truncated.first_obs_time.astimezone(
                ZoneInfo(str(getattr(city, "timezone")))
            )
            metar_low_coverage = _compute_day0_coverage_status(
                _metar_first_local, truncated.sample_count
            )
            metar_low_comparable = metar_low_coverage != "WINDOW_INCOMPLETE"
        except Exception:  # noqa: BLE001 — tz/helper failure -> not comparable (never a pause)
            metar_low_comparable = False
    high_delta = (
        abs(float(wu_high_so_far) - float(truncated.high_so_far))
        if wu_high_so_far is not None and truncated.high_so_far is not None
        else None
    )
    low_delta_raw = (
        abs(float(wu_low_so_far) - float(truncated.low_so_far))
        if wu_low_so_far is not None and truncated.low_so_far is not None
        else None
    )
    # Only a low difference observed by BOTH windows can conclude divergence; an
    # uncovered METAR low window contributes nothing to the divergence test.
    low_delta = low_delta_raw if metar_low_comparable else None
    diverged = any(delta is not None and delta > threshold for delta in (high_delta, low_delta))
    detail = (
        f"unit={unit} threshold={threshold} threshold_provenance={threshold_provenance} "
        f"high_delta={high_delta} low_delta={low_delta} low_delta_raw={low_delta_raw} "
        f"metar_low_coverage={metar_low_coverage} "
        f"wu_last_obs={wu_last_obs_time.isoformat()} metar_samples={truncated.sample_count}"
    )
    return DivergenceVerdict(
        city=city_name, target_date=str(target_date), unit=unit,
        compared=True, diverged=diverged,
        high_delta=high_delta, low_delta=low_delta,
        wu_last_obs_time=wu_last_obs_time.astimezone(UTC).isoformat(),
        metar_samples=truncated.sample_count, detail=detail,
    )
