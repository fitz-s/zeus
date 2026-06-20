# Created: 2026-06-10
# Last reused or audited: 2026-06-17
# Authority basis: adversarial review /tmp/day0_adversarial_review.md MUST-FIX
#   #1 (hard-fact bin-death exit lane) + #3-wiring (resting-order cancel on bin
#   death) — operator requirement "新高出现时能否立即drop". Calibration artifact:
#   config/wu_metar_divergence.json (empirical METAR-vs-WU divergence).
"""Day0 HARD-FACT exit lane: absorbing-boundary bin death exits immediately.

The category split this module encodes (the panic-sell fix's missing half):

  ESTIMATOR FLIP (probability opinion moved)   -> maturity gate + CI-separation
                                                  evidence (panic-sell hardening,
                                                  monitor_refresh + portfolio —
                                                  UNCHANGED by this module).
  HARD FACT (running extreme crossed the bin's -> EXIT NOW, this lane. A measured
  survival edge — monotone, irreversible)         max cannot be un-seen; holding a
                                                  structurally dead bin donates the
                                                  remaining salvage value.

Verdicts (both directions, both metrics):
  - buy_yes on a DEAD bin (extreme passed beyond the far edge)      -> EXIT_DEAD_BIN
  - buy_no  on an ABSORBING SHOULDER the extreme entered            -> EXIT_DEAD_BIN
    (the extreme can never leave an open-ended shoulder: NO has structurally lost)
  - buy_no  on a DEAD bin                                           -> HOLD_STRUCTURAL_WIN
    (NO is a guaranteed winner; never sell it on a hard fact)
  - buy_yes on the shoulder the extreme entered                     -> HOLD_STRUCTURAL_WIN
  - finite bin merely CONTAINING the extreme                        -> None
    (not a hard fact for either side: a max can still leave upward / min downward;
     that is estimator territory and stays behind the maturity gate)

Settlement-grade extreme sources (provenance-ordered):
  1. WU live obs (THE settlement reference) — throttled per (city, date); margin 0.
  2. METAR fast-lane memo (same physical station, ~3-9 min fresh) — admitted only
     for settlement-faithful cities (config/wu_metar_divergence.json), with a
     divergence margin derived from the SAME calibration artifact:
       empirical threshold <= 1.0 (feeds measured byte-identical post-rounding)
         -> margin 0 whole units: the integer-grid strict crossing (rounded 26 vs
            edge 25) is already a full rounding-quantum crossing;
       otherwise (default_guess / measured spread) -> margin = ceil(threshold)
            extra whole units beyond the edge before the kill counts as hard.
  An ACTIVE oracle-anomaly pause for the family disables the lane entirely
  (a suspect truth source must not drive an irreversible exit).

The lane is consumed by cycle_runtime's monitor loop (every exit-monitor cycle,
~2 min) BEFORE Position.evaluate_exit — it does not depend on fresh_prob, so the
buy_no day0 exit hole (no model authority at all) is closed for the hard-fact
class without touching the estimator-evidence machinery.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

UTC = timezone.utc

#: Throttle for the WU live-obs source (per city+date) — WU's own cadence is
#: 30-60 min; the METAR memo carries the fast path.
_WU_FETCH_INTERVAL_S = 600.0
_WU_MEMO: dict[tuple[str, str], tuple[float, Optional[float], Optional[float]]] = {}
_WU_MEMO_LOCK = threading.Lock()


@dataclass(frozen=True)
class HardFactVerdict:
    action: str  # "EXIT_DEAD_BIN" | "HOLD_STRUCTURAL_WIN"
    reason: str
    metric: str
    rounded_extreme: float
    source: str  # "wu_api" | "metar_fast_lane" | "wu_api+metar_fast_lane"


@dataclass(frozen=True)
class HardFactMonitorBelief:
    """Exact monitor belief derived from an absorbing Day0 hard fact."""

    held_side_prob: float
    yes_prob: float
    yes_verdict: str  # "YES_WON" | "YES_DEAD"
    held_verdict: str  # "STRUCTURAL_WIN" | "STRUCTURAL_LOSS"


def _normalize_direction(direction: Any) -> str:
    return str(getattr(direction, "value", direction) or "")


def hard_fact_bin_verdict(
    *,
    metric: str,
    direction: str,
    bin_low: Optional[float],
    bin_high: Optional[float],
    effective_extreme: float,
) -> Optional[HardFactVerdict]:
    """Pure absorbing-boundary verdict for one held bin against a settlement-grade
    extreme (already margin-adjusted by the caller). None = no hard fact."""
    metric = str(getattr(metric, "value", metric) or "").strip().lower()
    direction = _normalize_direction(direction)
    if metric not in {"high", "low"} or direction not in {"buy_yes", "buy_no"}:
        return None
    if bin_low is None and bin_high is None:
        return None

    def _verdict(action: str, reason: str) -> HardFactVerdict:
        return HardFactVerdict(
            action=action, reason=reason, metric=metric,
            rounded_extreme=float(effective_extreme), source="",
        )

    if metric == "high":
        dead = bin_high is not None and effective_extreme > float(bin_high)
        shoulder_entered = (
            bin_high is None and bin_low is not None and effective_extreme >= float(bin_low)
        )
    else:  # low
        dead = bin_low is not None and effective_extreme < float(bin_low)
        shoulder_entered = (
            bin_low is None and bin_high is not None and effective_extreme <= float(bin_high)
        )

    if dead:
        if direction == "buy_yes":
            return _verdict(
                "EXIT_DEAD_BIN",
                f"running {metric} extreme {effective_extreme} beyond bin "
                f"[{bin_low},{bin_high}] — YES structurally dead",
            )
        return _verdict(
            "HOLD_STRUCTURAL_WIN",
            f"running {metric} extreme {effective_extreme} killed bin "
            f"[{bin_low},{bin_high}] — NO structurally won; hold to settlement",
        )
    if shoulder_entered:
        if direction == "buy_no":
            return _verdict(
                "EXIT_DEAD_BIN",
                f"running {metric} extreme {effective_extreme} entered absorbing "
                f"shoulder [{bin_low},{bin_high}] — NO structurally dead",
            )
        return _verdict(
            "HOLD_STRUCTURAL_WIN",
            f"running {metric} extreme {effective_extreme} entered absorbing "
            f"shoulder [{bin_low},{bin_high}] — YES structurally won",
        )
    return None


def hard_fact_monitor_belief(
    *, verdict: HardFactVerdict, direction: Any
) -> Optional[HardFactMonitorBelief]:
    """Convert a hard-fact action into exact YES and held-side probabilities."""

    direction = _normalize_direction(direction)
    action = str(getattr(verdict, "action", "") or "")
    if direction == "buy_yes" and action == "EXIT_DEAD_BIN":
        return HardFactMonitorBelief(
            held_side_prob=0.0,
            yes_prob=0.0,
            yes_verdict="YES_DEAD",
            held_verdict="STRUCTURAL_LOSS",
        )
    if direction == "buy_no" and action == "HOLD_STRUCTURAL_WIN":
        return HardFactMonitorBelief(
            held_side_prob=1.0,
            yes_prob=0.0,
            yes_verdict="YES_DEAD",
            held_verdict="STRUCTURAL_WIN",
        )
    if direction == "buy_yes" and action == "HOLD_STRUCTURAL_WIN":
        return HardFactMonitorBelief(
            held_side_prob=1.0,
            yes_prob=1.0,
            yes_verdict="YES_WON",
            held_verdict="STRUCTURAL_WIN",
        )
    if direction == "buy_no" and action == "EXIT_DEAD_BIN":
        return HardFactMonitorBelief(
            held_side_prob=0.0,
            yes_prob=1.0,
            yes_verdict="YES_WON",
            held_verdict="STRUCTURAL_LOSS",
        )
    return None


def _metar_kill_margin_units(city_name: str, unit: str) -> Optional[float]:
    """Whole-unit margin a METAR-sourced extreme must exceed beyond the bin edge
    before its crossing counts as a HARD fact.

    Derived from the measured calibration artifact (operator rule: 'boundary
    crossing measured beyond the empirical divergence threshold + rounding
    quantum'): the integer-grid strict crossing already consumes one full
    rounding quantum, and the divergence allowance is the measured p99 —
    0 for cities where the feeds are byte-identical post-rounding (threshold
    1.0), `threshold` extra whole units for unmeasured/spread cities. Returns
    None when METAR must not drive kills at all (not settlement-faithful).
    """
    from src.data.day0_oracle_anomaly import (
        city_metar_settlement_faithful,
        divergence_threshold_for_city,
    )

    if not city_metar_settlement_faithful(city_name):
        return None
    threshold, provenance = divergence_threshold_for_city(city_name, unit)
    if provenance == "empirical" and threshold <= 1.0:
        return 0.0
    return float(threshold)


def _wu_rounded_extremes(
    city: Any, target_date: str, *, now: datetime
) -> tuple[Optional[float], Optional[float]]:
    """(rounded_high_so_far, rounded_low_so_far) from the WU settlement reference,
    throttled per (city, date). (None, None) on any failure — fail-soft: the lane
    simply has no WU source this cycle."""
    key = (str(getattr(city, "name", "")), str(target_date))
    monotonic_now = time.monotonic()
    with _WU_MEMO_LOCK:
        cached = _WU_MEMO.get(key)
        if cached is not None and monotonic_now - cached[0] < _WU_FETCH_INTERVAL_S:
            return cached[1], cached[2]
    high = low = None
    try:
        from src.contracts.settlement_semantics import SettlementSemantics
        from src.data.observation_client import get_current_observation

        obs = get_current_observation(city, target_date=target_date, reference_time=now)
        semantics = SettlementSemantics.for_city(city)
        raw_high = getattr(obs, "high_so_far", None)
        raw_low = getattr(obs, "low_so_far", None)
        if raw_high is not None:
            high = float(semantics.round_single(float(raw_high)))
        if raw_low is not None:
            low = float(semantics.round_single(float(raw_low)))
    except Exception as exc:  # noqa: BLE001 — source fail-soft, lane holds
        logger.debug("day0 hard-fact WU source unavailable for %s/%s: %s", key[0], key[1], exc)
    with _WU_MEMO_LOCK:
        _WU_MEMO[key] = (monotonic_now, high, low)
    return high, low


def _metar_rounded_extreme(
    city_name: str, target_date: str, metric: str, *, world_conn: Any = None
) -> Optional[float]:
    """Settlement-grade rounded extreme from the fast METAR lane's emit memo
    (values there passed the LIVE_AUTHORITY hard-fact statuses at emission).

    ``world_conn`` is threaded from the caller's composite connection so the
    kill-memo restart-recovery path does not open an independent world connection.
    When None (non-composite callers), recovery is skipped for this call — the
    in-process memo is used when warm, or None is returned when cold.
    """
    try:
        from src.data.day0_fast_obs import get_fast_obs_emitter

        return get_fast_obs_emitter().latest_rounded_extreme(
            city_name, target_date, metric, world_conn=world_conn
        )
    except Exception:  # noqa: BLE001
        return None


def _durable_observation_instants_extremes(
    *,
    city: Any,
    target_date: str,
    now: datetime,
    world_conn: Any = None,
) -> tuple[Optional[float], Optional[float], str]:
    """Verified durable WU-hourly extrema for the local target date.

    This is the restart-safe side of the hard-fact lane. WU live API and METAR
    memo are useful when warm, but monitor decisions must also consume verified
    rows already written to the canonical observation surface. LOW uses the
    monotone minimum over the local target date; HIGH uses the monotone maximum.
    """

    if world_conn is None:
        return None, None, ""
    city_name = str(getattr(city, "name", "") or "")
    if not city_name or not target_date:
        return None, None, ""

    metric_filter = ("", "high", "low")
    now_iso = now.astimezone(UTC).isoformat()
    table_refs = (
        "world.observation_instants",
        "observation_instants",
        "forecasts.observation_instants",
    )
    for table_ref in table_refs:
        try:
            row = world_conn.execute(
                f"""
                SELECT
                    MAX(CASE WHEN running_max IS NOT NULL THEN CAST(running_max AS REAL) END) AS high,
                    MIN(CASE WHEN running_min IS NOT NULL THEN CAST(running_min AS REAL) END) AS low,
                    COUNT(*) AS n_rows
                FROM {table_ref}
                WHERE city = ?
                  AND target_date = ?
                  AND substr(local_timestamp, 1, 10) = target_date
                  AND utc_timestamp <= ?
                  AND UPPER(COALESCE(authority, '')) = 'VERIFIED'
                  AND COALESCE(causality_status, 'OK') = 'OK'
                  AND LOWER(COALESCE(source, '')) LIKE 'wu%'
                  AND LOWER(COALESCE(temperature_metric, '')) IN (?, ?, ?)
                """,
                (city_name, target_date, now_iso, *metric_filter),
            ).fetchone()
        except Exception:  # noqa: BLE001 - missing attachment/table/columns fail soft
            continue
        if row is None:
            continue
        try:
            n_rows = int(row["n_rows"] if hasattr(row, "keys") else row[2] or 0)
            high_raw = row["high"] if hasattr(row, "keys") else row[0]
            low_raw = row["low"] if hasattr(row, "keys") else row[1]
        except (TypeError, KeyError, IndexError, ValueError):
            continue
        if n_rows <= 0 or (high_raw is None and low_raw is None):
            continue
        high = float(high_raw) if high_raw is not None else None
        low = float(low_raw) if low_raw is not None else None
        return high, low, "durable_observation_instants"
    return None, None, ""


def settlement_grade_effective_extreme(
    *,
    city: Any,
    target_date: str,
    metric: str,
    now: datetime,
    world_conn: Any = None,
) -> tuple[Optional[float], str]:
    """(effective_extreme, source) for hard-fact decisions, margin-adjusted.

    WU contributes at face value (it IS the settlement reference). METAR
    contributes shifted by the calibration margin in the NON-kill direction
    (HIGH: minus margin; LOW: plus margin) so a METAR-only crossing must clear
    the measured divergence allowance. The two compose by the absorbing law
    (HIGH max / LOW min). None when no source is available.

    ``world_conn`` is threaded from the monitoring-phase composite connection so
    the METAR kill-memo recovery (cold-start path) does not open an independent
    world connection — see connection-burst antibody (2026-06-13).
    """
    city_name = str(getattr(city, "name", "") or "")
    unit = str(getattr(city, "settlement_unit", "F") or "F").upper()
    wu_high, wu_low = _wu_rounded_extremes(city, target_date, now=now)
    durable_high, durable_low, durable_source = _durable_observation_instants_extremes(
        city=city,
        target_date=target_date,
        now=now,
        world_conn=world_conn,
    )

    wu_values = []
    wu_sources = []
    api_value = wu_high if metric == "high" else wu_low
    durable_value = durable_high if metric == "high" else durable_low
    if api_value is not None:
        wu_values.append(float(api_value))
        wu_sources.append("wu_api")
    if durable_value is not None:
        wu_values.append(float(durable_value))
        wu_sources.append(durable_source)
    if wu_values:
        wu_value = max(wu_values) if metric == "high" else min(wu_values)
        wu_source = "+".join(dict.fromkeys(wu_sources))
    else:
        wu_value = None
        wu_source = ""

    metar_value = None
    margin = _metar_kill_margin_units(city_name, unit)
    if margin is not None:
        raw = _metar_rounded_extreme(city_name, target_date, metric, world_conn=world_conn)
        if raw is not None:
            metar_value = raw - margin if metric == "high" else raw + margin

    if wu_value is None and metar_value is None:
        return None, ""
    if metar_value is None:
        return float(wu_value), wu_source
    if wu_value is None:
        return float(metar_value), "metar_fast_lane"
    if metric == "high":
        return float(max(wu_value, metar_value)), f"{wu_source}+metar_fast_lane"
    return float(min(wu_value, metar_value)), f"{wu_source}+metar_fast_lane"


def evaluate_hard_fact_exit(
    *,
    position: Any,
    city: Any,
    now: Optional[datetime] = None,
    world_conn: Any = None,
    durable_only: bool = False,
) -> Optional[HardFactVerdict]:
    """The lane entry point for one held day0 position. None = no hard fact
    (the estimator-evidence lane proceeds unchanged). Fail-soft everywhere:
    any data gap or active oracle-anomaly pause yields None (hold).

    ``world_conn`` should be the caller's composite world connection (zeus_trades
    with zeus-world ATTACHed). It is threaded through to the METAR kill-memo
    recovery path so the cold-start restart does not open an independent world
    connection per city. When None, the METAR memo recovery is skipped for cold
    cells; warm memo cells are unaffected.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        target_date = str(getattr(position, "target_date", "") or "")
        direction = _normalize_direction(getattr(position, "direction", "") or "")
        metric = str(getattr(position, "temperature_metric", "") or "high")
        if not target_date or direction not in {"buy_yes", "buy_no"}:
            return None

        from src.data.day0_oracle_anomaly import is_day0_family_paused

        city_name = str(getattr(city, "name", "") or "")
        if is_day0_family_paused(city_name, target_date, now=moment):
            logger.warning(
                "DAY0_HARD_FACT_LANE_SUSPENDED city=%s date=%s trade=%s — oracle anomaly pause active",
                city_name, target_date, getattr(position, "trade_id", "?"),
            )
            return None

        from src.data.market_scanner import _parse_temp_range

        bin_low, bin_high = _parse_temp_range(str(getattr(position, "bin_label", "") or ""))
        if bin_low is None and bin_high is None:
            return None

        durable_high, durable_low, durable_source = _durable_observation_instants_extremes(
            city=city,
            target_date=target_date,
            now=moment,
            world_conn=world_conn,
        )
        durable_effective = durable_high if metric == "high" else durable_low
        if durable_effective is not None:
            durable_verdict = hard_fact_bin_verdict(
                metric=metric, direction=direction,
                bin_low=bin_low, bin_high=bin_high,
                effective_extreme=float(durable_effective),
            )
            if durable_verdict is not None:
                verdict = HardFactVerdict(
                    action=durable_verdict.action,
                    reason=durable_verdict.reason,
                    metric=durable_verdict.metric,
                    rounded_extreme=durable_verdict.rounded_extreme,
                    source=durable_source,
                )
                log = logger.warning if verdict.action == "EXIT_DEAD_BIN" else logger.info
                log(
                    "DAY0_HARD_FACT_%s trade=%s city=%s date=%s dir=%s bin=[%s,%s] "
                    "extreme=%s source=%s: %s",
                    verdict.action, getattr(position, "trade_id", "?"), city_name, target_date,
                    direction, bin_low, bin_high, durable_effective, durable_source, verdict.reason,
                )
                return verdict
        if durable_only:
            return None

        effective, source = settlement_grade_effective_extreme(
            city=city, target_date=target_date, metric=metric, now=moment, world_conn=world_conn
        )
        if effective is None:
            return None
        verdict = hard_fact_bin_verdict(
            metric=metric, direction=direction,
            bin_low=bin_low, bin_high=bin_high,
            effective_extreme=effective,
        )
        if verdict is None:
            return None
        verdict = HardFactVerdict(
            action=verdict.action, reason=verdict.reason, metric=verdict.metric,
            rounded_extreme=verdict.rounded_extreme, source=source,
        )
        log = logger.warning if verdict.action == "EXIT_DEAD_BIN" else logger.info
        log(
            "DAY0_HARD_FACT_%s trade=%s city=%s date=%s dir=%s bin=[%s,%s] extreme=%s source=%s: %s",
            verdict.action, getattr(position, "trade_id", "?"), city_name, target_date,
            direction, bin_low, bin_high, effective, source, verdict.reason,
        )
        return verdict
    except Exception as exc:  # noqa: BLE001 — the lane must never break the monitor
        logger.warning(
            "DAY0_HARD_FACT_LANE_ERROR trade=%s exc=%s: %s",
            getattr(position, "trade_id", "?"), type(exc).__name__, exc,
        )
        return None


# ---------------------------------------------------------------------------
# FIX 2 — resting-order cancel on bin death / family anomaly pause.
# Minimal correct cut (adversarial review finding 4): day0 families' resting
# ENTRY orders are cancelled when their bin is hard-fact dead for the order's
# side, or when the family is oracle-anomaly paused. The general
# screen_reprice/stale-quote cancel wiring remains future work.
# ---------------------------------------------------------------------------


def _order_field(order: dict, *names: str) -> str:
    for name in names:
        value = order.get(name)
        if value:
            return str(value)
    return ""


def _row_get(row: Any, key: str, index: int) -> Any:
    return row[key] if hasattr(row, "keys") else row[index]


def _resolve_order_bin_identity(conn: Any, token_id: str) -> Optional[dict]:
    """Token -> (city, target_date, metric, range bounds, direction) using the
    PRODUCTION topology surfaces (PR#404 P1 fix — the prior single
    market_events.token_id lookup missed every NO token, because market_events
    stores only the YES token; and the metric was guessed from the slug).

    Resolution chain (all fail-soft per source):
      1. executable_market_snapshots (trades main schema): yes_token_id /
         no_token_id -> condition_id + DIRECTION (asset==no_token -> buy_no).
      2. market_events by condition_id OR token_id (main / world. / forecasts.
         schemas): city, target_date, range_low/high, and — where the schema
         carries it — the TYPED temperature_metric column.
      3. market_topology_state by condition_id (trades main schema): the TYPED
         temperature_metric + city_id + target_local_date authority.
    The metric is NEVER derived from slug substrings: a row whose metric
    cannot be typed is SKIPPED (no cancel — fail-soft, never wrong-direction).
    """
    import sqlite3 as _sqlite3

    condition_id = ""
    direction = ""
    try:
        row = conn.execute(
            """
            SELECT condition_id, yes_token_id, no_token_id
            FROM executable_market_snapshots
            WHERE yes_token_id = ? OR no_token_id = ?
            ORDER BY captured_at DESC LIMIT 1
            """,
            (token_id, token_id),
        ).fetchone()
        if row is not None:
            condition_id = str(_row_get(row, "condition_id", 0) or "")
            no_token = str(_row_get(row, "no_token_id", 2) or "")
            direction = "buy_no" if token_id == no_token else "buy_yes"
    except _sqlite3.Error:
        pass

    identity: dict = {}
    # EXPLICIT COLUMN LISTS + tuple-safe access (PR#404 round-2 P1-B): the
    # prior SELECT * + `dict(row) if hasattr(row, "keys") else {}` silently
    # produced an EMPTY identity on connections WITHOUT sqlite3.Row factory —
    # a dead-bin resting order quietly escaped cancellation because of an
    # implicit connection attribute. A risk-reduction path must be
    # row-factory-agnostic: explicit columns + positional _row_get, with a
    # two-query fallback for legacy schemas lacking temperature_metric.
    _ME_COLS_WITH_METRIC = (
        "city, target_date, range_low, range_high, temperature_metric, condition_id, token_id"
    )
    _ME_COLS_LEGACY = "city, target_date, range_low, range_high, condition_id, token_id"
    for table_ref in ("market_events", "world.market_events", "forecasts.market_events"):
        me_row = None
        has_metric_col = True
        for columns, with_metric in ((_ME_COLS_WITH_METRIC, True), (_ME_COLS_LEGACY, False)):
            try:
                if condition_id:
                    me_row = conn.execute(
                        f"SELECT {columns} FROM {table_ref} "
                        "WHERE condition_id = ? OR token_id = ? LIMIT 1",
                        (condition_id, token_id),
                    ).fetchone()
                else:
                    me_row = conn.execute(
                        f"SELECT {columns} FROM {table_ref} WHERE token_id = ? LIMIT 1",
                        (token_id,),
                    ).fetchone()
                has_metric_col = with_metric
                break  # query shape accepted (row may still be None)
            except _sqlite3.Error:
                me_row = None
                continue  # missing table/schema OR missing temperature_metric column
        if me_row is None:
            continue
        if has_metric_col:
            metric_value = str(_row_get(me_row, "temperature_metric", 4) or "")
            cond_value = str(_row_get(me_row, "condition_id", 5) or "")
            row_token = str(_row_get(me_row, "token_id", 6) or "")
        else:
            metric_value = ""
            cond_value = str(_row_get(me_row, "condition_id", 4) or "")
            row_token = str(_row_get(me_row, "token_id", 5) or "")
        identity = {
            "city": str(_row_get(me_row, "city", 0) or ""),
            "target_date": str(_row_get(me_row, "target_date", 1) or ""),
            "range_low": _row_get(me_row, "range_low", 2),
            "range_high": _row_get(me_row, "range_high", 3),
            "metric": metric_value,
            "condition_id": condition_id or cond_value,
        }
        if not direction:
            # market_events stores the YES token; matching by token_id here
            # means the order IS the YES side.
            direction = "buy_yes" if row_token == token_id else ""
        break
    if not identity:
        return None

    if not identity.get("metric") and identity.get("condition_id"):
        # TYPED metric authority: market_topology_state (never slug guessing).
        try:
            mts = conn.execute(
                """
                SELECT temperature_metric, city_id, target_local_date
                FROM market_topology_state
                WHERE condition_id = ?
                ORDER BY recorded_at DESC LIMIT 1
                """,
                (identity["condition_id"],),
            ).fetchone()
            if mts is not None:
                identity["metric"] = str(_row_get(mts, "temperature_metric", 0) or "")
                identity.setdefault("city", str(_row_get(mts, "city_id", 1) or ""))
                if not identity.get("target_date"):
                    identity["target_date"] = str(_row_get(mts, "target_local_date", 2) or "")
        except _sqlite3.Error:
            pass

    if identity.get("metric") not in {"high", "low"} or not direction:
        return None
    identity["direction"] = direction
    return identity


def cancel_day0_dead_bin_resting_entries(
    *,
    clob: Any,
    conn: Any,
    cities_by_name: dict[str, Any],
    now: Optional[datetime] = None,
    limit: int = 25,
) -> int:
    """Cancel our OPEN resting entry orders whose day0 bin is hard-fact dead
    (for the order's side) or whose family is anomaly-paused.

    Token -> bin identity via _resolve_order_bin_identity (EMS yes/no tokens +
    market_events bounds + TYPED metric — PR#404 P1). Fail-soft per order; a
    cancel failure is loud but never raises. Returns cancels issued.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        open_orders = clob.get_open_orders() or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("day0 dead-bin cancel sweep: get_open_orders failed: %s", exc)
        return 0
    if not open_orders:
        return 0

    from src.data.day0_oracle_anomaly import is_day0_family_paused
    from zoneinfo import ZoneInfo

    cancelled = 0
    for order in open_orders:
        if cancelled >= max(1, int(limit)):
            break
        try:
            side = _order_field(order, "side").upper()
            if side and side != "BUY":
                continue  # exit (SELL) orders belong to the exit lifecycle
            order_id = _order_field(order, "orderID", "order_id", "id")
            token_id = _order_field(order, "asset_id", "token_id", "tokenID", "market")
            if not order_id or not token_id:
                continue
            identity = _resolve_order_bin_identity(conn, token_id)
            if identity is None:
                continue
            city_name = identity["city"]
            target_date = identity["target_date"]
            range_low = identity["range_low"]
            range_high = identity["range_high"]
            metric = identity["metric"]
            direction = identity["direction"]
            city = cities_by_name.get(city_name)
            if city is None:
                continue
            # day0 scope: the order's market settles TODAY in city-local time.
            local_today = moment.astimezone(ZoneInfo(str(city.timezone))).date().isoformat()
            if str(target_date)[:10] != local_today:
                continue

            paused = is_day0_family_paused(city_name, target_date, now=moment)
            verdict = None
            if not paused:
                effective, source = settlement_grade_effective_extreme(
                    city=city, target_date=target_date, metric=metric, now=moment
                )
                if effective is not None:
                    verdict = hard_fact_bin_verdict(
                        metric=metric, direction=direction,
                        bin_low=float(range_low) if range_low is not None else None,
                        bin_high=float(range_high) if range_high is not None else None,
                        effective_extreme=effective,
                    )
            if not paused and (verdict is None or verdict.action != "EXIT_DEAD_BIN"):
                continue
            reason = "ORACLE_ANOMALY_PAUSE" if paused else "HARD_FACT_BIN_DEAD"
            try:
                clob.cancel_order(order_id)
                cancelled += 1
                logger.warning(
                    "DAY0_RESTING_ORDER_CANCELLED order=%s token=%s city=%s date=%s side=%s "
                    "dir=%s reason=%s%s",
                    order_id, token_id[:18], city_name, target_date, side or "BUY",
                    direction, reason,
                    "" if paused else f" ({verdict.reason})",
                )
            except Exception as exc:  # noqa: BLE001 — cancel fail loud, sweep continues
                logger.error(
                    "DAY0_RESTING_ORDER_CANCEL_FAILED order=%s reason=%s exc=%s: %s",
                    order_id, reason, type(exc).__name__, exc,
                )
        except Exception as exc:  # noqa: BLE001 — one order must not kill the sweep
            logger.debug("day0 dead-bin cancel sweep: order skipped: %s", exc)
    return cancelled


def _reset_wu_memo_for_tests() -> None:
    with _WU_MEMO_LOCK:
        _WU_MEMO.clear()
