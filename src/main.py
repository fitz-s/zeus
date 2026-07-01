"""Zeus main entry point — trading daemon only (Phase 3).

All discovery modes go through the same CycleRunner with different DiscoveryMode values.
The lifecycle is identical for all modes — only scanner parameters differ.

Phase 3: K2 ingest jobs removed. src/ingest_main.py owns all K2 ticks,
etl_recalibrate, ecmwf_open_data, automation_analysis, hole_scanner,
startup_catch_up, source_health_probe, drift_detector, ingest_status_rollup,
and harvester_truth_writer. Trading owns only discovery, harvester_pnl_resolver,
venue heartbeat, wallet gate, freshness gate (consumer), schema validator (consumer).

Advisory file lock infrastructure (src.data.dual_run_lock) is retained in code
— other daemons may be added in future. The K2 ticks that called it are removed.
"""

# Created: pre-Phase-0 (K2 scheduler wiring via 27bedbd; P9A run_mode observability via 7081634)
# Last reused/audited: 2026-06-29
# Authority basis: Phase 3 two-system independence — docs/operations/task_2026-04-30_two_system_independence/design.md §5 Phase 3; docs/archive/2026-Q2/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md;
#   MAJOR #1 antibody (2026-06-05) — assert_kelly_multiplier_within_correlated_ceiling boot guard (over-size door / iron rule 5)
#                  + 2026-05-17 CLOB venue-heartbeat critical-path split
#                  + 2026-06-04 mainstream made display-only/unconstructable-as-decision (arm direction-gate boot guard + submit enforce branch DELETED)

import functools
import json
import logging
import math
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import faulthandler
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

# Live-hang diagnostics (2026-05-31): SIGUSR1 dumps ALL thread stacks to stderr
# (logs/zeus-live.err) so a frozen reactor cycle (indefinite _PyMutex/lock
# deadlock — same class as the 5h market-channel hang) can be pinned WITHOUT
# root-level py-spy. faulthandler.enable() also dumps on fatal signals. Additive.
faulthandler.enable()
try:
    faulthandler.register(signal.SIGUSR1, all_threads=True, chain=True)
except (AttributeError, ValueError, OSError):
    pass

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
except ModuleNotFoundError:  # pragma: no cover - local minimal test env fallback
    BlockingScheduler = None

from src.config import cities_by_name, get_mode, settings
from src.contracts.canonical_lifecycle import VenueOrderStatus
from src.engine.discovery_mode import DiscoveryMode
from src.observability.scheduler_health import _write_scheduler_health
from src.runtime import bankroll_provider
from src.state.db import (
    init_schema,
    init_schema_trade_only,
    get_world_connection,
    get_trade_connection,
    get_world_connection_read_only,
)
from src.state.portfolio import load_portfolio

logger = logging.getLogger("zeus")

# Cross-mode lock: prevents two discovery modes from reading/writing portfolio concurrently
_cycle_lock = threading.Lock()
_held_position_monitor_active = threading.Event()
_held_position_monitor_bootstrap_complete = threading.Event()
_edli_redecision_confirm_refresh_lock = threading.Lock()
_HELD_POSITION_MONITOR_DEFER_JOBS = frozenset(
    {
        "market_discovery",
        "EDLI mainstream warm",
    }
)
_market_discovery_last_completed_monotonic: float | None = None
OPENING_HUNT_FIRST_DELAY_SECONDS = 30.0
HELD_POSITION_MONITOR_FIRST_DELAY_SECONDS = 5.0
# Fitz #5 scheduler-liveness (2026-06-08): the EDLI market-substrate warm cycle's
# APScheduler interval. The refresh wall-clock budget
# (ZEUS_REACTOR_REFRESH_BUDGET_SECONDS in src.data.substrate_observer) MUST be
# strictly less than this so a cycle finishes before its next trigger; otherwise
# max_instances=1 skips every overlapping run ("maximum number of running instances
# reached"), the executable substrate is never refreshed, and the armed daemon is
# starved of candidates. The interval also stays within the 180s executable-price
# freshness window. The invariant is asserted at job registration.
_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS = 20.0

def _ensure_day0_identity_platt_fit_at_boot() -> None:
    """Ensure Day0 nowcast has a live fit row before scheduler/reactor work starts."""

    try:
        from src.state.day0_nowcast_store import ensure_identity_platt_fit

        fit = ensure_identity_platt_fit()
        logger.info(
            "day0_horizon_platt_fit_bootstrap: fit_run_id=%s fit_artifact_id=%s",
            getattr(fit, "fit_run_id", None),
            getattr(fit, "fit_artifact_id", None),
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"DAY0_HORIZON_PLATT_FIT_BOOTSTRAP_FAILED:{exc}") from exc


def _substrate_refresh_family_text_key(value: object) -> str:
    text = str(value or "").strip().lower()
    return " ".join(text.replace("-", " ").replace("_", " ").split())


def _substrate_refresh_city_alias_to_name() -> dict[str, str]:
    from src.config import cities_by_name as _refresh_cities_by_name

    alias_to_name: dict[str, str] = {}
    for _city in _refresh_cities_by_name.values():
        for _surface in (
            _city.name,
            *_city.aliases,
            *_city.slug_names,
        ):
            _key = _substrate_refresh_family_text_key(_surface)
            if _key:
                alias_to_name[_key] = _city.name
    return alias_to_name


def _substrate_refresh_canonical_city_name(city: object) -> str:
    raw = str(getattr(city, "name", None) or city or "").strip()
    return _substrate_refresh_city_alias_to_name().get(
        _substrate_refresh_family_text_key(raw),
        raw,
    )


def _substrate_refresh_canonical_metric(metric: object) -> str:
    text = _substrate_refresh_family_text_key(metric)
    if text in {"low", "lowest", "min", "minimum", "tmin"} or text.startswith("lowest "):
        return "low"
    if text in {"high", "highest", "max", "maximum", "tmax"} or text.startswith("highest "):
        return "high"
    return text


def _substrate_refresh_family_key(
    city: object,
    target_date: object,
    metric: object,
) -> tuple[str, str, str]:
    return (
        _substrate_refresh_family_text_key(
            _substrate_refresh_canonical_city_name(city)
        ),
        str(target_date or "").strip(),
        _substrate_refresh_canonical_metric(metric),
    )


@dataclass(frozen=True)
class _Day0LiveFamilyAdmission:
    admitted_families: frozenset[tuple[str, str, str]]
    expiry_safe: bool

    def __call__(self, observation: dict[str, Any]) -> bool:
        family = _substrate_refresh_family_key(
            observation.get("city"),
            observation.get("target_date"),
            observation.get("metric"),
        )
        return all(family) and family in self.admitted_families


def _edli_day0_live_family_admission(
    forecasts_conn,
    trade_conn,
    *,
    decision_time: datetime | None = None,
) -> _Day0LiveFamilyAdmission:
    """Build the Day0 execution admission set from live market/exposure truth.

    Day0 observations are valid observation facts, but live execution events must be
    market-backed or tied to already-owned risk. Otherwise the reactor spends claim and
    substrate budget on families where no order can ever be placed.
    """

    decision_time_utc = (
        datetime.now(timezone.utc)
        if decision_time is None
        else decision_time.astimezone(timezone.utc)
    )

    def _market_family_is_current_local_day(city: object, target_date: object) -> bool:
        city_name = _substrate_refresh_canonical_city_name(city)
        city_cfg = cities_by_name.get(city_name)
        city_tz = str(getattr(city_cfg, "timezone", "") or "")
        if not city_tz:
            return False
        try:
            target_local_date = date.fromisoformat(str(target_date or "").strip())
            decision_local_date = decision_time_utc.astimezone(ZoneInfo(city_tz)).date()
        except Exception:
            return False
        return target_local_date == decision_local_date

    target_floor = (decision_time_utc.date() - timedelta(days=1)).isoformat()
    target_ceiling = (decision_time_utc.date() + timedelta(days=1)).isoformat()
    market_families: set[tuple[str, str, str]] = set()
    market_surface_read_ok = False
    try:
        table_row = forecasts_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_events' LIMIT 1"
        ).fetchone()
        if table_row is not None:
            rows = forecasts_conn.execute(
                """
                SELECT city, target_date, temperature_metric
                  FROM market_events
                 WHERE city IS NOT NULL
                   AND target_date IS NOT NULL
                   AND temperature_metric IN ('high', 'low')
                   AND target_date BETWEEN ? AND ?
                """,
                (target_floor, target_ceiling),
            ).fetchall()
            market_surface_read_ok = True
            for city, target_date, metric in rows:
                if not _market_family_is_current_local_day(city, target_date):
                    continue
                family = _substrate_refresh_family_key(city, target_date, metric)
                if all(family):
                    market_families.add(family)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "EDLI day0 live family admission: market_events read failed; "
            "Day0 execution emission restricted to current exposure families: %r",
            exc,
        )

    exposure_families: set[tuple[str, str, str]] = set()
    exposure_surface_read_ok = False
    try:
        trade_tables = {
            str(row[0])
            for row in trade_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        exposure_surface_read_ok = {
            "position_current",
            "venue_commands",
            "venue_order_facts",
        }.issubset(trade_tables)
    except Exception as exc:  # noqa: BLE001
        logger.warning("EDLI day0 live family admission: trade exposure surface probe failed: %r", exc)

    def _add_families(raw: Iterable[tuple[object, object, object]]) -> None:
        for city, target_date, metric in raw or ():
            family = _substrate_refresh_family_key(city, target_date, metric)
            if all(family):
                exposure_families.add(family)

    try:
        _add_families(_open_rest_family_rows_for_refresh(trade_conn))
    except Exception as exc:  # noqa: BLE001
        logger.warning("EDLI day0 live family admission: open-rest family read failed: %r", exc)
    try:
        from src.data.replacement_cycle_advance_trigger import _held_position_families

        _add_families(_held_position_families(trade_conn))
    except Exception as exc:  # noqa: BLE001
        logger.warning("EDLI day0 live family admission: held-position family read failed: %r", exc)

    admitted = frozenset(market_families | exposure_families)
    return _Day0LiveFamilyAdmission(
        admitted_families=admitted,
        expiry_safe=market_surface_read_ok and exposure_surface_read_ok,
    )


# Wave-2 item 5 (2026-06-12): "edli_live" is the only event-driven live mode.
LIVE_EXECUTION_MODES = {
    "legacy_cron",
    "edli_live",
    "disabled",
}
EDLI_EVENT_DRIVEN_MODES = {
    "edli_live",
}
REACTOR_MODE_BY_LIVE_STAGE = {
    "legacy_cron": "disabled",
    "disabled": "disabled",
    "edli_live": "live",
}
# Live production has one EDLI scope: forecast plus Day0. Historical staging
# scopes stay out of the live daemon so the execution layer has one state.
EDLI_LIVE_SCOPES = frozenset({"forecast_plus_day0"})
EDLI_RUNTIME_FLAGS = (
    "enabled",
    "event_writer_enabled",
    "forecast_snapshot_trigger_enabled",
    "market_channel_ingestor_enabled",
    "edli_user_channel_reconcile_enabled",
)
EDLI_STAGE_PASS = "PASS"
EDLI_STAGE_WAITING = "WAITING_FOR_QUALIFYING_EVENT"
EDLI_STAGE_FAIL = "FAIL"
EDLI_STAGE_RISK_REASON_PREFIXES = (
    "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN",
    "EDLI_STAGE_LIVE_CAP_RESERVED",
    "EDLI_STAGE_SOURCE_HEALTH_STALE",
    "EDLI_STAGE_SOURCE_HEALTH_MISSING",
    "EDLI_STAGE_STATUS_SUMMARY_STALE",
    "EDLI_STAGE_STATUS_SUMMARY_MISSING",
)
EDLI_STAGE_FRESH_FILE_FUTURE_SKEW_TOLERANCE_SECONDS = 5.0
_EDLI_LIVE_BOOT_DEFERRED_REASON_PREFIXES = (
    "EDLI_STAGE_STATUS_SUMMARY_STALE",
    "EDLI_STAGE_STATUS_SUMMARY_MISSING",
)
REQUIRED_STAGE_FILES_BY_MODE = {
    "edli_live": (
        "edli_stage_loaded_sha_file",
        "edli_stage_source_health_json",
        "edli_stage_status_json",
        "edli_live_promotion_artifact_path",
    ),
}

# PR-S6 deployment freshness gate — mutable container populated in main() at boot.
# Tests monkeypatch this dict directly; scheduler job reads it each tick.
_BOOT_STATE: dict = {"sha": None, "ts": None}


@dataclass(frozen=True)
class EdliStageReadiness:
    stage: str
    status: str
    live_entries_allowed: bool
    submit_allowed: bool = False
    scaleout_allowed: bool = False
    reasons: tuple[str, ...] = ()


def _utc_run_time_after(seconds: float) -> datetime:
    """Return a UTC first-run time for APScheduler interval jobs."""

    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _day0_first_delay_seconds(discovery: dict) -> float:
    """Stagger Day0 away from opening_hunt so equal-interval jobs do not race."""

    interval_seconds = float(discovery["day0_interval_min"]) * 60.0
    return OPENING_HUNT_FIRST_DELAY_SECONDS + (interval_seconds / 2.0)


def _defer_for_held_position_monitor(job_name: str) -> bool:
    """Return True when the held-position monitor should pre-empt discretionary jobs.

    The monitor itself is non-reentrant, but it must not globally stop the live
    money path. Targeted EDLI jobs (event reactor, continuous redecision, maker
    rest escalation, command recovery, targeted market-substrate warm, and
    market-channel refresh) are the continuous decision line and must keep
    running while held positions are monitored. Only broad/discretionary scans
    yield to the monitor bootstrap.
    """

    if job_name not in _HELD_POSITION_MONITOR_DEFER_JOBS:
        return False

    if _held_position_monitor_active.is_set():
        logger.info("%s deferred: held-position monitor active", job_name)
        return True
    if not _held_position_monitor_bootstrap_complete.is_set():
        logger.info(
            "%s deferred: first held-position monitor cycle has not completed",
            job_name,
        )
        return True
    return False


def _mark_held_position_monitor_complete() -> None:
    _held_position_monitor_active.clear()
    _held_position_monitor_bootstrap_complete.set()


def _live_execution_mode(edli_cfg: dict) -> str:
    mode = str(edli_cfg.get("live_execution_mode") or "legacy_cron")
    if mode not in LIVE_EXECUTION_MODES:
        raise ValueError(f"UNSUPPORTED_LIVE_EXECUTION_MODE:{mode}")
    return mode


def _harvester_should_register(live_execution_mode: str) -> bool:
    """Whether the settlement P&L + redeem-intent resolver (_harvester_cycle) is
    scheduled for this live-execution mode.

    守護 blocker (2026-06-03): the harvester was gated to ``legacy_cron`` ONLY, so
    in EDLI event-driven mode a FILLED position that rode to market settlement
    sat phase=active forever — the redeem pollers (its consumers) had nothing to
    consume, and capital stayed stuck on-chain (memory #56 "settled-target-still-
    active", reproducing on Shanghai cca68b44).

    The resolver is settlement-read-only: ``resolve_pnl_for_settled_markets`` READS VERIFIED
    settlement_outcomes (read-only) and writes only trade-side close + a durable
    REDEEM_INTENT_CREATED row. The actual on-chain redeem POST lives in the
    SEPARATELY-gated _redeem_submitter_cycle (already scheduled in all modes), whose
    adapter only broadcasts when autonomous redeem is enabled; scheduling the
    resolver adds ZERO new on-chain surface. The resolver also has its own
    ZEUS_HARVESTER_LIVE_ENABLED kill-switch (default OFF, no-op when unset).

    The shared predicate keeps the registration gate and the boot-recovery call in
    lockstep, and is the single source the antibody test asserts against.
    """
    return live_execution_mode in EDLI_EVENT_DRIVEN_MODES or live_execution_mode == "legacy_cron"


def _settings_section(name: str, default=None):
    source = settings._data if hasattr(settings, "_data") else settings
    if isinstance(source, dict):
        return source.get(name, default)
    try:
        return source[name]
    except KeyError:
        return default


def _edli_runtime_requested(edli_cfg: dict) -> bool:
    return any(bool(edli_cfg.get(flag, False)) for flag in EDLI_RUNTIME_FLAGS)


def _require_edli_flags(edli_cfg: dict, mode: str, flags: tuple[str, ...]) -> None:
    missing = [flag for flag in flags if not bool(edli_cfg.get(flag, False))]
    if missing:
        raise RuntimeError(f"{mode.upper()}_REQUIRES_{'_AND_'.join(missing).upper()}")


# ---------------------------------------------------------------------------
# W0-T2 boot-guards: calibration pin shape + staleness
# ---------------------------------------------------------------------------

def assert_calibration_pin_shape_is_dict(cfg: dict) -> None:
    """Fail-closed guard: calibration.pin.model_keys must be a dict or absent.

    Raises RuntimeError("MODEL_KEYS_MUST_BE_DICT: ...") when model_keys is
    present but not a dict (e.g. a JSON list from misconfigured settings).
    A list is silently skipped by manager.py:get_calibration_pin_config —
    all 137 pins would be dead config.  This guard makes the misconfiguration
    visible at boot instead of silent at runtime.
    """
    model_keys = (
        (cfg.get("calibration") or {})
        .get("pin", {})
        .get("model_keys")
    )
    if model_keys is not None and not isinstance(model_keys, dict):
        raise RuntimeError(
            f"MODEL_KEYS_MUST_BE_DICT: calibration.pin.model_keys is a "
            f"{type(model_keys).__name__}, must be dict"
        )


def assert_frozen_as_of_not_stale(
    cfg: dict,
    *,
    now: "datetime | None" = None,
) -> None:
    """WARN if calibration pin is older than 10 days; FATAL if older than 21 days.

    Honors env escape ZEUS_FREEZE_GUARD_DISABLE=1 (skips the FATAL).
    Pass `now` explicitly so tests can pin the reference time without
    calling datetime.now() at import.

    WARN threshold: 10 days.
    FATAL threshold: 21 days (unless ZEUS_FREEZE_GUARD_DISABLE=1 or the
    current live probability authority is the replacement qkernel path).
    """
    from datetime import datetime, timezone  # safe re-import: stdlib already loaded
    frozen_str: str | None = (
        (cfg.get("calibration") or {})
        .get("pin", {})
        .get("frozen_as_of")
    )
    if not frozen_str:
        return
    if now is None:
        now = datetime.now(tz=timezone.utc)
    try:
        frozen_dt = datetime.fromisoformat(frozen_str.replace("Z", "+00:00"))
    except ValueError:
        logger.warning(
            "assert_frozen_as_of_not_stale: cannot parse frozen_as_of=%r; skipping staleness check",
            frozen_str,
        )
        return
    age_days = (now - frozen_dt).total_seconds() / 86400.0
    if age_days > 21:
        if _replacement_qkernel_live_probability_authority_enabled(cfg):
            logger.warning(
                "FROZEN_AS_OF_STALE: calibration pin is %.0f days old (>21d threshold), "
                "but current live probability authority is replacement_0_1/qkernel; "
                "legacy Platt pin staleness is non-fatal for daemon boot",
                age_days,
            )
            return
        if os.environ.get("ZEUS_FREEZE_GUARD_DISABLE", "0") == "1":
            logger.warning(
                "FROZEN_AS_OF_STALE: calibration pin is %.0f days old (>21d threshold); "
                "FATAL suppressed by ZEUS_FREEZE_GUARD_DISABLE=1",
                age_days,
            )
        else:
            raise RuntimeError(
                f"FROZEN_AS_OF_STALE: calibration.pin.frozen_as_of is {age_days:.0f} days old "
                f"(>{21}d threshold). Update the pin or set ZEUS_FREEZE_GUARD_DISABLE=1 to skip."
            )
    elif age_days > 10:
        logger.warning(
            "FROZEN_AS_OF_STALE: calibration pin is %.0f days old (>10d warn threshold). "
            "Consider refreshing calibration.pin.frozen_as_of.",
            age_days,
        )


def _replacement_qkernel_live_probability_authority_enabled(cfg: dict) -> bool:
    """Return True when live entry probability is served by replacement qkernel.

    The calibration pin guards the legacy Platt/ENS calibration generation.  It
    must remain fatal when that legacy path is the live probability authority.
    When the live money path is explicitly the replacement_0_1 qkernel spine,
    a stale Platt pin is stale calibration inventory, not a daemon-start
    blocker; the replacement posterior freshness gates own live readiness.
    """

    edli = cfg.get("edli") if isinstance(cfg.get("edli"), dict) else {}
    flags = cfg.get("feature_flags") if isinstance(cfg.get("feature_flags"), dict) else {}
    return (
        bool(flags.get("qkernel_spine_enabled", False))
        and str(edli.get("live_execution_mode") or "") == "edli_live"
        and str(edli.get("reactor_mode") or "") == "live"
        and bool(edli.get("replacement_0_1_bayes_precision_fusion_enabled", False))
        and bool(edli.get("replacement_0_1_fused_q_shape_enabled", False))
    )


def assert_kelly_multiplier_within_correlated_ceiling(cfg: dict) -> None:
    """Fail-closed guard: sizing.kelly_multiplier must not exceed
    sizing.max_correlated_pct (the over-size door / iron rule 5 = ruin).

    WHY (MAJOR #1 antibody, P1 sizing fix a281ba14a2/efe91afdb5): the corr
    ceiling ``Σ corr-weighted stakes ≤ max_correlated_pct·B`` (the whole point
    of FIX A in money_path_adapters.evaluate_kelly) holds ONLY when the Kelly
    base cap ``kelly_multiplier`` is ≤ the corr ceiling ``max_correlated_pct``.
    The sized stake is
        s = (f*·m / f_cap_corr)·(f_cap_corr·B − committed),  f_cap_corr = max_correlated_pct
    and ``f*·m ≤ kelly_multiplier``. So ``f*·m / f_cap_corr ≤ 1`` — and Σ stays
    under the ceiling — ONLY while ``kelly_multiplier ≤ max_correlated_pct``.
    These are TWO INDEPENDENT config knobs (sizing.kelly_multiplier vs
    sizing.max_correlated_pct), equal at 0.25 today only by coincidence — the
    SAME coincidence that masked the original bug. A legal operator value of
    e.g. 0.5 silently breaches the ceiling (3 same-cycle same-city bets summed
    to $51 > $42.50 at B=170 in the critic repro, a 20% over-size) even with the
    INV-K3 single cap intact. ``_runtime_kelly_multiplier`` only rejects ≤ 0, so
    0.5 is accepted at runtime — this guard closes the door at boot instead.

    Raises RuntimeError("KELLY_MULT_EXCEEDS_CORR_CEILING: ...") when
    kelly_multiplier > max_correlated_pct. No-op when either key is absent
    (other config validation owns presence) or when within the ceiling.
    """
    sizing = cfg.get("sizing") or {}
    raw_mult = sizing.get("kelly_multiplier")
    raw_corr = sizing.get("max_correlated_pct")
    if raw_mult is None or raw_corr is None:
        # Presence is owned by Settings/config validation elsewhere; this guard
        # only enforces the RELATIONSHIP between the two knobs when both exist.
        return
    kelly_mult = float(raw_mult)
    max_corr = float(raw_corr)
    # Fail-closed on non-finite inputs: ``float('nan') > x`` and ``x > float('nan')``
    # are ALWAYS False, so a NaN (or an inf max_corr) would slip past the ``>``
    # comparison below and silently re-open the over-size door. Reject non-finite
    # values explicitly, consistent with the other fail-closed sizing inputs.
    if not math.isfinite(kelly_mult) or not math.isfinite(max_corr):
        raise RuntimeError(
            f"KELLY_MULT_EXCEEDS_CORR_CEILING (NON_FINITE): non-finite sizing "
            f"input — sizing.kelly_multiplier={kelly_mult}, "
            f"sizing.max_correlated_pct={max_corr}. A NaN/inf knob bypasses the "
            f"corr-ceiling comparison (the over-size door / iron rule 5 = ruin). "
            f"Both must be finite."
        )
    if kelly_mult > max_corr:
        raise RuntimeError(
            f"KELLY_MULT_EXCEEDS_CORR_CEILING: sizing.kelly_multiplier="
            f"{kelly_mult} must not exceed sizing.max_correlated_pct={max_corr} "
            f"— would breach the correlated-capital ceiling "
            f"(Σ corr-weighted stakes ≤ max_correlated_pct·B) = over-size = ruin "
            f"(iron rule 5). Lower kelly_multiplier to ≤ {max_corr} or raise "
            f"max_correlated_pct."
        )


# ---------------------------------------------------------------------------
# W0-T3: _run_boot_guards / _validate_boot — safe pre-restart smoke
# (2026-06-03)
# ---------------------------------------------------------------------------

def _run_boot_guards(raw_cfg: dict) -> list:
    """Run every pre-loop boot guard against *raw_cfg* (plain dict from Settings._data).

    Returns a list of (name: str, passed: bool, detail: str) tuples — one per
    guard.  Never raises; all exceptions are caught and surfaced in `detail`.

    Guards included (same set the real boot path runs, in the same order):
      1. assert_calibration_pin_shape_is_dict  — model_keys must be dict/absent
      2. assert_frozen_as_of_not_stale         — WARN>10d, FATAL>21d
      3. assert_kelly_multiplier_within_correlated_ceiling
                                               — kelly_multiplier ≤ max_correlated_pct
                                                 (over-size door / iron rule 5)

    Read-only: no DB writes, no network calls, no exclusive locks acquired.
    """
    from datetime import datetime, timezone

    results: list = []

    # Guard 1: calibration pin shape
    try:
        assert_calibration_pin_shape_is_dict(raw_cfg)
        results.append(("calibration_pin_shape", True, "model_keys absent or dict — OK"))
    except RuntimeError as exc:
        results.append(("calibration_pin_shape", False, str(exc)))
    except Exception as exc:  # pragma: no cover
        results.append(("calibration_pin_shape", False, f"unexpected: {exc}"))

    # Guard 2: frozen_as_of staleness
    try:
        assert_frozen_as_of_not_stale(raw_cfg, now=datetime.now(tz=timezone.utc))
        results.append((
            "frozen_as_of_staleness",
            True,
            "frozen_as_of absent, within 21d, or non-fatal under replacement qkernel authority — OK",
        ))
    except RuntimeError as exc:
        results.append(("frozen_as_of_staleness", False, str(exc)))
    except Exception as exc:  # pragma: no cover
        results.append(("frozen_as_of_staleness", False, f"unexpected: {exc}"))

    # Guard 3: kelly_multiplier ≤ max_correlated_pct (over-size door / iron rule 5)
    try:
        assert_kelly_multiplier_within_correlated_ceiling(raw_cfg)
        results.append((
            "kelly_mult_corr_ceiling",
            True,
            "kelly_multiplier ≤ max_correlated_pct (or absent) — corr ceiling intact",
        ))
    except RuntimeError as exc:
        results.append(("kelly_mult_corr_ceiling", False, str(exc)))
    except Exception as exc:  # pragma: no cover
        results.append(("kelly_mult_corr_ceiling", False, f"unexpected: {exc}"))

    return results


def _run_schema_guards() -> list:
    """Read-only DB schema guards for --validate-boot.

    Opens each canonical DB in read-only URI mode (shared read lock, no
    write/exclusive lock).  Safe alongside the live daemon because SQLite WAL
    permits concurrent readers without blocking writers.  Returns a list of
    (name, passed, detail).

    Checks:
      world_db_schema    — assert_schema_current + canonical table presence
      forecasts_db_schema — assert_schema_current_forecasts + live-required schema presence
      world_registry     — assert_db_matches_registry(WORLD)
      trade_registry     — assert_db_matches_registry(TRADE)
    """
    import sqlite3 as _sqlite3

    from src.state.db import (
        ZEUS_WORLD_DB_PATH,
        ZEUS_FORECASTS_DB_PATH,
        _zeus_trade_db_path,
        assert_schema_current,
        assert_schema_current_forecasts,
    )
    from src.state.table_registry import DBIdentity, assert_db_matches_registry

    results: list = []

    def _ro_conn(path):
        return _sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5.0)

    # World DB schema
    try:
        if not ZEUS_WORLD_DB_PATH.exists():
            raise FileNotFoundError(f"{ZEUS_WORLD_DB_PATH} does not exist")
        conn = _ro_conn(ZEUS_WORLD_DB_PATH)
        try:
            conn.execute("PRAGMA query_only = ON")
            assert_schema_current(conn)
            results.append(("world_db_schema", True, "schema structural check — OK"))
        finally:
            conn.close()
    except Exception as exc:
        results.append(("world_db_schema", False, str(exc)))

    # Forecasts DB schema
    try:
        if not ZEUS_FORECASTS_DB_PATH.exists():
            raise FileNotFoundError(f"{ZEUS_FORECASTS_DB_PATH} does not exist")
        conn = _ro_conn(ZEUS_FORECASTS_DB_PATH)
        try:
            conn.execute("PRAGMA query_only = ON")
            assert_schema_current_forecasts(conn)
            results.append(("forecasts_db_schema", True, "schema structural check — OK"))
        finally:
            conn.close()
    except Exception as exc:
        results.append(("forecasts_db_schema", False, str(exc)))

    # World registry
    try:
        if not ZEUS_WORLD_DB_PATH.exists():
            raise FileNotFoundError(f"{ZEUS_WORLD_DB_PATH} does not exist")
        conn = _ro_conn(ZEUS_WORLD_DB_PATH)
        try:
            assert_db_matches_registry(conn, DBIdentity.WORLD)
            results.append(("world_registry", True, "world table-set matches registry — OK"))
        finally:
            conn.close()
    except Exception as exc:
        results.append(("world_registry", False, str(exc)))

    # Trade registry. Keep --validate-boot aligned with the real src.main boot
    # path: world/trade registry are enforced there; forecasts registry is not.
    try:
        trade_db_path = _zeus_trade_db_path()
        if not trade_db_path.exists():
            raise FileNotFoundError(f"{trade_db_path} does not exist")
        conn = _ro_conn(trade_db_path)
        try:
            assert_db_matches_registry(conn, DBIdentity.TRADE)
            results.append(("trade_registry", True, "trade table-set matches registry — OK"))
        finally:
            conn.close()
    except Exception as exc:
        results.append(("trade_registry", False, str(exc)))

    return results


def _validate_boot(settings_path=None) -> int:
    """Run all read-only boot guards and print PASS/FAIL for each.

    Safe to invoke while the live daemon is running: opens no exclusive
    locks, acquires no ports, starts no threads, makes no network calls,
    performs no DB writes.

    Args:
        settings_path: Optional[str | Path] — override the settings.json path.
            Useful for testing with a temporary config file.

    Returns:
        0 if all checks pass, 1 if any fail.
    """
    from pathlib import Path as _Path

    from src.config import Settings as _Settings

    # Load settings — use override path when supplied (test / operator use)
    try:
        path = _Path(settings_path) if settings_path else None
        _s = _Settings(path=path)
        raw_cfg = _s._data if hasattr(_s, "_data") else _s
        print("PASS settings_load")
    except Exception as exc:
        print(f"FAIL settings_load: {exc}")
        return 1

    all_results = []

    # Boot guards (calibration pin shape + staleness)
    all_results.extend(_run_boot_guards(raw_cfg))

    # Read-only schema / registry guards
    all_results.extend(_run_schema_guards())

    # Report
    any_fail = False
    for name, passed, detail in all_results:
        tag = "PASS" if passed else "FAIL"
        print(f"{tag} {name}: {detail}")
        if not passed:
            any_fail = True

    return 1 if any_fail else 0


def _assert_edli_live_promotion_artifact(edli_cfg: dict) -> None:
    # The operator ARM kill-switch for edli_live (edli_live_operator_authorized) is the
    # ONLY honest gate here and is kept fail-closed.
    if not bool(edli_cfg.get("edli_live_operator_authorized", False)):
        raise RuntimeError("EDLI_LIVE_REQUIRES_EDLI_LIVE_OPERATOR_AUTHORIZED")
    # Wave-1 2026-06-12: the promotion-artifact + canary-fill-count verification that used
    # to run here is DELETED. It was promotion bureaucracy (an artifact file proving a
    # min canary fill count) the operator had already disabled via
    # edli_live_promotion_artifact_required=false. The operator arm above is the sole gate.
    return


@dataclass(frozen=True)
class OperatorArm:
    """FIX-2b (PR_SPEC.md §2) operator-arm token for the EDLI live-submit boundary.

    A capability token that is constructible ONLY through ``require_operator_arm``
    after asserting ``edli_live_operator_authorized is True``. The EDLI live submit
    adapter requires this token (regardless of mode — canary included) before any
    real venue submit. Absent the token, the live adapter's submit guard fails closed
    with ``OPERATOR_ARM_REQUIRED`` and main.py selects the no-submit adapter.

    Frozen + presence-typed so "armed without operator authorization" is
    unconstructable rather than merely flag-OFF. The token is applied EXACTLY at the
    EDLI boundary; the mainline executor (execute_final_intent / _live_order) never
    constructs this adapter and so is untouched by this gate.
    """

    authorized: bool = True


def require_operator_arm(edli_cfg: dict) -> "OperatorArm | None":
    """Mint an ``OperatorArm`` token IFF the operator has explicitly authorized live.

    Mirrors the strict assert pattern at ``_assert_edli_live_promotion_artifact``
    (main.py:567): only the literal ``True`` for ``edli_live_operator_authorized``
    authorizes — any other value (missing, False, truthy-non-bool) returns ``None``.
    Returning ``None`` (rather than raising) lets the live-builder selector degrade to
    the no-submit adapter fail-closed instead of crashing the daemon boot.
    """

    if edli_cfg.get("edli_live_operator_authorized") is True:
        return OperatorArm(authorized=True)
    return None


def _assert_edli_arm_gate_artifact(edli_cfg: dict) -> None:
    """Wave-1 2026-06-12: the full-live ARM-gate ARTIFACT requirement is DELETED.

    This used to demand a state/edli_arm_gate_artifact.json proving a positive
    settlement EV on the booted commit before edli_live. It was already de-bound by
    the operator (edli_arm_gate_artifact_required=false), and the artifact-proof gate is
    exactly the "circular promotion proof" bureaucracy the operator law forbids. The
    honest gate is the operator arm (edli_live_operator_authorized, asserted in
    _assert_edli_live_promotion_artifact) plus the runtime submit-chain proofs. This is
    now an intentional no-op; the dead ``edli_arm_gate_artifact_required`` key is removed.
    """
    return


# OPERATOR LAW (2026-06-04, Rule-4 antibody): the former
# ``_assert_edli_arm_requires_direction_gate`` two-key arm boot guard is DELETED.
# It coupled arming to the mainstream-enforcement flag — but mainstream is now
# OBSERVATIONAL / DISPLAY-ONLY and is NEVER a decision/arm input. The submit-time
# enforce branch it guarded was also deleted (event_reactor_adapter submit closure),
# so there is no "direction gate" left to require. Mainstream cannot block boot/arm.


def evaluate_edli_stage_readiness(
    *,
    stage: str,
    world_db_path: str | None = None,
    trade_db_path: str | None = None,
    forecasts_db_path: str | None = None,
    loaded_sha_file: str | None = None,
    promotion_artifact_path: str | None = None,
    source_health_json: str | None = None,
    status_json: str | None = None,
    max_age_seconds: int = 15 * 60,
) -> EdliStageReadiness:
    del trade_db_path, forecasts_db_path, promotion_artifact_path
    if stage in {"legacy_cron", "disabled"}:
        return EdliStageReadiness(stage=stage, status=EDLI_STAGE_PASS, live_entries_allowed=False)

    reasons: list[str] = []
    now = datetime.now(timezone.utc)
    if loaded_sha_file:
        reasons.extend(_edli_stage_loaded_sha_reasons(loaded_sha_file))
    conn = _edli_stage_world_connection(world_db_path)
    try:
        try:
            unresolved = _edli_stage_pending_reconcile_count(conn)
        except RuntimeError as exc:
            reasons.append(str(exc))
        else:
            if unresolved:
                reasons.append(f"EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:{unresolved}")
        try:
            reserved = _edli_stage_open_cap_reservation_count(conn)
        except RuntimeError as exc:
            reasons.append(str(exc))
        else:
            if reserved:
                reasons.append(f"EDLI_STAGE_LIVE_CAP_RESERVED:{reserved}")
        if source_health_json:
            reasons.extend(
                _edli_stage_fresh_file_reasons(
                    name="SOURCE_HEALTH",
                    path=source_health_json,
                    max_age_seconds=max_age_seconds,
                    now=now,
                )
            )
        if status_json:
            reasons.extend(
                _edli_stage_fresh_file_reasons(
                    name="STATUS_SUMMARY",
                    path=status_json,
                    max_age_seconds=max_age_seconds,
                    now=now,
                )
            )
    finally:
        conn.close()

    if reasons:
        return EdliStageReadiness(stage=stage, status=EDLI_STAGE_FAIL, live_entries_allowed=False, reasons=tuple(reasons))
    return EdliStageReadiness(
        stage=stage,
        status=EDLI_STAGE_PASS,
        live_entries_allowed=True,
        submit_allowed=True,
        scaleout_allowed=True,
    )


def _assert_edli_stage_readiness(edli_cfg: dict) -> EdliStageReadiness:
    stage = _live_execution_mode(edli_cfg)
    if stage in {"legacy_cron", "disabled"}:
        return EdliStageReadiness(stage=stage, status=EDLI_STAGE_PASS, live_entries_allowed=False)
    _require_stage_file_paths(edli_cfg, stage)
    report = evaluate_edli_stage_readiness(
        stage=stage,
        world_db_path=str(_settings_section("state", {}).get("world_db", "")) if isinstance(_settings_section("state", {}), dict) else None,
        trade_db_path=str(_settings_section("state", {}).get("trade_db", "")) if isinstance(_settings_section("state", {}), dict) else None,
        forecasts_db_path=str(_settings_section("state", {}).get("forecasts_db", "")) if isinstance(_settings_section("state", {}), dict) else None,
        loaded_sha_file=_resolve_edli_stage_runtime_path(edli_cfg.get("edli_stage_loaded_sha_file")),
        promotion_artifact_path=str(edli_cfg.get("edli_live_promotion_artifact_path") or ""),
        source_health_json=_resolve_edli_stage_runtime_path(edli_cfg.get("edli_stage_source_health_json")),
        status_json=_resolve_edli_stage_runtime_path(edli_cfg.get("edli_stage_status_json")),
        max_age_seconds=int(edli_cfg.get("edli_stage_readiness_max_age_seconds", 15 * 60)),
    )
    if stage == "edli_live":
        # Wave-2 item 5: the canary boot-resilience logic (crash-loop antibody +
        # status-summary deferral) is the live-mode readiness path now that canary
        # is collapsed into edli_live. Scaleout is unconditionally permitted for the
        # single live mode (the canary scaleout=False qualifying-lane semantics are
        # dead — operator arm is the sole submit gate).
        deferred = [
            reason for reason in (report.reasons or ())
            if reason.startswith(_EDLI_LIVE_BOOT_DEFERRED_REASON_PREFIXES)
        ]
        blocking = [
            reason for reason in (report.reasons or ())
            if not reason.startswith(_EDLI_LIVE_BOOT_DEFERRED_REASON_PREFIXES)
        ]
        risk_reasons = [reason for reason in blocking if reason.startswith(EDLI_STAGE_RISK_REASON_PREFIXES)]
        if report.status not in {EDLI_STAGE_PASS, EDLI_STAGE_WAITING} and blocking:
            # BOOT CRASH-LOOP ANTIBODY (2026-06-12, 3 incidents same day): when
            # the ONLY blockers are stuck post-submit unknowns + their cap
            # reservations, run the operator-ratified authenticated-absence
            # resolution automatically (same contract as the manual script —
            # refuses on any real venue exposure) and re-evaluate ONCE
            # (re-entry marker forbids a second attempt). Any other blocker,
            # a refusal, or a venue-read failure falls through to the
            # original fail-closed raise.
            if not edli_cfg.get("_boot_auto_resolution_reentry"):
                from src.execution.edli_absence_resolver import (
                    boot_auto_resolve_stuck_unknowns,
                )

                if boot_auto_resolve_stuck_unknowns(list(blocking)):
                    return _assert_edli_stage_readiness(
                        {**edli_cfg, "_boot_auto_resolution_reentry": True}
                    )
            raise RuntimeError("EDLI_LIVE_READINESS_FAIL:" + ",".join(blocking or (report.status,)))
        if risk_reasons:
            raise RuntimeError("EDLI_LIVE_READINESS_FAIL:" + ",".join(risk_reasons))
        if deferred:
            logger.warning(
                "EDLI live boot: status_summary freshness is deferred "
                "until the scheduler emits its first genuine cycle pulse: %s",
                ", ".join(deferred),
            )
            if report.status not in {EDLI_STAGE_PASS, EDLI_STAGE_WAITING}:
                return EdliStageReadiness(
                    stage=stage,
                    status=EDLI_STAGE_WAITING,
                    live_entries_allowed=True,
                    submit_allowed=True,
                    scaleout_allowed=True,
                    reasons=tuple(deferred),
                )
        if report.submit_allowed is not True:
            raise RuntimeError("EDLI_LIVE_SUBMIT_NOT_ALLOWED")
        if report.status != EDLI_STAGE_PASS or report.scaleout_allowed is not True:
            raise RuntimeError("EDLI_LIVE_SCALEOUT_READINESS_FAIL:" + ",".join(report.reasons or (report.status,)))
        return report
    return report


def _require_stage_file_paths(edli_cfg: dict, stage: str) -> None:
    missing = [
        key
        for key in REQUIRED_STAGE_FILES_BY_MODE.get(stage, ())
        if not str(edli_cfg.get(key) or "").strip()
    ]
    if missing:
        raise RuntimeError(f"{stage.upper()}_REQUIRES_STAGE_EVIDENCE_FILES:{','.join(missing)}")


def _resolve_edli_stage_runtime_path(raw_path: object) -> str:
    path_text = str(raw_path or "").strip()
    if not path_text:
        return ""
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return str(path)
    from src.config import RUNTIME_ROOT, STATE_DIR

    if path.parts and path.parts[0] == "state":
        return str(STATE_DIR.joinpath(*path.parts[1:]))
    return str(RUNTIME_ROOT / path)


def _edli_stage_pending_reconcile_count(conn) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM edli_live_order_projection
            WHERE pending_reconcile = 1
            """
        ).fetchone()
    except Exception as exc:
        raise RuntimeError(f"EDLI_STAGE_PENDING_RECONCILE_QUERY_FAILED:{type(exc).__name__}") from exc
    return int(row[0] if row else 0)


def _edli_stage_world_connection(world_db_path: str | None):
    if world_db_path:
        import sqlite3

        db_path = Path(world_db_path)
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    return get_world_connection_read_only()


def _edli_stage_loaded_sha_reasons(path: str) -> list[str]:
    file_path = Path(path)
    if not file_path.exists():
        return [f"EDLI_STAGE_LOADED_SHA_MISSING:{path}"]
    try:
        payload = json.loads(file_path.read_text())
    except json.JSONDecodeError:
        return [f"EDLI_STAGE_LOADED_SHA_INVALID_JSON:{path}"]
    loaded_sha = str(payload.get("loaded_sha") or payload.get("boot_sha") or payload.get("current_sha") or "").strip()
    expected_sha = str(_BOOT_STATE.get("sha") or "").strip()
    if expected_sha and loaded_sha and loaded_sha != expected_sha:
        return [f"EDLI_STAGE_LOADED_SHA_MISMATCH:loaded={loaded_sha}:expected={expected_sha}"]
    if not loaded_sha:
        return ["EDLI_STAGE_LOADED_SHA_MISSING_VALUE"]
    return []


def _edli_stage_open_cap_reservation_count(conn) -> int:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM edli_live_cap_usage
            WHERE reservation_status = 'RESERVED'
            """
        ).fetchone()
    except Exception as exc:
        raise RuntimeError(f"EDLI_STAGE_OPEN_CAP_QUERY_FAILED:{type(exc).__name__}") from exc
    return int(row[0] if row else 0)


def _edli_stage_fresh_file_reasons(*, name: str, path: str, max_age_seconds: int, now: datetime) -> list[str]:
    file_path = Path(path)
    if not file_path.exists():
        return [f"EDLI_STAGE_{name}_MISSING:{path}"]
    try:
        payload = json.loads(file_path.read_text())
    except json.JSONDecodeError:
        return [f"EDLI_STAGE_{name}_INVALID_JSON:{path}"]
    stamp = payload.get("generated_at") or payload.get("updated_at") or payload.get("observed_at") or payload.get("captured_at")
    if not stamp:
        return [f"EDLI_STAGE_{name}_STALE:missing_timestamp"]
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return [f"EDLI_STAGE_{name}_STALE:invalid_timestamp"]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = (now - parsed.astimezone(timezone.utc)).total_seconds()
    if age < -EDLI_STAGE_FRESH_FILE_FUTURE_SKEW_TOLERANCE_SECONDS:
        return [f"EDLI_STAGE_{name}_STALE:{age:.0f}s"]
    age = max(0.0, age)
    if age > max_age_seconds:
        return [f"EDLI_STAGE_{name}_STALE:{age:.0f}s"]
    return []


def _assert_edli_live_scope(edli_cfg: dict) -> None:
    scope = str(edli_cfg.get("edli_live_scope") or "forecast_plus_day0")
    if scope not in EDLI_LIVE_SCOPES:
        raise RuntimeError(f"UNSUPPORTED_EDLI_LIVE_SCOPE:{scope}")


def _build_edli_status_pulse(
    *,
    started_at: str,
    completed_at: str,
    candidates: int,
    processed: int,
    proof_accepted: int,
    rejected: int,
    retried: int,
    dead_lettered: int,
    rejection_reason_counts: dict,
    submit_disabled_effective_mode: bool,
    live_submit_attempts: int,
    live_venue_acks: int = 0,
) -> dict:
    """Build the EDLI reactor status pulse dict.

    FIX-4 (P2, 2026-06-09): separates proof_accepted from live_submit_attempts.
    ``proof_accepted`` counts events whose money-path proof was accepted (i.e.,
    final intent was built). ``live_submit_attempts`` counts ONLY actual venue
    submit calls made this cycle — 0 when the live-submit lane is not selected. Dashboards
    MUST NOT treat proof_accepted as evidence of a venue interaction.

    ``live_venue_acks`` counts venue responses where ``venue_ack_received`` is
    True (successful ACK from the exchange).  Always <= live_submit_attempts.
    """
    return {
        "mode": "edli_event_reactor",
        "started_at": started_at,
        "completed_at": completed_at,
        "candidates": candidates,
        "candidates_evaluated": candidates,
        "processed": processed,
        "proof_accepted": proof_accepted,
        "final_intents_built": proof_accepted,
        "submit_attempts": live_submit_attempts,
        "venue_acks": live_venue_acks,
        "no_trades": rejected + retried + dead_lettered,
        "rejected": rejected,
        "retried": retried,
        "dead_lettered": dead_lettered,
        "rejection_reason_counts": rejection_reason_counts,
        "top_no_trade_reasons": rejection_reason_counts,
        "deterministic_rejections": (
            {"real_order_submit_disabled": proof_accepted}
            if submit_disabled_effective_mode and proof_accepted > 0
            else {}
        ),
    }


def _assert_calibration_coverage_contract(edli_cfg: dict) -> None:  # noqa: ARG001
    """Legacy bias/Platt calibration-coverage guard — now an unconditional no-op.

    SINGLE TRUTH (bias-maze strip 2026-06-17): the live q runs on the raw precise
    multi-model fused center (qkernel_spine / EMOS / honest-raw substrate); the legacy
    per-city bias+Platt calibration path is never consumed by a live decision. The legacy
    coverage contract is therefore permanently NOT APPLICABLE. It is deliberately a logged
    no-op rather than a raise: forcing the old guard to run would have RAISED in armed mode
    on missing VERIFIED bias rows (a spurious boot-block under the single-truth law).
    """
    logger.info(
        "CALIBRATION_COVERAGE_NOT_APPLICABLE: legacy bias/Platt coverage guard removed "
        "(single-truth raw multi-model center; no live bias/Platt consumer)"
    )


def _assert_live_execution_mode_contract(edli_cfg: dict) -> str:
    mode = _live_execution_mode(edli_cfg)
    _assert_edli_live_scope(edli_cfg)
    expected_reactor_mode = REACTOR_MODE_BY_LIVE_STAGE[mode]
    reactor_mode = str(edli_cfg.get("reactor_mode") or "disabled")
    if reactor_mode != expected_reactor_mode:
        raise RuntimeError(f"{mode.upper()}_REQUIRES_REACTOR_MODE_{expected_reactor_mode.upper()}")
    if mode == "legacy_cron" and _edli_runtime_requested(edli_cfg):
        raise RuntimeError("EDLI_RUNTIME_CONFLICTS_WITH_LEGACY_CRON")
    if mode == "disabled" and _edli_runtime_requested(edli_cfg):
        raise RuntimeError("EDLI_RUNTIME_CONFLICTS_WITH_DISABLED_MODE")
    if mode in EDLI_EVENT_DRIVEN_MODES:
        _require_edli_flags(edli_cfg, mode, ("enabled", "event_writer_enabled", "forecast_snapshot_trigger_enabled"))
    if mode == "edli_live":
        _require_edli_flags(
            edli_cfg,
            mode,
            (
                "market_channel_ingestor_enabled",
                "edli_user_channel_reconcile_enabled",
                "real_order_submit_enabled",
                "durable_submit_outbox_enabled",
            ),
        )
    if mode == "edli_live":
        _assert_edli_live_promotion_artifact(edli_cfg)
    if mode == "edli_live":
        _assert_edli_arm_gate_artifact(edli_cfg)
        # OPERATOR LAW (2026-06-04): the former two-key arm direction-gate guard is
        # DELETED — mainstream is observational/display-only and is NEVER a decision/arm
        # input, so there is no mainstream-enforcement key to require at arm time.
    return mode


def _scheduler_job(job_name: str):
    """Decorator: every scheduler.add_job(fn, ...) target in this module must
    wear this (B047 — see SCAFFOLD_B047_scheduler_observability.md).

    Wraps fn so that:
      - success → ``scheduler_jobs_health.json[job_name].status = OK`` + timestamp
      - exception → logged with traceback + ``status = FAILED`` + failure_reason

    Never re-raises (fail-open per K2 design in 27bedbd: daemon must keep
    running; OpenClaw supervisor relies on heartbeat). ``_write_heartbeat``
    is the sole scheduler target exempt from this decorator (it IS the
    coarse observability channel).
    """

    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                _write_scheduler_health(job_name, failed=False)
                return result
            except Exception as exc:
                logger.error("%s failed: %s", job_name, exc, exc_info=True)
                _write_scheduler_health(job_name, failed=True, reason=str(exc))

        return _wrapper

    return _decorator


@_scheduler_job("run_mode")
def _run_mode(mode: DiscoveryMode):
    """Wrapper with error handling and cycle lock for scheduler.

    Dual-signal observability: this wrapper writes to ``status_summary.json``
    via status_summary.write_status (the legacy mode-specific channel) AND
    the ``@_scheduler_job`` decorator independently writes to
    ``scheduler_jobs_health.json`` (B047 uniform channel). Non-conflicting.
    """
    acquired = _cycle_lock.acquire(blocking=False)
    if not acquired:
        logger.warning("%s skipped: another cycle is still running", mode.value)
        _write_scheduler_health(
            f"run_mode:{mode.value}",
            failed=False,
            skipped=True,
            skip_reason="cycle_lock_busy",
        )
        return
    try:
        from src.engine.cycle_runner import run_cycle

        _write_scheduler_health(
            f"run_mode:{mode.value}",
            failed=False,
            started=True,
        )
        summary = run_cycle(mode)
        logger.info("%s: %s", mode.value, summary)
        _write_scheduler_health(
            f"run_mode:{mode.value}",
            failed=False,
            extra=_run_mode_business_liveness(mode, summary),
        )
    except Exception as e:
        logger.error("%s failed: %s", mode.value, e, exc_info=True)
        _write_scheduler_health(
            f"run_mode:{mode.value}",
            failed=True,
            reason=str(e),
        )
        try:
            from src.observability.status_summary import write_status

            write_status(
                {
                    "mode": mode.value,
                    "failed": True,
                    "failure_reason": str(e),
                }
            )
        except Exception:
            logger.debug("failed to write error status for %s", mode.value, exc_info=True)
    finally:
        _cycle_lock.release()


def _run_mode_business_liveness(mode: DiscoveryMode, summary: object) -> dict:
    """Extract mode-specific business-plane counters from a cycle summary."""

    if not isinstance(summary, dict):
        return {
            "last_completed_at": datetime.now(timezone.utc).isoformat(),
            "last_mode": mode.value,
        }
    frontier = summary.get("money_path_frontier")
    if not isinstance(frontier, dict):
        frontier = {}
    return {
        "last_completed_at": datetime.now(timezone.utc).isoformat(),
        "last_mode": mode.value,
        "last_candidates": int(summary.get("candidates", 0) or 0),
        "last_no_trades": int(summary.get("no_trades", 0) or 0),
        "last_final_intent_built": int(summary.get("final_intents_built", 0) or 0),
        "last_submit_attempts": int(summary.get("submit_attempts", 0) or 0),
        "last_venue_acks": int(summary.get("venue_acks", 0) or 0),
        "last_entry_orders_submitted": int(summary.get("entry_orders_submitted", 0) or 0),
        "last_terminal_classification": str(
            frontier.get("terminal_classification") or ""
        ),
    }






@_scheduler_job("settlement_guard_report")
def _settlement_guard_report_tick() -> None:
    """Daily 守護 settlement-guard scorecard (operator-approved Phase-2 organ).

    Read-only: grades every executed fill against the spine-graded VERIFIED
    settlement truth (via grade_receipt — the ONE Direction-Law truth function),
    computes the after-cost win-rate vs the 51% GOAL bar with a binomial CI,
    flags SUSPEND_CANDIDATE cities (report-only), and writes:
      - state/settlement_guard_report.json (machine)
      - docs/evidence/settlement_guard/<date>_settlement_guard.md (human)
    plus a one-line INFO summary the operator sees in this daemon's log daily.

    Idempotent + cheap (one read-only pass over graded tables); n=0 produces an
    honest report, never a crash. Import is local to keep src.main import-light.
    """
    from src.analysis.settlement_guard_report import run_settlement_guard_report

    run_settlement_guard_report()


@_scheduler_job("settlement_skill_attribution")
def _settlement_skill_attribution_tick() -> None:
    """Grade every SETTLED position into a skill category (operator 2026-06-12 law).

    A profitable settlement is NOT proof of skill. This tick grades each settled
    position into SKILL_WIN / LUCKY_WIN / SKILL_LOSS / MISCALIBRATED_LOSS /
    STALE_DECISION / UNATTRIBUTABLE_Q_MISSING by comparing our position + the
    IMMUTABLE decision-time q (ActionableTradeCertificate) + the freshest
    settlement-eve posterior + the settled outcome + market price. A LUCKY_WIN
    (won but our own freshest data disagreed — the Denver-if-92 shape) counts as a
    MISS so a lucky win can no longer masquerade as system health. A position
    whose immutable decision-q certificate is unresolvable grades
    UNATTRIBUTABLE_Q_MISSING (never SKILL/LUCK). The skill win-rate = SKILL_WIN /
    (SKILL_WIN + LUCKY_WIN + SKILL_LOSS + MISCALIBRATED_LOSS).

    Runs after the settlement harvesting tick (settlement truth already landed).
    Idempotent per position (UNIQUE(position_id)); backfills every
    historically-settled position on first run. Sole writer of
    settlement_attribution. Import local to keep src.main import-light.
    """
    if _edli_reactor_active() or _edli_redecision_screen_lock.locked():
        logger.info(
            "settlement_skill_attribution skipped: live money-path cycle active"
        )
        return
    from src.analysis.settlement_skill_attribution import run_settlement_skill_attribution

    try:
        stats = run_settlement_skill_attribution()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if (
            "database is locked" in message
            or "database table is locked" in message
            or "database is busy" in message
        ):
            logger.warning(
                "settlement_skill_attribution deferred: database writer busy"
            )
            return
        raise
    logger.info(
        "settlement_skill_attribution: graded=%s skill_win_rate=%s by_category=%s",
        stats.get("graded"), stats.get("skill_win_rate"), stats.get("by_category"),
    )


# ---------------------------------------------------------------------------
# F14 + F16 cascade-liveness pollers (2026-05-16, SCAFFOLD §K v5)
# ---------------------------------------------------------------------------
# Per architecture/cascade_liveness_contract.yaml: each state-machine table
# with *_INTENT_CREATED / *_REQUESTED rows MUST have a registered scheduler
# poller. Without these, settlement_commands rows enqueued by
# harvester_pnl_resolver would sit forever (the F14 SEV-0 defect documented
# in docs/archive/2026-Q2/task_2026-05-16_deep_alignment_audit/).
#
# _redeem_submitter_cycle: polls REDEEM_INTENT_CREATED, calls submit_redeem
#   (which transitions stub-deferred rows to REDEEM_OPERATOR_REQUIRED per
#   SCAFFOLD §K.3; operator then completes via scripts/operator_record_redeem.py).
# _redeem_reconciler_cycle: polls REDEEM_TX_HASHED, calls reconcile_pending_redeems
#   (no-op until web3 is wired — operator-recorded tx_hash sits in TX_HASHED
#   until PR-I.5 follow-up).
# Wrap cycle functions (2026-05-19 auto-wrap-post-redeem):
# _wrap_intent_creator_cycle: reads Safe USDC.e balance; inserts WRAP_REQUESTED
#   if balance > threshold and no non-terminal WRAP row exists.
# _wrap_submitter_cycle: picks up WRAP_REQUESTED → submits APPROVE tx;
#   picks up WRAP_APPROVED → submits WRAP tx; advances state on success.
# _wrap_reconciler_cycle: polls chain for tx receipts; advances
#   WRAP_APPROVE_TX_HASHED → WRAP_APPROVED and WRAP_TX_HASHED → WRAP_CONFIRMED;
#   on WRAP_CONFIRMED calls adapter.update_balance_allowance() to refresh CLOB ledger.

def _wrap_proceeds_same_tick(creds: dict, adapter: Any) -> None:
    """Proceeds-driven wrap: leave ZERO unwrapped USDC.e after this tick.

    STRUCTURAL FIX (operator directive 2026-06-09): redemption proceeds land as
    USDC.e at the Safe, but the periodic wrap state machine (intent creator /
    submitter / reconciler, 5-min ticks) advanced one step per tick — fresh
    proceeds sat unwrapped for up to ~25 minutes ("Confirm pending deposit").
    This helper is called from the SAME redeem ticks that broadcast/confirm
    redemptions and synchronously drives the full APPROVE→WRAP chain via
    wrap_proceeds_now. Fail-soft: any failure logs and defers to the periodic
    wrap jobs (which remain as the resume/backstop path).

    P0-2 (d) shared logical lock: takes the single `wrap_state_machine` lock
    shared by _wrap_intent_creator_cycle / _wrap_submitter_cycle /
    _wrap_reconciler_cycle / this same-tick path. With ONE lock, no two of them
    ever submit a Safe tx or transition a wrap row concurrently — so a stale
    snapshot can never drive a duplicate on-chain tx (burned gas) against a row
    another worker is advancing. The CAS in _transition is the structural
    anti-reversion guard; this lock is the duplicate-submission guard.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.execution.wrap_unwrap_commands import wrap_proceeds_now
    from src.state.db import get_world_connection

    try:
        from eth_account import Account as _Account
        signer_eoa = _Account.from_key(creds["private_key"]).address
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("wrap_proceeds_same_tick: signer derivation failed: %s", exc)
        return
    with acquire_lock("wrap_state_machine") as acquired:
        if not acquired:
            logger.info("wrap_proceeds_same_tick skipped_lock_held")
            return
        wconn = get_world_connection()
        try:
            summary = wrap_proceeds_now(
                wconn, adapter, creds["funder_address"], signer_eoa,
            )
            if summary.get("enqueued") or summary.get("confirmed") or summary.get("failed"):
                logger.info(
                    "wrap_proceeds_same_tick: balance_before=%s enqueued=%s "
                    "confirmed=%s failed=%s pending=%s",
                    summary.get("balance_micro_before"), summary.get("enqueued"),
                    summary.get("confirmed"), summary.get("failed"),
                    summary.get("pending"),
                )
        except Exception as exc:  # noqa: BLE001 — fail-soft, periodic jobs resume
            logger.warning("wrap_proceeds_same_tick failed (fail-soft): %s", exc)
        finally:
            wconn.close()


# One-shot guard so the redeem-submitter law banner logs once per process, not
# every scheduler tick (operator law 2026-06-10 — redeem submission forbidden).
_REDEEM_SUBMITTER_LAW_LOGGED = False












def _assert_cascade_liveness_contract(scheduler) -> None:
    """Boot-time mirror of tests/test_cascade_liveness_contract.py.

    Fail-closed: refuses to start the daemon if any required poller from
    architecture/cascade_liveness_contract.yaml is missing from scheduler.
    Guards against accidental edits that delete a job registration without
    updating the contract (or vice versa).
    """
    import pathlib
    import yaml

    contract_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "architecture"
        / "cascade_liveness_contract.yaml"
    )
    if not contract_path.exists():
        # Defensive: if contract YAML absent, skip — but log loudly so the
        # operator notices. Antibody test will still catch this in CI.
        logger.error(
            "_assert_cascade_liveness_contract: %s missing; skipping boot check",
            contract_path,
        )
        return
    contract = yaml.safe_load(contract_path.read_text())
    job_ids = {j.id for j in scheduler.get_jobs()}
    missing: list[tuple[str, str]] = []
    for sm in contract.get("state_machines", []) or []:
        for poller in sm.get("required_pollers", []) or []:
            owner_daemon = str(poller.get("owner_daemon") or "").strip()
            owner = str(poller.get("owner") or "")
            if owner_daemon and owner_daemon != "main":
                continue
            if not owner_daemon and "post_trade_capital" in owner:
                continue
            if poller["id"] not in job_ids:
                missing.append((sm["table"], poller["id"]))
    if missing:
        raise SystemExit(
            f"FATAL: cascade_liveness_contract violation: missing pollers "
            f"{missing!r}. Refusing to boot. Either register the job in "
            f"src/main.py OR remove the contract entry in "
            f"architecture/cascade_liveness_contract.yaml."
        )


def run_single_cycle():
    """Run one complete cycle of all modes. For testing, not production."""
    logger.info("=== SINGLE CYCLE TEST ===")
    for mode in DiscoveryMode:
        logger.info("[%s]...", mode.value)
        _run_mode(mode)
    _harvester_cycle()
    logger.info("=== SINGLE CYCLE COMPLETE ===")


_heartbeat_fails = 0

def _write_heartbeat() -> None:
    """Write a heartbeat JSON to state/ every 60s so operators can detect silent crashes."""
    global _heartbeat_fails
    from src.config import state_path
    path = state_path("daemon-heartbeat.json")
    try:
        import json
        payload = {
            "alive": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": get_mode(),
            "pid": os.getpid(),
            "process": "src.main",
        }
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
        # Keep operator status freshness independent of the long chain/monitor job.
        # The pulse is derived/read-only visibility; failure must not mask heartbeat.
        try:
            from src.observability.status_summary import write_cycle_pulse
            write_cycle_pulse({"mode": "heartbeat_pulse", "heartbeat": True})
        except Exception:
            pass
        # Relationship-F: surface composite live-health on every heartbeat cycle
        try:
            from src.control.live_health import compute_composite_live_health
            compute_composite_live_health()
        except Exception:
            pass  # observability write must never mask heartbeat success
        _heartbeat_fails = 0
    except Exception as exc:
        _heartbeat_fails += 1
        logger.error("Heartbeat write failed (%d/3): %s", _heartbeat_fails, exc)
        try:
            from src.observability.status_summary import write_status
            write_status({
                "daemon_health": "FAULT",
                "failure_reason": f"heartbeat_write_failed: {exc}"
            })
        except Exception:
            pass

        if _heartbeat_fails >= 3:
            logger.critical("FATAL: Heartbeat failed 3 consecutive times. Halting daemon to prevent zombie state.")
            os._exit(1)


_venue_heartbeat_supervisor = None
_venue_heartbeat_adapter = None
_venue_heartbeat_thread = None
_edli_reactor_active_lock = threading.Lock()
_venue_background_maintenance_lock = threading.Lock()
_last_venue_background_maintenance_attempt_at = None
VENUE_BACKGROUND_MAINTENANCE_SECONDS = 30.0
_collateral_background_refresh_lock = threading.Lock()
_last_collateral_heartbeat_refresh_attempt_at = None
COLLATERAL_HEARTBEAT_REFRESH_SECONDS = 30.0

# Continuous re-decision P2 (resurrection 2026-06-12): the cheap-screen job's advisory lock (so
# overlapping triggers never double-run the screen) and the PROCESS-GLOBAL act-once-per-edge dedup
# state (held across cycles so a bare price wiggle does not re-fire — R6). Plain dict mutated only
# under the lock-held job; no cross-thread contention beyond the advisory acquire.
_edli_redecision_screen_lock = threading.Lock()
_REDECISION_REST_PULL_EXPIRY_GRACE_SECONDS = 20 * 60
_REDECISION_PENDING_EXPIRY_GRACE_SECONDS = 300
_REDECISION_FRESH_SCREEN_SUPERSEDE_GRACE_SECONDS = 75
_edli_redecision_acted_state: dict = {}


def _venue_heartbeat_mode() -> str:
    return os.environ.get("ZEUS_VENUE_HEARTBEAT_MODE", "internal").strip().lower()


def _external_venue_heartbeat_enabled() -> bool:
    return _venue_heartbeat_mode() == "external"


def _edli_reactor_active() -> bool:
    return _edli_reactor_active_lock.locked()


def _edli_reactor_pending_backlog_exists(*, conn_factory=None) -> bool:
    """Return True when EDLI has pending opportunity events that should drain first."""

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled") or not edli_cfg.get("event_writer_enabled"):
        return False
    owns_connection = conn_factory is None
    conn = None
    try:
        from src.state.db import get_world_connection

        conn = (conn_factory or get_world_connection)()
        row = conn.execute(
            """
            SELECT 1
              FROM opportunity_event_processing
             WHERE consumer_name = 'edli_reactor_v1'
               AND processing_status = 'pending'
             LIMIT 1
            """
        ).fetchone()
        return row is not None
    except Exception as exc:  # noqa: BLE001 - fail-open; heartbeat must stay alive.
        logger.warning("EDLI pending backlog check failed open: %r", exc)
        return False
    finally:
        if owns_connection and conn is not None:
            conn.close()


def _ws_gap_m5_reconcile_required() -> bool:
    """Return True when venue maintenance is required to clear the WS submit latch."""

    try:
        from src.control.ws_gap_guard import summary as _ws_gap_summary

        return bool(_ws_gap_summary().get("m5_reconcile_required", False))
    except Exception as exc:  # noqa: BLE001 - heartbeat maintenance must stay alive.
        logger.warning("WS gap M5 requirement check failed closed: %r", exc)
        return False


def _configure_external_venue_heartbeat_supervisor_if_needed() -> None:
    from src.control.heartbeat_supervisor import (
        ExternalHeartbeatSupervisor,
        configure_global_supervisor,
        get_global_supervisor,
    )

    supervisor = get_global_supervisor()
    if isinstance(supervisor, ExternalHeartbeatSupervisor):
        return
    configure_global_supervisor(ExternalHeartbeatSupervisor())


def _ensure_venue_read_side_adapter():
    """Install the venue adapter used by non-heartbeat read-side maintenance."""

    global _venue_heartbeat_adapter
    if _venue_heartbeat_adapter is None:
        from src.data.polymarket_client import PolymarketClient

        _venue_heartbeat_adapter = PolymarketClient()._ensure_v2_adapter()
    return _venue_heartbeat_adapter


def _refresh_global_collateral_snapshot_if_due(
    adapter,
    *,
    now: datetime | None = None,
) -> bool:
    """Keep live collateral truth fresh without polling every heartbeat tick."""

    if adapter is None:
        return False
    if not _collateral_background_refresh_lock.acquire(blocking=False):
        return False
    try:
        from src.state.collateral_ledger import get_global_ledger

        ledger = get_global_ledger()
        if ledger is None:
            return False
        global _last_collateral_heartbeat_refresh_attempt_at
        current = now or datetime.now(timezone.utc)
        last_attempt = _last_collateral_heartbeat_refresh_attempt_at
        if last_attempt is not None:
            if last_attempt.tzinfo is None:
                last_attempt = last_attempt.replace(tzinfo=timezone.utc)
            attempt_age_seconds = (
                current - last_attempt.astimezone(timezone.utc)
            ).total_seconds()
            if 0 <= attempt_age_seconds < COLLATERAL_HEARTBEAT_REFRESH_SECONDS:
                return False
        snapshot = ledger.snapshot()
        captured_at = snapshot.captured_at
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        age_seconds = (current - captured_at.astimezone(timezone.utc)).total_seconds()
        if (
            snapshot.authority_tier != "DEGRADED"
            and age_seconds >= 0
            and age_seconds < COLLATERAL_HEARTBEAT_REFRESH_SECONDS
        ):
            return False
        _last_collateral_heartbeat_refresh_attempt_at = current
        refreshed = ledger.refresh(adapter)
        logger.info(
            "CollateralLedger heartbeat refresh: authority=%s captured_at=%s "
            "reserved_pusd_micro=%s reserved_token_count=%s",
            refreshed.authority_tier,
            refreshed.captured_at.isoformat(),
            refreshed.reserved_pusd_for_buys_micro,
            len(refreshed.reserved_tokens_for_sells),
        )
        return True
    except Exception as exc:
        logger.warning("CollateralLedger heartbeat refresh failed closed: %s", exc)
        return False
    finally:
        _collateral_background_refresh_lock.release()


def _global_collateral_snapshot_needs_refresh(
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether collateral is too stale/degraded to defer behind backlog."""

    try:
        from src.state.collateral_ledger import get_global_ledger

        ledger = get_global_ledger()
        if ledger is None:
            return False
        snapshot = ledger.snapshot()
        if snapshot.authority_tier == "DEGRADED":
            return True
        current = now or datetime.now(timezone.utc)
        captured_at = snapshot.captured_at
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        age_seconds = (current - captured_at.astimezone(timezone.utc)).total_seconds()
        return age_seconds >= COLLATERAL_HEARTBEAT_REFRESH_SECONDS
    except Exception as exc:
        logger.warning("CollateralLedger refresh-need check failed closed: %s", exc)
        return True


def _run_ws_gap_reconcile_if_required(
    adapter,
    *,
    conn_factory=None,
    ws_guard=None,
    now: datetime | None = None,
) -> dict:
    """Consume the M5 latch with a fresh read-only venue reconciliation sweep."""

    if adapter is None:
        return {"status": "adapter_unavailable"}
    if _cycle_lock.locked() or _edli_reactor_active():
        return {"status": "deferred_cycle_running"}
    if ws_guard is None:
        from src.control import ws_gap_guard as ws_guard
    current = now or datetime.now(timezone.utc)
    try:
        summary = ws_guard.summary(now=current)
    except TypeError:
        summary = ws_guard.summary()
    if not bool(summary.get("m5_reconcile_required", False)):
        return {"status": "not_required"}
    if (
        summary.get("subscription_state") == "DISCONNECTED"
        and summary.get("gap_reason") == "not_configured"
    ):
        return {
            "status": "deferred_ws_not_ready",
            "reason": "ws_not_configured",
            "subscription_state": summary.get("subscription_state"),
            "gap_reason": summary.get("gap_reason"),
            "m5_reconcile_required": True,
        }

    owns_connection = conn_factory is None
    conn = None
    try:
        from src.execution.exchange_reconcile import run_ws_gap_reconcile_and_clear
        from src.state.db import get_trade_connection

        conn = (conn_factory or (lambda: get_trade_connection(write_class="live")))()
        result = run_ws_gap_reconcile_and_clear(
            adapter,
            conn,
            ws_guard=ws_guard,
            observed_at=current,
        )
        conn.commit()
        if result.get("status") == "cleared":
            released = _release_ws_gap_blocked_exit_retries_after_m5_clear(
                conn,
                observed_at=current,
            )
            if released.get("released", 0):
                conn.commit()
            result["exit_retries_released"] = released.get("released", 0)
            result["exit_retry_position_ids"] = released.get("position_ids", [])
            logger.info("M5 WS-gap reconcile cleared submit latch: %s", result)
        else:
            logger.info("M5 WS-gap reconcile kept submit latch closed: %s", result)
        return result
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning("M5 WS-gap reconcile failed closed: %s", exc)
        return {"status": "failed_closed", "error": str(exc)}
    finally:
        if owns_connection and conn is not None:
            conn.close()


def _release_ws_gap_blocked_exit_retries_after_m5_clear(
    conn,
    *,
    observed_at: datetime,
) -> dict:
    """Release reduce-only exit retries that were delayed only by the M5 WS latch.

    M5 clearing proves the user-channel gap has been reconciled. Keeping positions
    that were rejected for ``ws_gap...m5_reconcile_required=True`` on exponential
    backoff after that proof delays exits for no additional safety evidence.
    """

    now_iso = observed_at.isoformat()
    recent_cutoff = (observed_at - timedelta(minutes=10)).isoformat()
    try:
        rows = conn.execute(
            """
            SELECT pc.position_id
              FROM position_current pc
             WHERE COALESCE(pc.exit_retry_count, 0) > 0
               AND COALESCE(pc.next_exit_retry_at, '') > ?
               AND COALESCE(pc.phase, '') IN ('active', 'day0_window', 'pending_exit')
               AND (
                    COALESCE(pc.chain_shares, 0) > 0
                 OR (
                        COALESCE(pc.chain_shares, 0) = 0
                    AND COALESCE(pc.shares, 0) > 0
                    AND COALESCE(pc.chain_state, '') = 'synced'
                    )
               )
               AND EXISTS (
                    SELECT 1
                      FROM position_events pe
                     WHERE pe.position_id = pc.position_id
                       AND pe.event_type = 'EXIT_ORDER_REJECTED'
                       AND pe.occurred_at >= ?
                       AND COALESCE(json_extract(pe.payload_json, '$.error'), '') LIKE 'ws_gap=%'
                       AND COALESCE(json_extract(pe.payload_json, '$.error'), '') LIKE '%m5_reconcile_required=True%'
               )
             ORDER BY pc.next_exit_retry_at, pc.position_id
            """,
            (now_iso, recent_cutoff),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - maintenance must not crash heartbeat.
        logger.warning("M5 exit-retry release query failed closed: %s", exc)
        return {"released": 0, "position_ids": [], "error": str(exc)}
    position_ids = [str(row[0]) for row in rows if str(row[0] or "")]
    if not position_ids:
        return {"released": 0, "position_ids": []}
    released = _append_exit_retry_release_events_and_update_projection(
        conn,
        position_ids,
        observed_at=observed_at,
        release_reason="M5_WS_GAP_RECONCILE_CLEARED",
        release_error="ws_gap_m5_reconcile_cleared",
    )
    changed = int(released.get("released", 0) or 0)
    position_ids = list(released.get("position_ids", []) or [])
    logger.info(
        "M5 cleared WS latch; released %d ws-gap-blocked exit retries: %s",
        changed,
        position_ids,
    )
    return released


def _append_exit_retry_release_events_and_update_projection(
    conn,
    position_ids: list[str],
    *,
    observed_at: datetime,
    release_reason: str,
    release_error: str,
) -> dict:
    """Append retry-release evidence before shortening projection cooldowns."""

    if not position_ids:
        return {"released": 0, "position_ids": []}
    now_iso = observed_at.isoformat()
    placeholders = ",".join("?" for _ in position_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT position_id,
                   COALESCE(phase, '') AS phase,
                   COALESCE(strategy_key, '') AS strategy_key,
                   COALESCE(order_id, '') AS order_id,
                   COALESCE(exit_retry_count, 0) AS exit_retry_count,
                   COALESCE(next_exit_retry_at, '') AS next_exit_retry_at
              FROM position_current
             WHERE position_id IN ({placeholders})
             ORDER BY position_id
            """,
            tuple(position_ids),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("exit-retry release projection read failed closed: %s", exc)
        return {"released": 0, "position_ids": [], "error": str(exc)}

    changed = 0
    released_ids: list[str] = []
    for row in rows:
        position_id = str(row[0] or "")
        if not position_id:
            continue
        try:
            conn.execute("SAVEPOINT exit_retry_release")
            sequence_row = conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
                (position_id,),
            ).fetchone()
            sequence_no = int(sequence_row[0] or 0) + 1
            payload = {
                "status": "ready",
                "exit_reason": release_reason,
                "error": release_error,
                "retry_count": int(row[4] or 0),
                "previous_next_retry_at": str(row[5] or ""),
                "next_retry_at": now_iso,
                "release_reason": release_reason,
            }
            conn.execute(
                """
                INSERT INTO position_events (
                    event_id, position_id, event_version, sequence_no, event_type,
                    occurred_at, phase_before, phase_after, strategy_key, decision_id,
                    snapshot_id, order_id, command_id, caused_by, idempotency_key,
                    venue_status, source_module, payload_json, env
                ) VALUES (?, ?, 1, ?, 'EXIT_RETRY_RELEASED',
                          ?, ?, ?, ?, NULL, NULL, ?, NULL, ?,
                          ?, 'ready', 'src.main', ?, 'live')
                """,
                (
                    f"{position_id}:exit_retry_released:{sequence_no}",
                    position_id,
                    sequence_no,
                    now_iso,
                    str(row[1] or "pending_exit"),
                    str(row[1] or "pending_exit"),
                    str(row[2] or ""),
                    str(row[3] or "") or None,
                    release_reason,
                    f"{position_id}:exit_retry_released:{sequence_no}",
                    json.dumps(payload, sort_keys=True),
                ),
            )
            cur = conn.execute(
                """
                UPDATE position_current
                   SET next_exit_retry_at = ?,
                       updated_at = ?
                 WHERE position_id = ?
                """,
                (now_iso, now_iso, position_id),
            )
            if int(cur.rowcount or 0) > 0:
                changed += int(cur.rowcount or 0)
                released_ids.append(position_id)
                conn.execute("RELEASE SAVEPOINT exit_retry_release")
            else:
                conn.execute("ROLLBACK TO SAVEPOINT exit_retry_release")
                conn.execute("RELEASE SAVEPOINT exit_retry_release")
        except Exception as exc:  # noqa: BLE001
            try:
                conn.execute("ROLLBACK TO SAVEPOINT exit_retry_release")
                conn.execute("RELEASE SAVEPOINT exit_retry_release")
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "exit-retry release append/update failed closed for %s: %s",
                position_id,
                exc,
            )
    return {"released": changed, "position_ids": released_ids}


def _release_allocator_config_blocked_exit_retries_after_refresh(
    conn,
    portfolio,
    *,
    observed_at: datetime,
) -> dict:
    """Release exits delayed only because allocator refresh had not run yet."""

    now_iso = observed_at.isoformat()
    recent_cutoff = (observed_at - timedelta(minutes=10)).isoformat()
    try:
        rows = conn.execute(
            """
            SELECT pc.position_id
              FROM position_current pc
             WHERE COALESCE(pc.exit_retry_count, 0) > 0
               AND COALESCE(pc.next_exit_retry_at, '') > ?
               AND COALESCE(pc.phase, '') IN ('active', 'day0_window', 'pending_exit')
               AND (
                    COALESCE(pc.chain_shares, 0) > 0
                 OR (
                        COALESCE(pc.chain_shares, 0) = 0
                    AND COALESCE(pc.shares, 0) > 0
                    AND COALESCE(pc.chain_state, '') = 'synced'
                    )
               )
               AND EXISTS (
                    SELECT 1
                      FROM position_events pe
                     WHERE pe.position_id = pc.position_id
                       AND pe.event_type = 'EXIT_ORDER_REJECTED'
                       AND pe.occurred_at >= ?
                       AND COALESCE(json_extract(pe.payload_json, '$.error'), '') = 'allocator_not_configured'
               )
             ORDER BY pc.next_exit_retry_at, pc.position_id
            """,
            (now_iso, recent_cutoff),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - maintenance must not crash monitor.
        logger.warning("Allocator-config exit-retry release query failed closed: %s", exc)
        return {"released": 0, "position_ids": [], "error": str(exc)}
    position_ids = [str(row[0]) for row in rows if str(row[0] or "")]
    if not position_ids:
        return {"released": 0, "position_ids": []}
    released = _append_exit_retry_release_events_and_update_projection(
        conn,
        position_ids,
        observed_at=observed_at,
        release_reason="ALLOCATOR_CONFIGURED_AFTER_REFRESH",
        release_error="allocator_not_configured_released",
    )
    changed = int(released.get("released", 0) or 0)
    position_ids = list(released.get("position_ids", []) or [])
    id_set = set(position_ids)
    for pos in getattr(portfolio, "positions", []) or []:
        if str(getattr(pos, "trade_id", "")) in id_set:
            pos.next_exit_retry_at = now_iso
    logger.info(
        "Allocator configured; released %d allocator-not-configured exit retries: %s",
        changed,
        position_ids,
    )
    return released


def _refresh_reconcile_findings_if_required(
    adapter,
    *,
    conn_factory=None,
    now: datetime | None = None,
) -> dict:
    """Resolve stale M5 findings after late venue confirmations arrive."""

    if adapter is None:
        return {"status": "adapter_unavailable"}
    if _cycle_lock.locked() or _edli_reactor_active():
        return {"status": "deferred_cycle_running"}
    owns_connection = conn_factory is None
    conn = None
    current = now or datetime.now(timezone.utc)
    try:
        from src.execution.exchange_reconcile import refresh_unresolved_reconcile_findings
        from src.state.db import get_trade_connection

        conn = (conn_factory or (lambda: get_trade_connection(write_class="live")))()
        unresolved = int(
            conn.execute(
                """
                SELECT COUNT(*) AS count
                  FROM exchange_reconcile_findings
                 WHERE resolved_at IS NULL
                """
            ).fetchone()["count"]
            or 0
        )
        if unresolved <= 0:
            return {"status": "not_required", "unresolved_findings": 0}
        result = refresh_unresolved_reconcile_findings(
            adapter,
            conn,
            observed_at=current,
        )
        result["unresolved_findings_before"] = unresolved
        conn.commit()
        if result.get("status") == "resolved":
            logger.info("M5 reconcile finding refresh resolved stale blockers: %s", result)
        else:
            logger.info("M5 reconcile finding refresh kept blockers: %s", result)
        return result
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning("M5 reconcile finding refresh failed closed: %s", exc)
        return {"status": "failed_closed", "error": str(exc)}
    finally:
        if owns_connection and conn is not None:
            conn.close()


def _run_venue_background_maintenance_once(adapter=None) -> dict:
    """Run venue read-side maintenance outside the heartbeat critical path."""

    if _cycle_lock.locked() or _edli_reactor_active():
        return {"status": "deferred_cycle_running"}
    active_adapter = adapter or _venue_heartbeat_adapter
    if active_adapter is None:
        return {"status": "adapter_unavailable"}
    reconcile_findings_refresh = _refresh_reconcile_findings_if_required(active_adapter)
    return {
        "status": "ok",
        "ws_gap_reconcile": _run_ws_gap_reconcile_if_required(active_adapter),
        "reconcile_findings_refresh": reconcile_findings_refresh,
        "collateral_refreshed": "owned_by_post_trade_capital",
    }


def _start_collateral_background_refresh_async(adapter=None) -> str:
    """Compatibility no-op: collateral refresh is owned by post-trade-capital."""

    return "owned_by_post_trade_capital"


def _start_venue_background_maintenance_async(adapter=None) -> str:
    """Start slow venue maintenance without delaying the next heartbeat tick."""

    global _last_venue_background_maintenance_attempt_at
    if _cycle_lock.locked() or _edli_reactor_active():
        return "deferred_cycle_running"
    active_adapter = adapter or _venue_heartbeat_adapter
    if active_adapter is None:
        return "adapter_unavailable"
    now = datetime.now(timezone.utc)
    m5_reconcile_required = _ws_gap_m5_reconcile_required()
    if (
        not m5_reconcile_required
        and
        _last_venue_background_maintenance_attempt_at is not None
        and (now - _last_venue_background_maintenance_attempt_at).total_seconds()
        < VENUE_BACKGROUND_MAINTENANCE_SECONDS
    ):
        return "throttled"
    if _edli_reactor_pending_backlog_exists() and not m5_reconcile_required:
        _last_venue_background_maintenance_attempt_at = now
        return "deferred_edli_pending_backlog"
    if not _venue_background_maintenance_lock.acquire(blocking=False):
        return "already_running"
    _last_venue_background_maintenance_attempt_at = now

    def _runner() -> None:
        try:
            _run_venue_background_maintenance_once(active_adapter)
        finally:
            _venue_background_maintenance_lock.release()

    thread = threading.Thread(
        target=_runner,
        name="venue-background-maintenance",
        daemon=True,
    )
    thread.start()
    return "started"


def _start_venue_background_maintenance_after_reactor_if_required() -> str:
    """Deterministically retry M5 venue maintenance after the reactor releases."""

    if not _ws_gap_m5_reconcile_required():
        return "not_required"
    try:
        adapter = _ensure_venue_read_side_adapter()
    except Exception as exc:  # noqa: BLE001 - post-cycle maintenance must not crash EDLI.
        logger.warning("M5 post-reactor maintenance adapter unavailable: %s", exc)
        return "adapter_unavailable"
    return _start_venue_background_maintenance_async(adapter)


_user_channel_ingestor = None
_user_channel_thread = None
_edli_market_channel_thread = None

# B4 (Phase-2): monotonic redecision cycle index. The continuous-redecision emit passes
# a per-cycle distinct `source` to scan_committed_snapshots for TWO reasons: (1) the
# B4 round-robin derives its window index from int(source.split('-')[-1]) — it needs a
# parseable "cycle-N" suffix; and (2) the source must be distinct per cycle so the
# re-emitted FSR-equivalent does not dedup to the consumed FSR.
#
# CROSS-RESTART UNIQUENESS (MAJOR-2 adversarial finding + HARDEN-1): the idempotency
# key is stable_idempotency_key(event_type, entity_key, source, available_at, digest).
# available_at is SNAPSHOT-STABLE (it does not advance per cycle), so `source` is
# the only varying component. A bare counter that resets to 0 on restart means the
# post-restart cycle-0 emit produces the SAME idempotency key as the pre-restart
# cycle-0 emit for the same snapshot family → dedup → family not re-decided for the
# early post-restart cycles.
#
# Fix (HARDEN-1): the boot token is `f"{int(time.time())}{os.getpid()}"` — a single
# decimal string with NO internal hyphens so source.split('-') stays ['cycle', TOKEN, N]
# and int(source.split('-')[-1]) still yields N. The PID changes on EVERY restart
# (even a crash-loop restart within the same wall-clock second), so the token is
# guaranteed restart-unique regardless of timing. int(time.time()) is included for
# human readability; PID alone would also suffice for correctness.
#
# Format: `cycle-{EPOCH}{PID}-{N}` where EPOCH and PID are concatenated (no separator)
# so the only hyphens in the string are the two that delimit the three components.
import time as _time

_edli_redecision_boot_token: str = f"{int(_time.time())}{os.getpid()}"
_edli_redecision_cycle_index: int = 0
_edli_redecision_screen_belief_cursor: int = 0
# Wave-1 2026-06-12: fixed per-cycle re-decision/screen batch fed to the WRAPPING fair
# cursor (CoverageFairnessRequest.select_rows). Replaces the deleted redecision_max_per_cycle
# settings cap. The cursor wraps modulo the family count, so this batch reaches EVERY family
# within ceil(N/batch) cycles and never silently drops the tail. Sized to sweep the full live
# family universe (~108 city×metric families) within ~2 cycles at the ~60-90s reactor cadence.
_EDLI_REDECISION_FAIR_BATCH: int = 60


def _edli_belief_family_key(belief) -> tuple[str, str, str, str]:
    return (
        str(getattr(belief, "city", "") or "").strip(),
        str(getattr(belief, "target_date", "") or "").strip(),
        str(getattr(belief, "metric", "") or "").strip(),
        str(getattr(belief, "family_id", "") or "").strip(),
    )


def _edli_redecision_screen_belief_batch(
    beliefs: list,
    *,
    max_families: int,
) -> tuple[list, set[tuple[str, str, str, str]], int]:
    """Return the fair-cursor entry-screen belief slice for this tick.

    The redecision screen used to feed every cached belief into the price reader,
    which meant a live table with millions of executable snapshots could keep one
    scheduler worker busy for minutes before the reactor reached any event. This
    is a fairness cursor, not an edge cap: it wraps through the complete belief
    universe over successive ticks and bounds the per-tick DB read surface.
    """
    global _edli_redecision_screen_belief_cursor
    if not beliefs:
        return [], set(), 0
    ordered = sorted(beliefs, key=_edli_belief_family_key)
    total = len(ordered)
    if max_families <= 0 or max_families >= total:
        keys = {_edli_belief_family_key(b) for b in ordered}
        _edli_redecision_screen_belief_cursor = 0
        return ordered, keys, total
    start = _edli_redecision_screen_belief_cursor % total
    selected = [ordered[(start + i) % total] for i in range(max_families)]
    _edli_redecision_screen_belief_cursor = (start + max_families) % total
    keys = {_edli_belief_family_key(b) for b in selected}
    return selected, keys, total


def _edli_filter_beliefs_to_family_keys(
    beliefs: list,
    family_keys: set[tuple[str, str, str, str]],
) -> list:
    if not family_keys:
        return []
    return [belief for belief in beliefs if _edli_belief_family_key(belief) in family_keys]


def _edli_next_redecision_source() -> str:
    """Return the next continuous-redecision emit source as ``cycle-{TOKEN}-{N}``.

    TOKEN = f"{int(time.time())}{os.getpid()}" captured once at module init — no
    internal hyphens, so split('-')[-1] == str(N) always. PID changes on every
    restart (including crash-loop restarts within the same wall-clock second), so
    the token is restart-unique unconditionally. N advances monotonically within a
    process, ensuring within-process sources are also distinct.
    """
    global _edli_redecision_cycle_index
    n = _edli_redecision_cycle_index
    _edli_redecision_cycle_index = n + 1
    return f"cycle-{_edli_redecision_boot_token}-{n}"


def _reset_edli_redecision_cycle_index() -> None:
    """Test hook: reset the monotonic redecision cycle counter to 0."""
    global _edli_redecision_cycle_index
    _edli_redecision_cycle_index = 0


def _set_edli_redecision_boot_token(token: str) -> None:
    """Test hook: set the boot token to a fixed value for deterministic testing.

    The token must contain NO hyphens (see format contract above).
    """
    global _edli_redecision_boot_token
    assert "-" not in token, f"boot token must not contain hyphens, got {token!r}"
    _edli_redecision_boot_token = token


USER_CHANNEL_REQUIRED_ENV_VARS = (
    "ZEUS_USER_CHANNEL_WS_ENABLED",
    "POLYMARKET_USER_WS_CONDITION_IDS",
)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def _parse_market_event_recorded_at(value: object) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _dedupe_user_channel_condition_ids(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        condition_id = str(value or "").strip()
        if not condition_id or condition_id in seen:
            continue
        seen.add(condition_id)
        result.append(condition_id)
    return result


def _market_events_fallback_max_age_hours() -> float:
    raw = os.environ.get("ZEUS_USER_CHANNEL_WS_MARKET_EVENTS_FALLBACK_MAX_AGE_HOURS", "36")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "invalid ZEUS_USER_CHANNEL_WS_MARKET_EVENTS_FALLBACK_MAX_AGE_HOURS=%r; "
            "using default 36h",
            raw,
        )
        return 36.0
    if value <= 0:
        logger.warning(
            "non-positive ZEUS_USER_CHANNEL_WS_MARKET_EVENTS_FALLBACK_MAX_AGE_HOURS=%r; "
            "using default 36h",
            raw,
        )
        return 36.0
    return value


def _market_events_user_channel_condition_ids(
    *,
    now: datetime | None = None,
) -> list[str]:
    """Read fresh condition_ids from canonical market_events."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    max_age_hours = _market_events_fallback_max_age_hours()
    cutoff = current - timedelta(hours=max_age_hours)
    try:
        from src.state.db import get_forecasts_connection

        conn = get_forecasts_connection()
        try:
            rows = conn.execute(
                """
                SELECT condition_id, target_date, recorded_at
                  FROM market_events
                 WHERE condition_id IS NOT NULL
                   AND TRIM(condition_id) != ''
                   AND target_date >= ?
                 ORDER BY recorded_at DESC, condition_id
                 LIMIT 2048
                """,
                (current.date().isoformat(),),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("user-channel WS market_events fallback failed: %s", exc)
        return []

    fresh_ids: list[str] = []
    for row in rows:
        recorded_at = _parse_market_event_recorded_at(row["recorded_at"])
        if recorded_at is None or recorded_at < cutoff:
            continue
        fresh_ids.append(row["condition_id"])
    return _dedupe_user_channel_condition_ids(fresh_ids)


@_scheduler_job("venue_heartbeat")
def _write_venue_heartbeat() -> None:
    """Post the Polymarket venue heartbeat required for live resting orders.

    Keep this function narrow. Polymarket cancels resting GTC/GTD orders when
    valid heartbeats stop, so slow reconciliation and collateral reads must not
    run inline with the heartbeat tick.
    """
    global _venue_heartbeat_supervisor, _venue_heartbeat_adapter
    import asyncio

    from src.control.heartbeat_supervisor import (
        HeartbeatHealth,
        HeartbeatSupervisor,
        current_status,
        configure_global_supervisor,
        fresh_heartbeat_id_from_status,
        heartbeat_cadence_seconds_from_env,
        write_heartbeat_keeper_status,
    )

    if _external_venue_heartbeat_enabled():
        _configure_external_venue_heartbeat_supervisor_if_needed()
        status = current_status()
        if status.health is not HeartbeatHealth.HEALTHY:
            raise RuntimeError(
                f"external venue heartbeat unhealthy: health={status.health.value}; "
                f"error={status.last_error or ''}"
            )
        return

    try:
        if _venue_heartbeat_supervisor is None:
            from src.data.polymarket_client import PolymarketClient

            adapter = PolymarketClient()._ensure_v2_adapter()
            _venue_heartbeat_adapter = adapter
            _venue_heartbeat_supervisor = HeartbeatSupervisor(
                adapter,
                cadence_seconds=heartbeat_cadence_seconds_from_env(),
                initial_heartbeat_id=fresh_heartbeat_id_from_status(),
            )
            configure_global_supervisor(_venue_heartbeat_supervisor)
    except Exception as exc:
        if _venue_heartbeat_supervisor is None:
            _venue_heartbeat_supervisor = HeartbeatSupervisor(
                adapter=None,
                cadence_seconds=heartbeat_cadence_seconds_from_env(),
            )
            configure_global_supervisor(_venue_heartbeat_supervisor)
        _venue_heartbeat_supervisor.record_failure(exc)
        logger.error("Venue heartbeat failed closed: %s", exc)
        raise

    try:
        status = asyncio.run(_venue_heartbeat_supervisor.run_once())
    except Exception as exc:
        _venue_heartbeat_supervisor.record_failure(exc)
        logger.error("Venue heartbeat failed closed: %s", exc)
        raise
    if status.health is not HeartbeatHealth.HEALTHY:
        raise RuntimeError(
            f"venue heartbeat unhealthy: health={status.health.value}; "
            f"error={status.last_error or ''}"
        )
    write_heartbeat_keeper_status(status, owner="zeus-live-daemon")
    _start_venue_background_maintenance_async(_venue_heartbeat_adapter)


@_scheduler_job("venue_heartbeat")
def _start_venue_heartbeat_loop_if_needed() -> None:
    """Keep a dedicated venue-heartbeat loop alive outside APScheduler load."""

    global _venue_heartbeat_thread
    if _external_venue_heartbeat_enabled():
        _configure_external_venue_heartbeat_supervisor_if_needed()
        return
    if _venue_heartbeat_thread is not None and _venue_heartbeat_thread.is_alive():
        return

    from src.control.heartbeat_supervisor import heartbeat_cadence_seconds_from_env

    cadence_seconds = heartbeat_cadence_seconds_from_env()
    _venue_heartbeat_thread = threading.Thread(
        target=_run_venue_heartbeat_loop,
        args=(cadence_seconds,),
        name="venue-heartbeat",
        daemon=True,
    )
    _venue_heartbeat_thread.start()


def _run_venue_heartbeat_loop(cadence_seconds: float) -> None:
    """Run venue heartbeats forever; a failed tick must not kill the loop."""

    import time

    while True:
        started = datetime.now(timezone.utc)
        try:
            _write_venue_heartbeat()
        except Exception as exc:
            logger.error("venue heartbeat loop tick failed: %s", exc, exc_info=True)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        time.sleep(max(0.1, cadence_seconds - elapsed))


def _pending_family_refresh_event_window_limit() -> int:
    raw = os.environ.get("ZEUS_PENDING_FAMILY_REFRESH_EVENT_WINDOW_LIMIT", "2000")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 2000
    return max(100, min(10000, value))


def _pending_family_rows_for_refresh(
    world_conn,
    *,
    consumer_name: str,
    event_window_limit: int | None = None,
    now_utc: datetime | None = None,
):
    from src.events.event_store import EventStore, _oceania_frontier_target_floor

    if event_window_limit is None:
        event_window_limit = _pending_family_refresh_event_window_limit()
    event_window_limit = max(100, min(10000, int(event_window_limit)))
    decision_utc = now_utc if now_utc is not None else datetime.now(timezone.utc)
    stale_target_floor = _oceania_frontier_target_floor(decision_utc)
    rows = world_conn.execute(
        """
        WITH pending AS (
            SELECT p.event_id
            FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
            JOIN opportunity_events e ON e.event_id = p.event_id
            WHERE p.consumer_name = ? AND p.processing_status = 'pending'
              AND (p.claimed_at IS NULL OR p.claimed_at <= ?)
              AND (
                    e.event_type NOT IN (
                        'FORECAST_SNAPSHOT_READY',
                        'EDLI_REDECISION_PENDING',
                        'DAY0_EXTREME_UPDATED'
                    )
                    OR json_extract(e.payload_json, '$.target_date') IS NULL
                    OR json_extract(e.payload_json, '$.target_date') >= ?
              )
            ORDER BY p.updated_at DESC
            LIMIT ?
        )
        SELECT
            json_extract(e.payload_json, '$.city')        AS city,
            json_extract(e.payload_json, '$.target_date') AS target_date,
            json_extract(e.payload_json, '$.metric')      AS metric,
            MAX(CASE e.event_type
                  WHEN 'DAY0_EXTREME_UPDATED' THEN 4
                  WHEN 'EDLI_REDECISION_PENDING' THEN 3
                  WHEN 'FORECAST_SNAPSHOT_READY' THEN 2
                  ELSE 1
                END) AS refresh_urgency
        FROM pending p
        JOIN opportunity_events e ON e.event_id = p.event_id
        GROUP BY city, target_date, metric
        -- Refresh live-money urgency first. Day0 hard facts and price-driven
        -- redecisions are the rows whose stale executable substrate directly
        -- blocks hold/exit/shift/new-entry decisions. Target date remains a
        -- freshness tiebreak, not the primary ordering law; otherwise future
        -- families can bury same-day Day0 rows.
        ORDER BY
            MAX(CASE e.event_type
                  WHEN 'DAY0_EXTREME_UPDATED' THEN 4
                  WHEN 'EDLI_REDECISION_PENDING' THEN 3
                  WHEN 'FORECAST_SNAPSHOT_READY' THEN 2
                  ELSE 1
                END) DESC,
            MAX(e.priority) DESC,
            MAX(e.available_at) DESC,
            MAX(json_extract(e.payload_json, '$.target_date')) DESC,
            MIN(e.event_id) ASC
        """,
        (consumer_name, decision_utc.isoformat(), stale_target_floor, event_window_limit),
    ).fetchall()
    return [
        row
        for row in rows
        if not EventStore._strictly_past_in_tz(
            str(row[0] or "").strip(),
            str(row[1] or "").strip(),
            decision_utc,
        )
    ]


def _open_rest_family_rows_for_refresh(trade_conn) -> list[tuple[str, str, str]]:
    """Families with live unfilled ENTRY rests that need fresh executable books.

    Pending opportunity events are not the only source of live money-at-risk
    freshness demand. Once an ENTRY maker rest is live, duplicate suppression can
    correctly prevent new entry events for that token, leaving no pending event to
    keep the book warm. Use bounded latest-fact seeks over the small command set
    so the substrate warmer can keep open rests re-priceable without scanning the
    full order-fact history.
    """

    from src.execution.maker_rest_escalation import OPEN_REST_FACT_STATES

    try:
        command_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(venue_commands)").fetchall()
        }
        fact_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(venue_order_facts)").fetchall()
        }
        token_select = "token_id" if "token_id" in command_cols else "'' AS token_id"
        snapshot_select = "snapshot_id" if "snapshot_id" in command_cols else "'' AS snapshot_id"
        state_select = "state" if "state" in command_cols else "'' AS state"
        state_filter = (
            "AND state IN ('ACKED', 'POST_ACKED', 'PARTIAL')" if "state" in command_cols else ""
        )
        remaining_select = "remaining_size" if "remaining_size" in fact_cols else "NULL AS remaining_size"
        commands = trade_conn.execute(
            f"""
            SELECT command_id, position_id, venue_order_id, {token_select}, {snapshot_select}, {state_select}
              FROM venue_commands
             WHERE intent_kind = 'ENTRY'
               AND venue_order_id IS NOT NULL
               AND venue_order_id != ''
               {state_filter}
            """
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    open_states = set(OPEN_REST_FACT_STATES)
    for row in commands:
        venue_order_id = str(row[2] or "")
        if not venue_order_id:
            continue
        try:
            fact = trade_conn.execute(
                f"""
                SELECT state, {remaining_select}
                  FROM venue_order_facts
                 WHERE venue_order_id = ?
                 ORDER BY local_sequence DESC
                 LIMIT 1
                """,
                (venue_order_id,),
            ).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if fact is None or str(fact[0] or "") not in open_states:
            continue
        remaining_value = fact[1] if len(fact) > 1 else None
        raw_remaining = "" if remaining_value is None else str(remaining_value).strip()
        if raw_remaining:
            try:
                if float(raw_remaining) <= 0.000001:
                    continue
            except ValueError:
                continue
        if str(fact[0] or "") == VenueOrderStatus.PARTIALLY_MATCHED and not raw_remaining:
            continue
        position_id = str(row[1] or "")
        family: tuple[str, str, str] | None = None
        try:
            pos = trade_conn.execute(
                """
                SELECT city, target_date, temperature_metric
                  FROM position_current
                 WHERE position_id = ?
                   AND phase IN ('pending_entry', 'active', 'day0_window')
                 LIMIT 1
                """,
                (position_id,),
            ).fetchone() if position_id else None
        except Exception:  # noqa: BLE001
            pos = None
        if pos is not None:
            family = (
                str(pos[0] or "").strip(),
                str(pos[1] or "").strip(),
                str(pos[2] or "").strip(),
            )
        if not family or not all(family):
            family = _open_rest_family_from_snapshot(
                trade_conn,
                token_id=str(row[3] or ""),
                snapshot_id=str(row[4] or ""),
            )
        if family and all(family) and family not in seen:
            seen.add(family)
            out.append(family)
    return out


def _open_rest_family_from_snapshot(
    trade_conn,
    *,
    token_id: str,
    snapshot_id: str,
) -> tuple[str, str, str] | None:
    """Resolve an ACKED rest's family even before position_current projection exists."""

    try:
        snap_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()
        }
    except Exception:  # noqa: BLE001
        return None
    slug_cols = [col for col in ("event_id", "event_slug") if col in snap_cols]
    if not slug_cols:
        return None
    select_slug = slug_cols[0] if len(slug_cols) == 1 else "COALESCE(" + ", ".join(slug_cols) + ")"
    predicates: list[str] = []
    params: list[str] = []
    if snapshot_id and "snapshot_id" in snap_cols:
        predicates.append("snapshot_id = ?")
        params.append(snapshot_id)
    if token_id:
        for col in ("selected_outcome_token_id", "yes_token_id", "no_token_id"):
            if col in snap_cols:
                predicates.append(f"{col} = ?")
                params.append(token_id)
    if not predicates:
        return None
    snapshot_order = "CASE WHEN snapshot_id = ? THEN 0 ELSE 1 END" if "snapshot_id" in snap_cols else "1"
    query_params = [*params]
    if "snapshot_id" in snap_cols:
        query_params.append(snapshot_id)
    try:
        row = trade_conn.execute(
            f"""
            SELECT {select_slug} AS market_slug
              FROM executable_market_snapshots
             WHERE {" OR ".join(predicates)}
             ORDER BY
               {snapshot_order},
               captured_at DESC
             LIMIT 1
            """,
            tuple(query_params),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    return _weather_family_from_market_slug(str(row[0] or ""))


def _weather_family_from_market_slug(slug: str) -> tuple[str, str, str] | None:
    text = str(slug or "").strip().lower()
    prefixes = (
        ("highest-temperature-in-", "high"),
        ("lowest-temperature-in-", "low"),
    )
    metric = ""
    rest = ""
    for prefix, candidate_metric in prefixes:
        if text.startswith(prefix):
            metric = candidate_metric
            rest = text[len(prefix):]
            break
    if not rest or "-on-" not in rest:
        return None
    city_slug, date_slug = rest.rsplit("-on-", 1)
    parts = date_slug.split("-")
    if len(parts) != 3:
        return None
    month_name, day_text, year_text = parts
    month_map = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    try:
        target_date = date(
            int(year_text),
            month_map[month_name],
            int(day_text),
        ).isoformat()
    except Exception:  # noqa: BLE001
        return None
    try:
        from src.config import runtime_cities_by_name

        city_by_slug: dict[str, str] = {}
        for name, city in runtime_cities_by_name().items():
            aliases = set(getattr(city, "slug_names", ()) or ())
            aliases.add(str(name).lower().replace(" ", "-"))
            aliases.add(str(getattr(city, "name", name)).lower().replace(" ", "-"))
            for alias in aliases:
                if alias:
                    city_by_slug[str(alias).lower()] = str(getattr(city, "name", name) or name)
        city = city_by_slug.get(city_slug)
    except Exception:  # noqa: BLE001
        city = None
    if not city:
        city = city_slug.replace("-", " ").title()
    return (city, target_date, metric)


def _condition_buy_sides_fresh(write_conn, condition_id: str, fresh_at_iso: str) -> bool:
    from src.state.snapshot_repo import condition_buy_sides_fresh

    return condition_buy_sides_fresh(write_conn, condition_id, fresh_at_iso)


def _prune_fresh_market_outcomes_for_snapshot_refresh(
    write_conn,
    markets: list[dict],
    *,
    fresh_at_iso: str,
    restrict_to_condition_ids: Iterable[str] | None = None,
) -> tuple[list[dict], int, int]:
    scoped_conditions = {
        str(condition_id or "").strip()
        for condition_id in (restrict_to_condition_ids or ())
        if str(condition_id or "").strip()
    }
    pruned: list[dict] = []
    fresh_conditions_skipped = 0
    stale_conditions_submitted = 0
    for market in markets:
        stale_outcomes: list[dict] = []
        for outcome in market.get("outcomes", []) or []:
            if not isinstance(outcome, dict):
                continue
            cid = str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
            if scoped_conditions and cid not in scoped_conditions:
                continue
            if cid and _condition_buy_sides_fresh(write_conn, cid, fresh_at_iso):
                fresh_conditions_skipped += 1
                continue
            stale_outcomes.append(outcome)
            stale_conditions_submitted += 1
        if not stale_outcomes:
            continue
        cloned = dict(market)
        cloned["outcomes"] = stale_outcomes
        if "condition_ids" in cloned:
            cloned["condition_ids"] = [
                str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
                for outcome in stale_outcomes
                if str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
            ]
        pruned.append(cloned)
    return pruned, fresh_conditions_skipped, stale_conditions_submitted


def _gamma_lookup_deadline_for_snapshot_refresh(
    *,
    refresh_deadline: float,
    refresh_budget_s: float,
    snapshot_reserve_s: float,
    cached_topology_count: int,
    gamma_family_count: int = 0,
) -> float:
    pre_capture_deadline = refresh_deadline - snapshot_reserve_s
    if cached_topology_count > 0 and gamma_family_count <= 0:
        cached_gamma_s = max(
            0.1,
            float(os.environ.get("ZEUS_REACTOR_CACHED_TOPOLOGY_GAMMA_SECONDS", "1.0")),
        )
        return min(pre_capture_deadline, refresh_deadline - refresh_budget_s + cached_gamma_s)
    return refresh_deadline - snapshot_reserve_s




def _capture_boot_state() -> dict:
    """PR-S6: capture git HEAD SHA + timestamp at daemon start.

    Returns {"sha": sha, "ts": datetime} on success.
    Returns {"sha": None, "ts": None} if ZEUS_ACCEPT_STALE_DEPLOY=1 and git fails.
    Raises SystemExit if git fails and ZEUS_ACCEPT_STALE_DEPLOY != "1" (fail-loud).

    Extracted as a named function so tests can call it directly (not an inlined copy).
    """
    import subprocess

    from src.config import PROJECT_ROOT

    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip().decode()
        return {"sha": sha, "ts": datetime.now(timezone.utc)}
    except Exception as exc:
        if os.environ.get("ZEUS_ACCEPT_STALE_DEPLOY") == "1":
            logger.warning(
                "deployment_freshness: boot SHA capture failed (%s); "
                "ZEUS_ACCEPT_STALE_DEPLOY=1 — skipping gate", exc,
            )
            return {"sha": None, "ts": None}
        raise SystemExit(
            f"deployment_freshness: boot SHA capture failed ({exc}) and "
            "ZEUS_ACCEPT_STALE_DEPLOY != 1. Cannot initialize freshness gate. "
            "Set ZEUS_ACCEPT_STALE_DEPLOY=1 to skip."
        )


def _write_loaded_sha_state(boot_sha: str | None) -> None:
    """Write the running daemon's git HEAD SHA to state/loaded_sha.json at boot.

    EDLI-mode release-gate surface. The live-release gate's loaded_sha check and
    main.evaluate_edli_stage_readiness compare the *loaded* SHA (what this process
    actually booted on) against the expected HEAD. In legacy_cron mode run_cycle
    produced no such file; in EDLI modes nothing wrote it -> gate FAIL
    (missing_loaded_sha). This writes the GENUINE booted SHA (reuses the value
    _capture_boot_state already captured via git rev-parse HEAD), once at boot.

    A divergence between loaded_sha and current HEAD (filesystem updated without
    restart) is exactly what the gate is meant to catch — so this file is written
    ONCE at boot and intentionally NOT refreshed, encoding the truly-loaded SHA.

    Authority: fix/edli-stage-readiness-2026-05-31 (loaded_sha surface).
    """
    if not boot_sha:
        logger.warning(
            "loaded_sha: boot SHA unavailable (ZEUS_ACCEPT_STALE_DEPLOY override?); "
            "skipping state/loaded_sha.json write — release gate will read missing_loaded_sha"
        )
        return
    from src.config import state_path

    out_path = state_path("loaded_sha.json")
    payload = {
        "loaded_sha": boot_sha,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(out_path)
        logger.info("loaded_sha: wrote state/loaded_sha.json loaded_sha=%s", boot_sha[:8])
    except OSError as exc:
        logger.error("loaded_sha: failed to write state/loaded_sha.json: %s", exc)


@_scheduler_job("deployment_freshness")
def _check_deployment_freshness(
    *,
    boot_sha: str | None = None,
    boot_ts: datetime | None = None,
    repo_root: "Path | None" = None,
    now: datetime | None = None,
) -> None:
    """PR-S6: deployment freshness gate — detects stale daemon (merged code never reloaded).

    Compares the git HEAD SHA at daemon boot vs the current working-tree HEAD.
    Divergence means a merge/deploy happened after the daemon started.

    Live-money enforcement:
      < 24h  : ERROR log + state/deployment_freshness.json flag + pause_entries
               (reason='deployment_freshness_mismatch'). Trading paused immediately
               to prevent stale entry decisions while operator restarts.
      >= 24h : SystemExit fail-closed unless ZEUS_ACCEPT_STALE_DEPLOY=1.

    Advisory state written to state/deployment_freshness.json (NOT control_plane.json
    which is overwritten every cycle by _write_control_payload).

    All git failures and non-git-repo environments are silent (no crash).
    """
    import json
    import subprocess

    from src.config import PROJECT_ROOT, state_path

    _boot_sha: str | None = boot_sha if boot_sha is not None else _BOOT_STATE.get("sha")
    _boot_ts: datetime | None = boot_ts if boot_ts is not None else _BOOT_STATE.get("ts")
    _now: datetime = now if now is not None else datetime.now(timezone.utc)
    _repo_root: Path = repo_root if repo_root is not None else PROJECT_ROOT

    if not _boot_sha or not _boot_ts:
        # Boot capture failed — skip silently.
        logger.debug("_check_deployment_freshness: boot state not captured, skipping")
        return

    # Check ZEUS_ACCEPT_STALE_DEPLOY override first.
    if os.environ.get("ZEUS_ACCEPT_STALE_DEPLOY") == "1":
        logger.warning(
            "deployment_freshness: ZEUS_ACCEPT_STALE_DEPLOY=1 override active; "
            "skipping staleness check (boot_sha=%s)", _boot_sha[:8]
        )
        return

    try:
        current_sha: str = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_repo_root),
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip().decode()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning(
            "deployment_freshness: git rev-parse failed (%s); skipping check", exc
        )
        return

    uptime_hours: float = (_now - _boot_ts).total_seconds() / 3600.0
    df_path = state_path("deployment_freshness.json")

    def _write_deployment_freshness_state(payload: dict[str, object]) -> None:
        try:
            _tmp = str(df_path) + ".tmp"
            with open(_tmp, "w") as _f:
                json.dump(payload, _f, indent=2)
            os.replace(_tmp, str(df_path))
        except Exception as _exc:
            logger.warning("deployment_freshness: failed to write flag file: %s", _exc)

    if current_sha == _boot_sha:
        if df_path.exists():
            _write_deployment_freshness_state(
                {
                    "boot_sha": _boot_sha,
                    "current_sha": current_sha,
                    "uptime_hours": round(uptime_hours, 2),
                    "detected_at": _now.isoformat(),
                    "pause_reason": None,
                    "status": "fresh",
                }
            )
        return  # No divergence.

    if uptime_hours >= 24.0:
        import signal as _signal
        logger.critical(
            "DEPLOYMENT_STALE — loaded SHA %s but filesystem has %s for >%.1fh. "
            "Signaling SIGTERM to escape APScheduler exception boundary.",
            _boot_sha[:8], current_sha[:8], uptime_hours,
        )
        # os.kill(SIGTERM) propagates to the process's signal handler OUTSIDE
        # APScheduler's BaseException catch in run_job(), ensuring the daemon
        # actually stops. The trailing raise keeps direct callers (test suite)
        # correctly fail-closed.
        os.kill(os.getpid(), _signal.SIGTERM)
        raise SystemExit(
            f"DEPLOYMENT_STALE — daemon loaded SHA {_boot_sha[:8]} but filesystem "
            f"has {current_sha[:8]} for >{uptime_hours:.1f}h. "
            f"Set ZEUS_ACCEPT_STALE_DEPLOY=1 to override."
        )
    else:
        logger.error(
            "deployment_freshness_diverged_total: boot_sha=%s current_sha=%s "
            "uptime_hours=%.1f — merged code not reloaded; pausing entries immediately",
            _boot_sha[:8], current_sha[:8], uptime_hours,
        )
        # Write advisory flag to dedicated state/deployment_freshness.json.
        # NOT control_plane.json — that file is overwritten on every cycle by
        # _write_control_payload (control_plane.py:119) which writes only
        # {commands, acks}. A dedicated file survives all control_plane writes.
        _write_deployment_freshness_state(
            {
                "boot_sha": _boot_sha,
                "current_sha": current_sha,
                "uptime_hours": round(uptime_hours, 2),
                "detected_at": _now.isoformat(),
                "pause_reason": _DEPLOYMENT_FRESHNESS_PAUSE_REASON,
                "status": "mismatch",
            }
        )
        # Pause new entries immediately. Exit submits are also protected at the
        # live_venue_submit capability boundary, so stale code cannot keep trading
        # while the operator restarts.
        try:
            from src.control.control_plane import pause_entries
            # issued_by="system_auto_pause" activates the idempotency guard in
            # control_plane._has_active_auto_pause_override — prevents duplicate
            # control_overrides rows and alert spam on every 60s tick.
            pause_entries(
                _DEPLOYMENT_FRESHNESS_PAUSE_REASON,
                issued_by="system_auto_pause",
                effective_until=None,
            )
        except Exception as _exc:
            logger.error(
                "deployment_freshness: pause_entries failed (%s); "
                "entries NOT paused despite SHA mismatch", _exc,
            )


_DEPLOYMENT_FRESHNESS_PAUSE_REASON = "deployment_freshness_mismatch"
_DEPLOYMENT_FRESHNESS_LEGACY_PAUSE_REASONS = frozenset(
    {"deployment_freshness_4h_divergence"}
)


def _boot_deployment_freshness_auto_resume() -> None:
    """Boot-time auto-resume: clear a deployment freshness pause when
    the operator has restarted the daemon with the current git HEAD SHA.

    Called AFTER _assert_live_safe_strategies_or_exit() (which hydrates _control_state
    via refresh_control_state) so is_entries_paused() / get_entries_pause_reason()
    reflect durable DB state, not stale in-memory defaults.

    Logic:
    - If entries are NOT paused → no-op.
    - If entries are paused for a non-deployment-freshness reason → no-op;
      do not clear operator-issued or other system pauses.
    - If entries are paused with deployment-freshness reason AND the current
      git HEAD matches _BOOT_STATE['sha'] → call resume_entries() to clear the DB override
      + tombstone + refresh in-memory state. Logs at INFO with both SHAs.
    - If SHA is still mismatched → do NOT auto-resume; operator must investigate.
    - Any exception is caught and logged at WARNING so boot proceeds safely.
    """
    import subprocess

    from src.config import PROJECT_ROOT
    from src.control.control_plane import (
        get_entries_pause_reason,
        is_entries_paused,
        resume_entries,
    )

    try:
        if not is_entries_paused():
            return
        pause_reason = get_entries_pause_reason()
        if pause_reason not in (
            {_DEPLOYMENT_FRESHNESS_PAUSE_REASON}
            | set(_DEPLOYMENT_FRESHNESS_LEGACY_PAUSE_REASONS)
        ):
            return
        boot_sha: str | None = _BOOT_STATE.get("sha")
        if not boot_sha:
            logger.warning(
                "deployment_freshness_auto_resume: boot SHA not captured; cannot verify SHA match"
            )
            return
        try:
            current_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(PROJECT_ROOT),
                timeout=5,
                stderr=subprocess.DEVNULL,
            ).strip().decode()
        except Exception as exc:
            logger.warning(
                "deployment_freshness_auto_resume: git rev-parse failed (%s); cannot verify SHA match",
                exc,
            )
            return
        if current_sha != boot_sha:
            logger.warning(
                "deployment_freshness_auto_resume: SHA still mismatched at boot "
                "(boot=%s current=%s) — NOT auto-resuming; operator must investigate",
                boot_sha[:8], current_sha[:8],
            )
            return
        # SHA matches: operator restarted with current code — safe to auto-resume.
        resume_entries(
            "deployment_freshness_resumed_on_sha_match",
            issued_by="control_plane",
        )
        logger.info(
            "deployment_freshness_auto_resume: cleared deployment freshness pause "
            "— boot_sha=%s matches filesystem HEAD=%s; entries unblocked",
            boot_sha[:8], current_sha[:8],
        )
    except Exception as exc:
        logger.warning(
            "deployment_freshness_auto_resume: unexpected error (%s); boot continues without auto-resume",
            exc,
            exc_info=True,
        )


def _startup_freshness_check() -> None:
    """§3.1: data freshness gate at boot — uses evaluate_freshness_at_boot.

    §3.7 gate split:
    - Data freshness gate: degrade-or-warn on STALE. Operator may override
      individual sources via state/control_plane.json::force_ignore_freshness.
    - Wallet reachability warm-up (_startup_wallet_check): NEVER synthesizes
      bankroll truth; missing wallet truth leaves new submit/sizing fail-closed
      while monitor/redecision continues.

    Boot behavior (driven by evaluate_freshness_at_boot):
    - FRESH: log at INFO, proceed.
    - STALE: log warning with per-source details, proceed (degraded mode).
    - ABSENT: retry every BOOT_RETRY_INTERVAL_SECONDS up to
      BOOT_RETRY_MAX_ATTEMPTS, then SystemExit. The boot helper handles retry
      internally and never returns an ABSENT verdict to this caller.

    Codex PR #31 (P1) fix 2026-05-01: previously called
    evaluate_freshness_mid_run, which synthesizes ABSENT into a degraded
    all-STALE verdict. That made the `if branch == "ABSENT"` retry path here
    unreachable and silently weakened the boot safety contract — a missing
    source_health.json proceeded immediately as degraded instead of
    triggering the retry-then-FATAL window. Switching to the boot helper
    restores the design §3.1 contract.
    """
    from src.config import STATE_DIR
    from src.control.freshness_gate import evaluate_freshness_at_boot

    # evaluate_freshness_at_boot handles retry + SystemExit on ABSENT internally.
    verdict = evaluate_freshness_at_boot(STATE_DIR)

    if verdict.branch == "STALE":
        logger.warning(
            "Freshness gate STALE at boot: stale_sources=%s day0_capture_disabled=%s "
            "ensemble_disabled=%s (trading continues in degraded mode)",
            verdict.stale_sources, verdict.day0_capture_disabled, verdict.ensemble_disabled,
        )
    elif verdict.branch == "FRESH":
        logger.info("Freshness gate: FRESH — all sources within budget")


def _startup_world_schema_ready_check() -> None:
    """Design §4.2: trading boot retries then FAILs if DB schema readiness is not proven.

    Mirrors _startup_freshness_check retry pattern (30 × 10s = 5 min).
    Fail-closed: raises SystemExit if direct world or forecast DB schema checks
    fail after retries.
    This is the Phase 2→Phase 3 enforcement promotion per architect audit A-2.

    K1 split 2026-05-11: this function now delegates to _startup_db_schema_ready_check,
    which checks both canonical DB files directly. The old data-ingest sentinel
    is no longer authority for live boot because live forecast production moved
    to forecast-live while com.zeus.data-ingest is not a required live process.
    Kept for API compat; do not remove.
    """
    _startup_db_schema_ready_check()


def _startup_world_db_schema_ready_check() -> str:
    """Read-only world DB structural schema check for live startup.

    Verifies presence of a minimal set of canonical world tables via
    sqlite_master (read-only).  Missing DB or missing tables fail closed.
    B2 (2026-05-28) cancelled the schema-version counter mechanism entirely.
    """
    import sqlite3

    from src.state.db import ZEUS_WORLD_DB_PATH, assert_schema_current

    _CANONICAL_WORLD_TABLES = frozenset({
        "decision_events",
        "position_current",
        "trade_decisions",
    })
    _LIVE_REQUIRED_WORLD_INDEXES = frozenset({
        "idx_opportunity_events_day0_family_extreme",
        "idx_opportunity_event_processing_pending_retry_floor",
        "idx_opportunity_event_processing_stale_claim",
        "idx_opportunity_event_processing_status",
    })

    if not ZEUS_WORLD_DB_PATH.exists():
        raise FileNotFoundError(f"{ZEUS_WORLD_DB_PATH} does not exist")
    conn = sqlite3.connect(
        f"file:{ZEUS_WORLD_DB_PATH.resolve()}?mode=ro",
        uri=True,
        timeout=1.0,
    )
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 1000")
        assert_schema_current(conn)
        missing = {
            table
            for table in _CANONICAL_WORLD_TABLES
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (table,),
            ).fetchone()
            is None
        }
        if missing:
            raise RuntimeError(
                f"world DB missing canonical tables: {sorted(missing)}"
            )
        missing_indexes = {
            index
            for index in _LIVE_REQUIRED_WORLD_INDEXES
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=? LIMIT 1",
                (index,),
            ).fetchone()
            is None
        }
        if missing_indexes:
            raise RuntimeError(
                f"world DB missing live-required indexes: {sorted(missing_indexes)}"
            )
        return "ready"
    finally:
        conn.close()


def _startup_world_db_schema_prepare() -> str:
    """Operator-only world schema repair hook, intentionally unused by live boot.

    Live startup must not run idempotent DDL on the 60GB canonical world DB. A
    trading daemon restart is a runtime liveness operation, not a migration
    window; schema repair belongs to explicit deployment tooling before the live
    process is armed. Keeping this helper preserves old import compatibility
    while making accidental runtime use visible in code review.
    """
    import src.state.db as db_module

    path = db_module.ZEUS_WORLD_DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")

    conn = db_module.get_world_connection(write_class="live")
    try:
        db_module.init_schema(conn)
        conn.commit()
        logger.info("world DB schema prepared by explicit operator repair: init_schema complete")
        return "prepared"
    finally:
        conn.close()


def _startup_world_db_hot_index_prepare() -> str:
    """Create only live-required world hot indexes during boot repair.

    Full ``init_schema`` is still available as the explicit operator repair
    helper above. Live boot only needs the hot indexes that executable fetch,
    retry-floor, and Day0 supersession paths require to avoid starting in a
    known-broken slow/error state.
    """
    import src.state.db as db_module
    from src.state.schema.opportunity_event_processing_schema import (
        CREATE_PENDING_RETRY_FLOOR_INDEX_SQL,
        CREATE_STALE_CLAIM_INDEX_SQL,
        CREATE_STATUS_INDEX_SQL,
    )
    from src.state.schema.opportunity_events_schema import (
        CREATE_DAY0_FAMILY_EXTREME_INDEX_SQL,
    )

    path = db_module.ZEUS_WORLD_DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")

    conn = db_module.get_world_connection(write_class="live")
    try:
        for sql in (
            CREATE_DAY0_FAMILY_EXTREME_INDEX_SQL,
            CREATE_STATUS_INDEX_SQL,
            CREATE_PENDING_RETRY_FLOOR_INDEX_SQL,
            CREATE_STALE_CLAIM_INDEX_SQL,
        ):
            conn.execute(sql)
            conn.commit()
        logger.info("world DB hot-index repair complete")
        return "prepared"
    finally:
        conn.close()


def _startup_forecasts_schema_ready_check() -> str:
    """Read-only forecasts DB structural schema check for forecast-live split authority.

    Verifies presence of a minimal set of canonical forecast tables via
    sqlite_master (read-only).  Missing DB or missing tables fail closed.
    B2 (2026-05-28) cancelled the schema-version counter mechanism entirely.
    """
    import sqlite3

    from src.state.db import ZEUS_FORECASTS_DB_PATH, assert_schema_current_forecasts

    _CANONICAL_FORECASTS_TABLES = frozenset({
        "ensemble_snapshots",
        "settlement_outcomes",
        "source_run",
    })

    if not ZEUS_FORECASTS_DB_PATH.exists():
        raise FileNotFoundError(f"{ZEUS_FORECASTS_DB_PATH} does not exist")
    conn = sqlite3.connect(
        f"file:{ZEUS_FORECASTS_DB_PATH.resolve()}?mode=ro",
        uri=True,
        timeout=1.0,
    )
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 1000")
        assert_schema_current_forecasts(conn)
        missing = {
            table
            for table in _CANONICAL_FORECASTS_TABLES
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (table,),
            ).fetchone()
            is None
        }
        if missing:
            raise RuntimeError(
                f"forecasts DB missing canonical tables: {sorted(missing)}"
            )
        return "ready"
    finally:
        conn.close()


def _startup_db_schema_ready_check() -> None:
    """K1 split: directly verify world and forecast DB schema currency.

    Replaces _startup_world_schema_ready_check (retained above as a thin shim).
    Schema currency is verified directly and read-only against zeus-world.db and
    zeus-forecasts.db. This avoids binding live startup to stale JSON sentinels
    from retired or split data-daemon processes, and it avoids running DDL from
    the trading daemon during a restart.

    Retry pattern: 30 × 10s = 5 min (mirrors _startup_freshness_check).
    """
    import time
    from src.control.freshness_gate import BOOT_RETRY_INTERVAL_SECONDS, BOOT_RETRY_MAX_ATTEMPTS

    for attempt in range(1, BOOT_RETRY_MAX_ATTEMPTS + 1):
        missing = []
        try:
            _startup_world_db_schema_ready_check()
            logger.info("world DB schema structural check: ready")
        except Exception as exc:
            logger.warning(
                "world DB schema readiness check failed: %s — running hot-index repair",
                exc,
            )
            try:
                _startup_world_db_hot_index_prepare()
                _startup_world_db_schema_ready_check()
                logger.info("world DB schema structural check: ready after repair")
            except Exception as repair_exc:
                logger.warning(
                    "world DB schema repair/readiness failed: %s — retrying",
                    repair_exc,
                )
                missing.append("world")
        try:
            _startup_forecasts_schema_ready_check()
            logger.info("forecasts DB schema structural check: ready")
        except Exception as exc:
            logger.warning("forecasts DB schema readiness check failed: %s — retrying", exc)
            missing.append("forecasts")

        if not missing:
            return  # World and forecast DB schemas are current.

        if attempt < BOOT_RETRY_MAX_ATTEMPTS:
            logger.info(
                "DB schema checks missing=%s at boot — retry %d/%d in %ds",
                missing, attempt, BOOT_RETRY_MAX_ATTEMPTS, BOOT_RETRY_INTERVAL_SECONDS,
            )
            time.sleep(BOOT_RETRY_INTERVAL_SECONDS)

    raise SystemExit(
        "FATAL: DB schema readiness not proven within 5 min "
        "(zeus-world.db + zeus-forecasts.db structural table checks). "
        "Check direct DB schema initialization and launchctl list com.zeus.forecast-live"
    )


class _BootWalletWarmHolder:
    """Thread-safe-by-join handoff slot for the boot wallet warm thread.

    The warm thread writes ``record`` exactly once (success → BankrollOfRecord,
    swallowed failure → stays None). main() reads it ONLY after joining the
    thread, so no lock is required — the join is the happens-before barrier.
    """

    __slots__ = ("record",)

    def __init__(self):
        self.record = None


# Default join bound for the boot wallet warm thread. The on-chain wallet RPC
# is 5-30s; this caps the worst-case wait so a wedged RPC can't hang boot past
# the gate's own fail-closed budget. A timeout that leaves the thread alive is
# treated as a cold cache (record stays None) → gate fail-closes — never a hang.
_BOOT_WALLET_WARM_JOIN_TIMEOUT_SECONDS = 35.0


def _start_boot_wallet_warm():
    """Spawn a daemon thread that warms bankroll_provider.current() at boot.

    Efficiency #3: the wallet RPC is network-bound while the schema-ready gate /
    registry assert / f109 consolidator / freshness / boot-guards are DB-bound.
    Starting the wallet warm on a background thread right after the venue
    heartbeat lets those DB steps run CONCURRENTLY with the RPC; main() joins
    this thread immediately before the (deterministic) wallet gate.

    The warm fn swallows+logs ANY exception so a warm-thread failure NEVER
    crashes boot — it just leaves a cold cache (holder.record stays None) and
    the wallet gate does its own fail-closed handling. Returns (thread, holder);
    read holder.record only AFTER _join_boot_wallet_warm(thread).
    """
    holder = _BootWalletWarmHolder()

    def _warm():
        try:
            from src.runtime.bankroll_provider import current as _bankroll_current

            holder.record = _bankroll_current()
        except Exception as exc:  # noqa: BLE001 — must never crash boot
            logger.warning(
                "boot wallet warm thread failed (cold cache; wallet gate will "
                "do its own fail-closed fetch): %s",
                exc,
            )

    thread = threading.Thread(
        target=_warm, name="boot-wallet-warm", daemon=True
    )
    thread.start()
    return thread, holder


def _join_boot_wallet_warm(
    thread, timeout: float = _BOOT_WALLET_WARM_JOIN_TIMEOUT_SECONDS
) -> None:
    """Join the boot wallet warm thread so the wallet gate stays deterministic.

    Bounded by ``timeout``: if the warm RPC wedges past it, the thread is left
    running (daemon → dies with the process) and the holder record stays None,
    so the wallet gate fail-closes rather than the boot hanging forever. A
    None/missing thread is a no-op (warm never started → gate self-fetches).
    """
    if thread is None:
        return
    thread.join(timeout=timeout)
    if thread.is_alive():
        logger.warning(
            "boot wallet warm thread did not finish within %.0fs; proceeding "
            "with a cold cache — the wallet gate will fail-closed if the RPC "
            "stays unreachable.",
            timeout,
        )


def _warn_if_cadence_uncovered(
    effective_sweep_period_s: float,
    freshness_window_s: float,
) -> None:
    """Cadence-coverage guard (C5, timing-semantics fix 2026-06-16).

    BASIS: the selection freshness window is only honored when the daemon's
    effective sweep cadence keeps pace.  If effective_sweep_period_s exceeds
    freshness_window_s, the snapshot captured in one cycle is already past the
    freshness deadline by the time the next cycle even starts, so every
    selection silently reads stale data and falls back — the exact
    reactor-lane starvation fixed in #122, now guarded explicitly.

    WARNING only — does NOT raise, does NOT exit, does NOT block boot.
    """
    if effective_sweep_period_s > freshness_window_s:
        logger.warning(
            "CADENCE UNCOVERED: effective sweep period %.1fs exceeds selection "
            "freshness window %.1fs; selections will read stale data and fall "
            "back. Shorten sweep or widen freshness.",
            effective_sweep_period_s,
            freshness_window_s,
        )


# Sentinel: distinguishes "caller handed a warm record (possibly None)" from
# "no warm record supplied — gate must self-fetch via current()".
_WALLET_RECORD_UNSET = object()


def _startup_wallet_check(clob=None, bankroll_record=_WALLET_RECORD_UNSET):
    """P7: Startup wallet reachability warm-up.

    Accepts an optional clob for testing. In production, creates a live
    PolymarketClient.

    Also installs the process-wide CollateralLedger singleton with a
    persistent ledger-owned conn (2026-05-13 remediation). Prior to this
    the singleton was published from `PolymarketClient.get_balance()` while
    that wrapper still owned the conn — the wrapper's `finally: conn.close()`
    immediately poisoned the singleton, blocking every downstream
    `assert_buy_preflight` / `assert_sell_preflight` with
    `collateral_ledger_unconfigured` or `sqlite3.ProgrammingError`.

    Wallet unreachability is fail-closed for new live submit, not fatal for the
    whole daemon. Held-position monitoring, redecision, settlement, and later
    bankroll warm retries must continue; submit/sizing paths consume
    bankroll_provider.cached() and already fail closed when it is unavailable.
    """
    balance = None
    bankroll_unavailable_detail: str | None = None
    if clob is not None:
        # TEST-INJECTION PATH: an explicit clob was supplied. Use it directly
        # and keep the same fail-closed semantics. Production never reaches here.
        try:
            balance = float(clob.get_balance())
            logger.info("Startup wallet check: $%.2f pUSD available", balance)
        except Exception as exc:
            logger.critical(
                "STARTUP_WALLET_UNAVAILABLE: wallet query failed at daemon start; "
                "continuing monitor/redecision while new submit remains fail-closed: %s",
                exc,
            )
    else:
        # PRODUCTION PATH: route the fail-closed wallet-reachability gate through
        # bankroll_provider.current() instead of constructing a SECOND
        # PolymarketClient.
        #
        # Efficiency #3 (warm-overlap): when main() hands a ``bankroll_record``
        # (the result the boot warm thread already fetched via current(), then
        # joined), the gate CONSUMES it — warm + gate together issue exactly ONE
        # current() acquisition. A handed None means the warm fetch failed or
        # was never warmed → the gate fail-closes below (correct fail-safe).
        #
        # When no record is supplied (_WALLET_RECORD_UNSET — direct callers /
        # tests / a boot path without the warm thread) the gate self-fetches via
        # current(). Efficiency #1 still holds: Site A warmed the 30s cache, so
        # current() here is a fresh CACHE HIT with no additional on-chain RPC; on
        # a cold cache it does a real fetch. None keeps the submit lane
        # fail-closed via bankroll_provider.cached() consumers, but no longer
        # kills monitoring/redecision.
        try:
            if bankroll_record is _WALLET_RECORD_UNSET:
                from src.runtime.bankroll_provider import current as _bankroll_current

                rec = _bankroll_current()
            else:
                rec = bankroll_record
        except Exception as exc:
            rec = None
            bankroll_unavailable_detail = (
                f"bankroll_provider.current() raised: {exc}"
            )
        if rec is None:
            bankroll_unavailable_detail = (
                bankroll_unavailable_detail
                or "bankroll_provider returned None at daemon start"
            )
        else:
            balance = rec.value_usd
            logger.info(
                "Startup wallet check: $%.2f pUSD available (source=%s cached=%s)",
                balance, rec.source, rec.cached,
            )

    # Install the process-wide collateral ledger singleton with a ledger-owned
    # persistent conn so downstream executor / riskguard preflight callers do
    # not race against transient conn close. Failures here are non-fatal at
    # boot — preflight will surface `collateral_ledger_unconfigured` if the
    # singleton is missing, which is already the existing fail-closed code
    # path for any operator misconfiguration.
    try:
        from src.state.collateral_ledger import (
            CollateralLedger,
            configure_global_ledger,
        )
        from src.state.db import _zeus_trade_db_path

        ledger = CollateralLedger(db_path=_zeus_trade_db_path())
        configure_global_ledger(ledger)
        logger.info(
            "CollateralLedger global singleton installed (db=%s)",
            _zeus_trade_db_path(),
        )
    except Exception as exc:
        logger.warning(
            "CollateralLedger global singleton install failed (preflight will fail-closed): %s",
            exc,
        )

    if clob is None and balance is None:
        try:
            warm_rec = bankroll_provider.warm_from_collateral_snapshot()
        except Exception as exc:
            warm_rec = None
            logger.warning(
                "Startup collateral snapshot bankroll warm failed "
                "(submit remains fail-closed until a later warm succeeds): %s",
                exc,
            )
        if warm_rec is not None:
            balance = warm_rec.value_usd
            logger.info(
                "Startup wallet check: $%.2f pUSD available "
                "(source=%s cached=%s staleness=%.1fs)",
                balance,
                warm_rec.source,
                warm_rec.cached,
                warm_rec.staleness_seconds,
            )
        else:
            logger.critical(
                "STARTUP_WALLET_UNAVAILABLE: %s; no fresh collateral ledger "
                "snapshot was available at daemon start. Continuing "
                "monitor/redecision while new submit remains fail-closed until "
                "a later bankroll warm succeeds.",
                bankroll_unavailable_detail
                or "wallet query failed before bankroll cache was populated",
            )


def _startup_data_health_check(conn):
    """Warn about deferred data actions on every startup.

    The warnings persist until the actions are taken.
    """
    try:
        forecast_city_count = conn.execute(
            "SELECT COUNT(DISTINCT city) FROM forecast_skill"
        ).fetchone()[0]
        configured_city_count = len(cities_by_name)
        if forecast_city_count < configured_city_count:
            logger.warning(
                "⚠ DATA QUALITY GAP: forecast_skill covers %d/%d configured cities.",
                forecast_city_count,
                configured_city_count,
            )

        # 2. Data freshness check
        stale_tables = []
        for table, col in [
            ("asos_wu_offsets", None),
            ("observation_instants", None),
            ("diurnal_curves", None),
            ("diurnal_peak_prob", None),
            ("temp_persistence", None),
            ("solar_daily", None),
        ]:
            try:
                row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                if row is None:
                    stale_tables.append(f"{table} (empty)")
            except Exception:
                stale_tables.append(f"{table} (missing)")

        if stale_tables:
            logger.warning(
                "⚠ DATA GAPS: %s — run ETL scripts to populate",
                ", ".join(stale_tables),
            )

        # 3. Assumption manifest validation
        try:
            from scripts.validate_assumptions import run_validation

            validation = run_validation()
            if not validation["valid"]:
                logger.warning(
                    "⚠ ASSUMPTION MISMATCHES: %s",
                    " | ".join(validation["mismatches"]),
                )
        except Exception as e:
            logger.warning("⚠ Assumption validation failed to run: %s", e)

    except Exception as e:
        logger.debug("Startup health check failed: %s", e)


def _run_f109_consolidator() -> None:
    """Boot-time F109 consolidation: reduce duplicate open-phase position rows.

    Must run BEFORE the 202605_position_current_idempotent_open_per_token
    migration applies the partial UNIQUE INDEX (that migration's pre-flight
    raises if duplicates still exist). Idempotent: NO-OP on healthy state.

    Failure-tolerant: logs WARNING + returns without raising so the daemon
    continues to boot; the migration's own pre-flight then raises if the DB
    is still inconsistent (fail-closed guarantee preserved).

    Karachi-safe: single-row positions pass the HAVING COUNT(*) > 1 filter
    and are never touched.

    Logs: [F109_CONSOLIDATOR_BOOT] tokens_scanned=N voided=M divergent=K
    """
    from src.state.db import get_trade_connection
    from src.state.position_duplicate_consolidator import consolidate

    try:
        trade_conn = get_trade_connection(write_class="live")
        try:
            report = consolidate(trade_conn)
        finally:
            trade_conn.close()
    except Exception as exc:
        logger.warning(
            "[F109_CONSOLIDATOR_BOOT] failed — continuing boot (migration pre-flight "
            "will enforce hard gate if duplicates remain): %s",
            exc,
        )
        return

    logger.info(
        "[F109_CONSOLIDATOR_BOOT] tokens_scanned=%d voided=%d divergent=%d "
        "chain_snapshot_used=%s",
        report["scanned_tokens"],
        len(report["voided_positions"]),
        len(report["divergent_tokens"]),
        report["chain_snapshot_used"],
    )


def _check_s1_without_s2_sla() -> None:
    """N2 boot gate (PR-S1, Bug #3): refuse boot if S1 deployed >4h without S2.

    Reads state/control_plane.json for s1_deployed_at / s2_deployed_at markers
    written by the deployment script (not Zeus code). If S1 is deployed but S2
    has not been deployed within the SLA window, the daemon exits with code 1.

    Absence of the file or of s1_deployed_at = pre-deployment environment → pass.
    Override: ZEUS_ACCEPT_S1_ALONE=1 environment variable (emergency only).
    """
    import json
    import os
    from datetime import datetime, timedelta, timezone
    from src.config import state_path

    S1_S2_SLA_HOURS = 4

    if os.environ.get("ZEUS_ACCEPT_S1_ALONE") == "1":
        logger.warning("ZEUS_ACCEPT_S1_ALONE=1 set — skipping S1-without-S2 SLA gate")
        return

    control_path = state_path("control_plane.json")
    try:
        with open(control_path) as f:
            payload = json.load(f)
    except FileNotFoundError:
        return  # No deployment marker file — pre-deployment env, pass.
    except (json.JSONDecodeError, OSError) as exc:
        # Malformed or unreadable file → fail-closed.
        logger.error("N2 gate: cannot read control_plane.json: %s", exc)
        raise SystemExit(1) from exc

    if not isinstance(payload, dict):
        # Deployment-script bug produced a non-dict JSON value — fail-closed.
        logger.error(
            "N2 gate: control_plane.json corrupt — non-dict payload (type=%s)",
            type(payload).__name__,
        )
        raise SystemExit(1)

    s1_ts_raw = payload.get("s1_deployed_at")
    if not s1_ts_raw:
        return  # S1 not yet deployed → pass.

    s2_ts_raw = payload.get("s2_deployed_at")
    if s2_ts_raw:
        return  # Both deployed → pass.

    # S1 deployed, S2 missing — check age.
    try:
        s1_dt = datetime.fromisoformat(str(s1_ts_raw).replace("Z", "+00:00"))
        if s1_dt.tzinfo is None:
            s1_dt = s1_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as exc:
        logger.error("N2 gate: s1_deployed_at unparseable (%r): %s", s1_ts_raw, exc)
        raise SystemExit(1) from exc

    age = datetime.now(timezone.utc) - s1_dt
    if age >= timedelta(hours=S1_S2_SLA_HOURS):
        msg = (
            f"S1_WITHOUT_S2_BEYOND_SLA — s1_deployed_at={s1_ts_raw} "
            f"age={age} >= {S1_S2_SLA_HOURS}h — "
            "set ZEUS_ACCEPT_S1_ALONE=1 to override"
        )
        logger.error("BOOT_REFUSED: %s", msg)
        raise SystemExit(msg)


def _assert_live_safe_strategies_or_exit(*, refresh_state: bool = True) -> None:
    """G6 boot guard: refuse live launch when a non-allowlisted strategy is enabled.

    Composes the production-path enabled set:
      enabled = {s for s in strategy_profile.live_safe_keys() if is_strategy_enabled(s)}
    where ``is_strategy_enabled`` reads ``_control_state["strategy_gates"]`` —
    which is empty until ``refresh_control_state()`` hydrates it from the
    ``control_overrides`` table. Without that hydration, every strategy looks
    enabled (default-True) and the guard would refuse every launch regardless
    of operator configuration. So the helper hydrates first by default.

    ``refresh_state=False`` is reserved for tests that supply pre-populated
    state via monkeypatch; production callers should always leave the default.

    On success: returns silently. On refusal: SystemExit with FATAL message
    naming offending strategies (matches src/main.py:472-477 pattern).
    """
    from src.control.control_plane import (
        assert_live_safe_strategies_under_live_mode,
        is_strategy_enabled,
        refresh_control_state,
    )
    from src.strategy.strategy_profile import live_safe_keys
    if refresh_state:
        refresh_control_state()
    enabled_strategies = {s for s in live_safe_keys() if is_strategy_enabled(s)}
    assert_live_safe_strategies_under_live_mode(enabled_strategies)


def _edli_refresh_global_allocator_for_live_bridge(conn) -> dict:
    """Configure the process-wide risk allocator/governor for the EDLI live path.

    ROOT (see /tmp/edli_submit_gate_trace.md): the live ``_live_order`` submit path
    calls ``select_global_order_type`` which raises
    ``AllocationDenied("allocator_not_configured")`` whenever the process singletons
    ``_GLOBAL_ALLOCATOR`` / ``_GLOBAL_GOVERNOR_STATE`` are None. The legacy discover
    cycle (``src/engine/cycle_runner.py``) populates them via
    ``refresh_global_allocator``; the EDLI event-reactor cycle does NOT run that
    legacy cycle, so without this seam every canary order silently blocks.

    Drawdown sourcing (this drives the governor's drawdown kill-switch — getting it
    wrong is a live-capital risk):
      * baseline (``daily_baseline_total``) comes from ``load_portfolio()``.
        NOTE: ``daily_baseline_total`` is structurally 0.0 system-wide (it equals
        ``bankroll`` in the canonical DB loader — see ``src/state/portfolio.py:1790``
        and verified live 2026-05-31). The legacy discover cycle
        (``src/engine/cycle_runner.py:711``) uses ``_drawdown_pct = ... if _baseline
        > 0 else 0.0`` — i.e. it tolerates zero baseline by passing drawdown=0.0 and
        PROCEEDING to configure the allocator. The drawdown-from-baseline kill-switch
        is therefore inert system-wide; real safety layers are riskguard risk_level
        (GREEN gate), trailing-loss reference, bankroll truth, $5 canary cap, and
        Kelly sizing. This seam MUST mirror that same tolerance — a stricter gate
        here would permanently block the EDLI canary while the legacy cycle runs fine.
      * current bankroll comes from the on-chain wallet truth via
        ``bankroll_provider.cached()`` (warmed once per cycle by the EDLI cycle's
        bankroll warm at the top of ``_edli_event_reactor_cycle``). The on-chain
        wallet is the only bankroll truth source in live mode.
      * drawdown_pct mirrors the legacy formula EXACTLY
        (``src/engine/cycle_runner.py:711``):
        ``max((baseline - bankroll) / baseline * 100, 0)`` for ``baseline > 0``,
        ``0.0`` otherwise.

    FAIL-CLOSED: if bankroll cache is None (wallet unreachable) or any exception
    occurs, this does NOT configure an allow-everything allocator. It leaves the
    singletons in their submit-blocking state and returns ``{"configured": False,
    "fail_closed": True, ...}`` so the caller degrades to no-submit this cycle.
    Zero/negative baseline is NOT a fail-closed trigger — it mirrors the legacy
    path's drawdown=0.0 tolerance. Mirrors ``src/engine/cycle_runner.py:718-728``.
    """
    from src.control.heartbeat_supervisor import summary as _heartbeat_summary
    from src.control.ws_gap_guard import summary as _ws_gap_summary
    from src.risk_allocator import refresh_global_allocator
    from src.riskguard.riskguard import get_current_level

    try:
        # On-chain wallet is the only bankroll truth. cached() never re-fetches; the
        # EDLI cycle warms it via current(max_age_seconds=0.0) at cycle start. None →
        # wallet unreachable / cache cold → drawdown untrustworthy → fail closed.
        _bk = bankroll_provider.cached()
        if _bk is None:
            logger.error(
                "EDLI live-bridge allocator refresh: on-chain bankroll cache is None "
                "(wallet unreachable) — drawdown untrustworthy; FAIL-CLOSED, blocking "
                "live submit this cycle (no fake-0.0 drawdown)."
            )
            return {
                "configured": False,
                "fail_closed": True,
                "error": "bankroll_unavailable",
                "entry": {"allow_submit": False, "reason": "allocator_not_configured"},
            }
        _current_bankroll = float(getattr(_bk, "value_usd", 0.0) or 0.0)

        _portfolio = load_portfolio()
        _baseline = float(getattr(_portfolio, "daily_baseline_total", 0.0) or 0.0)

        # Legacy formula EXACTLY (cycle_runner.py:711): drawdown=0.0 when baseline<=0.
        # baseline is structurally 0.0 system-wide; the legacy cycle runs fine with
        # this — we must not impose a stricter gate here.
        _drawdown_pct = (
            max(((_baseline - _current_bankroll) / _baseline) * 100.0, 0.0)
            if _baseline > 0.0
            else 0.0
        )

        _result = refresh_global_allocator(
            conn,
            ledger={"current_drawdown_pct": _drawdown_pct, "risk_level": get_current_level().value},
            heartbeat=_heartbeat_summary(),
            ws_status=_ws_gap_summary(),
        )
        logger.info(
            "EDLI live-bridge allocator refresh: CONFIGURED drawdown_pct=%.3f baseline=%.2f "
            "bankroll=%.2f",
            _drawdown_pct, _baseline, _current_bankroll,
        )
        return _result
    except Exception as _refresh_exc:  # noqa: BLE001 — fail-closed by contract
        # Never let a refresh failure leave an unconfigured-but-proceeding live submit.
        # Reset to the explicit unconfigured (blocking) state so the submit path keeps
        # raising allocator_not_configured, and signal the caller to degrade to no-submit.
        from src.risk_allocator import configure_global_allocator

        try:
            configure_global_allocator(None, None)
        except Exception:  # noqa: BLE001
            pass
        logger.error(
            "EDLI live-bridge allocator refresh FAILED: %s; FAIL-CLOSED, blocking live "
            "submit this cycle (degrade to no-submit).",
            _refresh_exc,
            exc_info=True,
        )
        return {
            "configured": False,
            "fail_closed": True,
            "error": str(_refresh_exc),
            "entry": {"allow_submit": False, "reason": "allocator_not_configured"},
        }


def _refresh_global_allocator_for_held_position_monitor(conn, portfolio) -> dict:
    """Configure risk allocator before held-position exit decisions run.

    The held-position monitor is an independent live lane and can run before the
    EDLI reactor's allocator refresh after daemon restart. It must not reach the
    executor with unconfigured risk singletons, because that turns real exit
    decisions into ``allocator_not_configured`` backoff.
    """

    from src.control.heartbeat_supervisor import summary as _heartbeat_summary
    from src.control.ws_gap_guard import summary as _ws_gap_summary
    from src.risk_allocator import configure_global_allocator, refresh_global_allocator
    from src.riskguard.riskguard import get_current_level

    try:
        _baseline = float(getattr(portfolio, "daily_baseline_total", 0.0) or 0.0)
        _current_bankroll = float(getattr(portfolio, "bankroll", 0.0) or 0.0)
        _drawdown_pct = (
            max(((_baseline - _current_bankroll) / _baseline) * 100.0, 0.0)
            if _baseline > 0.0
            else 0.0
        )
        result = refresh_global_allocator(
            conn,
            ledger={
                "current_drawdown_pct": _drawdown_pct,
                "risk_level": get_current_level().value,
            },
            heartbeat=_heartbeat_summary(),
            ws_status=_ws_gap_summary(),
        )
        logger.info(
            "held-position monitor allocator refresh: configured=%r drawdown_pct=%.3f",
            result.get("configured"),
            _drawdown_pct,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - fail closed with explicit state.
        try:
            configure_global_allocator(None, None)
        except Exception:  # noqa: BLE001
            pass
        logger.error(
            "held-position monitor allocator refresh FAILED: %s; exit submit remains fail-closed",
            exc,
            exc_info=True,
        )
        return {
            "configured": False,
            "fail_closed": True,
            "error": str(exc),
            "entry": {"allow_submit": False, "reason": "allocator_not_configured"},
        }


# WIRING FIX (operator Point-1 directive 2026-06-08): the BAYES_PRECISION_FUSION/replacement forecast
# PRODUCTION functions (raw-input download + light live materialization) were moved
# VERBATIM to src/data/replacement_forecast_production.py and are now SCHEDULED on the
# forecast-live (data) daemon, NOT here. Heavy forecast fetches must never run
# inside the live-trading process (they monopolized disk I/O -> DATA_DEGRADED flap). They
# are imported back into this module ONLY so the in-cycle runtime-flags read below and
# existing by-name references (tests, runtime-wiring-audit anchors) keep resolving — the
# live-trading scheduler no longer registers the download/materialize jobs.
from src.data.replacement_forecast_production import (  # noqa: E402
    _download_replacement_forecast_current_targets_if_needed,
    _download_bayes_precision_fusion_extra_raw_inputs_if_needed,
    _replacement_forecast_download_cycle,
    _replacement_forecast_live_materialization_queue_config,
    _replacement_forecast_live_materialize_cycle,
    _replacement_forecast_runtime_flags_from_settings,
)


def _replacement_forecast_refit_decision_from_settings():
    from src.config import PROJECT_ROOT
    from src.data.replacement_forecast_refit_handoff import refit_decision_from_handoff_payload

    cfg = _settings_section("replacement_forecast_live", {}) or {}
    raw_path = cfg.get("refit_handoff_path") or "state/replacement_forecast_live/refit_handoff.json"
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - fail closed at switch decision
        logger.warning("replacement forecast refit handoff unreadable: %s", exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("replacement forecast refit handoff must be a JSON object: %s", path)
        return None
    try:
        return refit_decision_from_handoff_payload(payload)
    except Exception as exc:  # noqa: BLE001 - fail closed at switch decision
        logger.warning("replacement forecast refit handoff invalid: %s", exc)
        return None


# DEAD-PROMOTION-APPARATUS REMOVAL (2026-06-16): the promotion / capital-objective
# evidence parsers (_replacement_forecast_{promotion,capital_objective}_evidence_from_
# settings) were REMOVED. They imported the deleted go_live_report verdict module and
# fed the runtime-policy resolver / switch-decision evaluator — both of which ignore
# these objects after the live runtime flag path moved to runtime_layer='live'. The two
# live-adapter call sites now pass None (the adapter default),
# which is behavior-identical. See docs/evidence/timing_audit/.
def _sqlite_table_names(conn) -> tuple[str, ...]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            names.append(str(row["name"]))
        else:
            names.append(str(row[0]))
    return tuple(sorted(names))


def _current_live_fact_status(relative_path: str) -> str:
    from src.config import PROJECT_ROOT

    path = PROJECT_ROOT / relative_path
    try:
        first_lines = path.read_text(encoding="utf-8").splitlines()[:20]
    except OSError:
        return "STALE_FOR_LIVE"
    for line in first_lines:
        if line.startswith("Status:"):
            return "CURRENT_FOR_LIVE" if "CURRENT_FOR_LIVE" in line else "STALE_FOR_LIVE"
    return "STALE_FOR_LIVE"


# WIRING FIX (operator Point-1 directive 2026-06-08): _replacement_forecast_download_cycle
# and _replacement_forecast_live_materialize_cycle were MOVED to
# src/data/replacement_forecast_production.py and are now SCHEDULED on the forecast-live
# (data) daemon (src/ingest/forecast_live_daemon.py). They are imported back into this
# module (top of file) for by-name resolution only; the live-trading scheduler no longer
# registers them.


@_scheduler_job("edli_event_reactor")
def _edli_event_reactor_cycle() -> None:
    """EDLI event-reactor scheduler hook.

    Cut 10 wires daemon scheduling and schema/config readiness. The live-money
    submit adapter still uses injected gates; until an event is explicitly
    accepted by those gates, this job is conservative and side-effect free.
    """

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled") or not edli_cfg.get("event_writer_enabled"):
        return
    if _defer_for_held_position_monitor("edli_event_reactor"):
        return
    if _edli_reactor_active():
        logger.warning("EDLI reactor skipped: previous EDLI reactor cycle is still running")
        return
    import sqlite3  # transient world-DB lock classification for fail-soft emit boundary
    from src.engine.event_reactor_adapter import (
        edli_source_truth_gate,
        event_bound_live_adapter_from_trade_conn,
        event_bound_no_submit_adapter_from_trade_conn,
        executable_snapshot_gate_from_trade_conn,
        replacement_forecast_baseline_bundle_provider_from_forecast_conn,
        riskguard_allows_new_entries,
    )
    from src.engine.event_bound_final_intent import submit_event_bound_final_intent_via_existing_executor
    from src.events.event_priority import day0_is_tradeable_for_scope
    from src.events.event_store import EventStore
    from src.events.reactor import OpportunityEventReactor, ReactorConfig
    from src.riskguard.riskguard import get_current_level
    from src.state.db import ZEUS_FORECASTS_DB_PATH, get_forecasts_connection_read_only, get_trade_connection_with_world_required, get_world_connection
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

    if not _edli_reactor_active_lock.acquire(blocking=False):
        logger.warning("EDLI reactor skipped: previous EDLI reactor cycle is still running")
        return
    try:
        conn = get_world_connection()
    except Exception:
        _edli_reactor_active_lock.release()
        raise
    # K1: the calibration authority is split — platt_models lives in the world DB (this conn's
    # main) while calibration_pairs lives in the forecasts DB. get_calibrator reads BOTH, so the
    # calibration_conn must have forecasts attached for the unqualified calibration_pairs read to
    # resolve; otherwise every live decision fails CALIBRATION_AUTHORITY_MISSING:calibration store
    # unavailable. Read-only attach (no cross-DB write), idempotent.
    try:
        _attached_dbs = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" not in _attached_dbs:
            conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
    except Exception as _attach_exc:  # noqa: BLE001 - non-fatal; calibration will fail-closed if unresolved
        logger.warning("EDLI reactor: ATTACH forecasts to calibration conn failed (non-fatal): %r", _attach_exc)
    try:
        forecasts_conn = get_forecasts_connection_read_only()
    except Exception:
        conn.close()
        _edli_reactor_active_lock.release()
        raise
    # Warm the in-process bankroll-of-record cache from the durable collateral
    # ledger snapshot so the per-event no-submit Kelly proof can read
    # bankroll_provider.cached() without performing venue/RPC I/O inside the
    # reactor. The post-trade-capital sidecar owns wallet refreshes; this live
    # scheduler consumes only local fresh truth. Non-fatal — Kelly fails closed
    # (KELLY_PROOF_MISSING) if the ledger is absent/stale/degraded.
    try:
        from src.runtime import bankroll_provider as _bankroll_provider

        _bk_warm = _bankroll_provider.warm_from_collateral_snapshot()
        if _bk_warm is None:
            logger.error(
                "EDLI reactor: bankroll ledger warm returned None — cache cold, Kelly will "
                "fail closed (KELLY_PROOF_MISSING). Collateral snapshot is missing, stale, "
                "or degraded."
            )
    except Exception as _bk_exc:  # noqa: BLE001
        logger.warning("EDLI reactor: bankroll cache warm failed (non-fatal): %r", _bk_exc)
    try:
        from src.state.db import world_write_mutex as _world_write_mutex

        _stage_started = time.monotonic()

        def _log_stage(stage: str) -> None:
            nonlocal _stage_started
            _now_mono = time.monotonic()
            _elapsed = _now_mono - _stage_started
            if _elapsed >= 1.0:
                logger.info("EDLI reactor stage completed: %s elapsed_s=%.3f", stage, _elapsed)
            _stage_started = _now_mono

        now = datetime.now(timezone.utc)
        received_at = now.isoformat()
        forecast_emit_limit = _edli_positive_int_or_unbounded(
            edli_cfg, "forecast_snapshot_emit_limit", default=20, maximum=50
        )
        day0_emit_limit = _edli_bounded_positive_int(edli_cfg, "day0_catchup_emit_limit", default=20, maximum=100)
        # Live cadence invariant: full coverage is achieved by fair rotation across
        # continuous cycles, not by processing an unbounded queue in one cycle. The
        # unbounded 2026-06-12 setting let stale substrate / slow JIT book events hold
        # one reactor run past the 60s scheduler cadence, so the next run skipped and
        # entry/day0/redecision stalled. Bound per-cycle work; events not reached stay
        # pending and are reached by EventStore's city/lane fairness.
        proof_limit = _edli_bounded_positive_int(
            edli_cfg,
            "reactor_process_limit",
            default=12,
            maximum=50,
        )
        store = EventStore(conn)
        #
        # PR#404 P0-2 (operator merge blocker): the day0 fast lane's network IO
        # (aviationweather METAR fetch, WU anomaly cross-check, open-meteo
        # hourly-vector refresh) MUST happen BEFORE the mutex is acquired —
        # the world-write mutex exists for WAL writer exclusion and must never
        # span an external HTTP call (a slow venue/API response would serialize
        # every world writer: ingestor, collateral heartbeat, reactor drain).
        # The prefetch returns a pure in-memory snapshot; only EventWriter
        # writes happen inside the mutex.
        _day0_fast_prefetch = None
        if (
            edli_cfg.get("day0_extreme_trigger_enabled")
            and edli_cfg.get("day0_authority_catchup_scanner_enabled", False)
        ):
            _day0_fast_prefetch = _edli_prefetch_day0_fast_obs(decision_time=now)
        _log_stage("day0_prefetch")
        _day0_family_admission: _Day0LiveFamilyAdmission | None = None
        if (
            edli_cfg.get("day0_extreme_trigger_enabled")
            and edli_cfg.get("day0_authority_catchup_scanner_enabled", False)
        ):
            try:
                from src.state.db import get_trade_connection_read_only as _get_trade_ro

                _day0_admission_trade_conn = _get_trade_ro()
                try:
                    _day0_family_admission = _edli_day0_live_family_admission(
                        forecasts_conn,
                        _day0_admission_trade_conn,
                        decision_time=now,
                    )
                finally:
                    _day0_admission_trade_conn.close()
            except Exception as _day0_admission_exc:  # noqa: BLE001
                logger.warning(
                    "EDLI day0 live family admission build failed; Day0 execution emit "
                    "will be restricted this cycle and unmarketed pending expiry skipped: %r",
                    _day0_admission_exc,
                )
                _day0_family_admission = _Day0LiveFamilyAdmission(
                    admitted_families=frozenset(),
                    expiry_safe=False,
                )
        _prune_mutex = _world_write_mutex()
        _prune_lock_timeout_s = _edli_prune_lock_timeout_seconds(edli_cfg)
        _prune_acquired = _prune_mutex.acquire(timeout=_prune_lock_timeout_s)
        if _prune_acquired:
            try:
                _edli_prune_pending_working_set(
                    store,
                    decision_time=now,
                    day0_family_admission=_day0_family_admission,
                )
                conn.commit()
            finally:
                _prune_mutex.release()
        else:
            logger.warning(
                "EDLI reactor prune skipped: world write mutex unavailable after %.3fs; "
                "deferring maintenance so the money-path reactor can drain events.",
                _prune_lock_timeout_s,
            )
        _log_stage("pending_prune")
        _fsr_events = []
        if edli_cfg.get("forecast_snapshot_trigger_enabled"):
            try:
                _fair_source = _edli_next_redecision_source()
                _fsr_pending = _edli_pending_entity_keys(
                    conn,
                    event_types=("FORECAST_SNAPSHOT_READY",),
                )
                _fsr_events = _edli_build_forecast_snapshot_events(
                    conn,
                    decision_time=now,
                    received_at=received_at,
                    limit=forecast_emit_limit,
                    source=_fair_source,
                    already_pending_keys=_fsr_pending,
                    suppress_recent_no_value_refutations=True,
                    budget_seconds=_edli_forecast_snapshot_build_budget_seconds(edli_cfg),
                )
                _log_stage("forecast_snapshot_build")
            except sqlite3.OperationalError as _emit_lock_exc:
                if "locked" in str(_emit_lock_exc).lower() or "busy" in str(_emit_lock_exc).lower():
                    logger.warning(
                        "EDLI reactor: forecast-snapshot build hit transient DB lock "
                        "(%r) — skipping emit this cycle, draining already-queued candidates.",
                        _emit_lock_exc,
                    )
                else:
                    raise
        # EDLI live contention fix (2026-05-31): the FSR/Day0/redecision
        # EMIT block writes opportunity_events to the WAL zeus-world.db shared
        # in-process with the market-channel ingestor. Serialize the whole
        # prune+emit+commit unit under the process-global world-DB write mutex so it
        # never holds the WAL write lock concurrently with the ingestor. Forecast
        # selection/no-value refutation and Day0 HTTP have already completed above;
        # the mutex only covers prune/write/commit.
        # Explicit acquire/finally (not ``with``) to avoid reindenting the block.
        _emit_mutex = _world_write_mutex()
        _emit_mutex.acquire()
        try:
            if edli_cfg.get("forecast_snapshot_trigger_enabled"):
                # FAIL-SOFT (2026-05-31): the FSR event-emit is the queue-FILL step, writing
                # opportunity_events to the WAL world DB shared with the market-channel
                # ingestor and CollateralLedger heartbeat. Under live load that DB hits
                # transient "database is locked" past the 30s busy_timeout. A locked-out
                # emit must NOT crash the whole reactor cycle — the cycle should still drain
                # candidates already queued from prior cycles. Catch ONLY the transient lock
                # (narrow, by message) and continue; real schema/logic faults still propagate.
                try:
                    _current_fsr_pending = _edli_pending_entity_keys(
                        conn,
                        event_types=("FORECAST_SNAPSHOT_READY",),
                    )
                    _fresh_fsr_events = [
                        event for event in _fsr_events
                        if event.entity_key not in _current_fsr_pending
                    ]
                    from src.events.event_writer import EventWriter

                    EventWriter(conn).write_many(_fresh_fsr_events)
                    _log_stage("forecast_snapshot_emit")
                except sqlite3.OperationalError as _emit_lock_exc:
                    if "locked" in str(_emit_lock_exc).lower() or "busy" in str(_emit_lock_exc).lower():
                        logger.warning(
                            "EDLI reactor: forecast-snapshot emit hit transient world-DB lock "
                            "(%r) — skipping emit this cycle, draining already-queued candidates.",
                            _emit_lock_exc,
                        )
                    else:
                        raise
            # Continuous re-decision admission is intentionally NOT all-universe here.
            # The dedicated screen job below owns EDLI_REDECISION_PENDING and admits only
            # families with confirmed trade value, maker rests needing action, or held
            # positions with money at risk. The reactor still emits ordinary
            # FORECAST_SNAPSHOT_READY candidates for new-entry discovery above.
            if (
                edli_cfg.get("day0_extreme_trigger_enabled")
                and edli_cfg.get("day0_authority_catchup_scanner_enabled", False)
            ):
                _day0_trade_conn = get_trade_connection_with_world_required(write_class=None)
                try:
                    try:
                        _edli_emit_day0_extreme_events(
                            conn,
                            _day0_trade_conn,
                            decision_time=now,
                            received_at=received_at,
                            limit=day0_emit_limit,
                            # PR#404 P0-2: HTTP was prefetched OUTSIDE the mutex
                            # (_day0_fast_prefetch above, before acquire); this call
                            # is the pure write phase.
                            fast_prefetch=_day0_fast_prefetch,
                            # Stamp scope-aware emission priority. Production live
                            # scope makes Day0 tradeable.
                            day0_is_tradeable=day0_is_tradeable_for_scope(
                                str(edli_cfg.get("edli_live_scope") or "forecast_plus_day0")
                            ),
                            budget_seconds=_edli_day0_emit_budget_seconds(edli_cfg),
                            family_admission=_day0_family_admission,
                        )
                        _log_stage("day0_emit")
                    except sqlite3.OperationalError as _day0_emit_lock_exc:
                        if _edli_is_sqlite_lock_error(_day0_emit_lock_exc):
                            logger.warning(
                                "EDLI reactor: day0 emit still locked after bounded retry "
                                "(%r) — skipping Day0 emit this cycle and draining already-queued candidates.",
                                _day0_emit_lock_exc,
                            )
                        else:
                            raise
                finally:
                    _day0_trade_conn.close()
            # Commit the emit WRITE UNIT (FSR + redecision + day0 → opportunity_events)
            # while still holding the world-DB write mutex, so the WAL write lock is
            # released by the COMMIT before any other writer (ingestor / collateral
            # heartbeat) can interleave. No HTTP/venue work runs inside this block.
            conn.commit()
            _log_stage("emit_commit")
        finally:
            _emit_mutex.release()
        # THROUGHPUT STRUCTURAL FIX (2026-06-01): the executable-snapshot refresh
        # (_refresh_pending_family_snapshots) runs a full-universe Gamma scan
        # (find_weather_markets → _get_active_events, benchmarked ~76s COLD; TTL 300s
        # so it re-ran nearly every cycle) + per-token CLOB /book capture across all
        # pending-family bins. Running it INLINE here made the reactor cycle wall-clock
        # blow past the 1-min APScheduler interval (overlapping triggers coalesced/
        # skipped → 0 completed cycles → 0 receipts/trades despite the live submit path
        # being CODE-CLEAR to the venue POST boundary). It is now DECOUPLED into the
        # dedicated _edli_market_substrate_warm_cycle job (mirroring _edli_bankroll_warm_cycle,
        # #45), so this reactor cycle reads ALREADY-captured snapshots (DB-only,
        # microseconds) and reaches process_pending → submit in seconds. Decision
        # semantics are UNCHANGED: a family not yet captured by the warm job still
        # requeues via the reactor's existing EXECUTABLE_SNAPSHOT_RETRY path (fail-closed).
        trade_conn = get_trade_connection_with_world_required(write_class=None)

        regret_ledger = NoTradeRegretLedger(conn)
        reactor_mode = str(edli_cfg.get("reactor_mode", "live"))
        edli_live_scope = str(edli_cfg.get("edli_live_scope") or "forecast_plus_day0")
        real_order_submit_enabled = bool(edli_cfg.get("real_order_submit_enabled", False))
        submit_disabled_effective_mode = reactor_mode == "live_no_submit"
        live_bridge_mode = reactor_mode == "live"
        real_submit_effective = real_order_submit_enabled if reactor_mode == "live" else False
        # Configure the process-wide risk allocator/governor BEFORE the submit adapter is
        # built so the live submit path's select_global_order_type does not raise
        # AllocationDenied("allocator_not_configured"). The legacy discover cycle wires this
        # via refresh_global_allocator; the EDLI cycle does not run that cycle, so without
        # this seam every canary order silently blocks (see /tmp/edli_submit_gate_trace.md).
        # FAIL-CLOSED: if the refresh cannot source a trustworthy drawdown (wallet unreachable
        # / baseline undefined / exception), block THIS cycle to the no-submit adapter rather
        # than submit live with an unconfigured-but-proceeding allocator.
        # SUBMIT-LANE STAMP (silent-trade-kill antibody 2026-06-12): track the TYPED
        # cause whenever a live block clears live_submit_effective so the no-submit adapter
        # can name it on every full-pass receipt it consumes (single source of truth —
        # the same value that drove the selector off the live lane). None => no live block
        # (the live lane was simply not configured for this reactor_mode).
        _live_lane_block_cause: str | None = None
        live_submit_effective = live_bridge_mode or submit_disabled_effective_mode
        if live_submit_effective:
            _alloc_refresh = _edli_refresh_global_allocator_for_live_bridge(trade_conn)
            if live_bridge_mode and not _alloc_refresh.get("configured"):
                live_submit_effective = False
                _alloc_reason = _alloc_refresh.get("entry", {}).get("reason") or "allocator_not_configured"
                _live_lane_block_cause = f"live_submit_effective_false:allocator_refresh:{_alloc_reason}"
                logger.error(
                    "EDLI reactor: live-bridge allocator refresh did not configure "
                    "(fail_closed=%r reason=%r) — selecting NO-SUBMIT this cycle.",
                    _alloc_refresh.get("fail_closed"),
                    _alloc_refresh.get("entry", {}).get("reason"),
                )
        # Task #107 (portfolio/multi Kelly): source the PortfolioState ONCE per
        # reactor cycle (DB-only, microseconds) so per-event Kelly sizes against
        # the bankroll NET of correlation-weighted committed capital. The
        # provider closure hands the SAME cached snapshot to every event this
        # cycle (cycle-level read, not per-decision — mirrors the bankroll warm).
        # The no-submit adapter may still build read-only receipts when the portfolio
        # snapshot is unavailable. Real-submit is different: never let the live path
        # fall back to pre-#107 single-asset Kelly sizing, because that ignores
        # open/pending/correlated exposure.
        _portfolio_state_provider = None
        try:
            _portfolio_snapshot = load_portfolio()
            _portfolio_state_provider = lambda: _portfolio_snapshot  # noqa: E731 — cycle-scoped closure
        except Exception as _portfolio_exc:  # noqa: BLE001 — mode-sensitive fail-closed below
            logger.warning(
                "EDLI reactor: portfolio snapshot load failed; no-submit telemetry may observe "
                "with single-asset sizing, but real-submit will fail closed: %r",
                _portfolio_exc,
            )
        if real_submit_effective and _portfolio_state_provider is None:
            live_submit_effective = False
            _live_lane_block_cause = "live_submit_effective_false:portfolio_state_unavailable"
            logger.error(
                "EDLI reactor: real submit disabled this cycle because portfolio_state_unavailable"
            )
        # The FSR/redecision emit phase intentionally uses the cycle-start timestamp for
        # event identity. Decision certificates are built later, after DB-backed substrate
        # and portfolio reads; use the actual processing timestamp so fresh executable/book
        # parent certificates are never later than the decision they support.
        process_pending_decision_time = datetime.now(timezone.utc)
        replacement_forecast_runtime_flags = _replacement_forecast_runtime_flags_from_settings()
        replacement_forecast_refit_decision = _replacement_forecast_refit_decision_from_settings()
        # DEAD-PROMOTION-APPARATUS REMOVAL (2026-06-16): the runtime-policy resolver and
        # switch-decision evaluator ignore these evidence objects after the live runtime
        # flag path moved to runtime_layer='live'. None is behavior-identical to the deleted parsers.
        replacement_forecast_promotion_evidence = None
        replacement_forecast_capital_objective_evidence = None
        replacement_forecast_baseline_bundle_provider = replacement_forecast_baseline_bundle_provider_from_forecast_conn(
            forecasts_conn
        )
        replacement_forecast_world_tables = _sqlite_table_names(conn)
        from src.data.replacement_forecast_live_switch_surface import (
            CURRENT_DATA_FACT_FILE,
            CURRENT_SOURCE_FACT_FILE,
        )

        replacement_forecast_source_fact_status = _current_live_fact_status(CURRENT_SOURCE_FACT_FILE)
        replacement_forecast_data_fact_status = _current_live_fact_status(CURRENT_DATA_FACT_FILE)
        # FIX-2b (PR_SPEC.md §2): mint the operator-arm token IFF edli_live_operator_authorized
        # is True. The live submit adapter is selected ONLY when (live_submit_effective AND
        # operator_arm is not None); otherwise the no-submit adapter is chosen. This gates
        # EVERY real submit (canary included) at the EDLI boundary by TYPE. The mainline
        # executor never constructs this adapter, so the 293-order mainline is untouched.
        operator_arm = require_operator_arm(edli_cfg)
        # SUBMIT-LANE STAMP + CYCLE-LEVEL LIVE-BLOCK SIGNAL (silent-trade-kill antibody
        # 2026-06-12; /tmp/allpass_nosubmit_rootcause.md). The selector picks the live
        # adapter ONLY when (live_submit_effective AND operator_arm is not None); else
        # the no-submit adapter. Resolve the TYPED cause once, here, so it is
        # the single source of truth threaded onto the control-blocked lane's receipts.
        _edli_live_operator_authorized = edli_cfg.get("edli_live_operator_authorized") is True
        _live_lane_selected = bool(live_submit_effective and operator_arm is not None)
        if operator_arm is None and _live_lane_block_cause is None:
            _live_lane_block_cause = "operator_arm_none"
        if _live_lane_block_cause is None and not _live_lane_selected:
            # live_submit_effective was False without a tracked live block.
            _live_lane_block_cause = f"live_lane_unselected:reactor_mode={reactor_mode}"
        _no_submit_live_block_cause = _live_lane_block_cause or "live_lane_unselected"
        # LOUD cycle-level live-block signal: the live lane is dark THIS cycle while the
        # operator has nominally armed it (reactor_mode=live + operator_authorized). The
        # crash-loop incident ran ~50 min on the no-submit lane with the arm on and NO
        # decision-lane signal. One ERROR per cycle here makes it impossible to miss.
        if not _live_lane_selected and _edli_live_operator_authorized and reactor_mode == "live":
            logger.error(
                "LIVE LANE DARK: no-submit adapter selected while operator arm is on "
                "(reactor_mode=live, edli_live_operator_authorized=True) — cause=%s. "
                "Full-pass candidates this cycle are consumed on the NO_SUBMIT_ADAPTER "
                "lane (receipts stamped with this cause); the live lane submitted nothing.",
                _no_submit_live_block_cause,
            )
        # Decision-triggered targeted substrate marker: when the adapter sees stale
        # executable prices, it marks the family for sidecar capture and returns
        # False so the stale event requeues fail-closed. Snapshot writes are owned
        # by the substrate-observer daemon, not the decision critical path.
        _decision_family_snapshot_refresher = _edli_decision_family_snapshot_refresher(
            forecasts_conn
        )
        # ALWAYS-DECIDABLE invariant (operator law 2026-06-12): a blocked event must
        # create visible refresh work. The substrate-observer sidecar owns broad
        # universe warming, but an event that just blocked on stale executable
        # evidence needs a targeted family recapture; otherwise stale events can
        # requeue forever while broad warming rotates past them.
        _reactor_family_snapshot_refresher = _decision_family_snapshot_refresher
        _reactor_cycle_advance_enqueuer = _edli_reactor_cycle_advance_enqueuer()
        _reactor_day0_hourly_refresher = _edli_reactor_day0_hourly_refresher()
        _reactor_family_market_absence_provider = (
            _edli_reactor_family_market_absence_provider()
        )
        submit_adapter = (
            event_bound_live_adapter_from_trade_conn(
                trade_conn,
                forecast_conn=forecasts_conn,
                topology_conn=forecasts_conn,
                calibration_conn=conn,
                get_current_level=get_current_level,
                portfolio_state_provider=_portfolio_state_provider,
                real_order_submit_enabled=real_submit_effective,
                durable_submit_outbox_enabled=bool(edli_cfg.get("durable_submit_outbox_enabled", False)),
                replacement_forecast_runtime_flags=replacement_forecast_runtime_flags,
                replacement_forecast_baseline_bundle_provider=replacement_forecast_baseline_bundle_provider,
                replacement_forecast_world_tables=replacement_forecast_world_tables,
                replacement_forecast_source_fact_status=replacement_forecast_source_fact_status,
                replacement_forecast_data_fact_status=replacement_forecast_data_fact_status,
                replacement_forecast_refit_decision=replacement_forecast_refit_decision,
                replacement_forecast_promotion_evidence=replacement_forecast_promotion_evidence,
                replacement_forecast_capital_objective_evidence=replacement_forecast_capital_objective_evidence,
                pre_submit_authority_provider=_edli_pre_submit_authority_provider_from_book_evidence_conn(
                    trade_conn,
                    edli_cfg,
                    # GATE #84: in live-submit mode the pre-submit authority pulls a
                    # just-in-time live book for the selected candidate so quote_age
                    # reflects observation-to-submit latency, not the venue's coarse
                    # book-change stamp on the shared feasibility feed.
                    book_quote_provider=(
                        _edli_pre_submit_jit_book_quote_provider() if live_submit_effective else None
                    ),
                ),
                executor_submit=lambda final_intent_cert, execution_command_cert: submit_event_bound_final_intent_via_existing_executor(
                    final_intent_cert=final_intent_cert,
                    execution_command_cert=execution_command_cert,
                    conn=trade_conn,
                    snapshot_conn=trade_conn,
                    decision_time=process_pending_decision_time,
                ),
                operator_arm=operator_arm,
                # Production live scope: forecast and Day0 share the same
                # submit boundary.
                edli_live_scope=edli_live_scope,
                family_snapshot_refresher=_decision_family_snapshot_refresher,
            )
            if (live_submit_effective and operator_arm is not None)
            else event_bound_no_submit_adapter_from_trade_conn(
                trade_conn,
                forecast_conn=forecasts_conn,
                topology_conn=forecasts_conn,
                calibration_conn=conn,
                get_current_level=get_current_level,
                portfolio_state_provider=_portfolio_state_provider,
                replacement_forecast_runtime_flags=replacement_forecast_runtime_flags,
                replacement_forecast_baseline_bundle_provider=replacement_forecast_baseline_bundle_provider,
                replacement_forecast_world_tables=replacement_forecast_world_tables,
                replacement_forecast_source_fact_status=replacement_forecast_source_fact_status,
                replacement_forecast_data_fact_status=replacement_forecast_data_fact_status,
                replacement_forecast_refit_decision=replacement_forecast_refit_decision,
                replacement_forecast_promotion_evidence=replacement_forecast_promotion_evidence,
                replacement_forecast_capital_objective_evidence=replacement_forecast_capital_objective_evidence,
                family_snapshot_refresher=_decision_family_snapshot_refresher,
                # SUBMIT-LANE STAMP: name the live-block cause that selected this lane so a
                # full-pass receipt consumed here can never be confused with a genuine
                # decision-declined no-submit (single source of truth from the selector).
                live_block_cause=_no_submit_live_block_cause,
            )
        )

        reactor = OpportunityEventReactor(
            store,
            source_truth_gate=edli_source_truth_gate,
            executable_snapshot_gate=executable_snapshot_gate_from_trade_conn(
                trade_conn,
                topology_conn=forecasts_conn,
            ),
            riskguard_gate=riskguard_allows_new_entries(get_current_level=get_current_level),
            final_intent_submit=submit_adapter,
            reject=lambda _event, _stage, _reason: None,
            regret_ledger=regret_ledger,
            # ALWAYS-DECIDABLE invariant (operator law 2026-06-12): the reactor refreshes a blocked
            # family's substrate as part of the SAME handling (Build 1 snapshot refresher + Build 2
            # single-family cycle-advance reseed), so requeue-without-refresh is structurally
            # impossible for refreshable substrate classes.
            family_snapshot_refresher=_reactor_family_snapshot_refresher,
            cycle_advance_enqueuer=_reactor_cycle_advance_enqueuer,
            day0_hourly_refresher=_reactor_day0_hourly_refresher,
            # Held-position families are refreshed FIRST (money at risk); NO liquidity ordering
            # (operator correction 2026-06-12). Fail-soft read-only provider on zeus_trades.
            held_family_provider=_edli_reactor_held_family_provider(),
            # Current Gamma-empty/no-listed-market proof terminalizes only the blocked event; a
            # future event for the same family can still process if the venue lists later.
            family_market_absence_provider=_reactor_family_market_absence_provider,
            config=ReactorConfig(
                reactor_mode=reactor_mode,
                real_order_submit_enabled=real_order_submit_enabled,
                # Task #102 book-wide edge-zone admission. Absent key => default
                # False => byte-identical legacy money-path (the operator owns
                # config/settings.json; this reads it without writing it).
                # Scope-aware claim tier. Production live scope makes Day0
                # tradeable and rank as fresh alpha.
                day0_is_tradeable=day0_is_tradeable_for_scope(edli_live_scope),
                # SUBMIT-LANE PERSIST-BOUNDARY INVARIANT (silent-trade-kill antibody
                # 2026-06-12): the SAME operator-arm authority the selector above reads,
                # threaded so the reactor's no-submit persist boundary can recognise a
                # nominally-armed live daemon and refuse to silently book a LIVE-stamped
                # full-pass NO_SUBMIT. Not a second authority — the same flag value.
                edli_live_operator_authorized=_edli_live_operator_authorized,
            ),
        )
        _log_stage("reactor_construct")
        _rr = reactor.process_pending(decision_time=process_pending_decision_time, limit=proof_limit)
        _log_stage("process_pending")
        _rejection_counts = dict(Counter(_rr.rejection_reasons))
        _edli_candidates = int(_rr.proof_accepted + _rr.rejected + _rr.retried + _rr.dead_lettered)
        # FIX-4 (P2): read the per-cycle live-submit and venue-ack counters from
        # the adapter.  The live adapter exposes _live_submit_count and
        # _live_ack_count (mutable 1-element lists) after FIX-4; the no-submit
        # adapter and any legacy adapter do not, so getattr returns [0] for both
        # → live_submit_attempts=0 and live_venue_acks=0 (correct for no-submit
        # cycles).
        # FAIL-SOFT counter read (Copilot PR#404): honor the FIX-4 closure
        # counter when it has the expected 1-element-list shape; any other
        # shape (legacy adapter, int, empty list, None) reads as 0 instead of
        # crashing the status-pulse write.
        _live_submit_count_ref = getattr(submit_adapter, "_live_submit_count", [0])
        try:
            _live_submit_attempts = int(_live_submit_count_ref[0])
        except (TypeError, IndexError, KeyError, ValueError):
            _live_submit_attempts = 0
        _live_ack_count_ref = getattr(submit_adapter, "_live_ack_count", [0])
        _live_venue_acks = int(_live_ack_count_ref[0])
        try:
            from src.observability.status_summary import write_cycle_pulse

            write_cycle_pulse(
                _build_edli_status_pulse(
                    started_at=process_pending_decision_time.isoformat(),
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    candidates=_edli_candidates,
                    processed=int(_rr.processed),
                    proof_accepted=int(_rr.proof_accepted),
                    rejected=int(_rr.rejected),
                    retried=int(_rr.retried),
                    dead_lettered=int(_rr.dead_lettered),
                    rejection_reason_counts=_rejection_counts,
                    submit_disabled_effective_mode=submit_disabled_effective_mode,
                    live_submit_attempts=_live_submit_attempts,
                    live_venue_acks=_live_venue_acks,
                )
            )
        except Exception as exc:
            logger.error(
                "EDLI reactor: status pulse failed (non-fatal): %s",
                exc,
                exc_info=True,
            )
        logger.info(
            "EDLI reactor cycle result: processed=%d proof_accepted=%d rejected=%d retried=%d dead=%d "
            "claim_lock_bounces=%d reasons=%r",
            _rr.processed, _rr.proof_accepted, _rr.rejected, _rr.retried, _rr.dead_lettered,
            getattr(_rr, "claim_lock_bounces", 0), _rr.rejection_reasons[:8],
        )
        conn.commit()
    finally:
        try:
            trade_conn.close()
        except NameError:
            pass
        try:
            forecasts_conn.close()
        except NameError:
            pass
        conn.close()
        _edli_reactor_active_lock.release()
        _start_venue_background_maintenance_after_reactor_if_required()


@_scheduler_job("edli_bankroll_warm")
def _edli_bankroll_warm_cycle() -> None:
    """Dedicated frequent (~60s) bankroll-of-record cache warmer.

    STRUCTURAL FIX (2026-05-31, follow-up to #45): the per-event no-submit Kelly
    proof and the live-bridge allocator refresh both read
    ``bankroll_provider.cached()`` (300s fail-closed window) and MUST NOT live-fetch
    per decision. The reactor cycle previously warmed that cache ONCE at cycle
    start, but the canary cycle runs ~330s (heavy MC re-pricing + live /book fetches
    + submit path), so by the time those consumers ran near cycle END the cache age
    had exceeded 300s → ``cached()`` returned None → allocator fail-closed
    (bankroll_unavailable) AND every candidate rejected with
    ``KELLY_PROOF_MISSING:bankroll_provider_unavailable``. Bankroll freshness was
    structurally COUPLED to the slow reactor cycle.

    This job DECOUPLES freshness from the reactor cycle without doing live venue
    I/O in the trading daemon. It consumes the durable CollateralLedger snapshot
    maintained by the post-trade-capital sidecar and advances the in-process
    cache from that local truth. It does NOT widen the ``cached()`` window or
    weaken fail-closed semantics — stale/degraded/missing collateral snapshots
    leave consumers fail-closed.
    """

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    from src.runtime import bankroll_provider as _bankroll_provider

    try:
        warm = _bankroll_provider.warm_from_collateral_snapshot()
    except Exception as exc:  # noqa: BLE001 — fail-soft; consumers fail-closed on None
        logger.error(
            "EDLI bankroll warm: collateral snapshot warm raised (non-fatal, freshness "
            "did not advance this tick): %r",
            exc,
        )
        return
    if warm is None:
        logger.error(
            "EDLI bankroll warm: collateral snapshot warm returned None — cached() will "
            "fail closed (KELLY_PROOF_MISSING) until post-trade-capital publishes a fresh "
            "non-degraded collateral snapshot."
        )


def _command_recovery_summary_mutated_allocator_inputs(summary: object) -> bool:
    """Return True when command recovery changed facts used by submit gating."""

    if not isinstance(summary, dict):
        return False
    mutation_keys = {"advanced", "corrected", "projected", "exit_projected"}
    for key, value in summary.items():
        if isinstance(value, dict):
            if _command_recovery_summary_mutated_allocator_inputs(value):
                return True
            continue
        if key in mutation_keys:
            try:
                if int(value or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


@_scheduler_job("edli_command_recovery")
def _edli_command_recovery_cycle() -> None:
    """Unresolved venue-command reconcile sweep for the EDLI lane (#28c).

    INCIDENT (2026-06-10 22:54Z): command 84fb2c4c lost its submit ack and sat
    SUBMITTING for 8+ minutes while the order had FILLED on-chain at 22:55:13 —
    invisible exposure. reconcile_unresolved_commands (INV-31) previously ran
    ONLY inside the legacy cycle_runner loop; the EDLI event-driven lane had NO
    scheduled owner for unresolved side-effect states. This job gives the sweep
    a 3-minute cadence independent of which lane is live. The sweep itself is
    unchanged (venue lookup per in-flight command; REVIEW_REQUIRED handoff for
    ack-lost rows without an order id).
    """
    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    if get_mode() != "live":
        return
    if _defer_for_held_position_monitor("edli_command_recovery"):
        return
    from src.execution.command_recovery import reconcile_unresolved_commands
    from src.state.db import get_trade_connection_with_world_required

    summary = reconcile_unresolved_commands(scope="live_tick")
    if summary.get("scanned"):
        logger.info("edli_command_recovery: %s", summary)
    if _command_recovery_summary_mutated_allocator_inputs(summary):
        trade_conn = get_trade_connection_with_world_required(write_class=None)
        try:
            allocator_refresh = _edli_refresh_global_allocator_for_live_bridge(trade_conn)
        finally:
            trade_conn.close()
        logger.info(
            "edli_command_recovery: refreshed allocator after recovery mutation: %s",
            allocator_refresh,
        )
    _emit_command_recovery_redecision_continuations(summary, log_context="edli_command_recovery")


def _edli_boot_command_recovery_once() -> None:
    """Run one bounded EDLI recovery pass before the first live reactor tick.

    The periodic ``edli_command_recovery`` job starts about a minute after boot.
    That is too late for restart-relevant live-order projections that can keep
    family locks active or leave old pre-submit payloads in the restart gate.
    This boot pass uses a narrower boot_fast recovery contract before any new
    entry order can be produced. It clears submit/cap/family locks that can
    block entry, while leaving heavier maker-fill and partial-remainder
    maintenance for the scheduled live_tick job after the scheduler starts.
    """

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    if get_mode() != "live":
        return
    from src.execution.command_recovery import reconcile_unresolved_commands
    from src.state.db import get_trade_connection_with_world_required

    summary = reconcile_unresolved_commands(scope="boot_fast")
    try:
        from src.execution.edli_absence_resolver import take_boot_auto_resolution_continuations

        boot_auto_continuations = take_boot_auto_resolution_continuations()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "edli_boot_command_recovery: boot auto-resolution continuation read failed: %r",
            exc,
        )
        boot_auto_continuations = []
    if boot_auto_continuations:
        existing = list(summary.get("terminal_no_fill_continuations") or [])
        summary["terminal_no_fill_continuations"] = existing + boot_auto_continuations
    logger.warning("edli_boot_command_recovery: %s", summary)
    if _command_recovery_summary_mutated_allocator_inputs(summary):
        trade_conn = get_trade_connection_with_world_required(write_class=None)
        try:
            allocator_refresh = _edli_refresh_global_allocator_for_live_bridge(trade_conn)
        finally:
            trade_conn.close()
        logger.info(
            "edli_boot_command_recovery: refreshed allocator after recovery mutation: %s",
            allocator_refresh,
        )
    _emit_command_recovery_redecision_continuations(summary, log_context="edli_boot_command_recovery")


def _edli_boot_invalid_pending_entry_authority_cancel_once() -> None:
    """Cancel invalid zero-fill pending ENTRY rests before the first reactor tick."""

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    if get_mode() != "live":
        return
    from src.data.polymarket_client import PolymarketClient
    from src.execution.command_recovery import find_invalid_pending_entry_authority_cancels
    from src.execution.maker_rest_escalation import run_persisted_cancels_for_expired_rests
    from src.state.db import (
        get_forecasts_connection_read_only,
        get_trade_connection,
        get_trade_connection_read_only,
    )

    trade_ro = get_trade_connection_read_only()
    try:
        entries = find_invalid_pending_entry_authority_cancels(trade_ro)
    finally:
        trade_ro.close()
    if not entries:
        return

    cancelled_entries: list[dict] = []
    stats = run_persisted_cancels_for_expired_rests(
        entries,
        PolymarketClient(),
        conn_factory=lambda: get_trade_connection(write_class="live"),
        collect_cancelled=cancelled_entries,
    )
    logger.warning(
        "edli_boot_invalid_pending_entry_authority_cancel: entries=%d stats=%s",
        len(entries),
        stats,
    )
    if cancelled_entries and edli_cfg.get("event_writer_enabled"):
        trade_post = get_trade_connection_read_only()
        forecasts_ro = get_forecasts_connection_read_only()
        try:
            families = _escalation_families_from_cancelled(
                cancelled_entries,
                trade_post,
                forecasts_ro,
            )
        finally:
            try:
                trade_post.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                forecasts_ro.close()
            except Exception:  # noqa: BLE001
                pass
        if families:
            cleared = _clear_redecision_acted_state_for_families(families)
            now = datetime.now(timezone.utc)
            emitted = _emit_live_redecision_events_for_families(
                families,
                decision_time=now,
                received_at=now.isoformat(),
                origin="invalid_pending_entry_authority_cancel",
            )
            logger.warning(
                "edli_boot_invalid_pending_entry_authority_cancel: "
                "families=%d acted_state_cleared=%d events_emitted=%d",
                len(families),
                cleared,
                emitted,
            )
    if int(stats.get("cancelled", 0) or 0) != len(entries):
        raise RuntimeError(
            "EDLI_INVALID_PENDING_ENTRY_AUTHORITY_CANCEL_INCOMPLETE:"
            f"entries={len(entries)} stats={stats}"
        )


def _escalation_families_from_cancelled(
    cancelled: list[dict],
    trade_conn,
    forecasts_conn,
) -> set[tuple[str, str, str]]:
    """Recover the ``(city, target_date, metric)`` family key for each just-cancelled
    escalation rest, from VENUE TRUTH (no cached-belief dependency).

    Path (both joins are canonical and already proven):
      1. ``venue_commands.token_id`` -> ``condition_id`` via the freshest
         ``executable_market_snapshots.selected_outcome_token_id`` row (the SAME
         token->condition resolution the continuous-redecision rest screen uses,
         ``_edli_open_maker_rests_for_screen``).
      2. ``condition_id`` -> ``(city, target_date, temperature_metric)`` via
         ``market_events`` (forecasts DB) — the canonical condition->family map the
         FSR re-emit machinery already trusts (its ``market_filter`` joins the same
         table on city/target_date/metric).

    Pure reads on read-only connections. Best-effort per entry: a row that cannot be
    resolved (no snapshot, no market_events) is SKIPPED (the standard round-robin
    still reaches it eventually) rather than crashing the cancel job.
    """
    direct_families: set[tuple[str, str, str]] = set()
    for entry in cancelled:
        metric = _substrate_refresh_canonical_metric(
            entry.get("metric") or entry.get("temperature_metric") or ""
        )
        key = (
            str(entry.get("city") or "").strip(),
            str(entry.get("target_date") or "").strip(),
            metric,
        )
        if all(key) and key[2] in {"high", "low"}:
            direct_families.add(key)
    direct_condition_ids = {
        str(e.get("condition_id") or "").strip()
        for e in cancelled
        if str(e.get("condition_id") or "").strip()
    }
    token_ids = {str(e.get("token_id") or "") for e in cancelled if e.get("token_id")}
    cond_by_token: dict[str, str] = {}
    if token_ids:
        try:
            tph = ",".join("?" for _ in token_ids)
            for cr in trade_conn.execute(
                f"""
                SELECT selected_outcome_token_id, condition_id
                FROM executable_market_snapshot_latest
                WHERE selected_outcome_token_id IN ({tph})
                ORDER BY captured_at DESC
                """,
                tuple(token_ids),
            ).fetchall():
                if cr[0] and cr[1] and str(cr[0]) not in cond_by_token:
                    cond_by_token[str(cr[0])] = str(cr[1])
        except Exception:  # noqa: BLE001 — token->condition resolution is best-effort
            cond_by_token = {}
        if not cond_by_token:
            try:
                tph = ",".join("?" for _ in token_ids)
                for cr in trade_conn.execute(
                    f"""
                    SELECT selected_outcome_token_id, condition_id,
                           ROW_NUMBER() OVER (PARTITION BY selected_outcome_token_id
                                              ORDER BY captured_at DESC) AS rn
                    FROM executable_market_snapshots
                    WHERE selected_outcome_token_id IN ({tph})
                    """,
                    tuple(token_ids),
                ).fetchall():
                    if cr[2] == 1 and cr[0] and cr[1]:
                        cond_by_token[str(cr[0])] = str(cr[1])
            except Exception:  # noqa: BLE001 — token->condition resolution is best-effort
                cond_by_token = {}
    cond_ids = {c for c in cond_by_token.values() if c} | direct_condition_ids
    if not cond_ids:
        return direct_families
    families: set[tuple[str, str, str]] = set(direct_families)
    try:
        cph = ",".join("?" for _ in cond_ids)
        for fr in forecasts_conn.execute(
            f"""
            SELECT DISTINCT city, target_date, temperature_metric
            FROM market_events
            WHERE condition_id IN ({cph})
            """,
            tuple(cond_ids),
        ).fetchall():
            city, target_date, metric = (
                str(fr[0] or ""), str(fr[1] or ""), str(fr[2] or "")
            )
            if city and target_date and metric:
                families.add((city, target_date, metric))
    except Exception:  # noqa: BLE001 — condition->family map is best-effort
        return families
    return families


def _clear_redecision_acted_state_for_families(
    families: set[tuple[str, str, str]],
) -> int:
    """Release anti-noise latches after terminal no-fill proves the prior rest ended."""

    if not families:
        return 0
    removed = 0
    for key in list(_edli_redecision_acted_state.keys()):
        if not isinstance(key, tuple):
            continue
        family: tuple[str, str, str] | None = None
        if len(key) == 4 and key[0] == "family":
            family = (str(key[1]), str(key[2]), str(key[3]))
        elif len(key) == 5:
            family = (str(key[0]), str(key[1]), str(key[2]))
        if family in families:
            _edli_redecision_acted_state.pop(key, None)
            removed += 1
    return removed


def _emit_live_redecision_events_for_families(
    families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
    received_at: str,
    origin: str,
) -> int:
    """Emit standard live redecision rows for already-live order management work."""

    if not families:
        return 0
    from src.events.continuous_redecision import REDECISION_EVENT_TYPE
    from src.events.event_writer import EventWriter
    from src.events.triggers.forecast_snapshot_ready import (
        ForecastSnapshotReadyTrigger,
        executable_forecast_live_eligible_reader,
    )
    from src.state.db import (
        get_forecasts_connection_read_only,
        get_world_connection,
        world_write_mutex as _world_write_mutex,
    )

    world = get_world_connection()
    forecasts_ro = get_forecasts_connection_read_only()
    emit_mutex = _world_write_mutex()
    emit_mutex.acquire()
    try:
        trig = ForecastSnapshotReadyTrigger(
            EventWriter(world),
            live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_ro),
        )
        events = trig.build_committed_snapshot_events(
            forecasts_conn=forecasts_ro,
            decision_time=decision_time,
            received_at=received_at,
            limit=None,
            source=_edli_next_redecision_source(),
            event_type=REDECISION_EVENT_TYPE,
            restrict_to_families=families,
        )
        write_results = EventWriter(world).write_many(
            [_redecision_event_with_origin(event, origin) for event in events]
        )
        world.commit()
        return sum(1 for result in write_results if result.inserted)
    finally:
        emit_mutex.release()
        try:
            forecasts_ro.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            world.close()
        except Exception:  # noqa: BLE001
            pass


def _emit_terminal_no_fill_redecision_continuations(
    families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
    received_at: str,
) -> int:
    """Emit standard continuous redecision rows after no-fill terminal recovery."""

    return _emit_live_redecision_events_for_families(
        families,
        decision_time=decision_time,
        received_at=received_at,
        origin="terminal_no_fill",
    )


def _terminal_no_fill_continuation_families(
    summary: object,
    trade_conn,
    forecasts_conn,
) -> set[tuple[str, str, str]]:
    if not isinstance(summary, dict):
        return set()
    continuations = summary.get("terminal_no_fill_continuations")
    if not isinstance(continuations, list):
        return set()
    entries = [entry for entry in continuations if isinstance(entry, dict)]
    if not entries:
        return set()
    direct: set[tuple[str, str, str]] = set()
    unresolved: list[dict] = []
    for entry in entries:
        metric = _substrate_refresh_canonical_metric(
            entry.get("metric") or entry.get("temperature_metric") or ""
        )
        key = (
            str(entry.get("city") or "").strip(),
            str(entry.get("target_date") or "").strip(),
            metric,
        )
        if all(key) and key[2] in {"high", "low"}:
            direct.add(key)
        else:
            unresolved.append(entry)
    if not unresolved:
        return direct
    return direct | _escalation_families_from_cancelled(unresolved, trade_conn, forecasts_conn)


def _emit_command_recovery_redecision_continuations(
    summary: object,
    *,
    log_context: str,
) -> None:
    edli_cfg = _settings_section("edli", {})
    if not (edli_cfg.get("event_writer_enabled") and isinstance(summary, dict)):
        return
    try:
        from datetime import datetime, timezone
        from src.state.db import get_forecasts_connection_read_only, get_trade_connection_read_only

        trade_ro = get_trade_connection_read_only()
        forecasts_ro = get_forecasts_connection_read_only()
        try:
            families = _terminal_no_fill_continuation_families(
                summary,
                trade_ro,
                forecasts_ro,
            )
        finally:
            try:
                trade_ro.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                forecasts_ro.close()
            except Exception:  # noqa: BLE001
                pass
        if families:
            cleared = _clear_redecision_acted_state_for_families(families)
            now = datetime.now(timezone.utc)
            emitted = _emit_terminal_no_fill_redecision_continuations(
                families,
                decision_time=now,
                received_at=now.isoformat(),
            )
            logger.info(
                "%s: terminal no-fill/pre-submit continuation "
                "families=%d acted_state_cleared=%d events_emitted=%d",
                log_context,
                len(families),
                cleared,
                emitted,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s: terminal no-fill/pre-submit continuation emit failed "
            "(non-fatal; family remains eligible for normal redecision): %r",
            log_context,
            exc,
        )


def _emit_rest_pull_redecisions(
    families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
    received_at: str,
) -> int:
    """Emit one standard redecision per pulled maker rest family.

    This is live order management, not a second forecast lane. A pulled rest has
    either finished as terminal no-fill or is about to leave the open-rest screen,
    so continuity must be a durable ``EDLI_REDECISION_PENDING`` row that the normal
    reactor path consumes on the next cycle.
    """

    return _emit_live_redecision_events_for_families(
        families,
        decision_time=decision_time,
        received_at=received_at,
        origin="rest_pull",
    )


@_scheduler_job("maker_rest_escalation")
def _maker_rest_escalation_cycle() -> None:
    """K4.0 REST-THEN-CROSS deadline owner (consolidated overhaul 2026-06-11).

    Cancels post_only GTC ENTRY rests older than the measured escalation
    deadline (maker_rest_escalation_deadline, 20min derived from the measured
    KM hazard curve on n=108 resting facts). GTC rests have NO other TTL owner. The job is
    deliberately dumb: cancel only. The next reactor cycle re-certifies the
    family through the FULL standard pipeline; _family_rest_state then sees the
    cancelled-unfilled >= deadline rest in venue truth and licenses the
    policy's TAKER_ESCALATED_AFTER_REST cross. Edge decayed -> no candidate ->
    the standard regret receipt records the decay (free rest-cost measurement).

    Read-only on the DB; venue cancels only; fail-soft per order.
    """
    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    if get_mode() != "live":
        return
    if _defer_for_held_position_monitor("maker_rest_escalation"):
        return
    from src.data.polymarket_client import PolymarketClient
    from src.execution.command_recovery import find_invalid_pending_entry_authority_cancels
    from src.execution.maker_rest_escalation import (
        find_expired_resting_entries,
        run_persisted_cancels_for_expired_rests,
    )
    from src.state.db import get_trade_connection, get_trade_connection_read_only

    # Clean shape (dependency_db_locked antibody, 2026-06-11): SNAPSHOT the
    # expired-rest candidates on a short read-only connection and CLOSE it before
    # any venue cancel. The read-only connection never takes a WAL write lock, so
    # holding it across cancels could not have caused the incident — but
    # close-before-network is the structural shape this lane should still follow,
    # so a future edit cannot silently turn this into a write-conn-across-network
    # regression. The cancel loop holds no connection.
    conn = get_trade_connection_read_only()
    try:
        expired = find_expired_resting_entries(conn, now=datetime.now(timezone.utc))
        invalid_authority_pending = find_invalid_pending_entry_authority_cancels(conn)
    finally:
        conn.close()

    clob = PolymarketClient()
    # ESCALATION RE-DECISION (redecide-block fix 2026-06-16): harvest the families
    # whose rest was CONFIRMED-cancelled so we can emit a Tier-0 re-decision for
    # each — the just-cancelled, ARMED family crosses as TAKER_ESCALATED_AFTER_REST
    # on the NEXT cycle instead of waiting ~2-3h for the 49-deep per-city
    # round-robin. The cancel path now journals CANCEL_REQUESTED/CANCEL_ACKED around
    # the venue side effect so a successfully pulled rest cannot remain a local ACK ghost.
    cancelled_entries: list[dict] = []
    stats = run_persisted_cancels_for_expired_rests(
        [*expired, *invalid_authority_pending],
        clob,
        conn_factory=lambda: get_trade_connection(write_class="live"),
        collect_cancelled=cancelled_entries,
    )
    if stats["scanned"]:
        logger.info(
            "maker_rest_escalation: %s expired_rests=%d invalid_authority_pending=%d",
            stats,
            len(expired),
            len(invalid_authority_pending),
        )

    # FAIL-CLOSED on the re-decision emit: any error here must NOT crash the cancel
    # job (the cancels already succeeded; the worst case without the re-decision is
    # the pre-fix behavior — the family waits for the round-robin). Connection-free
    # in the cancel phase is preserved: the family-recovery reads and the world-DB
    # event-write both run HERE in the caller (which owns DB access), AFTER the
    # venue cancels, never during them.
    if cancelled_entries and edli_cfg.get("event_writer_enabled"):
        try:
            from src.state.db import (
                get_forecasts_connection_read_only,
                get_trade_connection_read_only as _get_trade_ro,
            )

            now = datetime.now(timezone.utc)
            trade_ro = _get_trade_ro()
            forecasts_ro = get_forecasts_connection_read_only()
            try:
                families = _escalation_families_from_cancelled(
                    cancelled_entries, trade_ro, forecasts_ro
                )
            finally:
                try:
                    trade_ro.close()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    forecasts_ro.close()
                except Exception:  # noqa: BLE001
                    pass
            emitted = _emit_rest_pull_redecisions(
                families, decision_time=now, received_at=now.isoformat()
            )
            logger.info(
                "maker_rest_escalation: rest-pull re-decision emit "
                "cancelled=%d families_resolved=%d events_emitted=%d",
                len(cancelled_entries), len(families), emitted,
            )
        except Exception as _redecide_exc:  # noqa: BLE001 — fail-closed: never crash the cancel job
            logger.warning(
                "maker_rest_escalation: rest-pull re-decision emit failed "
                "(non-fatal; family will wait for the round-robin): %r",
                _redecide_exc,
            )


def _edli_open_maker_rests_for_screen(trade_conn, world_conn, *, beliefs=None) -> "list":
    """Build OpenRest entries for §4.5 rest management: every OPEN maker ENTRY rest joined to its
    decision belief via condition_id. Pure read on both DBs.

    The rest's condition_id (token_id → executable_market_snapshots) joins to the belief's
    per-bin condition_ids → (family_id, bin_label, resting_posterior, resting_snapshot_id). The
    resting_posterior is the belief's posterior at that bin from the LATEST cached belief whose
    snapshot matches the rest's pricing snapshot (anti-twitch: screen_reprice fires only when the
    LATEST belief is from a NEWER snapshot than the rest's). When the bin/belief cannot be resolved
    the rest still gets the book/stale checks (which need no posterior)."""
    from datetime import datetime, timezone
    from src.events.continuous_redecision import OpenRest, _all_latest_beliefs
    from src.execution.maker_rest_escalation import OPEN_REST_FACT_STATES

    now = datetime.now(timezone.utc)
    try:
        fact_cols = {str(row[1]) for row in trade_conn.execute("PRAGMA table_info(venue_order_facts)").fetchall()}
    except Exception:  # noqa: BLE001
        fact_cols = set()
    try:
        command_cols = {str(row[1]) for row in trade_conn.execute("PRAGMA table_info(venue_commands)").fetchall()}
    except Exception:  # noqa: BLE001
        command_cols = set()
    matched_select = "matched_size" if "matched_size" in fact_cols else "NULL AS matched_size"
    command_state_filter = (
        "AND state IN ('ACKED', 'POST_ACKED', 'PARTIAL')" if "state" in command_cols else ""
    )
    command_rows = trade_conn.execute(
        f"""
        SELECT command_id, venue_order_id, token_id, market_id,
               side, price, snapshot_id, created_at
          FROM venue_commands
         WHERE intent_kind = 'ENTRY'
           {command_state_filter}
           AND venue_order_id IS NOT NULL AND venue_order_id != ''
        """
    ).fetchall()
    rows = []
    if command_rows:
        fact_sql = f"""
            SELECT state, {matched_select}
              FROM venue_order_facts
             WHERE venue_order_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
        """
        open_states = set(OPEN_REST_FACT_STATES)
        for vc in command_rows:
            latest_fact = trade_conn.execute(fact_sql, (vc[1],)).fetchone()
            if latest_fact is None:
                continue
            fact_state = str(latest_fact[0] or "")
            if fact_state not in open_states:
                continue
            rows.append(tuple(vc) + (fact_state, latest_fact[1]))
    if not rows:
        return []
    # Resolve token_id -> condition_id and held-side direction from the freshest
    # executable_market_snapshots row. BOOK_MOVED checks are direction-specific:
    # buy_yes rests compare against YES best bid; buy_no rests compare against
    # native NO best bid.
    token_ids = {str(r[2] or "") for r in rows if r[2]}
    cond_by_token: dict[str, str] = {}
    side_by_token: dict[str, str] = {}
    if token_ids:
        try:
            tph = ",".join("?" for _ in token_ids)
            for cr in trade_conn.execute(
                f"""
                SELECT selected_outcome_token_id, condition_id, yes_token_id, no_token_id,
                       captured_at
                FROM executable_market_snapshot_latest
                WHERE selected_outcome_token_id IN ({tph})
                   OR yes_token_id IN ({tph})
                   OR no_token_id IN ({tph})
                ORDER BY captured_at DESC
                """,
                (*tuple(token_ids), *tuple(token_ids), *tuple(token_ids)),
            ).fetchall():
                selected = str(cr[0] or "")
                cond = str(cr[1] or "")
                yes_token = str(cr[2] or "")
                no_token = str(cr[3] or "")
                for token, side in (
                    (selected, "buy_no" if selected and selected == no_token else "buy_yes"),
                    (yes_token, "buy_yes"),
                    (no_token, "buy_no"),
                ):
                    if token and token in token_ids and token not in cond_by_token:
                        cond_by_token[token] = cond
                        side_by_token[token] = side
        except Exception:  # noqa: BLE001 — token→condition resolution is best-effort
            cond_by_token = {}
            side_by_token = {}
        if not cond_by_token:
            try:
                tph = ",".join("?" for _ in token_ids)
                for cr in trade_conn.execute(
                    f"""
                    SELECT selected_outcome_token_id, condition_id, yes_token_id, no_token_id,
                           captured_at
                    FROM executable_market_snapshots
                    WHERE selected_outcome_token_id IN ({tph})
                       OR yes_token_id IN ({tph})
                       OR no_token_id IN ({tph})
                    ORDER BY captured_at DESC
                    """,
                    (*tuple(token_ids), *tuple(token_ids), *tuple(token_ids)),
                ).fetchall():
                    selected = str(cr[0] or "")
                    cond = str(cr[1] or "")
                    yes_token = str(cr[2] or "")
                    no_token = str(cr[3] or "")
                    for token, side in (
                        (selected, "buy_no" if selected and selected == no_token else "buy_yes"),
                        (yes_token, "buy_yes"),
                        (no_token, "buy_no"),
                    ):
                        if token and token in token_ids and token not in cond_by_token:
                            cond_by_token[token] = cond
                            side_by_token[token] = side
            except Exception:  # noqa: BLE001 — token→condition resolution is best-effort
                cond_by_token = {}
                side_by_token = {}
    if beliefs is None:
        beliefs = _all_latest_beliefs(world_conn)
    # Index belief bins by condition_id → (belief, bin_label, posterior).
    bin_by_cond: dict[str, tuple] = {}
    for belief in beliefs:
        conds = belief.condition_ids or []
        for idx, label in enumerate(belief.bin_labels):
            if idx < len(conds) and conds[idx]:
                if idx < len(belief.p_posterior_vec):
                    bin_by_cond[str(conds[idx])] = (belief, label, float(belief.p_posterior_vec[idx]))
    out = []
    for r in rows:
        command_id, venue_order_id, token_id, market_id, side, price, snap_id, created_at, fact_state, matched_size = (
            str(r[0] or ""), str(r[1] or ""), str(r[2] or ""), str(r[3] or ""),
            str(r[4] or ""), r[5], str(r[6] or ""), str(r[7] or ""), str(r[8] or ""), r[9],
        )
        cond = cond_by_token.get(token_id, "")
        belief_hit = bin_by_cond.get(cond)
        family_id = belief_hit[0].family_id if belief_hit else ""
        city = str(getattr(belief_hit[0], "city", "") or "") if belief_hit else ""
        target_date = str(getattr(belief_hit[0], "target_date", "") or "") if belief_hit else ""
        metric = str(getattr(belief_hit[0], "metric", "") or "") if belief_hit else ""
        bin_label = belief_hit[1] if belief_hit else ""
        resting_posterior = belief_hit[2] if belief_hit else 0.0
        # quote_age_ms from the command's creation (the order has rested since created_at).
        try:
            from datetime import datetime as _dt
            age_ms = max(0.0, (now - _dt.fromisoformat(created_at)).total_seconds() * 1000.0) if created_at else 0.0
        except Exception:  # noqa: BLE001
            age_ms = 0.0
        screen_side = side_by_token.get(token_id, "buy_yes")
        if not (cond and family_id and bin_label and snap_id):
            continue
        resting_held_side_posterior = (
            1.0 - resting_posterior if screen_side == "buy_no" else resting_posterior
        )
        out.append(
            OpenRest(
                command_id=command_id,
                venue_order_id=venue_order_id,
                family_id=family_id,
                bin_label=bin_label,
                side=screen_side,
                condition_id=cond,
                resting_posterior=resting_held_side_posterior,
                resting_snapshot_id=snap_id,
                limit_price=float(price) if price is not None else 0.0,
                quote_age_ms=age_ms,
                created_at=created_at,
                fact_state=fact_state,
                matched_size=None if matched_size is None else float(matched_size),
                city=city,
                target_date=target_date,
                metric=metric,
            )
        )
    return out


@_scheduler_job("edli_continuous_redecision_screen")
def _edli_continuous_redecision_screen_cycle() -> None:
    """P2 cheap-screen job (continuous re-decision resurrection 2026-06-12).

    Reads cached beliefs (world, RO) × freshest executable prices (trade, RO), runs the cheap edge
    screen, and ENQUEUES EDLI_REDECISION_PENDING events for families whose edge fired — so the
    reactor re-decides on PRICE movement between forecast cycles (the ~5-6h cadence gap the operator
    flagged). ALSO screens OPEN maker rests (§4.5): a rest whose belief decayed on new evidence, or
    whose book moved/went stale, is pulled (re-decide at fresh price) — the fix for "submitted then
    abandoned" (Busan/Beijing). NO new HTTP: reads only what the warm/fast lanes already persisted;
    the actual cancel reuses maker_rest_escalation's cancel path. Fail-soft: never crashes
    the scheduler.

    Wave-1 2026-06-12: the redecision_screen_enabled gate is DELETED. The screen is the
    fill-rate ORGAN, not an optional feature — it now runs whenever the reactor is LIVE and
    event writing is enabled (the same arm conditions that license the reactor itself). Data
    + cancel only; no new submit authority of its own."""
    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled") or not edli_cfg.get("event_writer_enabled"):
        return
    # Live-armed condition (replaces the deleted redecision_screen_enabled flag): the reactor
    # must be in live mode. When submit is disabled the screen organ stays dark.
    if str(edli_cfg.get("reactor_mode", "live")) != "live":
        return
    if _defer_for_held_position_monitor("edli_redecision_screen"):
        return
    if not _edli_redecision_screen_lock.acquire(blocking=False):
        logger.info("edli_redecision_screen skipped: previous screen still running")
        return
    try:
        from datetime import datetime, timezone
        from src.events.continuous_redecision import (
            _all_latest_beliefs,
            entry_substrate_refresh_scope,
            filter_redecisions_with_spine_members,
            screen_entry_redecisions,
            screened_family_keys,
            screen_resting_orders,
            REDECISION_EVENT_TYPE,
        )
        from src.state.db import (
            get_world_connection_read_only,
            get_trade_connection_read_only,
            get_world_connection,
            get_forecasts_connection_read_only,
        )

        now = datetime.now(timezone.utc)
        received_at = now.isoformat()
        min_edge = float(edli_cfg.get("redecision_screen_min_edge", 0.01))
        # Wave-1 2026-06-12: redecision_max_per_cycle cap DELETED. The screen re-emit uses the
        # fixed fair-cursor batch (wraps modulo family count → full coverage, no tail drop).
        rd_cap = _EDLI_REDECISION_FAIR_BATCH

        # 1) ENTRY screen + rest screen on RO connections (pure read, no HTTP).
        world_ro = get_world_connection_read_only()
        trade_ro = get_trade_connection_read_only()
        try:
            all_beliefs = _all_latest_beliefs(
                world_ro,
                decision_time=received_at,
                forecast_only_admissible=True,
            )
            beliefs, screened_belief_keys, total_beliefs = _edli_redecision_screen_belief_batch(
                all_beliefs,
                max_families=rd_cap,
            )
            if total_beliefs and len(beliefs) < total_beliefs:
                logger.info(
                    "edli_redecision_screen: entry belief fair batch size=%d total=%d cursor=%d",
                    len(beliefs),
                    total_beliefs,
                    _edli_redecision_screen_belief_cursor,
                )
            probe_acted_state = dict(_edli_redecision_acted_state)
            redecisions = screen_entry_redecisions(
                world_ro,
                trade_ro,
                decision_time=received_at,
                min_edge=min_edge,
                acted_state=probe_acted_state,
                beliefs=beliefs,
            )
            try:
                forecasts_filter_ro = get_forecasts_connection_read_only()
                try:
                    entry_redecisions = filter_redecisions_with_spine_members(
                        forecasts_filter_ro,
                        redecisions,
                        beliefs=beliefs,
                        decision_time=received_at,
                    )
                finally:
                    forecasts_filter_ro.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "edli_redecision_screen: spine availability read failed; "
                    "entry redecisions not admitted this tick: %r",
                    exc,
                )
                entry_redecisions = []
            raw_entry_family_keys = screened_family_keys(world_ro, entry_redecisions, beliefs=beliefs)
            # Open maker rests are already-live order-management obligations.
            # They must be screened every cycle even when their family is outside
            # the entry fair-batch cursor; the fair batch limits new entry scans,
            # not management of submitted GTC rests that hold the submit mutex.
            open_rests = _edli_open_maker_rests_for_screen(
                trade_ro,
                world_ro,
                beliefs=all_beliefs,
            )
            entry_refresh_condition_scope = entry_substrate_refresh_scope(
                trade_ro,
                beliefs=beliefs,
                decision_time=received_at,
                max_families=rd_cap,
                min_edge=min_edge,
            )
            rest_pulls = screen_resting_orders(
                world_ro,
                trade_ro,
                open_rests=open_rests,
                decision_time=received_at,
            )
            entry_condition_scope = _edli_redecision_condition_scope(entry_redecisions, beliefs)
            open_rest_condition_scope = _edli_open_rest_condition_scope(open_rests, all_beliefs)
            rest_condition_scope = _edli_rest_pull_condition_scope(rest_pulls, beliefs)
        finally:
            try:
                world_ro.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                trade_ro.close()
            except Exception:  # noqa: BLE001
                pass

        # A rest-pull family must also re-decide (cancel + re-decide at fresh price). Add its
        # family key to the re-emit restriction so the reactor re-certifies it; the cancel itself
        # runs through the maker_rest_escalation cancel path below.
        rest_pull_families: set = set()
        if rest_pulls:
            by_family = {
                b.family_id: (b.city, b.target_date, b.metric) for b in all_beliefs
            }
            for rest, _decision in rest_pulls:
                key = _edli_family_key_from_rest(rest) or by_family.get(rest.family_id)
                if key is not None and all(key):
                    rest_pull_families.add(key)
        held_families = _edli_current_held_position_family_keys()
        held_condition_scope = _edli_current_held_position_family_condition_scope(held_families)
        family_keys = _edli_entry_redecision_family_keys(
            raw_entry_family_keys,
            held_families,
            decision_time=now,
        )
        entry_refresh_families = set(entry_refresh_condition_scope)
        held_reemit_families = _edli_reemittable_held_position_family_keys(
            held_families,
            decision_time=now,
        )
        all_families = set(family_keys) | rest_pull_families | held_reemit_families
        confirmed_entry_scope = set(family_keys) | entry_refresh_families
        confirmed_rest_scope = set(rest_pull_families)
        confirmed_held_scope = set(held_reemit_families)
        held_refresh_families = set(held_condition_scope)
        confirm_families = set(all_families) | held_refresh_families | entry_refresh_families
        priority_condition_ids = _edli_confirm_priority_condition_ids(
            rest_condition_scope=rest_condition_scope,
            held_condition_scope=held_condition_scope,
            entry_condition_scope=entry_condition_scope,
            entry_refresh_condition_scope=entry_refresh_condition_scope,
            open_rest_condition_scope=open_rest_condition_scope,
            full_family_refresh_families=held_reemit_families,
        )
        confirm_refresh_summary: dict = {}
        if confirm_families:
            confirm_refresh_summary = _edli_refresh_continuous_money_path_families(
                confirm_families,
                now_utc=now,
                priority_condition_ids=priority_condition_ids,
            )
            confirm_status = str(confirm_refresh_summary.get("status") or "")
            if _edli_confirmation_refresh_unavailable(confirm_refresh_summary):
                logger.info(
                    "edli_redecision_screen: confirmation refresh not available; "
                    "skipping emit this tick rather than queueing stale redecision "
                    "families=%d status=%s coverage=%s summary=%r",
                    len(confirm_families),
                    confirm_status,
                    confirm_refresh_summary.get("executable_substrate_coverage_status"),
                    confirm_refresh_summary,
                )
                return
            fresh_entry_scope = _edli_families_with_fresh_scoped_executable_substrate(
                _edli_merge_condition_scopes(
                    entry_condition_scope,
                    entry_refresh_condition_scope,
                ),
                now_utc=now,
            )
            fresh_rest_scope = _edli_families_with_fresh_scoped_executable_substrate(
                rest_condition_scope,
                now_utc=now,
            )
            fresh_held_scope = _edli_families_with_fresh_scoped_executable_substrate(
                held_condition_scope,
                now_utc=now,
            )
            fresh_confirmed_families = fresh_entry_scope | fresh_rest_scope | fresh_held_scope
            confirmed_entry_scope &= fresh_entry_scope
            confirmed_rest_scope &= fresh_rest_scope
            confirmed_held_scope &= fresh_held_scope
            confirm_families &= fresh_confirmed_families
            scoped_filter_reason = (
                "incomplete_confirmation_refresh"
                if _edli_confirmation_refresh_needs_scoped_freshness_filter(confirm_refresh_summary)
                else "confirmation_refresh_verified"
            )
            logger.info(
                "edli_redecision_screen: %s admitted fresh scoped families=%d/%d "
                "entry_scope=%d rest_scope=%d held_scope=%d entry_conditions=%d "
                "rest_conditions=%d held_conditions=%d summary=%r",
                scoped_filter_reason,
                len(fresh_confirmed_families),
                len(set(all_families) | held_refresh_families | entry_refresh_families),
                len(confirmed_entry_scope),
                len(confirmed_rest_scope),
                len(confirmed_held_scope),
                sum(len(v) for v in entry_condition_scope.values())
                + sum(len(v) for v in entry_refresh_condition_scope.values()),
                sum(len(v) for v in rest_condition_scope.values()),
                sum(len(v) for v in held_condition_scope.values()),
                confirm_refresh_summary,
            )
            if not confirmed_entry_scope and not confirmed_rest_scope and not confirmed_held_scope:
                from src.state.db import world_write_mutex as _world_write_mutex

                world = get_world_connection()
                emit_mutex = _world_write_mutex()
                emit_mutex.acquire()
                try:
                    expired_unadmitted = _edli_expire_unadmitted_redecision_pending(
                        world,
                        set(),
                        decision_time=received_at,
                    )
                    world.commit()
                finally:
                    emit_mutex.release()
                    try:
                        world.close()
                    except Exception:  # noqa: BLE001
                        pass
                logger.info(
                    "edli_redecision_screen: confirmation refresh produced no fresh "
                    "screened money-path substrate; skipping emit this tick rather "
                    "than queueing stale redecision families=%d expired_unadmitted=%d",
                    len(set(all_families) | held_refresh_families),
                    expired_unadmitted,
                )
                return

            # Re-run the screen against the freshly refreshed money-path
            # substrate. The initial pass only chooses the confirmation scope;
            # this second pass is the value authority for emitted redecision rows.
            world_ro = get_world_connection_read_only()
            trade_ro = get_trade_connection_read_only()
            try:
                all_beliefs = _all_latest_beliefs(
                    world_ro,
                    decision_time=received_at,
                    forecast_only_admissible=True,
                )
                beliefs = _edli_filter_beliefs_to_family_keys(
                    all_beliefs,
                    screened_belief_keys,
                )
                redecisions = screen_entry_redecisions(
                    world_ro,
                    trade_ro,
                    decision_time=received_at,
                    min_edge=min_edge,
                    acted_state=_edli_redecision_acted_state,
                    beliefs=beliefs,
                )
                try:
                    forecasts_filter_ro = get_forecasts_connection_read_only()
                    try:
                        entry_redecisions = filter_redecisions_with_spine_members(
                            forecasts_filter_ro,
                            redecisions,
                            beliefs=beliefs,
                            decision_time=received_at,
                        )
                    finally:
                        forecasts_filter_ro.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "edli_redecision_screen: post-confirm spine availability read failed; "
                        "entry redecisions not admitted this tick: %r",
                        exc,
                    )
                    entry_redecisions = []
                raw_entry_family_keys = screened_family_keys(world_ro, entry_redecisions, beliefs=beliefs)
                open_rests = _edli_open_maker_rests_for_screen(
                    trade_ro,
                    world_ro,
                    beliefs=all_beliefs,
                )
                rest_pulls = screen_resting_orders(
                    world_ro,
                    trade_ro,
                    open_rests=open_rests,
                    decision_time=received_at,
                )
                entry_condition_scope = _edli_redecision_condition_scope(entry_redecisions, beliefs)
                rest_condition_scope = _edli_rest_pull_condition_scope(rest_pulls, beliefs)
            finally:
                try:
                    world_ro.close()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    trade_ro.close()
                except Exception:  # noqa: BLE001
                    pass

            rest_pull_families = set()
            if rest_pulls:
                by_family = {
                    b.family_id: (b.city, b.target_date, b.metric) for b in all_beliefs
                }
                for rest, _decision in rest_pulls:
                    key = _edli_family_key_from_rest(rest) or by_family.get(rest.family_id)
                    if key is not None and all(key):
                        rest_pull_families.add(key)
            rest_pull_families &= confirmed_rest_scope
            if rest_pull_families:
                rest_pull_families &= _edli_families_with_fresh_scoped_executable_substrate(
                    rest_condition_scope,
                    now_utc=now,
                )
            held_families = _edli_current_held_position_family_keys()
            family_keys = _edli_entry_redecision_family_keys(
                raw_entry_family_keys,
                held_families,
                decision_time=now,
            )
            family_keys &= confirmed_entry_scope
            if family_keys:
                family_keys &= _edli_families_with_fresh_scoped_executable_substrate(
                    entry_condition_scope,
                    now_utc=now,
                )
            held_reemit_families = _edli_reemittable_held_position_family_keys(
                held_families,
                decision_time=now,
            )
            held_reemit_families &= confirmed_held_scope
            if held_reemit_families:
                held_reemit_families &= _edli_families_with_fresh_scoped_executable_substrate(
                    _edli_current_held_position_family_condition_scope(held_reemit_families),
                    now_utc=now,
                )
            all_families = set(family_keys) | rest_pull_families | held_reemit_families
        expired_unadmitted = 0
        expired_stale_pending = 0
        expired_rest_pull_blockers = 0
        if not all_families:
            from src.state.db import world_write_mutex as _world_write_mutex

            world = get_world_connection()
            emit_mutex = _world_write_mutex()
            emit_mutex.acquire()
            try:
                expired_unadmitted = _edli_expire_unadmitted_redecision_pending(
                    world,
                    set(),
                    decision_time=received_at,
                )
                world.commit()
            finally:
                emit_mutex.release()
                try:
                    world.close()
                except Exception:  # noqa: BLE001
                    pass
            logger.info(
                "edli_redecision_screen: entry_candidates=%d entry_spine_confirmed=%d "
                "entry_families=0 rest_pulls=%d "
                "held_monitor_families=%d held_reemit_families=0 families_reemitted=0 "
                "events_emitted=0 rests_cancelled=0 expired_unadmitted=%d reason=no_screened_families",
                len(redecisions),
                len(entry_redecisions),
                len(rest_pulls),
                len(held_families),
                expired_unadmitted,
            )
            return

        # 2) EMIT EDLI_REDECISION_PENDING for the screened families (world write, under the mutex,
        #    no HTTP) — routed through the EXISTING FSR re-emit machinery (restrict_to_families).
        from src.events.event_writer import EventWriter
        from src.events.triggers.forecast_snapshot_ready import (
            ForecastSnapshotReadyTrigger,
            executable_forecast_live_eligible_reader,
        )
        from src.state.db import world_write_mutex as _world_write_mutex

        forecasts_ro = get_forecasts_connection_read_only()
        world_scan_ro = None
        try:
            world_prune = get_world_connection()
            prune_mutex = _world_write_mutex()
            prune_mutex.acquire()
            try:
                expired_stale_pending = _edli_expire_unadmitted_redecision_pending(
                    world_prune,
                    set(all_families),
                    decision_time=received_at,
                    supersede_stale_admitted=True,
                )
                expired_rest_pull_blockers = (
                    _edli_supersede_pending_redecisions_for_rest_pull_families(
                        world_prune,
                        rest_pull_families,
                        decision_time=received_at,
                    )
                )
                world_prune.commit()
            finally:
                prune_mutex.release()
                try:
                    world_prune.close()
                except Exception:  # noqa: BLE001
                    pass
            world_scan_ro = get_world_connection_read_only()
            pending = _edli_pending_entity_keys(world_scan_ro, event_types=(REDECISION_EVENT_TYPE,))
            pending_families = _edli_redecision_family_keys_from_entity_keys(pending)
            emit_families = set(all_families) - pending_families
            if emit_families:
                trig = ForecastSnapshotReadyTrigger(
                    EventWriter(world_scan_ro),
                    live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_ro),
                )
                events_to_emit = trig.build_committed_snapshot_events(
                    forecasts_conn=forecasts_ro,
                    decision_time=now,
                    received_at=received_at,
                    limit=rd_cap,
                    source=_edli_next_redecision_source(),
                    already_pending_keys=pending,
                    event_type=REDECISION_EVENT_TYPE,
                    restrict_to_families=emit_families,
                    phase_filter_exempt_families=set(),
                )
            else:
                events_to_emit = []
        finally:
            try:
                forecasts_ro.close()
            except Exception:  # noqa: BLE001
                pass
            if world_scan_ro is not None:
                try:
                    world_scan_ro.close()
                except Exception:  # noqa: BLE001
                    pass

        world = get_world_connection()
        emit_mutex = _world_write_mutex()
        emit_mutex.acquire()
        try:
            expired_unadmitted = _edli_expire_unadmitted_redecision_pending(
                world,
                set(all_families),
                decision_time=received_at,
            )
            expired_rest_pull_blockers += (
                _edli_supersede_pending_redecisions_for_rest_pull_families(
                    world,
                    rest_pull_families,
                    decision_time=received_at,
                )
            )
            current_pending = _edli_pending_entity_keys(world, event_types=(REDECISION_EVENT_TYPE,))
            fresh_events = []
            for event in events_to_emit:
                if event.entity_key in current_pending:
                    continue
                try:
                    payload = json.loads(str(event.payload_json or "{}"))
                    event_family = (
                        str(payload.get("city") or "").strip(),
                        str(payload.get("target_date") or "").strip(),
                        str(payload.get("metric") or "").strip(),
                    )
                except Exception:  # noqa: BLE001
                    event_family = ("", "", "")
                if event_family in rest_pull_families:
                    fresh_events.append(_redecision_event_with_origin(event, "rest_pull"))
                elif event_family in held_reemit_families:
                    fresh_events.append(_redecision_event_with_origin(event, "held_position"))
                elif event_family in family_keys:
                    fresh_events.append(_redecision_event_with_origin(event, "entry_screen"))
            emitted = EventWriter(world).write_many(fresh_events)
            world.commit()
        finally:
            emit_mutex.release()
            try:
                world.close()
            except Exception:  # noqa: BLE001
                pass

        # 3) CANCEL the pulled rests via the EXISTING maker_rest_escalation cancel path (no new
        #    venue call site). The next reactor cycle re-decides the re-emitted family at fresh price.
        cancelled = 0
        if rest_pulls and get_mode() == "live":
            from src.data.polymarket_client import PolymarketClient
            from src.execution.maker_rest_escalation import run_persisted_cancels_for_expired_rests
            from src.state.db import get_trade_connection

            to_cancel = [
                {"command_id": rest.command_id, "venue_order_id": rest.venue_order_id,
                 "created_at": rest.created_at, "fact_state": rest.fact_state,
                 "matched_size": rest.matched_size, "cancel_reason": decision.reason,
                 "cancel_action": decision.action, "cancel_detail": decision.detail}
                for rest, decision in rest_pulls
            ]
            cstats = run_persisted_cancels_for_expired_rests(
                to_cancel,
                PolymarketClient(),
                conn_factory=lambda: get_trade_connection(write_class="live"),
            )
            cancelled = cstats.get("cancelled", 0)

        logger.info(
            "edli_redecision_screen: entry_candidates=%d entry_spine_confirmed=%d "
            "entry_families=%d rest_pulls=%d "
            "held_monitor_families=%d held_reemit_families=%d families_reemitted=%d "
            "pending_redecision_families=%d suppressed_existing_pending=%d "
            "events_emitted=%d rests_cancelled=%d expired_unadmitted=%d "
            "expired_stale_pending=%d expired_rest_pull_blockers=%d",
            len(redecisions), len(entry_redecisions), len(family_keys), len(rest_pulls), len(held_families),
            len(held_reemit_families),
            len(all_families),
            len(pending_families),
            len(set(all_families) & pending_families),
            len(emitted), cancelled, expired_unadmitted, expired_stale_pending,
            expired_rest_pull_blockers,
        )
        if confirm_refresh_summary:
            logger.info(
                "edli_redecision_screen: confirmation_refresh_summary=%r",
                confirm_refresh_summary,
            )
    except sqlite3.OperationalError as exc:
        if not _edli_is_sqlite_lock_error(exc):
            raise
        logger.warning(
            "edli_redecision_screen skipped: database locked during read/write "
            "coordination; no venue side effect attempted and next tick will retry: %s",
            exc,
        )
    finally:
        _edli_redecision_screen_lock.release()




@_scheduler_job("edli_mainstream_warm")
def _edli_mainstream_warm_cycle() -> None:
    """Dedicated EDLI mainstream-forecast point warmer, DECOUPLED from the reactor.

    E2 (STEP 8 efficiency, consolidated timeliness fix). The mainstream
    direction-agreement reference reads an Open-Meteo HTTP point whose client
    applies a Retry-After ``time.sleep`` on 429s. The reactor proof path runs
    UNDER the world_write_mutex, so a synchronous fetch there serialized every
    world write behind a slow/blocked network call. This job fetches the point
    on its OWN cadence and stores it in the process-global warm cache
    (``mainstream_forecast_source._WARM_CACHE``); the reactor proof path reads
    that cache ONLY (``read_mainstream_point_cached``) and fail-closes to None on
    a miss — byte-identical to a stale/absent fetch today.

    Mirrors ``_edli_market_substrate_warm_cycle`` (#45): same warm-cache pattern,
    same fail-soft contract. Scoped to the SAME pending families the reactor will
    decide (city/target_date/metric of pending opportunity_events), so the cache
    is populated for exactly the candidates that need it.

    Bounded by ``mainstream_warm_max_families_per_cycle`` and the same fresh-first
    pending-family order as the substrate warmer. Open-Meteo can 429/sleep on
    quota pressure; unbounded warming over the historical pending backlog turns a
    display/reference cache into a scheduler liveness hazard. Not a DB writer (no
    table owned); the @_scheduler_job decorator is the only wiring needed (B047).
    Fail-soft: a transient Open-Meteo failure logs but never crashes this job
    (consumers fail-closed in the interim).
    """

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    if _defer_for_held_position_monitor("EDLI mainstream warm"):
        return
    from src.data.mainstream_forecast_source import warm_mainstream_point
    from src.state.db import get_world_connection

    cap = _edli_bounded_positive_int(
        edli_cfg, "mainstream_warm_max_families_per_cycle", default=8, maximum=50
    )
    conn = get_world_connection()
    try:
        pending_rows = _pending_family_rows_for_refresh(
            conn, consumer_name="edli_reactor_v1"
        )[:cap]
    except Exception as exc:  # noqa: BLE001 — fail-soft; next tick retries
        logger.error("EDLI mainstream warm: pending-event query failed (non-fatal): %r", exc)
        return
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    warmed = 0
    for row in pending_rows:
        city = str(row[0] or "").strip()
        target_date = str(row[1] or "").strip()
        metric = str(row[2] or "").strip().lower()
        if not (city and target_date and metric in ("high", "low")):
            continue
        try:
            if warm_mainstream_point(city, target_date, metric=metric) is not None:
                warmed += 1
        except Exception as exc:  # noqa: BLE001 — fail-soft per-family; never crash the job
            logger.warning(
                "EDLI mainstream warm: fetch failed for %s/%s/%s (non-fatal): %r",
                city, target_date, metric, exc,
            )
    logger.info(
        "EDLI mainstream warm: warmed=%d of %d pending families cap=%d",
        warmed, len(pending_rows), cap,
    )


@_scheduler_job("world_wal_checkpoint")
def _world_wal_checkpoint_cycle() -> None:
    """Periodic zeus-world.db WAL PASSIVE checkpoint backstop.

    Root (critic-proven, live): ``state/zeus-world.db-wal`` grew to GBs because
    long-lived READER connections held a WAL snapshot across cycles, pinning the
    WAL floor so ``wal_checkpoint`` returned BUSY ``(1,-1,-1)`` and never
    truncated → unbounded growth → eventual lock-starvation of opportunity_events
    emission (30-min ZERO candidates). Part 1 releases each long-lived reader's
    snapshot per cycle so the floor advances; THIS job is the periodic backstop
    that checkpoints freed frames via ``PRAGMA wal_checkpoint(PASSIVE)``.

    Observability: the ``(busy, log_frames, checkpointed_frames)`` triple is
    ALWAYS logged. ``busy == 0`` = safe frames copied; a CHRONIC ``busy == 1`` is a loud
    signal that a reader is still pinning the floor (a part-1 regression) — it is
    NOT silenced. Not a table writer; ``checkpoint_world_wal`` uses a dedicated
    short-lived connection and does NOT take the world write mutex. PASSIVE mode
    must not wait behind live writers, so it cannot block held-position monitor
    redecision. Fail-soft via the decorator.
    """
    from src.state.db import checkpoint_world_wal

    busy, log_frames, ckpt_frames = checkpoint_world_wal()
    if busy == 0:
        logger.info(
            "world WAL checkpoint(PASSIVE): OK busy=%d log_frames=%d checkpointed=%d",
            busy, log_frames, ckpt_frames,
        )
    else:
        # BUSY = a reader still pins the WAL floor.
        # Loud (warning) so chronic starvation is visible, not silent.
        logger.warning(
            "world WAL checkpoint(PASSIVE): BUSY busy=%d log_frames=%d checkpointed=%d "
            "— a reader is pinning the WAL floor (part-1 per-cycle release regression?)",
            busy, log_frames, ckpt_frames,
        )


@_scheduler_job("trades_wal_checkpoint")
def _trades_wal_checkpoint_cycle() -> None:
    """Periodic zeus_trades.db WAL PASSIVE checkpoint backstop — trade-DB twin.

    The 810 MB ``state/zeus_trades.db-wal`` incident (2026-06-16, live): a long-lived
    reader pinned the WAL floor, the -wal never truncated, ``executable_market_
    snapshots`` writes failed ``database is locked`` (auto-checkpoint contention on
    every write) → ``fresh_executable_city_count=0`` → the q-kernel spine could not
    price fresh families → no crosses. zeus-world.db had this backstop; the trade DB
    did not. Same discipline/observability as ``_world_wal_checkpoint_cycle``: a
    dedicated short-lived connection, no write mutex, the (busy, log, checkpointed)
    triple ALWAYS logged; a chronic ``busy == 1`` is the loud signal that a reader is
    not releasing the trade-DB floor. PASSIVE mode must not wait behind the live
    monitor writer. Fail-soft via the decorator.
    """
    from src.state.db import checkpoint_trades_wal

    busy, log_frames, ckpt_frames = checkpoint_trades_wal()
    if busy == 0:
        logger.info(
            "trades WAL checkpoint(PASSIVE): OK busy=%d log_frames=%d checkpointed=%d",
            busy, log_frames, ckpt_frames,
        )
    else:
        logger.warning(
            "trades WAL checkpoint(PASSIVE): BUSY busy=%d log_frames=%d checkpointed=%d "
            "— a reader is pinning the trade-DB WAL floor (long-lived reader not releasing)",
            busy, log_frames, ckpt_frames,
        )


def _edli_bounded_positive_int(config: dict, key: str, *, default: int, maximum: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))


def _edli_positive_int_or_unbounded(
    config: dict, key: str, *, default: int, maximum: int
) -> int | None:
    raw = config.get(key, default)
    if raw is False:
        return None
    if isinstance(raw, str) and raw.strip().lower() in {
        "false",
        "none",
        "no_cap",
        "uncapped",
        "unbounded",
        "unlimited",
    }:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))


def _edli_emit_forecast_snapshot_events(
    world_conn,
    *,
    decision_time: datetime,
    received_at: str,
    limit: int | None,
    source: str | None = None,
    already_pending_keys: set[str] | None = None,
) -> int:
    """Emit EDLI forecast events from committed forecast DB rows.

    With ``source`` set (continuous re-decision), each emitted event uses it so the idempotency_key
    differs per cycle → committed families re-emit a fresh FSR-equivalent every reactor cycle
    (instead of deduping to the consumed FSR) → the reactor re-decides continuously against
    just-in-time-refreshed prices. ``already_pending_keys`` (entity_keys with an unprocessed event)
    are skipped to bound the pending queue. Both default-None → original one-shot catch-up.
    """

    from src.events.event_writer import EventWriter

    events = _edli_build_forecast_snapshot_events(
        world_conn,
        decision_time=decision_time,
        received_at=received_at,
        limit=limit,
        source=source,
        already_pending_keys=already_pending_keys,
        suppress_recent_no_value_refutations=True,
    )
    return len(EventWriter(world_conn).write_many(events))


def _edli_build_forecast_snapshot_events(
    world_conn,
    *,
    decision_time: datetime,
    received_at: str,
    limit: int | None,
    source: str | None = None,
    already_pending_keys: set[str] | None = None,
    suppress_recent_no_value_refutations: bool = False,
    budget_seconds: float | None = None,
) -> list[Any]:
    """Build FSR events without mutating world DB.

    The live reactor calls this before taking ``world_write_mutex``. Forecast
    selection and no-value refutation reads can touch large side tables; the
    mutex must only cover the prune/write/commit unit.
    """

    from src.events.event_writer import EventWriter
    from src.events.triggers.forecast_snapshot_ready import (
        ForecastSnapshotReadyTrigger,
        executable_forecast_live_eligible_reader,
    )
    from src.state.db import get_forecasts_connection_read_only

    deadline_monotonic = (
        time.monotonic() + float(budget_seconds)
        if budget_seconds is not None and float(budget_seconds) > 0
        else None
    )
    _edli_install_sqlite_deadline(world_conn, deadline_monotonic=deadline_monotonic)
    forecasts_conn = get_forecasts_connection_read_only()
    _edli_install_sqlite_deadline(forecasts_conn, deadline_monotonic=deadline_monotonic)
    try:
        trigger = ForecastSnapshotReadyTrigger(
            EventWriter(world_conn),
            live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_conn),
        )
        return trigger.build_committed_snapshot_events(
            forecasts_conn=forecasts_conn,
            decision_time=decision_time,
            received_at=received_at,
            limit=limit,
            source=source,
            already_pending_keys=already_pending_keys,
            suppress_recent_no_value_refutations=suppress_recent_no_value_refutations,
        )
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            logger.warning(
                "EDLI forecast-snapshot build budget exhausted after %.3fs; "
                "skipping emit this cycle and draining already-queued candidates.",
                float(budget_seconds or 0.0),
            )
            return []
        raise
    finally:
        _edli_clear_sqlite_progress_handler(forecasts_conn)
        _edli_clear_sqlite_progress_handler(world_conn)
        forecasts_conn.close()


def _edli_current_held_position_family_keys() -> set[tuple[str, str, str]]:
    """Current held-position families for monitor and duplicate-entry suppression.

    Any family with real position_current exposure must keep receiving position-monitor
    attention even when no new-entry edge fires. Future/pre-settlement held exposure
    also re-enters EDLI_REDECISION_PENDING so the full family selector can exercise
    the already-owned-token fill-up / close-before-open shift lane. Same-day Day0
    remains on the observation-aware monitor lane because forecast-only redecision
    is phase-closed once the target local day starts.
    Fail-soft matches the reactor held-family provider; a read failure must not crash the daemon.
    """

    provider = _edli_reactor_held_family_provider()
    if provider is None:
        return set()
    try:
        raw_families = provider()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "edli_redecision_screen: held-position family read failed; held families not admitted this tick: %r",
            exc,
        )
        return set()
    out: set[tuple[str, str, str]] = set()
    for family in raw_families or ():
        try:
            city, target_date, metric = family
        except (TypeError, ValueError):
            continue
        key = (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        if all(key):
            out.add(key)
    return out


def _edli_family_key_from_belief(belief: Any) -> tuple[str, str, str] | None:
    key = (
        str(getattr(belief, "city", "") or "").strip(),
        str(getattr(belief, "target_date", "") or "").strip(),
        str(getattr(belief, "metric", "") or "").strip(),
    )
    if all(key) and key[2] in {"high", "low"}:
        return key
    return None


def _edli_redecision_condition_scope(
    redecisions: Iterable[Any],
    beliefs: Iterable[Any],
) -> dict[tuple[str, str, str], set[str]]:
    """Map screened entry candidates to the exact condition_ids that need fresh books."""

    by_family_id = {str(getattr(belief, "family_id", "") or ""): belief for belief in beliefs}
    out: dict[tuple[str, str, str], set[str]] = {}
    for redecision in redecisions or ():
        belief = by_family_id.get(str(getattr(redecision, "family_id", "") or ""))
        if belief is None:
            continue
        family_key = _edli_family_key_from_belief(belief)
        if family_key is None:
            continue
        label = str(getattr(redecision, "bin_label", "") or "")
        bin_labels = list(getattr(belief, "bin_labels", None) or ())
        condition_ids = list(getattr(belief, "condition_ids", None) or ())
        for idx, candidate_label in enumerate(bin_labels):
            if str(candidate_label or "") != label or idx >= len(condition_ids):
                continue
            condition_id = str(condition_ids[idx] or "").strip()
            if condition_id:
                out.setdefault(family_key, set()).add(condition_id)
    return out


def _edli_merge_condition_scopes(
    *scopes: dict[tuple[str, str, str], set[str]],
) -> dict[tuple[str, str, str], set[str]]:
    """Union condition scopes without mutating the caller-owned maps."""

    out: dict[tuple[str, str, str], set[str]] = {}
    for scope in scopes:
        for family_key, condition_ids in (scope or {}).items():
            clean = {
                str(condition_id or "").strip()
                for condition_id in condition_ids
                if str(condition_id or "").strip()
            }
            if clean:
                out.setdefault(family_key, set()).update(clean)
    return out


def _edli_merge_rest_pulls(*pull_groups: Iterable[tuple[Any, Any]]) -> list[tuple[Any, Any]]:
    """Merge rest-pull sources without emitting duplicate cancels for one order."""

    out: list[tuple[Any, Any]] = []
    seen: set[tuple[str, str]] = set()
    for pulls in pull_groups:
        for rest, decision in pulls or ():
            command_id = str(getattr(rest, "command_id", "") or "").strip()
            venue_order_id = str(getattr(rest, "venue_order_id", "") or "").strip()
            key = (command_id, venue_order_id)
            if key == ("", ""):
                key = (str(id(rest)), "")
            if key in seen:
                continue
            seen.add(key)
            out.append((rest, decision))
    return out


def _edli_rest_pull_condition_scope(
    rest_pulls: Iterable[tuple[Any, Any]],
    beliefs: Iterable[Any],
) -> dict[tuple[str, str, str], set[str]]:
    """Map live maker-rest pulls to the exact condition_ids being cancelled/repriced.

    A family-optimum replacement pull cancels the current rest so the existing
    reactor can re-certify a sibling. The confirmation refresh must therefore
    prioritize both the cancelled condition and the replacement condition, or
    the next pass can cancel correctly but still lack fresh substrate for the
    better sibling.
    """

    by_family_id = {str(getattr(belief, "family_id", "") or ""): belief for belief in beliefs}
    out: dict[tuple[str, str, str], set[str]] = {}
    for rest, decision in rest_pulls or ():
        family_key = _edli_family_key_from_rest(rest)
        if family_key is None:
            belief = by_family_id.get(str(getattr(rest, "family_id", "") or ""))
            family_key = _edli_family_key_from_belief(belief) if belief is not None else None
        if family_key is None:
            continue
        condition_id = str(getattr(rest, "condition_id", "") or "").strip()
        if condition_id:
            out.setdefault(family_key, set()).add(condition_id)
        replacement_condition_id = str(
            getattr(decision, "replacement_condition_id", "") or ""
        ).strip()
        if replacement_condition_id:
            out.setdefault(family_key, set()).add(replacement_condition_id)
    return out


def _edli_open_rest_condition_scope(
    open_rests: Iterable[Any],
    beliefs: Iterable[Any],
) -> dict[tuple[str, str, str], set[str]]:
    """Map all live maker rests to condition_ids that need price refresh.

    A rest cannot decide whether to cancel/reprice from stale books. This scope is
    intentionally built before ``screen_resting_orders`` so the confirmation
    refresh can make the rest screen's price inputs current.
    """

    by_family_id = {str(getattr(belief, "family_id", "") or ""): belief for belief in beliefs}
    out: dict[tuple[str, str, str], set[str]] = {}
    for rest in open_rests or ():
        family_key = _edli_family_key_from_rest(rest)
        if family_key is None:
            belief = by_family_id.get(str(getattr(rest, "family_id", "") or ""))
            family_key = _edli_family_key_from_belief(belief) if belief is not None else None
        if family_key is None:
            continue
        condition_id = str(getattr(rest, "condition_id", "") or "").strip()
        if condition_id:
            out.setdefault(family_key, set()).add(condition_id)
    return out


def _edli_family_key_from_rest(rest: Any) -> tuple[str, str, str] | None:
    city = str(getattr(rest, "city", "") or "").strip()
    target_date = str(getattr(rest, "target_date", "") or "").strip()
    metric = str(getattr(rest, "metric", "") or "").strip()
    if city and target_date and metric:
        return (city, target_date, metric)
    return None


def _edli_condition_latest_snapshot_executable(trade_conn, condition_id: str) -> bool:
    """Return False only when the latest known substrate says this condition cannot trade."""

    clean_condition_id = str(condition_id or "").strip()
    if not clean_condition_id:
        return False
    try:
        cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()
        }
    except sqlite3.Error:
        return True
    required = {"condition_id", "captured_at", "snapshot_id"}
    if not required.issubset(cols):
        return True
    selected_cols = [
        "closed" if "closed" in cols else "0 AS closed",
        "enable_orderbook" if "enable_orderbook" in cols else "1 AS enable_orderbook",
        "accepting_orders" if "accepting_orders" in cols else "1 AS accepting_orders",
    ]
    try:
        row = trade_conn.execute(
            f"""
            SELECT {", ".join(selected_cols)}
              FROM executable_market_snapshots
             WHERE condition_id = ?
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            (clean_condition_id,),
        ).fetchone()
    except sqlite3.Error:
        return True
    if row is None:
        return True

    def _truthy(value: object, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes"}:
            return True
        if text in {"0", "false", "no"}:
            return False
        return default

    closed = _truthy(row[0], False)
    enable_orderbook = _truthy(row[1], True)
    accepting_orders = _truthy(row[2], True)
    return bool(not closed and enable_orderbook and accepting_orders)


def _edli_current_held_position_condition_scope() -> dict[tuple[str, str, str], set[str]]:
    """Current held-position condition_ids for scoped redecision freshness admission."""

    from src.state.db import get_trade_connection_read_only

    out: dict[tuple[str, str, str], set[str]] = {}
    trade_ro = None
    try:
        trade_ro = get_trade_connection_read_only()
        try:
            cols = {
                str(row[1])
                for row in trade_ro.execute("PRAGMA table_info(position_current)").fetchall()
            }
        except sqlite3.Error:
            return {}
        required = {
            "city",
            "target_date",
            "temperature_metric",
            "phase",
            "condition_id",
            "chain_state",
            "chain_shares",
        }
        if not required.issubset(cols):
            return {}
        from src.contracts.position_truth import CURRENT_MONEY_RISK_CHAIN_STATES

        chain_state_values = tuple(sorted(CURRENT_MONEY_RISK_CHAIN_STATES))
        chain_placeholders = ",".join("?" for _ in chain_state_values)
        rows = trade_ro.execute(
            f"""
            SELECT city, target_date, temperature_metric, condition_id
              FROM position_current
             WHERE (
                    (
                        phase IN ('active', 'day0_window', 'pending_exit')
                        AND COALESCE(chain_state, '') IN ({chain_placeholders})
                    )
                    OR (
                        phase = 'quarantined'
                        AND COALESCE(chain_state, '') IN ({chain_placeholders})
                    )
                   )
               AND condition_id IS NOT NULL
               AND TRIM(condition_id) != ''
               AND COALESCE(chain_shares, 0) > 0.000001
            """,
            (*chain_state_values, *chain_state_values),
        ).fetchall()
        for row in rows:
            family_key = (
                str(row[0] or "").strip(),
                str(row[1] or "").strip(),
                str(row[2] or "").strip(),
            )
            condition_id = str(row[3] or "").strip()
            if all(family_key) and family_key[2] in {"high", "low"} and condition_id:
                if not _edli_condition_latest_snapshot_executable(trade_ro, condition_id):
                    continue
                out.setdefault(family_key, set()).add(condition_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "edli_redecision_screen: held-position condition scope read failed; "
            "held condition freshness not admitted this tick: %r",
            exc,
        )
        return {}
    finally:
        if trade_ro is not None:
            try:
                trade_ro.close()
            except Exception:  # noqa: BLE001
                pass
    return out


def _edli_current_held_position_family_condition_scope(
    families: set[tuple[str, str, str]] | None = None,
) -> dict[tuple[str, str, str], set[str]]:
    """Full family condition scope for held-position redecision.

    Held-position redecision is a family optimization problem, not an old-token
    refresh.  A stale or unrefreshed sibling can be the best fill-up/shift target,
    so the confirmation producer must refresh the complete executable family.
    """

    held_families = set(families or set()) or set(_edli_current_held_position_condition_scope())
    clean_families = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in held_families
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    if not clean_families:
        return {}

    from src.data.market_topology_rows import _event_family_market_topology_rows
    from src.state.db import get_forecasts_connection_read_only, get_trade_connection_read_only

    forecasts_ro = get_forecasts_connection_read_only()
    trade_ro = get_trade_connection_read_only()
    try:
        out: dict[tuple[str, str, str], set[str]] = {}
        for family in sorted(clean_families):
            city, target_date, metric = family
            try:
                topology_rows = _event_family_market_topology_rows(
                    forecasts_ro,
                    {"city": city, "target_date": target_date, "metric": metric},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "edli_redecision_screen: held-family topology read failed; "
                    "family not admitted for full redecision this tick: city=%r "
                    "target_date=%r metric=%r error=%r",
                    city,
                    target_date,
                    metric,
                    exc,
                )
                continue
            for row in topology_rows or ():
                condition_id = str(row.get("condition_id") or "").strip()
                if not condition_id:
                    continue
                if not _edli_condition_latest_snapshot_executable(trade_ro, condition_id):
                    continue
                out.setdefault(family, set()).add(condition_id)
        return out
    finally:
        try:
            forecasts_ro.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            trade_ro.close()
        except Exception:  # noqa: BLE001
            pass


def _edli_families_with_fresh_scoped_executable_substrate(
    condition_scope: dict[tuple[str, str, str], set[str]],
    *,
    now_utc: datetime,
) -> set[tuple[str, str, str]]:
    """Families whose scoped money-path conditions have fresh YES and NO books.

    Continuous redecision is triggered by specific entry candidates, maker rests,
    and held positions. A PARTIAL refresh should therefore prove the exact
    conditions that are about to re-enter the money path, not require every
    topology bin in a large weather family to refresh in the same tick.
    """

    clean_scope: dict[tuple[str, str, str], set[str]] = {}
    for family, condition_ids in (condition_scope or {}).items():
        try:
            city, target_date, metric = family
        except (TypeError, ValueError):
            continue
        family_key = (
            str(city or "").strip(),
            str(target_date or "").strip(),
            str(metric or "").strip(),
        )
        clean_condition_ids = {
            str(condition_id or "").strip()
            for condition_id in condition_ids or set()
            if str(condition_id or "").strip()
        }
        if all(family_key) and family_key[2] in {"high", "low"} and clean_condition_ids:
            clean_scope.setdefault(family_key, set()).update(clean_condition_ids)
    if not clean_scope:
        return set()
    from src.state.db import get_trade_connection_read_only

    fresh_at_iso = now_utc.isoformat()
    trade_ro = get_trade_connection_read_only()
    try:
        out: set[tuple[str, str, str]] = set()
        for family, condition_ids in sorted(clean_scope.items()):
            if all(_condition_buy_sides_fresh(trade_ro, cid, fresh_at_iso) for cid in sorted(condition_ids)):
                out.add(family)
        return out
    finally:
        try:
            trade_ro.close()
        except Exception:  # noqa: BLE001
            pass


def _edli_refresh_continuous_money_path_families(
    families: set[tuple[str, str, str]],
    *,
    now_utc: datetime,
    priority_condition_ids: Iterable[str] | None = None,
) -> dict:
    """Prioritize current continuous-money-path families before redecision emit.

    Continuous redecision is a consumer.  The substrate-observer daemon is the
    executable-snapshot producer.  This function only marks live-money families
    for priority sidecar capture, then the caller independently admits families
    whose scoped executable substrate is already fresh.
    """

    clean_families = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in families or set()
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    priority_conditions = {
        str(condition_id or "").strip()
        for condition_id in (priority_condition_ids or ())
        if str(condition_id or "").strip()
    }
    if not clean_families:
        return {"status": "no_families", "families_requested": 0}
    lock_timeout_s = max(
        0.0,
        float(os.environ.get("ZEUS_REDECISION_CONFIRM_REFRESH_LOCK_TIMEOUT_SECONDS", "25.0")),
    )
    if not _edli_redecision_confirm_refresh_lock.acquire(timeout=lock_timeout_s):
        return {
            "status": "skipped_lock_busy",
            "families_requested": len(clean_families),
            "lock_timeout_seconds": lock_timeout_s,
            "lock": "edli_redecision_confirm_refresh",
        }
    try:
        try:
            from src.data.substrate_priority import mark_money_path_substrate_priority

            mark_money_path_substrate_priority(
                reason="continuous_redecision_confirm_refresh",
                ttl_seconds=35.0,
                families=clean_families,
                condition_ids=priority_conditions,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "edli_redecision_screen: substrate priority marker write failed: %r",
                exc,
            )
            return {
                "status": "priority_marker_failed",
                "families_requested": len(clean_families),
                "reason": str(exc),
            }
        return {
            "status": "priority_marked",
            "families_requested": len(clean_families),
            "priority_condition_count": len(priority_conditions),
            "executable_substrate_coverage_status": "READ_FILTER_REQUIRED",
            "marked_at": now_utc.astimezone(timezone.utc).isoformat(),
        }
    finally:
        try:
            _edli_redecision_confirm_refresh_lock.release()
        except RuntimeError:
            pass


def _edli_redecision_priority_condition_limit() -> int:
    raw = os.environ.get(
        "ZEUS_REDECISION_PRIORITY_CONDITION_LIMIT",
        os.environ.get("ZEUS_MARKET_DISCOVERY_PRIORITY_DIRECT_CLOB_PREFETCH_MAX_CONDITIONS", "8"),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 8
    return max(1, min(500, value))


def _edli_confirm_priority_condition_ids(
    *,
    rest_condition_scope: dict[tuple[str, str, str], set[str]],
    held_condition_scope: dict[tuple[str, str, str], set[str]],
    entry_condition_scope: dict[tuple[str, str, str], set[str]],
    entry_refresh_condition_scope: dict[tuple[str, str, str], set[str]],
    open_rest_condition_scope: dict[tuple[str, str, str], set[str]],
    full_family_refresh_families: set[tuple[str, str, str]] | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return a bounded, ordered money-path condition frontier for sidecar capture."""

    condition_limit = _edli_redecision_priority_condition_limit() if limit is None else max(1, int(limit))
    full_family_refresh = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in (full_family_refresh_families or set())
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    ordered: list[str] = []
    seen: set[str] = set()

    def _add_scope(scope: dict[tuple[str, str, str], set[str]]) -> None:
        for family_key in sorted(scope or {}):
            try:
                normalized_family = tuple(str(part or "").strip() for part in family_key)
            except TypeError:
                normalized_family = ("", "", "")
            if normalized_family in full_family_refresh:
                continue
            for condition_id in sorted(scope.get(family_key) or set()):
                clean = str(condition_id or "").strip()
                if not clean or clean in seen:
                    continue
                seen.add(clean)
                ordered.append(clean)
                if len(ordered) >= condition_limit:
                    return

    for scope in (
        rest_condition_scope,
        held_condition_scope,
        entry_condition_scope,
        entry_refresh_condition_scope,
        open_rest_condition_scope,
    ):
        if len(ordered) >= condition_limit:
            break
        _add_scope(scope)
    return ordered


def _edli_confirmation_refresh_unavailable(summary: dict | None) -> bool:
    if not isinstance(summary, dict):
        return True
    status = str(summary.get("status") or "")
    if status == "skipped_lock_busy" or status.startswith("error"):
        return True
    return False


def _edli_confirmation_refresh_needs_scoped_freshness_filter(summary: dict | None) -> bool:
    # Incomplete coverage routes to scoped freshness admission — including when SOME
    # families hit a transient `database is locked` or the batch-prefetch cycle
    # inserts zero rows while prior quotes are still fresh. The scoped filter
    # (_edli_families_with_fresh_scoped_executable_substrate) does an INDEPENDENT fresh read of
    # the money-path conditions' executable substrate, so a lock that left current
    # rests/candidates/held legs stale cannot admit them; it only excludes them.
    # Forcing a full-tick drop on any lock hit
    # (the prior `and not _has_sqlite_lock_failures`) discarded EVERY candidate + reprice
    # on the tick — even families with complete fresh substrate — which (with ~757 lock
    # hits/run of WAL contention) was the dominant reason the candidate pipeline emitted
    # for only ~2 families instead of the full universe (2026-06-23 candidate-pipeline fix).
    if not isinstance(summary, dict):
        return False
    if str(summary.get("status") or "") == "priority_marked":
        return True
    return (
        str(summary.get("status") or "") == "refreshed"
        and str(summary.get("executable_substrate_coverage_status") or "") in {"NONE", "PARTIAL"}
    )


def _edli_families_with_fresh_executable_substrate(
    families: set[tuple[str, str, str]],
    *,
    now_utc: datetime,
) -> set[tuple[str, str, str]]:
    """Families whose complete market topology has fresh executable snapshots.

    This is the family-level confirmation proof for continuous redecision. A
    partial capture must not freeze every current money-path family, but it also
    must not queue decisions from stale prices. Each family is admitted only when
    every known condition has fresh YES and NO buy-side executable substrate.
    """

    clean_families = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in families or set()
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    if not clean_families:
        return set()
    from src.data.market_topology_rows import _event_family_market_topology_rows
    from src.state.db import get_forecasts_connection_read_only, get_trade_connection_read_only

    fresh_at_iso = now_utc.isoformat()
    out: set[tuple[str, str, str]] = set()
    forecasts_ro = get_forecasts_connection_read_only()
    trade_ro = get_trade_connection_read_only()
    try:
        for family in sorted(clean_families):
            city, target_date, metric = family
            try:
                topology_rows = _event_family_market_topology_rows(
                    forecasts_ro,
                    {"city": city, "target_date": target_date, "metric": metric},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "edli_redecision_screen: family freshness topology read failed; "
                    "family not admitted this tick city=%r target_date=%r metric=%r error=%r",
                    city,
                    target_date,
                    metric,
                    exc,
                )
                continue
            condition_ids = {
                str(row.get("condition_id") or "").strip()
                for row in topology_rows
                if str(row.get("condition_id") or "").strip()
            }
            if not condition_ids:
                continue
            if all(_condition_buy_sides_fresh(trade_ro, cid, fresh_at_iso) for cid in condition_ids):
                out.add(family)
    finally:
        try:
            forecasts_ro.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            trade_ro.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def _edli_reemittable_forecast_family_keys(
    families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
    log_context: str,
) -> set[tuple[str, str, str]]:
    """Families that may enter forecast redecision this tick.

    Day0 / post-trading families may still be managed by their owning lanes
    (held positions by chain-sync/exit monitor, new entry discovery by ordinary
    FSR when phase-admissible). They must not be logged as forecast re-emitted
    or keep stale EDLI_REDECISION_PENDING rows alive, because the FSR trigger
    will drop them with the same forecast-only phase predicate.
    """

    if not families:
        return set()
    from src.strategy.market_phase import market_phase_admits

    out: set[tuple[str, str, str]] = set()
    for city, target_date, metric in families:
        try:
            admitted = market_phase_admits(
                city=city,
                target_date=target_date,
                metric=metric,
                decision_time=decision_time,
                market_row={},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "edli_redecision_screen: %s phase read failed; "
                "family not forecast-reemitted this tick: city=%r target_date=%r metric=%r error=%r",
                log_context,
                city,
                target_date,
                metric,
                exc,
            )
            continue
        if admitted:
            out.add((city, target_date, metric))
    return out


def _edli_entry_redecision_family_keys(
    raw_entry_families: set[tuple[str, str, str]],
    held_families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
) -> set[tuple[str, str, str]]:
    """New-entry redecision families after removing already-held exposure.

    Fresh entry and held redecision have different safety semantics. New-entry
    screening excludes held families so it cannot duplicate owned exposure. Held
    families that are still forecast-lane admissible are re-emitted separately by
    _edli_reemittable_held_position_family_keys and enter the reactor with
    allow_same_family_monitor_owned=True, where fill-up and shift-bin leases own
    the only permitted same-family side effects.
    """

    return _edli_reemittable_forecast_family_keys(
        set(raw_entry_families or set()) - set(held_families or set()),
        decision_time=decision_time,
        log_context="entry-screen",
    )


def _edli_reemittable_held_position_family_keys(
    families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
) -> set[tuple[str, str, str]]:
    """Held-position families eligible for full pre-settlement redecision.

    Monitor refresh owns the cheap hold/direct-sell check for all active positions.
    It does not own same-family fill-up or close-before-open shift execution. While
    a held family is still in the forecast-lane admit phase, re-emit it through
    EDLI_REDECISION_PENDING so the existing reactor path runs with
    allow_same_family_monitor_owned=True and the family-rebalance lease enforces
    one active fill-up/shift per family. Day0/phase-closed held positions are left
    on the observation-aware monitor path.
    """

    return _edli_reemittable_forecast_family_keys(
        set(families or set()),
        decision_time=decision_time,
        log_context="held-redecision",
    )


def _redecision_event_with_origin(event: Any, origin: str) -> Any:
    """Return an equivalent immutable redecision event with explicit scheduler origin."""

    try:
        from src.events.opportunity_event import make_opportunity_event

        payload = json.loads(str(event.payload_json or "{}"))
        if not isinstance(payload, dict):
            return event
        payload["redecision_origin"] = str(origin)
        return make_opportunity_event(
            event_type=event.event_type,
            entity_key=event.entity_key,
            source=event.source,
            observed_at=event.observed_at,
            available_at=event.available_at,
            received_at=event.received_at,
            causal_snapshot_id=event.causal_snapshot_id,
            payload=payload,
            priority=event.priority,
            expires_at=event.expires_at,
            created_at=event.created_at,
        )
    except Exception:  # noqa: BLE001
        return event


def _redecision_payload_origin(payload: Mapping[str, Any]) -> str:
    return str(payload.get("redecision_origin") or "").strip().lower()


def _preserve_recent_rest_pull_redecision(
    payload: Mapping[str, Any],
    *,
    event_created_at: str,
    decision_dt: datetime,
) -> bool:
    """Keep cancel/reprice redecision rows alive long enough for the fresh screen.

    A pulled maker rest is removed from the open-rest input set as soon as the
    terminal cancel/no-fill fact is reconciled. The follow-on redecision event is
    the durable continuity proof for that family; expiring it on the next generic
    no-edge screen erases the price-management chain before the reactor can
    reprice/re-submit/decline from current evidence.
    """

    if _redecision_payload_origin(payload) != "rest_pull":
        return False
    try:
        created_dt = datetime.fromisoformat(str(event_created_at).replace("Z", "+00:00"))
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        age_seconds = (decision_dt - created_dt.astimezone(timezone.utc)).total_seconds()
    except Exception:  # noqa: BLE001
        return False
    return 0.0 <= age_seconds < float(_REDECISION_REST_PULL_EXPIRY_GRACE_SECONDS)


def _edli_supersede_pending_redecisions_for_rest_pull_families(
    world_conn,
    rest_pull_families: set[tuple[str, str, str]],
    *,
    decision_time: str,
) -> int:
    """Expire generic pending redecision rows that would suppress a rest-pull emit.

    A live OPEN maker rest that has fired ``rest_pull`` is command-management
    evidence: the order must be cancelled/repriced through a durable
    ``redecision_origin=rest_pull`` row. A generic market-price or entry-screen
    pending event for the same family is not equivalent because the rest may
    disappear from the open-rest set after cancel; if that generic row is the one
    preserved, the cancel/reprice continuity proof can be lost.

    Only unclaimed ``pending`` rows are superseded. Claimed/processing rows may
    already be inside the reactor and are left to the normal lease/stale paths.
    """

    clean_families = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in rest_pull_families or set()
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    if not clean_families:
        return 0
    from src.events.continuous_redecision import REDECISION_EVENT_TYPE as _REDECISION_EVENT_TYPE

    try:
        rows = world_conn.execute(
            """
            SELECT e.event_id, e.payload_json
              FROM opportunity_event_processing p
                   INDEXED BY idx_opportunity_event_processing_status
              JOIN opportunity_events e ON e.event_id = p.event_id
             WHERE p.consumer_name = 'edli_reactor_v1'
               AND p.processing_status = 'pending'
               AND e.event_type = ?
             ORDER BY p.updated_at ASC
             LIMIT 5000
            """,
            (_REDECISION_EVENT_TYPE,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return 0
    expire_ids: list[str] = []
    for row in rows:
        try:
            event_id = str(row[0] or "")
            payload = json.loads(str(row[1] or "{}"))
            family = (
                str(payload.get("city") or "").strip(),
                str(payload.get("target_date") or "").strip(),
                str(payload.get("metric") or "").strip(),
            )
        except Exception:  # noqa: BLE001
            continue
        if not event_id or family not in clean_families:
            continue
        if _redecision_payload_origin(payload) == "rest_pull":
            continue
        expire_ids.append(event_id)
    if not expire_ids:
        return 0
    now = str(decision_time)
    changed = 0
    for start in range(0, len(expire_ids), 250):
        chunk = expire_ids[start : start + 250]
        placeholders = ",".join("?" for _ in chunk)
        cur = world_conn.execute(
            f"""
            UPDATE opportunity_event_processing
               SET processing_status = 'expired',
                   processed_at = ?,
                   updated_at = ?,
                   last_error = 'REDECISION_SUPERSEDED_BY_REST_PULL:open_rest_requires_cancel_reprice'
             WHERE consumer_name = 'edli_reactor_v1'
               AND processing_status = 'pending'
               AND event_id IN ({placeholders})
            """,
            (now, now, *chunk),
        )
        changed += int(cur.rowcount or 0)
    return changed


def _edli_expire_unadmitted_redecision_pending(
    world_conn,
    admitted_families: set[tuple[str, str, str]],
    *,
    decision_time: str,
    supersede_stale_admitted: bool = False,
    claim_grace_seconds: float | None = None,
) -> int:
    """Expire redecision rows no longer backed by entry edge or rest reprice value.

    Fresh pending rows are not safe to expire immediately: the screen may emit a
    row seconds before the next reactor claim cycle. Pending rows must survive a
    claim grace window; processing rows are eligible only after the EventStore
    claim lease has expired. An in-flight reactor event must not be terminalized
    by the screen job that emitted it.
    """

    from src.events.continuous_redecision import REDECISION_EVENT_TYPE as _REDECISION_EVENT_TYPE

    try:
        decision_dt = datetime.fromisoformat(str(decision_time).replace("Z", "+00:00"))
        if decision_dt.tzinfo is None:
            decision_dt = decision_dt.replace(tzinfo=timezone.utc)
        decision_dt = decision_dt.astimezone(timezone.utc)
        if claim_grace_seconds is None:
            claim_grace_seconds = (
                _REDECISION_FRESH_SCREEN_SUPERSEDE_GRACE_SECONDS
                if supersede_stale_admitted
                else _REDECISION_PENDING_EXPIRY_GRACE_SECONDS
            )
        claim_grace_seconds = max(0.0, float(claim_grace_seconds))
        stale_processing_cutoff = (
            decision_dt - timedelta(seconds=claim_grace_seconds)
        ).isoformat()
        pending_admission_cutoff = (
            decision_dt - timedelta(seconds=claim_grace_seconds)
        ).isoformat()
    except Exception:  # noqa: BLE001
        decision_dt = datetime.now(timezone.utc)
        stale_processing_cutoff = ""
        pending_admission_cutoff = ""

    try:
        candidate_ids: list[str] = []
        if pending_admission_cutoff:
            candidate_ids.extend(
                str(row[0])
                for row in world_conn.execute(
                    """
                    SELECT p.event_id
                     FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_status
                     WHERE p.consumer_name = 'edli_reactor_v1'
                       AND p.processing_status = 'pending'
                     ORDER BY p.updated_at ASC
                     LIMIT 5000
                    """,
                ).fetchall()
            )
        if stale_processing_cutoff:
            candidate_ids.extend(
                str(row[0])
                for row in world_conn.execute(
                    """
                    SELECT p.event_id
                      FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_pending_retry_floor
                     WHERE p.consumer_name = 'edli_reactor_v1'
                       AND p.processing_status = 'processing'
                       AND p.claimed_at IS NOT NULL
                       AND p.claimed_at <= ?
                     ORDER BY p.claimed_at ASC
                     LIMIT 5000
                    """,
                    (stale_processing_cutoff,),
                ).fetchall()
            )
        candidate_ids = list(dict.fromkeys(event_id for event_id in candidate_ids if event_id))
        rows = []
        for start in range(0, len(candidate_ids), 250):
            chunk = candidate_ids[start : start + 250]
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                world_conn.execute(
                    f"""
                    SELECT e.event_id, e.payload_json, e.created_at
                      FROM opportunity_events e
                     WHERE e.event_type = ?
                       AND e.created_at <= ?
                       AND e.received_at <= ?
                       AND e.event_id IN ({placeholders})
                    """,
                    (
                        _REDECISION_EVENT_TYPE,
                        pending_admission_cutoff,
                        pending_admission_cutoff,
                        *chunk,
                    ),
                ).fetchall()
            )
    except Exception:  # noqa: BLE001
        return 0
    expire_by_reason: dict[str, list[str]] = {}
    for row in rows:
        try:
            event_id = str(row[0] or "")
            payload = json.loads(str(row[1] or "{}"))
            event_created_at = str(row[2] or "")
            family = (
                str(payload.get("city") or "").strip(),
                str(payload.get("target_date") or "").strip(),
                str(payload.get("metric") or "").strip(),
            )
        except Exception:  # noqa: BLE001
            continue
        if not event_id or not all(family):
            continue
        if family not in admitted_families:
            if _preserve_recent_rest_pull_redecision(
                payload,
                event_created_at=event_created_at,
                decision_dt=decision_dt,
            ):
                continue
            reason = "REDECISION_ADMISSION_EXPIRED:no_current_edge_or_rest_reprice_value"
            expire_by_reason.setdefault(reason, []).append(event_id)
        elif supersede_stale_admitted:
            reason = "REDECISION_SUPERSEDED_BY_FRESH_SCREEN:stale_pending_claim_grace_elapsed"
            expire_by_reason.setdefault(reason, []).append(event_id)
    if not expire_by_reason:
        return 0
    now = str(decision_time)
    changed = 0
    for reason, expire_ids in expire_by_reason.items():
        for start in range(0, len(expire_ids), 250):
            chunk = expire_ids[start : start + 250]
            placeholders = ",".join("?" for _ in chunk)
            cur = world_conn.execute(
                f"""
                UPDATE opportunity_event_processing
                   SET processing_status = 'expired',
                       processed_at = ?,
                       updated_at = ?,
                       last_error = ?
                 WHERE consumer_name = 'edli_reactor_v1'
                   AND (
                        processing_status = 'pending'
                     OR (
                        processing_status = 'processing'
                        AND claimed_at IS NOT NULL
                        AND ? != ''
                        AND claimed_at <= ?
                     )
                   )
                   AND event_id IN ({placeholders})
                """,
                (now, now, reason, stale_processing_cutoff, stale_processing_cutoff, *chunk),
            )
            changed += int(cur.rowcount or 0)
    return changed


def _edli_expire_unready_forecast_snapshot_pending(
    world_conn,
    forecasts_conn,
    *,
    decision_time: str,
) -> int:
    """Expire replacement FSR rows whose current latest posterior is not spine-ready.

    Pending FSR rows are admission work, not durable facts. Under the replacement lane an
    ``rmf-...`` event is consumable only when the family's latest posterior still matches that
    neutral id and has at least three same-cycle raw_model_forecasts members. If the latest
    posterior has advanced to a cycle without raw-model members, keeping the old pending row
    alive only burns reactor budget and produces MU_SIGMA_NOT_STASHED no-trades.
    """

    try:
        from src.events.triggers.forecast_snapshot_ready import REPLACEMENT_0_1_PRODUCT_ID
    except Exception:  # noqa: BLE001
        return 0
    try:
        rows = world_conn.execute(
            """
            SELECT e.event_id,
                   e.causal_snapshot_id,
                   json_extract(e.payload_json, '$.city') AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date,
                   json_extract(e.payload_json, '$.metric') AS metric
              FROM opportunity_event_processing p
              JOIN opportunity_events e ON e.event_id = p.event_id
             WHERE p.consumer_name = 'edli_reactor_v1'
               AND p.processing_status = 'pending'
               AND e.event_type = 'FORECAST_SNAPSHOT_READY'
               AND e.causal_snapshot_id LIKE 'rmf-%'
            """
        ).fetchall()
    except Exception:  # noqa: BLE001
        return 0
    expire_ids: list[str] = []
    candidates: list[tuple[str, str, str, str, str]] = []
    for row in rows:
        try:
            event_id = str(row[0] or "")
            causal_snapshot_id = str(row[1] or "")
            city = str(row[2] or "").strip()
            target_date = str(row[3] or "").strip()
            metric = str(row[4] or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if not (event_id and causal_snapshot_id and city and target_date and metric):
            continue
        candidates.append((event_id, causal_snapshot_id, city, target_date, metric))
    if not candidates:
        return 0

    family_keys = sorted({(city, target_date, metric) for _, _, city, target_date, metric in candidates})
    latest_cycle_by_family: dict[tuple[str, str, str], str] = {}
    _FORECAST_FAMILY_CHUNK = 250
    for start in range(0, len(family_keys), _FORECAST_FAMILY_CHUNK):
        chunk = family_keys[start : start + _FORECAST_FAMILY_CHUNK]
        key_predicate = " OR ".join(
            "(city = ? AND target_date = ? AND temperature_metric = ?)" for _ in chunk
        )
        params: list[Any] = [REPLACEMENT_0_1_PRODUCT_ID, decision_time, decision_time]
        for city, target_date, metric in chunk:
            params.extend([city, target_date, metric])
        try:
            latest_rows = forecasts_conn.execute(
                f"""
                SELECT city, target_date, temperature_metric, source_cycle_time
                  FROM forecast_posteriors
                 WHERE product_id = ?
                   AND runtime_layer = 'live'
                   AND (source_available_at IS NULL OR source_available_at <= ?)
                   AND (computed_at IS NULL OR computed_at <= ?)
                   AND ({key_predicate})
                 ORDER BY city ASC,
                          target_date ASC,
                          temperature_metric ASC,
                          source_cycle_time DESC,
                          computed_at DESC,
                          posterior_id DESC
                """,
                tuple(params),
            ).fetchall()
        except Exception:  # noqa: BLE001
            continue
        for latest in latest_rows:
            key = (str(latest[0] or ""), str(latest[1] or ""), str(latest[2] or ""))
            if key not in latest_cycle_by_family and latest[3] is not None:
                latest_cycle_by_family[key] = str(latest[3] or "")

    member_count_by_family_cycle: dict[tuple[str, str, str, str], int] = {}
    families_by_cycle_date: dict[str, list[tuple[str, str, str]]] = {}
    for key, source_cycle_time in latest_cycle_by_family.items():
        cycle_date = str(source_cycle_time or "")[:10]
        if len(cycle_date) == 10:
            families_by_cycle_date.setdefault(cycle_date, []).append(key)
    for cycle_date, keys in families_by_cycle_date.items():
        try:
            cycle_start = f"{cycle_date}T00:00:00+00:00"
            cycle_end = f"{(date.fromisoformat(cycle_date) + timedelta(days=1)).isoformat()}T00:00:00+00:00"
        except ValueError:
            continue
        for start in range(0, len(keys), _FORECAST_FAMILY_CHUNK):
            chunk = keys[start : start + _FORECAST_FAMILY_CHUNK]
            key_predicate = " OR ".join(
                "(city = ? AND target_date = ? AND metric = ?)" for _ in chunk
            )
            params: list[Any] = [cycle_start, cycle_end, decision_time]
            for city, target_date, metric in chunk:
                params.extend([city, target_date, metric])
            try:
                count_rows = forecasts_conn.execute(
                    f"""
                    SELECT city, target_date, metric, COUNT(DISTINCT model)
                      FROM raw_model_forecasts INDEXED BY idx_raw_model_forecasts_endpoint_family_cycle_members
                     WHERE source_cycle_time >= ?
                       AND source_cycle_time < ?
                       AND source_available_at <= ?
                       AND endpoint = 'single_runs'
                       AND forecast_value_c IS NOT NULL
                       AND ({key_predicate})
                     GROUP BY city, target_date, metric
                    """,
                    tuple(params),
                ).fetchall()
            except Exception:  # noqa: BLE001
                continue
            for count_row in count_rows:
                key = (
                    str(count_row[0] or ""),
                    str(count_row[1] or ""),
                    str(count_row[2] or ""),
                    cycle_date,
                )
                member_count_by_family_cycle[key] = int(count_row[3] or 0)

    for event_id, causal_snapshot_id, city, target_date, metric in candidates:
        latest_cycle = latest_cycle_by_family.get((city, target_date, metric))
        if latest_cycle is None:
            expire_ids.append(event_id)
            continue
        cycle_date = str(latest_cycle or "")[:10]
        current_causal = f"rmf-{city}|{target_date}|{metric}|{cycle_date}"
        if len(cycle_date) != 10 or causal_snapshot_id != current_causal:
            expire_ids.append(event_id)
            continue
        member_count = member_count_by_family_cycle.get((city, target_date, metric, cycle_date), 0)
        if member_count < 3:
            expire_ids.append(event_id)
    if not expire_ids:
        return 0
    now = str(decision_time)
    changed = 0
    for start in range(0, len(expire_ids), 250):
        chunk = expire_ids[start : start + 250]
        placeholders = ",".join("?" for _ in chunk)
        cur = world_conn.execute(
            f"""
            UPDATE opportunity_event_processing
               SET processing_status = 'expired',
                   processed_at = ?,
                   updated_at = ?,
                   last_error = 'FORECAST_ADMISSION_EXPIRED:latest_posterior_spine_unavailable'
             WHERE consumer_name = 'edli_reactor_v1'
               AND processing_status = 'pending'
               AND event_id IN ({placeholders})
            """,
            (now, now, *chunk),
        )
        changed += int(cur.rowcount or 0)
    return changed


def _edli_pending_entity_keys(
    world_conn,
    *,
    event_types: tuple[str, ...] = ("FORECAST_SNAPSHOT_READY",),
) -> set[str]:
    """entity_keys of opportunity_events still unprocessed for the EDLI reactor consumer.

    Passed as ``already_pending_keys`` to the continuous re-decision emit so families with a
    re-decision event already queued are not re-emitted (bounds the pending queue; the rate
    self-regulates to families the reactor has already drained).

    CLAIM-STORM ROOT CAUSE (2026-06-11 17:51Z incident): this helper used to run
    ``PRAGMA busy_timeout = 250`` on ``world_conn`` WITHOUT RESTORING IT. PRAGMA
    busy_timeout is CONNECTION-WIDE and PERMANENT — and ``world_conn`` here is the
    SAME connection the EventStore wraps for the reactor's ``claim()`` writes. One
    cycle after the first emit pass, every claim on the shared conn waited at most
    250 ms (instead of the configured 30 s) before raising "database is locked":
    measured live as 44-250 claim bounces per cycle (processed=0 retried=250)
    whenever any of the in-process world writers (collateral/venue heartbeat 2 s,
    market-channel ingestor, wrap reconciler 30 s, user-channel reconcile 60 s)
    overlapped a 250 ms window. The downgrade is now SCOPED: saved, applied for
    this single WAL read only, and restored in ``finally`` — a read helper's
    defensive timeout must never leak into the shared connection's WRITE path.
    """
    saved_busy_timeout_ms: int | None = None
    try:
        try:
            row = world_conn.execute("PRAGMA busy_timeout").fetchone()
            saved_busy_timeout_ms = int(row[0]) if row is not None else None
            world_conn.execute("PRAGMA busy_timeout = 250")
        except Exception:  # noqa: BLE001
            saved_busy_timeout_ms = None
        event_type_values = tuple(str(t).strip() for t in event_types if str(t).strip())
        if not event_type_values:
            return set()
        placeholders = ",".join("?" for _ in event_type_values)
        try:
            rows = world_conn.execute(
                f"""
                SELECT DISTINCT e.entity_key
                FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
                JOIN opportunity_events e ON e.event_id = p.event_id
                WHERE p.consumer_name = 'edli_reactor_v1'
                  AND p.processing_status IN ('pending', 'processing', 'claimed')
                  AND e.event_type IN ({placeholders})
            """,
                event_type_values,
            ).fetchall()
        except Exception:  # noqa: BLE001 — fail-open: no skip set (cap still bounds)
            return set()
        return {str(r[0]) for r in rows}
    finally:
        if saved_busy_timeout_ms is not None:
            try:
                world_conn.execute("PRAGMA busy_timeout = %d" % saved_busy_timeout_ms)
            except Exception:  # noqa: BLE001 — restore best-effort; next get_world_connection reapplies
                pass


def _edli_redecision_family_keys_from_entity_keys(
    entity_keys: set[str],
) -> set[tuple[str, str, str]]:
    """Extract (city, target_date, metric) keys from pending redecision entity keys."""

    out: set[tuple[str, str, str]] = set()
    for entity_key in entity_keys or set():
        parts = str(entity_key or "").split("|")
        if len(parts) < 3:
            continue
        city = parts[0].strip()
        target_date = parts[1].strip()
        metric = parts[2].strip()
        if city and target_date and metric in {"high", "low"}:
            out.add((city, target_date, metric))
    return out


_EDLI_LAST_PRUNE_MONOTONIC: float | None = None


def _edli_prune_batch_limit(config: dict) -> int:
    return _edli_bounded_positive_int(
        config,
        "reactor_prune_batch_limit",
        default=5_000,
        maximum=5_000,
    )


def _edli_prune_interval_seconds(config: dict) -> float:
    raw = config.get("reactor_prune_interval_seconds", 60)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 180.0
    return max(0.0, value)


def _edli_prune_budget_seconds(config: dict) -> float:
    raw = config.get("reactor_prune_budget_seconds", 6.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 6.0
    return max(0.0, min(value, 20.0))


def _edli_forecast_snapshot_build_budget_seconds(config: dict) -> float:
    raw = config.get("reactor_forecast_snapshot_build_budget_seconds", 8.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 8.0
    return max(0.0, min(value, 20.0))


def _edli_day0_emit_budget_seconds(config: dict) -> float:
    raw = config.get("reactor_day0_emit_budget_seconds", 8.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 8.0
    return max(0.0, min(value, 20.0))


def _edli_day0_emit_busy_timeout_ms(config: dict) -> int:
    raw = config.get("reactor_day0_emit_busy_timeout_ms", 750)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 750
    return max(1, min(value, 5_000))


def _edli_install_sqlite_deadline(conn, *, deadline_monotonic: float | None) -> None:
    """Interrupt long SQLite reads/writes once the caller's wall-clock budget is spent."""

    if deadline_monotonic is None:
        return

    def _deadline_progress() -> int:
        return 1 if time.monotonic() >= deadline_monotonic else 0

    conn.set_progress_handler(_deadline_progress, 1_000)


def _edli_clear_sqlite_progress_handler(conn) -> None:
    try:
        conn.set_progress_handler(None, 0)
    except Exception:  # noqa: BLE001
        pass


def _edli_sqlite_busy_timeout_ms(conn) -> int | None:
    try:
        row = conn.execute("PRAGMA busy_timeout").fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _edli_set_sqlite_busy_timeout_ms(conn, value: int | None) -> None:
    if value is None:
        return
    try:
        conn.execute("PRAGMA busy_timeout = %d" % max(1, int(value)))
    except Exception:  # noqa: BLE001
        pass


def _edli_prune_lock_timeout_seconds(config: dict) -> float:
    raw = config.get("reactor_prune_lock_timeout_seconds", 0.5)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.5
    return max(0.0, min(value, 5.0))


def _edli_prune_busy_timeout_ms(config: dict) -> int:
    raw = config.get("reactor_prune_busy_timeout_ms", 750)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 750
    return max(1, min(value, 5_000))


def _edli_unready_fsr_prune_min_active_pending(config: dict) -> int:
    raw = config.get("reactor_unready_fsr_prune_min_active_pending", 1_000)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 1_000
    return max(1, min(value, 50_000))


def _edli_active_rmf_forecast_snapshot_pending_count(world_conn, *, limit: int) -> int:
    """Count active replacement FSR rows up to ``limit`` for maintenance gating.

    The spine-readiness sweep opens the forecasts DB and cross-checks live
    posteriors/raw members. That is valuable backlog hygiene when the FSR queue
    is large, but it is not a per-cycle decision prerequisite for a tiny active
    set. Keep the gate on the world queue only so small live queues can keep
    reaching candidate evaluation even when forecast ingestion is busy.
    """

    bounded_limit = max(1, min(int(limit or 1), 50_000))
    try:
        row = world_conn.execute(
            """
            SELECT COUNT(*)
              FROM (
                    SELECT 1
                      FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
                      JOIN opportunity_events e ON e.event_id = p.event_id
                     WHERE p.consumer_name = 'edli_reactor_v1'
                       AND p.processing_status = 'pending'
                       AND e.event_type = 'FORECAST_SNAPSHOT_READY'
                       AND e.causal_snapshot_id LIKE 'rmf-%'
                     LIMIT ?
                   )
            """,
            (bounded_limit,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return bounded_limit
    return int(row[0] or 0) if row is not None else 0


def _edli_prune_pending_working_set(
    store,
    *,
    decision_time: datetime,
    day0_family_admission: _Day0LiveFamilyAdmission | None = None,
) -> None:
    """Prune stale/superseded rows before snapshotting the redecision skip set.

    Backlog pruning is maintenance, not trade decision logic. Keep it explicit
    opt-in so a slow sweep cannot pin the reactor worker and stop live candidate
    evaluation.
    """

    global _EDLI_LAST_PRUNE_MONOTONIC
    edli_cfg = _settings_section("edli", {})
    # ANTIBODY (2026-06-08, operator directive): the working-set prune is NON-OPTIONAL.
    # It is the ONLY drain of the pending opportunity_event_processing set (archive_expired_
    # candidates + archive_superseded_channel_events). Gating it behind an off-able flag
    # (reactor_prune_enabled, default off) is exactly what let the working set grow unbounded
    # when the flag was off — slowing fetch_pending and (before the market_discovery
    # decoupling) silently collapsing executable-substrate coverage -> zero trades, with
    # nothing connecting cause to effect. A necessary maintenance sweep must not be silently
    # switchable off. It now ALWAYS runs, bounded only by its own interval/batch limits below;
    # the legacy reactor_prune_enabled flag is ignored.
    interval_s = _edli_prune_interval_seconds(edli_cfg)
    now_mono = time.monotonic()
    if (
        interval_s > 0
        and _EDLI_LAST_PRUNE_MONOTONIC is not None
        and now_mono - _EDLI_LAST_PRUNE_MONOTONIC < interval_s
    ):
        return
    _EDLI_LAST_PRUNE_MONOTONIC = now_mono
    batch_limit = _edli_prune_batch_limit(edli_cfg)
    budget_s = _edli_prune_budget_seconds(edli_cfg)
    prune_started = time.monotonic()
    saved_busy_timeout_ms: int | None = None

    try:
        row = store.conn.execute("PRAGMA busy_timeout").fetchone()
        saved_busy_timeout_ms = int(row[0]) if row is not None else None
        store.conn.execute("PRAGMA busy_timeout = %d" % _edli_prune_busy_timeout_ms(edli_cfg))
    except Exception:  # noqa: BLE001
        saved_busy_timeout_ms = None

    def _log_prune_step(step: str, started: float, count: int | None = None) -> None:
        elapsed = time.monotonic() - started
        if elapsed >= 1.0:
            count_suffix = "" if count is None else f" count={count}"
            logger.info("EDLI reactor prune step completed: %s elapsed_s=%.3f%s", step, elapsed, count_suffix)

    def _restore_busy_timeout() -> None:
        nonlocal saved_busy_timeout_ms
        if saved_busy_timeout_ms is None:
            return
        try:
            store.conn.execute("PRAGMA busy_timeout = %d" % saved_busy_timeout_ms)
        except Exception:  # noqa: BLE001
            pass
        saved_busy_timeout_ms = None

    prune_deadline = (prune_started + budget_s) if budget_s > 0 else None
    _edli_install_sqlite_deadline(store.conn, deadline_monotonic=prune_deadline)

    def _budget_exhausted(next_step: str) -> bool:
        if budget_s <= 0:
            return False
        elapsed = time.monotonic() - prune_started
        if elapsed < budget_s:
            return False
        logger.warning(
            "EDLI reactor prune budget exhausted before %s elapsed_s=%.3f budget_s=%.3f; "
            "deferring remaining maintenance so the money-path reactor can drain events.",
            next_step,
            elapsed,
            budget_s,
        )
        _restore_busy_timeout()
        return True

    try:
        if _budget_exhausted("archive_orphan_processing_rows"):
            return
        _step_started = time.monotonic()
        _orphan_archived = store.archive_orphan_processing_rows(batch_limit=batch_limit)
        _log_prune_step("archive_orphan_processing_rows", _step_started, _orphan_archived)
        if _orphan_archived:
            logger.info(
                "EDLI reactor: archived %d orphan opportunity_event_processing rows "
                "(missing opportunity_events provenance) → 'expired'; active working "
                "set no longer includes unclaimable IDs (batch_limit=%d)",
                _orphan_archived,
                batch_limit,
            )
    except Exception as _orphan_sweep_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: archive_orphan_processing_rows sweep failed "
            "(non-fatal; joined readers still ignore orphan IDs): %r",
            _orphan_sweep_exc,
        )

    try:
        if _budget_exhausted("archive_expired_candidates"):
            return
        _step_started = time.monotonic()
        _archived = store.archive_expired_candidates(
            decision_time=decision_time.isoformat(),
            batch_limit=batch_limit,
        )
        _log_prune_step("archive_expired_candidates", _step_started, _archived)
        if _archived:
            logger.info(
                "EDLI reactor: archived %d expired (target-local-day-ended) "
                "candidates → 'expired' (excluded from future scans; batch_limit=%d)",
                _archived,
                batch_limit,
            )
    except Exception as _sweep_exc:  # noqa: BLE001 — fail-soft; read floor still guards
        logger.warning(
            "EDLI reactor: archive_expired_candidates sweep failed (non-fatal; "
            "fetch_pending read floor still drops strictly-past rows): %r",
            _sweep_exc,
    )

    try:
        if _budget_exhausted("archive_unmarketed_day0_events"):
            return
        _step_started = time.monotonic()
        _d0_unmarketed_archived = 0
        if day0_family_admission is not None and day0_family_admission.expiry_safe:
            _d0_unmarketed_archived = store.archive_unmarketed_day0_events(
                admitted_families=set(day0_family_admission.admitted_families),
                normalizer=_substrate_refresh_family_key,
                batch_limit=batch_limit,
            )
        _log_prune_step("archive_unmarketed_day0_events", _step_started, _d0_unmarketed_archived)
        if _d0_unmarketed_archived:
            logger.info(
                "EDLI reactor: expired %d unmarketed DAY0_EXTREME_UPDATED execution "
                "events with no market topology or live exposure; observation provenance "
                "kept, but non-executable Day0 facts no longer claim money-path budget",
                _d0_unmarketed_archived,
            )
    except Exception as _d0_unmarketed_sweep_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: archive_unmarketed_day0_events sweep failed (non-fatal): %r",
            _d0_unmarketed_sweep_exc,
        )

    try:
        if _budget_exhausted("repair_missing_processing_rows"):
            return
        _step_started = time.monotonic()
        _processing_repaired = store.repair_missing_processing_rows(
            decision_time=decision_time.isoformat(),
            batch_limit=min(batch_limit, 1000),
        )
        _log_prune_step("repair_missing_processing_rows", _step_started, _processing_repaired)
        if _processing_repaired:
            logger.warning(
                "EDLI reactor: repaired %d decision events missing processing rows "
                "so fetch_pending can claim them",
                _processing_repaired,
            )
    except Exception as _missing_processing_repair_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: missing processing-row repair sweep failed "
            "(non-fatal; normal pending events still drain): %r",
            _missing_processing_repair_exc,
        )

    try:
        if _budget_exhausted("requeue_misclassified_local_pre_submit_rejections"):
            return
        _step_started = time.monotonic()
        _recovered = store.requeue_misclassified_local_pre_submit_rejections(
            batch_limit=min(batch_limit, 1000),
        )
        _log_prune_step("requeue_misclassified_local_pre_submit_rejections", _step_started, _recovered)
        if _recovered:
            logger.warning(
                "EDLI reactor: requeued %d processed events that old executor-boundary "
                "code misclassified as venue rejects for local entries_paused pre-submit blocks",
                _recovered,
            )
    except Exception as _pre_submit_recovery_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: local pre-submit rejection recovery sweep failed "
            "(non-fatal; normal pending events still drain): %r",
            _pre_submit_recovery_exc,
        )

    try:
        if _budget_exhausted("requeue_processed_day0_entries_paused"):
            return
        _step_started = time.monotonic()
        try:
            from src.control.control_plane import is_entries_paused as _entries_paused_now

            _entries_paused_currently = bool(_entries_paused_now())
        except Exception as _pause_read_exc:  # noqa: BLE001
            logger.warning(
                "EDLI reactor: entries_paused state unavailable during Day0 pause "
                "requeue sweep; skipping recovery this cycle: %r",
                _pause_read_exc,
            )
            _entries_paused_currently = True
        _day0_pause_recovered = 0
        if not _entries_paused_currently:
            _day0_pause_recovered = store.requeue_processed_day0_entries_paused(
                decision_time=decision_time.isoformat(),
                batch_limit=min(batch_limit, 1000),
            )
        _log_prune_step("requeue_processed_day0_entries_paused", _step_started, _day0_pause_recovered)
        if _day0_pause_recovered:
            logger.warning(
                "EDLI reactor: requeued %d DAY0 events whose latest verdict was "
                "entries_paused/pause_entries; same observation facts will re-decide "
                "after the pause cleared",
                _day0_pause_recovered,
            )
    except Exception as _day0_pause_recovery_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: processed Day0 entries_paused recovery sweep failed "
            "(non-fatal; normal pending events still drain): %r",
            _day0_pause_recovery_exc,
        )

    try:
        if _budget_exhausted("requeue_false_static_venue_close_day0_dead_letters"):
            return
        _step_started = time.monotonic()
        _static_close_recovered = store.requeue_false_static_venue_close_day0_dead_letters(
            decision_time=decision_time.isoformat(),
            batch_limit=min(batch_limit, 1000),
        )
        _log_prune_step(
            "requeue_false_static_venue_close_day0_dead_letters",
            _step_started,
            _static_close_recovered,
        )
        if _static_close_recovered:
            logger.warning(
                "EDLI reactor: requeued %d DAY0 events falsely dead-lettered by "
                "old static F1 venue-close horizon",
                _static_close_recovered,
            )
    except Exception as _static_close_recovery_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: false static venue-close DAY0 recovery sweep failed "
            "(non-fatal; normal pending events still drain): %r",
            _static_close_recovery_exc,
        )

    try:
        if _budget_exhausted("requeue_false_executable_snapshot_deadline_day0_dead_letters"):
            return
        _step_started = time.monotonic()
        _snapshot_deadline_recovered = (
            store.requeue_false_executable_snapshot_deadline_day0_dead_letters(
                decision_time=decision_time.isoformat(),
                batch_limit=min(batch_limit, 1000),
            )
        )
        _log_prune_step(
            "requeue_false_executable_snapshot_deadline_day0_dead_letters",
            _step_started,
            _snapshot_deadline_recovered,
        )
        if _snapshot_deadline_recovered:
            logger.warning(
                "EDLI reactor: requeued %d DAY0 events falsely dead-lettered by "
                "old executable-snapshot selection-deadline horizon logic",
                _snapshot_deadline_recovered,
            )
    except Exception as _snapshot_deadline_recovery_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: false executable-snapshot selection-deadline DAY0 "
            "recovery sweep failed (non-fatal; normal pending events still drain): %r",
            _snapshot_deadline_recovery_exc,
        )

    try:
        if _budget_exhausted("archive_superseded_channel_events"):
            return
        _step_started = time.monotonic()
        _ch_archived = store.archive_superseded_channel_events(batch_limit=batch_limit)
        _log_prune_step("archive_superseded_channel_events", _step_started, _ch_archived)
        if _ch_archived:
            logger.info(
                "EDLI reactor: archived %d superseded channel events "
                "(BEST_BID_ASK_CHANGED/BOOK_SNAPSHOT/NEW_MARKET_DISCOVERED) → "
                "'expired'; pending channel-event scan reduced (batch_limit=%d)",
                _ch_archived,
                batch_limit,
            )
    except Exception as _ch_sweep_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: archive_superseded_channel_events sweep failed "
            "(non-fatal): %r",
            _ch_sweep_exc,
        )

    # DAY0 supersession (2026-06-15): keep only the latest DAY0_EXTREME_UPDATED per
    # (city, target_date, metric). Day0 was in NEITHER drain sweep, so stale duplicates
    # (measured 1972 pending rows / 152 families) piled up at Tier-0 claim priority and
    # starved the tradeable FORECAST_SNAPSHOT_READY (spine) lane to zero decisions.
    # Past-local-day day0 is handled by archive_expired_candidates (now day0-aware).
    try:
        if _budget_exhausted("archive_superseded_day0_events"):
            return
        _step_started = time.monotonic()
        _d0_archived = store.archive_superseded_day0_events(batch_limit=batch_limit)
        _log_prune_step("archive_superseded_day0_events", _step_started, _d0_archived)
        if _d0_archived:
            logger.info(
                "EDLI reactor: archived %d superseded DAY0_EXTREME_UPDATED events "
                "(keep-latest per city/target_date/metric) → 'expired'; Tier-0 day0 "
                "claim backlog drained so tradeable FSR is no longer starved "
                "(batch_limit=%d)",
                _d0_archived,
                batch_limit,
            )
    except Exception as _d0_sweep_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: archive_superseded_day0_events sweep failed (non-fatal): %r",
            _d0_sweep_exc,
    )

    try:
        if _budget_exhausted("expire_unready_forecast_snapshot_pending"):
            return
        _step_started = time.monotonic()
        _unready_fsr_min_active = _edli_unready_fsr_prune_min_active_pending(edli_cfg)
        _active_rmf_fsr_pending = _edli_active_rmf_forecast_snapshot_pending_count(
            store.conn,
            limit=_unready_fsr_min_active,
        )
        _unready_fsr_archived = 0
        if _active_rmf_fsr_pending >= _unready_fsr_min_active:
            from src.state.db import get_forecasts_connection_read_only as _get_forecasts_ro

            _forecasts_ro = _get_forecasts_ro()
            try:
                _unready_fsr_archived = _edli_expire_unready_forecast_snapshot_pending(
                    store.conn,
                    _forecasts_ro,
                    decision_time=decision_time.astimezone(timezone.utc).isoformat(),
                )
            finally:
                _forecasts_ro.close()
            _log_prune_step(
                "expire_unready_forecast_snapshot_pending",
                _step_started,
                _unready_fsr_archived,
            )
            if _unready_fsr_archived:
                logger.info(
                    "EDLI reactor: expired %d forecast-snapshot pending rows whose latest "
                    "posterior lacks same-cycle raw-model spine members; reactor will not "
                    "spend proof budget on MU_SIGMA_NOT_STASHED candidates",
                    _unready_fsr_archived,
                )
    except Exception as _spine_ready_sweep_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: replacement FSR spine-readiness sweep failed (non-fatal): %r",
            _spine_ready_sweep_exc,
    )

    try:
        if _budget_exhausted("archive_recent_no_value_refuted_events"):
            return
        _step_started = time.monotonic()
        _no_value_refuted_archived = store.archive_recent_no_value_refuted_events(
            decision_time=decision_time.astimezone(timezone.utc).isoformat(),
            batch_limit=batch_limit,
        )
        _log_prune_step("archive_recent_no_value_refuted_events", _step_started, _no_value_refuted_archived)
        if _no_value_refuted_archived:
            logger.info(
                "EDLI reactor: expired %d already-queued FSR/Day0 events refuted by "
                "same-evidence terminal no-trade receipts; reactor proof budget no "
                "longer replays known no-value families (batch_limit=%d)",
                _no_value_refuted_archived,
                batch_limit,
            )
    except Exception as _no_value_refutation_sweep_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: recent no-value refutation sweep failed (non-fatal): %r",
            _no_value_refutation_sweep_exc,
    )

    try:
        if _budget_exhausted("ignore_channel_cache_events"):
            return
        _step_started = time.monotonic()
        _ch_ignored = store.ignore_channel_cache_events(batch_limit=batch_limit)
        _log_prune_step("ignore_channel_cache_events", _step_started, _ch_ignored)
        if _ch_ignored:
            logger.info(
                "EDLI reactor: ignored %d channel cache events "
                "(BEST_BID_ASK_CHANGED/BOOK_SNAPSHOT/NEW_MARKET_DISCOVERED) after "
                "quote-cache/feasibility ingestion; excluded from submit reactor "
                "working set (batch_limit=%d)",
                _ch_ignored,
                batch_limit,
            )
    except Exception as _ch_ignore_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: ignore_channel_cache_events sweep failed "
            "(non-fatal): %r",
            _ch_ignore_exc,
    )

    try:
        if _budget_exhausted("archive_invalid_forecast_snapshot_events"):
            return
        _step_started = time.monotonic()
        _invalid_fsr_archived = store.archive_invalid_forecast_snapshot_events(
            batch_limit=batch_limit,
        )
        _log_prune_step("archive_invalid_forecast_snapshot_events", _step_started, _invalid_fsr_archived)
        if _invalid_fsr_archived:
            logger.info(
                "EDLI reactor: archived %d invalid forecast-snapshot/redecision "
                "events with impossible carrier counts → 'expired'; malformed live "
                "carriers removed from active decision working set (batch_limit=%d)",
                _invalid_fsr_archived,
                batch_limit,
            )
    except Exception as _invalid_fsr_sweep_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: archive_invalid_forecast_snapshot_events sweep failed "
            "(non-fatal): %r",
            _invalid_fsr_sweep_exc,
    )

    try:
        if _budget_exhausted("archive_superseded_forecast_snapshot_events"):
            return
        _step_started = time.monotonic()
        _fsr_archived = store.archive_superseded_forecast_snapshot_events(
            batch_limit=batch_limit,
        )
        _log_prune_step("archive_superseded_forecast_snapshot_events", _step_started, _fsr_archived)
        if _fsr_archived:
            logger.info(
                "EDLI reactor: archived %d superseded forecast-snapshot redecision "
                "events → 'expired'; newest active event per forecast family retained "
                "(batch_limit=%d)",
                _fsr_archived,
                batch_limit,
            )
    except Exception as _fsr_sweep_exc:  # noqa: BLE001 — fail-soft
        logger.warning(
            "EDLI reactor: archive_superseded_forecast_snapshot_events sweep failed "
            "(non-fatal): %r",
            _fsr_sweep_exc,
        )
    finally:
        _edli_clear_sqlite_progress_handler(store.conn)
        _restore_busy_timeout()


def _edli_day0_fast_lane_enabled() -> bool:
    try:
        edli_cfg = settings.get("edli", {}) if hasattr(settings, "get") else settings["edli"]
        return bool(edli_cfg.get("day0_fast_obs_lane_enabled", True))
    except Exception:
        return True


def _edli_prefetch_day0_fast_obs(*, decision_time: datetime):
    """HTTP PHASE of the day0 fast lane (PR#404 operator review P0-2).

    Runs OUTSIDE the world-write mutex: METAR batch fetch, WU anomaly
    cross-check, and the open-meteo hourly-vector refresh (which writes the
    FORECASTS db under its own writer lock — never the world WAL). Returns a
    pure in-memory FastObsPrefetch (or None) for the mutex-held write phase.
    decision_time is captured HERE so event identity uses the prefetch clock,
    not a lock-held clock. Fail-soft: any error returns None.
    """
    if not _edli_day0_fast_lane_enabled():
        return None
    prefetch = None
    try:
        from src.config import runtime_cities
        from src.data.day0_fast_obs import get_fast_obs_emitter
        from src.data.day0_oracle_anomaly import wu_metar_anomaly_action

        prefetch = get_fast_obs_emitter().prefetch(
            cities=runtime_cities(),
            decision_time=decision_time,
            anomaly_check=wu_metar_anomaly_action,
        )
    except Exception as _fast_exc:  # noqa: BLE001 — fast lane is additive
        logger.warning(
            "EDLI day0 fast obs prefetch failed (non-fatal, catch-up lanes continue): %r",
            _fast_exc,
        )
    return prefetch


def _edli_day0_hourly_priority_families() -> list[tuple[str, str, str]]:
    """Money-path families that should drive Day0 hourly-vector refresh order."""

    families: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(raw: Iterable[tuple[object, object, object]]) -> None:
        for city, target_date, metric in raw or ():
            key = _substrate_refresh_family_key(city, target_date, metric)
            if key and all(key) and key not in seen:
                seen.add(key)
                families.append(key)

    try:
        world_ro = get_world_connection_read_only()
        try:
            rows = _pending_family_rows_for_refresh(
                world_ro,
                consumer_name="edli_reactor_v1",
                event_window_limit=int(os.environ.get("ZEUS_DAY0_HOURLY_PRIORITY_EVENT_WINDOW_LIMIT", "2000")),
            )
        finally:
            world_ro.close()
        add(
            (
                row[0],
                row[1],
                row[2],
            )
            for row in rows
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("edli_day0_hourly_refresh: pending-family priority read failed: %s", exc)

    try:
        from src.state.db import get_trade_connection_read_only

        trade_ro = get_trade_connection_read_only()
        try:
            add(_open_rest_family_rows_for_refresh(trade_ro))
        finally:
            trade_ro.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("edli_day0_hourly_refresh: open-rest priority read failed: %s", exc)

    add(sorted(_edli_current_held_position_family_keys()))
    return families


_DAY0_HOURLY_REFRESH_CURSOR = 0


def _rotate_day0_refresh_segment(items: list[Any], cursor: int) -> list[Any]:
    if not items:
        return []
    offset = int(cursor) % len(items)
    return items[offset:] + items[:offset]


def _edli_rotate_day0_hourly_refresh_order(
    ordered: list[Any],
    *,
    priority_city_count: int,
    cursor: int,
) -> list[Any]:
    priority = ordered[: max(0, int(priority_city_count))]
    rest = ordered[max(0, int(priority_city_count)) :]
    return (
        _rotate_day0_refresh_segment(priority, cursor)
        + _rotate_day0_refresh_segment(rest, cursor)
    )


def _edli_order_day0_hourly_refresh_cities(
    cities: list[Any],
    *,
    decision_time: datetime,
    priority_families: Iterable[tuple[str, str, str]],
) -> tuple[list[Any], int]:
    """Put same-local-day money-path cities before the static universe sweep."""

    by_name_key = {
        _substrate_refresh_family_text_key(getattr(city, "name", "")): city
        for city in cities
        if str(getattr(city, "name", "") or "").strip()
    }
    priority_city_keys: list[str] = []
    seen_priority: set[str] = set()
    for city_name, target_date, metric in priority_families or ():
        if metric not in {"high", "low"}:
            continue
        city = by_name_key.get(_substrate_refresh_family_text_key(city_name))
        if city is None:
            continue
        try:
            local_date = decision_time.astimezone(ZoneInfo(str(getattr(city, "timezone")))).date().isoformat()
        except Exception:  # noqa: BLE001
            continue
        if str(target_date or "").strip() != local_date:
            continue
        key = _substrate_refresh_family_text_key(getattr(city, "name", ""))
        if key and key not in seen_priority:
            seen_priority.add(key)
            priority_city_keys.append(key)

    ordered: list[Any] = []
    emitted: set[str] = set()
    for key in priority_city_keys:
        city = by_name_key.get(key)
        if city is not None:
            ordered.append(city)
            emitted.add(key)
    for city in cities:
        key = _substrate_refresh_family_text_key(getattr(city, "name", ""))
        if key not in emitted:
            ordered.append(city)
            emitted.add(key)
    return ordered, len(priority_city_keys)


@_scheduler_job("edli_day0_hourly_refresh")
def _edli_day0_hourly_refresh_cycle() -> None:
    """Refresh Day0 high-resolution hourly vectors off the trading reactor cadence.

    These vectors improve remaining-day Day0 pricing, but fetching Open-Meteo
    and writing ``zeus-forecasts.db`` must not pin the live event reactor. The
    reactor consumes whatever is already fresh; this side job opportunistically
    refreshes the carrier and yields whenever the trading reactor/redecision
    lane is active.
    """

    global _DAY0_HOURLY_REFRESH_CURSOR

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    try:
        from src.config import runtime_cities as _rc
        from src.data.day0_hourly_vectors import maybe_refresh_day0_hourly_vectors

        decision_time = datetime.now(timezone.utc)
        priority_families = _edli_day0_hourly_priority_families()
        ordered_cities, priority_city_count = _edli_order_day0_hourly_refresh_cities(
            _rc(),
            decision_time=decision_time,
            priority_families=priority_families,
        )
        ordered_cities = _edli_rotate_day0_hourly_refresh_order(
            ordered_cities,
            priority_city_count=priority_city_count,
            cursor=_DAY0_HOURLY_REFRESH_CURSOR,
        )
        trading_lane_active = _edli_reactor_active() or _edli_redecision_screen_lock.locked()
        if trading_lane_active and priority_city_count <= 0:
            logger.info("edli_day0_hourly_refresh deferred: trading reactor/redecision lane active")
            return
        if trading_lane_active:
            logger.info(
                "edli_day0_hourly_refresh: priority refresh proceeding while trading lane active "
                "priority_cities=%d",
                priority_city_count,
            )
        configured_max_cities = int(os.environ.get("ZEUS_DAY0_HOURLY_REFRESH_MAX_CITIES", "3"))
        max_cities = max(configured_max_cities, priority_city_count)
        stats = maybe_refresh_day0_hourly_vectors(
            ordered_cities,
            decision_time=decision_time,
            budget_s=float(os.environ.get("ZEUS_DAY0_HOURLY_REFRESH_BUDGET_SECONDS", "6.0")),
            max_cities=max_cities,
            timeout_s=float(os.environ.get("ZEUS_DAY0_HOURLY_FETCH_TIMEOUT_SECONDS", "4.0")),
            persist_lock_blocking=False,
            return_stats=True,
        )
        vectors_written = int(getattr(stats, "vectors_written", stats))
        cities_attempted = int(getattr(stats, "cities_attempted", 0) or 0)
        if cities_attempted > 0 and ordered_cities:
            _DAY0_HOURLY_REFRESH_CURSOR = (
                _DAY0_HOURLY_REFRESH_CURSOR + cities_attempted
            ) % max(1, len(ordered_cities))
        if vectors_written or priority_city_count:
            logger.info(
                "edli_day0_hourly_refresh: vectors_written=%d priority_cities=%d "
                "max_cities=%d cities_attempted=%d skipped_throttle=%d "
                "incomplete_expected_bundles=%d budget_exhausted=%s cursor=%d",
                vectors_written,
                priority_city_count,
                max_cities,
                cities_attempted,
                int(getattr(stats, "cities_skipped_throttle", 0) or 0),
                int(getattr(stats, "incomplete_expected_bundles", 0) or 0),
                bool(getattr(stats, "budget_exhausted", False)),
                _DAY0_HOURLY_REFRESH_CURSOR,
            )
    except Exception as _vec_exc:  # noqa: BLE001 — additive lane, fail-soft
        logger.warning("EDLI day0 hourly-vector refresh failed (non-fatal): %r", _vec_exc)


def _edli_emit_day0_extreme_events(
    world_conn,
    trade_conn,
    *,
    decision_time: datetime,
    received_at: str,
    limit: int,
    fast_prefetch=None,
    day0_is_tradeable: bool = True,
    budget_seconds: float | None = None,
    family_admission: _Day0LiveFamilyAdmission | None = None,
) -> int:
    """Emit EDLI Day0 extreme events from live observation truth surfaces.

    WRITE PHASE ONLY (PR#404 P0-2): performs NO network IO — safe to call
    while holding the world-write mutex. The fast lane's HTTP results arrive
    via ``fast_prefetch`` (built by _edli_prefetch_day0_fast_obs OUTSIDE the
    mutex); the catch-up scanners below are DB-only by construction.

    Returns the TOTAL emitted across all three lanes (PR#404 P2: the fast-lane
    count was previously dropped from the return value).
    """

    from src.events.event_writer import EventWriter
    from src.events.triggers.day0_extreme_updated import Day0ExtremeUpdatedTrigger

    deadline_monotonic = (
        time.monotonic() + float(budget_seconds)
        if budget_seconds is not None and float(budget_seconds) > 0
        else None
    )
    edli_cfg = _settings_section("edli", {})
    day0_busy_timeout_ms = _edli_day0_emit_busy_timeout_ms(edli_cfg)
    saved_world_busy_timeout_ms = _edli_sqlite_busy_timeout_ms(world_conn)
    saved_trade_busy_timeout_ms = _edli_sqlite_busy_timeout_ms(trade_conn)
    _edli_set_sqlite_busy_timeout_ms(world_conn, day0_busy_timeout_ms)
    _edli_set_sqlite_busy_timeout_ms(trade_conn, day0_busy_timeout_ms)
    _edli_install_sqlite_deadline(world_conn, deadline_monotonic=deadline_monotonic)
    _edli_install_sqlite_deadline(trade_conn, deadline_monotonic=deadline_monotonic)
    fast_emitted = 0
    try:
        if fast_prefetch is not None:
            try:
                from src.data.day0_fast_obs import get_fast_obs_emitter

                fast_emitted = get_fast_obs_emitter().emit_prefetched(
                    world_conn=world_conn,
                    prefetch=fast_prefetch,
                    received_at=received_at,
                    limit=limit,
                    day0_is_tradeable=day0_is_tradeable,
                    family_admission=family_admission,
                )
            except Exception as _fast_exc:  # noqa: BLE001 — fast lane is additive; never block catch-up
                logger.warning(
                    "EDLI day0 fast obs emit failed (non-fatal, catch-up lanes continue): %r",
                    _fast_exc,
                )

        trigger = Day0ExtremeUpdatedTrigger(
            EventWriter(world_conn),
            day0_is_tradeable=day0_is_tradeable,
            suppress_recent_no_value_refutations=True,
            family_admission=family_admission,
        )
        authority_results, observation_results = _edli_scan_day0_with_lock_retry(
            trigger=trigger,
            world_conn=world_conn,
            trade_conn=trade_conn,
            decision_time=decision_time,
            received_at=received_at,
            limit=limit,
        )
        # Structured per-lane counters (PR#404 P2 observability fix).
        logger.info(
            "EDLI day0 emit: day0_fast_emitted=%d day0_authority_emitted=%d "
            "day0_observation_instants_emitted=%d admitted_families=%d",
            fast_emitted,
            len(authority_results),
            len(observation_results),
            0 if family_admission is None else len(family_admission.admitted_families),
        )
        return fast_emitted + len(authority_results) + len(observation_results)
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            logger.warning(
                "EDLI day0 emit budget exhausted after %.3fs; skipping remaining "
                "Day0 catch-up this cycle and draining already-queued candidates.",
                float(budget_seconds or 0.0),
            )
            return fast_emitted
        raise
    finally:
        _edli_clear_sqlite_progress_handler(trade_conn)
        _edli_clear_sqlite_progress_handler(world_conn)
        _edli_set_sqlite_busy_timeout_ms(trade_conn, saved_trade_busy_timeout_ms)
        _edli_set_sqlite_busy_timeout_ms(world_conn, saved_world_busy_timeout_ms)


def _edli_scan_day0_with_lock_retry(
    *,
    trigger,
    world_conn,
    trade_conn,
    decision_time: datetime,
    received_at: str,
    limit: int,
) -> tuple[list, list]:
    import sqlite3

    retry_delays = _edli_day0_emit_lock_retry_delays()
    for attempt in range(1, len(retry_delays) + 2):
        try:
            authority_results = trigger.scan_authority_rows(
                observation_conn=trade_conn,
                settlement_semantics=_edli_day0_settlement_semantics,
                decision_time=decision_time,
                received_at=received_at,
                limit=limit,
            )
            observation_results = trigger.scan_observation_instants_rows(
                observation_conn=world_conn,
                settlement_semantics=_edli_day0_settlement_semantics,
                decision_time=decision_time,
                received_at=received_at,
                limit=limit,
            )
            return authority_results, observation_results
        except sqlite3.OperationalError as exc:
            if not _edli_is_sqlite_lock_error(exc) or attempt > len(retry_delays):
                raise
            logger.warning(
                "EDLI day0 emit hit transient world-DB lock; retrying in %.1fs "
                "(attempt %d/%d): %r",
                retry_delays[attempt - 1],
                attempt,
                len(retry_delays) + 1,
                exc,
            )
            time.sleep(retry_delays[attempt - 1])
    raise RuntimeError("unreachable day0 emit retry state")


def _edli_day0_emit_lock_retry_delays() -> tuple[float, ...]:
    raw = os.environ.get("ZEUS_DAY0_EMIT_LOCK_RETRY_SECONDS", "1.0,2.0")
    delays: list[float] = []
    for piece in raw.split(","):
        text = piece.strip()
        if not text:
            continue
        try:
            delay_s = float(text)
        except ValueError:
            continue
        if delay_s > 0:
            delays.append(min(delay_s, 10.0))
    return tuple(delays)


def _edli_is_sqlite_lock_error(exc: Exception) -> bool:
    import sqlite3

    if isinstance(exc, sqlite3.OperationalError):
        lock_codes = {
            getattr(sqlite3, "SQLITE_BUSY", 5),
            getattr(sqlite3, "SQLITE_LOCKED", 6),
        }
        code = getattr(exc, "sqlite_errorcode", None)
        if code is not None and code in lock_codes:
            return True
    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database is busy" in message
    )


def _edli_day0_settlement_semantics(observation: dict):
    """Resolve Day0 settlement semantics from authority payload fields."""

    from src.contracts.settlement_semantics import SettlementSemantics

    station = str(observation.get("station_id") or observation.get("city") or "UNKNOWN")
    unit = str(observation.get("settlement_unit") or "F").upper()
    rounding_rule = str(observation.get("rounding_rule") or "wmo_half_up")
    precision_raw = observation.get("settlement_precision")
    try:
        precision = float(precision_raw) if precision_raw is not None else 1.0
    except (TypeError, ValueError):
        precision = 1.0
    if unit not in {"F", "C"}:
        unit = "F"
    if rounding_rule not in {"wmo_half_up", "floor", "ceil", "oracle_truncate"}:
        rounding_rule = "wmo_half_up"
    return SettlementSemantics(
        resolution_source=f"EDLI_DAY0_{station}",
        measurement_unit=unit,
        precision=precision,
        rounding_rule=rounding_rule,
        finalization_time="12:00:00Z",
    )


def _edli_filter_markets_for_condition(markets: list[dict], condition_id: str | None) -> list[dict]:
    condition = str(condition_id or "").strip()
    if not condition:
        return list(markets)
    filtered = []
    for market in markets:
        if str(market.get("condition_id") or market.get("market_id") or "") == condition:
            filtered.append(market)
            continue
        outcomes = market.get("outcomes", []) or []
        if any(
            str(outcome.get("condition_id") or outcome.get("market_id") or "") == condition
            for outcome in outcomes
            if isinstance(outcome, dict)
        ):
            filtered.append(market)
    return filtered


def _edli_pre_submit_clob_timeout_seconds() -> float:
    raw = os.environ.get("ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS")
    if raw in (None, ""):
        return 6.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS=%r; using 6.0", raw)
        return 6.0
    if value <= 0:
        logger.warning("Invalid ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS=%r; using 6.0", raw)
        return 6.0
    return value


def _edli_pre_submit_inner_io_timeout_seconds() -> float:
    """Network timeout used inside the outer pre-submit timeout guard.

    The outer guard is a daemon-protection circuit breaker.  Inner venue/RPC
    calls must time out first; otherwise the guard returns while the worker
    thread keeps blocking in TLS/SDK I/O and the live reactor eventually skips
    cycles with leaked pre-submit workers.
    """

    outer = _edli_pre_submit_clob_timeout_seconds()
    raw = os.environ.get("ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS")
    if raw not in (None, ""):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS=%r; deriving from outer timeout",
                raw,
            )
        else:
            if value > 0 and (value * 2.0) < outer:
                return value
            logger.warning(
                "Invalid ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS=%r; must be positive and < half outer timeout %.3fs",
                raw,
                outer,
            )
    return max(0.01, min(2.0, outer * 0.35))


def _edli_run_pre_submit_clob_call(label: str, fn, *, seconds: float | None = None):
    from src.runtime.timeout_guard import run_with_timeout

    return run_with_timeout(
        fn,
        seconds=seconds if seconds is not None else _edli_pre_submit_clob_timeout_seconds(),
        label=f"pre_submit_{label}",
    )


def _edli_pre_submit_jit_book_timeout():
    """STRICT connect/read timeout for the submit-time JIT ``/book`` fetch (GATE #84).

    Runs inside the pre-submit guard's worker thread, so it must fail-closed BEFORE
    the outer daemon guard (the 2026-06-19 invariant). httpcore applies the connect
    timeout to ``connect_tcp`` AND ``start_tls`` separately, so the worst-case connect
    cost is ``2*connect``; bound ``2*connect + read + write + pool`` strictly under the
    outer guard. With outer=6.0 this yields connect≈2.25 (worst case 2*2.25+0.85+0.25+
    0.10 = 5.70 < 6.0). This connect budget is a FAIL-CLOSED bound, not the normal
    path — the boot pre-warm + keepalive pinger keep the socket warm so the submit-time
    fetch reuses an established connection (~0.66s, measured forward 2026-06-22) and
    does not pay a cold handshake here. Cold handshakes (~2.2-2.7s) are absorbed by the
    generous warmup timeout OUTSIDE the worker.
    """

    import httpx

    outer = _edli_pre_submit_clob_timeout_seconds()
    # A 0.55s read cap was below observed live /book tail latency and caused the
    # armed submit path to fall back to stale DB feasibility rows. Keep the full
    # worst-case httpcore budget inside the outer guard, but give the warm read
    # enough room to complete on real CLOB tails.
    read, write, pool = 1.75, 0.20, 0.08
    connect = max(0.25, min(1.80, (outer - read - write - pool - 0.25) / 2.0))
    return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)


def _edli_pre_submit_jit_outer_timeout_seconds() -> float:
    raw = os.environ.get("ZEUS_PRE_SUBMIT_JIT_OUTER_TIMEOUT_SECONDS")
    if raw not in (None, ""):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid ZEUS_PRE_SUBMIT_JIT_OUTER_TIMEOUT_SECONDS=%r; using strict default",
                raw,
            )
        else:
            if value > 0:
                return min(value, _edli_pre_submit_clob_timeout_seconds())
    # The submit-time JIT /book read is the primary pre-submit book authority.
    # The former 1.6s cap was below observed live CLOB tail latency and caused
    # armed live cycles to fall back to stale DB rows, globally blocking orders.
    # Keep a small reserve for the post-book provenance/balance checks while
    # still letting a warm public /book request complete under the outer guard.
    outer = _edli_pre_submit_clob_timeout_seconds()
    return max(0.25, min(4.5, outer * 0.85))


def _edli_pre_submit_jit_warmup_timeout():
    """GENEROUS connect timeout for the JIT client's boot pre-warm + keepalive ping.

    Used ONLY by ``_edli_prewarm_pre_submit_jit_client`` / the pinger tick, which run
    OUTSIDE the submit worker, so a connect budget large enough to absorb a cold TLS
    handshake (~2.2-2.7s, with margin) does not threaten the pre-submit outer guard.
    The read budget stays tight (the ``/time`` health probe is tiny).
    """

    import httpx

    return httpx.Timeout(connect=4.5, read=0.75, write=0.25, pool=0.10)


_PRE_SUBMIT_JIT_CLOB_CLIENT = None
_PRE_SUBMIT_JIT_CLOB_CLIENT_LOCK = threading.Lock()


def _edli_reset_pre_submit_jit_clob_client():
    """Drop and close the warm JIT CLOB client (clean shutdown + test isolation)."""

    global _PRE_SUBMIT_JIT_CLOB_CLIENT
    with _PRE_SUBMIT_JIT_CLOB_CLIENT_LOCK:
        client = _PRE_SUBMIT_JIT_CLOB_CLIENT
        _PRE_SUBMIT_JIT_CLOB_CLIENT = None
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001 - best-effort close on shutdown
            pass


def _edli_pre_submit_jit_clob_client():
    """Return a WARM, reused CLOB client for the submit-time JIT ``/book`` fetch.

    Reusing one client keeps its TLS connection warm (httpx keepalive) across
    submit candidates, so each fetch skips the ~2.2-2.7s cold handshake that timed
    out 118/120 submits (warm reuse drops the fetch to ~0.66s, measured forward
    2026-06-22). Thread-safe: httpx.Client is safe to share across the pre-submit
    guard's worker threads; construction is lock-guarded (double-checked).
    """

    global _PRE_SUBMIT_JIT_CLOB_CLIENT
    client = _PRE_SUBMIT_JIT_CLOB_CLIENT
    if client is None:
        with _PRE_SUBMIT_JIT_CLOB_CLIENT_LOCK:
            client = _PRE_SUBMIT_JIT_CLOB_CLIENT
            if client is None:
                from src.data.polymarket_client import (
                    PRESUBMIT_JIT_CLOB_HTTP_LIMITS,
                    PolymarketClient,
                )

                client = PolymarketClient(
                    public_http_timeout=_edli_pre_submit_jit_book_timeout(),
                    public_http_limits=PRESUBMIT_JIT_CLOB_HTTP_LIMITS,
                )
                _PRE_SUBMIT_JIT_CLOB_CLIENT = client
    return client


def _edli_prewarm_pre_submit_jit_client() -> bool:
    """Construct the warm JIT CLOB client and complete a cold TLS handshake OUTSIDE
    the submit worker (boot + keepalive pinger). Uses the generous warmup timeout so
    a slow cold handshake is absorbed here, never on the money path. Fail-soft."""

    try:
        client = _edli_pre_submit_jit_clob_client()
        ok = client.warm_public_connection(timeout=_edli_pre_submit_jit_warmup_timeout())
        return bool(ok)
    except Exception:  # noqa: BLE001 - pre-warm is best-effort; never block boot
        return False


@_scheduler_job("edli_presubmit_jit_keepalive")
def _edli_pre_submit_jit_keepalive_tick() -> None:
    """Keepalive pinger: keep the submit-time JIT CLOB connection warm across reactor
    cycles (keepalive_expiry=90s > 60s cycle) so an edge-positive submit candidate
    never pays a cold TLS handshake at the pre-submit gate. Read-only /time probe;
    touches NO trading state; logs success/failure only (GATE #84, 2026-06-22)."""

    warmed = _edli_prewarm_pre_submit_jit_client()
    if warmed:
        logger.debug("pre-submit JIT keepalive: connection warm")
    else:
        logger.warning("pre-submit JIT keepalive: warm-up probe failed (will retry next tick)")


def _edli_pre_submit_jit_book_quote_provider():
    """Build the just-in-time single-token ``/book`` fetcher for the pre-submit
    authority (GATE #84). Returns a ``token_id -> dict`` callable that pulls the
    live CLOB book for exactly the selected candidate at submit time, or ``None``
    if a CLOB client cannot be constructed (caller then falls back to the DB row).

    Uses a WARM, REUSED client (see ``_edli_pre_submit_jit_clob_client``) so the
    TLS connection stays warm across submit candidates instead of paying a cold
    handshake per call. A failed fetch propagates to the caller, which returns
    ``None`` and falls back/requeues; httpx reopens a fresh pooled connection on
    the next fetch, so a transiently-dead socket costs at most one requeue.
    """

    def _fetch(token_id: str) -> dict:
        clob = _edli_pre_submit_jit_clob_client()
        return _edli_run_pre_submit_clob_call(
            "jit_book",
            lambda: clob.get_orderbook_snapshot(token_id),
            seconds=_edli_pre_submit_jit_outer_timeout_seconds(),
        )

    return _fetch


def _edli_decision_family_snapshot_refresher(topology_conn):
    """Build the decision-triggered targeted executable-substrate refresher.

    The live daemon normally consumes sidecar-produced executable snapshots.  When the
    selected row is already stale inside the money path, waiting for a later sidecar tick
    marks an exact sidecar priority scope.  The order daemon remains a consumer: it
    does not run the substrate producer inline, so it cannot fight the sidecar for
    the shared market_substrate_refresh lock or block the reactor on venue I/O.
    """

    def _refresh(*, city, target_date, metric, condition_ids=(), selected_token_id=None):
        family = (
            str(city or "").strip(),
            str(target_date or "").strip(),
            str(metric or "").strip(),
        )
        if not family[0] or not family[1] or family[2] not in {"high", "low"}:
            return False
        try:
            from src.data.substrate_priority import mark_money_path_substrate_priority

            mark_money_path_substrate_priority(
                reason="decision_triggered_targeted_refresh",
                ttl_seconds=45.0,
                families=[family],
                condition_ids=condition_ids,
                merge_existing=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("decision family refresh: priority marker write failed: %r", exc)
        logger.info(
            "decision family refresh delegated to substrate-observer sidecar: %s/%s/%s",
            family[0],
            family[1],
            family[2],
        )
        return False

    return _refresh


def _edli_reactor_family_snapshot_refresher():
    """Build the reactor-drain substrate nudge.

    The reactor is a live decision consumer, not the executable-substrate producer.
    A transient snapshot block is already requeued in ``opportunity_event_processing``;
    the substrate-observer sidecar reads that pending-family surface and performs
    Gamma/CLOB capture out-of-process. Returning False here preserves honest retry
    accounting without blocking the reactor on producer I/O.
    """

    def _refresh(*, city, target_date, metric, condition_ids=(), **_ignored):
        family = (
            str(city or "").strip(),
            str(target_date or "").strip(),
            str(metric or "").strip(),
        )
        if not family[0] or not family[1] or family[2] not in {"high", "low"}:
            return False
        try:
            from src.data.substrate_priority import mark_money_path_substrate_priority

            mark_money_path_substrate_priority(
                reason="reactor_blocked_family_refresh",
                ttl_seconds=45.0,
                families=[family],
                condition_ids=condition_ids,
                merge_existing=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("reactor family refresh priority marker write failed: %r", exc)
        logger.info(
            "reactor family refresh delegated to substrate-observer sidecar via pending event: %s/%s/%s",
            family[0], family[1], family[2],
        )
        return False

    return _refresh


def _edli_reactor_day0_hourly_refresher():
    """Build the reactor-drain refresher for Day0 remaining-day weather vectors."""

    def _refresh(*, city, target_date, metric, **_ignored):
        family = (
            str(city or "").strip(),
            str(target_date or "").strip(),
            str(metric or "").strip(),
        )
        if not family[0] or not family[1] or family[2] not in {"high", "low"}:
            return False
        try:
            from src.config import runtime_cities_by_name
            from src.data.day0_hourly_vectors import maybe_refresh_day0_hourly_vectors

            city_obj = runtime_cities_by_name().get(family[0])
            if city_obj is None:
                logger.warning(
                    "reactor day0-hourly refresh skipped: city config missing for %s/%s/%s",
                    family[0],
                    family[1],
                    family[2],
                )
                return False
            stats = maybe_refresh_day0_hourly_vectors(
                [city_obj],
                decision_time=datetime.now(timezone.utc),
                interval_s=0.0,
                budget_s=float(os.environ.get("ZEUS_DAY0_HOURLY_REFRESH_BUDGET_SECONDS", "6.0")),
                max_cities=1,
                timeout_s=float(os.environ.get("ZEUS_DAY0_HOURLY_FETCH_TIMEOUT_SECONDS", "4.0")),
                persist_lock_blocking=False,
                return_stats=True,
            )
            vectors_written = int(getattr(stats, "vectors_written", stats) or 0)
            cities_attempted = int(getattr(stats, "cities_attempted", 0) or 0)
            logger.info(
                "reactor day0-hourly refresh attempted for %s/%s/%s: vectors_written=%d "
                "cities_attempted=%d incomplete_expected_bundles=%d",
                family[0],
                family[1],
                family[2],
                vectors_written,
                cities_attempted,
                int(getattr(stats, "incomplete_expected_bundles", 0) or 0),
            )
            return vectors_written > 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reactor day0-hourly refresh failed for %s/%s/%s (fail-soft): %r",
                family[0],
                family[1],
                family[2],
                exc,
            )
            return False

    return _refresh


def _edli_reactor_family_market_absence_provider():
    """Build the reactor's live venue-listing absence proof provider.

    The only authority this provider exposes is durable market-unavailable
    evidence written by the substrate-observer sidecar. Plain missing topology,
    lock-busy, time-boxed probes, or network errors do not write that evidence
    and therefore do not terminalize reactor events.
    """

    def _is_absent(*, city, target_date, metric, **_ignored):
        try:
            from src.data.market_absence_evidence import has_recent_market_unavailable_evidence

            return has_recent_market_unavailable_evidence(
                city=city,
                target_date=target_date,
                metric=metric,
            )
        except Exception:
            return False

    return _is_absent


def _edli_reactor_held_family_provider():
    """ALWAYS-DECIDABLE invariant — ordering (operator correction 2026-06-12). Build the read-only,
    fail-soft provider of currently-HELD (city, target_date, metric) families so the reactor's
    refresh fan-out refreshes money-at-risk families FIRST (then liquidity-blind fair rotation —
    NO liquidity ordering). Reads zeus_trades.position_current via a short-lived mode=ro connection
    per call (the reactor owns zeus-world only; the trades read is injected so the reactor never
    opens a trades conn). Absent trades DB / any error => empty set (no held bias). Returns None
    when the trades DB path is unconfigured."""
    from src.state.db import _zeus_trade_db_path

    try:
        trades_path = _zeus_trade_db_path()
    except Exception:
        return None
    if not trades_path:
        return None

    def _provider():
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path

        from src.data.replacement_cycle_advance_trigger import _held_position_families

        p = _Path(str(trades_path))
        if not p.exists():
            return frozenset()
        conn_t = _sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5.0)
        try:
            return frozenset(_held_position_families(conn_t))
        finally:
            conn_t.close()

    return _provider


def _edli_reactor_cycle_advance_enqueuer():
    """ALWAYS-DECIDABLE invariant — Build 2 (operator law 2026-06-12). Build the single-family
    cycle-advance reseed enqueuer the reactor invokes when a family is blocked on a STALE/absent
    replacement posterior. Reuses the SAME cycle-advance re-materialization lane (seed builder +
    seed_dir the materialize cycle drains + idempotency marker) scoped to ONE family.

    Reads forecast_db / seed_dir / raw_manifest_dir from the live materialization queue config
    (the same source the poll-lane batch trigger uses). Returns None when the lane is not
    configured (no seed_dir) so the reactor simply skips the enqueue (fail-soft). The callable is
    fail-soft itself: any error returns a status dict, never raises into the reactor cycle.

    LOCK LAW: the enqueuer opens its OWN short-lived forecast-DB write connection inside the
    single-family function; the reactor invokes it from the end-of-cycle drain where NO per-event
    world/trade txn is open — no DB connection is held across this call from the reactor side.
    """
    from src.data.replacement_forecast_production import (
        _replacement_forecast_live_materialization_queue_config,
    )

    cfg = _replacement_forecast_live_materialization_queue_config()
    forecast_db = cfg.get("forecast_db")
    seed_dir = cfg.get("seed_dir")
    raw_manifest_dir = cfg.get("raw_manifest_dir")
    if forecast_db is None or seed_dir is None or raw_manifest_dir is None:
        return None

    def _enqueue(*, city, target_date, metric):
        from src.data.replacement_cycle_advance_trigger import (
            enqueue_single_family_cycle_advance_reseed,
        )

        report = enqueue_single_family_cycle_advance_reseed(
            forecast_db=Path(str(forecast_db)),
            seed_dir=Path(str(seed_dir)),
            raw_manifest_dir=Path(str(raw_manifest_dir)),
            city=city,
            target_date=target_date,
            metric=metric,
        )
        return bool(report.get("enqueued"))

    return _enqueue


def _edli_pre_submit_book_from_jit_fetch(book_quote_provider, *, token_id: str, side: str | None = None):
    """JIT single-token book fetch for the SELECTED candidate at submit time.

    GATE #84 root cause: the shared market-channel feasibility feed stamps
    ``quote_seen_at`` with the venue book-CHANGE timestamp (1s resolution, often
    minutes stale for slow weather books), and only refreshes a given token when
    its WS tick arrives (median per-candidate gap ~11s). The 1000ms pre-submit
    bound is a SUBMIT-TIME observation-freshness bound, so for the one selected
    candidate we pull its live book ``now`` and anchor freshness to OUR
    observation time — the FOK crosses against exactly this book.

    Returns ``(best_bid, best_ask, book_hash, observed_at)`` on a usable executable
    book, or ``None`` only when the fetch itself fails (fail-closed fallback to a
    genuinely-fresh DB row). If the fetch succeeds but proves the selected side is
    no longer executable, raise a typed PRE_SUBMIT_BOOK_AUTHORITY_JIT_* reason. That
    distinction matters in live: a successful JIT read is fresher authority than
    the old DB row and must force a fresh family redecision, not be masked as
    PRE_SUBMIT_BOOK_AUTHORITY_STALE.
    """

    if book_quote_provider is None:
        return None
    try:
        message = dict(book_quote_provider(token_id))
    except Exception as exc:  # noqa: BLE001 - JIT fetch failure must not fabricate freshness
        logger.warning("EDLI pre-submit JIT book fetch failed for %s: %s", token_id, exc)
        return None
    best_bid = _edli_book_best_price(message.get("bids"), best="bid")
    best_ask = _edli_book_best_price(message.get("asks"), best="ask")
    book_hash = str(message.get("hash") or "")
    normalized_side = str(side or "").upper()
    if normalized_side == "BUY" and best_ask is None:
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_BUY_ASK_MISSING:"
            f"token_id={token_id}:book_hash={book_hash or 'missing'}:best_bid={best_bid}"
        )
    if normalized_side == "SELL" and best_bid is None:
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_SELL_BID_MISSING:"
            f"token_id={token_id}:book_hash={book_hash or 'missing'}:best_ask={best_ask}"
        )
    if normalized_side not in {"BUY", "SELL"} and (best_bid is None or best_ask is None):
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_SIDE_MISSING:"
            f"token_id={token_id}:book_hash={book_hash or 'missing'}:"
            f"best_bid={best_bid}:best_ask={best_ask}"
        )
    if not book_hash:
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_HASH_MISSING:"
            f"token_id={token_id}:best_bid={best_bid}:best_ask={best_ask}"
        )
    if best_bid is not None and best_ask is not None and best_bid >= best_ask:
        # Crossed/locked book is not a usable pre-submit authority.
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_CROSSED_BOOK:"
            f"token_id={token_id}:best_bid={best_bid}:best_ask={best_ask}"
        )
    return best_bid, best_ask, book_hash, datetime.now(timezone.utc)


def _edli_book_best_price(levels, *, best: str):
    if not levels:
        return None
    parsed = []
    for level in levels:
        raw = level.get("price") if isinstance(level, dict) else (level[0] if level else None)
        if raw in (None, ""):
            continue
        try:
            parsed.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return None
    return max(parsed) if best == "bid" else min(parsed)


def _edli_pre_submit_authority_provider_from_book_evidence_conn(
    book_evidence_conn, edli_cfg, *, book_quote_provider=None
):
    """Build EDLI's production pre-submit authority provider.

    The provider consumes quote evidence, heartbeat/user-channel guards, and
    wallet allowance truth; missing authority remains fail-closed before command
    creation.

    ``book_quote_provider`` (GATE #84) is an optional just-in-time single-token
    ``/book`` fetch (``token_id -> dict``). When wired in live/canary mode it is
    the PRIMARY book authority: for the selected candidate at submit time we pull
    its live book and anchor ``quote_seen_at`` to our observation instant
    (``checked_at``), so the 1000ms freshness bound reflects observation-to-submit
    latency rather than the venue's coarse book-change stamp. The DB feasibility
    row is the fail-closed fallback and is only accepted when it is itself within
    ``max_quote_age_ms`` — a stale row never leaks through as a fresh quote.
    """

    from src.engine.event_reactor_adapter import PreSubmitAuthorityWitness

    max_quote_age_ms = int(edli_cfg.get("pre_submit_max_quote_age_ms", 1000) or 1000)
    balance_check_enabled = bool(edli_cfg.get("pre_submit_balance_allowance_check_enabled", True))
    venue_summary_cache: dict[str, object] | None = None
    pusd_collateral_payload_cache: dict[str, object] | None = None
    full_collateral_payload_cache: dict[str, object] | None = None

    def _cached_venue_summary(checked_at: datetime) -> dict[str, object]:
        nonlocal venue_summary_cache
        if venue_summary_cache is None:
            venue_summary_cache = _edli_venue_connectivity_authority_summary(checked_at)
        return venue_summary_cache

    def _cached_collateral_payload(side: str) -> dict[str, object]:
        nonlocal full_collateral_payload_cache, pusd_collateral_payload_cache
        normalized_side = str(side or "").upper()
        if normalized_side == "BUY" and pusd_collateral_payload_cache is None:
            from src.data.polymarket_client import PolymarketClient

            with PolymarketClient(public_http_timeout=_edli_pre_submit_inner_io_timeout_seconds()) as clob:
                adapter = clob._ensure_v2_adapter()
                pusd_payload_fn = getattr(adapter, "get_pusd_collateral_payload", None)
                if not callable(pusd_payload_fn):
                    pusd_payload_fn = adapter.get_collateral_payload
                pusd_collateral_payload_cache = dict(
                    _edli_run_pre_submit_clob_call(
                        "collateral_payload",
                        pusd_payload_fn,
                    )
                )
        if normalized_side == "BUY":
            return pusd_collateral_payload_cache
        if full_collateral_payload_cache is None:
            from src.data.polymarket_client import PolymarketClient

            with PolymarketClient(public_http_timeout=_edli_pre_submit_inner_io_timeout_seconds()) as clob:
                adapter = clob._ensure_v2_adapter()
                full_collateral_payload_cache = dict(
                    _edli_run_pre_submit_clob_call(
                        "collateral_payload",
                        adapter.get_collateral_payload,
                    )
                )
        return full_collateral_payload_cache

    def _provider(final_intent, _executable_snapshot, decision_time):
        checked_at = decision_time.astimezone(timezone.utc)
        intent = final_intent.payload
        token_id = str(intent["token_id"])

        # PRIMARY: just-in-time live book for the selected candidate. Freshness is
        # anchored to OUR observation time (checked_at) — the FOK crosses against
        # exactly this book — so quote_age_ms is the observation-to-submit latency.
        side = str(intent.get("side") or "").upper()
        jit = _edli_pre_submit_book_from_jit_fetch(book_quote_provider, token_id=token_id, side=side)
        if jit is not None:
            best_bid, best_ask, book_hash, book_observed_at = jit
            checked_at = book_observed_at.astimezone(timezone.utc)
            quote_seen_at = checked_at.isoformat()
            book_authority_id = "clob_jit_book"
        else:
            # FAIL-CLOSED FALLBACK: the shared feasibility feed. Accept the latest
            # row ONLY if it is itself within the freshness bound; a venue-stale row
            # (the GATE #84 pathology) must NOT be emitted as a fresh quote.
            row = _edli_latest_pre_submit_book_row(
                book_evidence_conn,
                token_id=token_id,
                side=side,
                decision_time=checked_at,
            )
            if row is None:
                raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_MISSING")
            quote_seen_at = str(_row_get(row, "quote_seen_at") or "")
            book_hash = str(_row_get(row, "book_hash_before") or "")
            best_bid = _row_float(row, "best_bid_before")
            best_ask = _row_float(row, "best_ask_before")
            if not quote_seen_at or not book_hash:
                raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_INCOMPLETE")
            if side == "BUY" and best_ask is None:
                raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_INCOMPLETE")
            if side == "SELL" and best_bid is None:
                raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_INCOMPLETE")
            try:
                row_quote_dt = datetime.fromisoformat(quote_seen_at.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_INCOMPLETE")
            if row_quote_dt.tzinfo is None:
                row_quote_dt = row_quote_dt.replace(tzinfo=timezone.utc)
            row_age_ms = (checked_at - row_quote_dt.astimezone(timezone.utc)).total_seconds() * 1000.0
            if row_age_ms > max_quote_age_ms:
                # No fresh JIT book and the only stored quote is stale: do not
                # leak a stale quote that the downstream gate would have to catch.
                raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_STALE")
            book_authority_id = "execution_feasibility_evidence"

        heartbeat_summary = _edli_heartbeat_authority_summary()
        user_ws_summary = _edli_user_ws_authority_summary(checked_at)
        venue_summary = _cached_venue_summary(checked_at)
        balance_status, balance_authority_id = _edli_balance_allowance_status(
            final_intent,
            checked_at,
            enabled=balance_check_enabled,
            collateral_payload=_cached_collateral_payload(str(intent.get("side") or "")),
        )

        return PreSubmitAuthorityWitness(
            quote_seen_at=quote_seen_at,
            book_hash=book_hash,
            current_best_bid=best_bid,
            current_best_ask=best_ask,
            tick_size=float(intent["tick_size"]),
            min_order_size=float(intent["min_order_size"]),
            neg_risk=bool(intent.get("neg_risk", False)),
            heartbeat_status="OK" if heartbeat_summary["allow_submit"] else "BLOCKED",
            user_ws_status="OK" if user_ws_summary["allow_submit"] else "BLOCKED",
            venue_connectivity_status="OK" if venue_summary["allow_submit"] else "BLOCKED",
            balance_allowance_status=balance_status,
            book_authority_id=book_authority_id,
            book_captured_at=quote_seen_at,
            heartbeat_authority_id=str(heartbeat_summary["authority_id"]),
            heartbeat_checked_at=checked_at.isoformat(),
            user_ws_authority_id=str(user_ws_summary["authority_id"]),
            user_ws_checked_at=checked_at.isoformat(),
            venue_connectivity_authority_id=str(venue_summary["authority_id"]),
            venue_connectivity_checked_at=checked_at.isoformat(),
            balance_allowance_authority_id=balance_authority_id,
            balance_allowance_checked_at=checked_at.isoformat(),
            checked_at=checked_at.isoformat(),
            max_quote_age_ms=max_quote_age_ms,
        )

    return _provider


def _edli_latest_pre_submit_book_row(
    book_evidence_conn,
    *,
    token_id: str,
    side: str | None = None,
    decision_time: datetime,
):
    normalized_side = str(side or "").upper()
    side_filter = ""
    if normalized_side == "BUY":
        side_filter = "AND best_ask_before IS NOT NULL"
    elif normalized_side == "SELL":
        side_filter = "AND best_bid_before IS NOT NULL"
    else:
        side_filter = "AND best_bid_before IS NOT NULL AND best_ask_before IS NOT NULL"
    return book_evidence_conn.execute(
        f"""
        SELECT quote_seen_at, book_hash_before, best_bid_before, best_ask_before
        FROM execution_feasibility_evidence
        WHERE token_id = ?
          AND quote_seen_at <= ?
          {side_filter}
          AND COALESCE(book_hash_before, '') != ''
        ORDER BY quote_seen_at DESC
        LIMIT 1
        """,
        (token_id, decision_time.isoformat()),
    ).fetchone()


def _edli_heartbeat_authority_summary() -> dict[str, object]:
    from src.control.heartbeat_supervisor import summary as heartbeat_summary

    summary = heartbeat_summary()
    return {
        "authority_id": "heartbeat_supervisor",
        "allow_submit": bool(summary.get("entry", {}).get("allow_submit", False)),
    }


def _edli_user_ws_authority_summary(checked_at: datetime) -> dict[str, object]:
    from src.control.ws_gap_guard import summary as ws_summary

    summary = ws_summary(now=checked_at)
    return {
        "authority_id": "ws_gap_guard",
        "allow_submit": bool(summary.get("entry", {}).get("allow_submit", False)),
    }


def _edli_venue_connectivity_authority_summary(checked_at: datetime) -> dict[str, object]:
    from src.data.polymarket_client import PolymarketClient

    with PolymarketClient(public_http_timeout=_edli_pre_submit_inner_io_timeout_seconds()) as clob:
        _edli_run_pre_submit_clob_call("venue_preflight", clob.v2_preflight)
    return {
        "authority_id": "polymarket_v2_preflight",
        "allow_submit": True,
        "checked_at": checked_at.isoformat(),
    }


def _edli_balance_allowance_status(
    final_intent,
    checked_at: datetime,
    *,
    enabled: bool,
    collateral_payload: dict[str, object] | None = None,
) -> tuple[str, str]:
    if not enabled:
        raise ValueError("PRE_SUBMIT_ALLOWANCE_CHECK_DISABLED")
    from src.data.polymarket_client import PolymarketClient

    intent = final_intent.payload
    side = str(intent.get("side") or "").upper()
    token_id = str(intent.get("token_id") or "")
    size = float(intent.get("size") or 0.0)
    notional = float(intent.get("notional_usd") or 0.0)
    if collateral_payload is None:
        with PolymarketClient(public_http_timeout=_edli_pre_submit_inner_io_timeout_seconds()) as clob:
            adapter = clob._ensure_v2_adapter()
            collateral = _edli_run_pre_submit_clob_call(
                "collateral_payload",
                adapter.get_collateral_payload,
            )
    else:
        collateral = collateral_payload
    if side == "BUY":
        balance_micro = int(collateral.get("pusd_balance_micro") or 0)
        allowance_micro = int(collateral.get("pusd_allowance_micro") or 0)
        required_micro = int(round(notional * 1_000_000))
        if balance_micro < required_micro:
            raise ValueError("PRE_SUBMIT_PUSD_BALANCE_INSUFFICIENT")
        if allowance_micro < required_micro:
            raise ValueError("PRE_SUBMIT_PUSD_ALLOWANCE_INSUFFICIENT")
        return "OK", "polymarket_wallet_readonly"
    if side == "SELL":
        balances = collateral.get("ctf_token_balances_units") or {}
        allowances = collateral.get("ctf_token_allowances_units") or {}
        token_balance = float(balances.get(token_id, 0) or 0)
        token_allowance = float(allowances.get(token_id, 0) or 0)
        if token_balance < size:
            raise ValueError("PRE_SUBMIT_CTF_BALANCE_INSUFFICIENT")
        if token_allowance < size:
            raise ValueError("PRE_SUBMIT_CTF_ALLOWANCE_INSUFFICIENT")
        return "OK", "polymarket_wallet_readonly"
    raise ValueError(f"PRE_SUBMIT_SIDE_UNSUPPORTED:{side}")


def _row_get(row, key: str):
    try:
        return row[key]
    except Exception:
        try:
            return getattr(row, key)
        except Exception:
            return None


def _row_float(row, key: str) -> float | None:
    value = _row_get(row, key)
    if value in (None, ""):
        return None
    return float(value)


def _edli_jsonl_records(path_value: str | os.PathLike[str] | None) -> list[dict]:
    path_text = str(path_value or "").strip()
    if not path_text:
        return []
    path = Path(path_text)
    if not path.exists():
        return []
    records: list[dict] = []
    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"EDLI_USER_CHANNEL_RECONCILE_QUEUE_INVALID_JSON:{path}:{line_number}") from exc
        if not isinstance(record, dict):
            raise RuntimeError(f"EDLI_USER_CHANNEL_RECONCILE_QUEUE_RECORD_NOT_OBJECT:{path}:{line_number}")
        records.append(record)
    return records


class _EdliJsonlUserChannelReader:
    def __init__(self, path_value: str | os.PathLike[str] | None):
        self._path_value = path_value

    def poll(self, *, max_messages: int) -> list[dict]:
        return _edli_jsonl_records(self._path_value)[:max(0, max_messages)]


class _EdliJsonlVenueReconcileReader:
    def __init__(self, path_value: str | os.PathLike[str] | None):
        self._facts = _edli_jsonl_records(path_value)

    def reconcile(self, pending) -> dict | None:
        aggregate_id = _row_get(pending, "aggregate_id")
        event_id = _row_get(pending, "event_id")
        final_intent_id = _row_get(pending, "final_intent_id")
        venue_order_id = _row_get(pending, "venue_order_id")
        for fact in self._facts:
            if fact.get("aggregate_id") and fact.get("aggregate_id") == aggregate_id:
                return fact
            if fact.get("venue_order_id") and fact.get("venue_order_id") == venue_order_id:
                return fact
            if fact.get("event_id") == event_id and fact.get("final_intent_id") == final_intent_id:
                return fact
        return None


def _edli_user_channel_reader(edli_cfg: dict) -> _EdliJsonlUserChannelReader:
    return _EdliJsonlUserChannelReader(edli_cfg.get("edli_user_channel_message_queue_path"))


def _edli_venue_reconcile_reader(edli_cfg: dict) -> _EdliJsonlVenueReconcileReader:
    return _EdliJsonlVenueReconcileReader(edli_cfg.get("edli_venue_reconcile_facts_path"))


def _parse_edli_runtime_time(payload: dict, *, default: datetime) -> datetime:
    for key in ("occurred_at", "observed_at", "timestamp", "created_at"):
        value = payload.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        if isinstance(value, str):
            text = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError as exc:
                raise RuntimeError(f"EDLI_RUNTIME_TIMESTAMP_INVALID:{key}") from exc
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return default


def _parse_edli_runtime_bool(value, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _resolve_edli_user_channel_aggregate_id(conn, message: dict) -> str:
    aggregate_id = str(message.get("aggregate_id") or "").strip()
    if aggregate_id:
        return aggregate_id
    venue_order_id = str(message.get("venue_order_id") or message.get("order_id") or "").strip()
    if venue_order_id:
        row = conn.execute(
            """
            SELECT aggregate_id
            FROM edli_live_order_projection
            WHERE venue_order_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (venue_order_id,),
        ).fetchone()
        if row is not None:
            return str(_row_get(row, "aggregate_id"))
    event_id = str(message.get("event_id") or "").strip()
    final_intent_id = str(message.get("final_intent_id") or "").strip()
    if event_id and final_intent_id:
        return f"{event_id}:{final_intent_id}"
    raise RuntimeError("EDLI_USER_CHANNEL_MESSAGE_AGGREGATE_UNRESOLVED")


def _edli_user_channel_message_seen(conn, *, aggregate_id: str, message_hash: str) -> bool:
    import json as _json

    if not message_hash:
        return False
    rows = conn.execute(
        """
        SELECT payload_json
        FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type IN ('UserOrderObserved','UserTradeObserved')
        """,
        (aggregate_id,),
    ).fetchall()
    for row in rows:
        payload = _json.loads(str(_row_get(row, "payload_json")))
        if payload.get("raw_user_channel_message_hash") == message_hash:
            return True
    return False


def _edli_user_channel_message_not_stale(conn, *, aggregate_id: str, occurred_at: datetime) -> None:
    row = conn.execute(
        """
        SELECT occurred_at
        FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_type = 'ExecutionCommandCreated'
        ORDER BY event_sequence DESC
        LIMIT 1
        """,
        (aggregate_id,),
    ).fetchone()
    if row is None:
        return
    command_time = datetime.fromisoformat(str(_row_get(row, "occurred_at")))
    if command_time.tzinfo is None:
        command_time = command_time.replace(tzinfo=timezone.utc)
    if occurred_at < command_time:
        raise RuntimeError("EDLI_USER_CHANNEL_MESSAGE_STALE_BEFORE_COMMAND")


def _edli_pending_reconcile_aggregates(conn, *, limit: int) -> list:
    return list(
        conn.execute(
            """
            SELECT aggregate_id, event_id, final_intent_id, venue_order_id
            FROM edli_live_order_projection
            WHERE pending_reconcile = 1
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (max(0, limit),),
        ).fetchall()
    )




def _edli_user_channel_reconcile_runtime_enabled(edli_cfg: dict) -> bool:
    if not edli_cfg.get("enabled"):
        return False
    if bool(edli_cfg.get("edli_user_channel_reconcile_enabled", False)):
        return True
    return False




def _edli_boot_fill_bridge_recovery() -> None:
    """MF-1: heal orphaned EDLI confirmed fills AT BOOT, before any new trading.

    The durable scan also runs every reconcile cycle, but running it once at boot
    closes the restart-specific orphan window immediately: if the daemon died
    between the inbox PROCESSED commit and the bridge commit on the prior run, the
    confirmed fill is stranded (no position_current, in-memory set empty). Without
    a boot pass, that capital stays invisible to chain-reconcile / exit / harvester
    / redeem until the first reconcile cycle fires (and only if the cycle is even
    enabled). Bridging at boot guarantees recovery precedes the next entry wave.

    Gate: same as the reconcile cycle — only in EDLI event-driven modes with the
    user-channel/reconcile boundary enabled. Fully fail-open: any error is logged,
    never fatal (boot must not be blocked by a recovery hiccup; the cycle retries).
    """
    try:
        edli_cfg = _settings_section("edli", {})
        if not _edli_user_channel_reconcile_runtime_enabled(edli_cfg):
            return
        now = datetime.now(timezone.utc)
        from src.state.db import get_trade_connection_with_world_required

        bridge_conn = None
        bridged = 0
        try:
            from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

            bridge_conn = get_trade_connection_with_world_required(write_class="live")
            bridged = _edli_durable_fill_bridge_scan(bridge_conn, now=now)
            bridge_conn.commit()
        finally:
            if bridge_conn is not None:
                try:
                    bridge_conn.close()
                except Exception:  # noqa: BLE001
                    pass
        if bridged:
            logger.warning(
                "EDLI boot fill-bridge recovery: healed %d orphaned confirmed "
                "fill(s) into position_current before entering the trading loop",
                bridged,
            )
        else:
            logger.info("EDLI boot fill-bridge recovery: no orphaned confirmed fills")
    except Exception as exc:  # noqa: BLE001
        # Boot recovery is best-effort: the per-cycle durable scan is the safety
        # net, so a boot-time hiccup must never block the daemon from starting.
        logger.error(
            "EDLI boot fill-bridge recovery failed (non-fatal; per-cycle scan "
            "retries): %s",
            exc,
            exc_info=True,
        )


def _edli_boot_settlement_redeem_recovery() -> None:
    """Acknowledge that settlement recovery is owned outside the order daemon.

    The P4 post-trade-capital daemon owns ``_harvester_cycle``. Running a boot
    harvester thread here re-couples a heavy post-trade SQLite/venue workflow to
    the live trading daemon and can starve the EDLI reactor immediately after a
    restart. The order daemon keeps only the fill bridge and live decision work;
    settled-position recovery drains through the dedicated post-trade daemon.
    """
    try:
        edli_cfg = _settings_section("edli", {})
        live_execution_mode = _live_execution_mode(edli_cfg)
        if not _harvester_should_register(live_execution_mode):
            return
        if live_execution_mode in EDLI_EVENT_DRIVEN_MODES and not edli_cfg.get("enabled"):
            return
        logger.info(
            "boot settlement-redeem recovery delegated to post-trade-capital "
            "daemon; order daemon will not run a boot harvester pass (mode=%s)",
            live_execution_mode,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "boot settlement-redeem ownership check failed (non-fatal; "
            "post-trade-capital daemon owns harvester retries): %s",
            exc,
            exc_info=True,
        )


def _edli_candidate_priority_token_ids(world_conn, *, lookback_hours: float = 48.0, limit: int = 4000) -> set[str]:
    """Tokens the EDLI reactor has recently decided on — the candidate universe.

    These are the YES/NO tokens of opportunity families the reactor actually
    evaluates. They MUST be pinned into the market-channel ingestor universe so a
    fresh ``execution_feasibility_evidence`` row exists for each by the time the
    reactor decides on it (Blocker #52). ``no_trade_regret_events`` records every
    reactor decision (incl. the witness-failure rejections we are fixing), so its
    recent token set is a precise, self-maintaining candidate signal — no
    cross-DB topology read in the hot path.
    """

    if world_conn is None:
        return set()
    try:
        has_table = world_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='no_trade_regret_events'"
        ).fetchone()
    except Exception:
        return set()
    if not has_table:
        return set()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(0.0, lookback_hours))).isoformat()
    try:
        rows = world_conn.execute(
            """
            SELECT DISTINCT token_id
            FROM no_trade_regret_events
            WHERE token_id IS NOT NULL AND token_id != '' AND token_id != 'None'
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (cutoff, int(limit)),
        ).fetchall()
    except Exception:
        return set()
    return {str(r[0]) for r in rows if r and r[0]}


def _edli_market_channel_refresh_kwargs(action, markets, clob, captured_at) -> dict:
    """Build refresh_executable_market_substrate_snapshots kwargs for a market-channel action.

    Authority is always VERIFIED (snapshots come from verified Gamma/CLOB data);
    the EDLI channel trigger reason is carried as non-authoritative refresh_reason
    metadata so it appears in the summary log without polluting the capture contract.

    Separating these two carriers fixes P1-1: the original code passed
    ``scan_authority=f"EDLI_MARKET_CHANNEL:{action.reason}"`` which caused
    capture_executable_market_snapshot to raise ExecutableSnapshotCaptureError on
    every attempt (it requires scan_authority == "VERIFIED"), making the entire
    reactive snapshot-refresh path silently dead.
    """
    return dict(
        markets=markets,
        clob=clob,
        captured_at=captured_at,
        scan_authority="VERIFIED",
        refresh_reason=f"EDLI_MARKET_CHANNEL:{action.reason}",
        max_outcomes=20,
        budget_seconds=15.0,
    )




# FIX 2c (2026-06-20): monitor-cadence watchdog. exit_monitor runs on a 2-min
# interval (see scheduler.add_job(..., minutes=2, id="exit_monitor")) and is the
# sole writer of MONITOR_REFRESHED. The live book observed whole-book silences of
# 8.8h and 11.8h (2026-06-18/19) during which belief AND the live bid collapsed
# unobserved, killing the only realized reversal exit. The multi-hour cause is a
# daemon/APScheduler process gap — that supervision is OPERATOR INFRA, out of
# code. What code CAN do is flag the gap on the first cycle after recovery: if
# the newest MONITOR_REFRESHED is older than ~2× the interval, the cadence broke.
# This is detection only; it does not (and must not) re-drive the schedule.
_EXIT_MONITOR_INTERVAL_SECONDS = 120.0
_MONITOR_CADENCE_GAP_FACTOR = 2.0


def _check_monitor_cadence_watchdog(conn, summary: dict) -> dict | None:
    """Flag when MONITOR_REFRESHED cadence has lapsed beyond ~2× the interval.

    Reads the newest canonical MONITOR_REFRESHED occurred_at from position_events
    (same trade DB this conn owns) and compares to now. Detection only — records
    the gap in ``summary`` and logs a warning so operator supervision can act;
    never restarts or back-fills. Returns the watchdog record dict when a gap is
    flagged, else None. Fail-soft: any read/parse error returns None.
    """
    if conn is None:
        return None
    threshold_seconds = _EXIT_MONITOR_INTERVAL_SECONDS * _MONITOR_CADENCE_GAP_FACTOR
    try:
        row = conn.execute(
            """
            SELECT MAX(occurred_at)
              FROM position_events
             WHERE event_type = 'MONITOR_REFRESHED'
            """
        ).fetchone()
    except Exception:
        return None
    if row is None or row[0] is None:
        return None
    last_refresh_raw = str(row[0])
    try:
        last_refresh = datetime.fromisoformat(last_refresh_raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if last_refresh.tzinfo is None:
        last_refresh = last_refresh.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    gap_seconds = (now - last_refresh.astimezone(timezone.utc)).total_seconds()
    summary["monitor_cadence_gap_seconds"] = round(gap_seconds, 1)
    if gap_seconds <= threshold_seconds:
        return None
    record = {
        "last_monitor_refreshed_at": last_refresh_raw,
        "observed_at": now.isoformat(),
        "gap_seconds": round(gap_seconds, 1),
        "interval_seconds": _EXIT_MONITOR_INTERVAL_SECONDS,
        "threshold_seconds": threshold_seconds,
        "gap_factor": round(gap_seconds / _EXIT_MONITOR_INTERVAL_SECONDS, 2),
    }
    summary["monitor_cadence_gap_flagged"] = record
    logger.warning(
        "MONITOR_CADENCE_GAP: last MONITOR_REFRESHED was %s (%.1fs ago, %.1f× the "
        "%.0fs interval > %.1f× threshold). exit_monitor cadence lapsed — likely a "
        "daemon/scheduler process gap (operator supervision, out of code).",
        last_refresh_raw,
        gap_seconds,
        gap_seconds / _EXIT_MONITOR_INTERVAL_SECONDS,
        _EXIT_MONITOR_INTERVAL_SECONDS,
        _MONITOR_CADENCE_GAP_FACTOR,
    )
    return record


@_scheduler_job("exit_monitor")
def _exit_monitor_cycle() -> None:
    """Standalone exit-lifecycle monitoring job owned by the order daemon.

    The chain-truth READ phase was lifted to the P4 post-trade-capital daemon.
    This order-runtime job keeps only the live exit-SUBMIT lane: held-position
    monitoring, exit preflight, pending-exit state transitions, and gated sell
    order submission when ``real_order_submit_enabled`` is true.
    """
    from src.data.polymarket_client import PolymarketClient
    from src.engine.cycle_runner import (
        _execute_monitoring_phase,
        get_connection,
        get_tracker,
        load_portfolio,
        save_tracker,
        save_portfolio,
    )
    from src.state.canonical_write import commit_then_export
    from src.state.decision_chain import CycleArtifact
    from src.state.decision_chain import store_artifact

    edli_cfg = _settings_section("edli", {})
    real_order_submit_enabled = bool(edli_cfg.get("real_order_submit_enabled", False))
    if _held_position_monitor_active.is_set():
        logger.warning("exit_monitor skipped: previous monitor cycle is still running")
        return
    _held_position_monitor_active.set()

    conn = get_connection()
    if conn is None:
        logger.warning("exit_monitor: DB write-lock degrade — skipping cycle")
        _mark_held_position_monitor_complete()
        return

    summary: dict = {"monitors": 0, "exits": 0}
    # FIX 2c (2026-06-20): detect a lapsed MONITOR_REFRESHED cadence (whole-book
    # silence) on the first cycle after recovery. Detection only; the underlying
    # daemon supervision is operator infra.
    try:
        _check_monitor_cadence_watchdog(conn, summary)
    except Exception as _wd_exc:  # noqa: BLE001 — watchdog must never break the cycle
        logger.warning("exit_monitor: cadence watchdog failed (non-fatal): %s", _wd_exc)
    try:
        portfolio = load_portfolio()
        held_monitor_allocator_refresh = _refresh_global_allocator_for_held_position_monitor(
            conn,
            portfolio,
        )
        summary["held_monitor_allocator_refresh"] = held_monitor_allocator_refresh
        if held_monitor_allocator_refresh.get("configured"):
            summary["held_monitor_allocator_retry_release"] = (
                _release_allocator_config_blocked_exit_retries_after_refresh(
                    conn,
                    portfolio,
                    observed_at=datetime.now(timezone.utc),
                )
            )
        with PolymarketClient() as clob:
            tracker = get_tracker()
            artifact = CycleArtifact(
                mode="exit_monitor",
                started_at=datetime.now(timezone.utc).isoformat(),
                summary=summary,
            )
            portfolio_dirty = False
            tracker_dirty = False
            try:
                portfolio_dirty, tracker_dirty = _execute_monitoring_phase(
                    conn,
                    clob,
                    portfolio,
                    artifact,
                    tracker,
                    summary,
                    exit_order_submit_enabled=real_order_submit_enabled,
                    run_exit_preflight=True,
                )
            except Exception as exc:
                logger.error(
                    "exit_monitor: monitoring phase failed (non-fatal): %s",
                    exc,
                    exc_info=True,
                )
                summary["monitoring_error"] = str(exc)

            # DAY0 resting-order cancel sweep (adversarial review
            # 2026-06-10 fix 2 — finding 4 "standing free option"). Cancels OUR
            # open resting ENTRY orders whose day0 bin is hard-fact dead for the
            # order's side, or whose family is oracle-anomaly paused. Cancels
            # only REDUCE standing risk; gated to live-submit mode because in
            # submit-disabled posture no real resting orders of ours exist (and
            # the venue cancel is a real API call). Fail-soft.
            if real_order_submit_enabled and bool(
                edli_cfg.get("day0_dead_bin_order_cancel_enabled", True)
            ):
                try:
                    from src.config import runtime_cities_by_name
                    from src.execution.day0_hard_fact_exit import (
                        cancel_day0_dead_bin_resting_entries,
                    )

                    cancelled = cancel_day0_dead_bin_resting_entries(
                        clob=clob,
                        conn=conn,
                        cities_by_name=runtime_cities_by_name(),
                    )
                    if cancelled:
                        summary["day0_dead_bin_orders_cancelled"] = cancelled
                except Exception as exc:  # noqa: BLE001 — sweep is additive
                    logger.warning(
                        "exit_monitor: day0 dead-bin cancel sweep failed (non-fatal): %s",
                        exc,
                    )

        # INV-17 / DT#1: commit the DB transaction (monitoring state transitions) FIRST,
        # then export the derived portfolio/tracker JSON with the committed artifact id —
        # so canonical_write.detect_stale_portfolio's marker stays valid and JSON can
        # never lead the DB.
        _aid_box: list = [None]

        def _db_op():
            _aid_box[0] = store_artifact(conn, artifact)
            return _aid_box[0]

        def _export_portfolio():
            if portfolio_dirty:
                save_portfolio(
                    portfolio,
                    last_committed_artifact_id=_aid_box[0],
                    source="exit_monitor",
                )

        def _export_tracker():
            if tracker_dirty:
                save_tracker(tracker)

        commit_then_export(
            conn, db_op=_db_op, json_exports=[_export_portfolio, _export_tracker]
        )
    except Exception as exc:
        logger.error(
            "exit_monitor: unexpected error: %s", exc, exc_info=True
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass
        _mark_held_position_monitor_complete()

    # EDLI status-summary freshness writer (release-gate surface).
    # In EDLI event-driven modes run_cycle() is never called, so the legacy
    # _export_status -> write_cycle_pulse path is silent and state/status_summary.json
    # goes stale -> the live-release gate fails status_summary / edli_stage_readiness.
    # This exit monitor runs under ALL EDLI modes, so emit a genuine business-plane
    # status pulse here each cycle. write_cycle_pulse re-reads the live DB read model
    # (open orders, risk, portfolio, capability) -> it reflects REAL current state,
    # never a hardcoded healthy value. Non-fatal: a pulse failure must not abort the
    # chain-sync job. Authority: fix/edli-stage-readiness-2026-05-31 (status_summary).
    try:
        from src.observability.status_summary import write_cycle_pulse
        write_cycle_pulse(summary)
    except Exception as exc:
        logger.error(
            "exit_monitor: status pulse failed (non-fatal): %s",
            exc,
            exc_info=True,
        )

    _write_scheduler_health(
        "exit_monitor",
        failed=False,
        extra={
            "exit_order_submit_enabled": real_order_submit_enabled,
            "monitors": summary.get("monitors", 0),
            "exits": summary.get("exits", 0),
        },
    )


def main():
    _start = time.monotonic()  # F86: process start time for SIGTERM elapsed log
    global BlockingScheduler
    if BlockingScheduler is None:
        from apscheduler.schedulers.blocking import BlockingScheduler as _BlockingScheduler

        BlockingScheduler = _BlockingScheduler
    mode = get_mode()
    once = "--once" in sys.argv

    # --validate-boot: read-only pre-restart smoke (W0-T3, 2026-06-03).
    # Runs EVERY boot guard (calibration pin shape, staleness, schema, registry)
    # without acquiring ANY exclusive resource — no venue heartbeat thread, no
    # world_write_lock, no APScheduler, no network calls, no DB writes.
    # Safe to run while the live daemon is active. Exits before the daemon loop.
    #
    # Usage:
    #   python -m src.main --validate-boot [--settings-path /path/to/settings.json]
    #
    # Exit codes: 0 = all guards pass, 1 = one or more fail.
    if "--validate-boot" in sys.argv:
        _sp_idx = sys.argv.index("--settings-path") if "--settings-path" in sys.argv else None
        if _sp_idx is not None and _sp_idx + 1 >= len(sys.argv):
            print("ERROR: --settings-path requires a following value", file=sys.stderr)
            sys.exit(1)
        _sp = sys.argv[_sp_idx + 1] if _sp_idx is not None else None
        # Use plain print (not logger) — logging not yet configured.
        print("zeus --validate-boot: running read-only boot guards")
        exit_code = _validate_boot(settings_path=_sp)
        print(f"zeus --validate-boot: {'ALL PASS' if exit_code == 0 else 'SOME FAIL'} (exit {exit_code})")
        sys.exit(exit_code)

    # F85: route INFO (below-WARNING) to stdout (.log) and WARNING+ to stderr (.err).
    # Plists correctly bifurcate StandardOutPath/.err; basicConfig default
    # StreamHandler(sys.stderr) was routing all output to .err only.
    _fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    _stdout_h = logging.StreamHandler(sys.stdout)
    _stdout_h.setLevel(logging.INFO)
    _stdout_h.setFormatter(_fmt)
    _stdout_h.addFilter(lambda r: r.levelno < logging.WARNING)
    _stderr_h = logging.StreamHandler(sys.stderr)
    _stderr_h.setLevel(logging.WARNING)
    _stderr_h.setFormatter(_fmt)
    _root = logging.getLogger()
    _root.handlers.clear()
    _root.setLevel(logging.INFO)
    _root.addHandler(_stdout_h)
    _root.addHandler(_stderr_h)
    # F86: forensic SIGTERM trail — logs elapsed seconds to .err before exit.
    signal.signal(
        signal.SIGTERM,
        lambda s, f: (
            logger.error(
                "SIGTERM_RECEIVED pid=%s ppid=%s elapsed=%ss",
                os.getpid(), os.getppid(), int(time.monotonic() - _start),
            ),
            sys.exit(0),
        ),
    )

    logger.info("Zeus starting in %s mode%s", mode, " (single cycle)" if once else "")

    # PR-S6: capture deployment snapshot for freshness gate.
    # Must run early (before any blocking I/O) so uptime accounting is accurate.
    # Fail-loud if git unavailable and ZEUS_ACCEPT_STALE_DEPLOY != "1".
    _boot = _capture_boot_state()
    _BOOT_STATE.update(_boot)
    if _boot.get("sha"):
        logger.info("deployment_freshness: boot_sha=%s", _boot["sha"][:8])
        os.environ["ZEUS_PROCESS_BOOT_SHA"] = str(_boot["sha"])
    # EDLI-mode release-gate surface: persist the genuinely-booted HEAD SHA so the
    # live-release gate's loaded_sha check can compare loaded vs expected. Reuses
    # the boot SHA captured above; written once (not refreshed) so a post-boot
    # filesystem divergence is still caught by the gate.
    _write_loaded_sha_state(_boot.get("sha"))

    # Proxy health gate: strip dead HTTP_PROXY so data-only mode works
    # without VPN. Must precede any HTTP call (PolymarketClient wallet check, etc).
    from src.data.proxy_health import bypass_dead_proxy_env_vars
    bypass_dead_proxy_env_vars()

    # Venue heartbeat is the liveness contract for already-resting CLOB orders.
    # Start it before any boot-time wallet/readiness HTTP so a restart cannot
    # leave existing orders without heartbeats while slow checks complete.
    _start_venue_heartbeat_loop_if_needed()

    # Live scheduler must start before any wallet/CLOB SDK warm path. The wallet
    # warm path imports py-clob/eth/http stacks and can hold the process import
    # lock while waiting on network or disk I/O. That is acceptable for the
    # submit lane to fail-closed, but not for monitor/redecision/Day0 startup.
    _wallet_warm_thread, _wallet_warm_holder = None, _BootWalletWarmHolder()

    # §4.2 DB schema-ready gate — fail-closed (Phase 3 enforcement).
    # Must run before the first world DB open/read so missing or uninitialized
    # DBs go through the retry/FATAL authority path rather than raw SQLite errors.
    # Directly verifies world/forecast DB schema versions. Older JSON sentinels
    # from data-ingest are not live boot authority after the forecast-live split.
    _startup_world_schema_ready_check()

    # Daemon is a read-only consumer of world DB. Schema currency was proven
    # above by direct read-only structural checks on the canonical DB files.
    # Opening without write_class avoids the v4 LIVE flock and never acquires
    # a SQLite writer lock for read-only ops below — so a concurrent ingest
    # or backfill cannot starve daemon startup.
    conn = get_world_connection()
    # Read-only smoke: confirm world DB is reachable (connectivity only).
    conn.execute("SELECT 1").fetchone()

    # Ensure trade DB has only trade-owned tables (PR-S4b: was init_schema which
    # also created world tables on zeus_trades.db; init_schema_trade_only creates
    # trade runtime tables plus the migration ledger so
    # assert_db_matches_registry(TRADE) passes).
    trade_conn = get_trade_connection(write_class="live")
    init_schema_trade_only(trade_conn)
    trade_conn.close()

    # F109 boot-time consolidation (2026-05-17 MAJ-1).
    # Must run BEFORE any strategy gate or wallet check that reads position_current.
    # Voids oldest duplicate open-phase rows so the migration pre-flight passes.
    _run_f109_consolidator()

    # Startup health check: warn about deferred data actions
    _startup_data_health_check(conn)

    # v1.F1 (2026-05-18): assert_db_matches_registry boot wiring.
    # Fail-closed per INV-05: RegistryAssertionError propagates and aborts daemon start.
    # No advisory mode — a live DB whose table-set diverges from
    # architecture/db_table_ownership.yaml must not enter the trading loop.
    # Guard: ZEUS_BOOT_REGISTRY_ASSERT_ENABLED defaults "1" (enabled).
    # Set to "0" ONLY during intentional schema migrations; document the migration window.
    if os.environ.get("ZEUS_BOOT_REGISTRY_ASSERT_ENABLED", "1") != "0":
        from src.state.table_registry import (
            DBIdentity,
            assert_db_matches_registry,
        )
        assert_db_matches_registry(conn, DBIdentity.WORLD)
        logger.info("assert_db_matches_registry: world DB table-set matches registry")
        _trade_conn_reg = get_trade_connection()
        try:
            assert_db_matches_registry(_trade_conn_reg, DBIdentity.TRADE)
            logger.info("assert_db_matches_registry: trade DB table-set matches registry")
        finally:
            _trade_conn_reg.close()
    conn.close()

    # W0-T2/T3: calibration pin shape + staleness guards (2026-06-03).
    # _run_boot_guards is the DRY helper shared with --validate-boot so the
    # pre-restart smoke and the real boot path run the SAME guards (no drift).
    # NB: guards take raw config dict (cfg.get(...)). settings is a strict
    # Settings object with no .get(); pass settings._data (the raw-dict accessor
    # _settings_section() also uses). Passing the object itself raised
    # AttributeError at boot, crash-looping the daemon (W0 fix 2026-06-03).
    _pin_guard_cfg = settings._data if hasattr(settings, "_data") else settings
    for _gname, _gpassed, _gdetail in _run_boot_guards(_pin_guard_cfg):
        if not _gpassed:
            raise RuntimeError(f"BOOT_GUARD_FAILED:{_gname}: {_gdetail}")
        logger.info("boot-guard %s: %s", _gname, _gdetail)
    logger.info("calibration pin shape + staleness boot-guards: OK")

    _ensure_day0_identity_platt_fit_at_boot()

    # N2 — S2 deployment gate (PR-S1, Bug #3).
    # If S1 is deployed but S2 has not been deployed within 4h, refuse boot.
    # Prevents the daemon running with partial fix coverage beyond the SLA window.
    # Operator override: ZEUS_ACCEPT_S1_ALONE=1 (emergency use only).
    _check_s1_without_s2_sla()

    # §3.1 Data freshness gate — WARN-only at boot (Phase 2: warn; Phase 3: enforce).
    # Runs BEFORE strategy gate so operator sees freshness diagnostics even when
    # strategy gate refuses. GATE SPLIT (§3.7): data gate is operator-overridable
    # via state/control_plane.json::force_ignore_freshness: ["source_name"].
    # Wallet reachability (_startup_wallet_check below) is never overridden into
    # fake bankroll truth.
    # Absent source_health.json → 5-min retry then FATAL (see freshness_gate.py).
    # Stale source_health.json → degrade per source family; trading continues.
    # Phase 3 will promote ABSENT result here to a hard FATAL (currently warn).
    _startup_freshness_check()

    # C5 cadence-coverage guard (timing-semantics fix 2026-06-16): warn if the
    # effective warm-cycle sweep period exceeds the selection freshness window.
    # _EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS is the APScheduler interval for the
    # executable-snapshot substrate warmer; FRESHNESS_WINDOW_DEFAULT is the
    # timedelta used by ExecutableMarketSnapshot to mark a captured snapshot stale.
    # If the cadence exceeds the window, every selection reads stale data silently.
    from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
    _warn_if_cadence_uncovered(
        _EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS,
        FRESHNESS_WINDOW_DEFAULT.total_seconds(),
    )

    # G6 antibody (2026-04-26, fixed 2026-04-26 per con-nyx CONDITION C1):
    # Refuse boot if any non-allowlisted strategy is enabled. Must run AFTER
    # init_schema (so control_overrides table exists) and BEFORE wallet check
    # (no point spending HTTP if guard refuses). The helper hydrates
    # _control_state from durable storage before composing the enabled set —
    # without hydration, every strategy reads as enabled (default-True) and
    # operator-set gates from prior `set_strategy_gate` invocations are not
    # visible. See _assert_live_safe_strategies_or_exit() docstring above.
    _assert_live_safe_strategies_or_exit()

    # PR-S8 boot-time auto-resume: if entries were paused at 4h SHA divergence
    # (PR #149 deployment_freshness gate) and the daemon is now restarted with
    # the current git HEAD, clear the pause automatically. Must run AFTER
    # _assert_live_safe_strategies_or_exit() (which hydrates _control_state).
    _boot_deployment_freshness_auto_resume()

    # Do not block scheduler startup on wallet warm. A missing warm record keeps
    # new submit/sizing fail-closed while monitor/redecision/settlement continue.
    _join_boot_wallet_warm(_wallet_warm_thread)
    _warm_rec = _wallet_warm_holder.record
    _capital_str = (
        f"${_warm_rec.value_usd:.2f}" if _warm_rec is not None else "<wallet_unreachable>"
    )
    logger.info("Capital (on-chain): %s | Kelly: %.0f%%",
                _capital_str,
                settings["sizing"]["kelly_multiplier"] * 100)

    # P7: Wallet reachability warm-up — must run before first cycle.
    # GATE SPLIT (§3.7): wallet failure is NEVER converted into fake bankroll
    # truth; new submit/sizing fail closed while monitor/redecision continues.
    # Consume the warm record (efficiency #3): warm + gate = exactly ONE
    # current() acquisition.
    _startup_wallet_check(bankroll_record=_warm_rec)

    # MF-1: durable self-healing capital spine — AT BOOT, before any new trading,
    # bridge any EDLI confirmed fill that was orphaned (no position_current) by a
    # prior daemon death / swallowed bridge exception. Closes the restart-specific
    # orphan window immediately so stuck capital is visible to chain-reconcile /
    # exit / harvester / redeem before the first entry wave. Fail-open (never
    # blocks boot); the per-cycle durable scan is the continuous safety net.
    _edli_boot_fill_bridge_recovery()

    # 守護 (2026-06-03): queue a non-blocking recovery pass for VERIFIED settlement
    # truth already on disk for FILLED positions still sitting phase=active.
    # Runs AFTER fill-bridge recovery so freshly-bridged positions are visible;
    # never blocks scheduler startup. Fail-open; no on-chain side effect.
    _edli_boot_settlement_redeem_recovery()

    if once:
        run_single_cycle()
        return

    # APScheduler loop mode.
    # P0 invariant: scheduler MUST run in UTC. Cron expressions like
    # ``hour=7,9,19,21`` for update_reaction_times_utc are written
    # against UTC; without an explicit timezone= kwarg APScheduler
    # falls back to the host's local tz (CDT/CST on the deployment
    # box), shifting every cron job by 5h. See ``docs/operations/
    # task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md`` §P0
    # (the file is at v3 per its §0.1 changelog) and §4 D-D drift +
    # operator directive 2026-05-04 "所有的执行时间都需要严格统一用utc".
    # Dedicated executor for the EDLI reactor so venue-heavy jobs (market
    # discovery, reconcile, venue heartbeat — many serial blocking CLOB HTTP
    # calls) in the shared 'default' pool cannot starve it. Symptom 2026-05-31:
    # the reactor misfired for 10+ min (coalesce-skipped) while all default
    # workers were blocked on socket reads (py-sample: 189 read frames, 0 reactor
    # frames), so 0 no-submit receipts ever formed. An isolated pool guarantees
    # the reactor always has a worker. Authority: docs plan A2-throughput.
    scheduler_kwargs = {"timezone": ZoneInfo("UTC")}
    try:
        from apscheduler.executors.pool import ThreadPoolExecutor as _APThreadPoolExecutor

        scheduler_kwargs["executors"] = {
            "default": _APThreadPoolExecutor(20),
            "reactor": _APThreadPoolExecutor(2),
        }
    except ModuleNotFoundError:
        if BlockingScheduler is None or getattr(BlockingScheduler, "__module__", "").startswith("apscheduler"):
            raise

    scheduler = BlockingScheduler(**scheduler_kwargs)
    discovery = settings["discovery"]

    # All modes use the SAME CycleRunner with different DiscoveryMode values
    # max_instances=1: prevent concurrent execution if previous cycle still running
    edli_cfg = _settings_section("edli", {})
    live_execution_mode = _assert_live_execution_mode_contract(edli_cfg)
    _assert_edli_stage_readiness(edli_cfg)
    _edli_boot_command_recovery_once()
    _edli_boot_invalid_pending_entry_authority_cancel_once()
    # SINGLE TRUTH (bias-maze strip 2026-06-17): the EMOS-CI license boot guard is REMOVED
    # (the override it guarded is gone). The legacy bias/Platt calibration-coverage contract
    # is now an unconditional logged no-op (not applicable under single-truth).
    _assert_calibration_coverage_contract(edli_cfg)
    if live_execution_mode == "legacy_cron":
        scheduler.add_job(
            lambda: _run_mode(DiscoveryMode.OPENING_HUNT), "interval",
            minutes=discovery["opening_hunt_interval_min"], id="opening_hunt",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS),
            max_instances=1, coalesce=True,
        )
        for time_str in discovery["update_reaction_times_utc"]:
            h, m = time_str.split(":")
            scheduler.add_job(
                lambda: _run_mode(DiscoveryMode.UPDATE_REACTION), "cron",
                hour=int(h), minute=int(m), id=f"update_reaction_{time_str}",
                max_instances=1, coalesce=True,
            )
        scheduler.add_job(
            lambda: _run_mode(DiscoveryMode.DAY0_CAPTURE), "interval",
            minutes=discovery["day0_interval_min"], id="day0_capture",
            next_run_time=_utc_run_time_after(_day0_first_delay_seconds(discovery)),
            max_instances=1, coalesce=True,
        )
        # imminent_open_capture: fires every 5 min to catch re-opened or D+1 markets
        # in the 0-24h window that fall below opening_hunt's min_hours_to_resolution:24
        # threshold. Fail-closed on stale data (same freshness gate as day0_capture).
        scheduler.add_job(
            lambda: _run_mode(DiscoveryMode.IMMINENT_OPEN_CAPTURE), "interval",
            minutes=discovery.get("imminent_open_capture_interval_min", 5),
            id="imminent_open_capture",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 15.0),
            max_instances=1, coalesce=True,
        )
    if live_execution_mode in EDLI_EVENT_DRIVEN_MODES and edli_cfg.get("enabled"):
        scheduler.add_job(
            _edli_event_reactor_cycle,
            "interval",
            minutes=1,
            id="edli_event_reactor",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 5.0),
            max_instances=1,
            coalesce=True,
            executor="reactor",
        )
        # GATE #84 keepalive pinger (2026-06-22): keep the submit-time JIT /book CLOB
        # connection warm so an edge-positive submit candidate never pays a cold TLS
        # handshake (~2.2-2.7s) at the pre-submit authority gate — the regression that
        # timed out 118/120 JIT fetches (06-17..06-22) and requeued ~84% of orders. 25s
        # cadence stays inside the 90s keepalive_expiry; read-only /time probe, touches
        # no trading state, fail-soft. Pre-warm fires ~immediately so the first submit
        # after boot is already warm. max_instances=1/coalesce so a slow ping can't stack.
        scheduler.add_job(
            _edli_pre_submit_jit_keepalive_tick,
            "interval",
            seconds=25,
            id="edli_presubmit_jit_keepalive",
            next_run_time=_utc_run_time_after(15.0),
            max_instances=1,
            coalesce=True,
        )
        # STRUCTURAL FIX (2026-05-31, #45 follow-up): dedicated ~60s on-chain bankroll
        # cache warmer, DECOUPLED from the slow (~330s) reactor cycle. The reactor's
        # warm-once-at-cycle-start let _last_fetched_at age past the cached() 300s
        # window before per-event Kelly / allocator reads ran near cycle END →
        # KELLY_PROOF_MISSING:bankroll_provider_unavailable on every candidate. This
        # frequent independent warm keeps cached() fresh. Not a DB writer; fail-soft.
        scheduler.add_job(
            _edli_bankroll_warm_cycle,
            "interval",
            seconds=60,
            id="edli_bankroll_warm",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 30.0),
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            _edli_day0_hourly_refresh_cycle,
            "interval",
            seconds=int(os.environ.get("ZEUS_DAY0_HOURLY_REFRESH_JOB_SECONDS", "180")),
            id="edli_day0_hourly_refresh",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 35.0),
            max_instances=1,
            coalesce=True,
        )
        # K4.0 REST-THEN-CROSS deadline owner: cancels GTC maker entry rests older
        # than the measured escalation deadline (20min). 5-min cadence is well inside
        # the deadline's 60-min derivation slack (taker_immediate_event_end_floor
        # relation in the time-semantics registry). Cancel-only; never submits.
        scheduler.add_job(
            _maker_rest_escalation_cycle,
            "interval",
            minutes=5,
            id="maker_rest_escalation",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 45.0),
            max_instances=1,
            coalesce=True,
        )
        # CONTINUOUS RE-DECISION P2 screen (resurrection 2026-06-12): reacts to PRICE movement
        # between forecast cycles. Reads cached beliefs × freshest executable prices (RO, no HTTP),
        # enqueues EDLI_REDECISION_PENDING for families whose edge fired, and pulls/​re-decides
        # abandoned maker rests (§4.5). ~90s cadence (well inside the executable-price freshness
        # window the substrate warmer maintains). Wave-1 2026-06-12: always REGISTERED; the job
        # body self-gates on live-armed conditions (reactor_mode == live + event_writer_enabled),
        # the redecision_screen_enabled flag deleted. Data + cancel only, fail-soft.
        # max_instances=1/coalesce so overlapping triggers skip.
        scheduler.add_job(
            _edli_continuous_redecision_screen_cycle,
            "interval",
            seconds=90,
            id="edli_continuous_redecision_screen",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 50.0),
            max_instances=1,
            coalesce=True,
        )
        # #28c: unresolved-command reconcile sweep with its own cadence — the
        # EDLI lane previously had NO owner for stuck SUBMITTING/UNKNOWN rows
        # (the INV-31 sweep only ran inside the legacy cycle_runner loop).
        scheduler.add_job(
            _edli_command_recovery_cycle,
            "interval",
            minutes=3,
            id="edli_command_recovery",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 60.0),
            max_instances=1,
            coalesce=True,
        )
        # MAINSTREAM WARM (E2 / operator directive 2026-06-04 #2): dedicated off-mutex
        # warmer for the mainstream-forecast point cache (read_mainstream_point_cached),
        # mirroring _edli_market_substrate_warm_cycle. The reactor proof path now ALWAYS
        # annotates the mainstream/bias agreement value on every candidate (decoupled from
        # mainstream_agreement_reference_enabled), reading the WARM CACHE only — so this job
        # MUST run for the cache to populate, else every receipt carries
        # mainstream_*=None (unknown). Gated only by edli.enabled (inside the job), NOT
        # by the reference flag — warming the cache is just a read, off-mutex, safe. The
        # fetch applies Retry-After backoff on 429s; on its own cadence it never serializes
        # a world write. Data-only (no orders); fail-soft. Display-only: the value it warms
        # is NEVER a decision input (the enforce/arm coupling is deleted).
        scheduler.add_job(
            _edli_mainstream_warm_cycle,
            "interval",
            seconds=90,
            id="edli_mainstream_warm",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 70.0),
            max_instances=1,
            coalesce=True,
        )
    # Exit-lifecycle monitoring stays in the order daemon. Chain-sync READ,
    # market/user channel ingest, substrate capture, and post-trade capital
    # pollers are owned by their dedicated live daemons.
    scheduler.add_job(
        _exit_monitor_cycle,
        "interval",
        minutes=2,
        id="exit_monitor",
        next_run_time=_utc_run_time_after(HELD_POSITION_MONITOR_FIRST_DELAY_SECONDS),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(_write_heartbeat, "interval", seconds=60, id="heartbeat",
                      max_instances=1, coalesce=True)
    # WAL checkpoint-starvation backstop (2026-06-04, part 2): periodic
    # PRAGMA wal_checkpoint(TRUNCATE) on zeus-world.db so the -wal file cannot
    # grow unboundedly when a reader transiently pins the floor between part-1
    # per-cycle releases. Mode-independent (the WAL bloat afflicts every mode),
    # so registered unconditionally. ~90s cadence (> the 60s reactor interval so
    # it does not fight an in-flight reactor read every tick; coalesce/max=1 so a
    # slow checkpoint never stacks).
    scheduler.add_job(
        _world_wal_checkpoint_cycle, "interval", seconds=90,
        id="world_wal_checkpoint", next_run_time=_utc_run_time_after(120.0),
        max_instances=1, coalesce=True,
    )
    # zeus_trades.db WAL TRUNCATE backstop (2026-06-16, the 810MB -wal incident).
    # The trade DB had no checkpoint backstop (only zeus-world.db did), so a reader
    # pinning the floor let zeus_trades.db-wal grow unbounded → snapshot-capture
    # writes failed `database is locked` → fresh_executable_city_count=0 → the spine
    # starved of priceable families. Same 90s cadence; offset start so it doesn't
    # fire in lockstep with the world checkpoint.
    scheduler.add_job(
        _trades_wal_checkpoint_cycle, "interval", seconds=90,
        id="trades_wal_checkpoint", next_run_time=_utc_run_time_after(135.0),
        max_instances=1, coalesce=True,
    )
    from src.control.heartbeat_supervisor import heartbeat_cadence_seconds_from_env
    scheduler.add_job(
        _start_venue_heartbeat_loop_if_needed,
        "interval",
        seconds=heartbeat_cadence_seconds_from_env(),
        id="venue_heartbeat",
        max_instances=1,
        coalesce=True,
    )

    # PR-S6: deployment freshness gate — runs every 60s, fail-closed at 24h uptime.
    scheduler.add_job(
        _check_deployment_freshness, "interval", seconds=60,
        id="deployment_freshness", max_instances=1, coalesce=True,
    )
    # Daily 守護 settlement-guard scorecard — runs at 09:15 UTC, after the
    # 07:30 forecasts tick and the hourly settlement-truth writes have landed.
    # Read-only over graded tables; writes state/settlement_guard_report.json +
    # docs/evidence/settlement_guard/<date>.md + a one-line INFO summary.
    scheduler.add_job(
        _settlement_guard_report_tick, "cron", hour=9, minute=15,
        id="settlement_guard_report", max_instances=1, coalesce=True,
    )
    # Settlement skill-attribution — runs ~2min after boot, then EVERY 30min.
    # WAS a single daily 09:30 cron, which silently stopped closing the audit loop
    # whenever the daemon was not alive at 09:30 (verified stale 06-13..06-22 while
    # the daemon cycled through frequent restarts). The decision->settlement audit
    # loop is the mandate's spine ("EVERY real chain decision audited with reality"),
    # so it must run continuously AND on every restart, not once a day. next_run_time
    # ~2min after boot grades on every daemon start; interval=30min keeps it current.
    # Grades each settled position into a skill category (SKILL_WIN / LUCKY_WIN /
    # SKILL_LOSS / MISCALIBRATED_LOSS / STALE_DECISION / UNATTRIBUTABLE_Q_MISSING) so
    # a lucky win can no longer fake system health (operator 2026-06-12 law). Skill is
    # attributed off the immutable decision-q certificate; an unresolvable cert grades
    # UNATTRIBUTABLE_Q_MISSING (2026-06-21). Idempotent per position; backfills history
    # on first run; also runs the settlement->audit pnl/outcome writeback. Sole writer
    # of settlement_attribution. (2026-06-22: cron->interval, consult REQ-20260622-021129.)
    scheduler.add_job(
        _settlement_skill_attribution_tick, "interval", minutes=30,
        id="settlement_skill_attribution", max_instances=1, coalesce=True,
        next_run_time=_utc_run_time_after(120.0),
    )

    # Boot-time fail-closed cascade-liveness contract check. MUST run AFTER
    # all scheduler.add_job calls so it sees the complete job set, and
    # BEFORE scheduler.start() so a contract violation prevents booting.
    _assert_cascade_liveness_contract(scheduler)

    # Phase 3: K2 ingest jobs removed from this scheduler block.
    # All K2 ticks, etl_recalibrate, ecmwf_open_data, automation_analysis,
    # hole_scanner, startup_catch_up, source_health_probe, drift_detector,
    # ingest_status_rollup, and harvester_truth_writer are now owned by
    # com.zeus.data-ingest (src/ingest_main.py).
    # See design §5 Phase 3 and antibody #8 (tests/test_main_module_scope.py).

    jobs = [j.id for j in scheduler.get_jobs()]
    logger.info("Scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus shutting down")
        scheduler.shutdown(wait=True)  # U7: wait=True so inflight cycles commit before exit


if __name__ == "__main__":
    main()
