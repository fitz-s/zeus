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
# Last reused/audited: 2026-06-05
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
import subprocess
import sys
import threading
import time
import faulthandler
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
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
_market_discovery_lock = threading.Lock()
_market_substrate_refresh_lock = threading.Lock()
_held_position_monitor_active = threading.Event()
_held_position_monitor_bootstrap_complete = threading.Event()
_HELD_POSITION_MONITOR_DEFER_JOBS = frozenset(
    {
        "market_discovery",
        "afternoon_snapshot_capture",
        "EDLI mainstream warm",
    }
)
_market_discovery_last_completed_monotonic: float | None = None
OPENING_HUNT_FIRST_DELAY_SECONDS = 30.0
HELD_POSITION_MONITOR_FIRST_DELAY_SECONDS = 5.0
# Fitz #5 scheduler-liveness (2026-06-08): the EDLI market-substrate warm cycle's
# APScheduler interval. The refresh wall-clock budget
# (ZEUS_REACTOR_REFRESH_BUDGET_SECONDS, _refresh_pending_family_snapshots) MUST be
# strictly less than this so a cycle finishes before its next trigger; otherwise
# max_instances=1 skips every overlapping run ("maximum number of running instances
# reached"), the executable substrate is never refreshed, and the armed daemon is
# starved of candidates. The interval also stays within the 180s executable-price
# freshness window. The invariant is asserted at job registration.
_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS = 20.0

# FUNNEL-STARVATION FIX (2026-06-09): rotating cursor for the pending-family
# substrate refresh. The warm cycle cannot reconstruct all live families in one
# wall-clock budget (~150 families × ~225ms ≈ 34s > 17s budget), so each cycle
# refreshes a SLICE; this offset advances by the slice size actually processed so
# consecutive cycles cover disjoint families and the whole live set is swept
# within a bounded number of cycles. See _refresh_pending_family_snapshots.
_SUBSTRATE_REFRESH_CURSOR = 0
# FUTURE-NOT-LISTED WARM-BACKOFF (2026-06-15, #122): family_key -> monotonic
# deadline until which a NO-topology family whose Gamma slug lookup returned an
# EMPTY event list (no Polymarket market listed yet for a future target_date) is
# NOT re-probed. Stops not-yet-listed future families (measured 200/200 such were
# next-day lows/highs probed the day before listing) from clogging the bounded
# Gamma time-box every ~20s warm cycle and starving CLOB capture of families that
# DO have topology — the fresh_executable_city_count 0-oscillation. Module-global
# (mirrors _SUBSTRATE_REFRESH_CURSOR); resets on restart (cold re-warm is fine).
_GAMMA_EMPTY_BACKOFF_UNTIL: dict[tuple[str, str, str], float] = {}
# New-listing scout (FIX 3c): condition_ids discovered by the 60s scout that have
# not yet been seen at the head of the substrate-warmer rotation.  The warmer
# reads + clears this set and prepends matching families so new markets are warmed
# immediately rather than waiting for normal round-robin rotation.
_NEW_FAMILY_CONDITION_IDS: set[str] = set()
# Condition_ids already known at last scout probe — used for diff.
_SCOUT_KNOWN_CONDITION_IDS: set[str] = set()

# Wave-2 item 5 (2026-06-12): the canary live mode is COLLAPSED. Canary
# semantics (min-fill-count + promotion-artifact qualifying lane) were deleted
# in 5e1e7efd76; "edli_live" is now the ONLY event-driven live mode. The old
# "edli_live_canary" string is no longer an admissible config value — it is
# mapped to "edli_live" at the read boundary (_live_execution_mode) so any
# persisted rows/receipts carrying the historical string remain readable.
_LEGACY_LIVE_EXECUTION_MODE_ALIASES = {
    "edli_live_canary": "edli_live",
}
LIVE_EXECUTION_MODES = {
    "legacy_cron",
    "edli_shadow_no_submit",
    "edli_submit_disabled_bridge",
    "edli_live",
    "disabled",
}
EDLI_EVENT_DRIVEN_MODES = {
    "edli_shadow_no_submit",
    "edli_submit_disabled_bridge",
    "edli_live",
}
REACTOR_MODE_BY_LIVE_STAGE = {
    "legacy_cron": "disabled",
    "disabled": "disabled",
    "edli_shadow_no_submit": "live_no_submit",
    "edli_submit_disabled_bridge": "submit_disabled_live_bridge",
    "edli_live": "live",
}
# Admissible edli_live_scope values. `forecast_only` is the PR-332 scope (day0
# OUT — day0 flags crash). `day0_shadow` ADMITS day0 (mask runs, shadow certs
# produced) but carries NO submit authority of its own — it is orthogonal to the
# arm axis (real_order_submit_enabled), so day0 candidates fall through the same
# not-armed block as everything else. `forecast_plus_day0` ADMITS day0 AND lets
# day0-lane events PASS the DAY0_SCOPE_SHADOW_ONLY adapter boundary (real submit
# is then subject to all other proofs/gates/arm) — operator directive 2026-06-09
# ('全部打开'): shadow-only strategies never self-promote, so the purgatory gate
# is opened. Any other value fails closed at boot.
EDLI_LIVE_SCOPES = frozenset({"forecast_only", "day0_shadow", "forecast_plus_day0"})
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
# Surface-freshness reasons that writers populate AFTER daemon boot.  In shadow
# mode these are expected-absent at boot-time and must WARN rather than prevent
# startup — the scheduler refreshes them once the first cycle runs.  All other
# reason prefixes (LOADED_SHA, UNRESOLVED_SUBMIT, LIVE_CAP_RESERVED) still
# block shadow boot because they reflect live-money or identity risk.
_EDLI_SHADOW_DEFERRED_REASON_PREFIXES = (
    "EDLI_STAGE_SOURCE_HEALTH_STALE",
    "EDLI_STAGE_SOURCE_HEALTH_MISSING",
    "EDLI_STAGE_STATUS_SUMMARY_STALE",
    "EDLI_STAGE_STATUS_SUMMARY_MISSING",
)
_EDLI_LIVE_BOOT_DEFERRED_REASON_PREFIXES = (
    "EDLI_STAGE_STATUS_SUMMARY_STALE",
    "EDLI_STAGE_STATUS_SUMMARY_MISSING",
)
REQUIRED_STAGE_FILES_BY_MODE = {
    "edli_submit_disabled_bridge": (
        "edli_stage_loaded_sha_file",
        "edli_stage_source_health_json",
        "edli_stage_status_json",
    ),
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
    # Wave-2 item 5: map the historical canary mode string to its collapsed
    # successor so persisted config/receipt data carrying the old value remains
    # readable instead of failing closed on UNSUPPORTED_LIVE_EXECUTION_MODE.
    mode = _LEGACY_LIVE_EXECUTION_MODE_ALIASES.get(mode, mode)
    if mode not in LIVE_EXECUTION_MODES:
        raise ValueError(f"UNSUPPORTED_LIVE_EXECUTION_MODE:{mode}")
    return mode


def _harvester_should_register(live_execution_mode: str) -> bool:
    """Whether the settlement P&L + redeem-intent resolver (_harvester_cycle) is
    scheduled for this live-execution mode.

    守護 blocker (2026-06-03): the harvester was gated to ``legacy_cron`` ONLY, so
    in EDLI event-driven modes (edli_shadow_no_submit, edli_submit_disabled_bridge,
    edli_live) a FILLED position that rode to market settlement
    sat phase=active forever — the redeem pollers (its consumers) had nothing to
    consume, and capital stayed stuck on-chain (memory #56 "settled-target-still-
    active", reproducing on Shanghai cca68b44).

    The resolver is shadow-safe: ``resolve_pnl_for_settled_markets`` READS VERIFIED
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
    FATAL threshold: 21 days (unless ZEUS_FREEZE_GUARD_DISABLE=1).
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
        results.append(("frozen_as_of_staleness", True, "frozen_as_of absent or within 21d — OK"))
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
      world_db_schema    — assert_schema_current (structural no-op) + canonical table presence
      forecasts_db_schema — assert_schema_current_forecasts (structural no-op) + canonical table presence
      world_registry     — assert_db_matches_registry(WORLD)
      forecasts_registry — assert_db_matches_registry(FORECASTS)
    """
    import sqlite3 as _sqlite3

    from src.state.db import (
        ZEUS_WORLD_DB_PATH,
        ZEUS_FORECASTS_DB_PATH,
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

    # Forecasts registry
    try:
        if not ZEUS_FORECASTS_DB_PATH.exists():
            raise FileNotFoundError(f"{ZEUS_FORECASTS_DB_PATH} does not exist")
        conn = _ro_conn(ZEUS_FORECASTS_DB_PATH)
        try:
            assert_db_matches_registry(conn, DBIdentity.FORECASTS)
            results.append(("forecasts_registry", True, "forecasts table-set matches registry — OK"))
        finally:
            conn.close()
    except Exception as exc:
        results.append(("forecasts_registry", False, str(exc)))

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
    if stage == "edli_submit_disabled_bridge":
        return EdliStageReadiness(stage=stage, status=EDLI_STAGE_PASS, live_entries_allowed=False)
    if stage == "edli_shadow_no_submit":
        return EdliStageReadiness(stage=stage, status=EDLI_STAGE_PASS, live_entries_allowed=False)
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
        loaded_sha_file=str(edli_cfg.get("edli_stage_loaded_sha_file") or ""),
        promotion_artifact_path=str(edli_cfg.get("edli_live_promotion_artifact_path") or ""),
        source_health_json=str(edli_cfg.get("edli_stage_source_health_json") or ""),
        status_json=str(edli_cfg.get("edli_stage_status_json") or ""),
        max_age_seconds=int(edli_cfg.get("edli_stage_readiness_max_age_seconds", 15 * 60)),
    )
    if stage in {"edli_shadow_no_submit", "edli_submit_disabled_bridge"}:
        if report.live_entries_allowed:
            raise RuntimeError("EDLI_STAGE_READINESS_FAILED:live_entries_not_allowed_in_shadow")
        if report.status not in {EDLI_STAGE_PASS, EDLI_STAGE_WAITING}:
            # Partition reasons: surface-freshness (writers populate after boot,
            # deferred in shadow) vs hard blockers (identity/risk, always fatal).
            blocking = [
                r for r in (report.reasons or ())
                if not r.startswith(_EDLI_SHADOW_DEFERRED_REASON_PREFIXES)
            ]
            deferred = [
                r for r in (report.reasons or ())
                if r.startswith(_EDLI_SHADOW_DEFERRED_REASON_PREFIXES)
            ]
            if blocking:
                raise RuntimeError(
                    "EDLI_STAGE_READINESS_FAILED:" + ",".join(blocking)
                )
            if deferred:
                logger.warning(
                    "EDLI shadow boot: stage surfaces stale/absent (expected "
                    "pre-first-cycle); will refresh after scheduler starts: %s",
                    ", ".join(deferred),
                )
        return report
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
    if age < 0 or age > max_age_seconds:
        return [f"EDLI_STAGE_{name}_STALE:{age:.0f}s"]
    return []


def _assert_edli_live_scope(edli_cfg: dict) -> None:
    scope = str(edli_cfg.get("edli_live_scope") or "forecast_only")
    if scope not in EDLI_LIVE_SCOPES:
        raise RuntimeError(f"UNSUPPORTED_EDLI_LIVE_SCOPE:{scope}")
    # day0_shadow ADMITS day0 (the absorbing-boundary mask runs and shadow
    # certificates are produced) but grants NO submit authority of its own:
    # edli_live_scope is independent of the arm axis (real_order_submit_enabled),
    # so a day0 candidate routes through the SAME not-armed block as any other
    # event (reactor.py EDLI_REAL_ORDER_SUBMIT_DISABLED / NO_SUBMIT) unless the
    # operator separately arms. forecast_plus_day0 ALSO admits day0 (operator
    # directive 2026-06-09); it additionally lets day0-lane events PASS the
    # DAY0_SCOPE_SHADOW_ONLY adapter boundary — real submit is then subject to
    # all other proofs/gates/arm. forecast_only stays byte-identical: day0 flags
    # on under forecast_only still crash with DAY0_OUT_OF_SCOPE_FOR_PR332.
    if scope in ("day0_shadow", "forecast_plus_day0"):
        return
    if bool(edli_cfg.get("day0_extreme_trigger_enabled", False)) or bool(
        edli_cfg.get("day0_hard_fact_live_enabled", False)
    ):
        raise RuntimeError("DAY0_OUT_OF_SCOPE_FOR_PR332")


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
    submit calls made this cycle — 0 in no-submit / degraded cycles. Dashboards
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


def _assert_emos_ci_license_seasonal_coverage(edli_cfg: dict) -> None:
    """Season-pin boot guard for the EMOS-CI live override (#90 pattern).

    When edli_emos_ci_live_enabled is True, every LICENSED city must have an
    emos_calibration cell for its CURRENT (city, season) served == "emos".
    A season rollover (e.g. JJA→SON) can otherwise silently make the override
    serve an uncalibrated EMOS lcb — the same class of defect as antibody #90.

    Uncovered licensed cities are DROPPED from the effective in-process license
    (the cache the live override reads), with a WARN. This is FAIL-CLOSED: an
    uncovered city falls back to the proven MC lcb rather than serving a wrong CI.
    A WARN (not a fatal raise) keeps every OTHER covered licensed city live and
    keeps the daemon up across a season boundary — the override is per-city and
    flag-gated, so a single uncovered city is not a launch blocker.

    Default OFF: when the flag is False the override never fires and this guard
    is a no-op.
    """
    if not bool(edli_cfg.get("edli_emos_ci_live_enabled", False)):
        return
    try:
        from src.calibration.emos_ci_license import load_emos_ci_license
        from src.calibration.emos import load_emos_table, emos_season, emos_cell_key
    except Exception as exc:  # pragma: no cover — import wiring
        logger.warning("EMOS-CI license boot guard import failed (override left disabled): %s", exc)
        return

    license_map = load_emos_ci_license()  # cached dict mutated in place to drop uncovered cities
    if not license_map:
        logger.warning(
            "EMOS-CI live override ENABLED but license is empty — override is a no-op "
            "(no city licensed). Operator must populate state/emos_ci_license.json."
        )
        return

    table = load_emos_table()
    cells = table.get("cells", {}) if isinstance(table, dict) else {}
    today = datetime.now(timezone.utc).date().isoformat()

    # EMOS-CI license is HIGH-metric (the override replaces the MC q_5pct on the HIGH q_lcb).
    # Canonical NH-month season + 3-key lookup: a hemisphere-aware season SH-flips and a 2-key
    # lookup misses the metric-keyed table — both silently drop the whole license (C1/C2 fix).
    dropped: list[str] = []
    for city in list(license_map.keys()):
        season = emos_season(today)
        cell = cells.get(emos_cell_key(city, season, "high"))
        served = str(cell.get("served", "")) if isinstance(cell, dict) else ""
        if served != "emos":
            dropped.append(f"{city}|{season}|high(served={served or 'missing'})")
            del license_map[city]

    if dropped:
        logger.warning(
            "EMOS-CI live override: %d licensed city(ies) lack an served==emos cell for the "
            "current season — DROPPED from the effective license (fail-closed, MC lcb stands): %s",
            len(dropped), ", ".join(dropped),
        )
    covered = sorted(license_map.keys())
    logger.info(
        "EMOS-CI live override ENABLED; effective licensed cities (season-covered): %s",
        covered if covered else "(none)",
    )


def _assert_calibration_coverage_contract(edli_cfg: dict) -> None:
    """Antibody #90: loud per-city bias+Platt calibration-coverage guard.

    This guard is for the legacy bias/Platt calibration path. When the EDLI
    reactor is in the EMOS-sole regime, non-day0 decisions do not consume that
    path: they serve EMOS, honest raw with calibrated sigma, or pure raw analytic
    with ``members_already_corrected=True``. In that regime, candidate-level
    EMOS/floor failures remain fail-closed at the q seam, but the legacy
    bias/Platt boot detector must not block armed boot on unused substrates.

    For EVERY live runtime city × metric, assert the current-season bias row
    (VERIFIED edli_per_city_v1) AND a non-borrowed/non-identity-by-starvation
    Platt exist.  Any city that would silently fall to RAW bias / borrow a
    foreign-cluster Platt / fall to identity is enumerated LOUDLY.

    SEVERITY (gated solely on real_order_submit_enabled):
      * SHADOW (real_order_submit_enabled=False): WARN-only, never raises, never
        starves the reactor — today's behaviour is byte-identical except new
        warning log lines.
      * ARMED (real_order_submit_enabled=True): raises CalibrationCoverageError
        (fail-closed — arming with silent partial calibration is forbidden).

    Read-only (SELECT on the world DB).  Wrapped so an UNEXPECTED import/probe
    error never blocks the SHADOW daemon (fail-open on infra error in shadow);
    in ARMED mode an unexpected error is re-raised (fail-closed) rather than
    masking a coverage check.
    """
    if bool(edli_cfg.get("edli_emos_sole_calibrator_enabled", False)):
        logger.info(
            "CALIBRATION_COVERAGE_SKIPPED_EMOS_SOLE: legacy bias/Platt guard "
            "not applicable while EDLI q source is EMOS/honest-raw sole calibrator"
        )
        return

    armed = bool(edli_cfg.get("real_order_submit_enabled", False))
    try:
        from src.observability.calibration_coverage_guard import (
            assert_calibration_coverage,
        )
    except Exception as exc:  # pragma: no cover — import wiring
        if armed:
            raise
        logger.warning(
            "calibration-coverage guard import failed (shadow, ignored): %s", exc
        )
        return
    try:
        assert_calibration_coverage(armed=armed)
    except Exception:
        # CalibrationCoverageError (armed) and any unexpected probe error must
        # surface when armed; in shadow only the WARN logging matters and the
        # guard itself never raises for gaps — re-raise only when armed.
        if armed:
            raise
        logger.warning(
            "calibration-coverage guard probe error (shadow, ignored)", exc_info=True
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
    if mode == "edli_shadow_no_submit" and bool(edli_cfg.get("real_order_submit_enabled", False)):
        raise RuntimeError("EDLI_SHADOW_NO_SUBMIT_FORBIDS_REAL_ORDER_SUBMIT")
    if mode == "edli_submit_disabled_bridge":
        _require_edli_flags(edli_cfg, mode, ("market_channel_ingestor_enabled", "edli_user_channel_reconcile_enabled"))
        if bool(edli_cfg.get("real_order_submit_enabled", False)):
            raise RuntimeError("EDLI_SUBMIT_DISABLED_BRIDGE_FORBIDS_REAL_ORDER_SUBMIT")
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


@_scheduler_job("harvester")
def _harvester_cycle():
    """Phase 1.5 harvester split: trading-side P&L resolver.

    Reads forecasts.settlements (written by ingest-side harvester_truth_writer)
    and settles positions + writes decision_log. If the resolver is unavailable,
    fail closed; the trading daemon must not fall back to the legacy integrated
    harvester path, which can derive and write settlement truth in the same lane.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.state.db import get_trade_connection, get_forecasts_connection
    with acquire_lock("harvester_pnl") as acquired:
        if not acquired:
            logger.info("harvester_pnl_resolver skipped_lock_held")
            return
        try:
            from src.execution.harvester_pnl_resolver import resolve_pnl_for_settled_markets
            # v4 plan §AX3: harvester PnL resolver = LIVE class.
            # K1 (2026-05-11): settlements → zeus-forecasts.db; pass forecasts conn.
            trade_conn = get_trade_connection(write_class="live")
            forecasts_conn = get_forecasts_connection(write_class="live")
            try:
                result = resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)
            finally:
                trade_conn.close()
                forecasts_conn.close()
        except ImportError as exc:
            logger.error(
                "harvester_pnl_resolver unavailable; refusing legacy run_harvester fallback: %s",
                exc,
            )
            result = {
                "status": "resolver_unavailable_fail_closed",
                "positions_settled": 0,
                "decision_log_rows_written": 0,
                "errors": 1,
            }
    logger.info("Harvester: %s", result)


@_scheduler_job("wu_daily")
def _wu_daily_dispatch() -> None:
    """K2 WU daily scheduler tick — collect WU daily observations for eligible cities.

    Called hourly by the daemon scheduler. WuDailyScheduler.should_collect_now
    gates collection per city using a window_minutes=60 default, so each city
    fires at most once per hour at its configured local trigger time.

    Cluster L wiring per G4_CLEANUP_DESIGN.md §2 L (2026-05-18).
    K2 import (daily_obs_append) lives in wu_scheduler.run_wu_daily_dispatch
    to keep src.main free of K2 ingest modules (Phase 3 boundary, antibody #8).
    Operator may override interval post-merge if cadence needs tuning.
    """
    from src.data.wu_scheduler import run_wu_daily_dispatch

    run_wu_daily_dispatch()


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


@_scheduler_job("shadow_comparator")
def _shadow_comparator_tick() -> None:
    """Daily standing shadow-vs-live comparator (operator-approved, K<<N organ).

    The ONE comparator every promotion uses instead of a bespoke harness. For
    each registered shadow candidate (immediate customer: day0_remaining_day_q),
    it READS the persisted shadow + live q for each settled cohort cell, grades
    both against the VERIFIED settlement via grade_receipt (the ONE Direction-Law
    truth function — never recomputes domain logic), and emits a running
    scoreboard with a PROMOTE_SUPPORTED | LIVE_BETTER | INSUFFICIENT_N verdict:
      - state/shadow_comparator.json (machine)
      - docs/evidence/shadow_comparisons/<date>_shadow_comparison.md (human)
    plus a one-line INFO verdict per candidate in this daemon's log.

    Co-located with the settlement-guard tick (same WORLD+forecasts read shape,
    same daily cadence). Read-only; honest absence (INSUFFICIENT_N) when a
    candidate's shadow lane is not yet persisting a comparable q — never a
    fabricated cohort. Import is local to keep src.main import-light.
    """
    from src.analysis.shadow_comparator import run_shadow_comparator_job

    report = run_shadow_comparator_job()
    for cand in report.get("candidates", []):
        logger.info("shadow_comparator[%s]: %s", cand["name"], cand["verdict_line"])


@_scheduler_job("day0_shadow_enrichment")
def _day0_shadow_enrichment_tick() -> None:
    """Grade SETTLED candidate-bearing day0 shadow receipts against VERIFIED truth.

    Closes the day0-evidence gap (operator 2026-06-11): later_outcome /
    would_have_won were 0% populated on the day0 lane because no writer existed.
    This tick joins no_trade_regret_events (day0 shadow rows carrying
    direction + bin_label) to VERIFIED forecasts.settlement_outcomes, grades each
    through the canonical grade_receipt (Direction Law + HK preimage), and writes
    the outcome via NoTradeRegretLedger.enrich_after_settlement. PURE-DB (no
    network) and NEVER-SUBMIT / NEVER-FABRICATE: only already-candidate-bearing
    receipts with a VERIFIED settlement are graded. Co-located with the
    shadow-comparator tick (same WORLD+forecasts read shape, same cadence).
    """
    from src.analysis.day0_shadow_enrichment import run_day0_shadow_enrichment_job

    report = run_day0_shadow_enrichment_job()
    logger.info(
        "day0_shadow_enrichment: status=%s enriched=%s",
        report.get("status"), report.get("enriched", report.get("error")),
    )


@_scheduler_job("settlement_skill_attribution")
def _settlement_skill_attribution_tick() -> None:
    """Grade every SETTLED position into a skill category (operator 2026-06-12 law).

    A profitable settlement is NOT proof of skill. This tick grades each settled
    position into SKILL_WIN / LUCKY_WIN / SKILL_LOSS / MISCALIBRATED_LOSS /
    STALE_DECISION by comparing our position + decision-time q + the freshest
    settlement-eve posterior + the settled outcome + market price. A LUCKY_WIN
    (won but our own freshest data disagreed — the Denver-if-92 shape) counts as a
    MISS so a lucky win can no longer masquerade as system health. The skill
    win-rate = SKILL_WIN / (SKILL_WIN + LUCKY_WIN + SKILL_LOSS + MISCALIBRATED_LOSS).

    Runs after the settlement harvesting + day0 enrichment ticks (settlement truth
    already landed). Idempotent per position (UNIQUE(position_id)); backfills every
    historically-settled position on first run. Sole writer of
    settlement_attribution. Import local to keep src.main import-light.
    """
    from src.analysis.settlement_skill_attribution import run_settlement_skill_attribution

    stats = run_settlement_skill_attribution()
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


@_scheduler_job("redeem_submitter")
def _redeem_submitter_cycle() -> None:
    """Poll settlement_commands for ALL _SUBMITTABLE_STATES rows + submit_redeem.

    PR #126 review-fix (Codex P1 + Copilot 3254021478): poll the full
    _SUBMITTABLE_STATES set (INTENT_CREATED + RETRYING), not just INTENT_CREATED.
    Without RETRYING in the query, rows that hit an adapter exception once
    and were durably moved to RETRYING by submit_redeem would never be
    re-attempted.

    PR #126 review-fix (Codex P1 + Copilot 3254021447/49): commit AFTER each
    submit_redeem call. submit_redeem only commits when own_conn=True; the
    poller passes conn=conn so own_conn=False; without an explicit commit
    the state transitions roll back when conn closes → INTENT_CREATED rows
    are re-processed every tick AND any real adapter tx_hash is not durably
    anchored. Per-row commit gives partial-failure tolerance.
    """
    # REDEEM SUBMISSION FORBIDDEN (operator law 2026-06-10, ABSOLUTE): the
    # scheduler NEVER drives redeem submission. redeem_submission_allowed() is
    # now unconditionally False (the operator-override escape hatch was deleted),
    # and submit_redeem / adapter.redeem each hard-raise REDEEM_SUBMISSION_FORBIDDEN
    # as deeper defense layers. This cycle is a no-op that logs the law once per
    # process so an operator scanning logs sees WHY redemption never runs here.
    from src.execution.settlement_commands import redeem_submission_allowed

    if not redeem_submission_allowed():
        global _REDEEM_SUBMITTER_LAW_LOGGED
        if not _REDEEM_SUBMITTER_LAW_LOGGED:
            logger.info(
                "redeem_submitter: SKIPPED — redeem submission FORBIDDEN (operator "
                "law 2026-06-10). Redemption is EXTERNAL; Zeus books "
                "EXTERNAL_REDEMPTION and never submits a redeem tx."
            )
            _REDEEM_SUBMITTER_LAW_LOGGED = True
        return
    from src.data.dual_run_lock import acquire_lock
    from src.data.polymarket_client import (
        resolve_polymarket_credentials,
        _resolve_clob_v2_signature_type,
        _resolve_q1_egress_evidence_path,
    )
    from src.execution.settlement_commands import (
        _SUBMITTABLE_STATES,
        submit_redeem,
    )
    from src.state.db import get_trade_connection
    from src.venue.polymarket_v2_adapter import (
        DEFAULT_Q1_EGRESS_EVIDENCE,
        DEFAULT_POLYGON_RPC_URL,
        DEFAULT_V2_HOST,
        PolymarketV2Adapter,
        Q1_EGRESS_EVIDENCE_ENV,
    )

    # PR-I.5.b — Karachi unblock prep (2026-05-18):
    # Paper/dry-run skips cleanly; live mode requires keychain credentials
    # before any adapter is constructed. The redeem adapter MUST share the
    # same credential source as the entry adapter (polymarket_client._ensure_v2_adapter)
    # to avoid the "structural decision incompletely executed" pattern:
    # different credential paths for entry vs redeem = silent drift hazard.
    #
    # Codex P2 fix (PR #145): credential lookup is deferred until AFTER the
    # empty-row check so that an idle daemon with no REDEEM_INTENT_CREATED /
    # REDEEM_RETRYING rows does NOT mark _scheduler_job FAILED every 5 min
    # merely because Keychain is unavailable at that moment.
    # Fail-closed still applies: if work exists and creds are missing, raise.
    if get_mode() != "live":
        logger.info("redeem_submitter skipped_non_live mode=%s", get_mode())
        return

    with acquire_lock("redeem_submitter") as acquired:
        if not acquired:
            logger.info("redeem_submitter skipped_lock_held")
            return
        conn = get_trade_connection(write_class="live")
        try:
            from src.execution.settlement_commands import reseat_stub_deferred_rows_for_autonomous_retry
            promoted = reseat_stub_deferred_rows_for_autonomous_retry(conn)
            if promoted > 0:
                conn.commit()
                logger.info(
                    "redeem_submitter: promoted %d stub-deferred rows to RETRYING",
                    promoted,
                )

            def _build_adapter(creds_: dict) -> PolymarketV2Adapter:
                q1_egress_evidence = _resolve_q1_egress_evidence_path(
                    default=DEFAULT_Q1_EGRESS_EVIDENCE,
                    env_name=Q1_EGRESS_EVIDENCE_ENV,
                )
                return PolymarketV2Adapter(
                    host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
                    funder_address=creds_["funder_address"],
                    signer_key=creds_["private_key"],
                    chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
                    signature_type=_resolve_clob_v2_signature_type(),
                    polygon_rpc_url=os.environ.get(
                        "POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL
                    ),
                    api_creds=creds_.get("api_creds"),
                    q1_egress_evidence_path=q1_egress_evidence,
                )

            # ── Inventory-truth auto-collect sweep (2026-06-09) ──────────────
            # Operator directive: the system must auto-collect with no operator
            # hands. The redeem trigger is the Safe's ACTUAL chain inventory
            # (data-api positions + per-candidate chain-truth verification),
            # NEVER the internal portfolio ledger — the ledger missed real
            # winners (pending_exit/admin_closed phases) and could not see
            # ledger-invisible holdings (London-16C YES ~$798, zero
            # position_current rows). See src/execution/inventory_redeem_sweep.py.
            # Fail-soft: any sweep failure logs and defers to the next tick;
            # the submit loop below is unaffected. Kill switch:
            # ZEUS_INVENTORY_REDEEM_SWEEP_DISABLED=1.
            creds = None
            adapter = None
            _sweep_disabled = os.environ.get(
                "ZEUS_INVENTORY_REDEEM_SWEEP_DISABLED", ""
            ).strip().lower() in ("1", "true", "yes", "on")
            if not _sweep_disabled:
                try:
                    creds = resolve_polymarket_credentials()
                except RuntimeError:
                    # Idle daemon without Keychain stays quiet (Codex P2 PR #145
                    # posture preserved): only raise when submit work exists.
                    creds = None
                if creds is not None:
                    from src.execution.inventory_redeem_sweep import (
                        sweep_chain_inventory_for_redeems,
                    )
                    adapter = _build_adapter(creds)
                    try:
                        swept = sweep_chain_inventory_for_redeems(
                            conn, adapter, creds["funder_address"],
                        )
                    except Exception as exc:  # noqa: BLE001 — fail-soft per tick
                        logger.warning(
                            "redeem_submitter: inventory sweep failed (fail-soft): %s",
                            exc,
                        )
                        swept = []
                    if swept:
                        conn.commit()
                        logger.info(
                            "redeem_submitter: inventory sweep enqueued/active %d command(s)",
                            len(swept),
                        )
                    # Proceeds-driven wrap (same tick): any USDC.e already
                    # sitting at the Safe from earlier confirmed redemptions is
                    # wrapped to pUSD NOW, not left for the slow periodic
                    # balance poll. Fail-soft inside.
                    _wrap_proceeds_same_tick(creds, adapter)
            # Poll ALL submittable states (INTENT_CREATED + RETRYING).
            placeholders = ",".join("?" * len(_SUBMITTABLE_STATES))
            state_values = tuple(s.value for s in _SUBMITTABLE_STATES)
            rows = conn.execute(
                f"""
                SELECT command_id FROM settlement_commands
                 WHERE state IN ({placeholders})
                 ORDER BY requested_at, command_id
                 LIMIT 32
                """,
                state_values,
            ).fetchall()
            if not rows:
                return
            # Credentials resolved only when actual work exists — fail-closed:
            # if Keychain is unavailable here, raise so the scheduler records
            # FAILED and the operator sees a clear provisioning gap.
            if creds is None:
                try:
                    creds = resolve_polymarket_credentials()
                except RuntimeError as exc:
                    raise RuntimeError(
                        f"redeem_submitter: credentials unavailable (fail-closed): {exc}"
                    ) from exc
            if adapter is None:
                adapter = _build_adapter(creds)
            submitted = 0
            failed = 0
            for row in rows:  # already capped at 32 via SQL LIMIT
                try:
                    result = submit_redeem(
                        row["command_id"], adapter, object(), conn=conn,
                    )
                    conn.commit()  # durable per-row commit; transitions stick
                    submitted += 1
                    logger.info(
                        "redeem_submitter: command_id=%s state=%s",
                        row["command_id"], result.state.value,
                    )
                except Exception as exc:  # noqa: BLE001 — fail-open per scheduler contract
                    # On exception submit_redeem may have committed an intermediate
                    # REDEEM_RETRYING via its own savepoint+commit (own_conn path
                    # closed it); for own_conn=False we still rollback in-flight
                    # uncommitted savepoints by closing the conn cleanly. Per-row
                    # rollback isolates failures from successful prior rows.
                    try:
                        conn.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    failed += 1
                    logger.error(
                        "redeem_submitter: command_id=%s error=%s",
                        row["command_id"], exc,
                    )
            logger.info(
                "redeem_submitter: submitted=%d failed=%d", submitted, failed,
            )
            if failed:
                raise RuntimeError(
                    f"redeem_submitter: submitted={submitted} failed={failed}"
                )
        finally:
            conn.close()


@_scheduler_job("redeem_reconciler")
def _redeem_reconciler_cycle() -> None:
    """Poll REDEEM_TX_HASHED rows + reconcile_pending_redeems against web3.

    PR-I.5 completion (2026-05-19): wires Web3 HTTPProvider + calls
    reconcile_pending_redeems so the antibody guard merged in PR #192 is
    reachable in production.  Karachi anchor: tx 0x0c85d94… (negRisk market
    c8c220f5…) sitting in REDEEM_TX_HASHED since 2026-05-19T08:26 UTC.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.execution.settlement_commands import (
        SettlementState,
        list_commands,
        reconcile_pending_redeems,
    )
    from src.state.db import get_trade_connection
    from src.venue.polymarket_v2_adapter import DEFAULT_POLYGON_RPC_URL

    if get_mode() != "live":
        logger.info("redeem_reconciler skipped_non_live mode=%s", get_mode())
        return

    with acquire_lock("redeem_reconciler") as acquired:
        if not acquired:
            logger.info("redeem_reconciler skipped_lock_held")
            return
        conn = get_trade_connection(write_class="live")
        try:
            rows = list_commands(conn, state=SettlementState.REDEEM_TX_HASHED)
            if not rows:
                logger.info("redeem_reconciler: results=0")
                return
            try:
                from web3 import Web3
            except ImportError:
                logger.info(
                    "redeem_reconciler: web3 not installed; rows=%d sitting in "
                    "TX_HASHED (expected pre-PR-I.5)", len(rows),
                )
                return
            polygon_rpc_url = os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)
            w3 = Web3(Web3.HTTPProvider(polygon_rpc_url, request_kwargs={"timeout": 15}))
            try:
                results = reconcile_pending_redeems(w3, conn)
                conn.commit()
                logger.info(
                    "redeem_reconciler: reconciled=%d states=%s",
                    len(results), [r.state.value for r in results],
                )
                # Proceeds-driven wrap (same tick): a REDEEM_CONFIRMED batch
                # means USDC.e proceeds just became chain-final at the Safe.
                # Wrap them NOW in this tick instead of waiting for the
                # periodic balance poll. Fail-soft: credential/adapter issues
                # log and defer to the periodic wrap jobs.
                if any(
                    r.state == SettlementState.REDEEM_CONFIRMED for r in results
                ):
                    try:
                        from src.data.polymarket_client import (
                            resolve_polymarket_credentials as _resolve_creds,
                            _resolve_clob_v2_signature_type as _sig_type,
                        )
                        from src.venue.polymarket_v2_adapter import (
                            DEFAULT_V2_HOST as _V2_HOST,
                            PolymarketV2Adapter as _V2Adapter,
                        )
                        _creds = _resolve_creds()
                        _adapter = _V2Adapter(
                            host=os.environ.get("POLYMARKET_CLOB_V2_HOST", _V2_HOST),
                            funder_address=_creds["funder_address"],
                            signer_key=_creds["private_key"],
                            chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
                            signature_type=_sig_type(),
                            polygon_rpc_url=polygon_rpc_url,
                            api_creds=_creds.get("api_creds"),
                        )
                        _wrap_proceeds_same_tick(_creds, _adapter)
                    except Exception as _wrap_exc:  # noqa: BLE001 — fail-soft
                        logger.warning(
                            "redeem_reconciler: same-tick wrap skipped: %s",
                            _wrap_exc,
                        )
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.error("redeem_reconciler: error=%s", exc)
                raise
        finally:
            conn.close()


@_scheduler_job("wrap_intent_creator")
def _wrap_intent_creator_cycle() -> None:
    """Enqueue WRAP_REQUESTED if Safe USDC.e balance > threshold and no pending row.

    On-chain balance-driven (not journal-driven). Idempotent: skips if any
    non-terminal WRAP row already exists. Skipped in non-live mode.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.polymarket_client import resolve_polymarket_credentials
    from src.execution.wrap_unwrap_commands import enqueue_wrap_if_balance_above_threshold
    from src.state.db import get_world_connection
    from src.venue.polymarket_v2_adapter import DEFAULT_POLYGON_RPC_URL

    if get_mode() != "live":
        logger.info("wrap_intent_creator skipped_non_live mode=%s", get_mode())
        return

    # P0-2 (d): single shared wrap state-machine lock (was "wrap_intent_creator").
    with acquire_lock("wrap_state_machine") as acquired:
        if not acquired:
            logger.info("wrap_intent_creator skipped_lock_held")
            return
        try:
            from web3 import Web3
        except ImportError:
            logger.info("wrap_intent_creator: web3 not installed; skipping")
            return
        # Resolve Safe address from the same Keychain-backed credential source
        # used by wrap_submitter and wrap_reconciler so all three cycles agree
        # on which Safe's balance to monitor and which Safe to transact against.
        try:
            creds = resolve_polymarket_credentials()
        except RuntimeError as exc:
            logger.warning("wrap_intent_creator: credentials unavailable, skipping: %s", exc)
            return
        safe_address = creds["funder_address"]
        if not safe_address:
            logger.warning("wrap_intent_creator: funder_address empty in credentials")
            return
        polygon_rpc_url = os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)
        w3 = Web3(Web3.HTTPProvider(polygon_rpc_url, request_kwargs={"timeout": 15}))
        conn = get_world_connection()
        try:
            command_id = enqueue_wrap_if_balance_above_threshold(
                safe_address, w3, conn,
            )
            if command_id:
                conn.commit()
                logger.info("wrap_intent_creator: enqueued command_id=%s", command_id)
            else:
                logger.debug("wrap_intent_creator: no wrap needed (threshold or pending)")
        finally:
            conn.close()


@_scheduler_job("wrap_submitter")
def _wrap_submitter_cycle() -> None:
    """Submit APPROVE tx for WRAP_REQUESTED rows; WRAP tx for WRAP_APPROVED rows.

    Each step is a separate Safe execTransaction. Skipped in non-live mode.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.polymarket_client import (
        resolve_polymarket_credentials,
        _resolve_clob_v2_signature_type,
        _resolve_q1_egress_evidence_path,
    )
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        fail_wrap,
        list_pending_wrap_commands,
        mark_wrap_approve_tx_hashed,
        mark_wrap_tx_hashed,
    )
    from src.state.db import get_world_connection
    from src.venue.polymarket_v2_adapter import (
        DEFAULT_Q1_EGRESS_EVIDENCE,
        DEFAULT_POLYGON_RPC_URL,
        DEFAULT_V2_HOST,
        PolymarketV2Adapter,
        Q1_EGRESS_EVIDENCE_ENV,
    )

    if get_mode() != "live":
        logger.info("wrap_submitter skipped_non_live mode=%s", get_mode())
        return

    # P0-2 (d): single shared wrap state-machine lock (was "wrap_submitter").
    with acquire_lock("wrap_state_machine") as acquired:
        if not acquired:
            logger.info("wrap_submitter skipped_lock_held")
            return
        conn = get_world_connection()
        try:
            rows = list_pending_wrap_commands(conn)
            actionable = [
                r for r in rows
                if r["state"] in (
                    WrapUnwrapState.WRAP_REQUESTED.value,
                    WrapUnwrapState.WRAP_APPROVED.value,
                )
            ]
            if not actionable:
                logger.debug("wrap_submitter: no actionable rows")
                return
            try:
                creds = resolve_polymarket_credentials()
            except RuntimeError as exc:
                raise RuntimeError(
                    f"wrap_submitter: credentials unavailable (fail-closed): {exc}"
                ) from exc
            q1_egress_evidence = _resolve_q1_egress_evidence_path(
                default=DEFAULT_Q1_EGRESS_EVIDENCE, env_name=Q1_EGRESS_EVIDENCE_ENV,
            )
            adapter = PolymarketV2Adapter(
                host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
                funder_address=creds["funder_address"],
                signer_key=creds["private_key"],
                chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
                signature_type=_resolve_clob_v2_signature_type(),
                polygon_rpc_url=os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL),
                api_creds=creds.get("api_creds"),
                q1_egress_evidence_path=q1_egress_evidence,
            )
            # Derive signer EOA from private_key (same as redeem flow).
            # creds["funder_address"] is the Safe proxy address, NOT an owner EOA.
            # _wrap_via_safe validates signer_eoa against Safe.getOwners(), so
            # passing funder_address would always fail with WRAP_SAFE_OWNER_MISMATCH.
            from eth_account import Account as _Account  # type: ignore[import]
            signer_eoa = _Account.from_key(creds["private_key"]).address
            submitted = 0
            failed = 0
            for row in actionable:
                command_id = row["command_id"]
                amount_micro = row["amount_micro"]
                current_state = row["state"]
                tx_kind = "APPROVE" if current_state == WrapUnwrapState.WRAP_REQUESTED.value else "WRAP"
                try:
                    result = adapter._wrap_via_safe(
                        safe_address=creds["funder_address"],
                        amount_micro=amount_micro,
                        tx_kind=tx_kind,
                        signer_eoa=signer_eoa,
                    )
                    if result.get("errorCode") == "WRAP_DRY_RUN_LOGGED":
                        logger.info(
                            "wrap_submitter: dry_run command_id=%s tx_kind=%s fingerprint=%s",
                            command_id, tx_kind, result.get("dry_run_fingerprint"),
                        )
                        continue
                    if not result.get("success"):
                        raise RuntimeError(
                            f"_wrap_via_safe failed: {result.get('errorCode')} "
                            f"{result.get('errorMessage')}"
                        )
                    tx_hash = result["tx_hash"]
                    if tx_kind == "APPROVE":
                        mark_wrap_approve_tx_hashed(
                            command_id, tx_hash, conn=conn,
                        )
                    else:
                        mark_wrap_tx_hashed(command_id, tx_hash, conn=conn)
                    conn.commit()
                    submitted += 1
                    logger.info(
                        "wrap_submitter: command_id=%s tx_kind=%s tx_hash=%s",
                        command_id, tx_kind, tx_hash,
                    )
                except Exception as exc:  # noqa: BLE001
                    try:
                        conn.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    failed += 1
                    logger.error(
                        "wrap_submitter: command_id=%s tx_kind=%s error=%s",
                        command_id, tx_kind, exc,
                    )
                    try:
                        fail_wrap(
                            command_id,
                            error_payload={"error": str(exc), "tx_kind": tx_kind},
                            conn=conn,
                        )
                        conn.commit()
                    except Exception:  # noqa: BLE001
                        pass
            logger.info("wrap_submitter: submitted=%d failed=%d", submitted, failed)
            if failed:
                raise RuntimeError(f"wrap_submitter: submitted={submitted} failed={failed}")
        finally:
            conn.close()


@_scheduler_job("wrap_reconciler")
def _wrap_reconciler_cycle() -> None:
    """Poll WRAP_APPROVE_TX_HASHED and WRAP_TX_HASHED rows; advance state on receipt.

    On WRAP_CONFIRMED, calls adapter.update_balance_allowance() to refresh CLOB ledger.
    Skipped in non-live mode.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.polymarket_client import (
        resolve_polymarket_credentials,
        _resolve_clob_v2_signature_type,
        _resolve_q1_egress_evidence_path,
    )
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        init_wrap_unwrap_schema,
        reconcile_pending_wraps,
    )
    from src.state.db import get_world_connection
    from src.venue.polymarket_v2_adapter import (
        DEFAULT_Q1_EGRESS_EVIDENCE,
        DEFAULT_POLYGON_RPC_URL,
        DEFAULT_V2_HOST,
        PolymarketV2Adapter,
        Q1_EGRESS_EVIDENCE_ENV,
    )

    if get_mode() != "live":
        logger.info("wrap_reconciler skipped_non_live mode=%s", get_mode())
        return

    # P0-2 (d): single shared wrap state-machine lock (was "wrap_reconciler").
    with acquire_lock("wrap_state_machine") as acquired:
        if not acquired:
            logger.info("wrap_reconciler skipped_lock_held")
            return
        try:
            from web3 import Web3
        except ImportError:
            logger.info("wrap_reconciler: web3 not installed; skipping")
            return
        polygon_rpc_url = os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)
        w3 = Web3(Web3.HTTPProvider(polygon_rpc_url, request_kwargs={"timeout": 15}))
        conn = get_world_connection()
        try:
            init_wrap_unwrap_schema(conn)
            reconcile_states = (
                WrapUnwrapState.WRAP_APPROVE_TX_HASHED.value,
                WrapUnwrapState.WRAP_TX_HASHED.value,
            )
            rows = conn.execute(
                "SELECT command_id FROM wrap_unwrap_commands WHERE state IN (?,?)",
                reconcile_states,
            ).fetchall()
            if not rows:
                logger.debug("wrap_reconciler: no rows to reconcile")
                return
            try:
                creds = resolve_polymarket_credentials()
            except RuntimeError as exc:
                raise RuntimeError(
                    f"wrap_reconciler: credentials unavailable (fail-closed): {exc}"
                ) from exc
            q1_egress_evidence = _resolve_q1_egress_evidence_path(
                default=DEFAULT_Q1_EGRESS_EVIDENCE, env_name=Q1_EGRESS_EVIDENCE_ENV,
            )
            adapter = PolymarketV2Adapter(
                host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
                funder_address=creds["funder_address"],
                signer_key=creds["private_key"],
                chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
                signature_type=_resolve_clob_v2_signature_type(),
                polygon_rpc_url=polygon_rpc_url,
                api_creds=creds.get("api_creds"),
                q1_egress_evidence_path=q1_egress_evidence,
            )
            try:
                results = reconcile_pending_wraps(w3, adapter, conn)
                conn.commit()
                logger.info(
                    "wrap_reconciler: reconciled=%d states=%s",
                    len(results), [r.get("state") for r in results],
                )
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.error("wrap_reconciler: error=%s", exc)
                raise
        finally:
            conn.close()


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
            import os
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
        ledger.refresh(adapter)
        return True
    except Exception as exc:
        logger.warning("CollateralLedger heartbeat refresh failed closed: %s", exc)
        return False
    finally:
        _collateral_background_refresh_lock.release()


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
        "collateral_refreshed": _refresh_global_collateral_snapshot_if_due(active_adapter),
    }


def _start_collateral_background_refresh_async(adapter=None) -> str:
    """Refresh collateral on an independent lane from slower venue maintenance."""

    if _cycle_lock.locked() or _edli_reactor_active():
        return "deferred_cycle_running"
    active_adapter = adapter or _venue_heartbeat_adapter
    if active_adapter is None:
        return "adapter_unavailable"
    if _edli_reactor_pending_backlog_exists():
        return "deferred_edli_pending_backlog"
    if _collateral_background_refresh_lock.locked():
        return "already_running"

    def _runner() -> None:
        _refresh_global_collateral_snapshot_if_due(active_adapter)

    thread = threading.Thread(
        target=_runner,
        name="collateral-background-refresh",
        daemon=True,
    )
    thread.start()
    return "started"


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
# Wave-1 2026-06-12: fixed per-cycle re-decision/screen batch fed to the WRAPPING fair
# cursor (CoverageFairnessRequest.select_rows). Replaces the deleted redecision_max_per_cycle
# settings cap. The cursor wraps modulo the family count, so this batch reaches EVERY family
# within ceil(N/batch) cycles and never silently drops the tail. Sized to sweep the full live
# family universe (~108 city×metric families) within ~2 cycles at the ~60-90s reactor cadence.
_EDLI_REDECISION_FAIR_BATCH: int = 60


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


def _edli_next_escalation_cross_source() -> str:
    """Return the next ESCALATION-cross re-decision emit source.

    Format ``escalation_cross-{TOKEN}-{N}`` — same restart-/within-process-unique
    scheme as ``_edli_next_redecision_source`` (shared boot token + monotonic N) so
    each escalation re-decision gets a distinct idempotency_key and does NOT dedup
    against the consumed FSR or the continuous ``cycle-*`` re-emit. The
    ``escalation_cross-`` PREFIX is the discriminator the claim-tier authority
    (``src.events.event_priority.claim_tier_expr_sql``) keys off to rank these
    re-decisions at Tier 0 — below the 49-deep per-city round-robin (redecide-block
    fix 2026-06-16). N has no internal hyphens, so ``split('-')[-1]`` stays an int
    for the fairness-cursor parse in scan_committed_snapshots.
    """
    from src.events.event_priority import ESCALATION_CROSS_SOURCE_PREFIX

    global _edli_redecision_cycle_index
    n = _edli_redecision_cycle_index
    _edli_redecision_cycle_index = n + 1
    return f"{ESCALATION_CROSS_SOURCE_PREFIX}{_edli_redecision_boot_token}-{n}"


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


def _auto_derive_user_channel_condition_ids(
    *,
    now: datetime | None = None,
) -> list[str]:
    """Derive the user-channel WS subscription set.

    Fresh persisted ``market_events`` rows are primary. When those rows are
    missing at boot, Gamma scanning is enabled by default so the one-shot
    user-channel starter does not latch to an empty subscription set for the
    lifetime of the live process. Operators can disable this fallback by setting
    ``ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN=0``.

    Total failure still returns [] rather than raising; the daemon then stays in
    the fail-closed WS posture recorded by the gap guard.
    """
    persisted_ids = _market_events_user_channel_condition_ids(now=now)
    if persisted_ids:
        return persisted_ids
    if os.getenv("ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN", "1").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        logger.warning(
            "user-channel WS found no fresh market_events condition_ids; "
            "boot Gamma scan disabled by ZEUS_USER_CHANNEL_BOOT_GAMMA_SCAN=0"
        )
        return []
    try:
        from src.data.market_scanner import (
            MarketEventsPersistenceError,
            extract_executable_condition_ids,
            find_weather_markets_or_raise,
        )

        events = find_weather_markets_or_raise(
            min_hours_to_resolution=0.0,
            include_slug_pattern=False,
        )
        return extract_executable_condition_ids(events)
    except MarketEventsPersistenceError as exc:
        logger.warning(
            "user-channel WS scanner: market_events persistence failure — "
            "degrading to empty condition_ids: %s", exc,
        )
        return []
    except Exception as exc:
        logger.warning("user-channel WS scanner failed: %s", exc)
        return []


def _start_user_channel_ingestor_if_enabled() -> None:
    """Start M3 Polymarket user-channel ingest in a daemon thread when enabled.

    Disabled by default so M3 adds no live WebSocket side effect until an
    operator explicitly enables `ZEUS_USER_CHANNEL_WS_ENABLED=1` and supplies
    condition IDs or enables condition auto-derive. L2 API credentials come
    from the Polymarket adapter's signer-bound SDK client, not static env. If
    enabled but misconfigured, the WS guard records an auth/config gap so new
    submits fail closed.

    Live-blockers 2026-05-01: when the WS is NOT enabled (or required env
    vars are missing) we now emit a single CLEAR WARNING line listing every
    missing var. Today the silent skip leaves operators with the cryptic
    ``ws_user_channel.gap_reason='not_configured'`` symptom and no surface
    explanation of which env vars to add to the launchd plist before the
    daemon can leave reduce_only mode.

    Auto-derive (2026-05-01): when ``ZEUS_USER_CHANNEL_WS_AUTO_DERIVE=1`` is
    set together with the master toggle and ``POLYMARKET_USER_WS_CONDITION_IDS``
    is empty, the subscription list is derived from the live market scanner
    so the daemon subscribes to exactly the markets it can trade, without
    a hardcoded plist value that would drift from on-chain truth as markets
    rotate (operator directive 2026-05-01: hardcoded values are structural
    failures). Operator can still pin a list via the env var; a non-empty
    env var always wins. Auto-derive returning 0 markets is a WARNING, not
    an error — the daemon stays in reduce_only mode, the WS guard reports
    ``condition_ids_missing``, and no exception escapes boot.
    """
    global _user_channel_ingestor, _user_channel_thread
    if not _truthy_env("ZEUS_USER_CHANNEL_WS_ENABLED"):
        missing = [
            name for name in USER_CHANNEL_REQUIRED_ENV_VARS
            if not (os.environ.get(name) or "").strip()
        ]
        logger.warning(
            "user-channel WS not configured: missing env vars %s; "
            "daemon stays in reduce_only=True mode",
            missing,
        )
        return
    if _user_channel_thread is not None and _user_channel_thread.is_alive():
        return

    raw_markets = os.environ.get("POLYMARKET_USER_WS_CONDITION_IDS", "")
    condition_ids = [m.strip() for m in raw_markets.split(",") if m.strip()]
    auto_derived = False
    if not condition_ids and _truthy_env("ZEUS_USER_CHANNEL_WS_AUTO_DERIVE"):
        condition_ids = _auto_derive_user_channel_condition_ids()
        auto_derived = True
        logger.info(
            "user-channel WS auto-derive yielded %d condition_ids "
            "(POLYMARKET_USER_WS_CONDITION_IDS empty, ZEUS_USER_CHANNEL_WS_AUTO_DERIVE=1)",
            len(condition_ids),
        )

    if not condition_ids:
        from src.control.ws_gap_guard import record_gap

        record_gap("condition_ids_missing", subscription_state="MARKET_MISMATCH")
        if auto_derived:
            logger.warning(
                "user-channel WS auto-derive yielded 0 condition_ids; daemon stays "
                "in reduce_only=True mode. Markets may be empty or the gamma query "
                "failed; check src.data.market_scanner."
            )
            return
        raise RuntimeError("POLYMARKET_USER_WS_CONDITION_IDS is required when ZEUS_USER_CHANNEL_WS_ENABLED=1")

    from src.data.polymarket_client import PolymarketClient
    from src.control.ws_gap_guard import record_gap
    from src.ingest.polymarket_user_channel import PolymarketUserChannelIngestor, WSAuth

    adapter = PolymarketClient()._ensure_v2_adapter()

    _WS_RETRY_BASE_SECONDS = 5
    _WS_RETRY_MAX_SECONDS = 300  # cap at 5 minutes

    # Boot-time transient failures from signer-bound L2 credential derivation
    # used to latch AUTH_FAILED forever because the
    # creds fetch lived outside the retry loop with a bare `return` on exception —
    # no thread ever started, ws_gap_guard never received a SUBSCRIBED message,
    # daemon stayed in reduce_only=True until the next SIGTERM.
    #
    # Structural fix: factor creds+ingestor construction into a helper that gets
    # invoked (a) eagerly so a healthy boot constructs synchronously like before,
    # and (b) again from inside the retry loop whenever the prior attempt failed
    # or the start() coroutine exited. Either path independently advances the
    # daemon — transient API failures no longer permanently latch the WS guard.
    # Map exception types to ws_gap_guard subscription_state so operator
    # telemetry distinguishes "auth/creds failed" from generic transport/network
    # failures. AUTH_FAILED gates differently from DISCONNECTED in the gap guard
    # (auth requires operator intervention; disconnect retries cleanly).
    # Conservative classification: only treat creds-shape failures as AUTH_FAILED.
    def _classify_build_failure(exc: BaseException) -> str:
        name = type(exc).__name__
        msg = str(exc).lower()
        auth_signals = (
            "creds",
            "auth",
            "api_key",
            "api-key",
            "passphrase",
            "secret",
            "signature",
            "unauthorized",
            "401",
            "403",
        )
        if any(sig in msg for sig in auth_signals):
            return "AUTH_FAILED"
        if name in {"WSAuthMissing", "ValueError", "TypeError"} and "creds" in msg:
            return "AUTH_FAILED"
        return "DISCONNECTED"

    def _build_ingestor() -> "PolymarketUserChannelIngestor | None":
        global _user_channel_ingestor
        # Invalidate the adapter's memoized SDK client so this attempt forces a
        # fresh signer-bound L2 credential derivation rather than reusing a cached
        # client whose creds were None from a prior failed boot
        # (codereview-may19 / Codex P1: src/venue/polymarket_v2_adapter.py:286
        # memoizes self._client; without reset, every retry sees the same bad
        # creds and the loop never recovers).
        try:
            adapter._client = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            # Adapter might not expose the attribute on all stub paths; non-fatal.
            pass

        try:
            sdk_client = adapter._sdk_client()
            sdk_creds = sdk_client.creds
            if sdk_creds is None:
                raise RuntimeError(
                    "adapter._sdk_client().creds is None "
                    "(signer-bound L2 credential derivation failed)"
                )
            ws_auth = WSAuth(
                api_key=sdk_creds.api_key,
                secret=sdk_creds.api_secret,
                passphrase=sdk_creds.api_passphrase,
            )
            ingestor = PolymarketUserChannelIngestor(
                adapter, condition_ids, auth=ws_auth
            )
            _user_channel_ingestor = ingestor
            return ingestor
        except Exception as exc:
            subscription_state = _classify_build_failure(exc)
            gap_reason = f"user_channel_attempt_failed:{type(exc).__name__}"
            record_gap(gap_reason, subscription_state=subscription_state)
            logger.error(
                "M3 user-channel ingestor build failed (subscription_state=%s): %s; "
                "will retry inside daemon thread",
                subscription_state,
                exc,
                exc_info=True,
            )
            return None

    # Eager best-effort construction (preserves the synchronous-build contract
    # that callers and unit tests rely on when the boot environment is healthy).
    _build_ingestor()

    def _runner() -> None:
        global _user_channel_ingestor
        import asyncio
        import time as _time

        attempt = 0
        while True:
            attempt += 1
            ingestor = _user_channel_ingestor or _build_ingestor()
            if ingestor is not None:
                try:
                    asyncio.run(ingestor.start())
                    logger.warning(
                        "M3 user-channel ingestor exited cleanly; reconnecting"
                    )
                except Exception as exc:
                    logger.error(
                        "M3 user-channel ingestor attempt %d stopped: %s",
                        attempt,
                        exc,
                        exc_info=True,
                    )
                # Force a fresh creds fetch on the next iteration — auth tokens may
                # have expired and a stale ingestor would just fail-loop again.
                _user_channel_ingestor = None
            backoff = min(
                _WS_RETRY_BASE_SECONDS * (2 ** min(attempt - 1, 6)),
                _WS_RETRY_MAX_SECONDS,
            )
            logger.info(
                "M3 user-channel ingestor will retry in %.0fs (attempt %d)",
                backoff,
                attempt,
            )
            _time.sleep(backoff)

    _user_channel_thread = threading.Thread(
        target=_runner,
        name="polymarket-user-channel",
        daemon=True,
    )
    _user_channel_thread.start()
    logger.info(
        "M3 user-channel ingestor thread launched for %d condition_ids "
        "(auto_derived=%s); creds re-fetched per-attempt inside retry loop on failure",
        len(condition_ids),
        auto_derived,
    )


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
        if _cycle_lock.locked() or _edli_reactor_active():
            return
        adapter = _ensure_venue_read_side_adapter()
        _start_collateral_background_refresh_async(adapter)
        _start_venue_background_maintenance_async(adapter)
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
):
    if event_window_limit is None:
        event_window_limit = _pending_family_refresh_event_window_limit()
    event_window_limit = max(100, min(10000, int(event_window_limit)))
    return world_conn.execute(
        """
        WITH pending AS (
            SELECT p.event_id
            FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
            WHERE p.consumer_name = ? AND p.processing_status = 'pending'
            ORDER BY p.updated_at DESC
            LIMIT ?
        )
        SELECT
            json_extract(e.payload_json, '$.city')        AS city,
            json_extract(e.payload_json, '$.target_date') AS target_date,
            json_extract(e.payload_json, '$.metric')      AS metric
        FROM pending p
        JOIN opportunity_events e ON e.event_id = p.event_id
        GROUP BY city, target_date, metric
        -- Refresh the newest target date first. Old target-date rows can remain
        -- pending after a market has disappeared from Gamma; if they consume the
        -- per-cycle cap, fresh executable snapshots starve and no receipt is
        -- emitted even though the reactor itself is healthy.
        ORDER BY
            MAX(json_extract(e.payload_json, '$.target_date')) DESC,
            MAX(e.priority) DESC,
            MAX(e.available_at) DESC,
            MIN(e.event_id) ASC
        """,
        (consumer_name, event_window_limit),
    ).fetchall()


def _condition_buy_sides_fresh(write_conn, condition_id: str, fresh_at_iso: str) -> bool:
    rows = write_conn.execute(
        """
        SELECT yes_token_id, no_token_id, selected_outcome_token_id
        FROM executable_market_snapshots
        WHERE condition_id = ? AND freshness_deadline >= ?
        ORDER BY captured_at DESC, snapshot_id DESC
        """,
        (condition_id, fresh_at_iso),
    ).fetchall()
    if not rows:
        return False

    yes_token_id = ""
    no_token_id = ""
    fresh_selected_tokens: set[str] = set()

    def _cell(row, key: str, index: int) -> str:
        try:
            value = row[key] if hasattr(row, "keys") else row[index]
        except (KeyError, IndexError, TypeError):
            value = None
        return str(value or "").strip()

    for row in rows:
        yes = _cell(row, "yes_token_id", 0)
        no = _cell(row, "no_token_id", 1)
        selected = _cell(row, "selected_outcome_token_id", 2)
        if yes and not yes_token_id:
            yes_token_id = yes
        if no and not no_token_id:
            no_token_id = no
        if selected:
            fresh_selected_tokens.add(selected)
    if not yes_token_id or not no_token_id:
        return False
    return yes_token_id in fresh_selected_tokens and no_token_id in fresh_selected_tokens


def _prune_fresh_market_outcomes_for_snapshot_refresh(
    write_conn,
    markets: list[dict],
    *,
    fresh_at_iso: str,
) -> tuple[list[dict], int, int]:
    pruned: list[dict] = []
    fresh_conditions_skipped = 0
    stale_conditions_submitted = 0
    for market in markets:
        stale_outcomes: list[dict] = []
        for outcome in market.get("outcomes", []) or []:
            if not isinstance(outcome, dict):
                continue
            cid = str(outcome.get("condition_id") or outcome.get("market_id") or "").strip()
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
) -> float:
    del refresh_budget_s, cached_topology_count
    return refresh_deadline - snapshot_reserve_s


def _topology_lookup_deadline_for_snapshot_refresh(
    *,
    refresh_deadline: float,
    refresh_budget_s: float,
    snapshot_reserve_s: float,
) -> float:
    """Stop topology reconstruction early enough to attempt direct Gamma lookup.

    FUNNEL-STARVATION FIX (2026-06-09): the topology/reconstruction phase is the
    phase that SELECTS which families' books to capture this cycle. The prior math
    reserved a FIXED 15s gamma slice on top of the 12s snapshot reserve, out of a
    17s budget — i.e. 27s reserved for downstream phases out of 17s, clamping the
    topology deadline to ``refresh_deadline - refresh_budget_s`` = CYCLE START.
    With a 0s topology budget the loop time-boxed after 1-2 of ~150 live families
    every cycle, so only ONE family's executable books were refreshed per ~20s
    tick. The other ~150 FORECAST_SNAPSHOT_READY events found no fresh snapshot,
    requeued EXECUTABLE_SNAPSHOT_PENDING, and after 8 cycles dead-lettered as
    EXECUTABLE_SNAPSHOT_BLOCKED — the visible funnel starvation (the substrate was
    never swept).

    Gamma HTTP is only needed for families with NO cached topology
    (``gamma_refresh_families``); in steady state that set is EMPTY (every live
    family already has market_events topology), so a fixed 15s gamma reserve is
    pure waste that starves the dominant topology phase. The reserve is now a SMALL
    FLOOR (default 2s, env-overridable) AND capped to at most half of the
    available pre-capture window, so the topology/reconstruction phase keeps the
    MAJORITY of the budget and can sweep its rotating family slice (see the
    rotating cursor in _refresh_pending_family_snapshots). When a cycle genuinely
    has gamma families, that small floor still lets the gamma phase begin; it just
    no longer pre-empts topology before topology has done any work.
    """

    pre_capture_deadline = refresh_deadline - snapshot_reserve_s
    available_pre_capture_s = max(0.0, refresh_budget_s - snapshot_reserve_s)
    gamma_min_slice_s = max(
        0.0,
        float(os.environ.get("ZEUS_REACTOR_GAMMA_LOOKUP_MIN_SECONDS", "2.0")),
    )
    # Never let the gamma floor consume more than half the pre-capture window —
    # the topology phase (which selects the capture set) must always keep the
    # majority share so it makes real progress through the rotating family slice.
    gamma_min_slice_s = min(gamma_min_slice_s, available_pre_capture_s * 0.5)
    return max(refresh_deadline - refresh_budget_s, pre_capture_deadline - gamma_min_slice_s)


def _snapshot_capture_budget_for_refresh(
    *,
    refresh_deadline: float,
    snapshot_reserve_s: float,
) -> float:
    """Return the CLOB capture slice for pending-family snapshot refresh.

    The warm job has two qualitatively different phases: cheap topology/cache
    selection and price capture.  Live evidence showed the selection phase can
    consume the full nominal refresh budget; passing the leftover 0.1s to CLOB
    creates one-row "progress" while every pending family remains effectively
    blocked.  The reserve is therefore a phase budget, not a leftover hint.
    """

    min_prefetch_window_s = max(
        0.0,
        float(os.environ.get("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_MIN_WINDOW_SECONDS", "0.75")),
    )
    target_prefetch_window_s = max(
        min_prefetch_window_s,
        float(os.environ.get("ZEUS_MARKET_DISCOVERY_ORDERBOOK_PREFETCH_TARGET_WINDOW_SECONDS", "2.0")),
    )
    # refresh_executable_market_substrate_snapshots internally reserves
    # snapshot_reserve_s for the capture loop and admits /books only before that
    # reserve starts. Passing exactly snapshot_reserve_s double-reserves the
    # same phase and makes the batch prefetch deadline effectively immediate.
    # The admission threshold is not enough as a budget: real scheduler/function
    # overhead can burn milliseconds between budget construction and prefetch,
    # making a nominal 0.750s window measure as "below 0.750s" and collapse back
    # to serial /book reads. Keep prefetch as its own small phase budget.
    min_budget_s = snapshot_reserve_s + target_prefetch_window_s
    remaining_s = refresh_deadline - time.monotonic()
    return max(min_budget_s, remaining_s)


def _refresh_pending_family_snapshots(
    world_conn,
    forecasts_conn,
    *,
    consumer_name: str = "edli_reactor_v1",
    now_utc: datetime | None = None,
) -> dict:
    """Targeted, cache-aware snapshot refresh for pending opportunity event families.

    Decision-driven design ("先有下单结果再去找市场"):
      - Scope: ONLY the families (city/target_date/metric) of PENDING events.
      - Cache: skip entire families whose ALL bins are still fresh.
      - Discovery: Gamma slug lookup scoped to pending target_dates — discovers
        EVERY bin (incl. never-seen illiquid MECE tail bins) via full token payload.
      - CLOB: max_outcomes=None so all family bins are captured (no city cap).
        tolerate_missing_book=True (inside refresh) lets illiquid bins snapshot
        as top_ask=None / executable_allowed=False.
      - No universe sweep, no market_discovery, no find_weather_markets.

    Reuses refresh_executable_market_substrate_snapshots write path unchanged.
    Returns a summary dict; never raises (failures are logged and skipped).
    """

    from src.data.market_scanner import (
        reconstruct_weather_market_from_static_topology,
        refresh_executable_market_substrate_snapshots,
    )
    from src.data.polymarket_client import PolymarketClient
    from src.engine.event_reactor_adapter import _event_family_market_topology_rows
    from src.state.db import get_trade_connection
    from src.strategy.market_phase import (
        family_venue_closed as _family_venue_closed,
    )

    # Injected-now (tests / replay): the venue-close warm-skip and the snapshot
    # freshness window both key off this single decision clock. Defaults to
    # wall-clock UTC in production; a test passes a frozen instant so the
    # venue-close skip is deterministic against fixed-date fixtures.
    now_utc = now_utc if now_utc is not None else datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    # Step 1: Collect distinct (city, target_date, metric) for pending events.
    try:
        pending_rows = _pending_family_rows_for_refresh(
            world_conn, consumer_name=consumer_name
        )
    except Exception as exc:
        logger.warning("refresh_pending_family_snapshots: pending-event query failed: %s", exc)
        return {"status": "error", "reason": str(exc)}

    from src.config import cities_by_name as _refresh_cities_by_name

    def _refresh_family_text_key(value: object) -> str:
        text = str(value or "").strip().lower()
        return " ".join(text.replace("-", " ").replace("_", " ").split())

    _refresh_city_alias_to_name: dict[str, str] = {}
    for _city in _refresh_cities_by_name.values():
        for _surface in (
            _city.name,
            *_city.aliases,
            *_city.slug_names,
        ):
            _key = _refresh_family_text_key(_surface)
            if _key:
                _refresh_city_alias_to_name[_key] = _city.name

    def _canonical_refresh_city_name(city: object) -> str:
        raw = str(city or "").strip()
        return _refresh_city_alias_to_name.get(_refresh_family_text_key(raw), raw)

    def _canonical_refresh_metric(metric: object) -> str:
        text = _refresh_family_text_key(metric)
        if text in {"low", "lowest", "min", "minimum"} or text.startswith("lowest "):
            return "low"
        if text in {"high", "highest", "max", "maximum"} or text.startswith("highest "):
            return "high"
        return text

    def _refresh_family_key(city: object, target_date: object, metric: object) -> tuple[str, str, str]:
        return (
            _refresh_family_text_key(_canonical_refresh_city_name(city)),
            str(target_date or "").strip(),
            _canonical_refresh_metric(metric),
        )

    families: list[tuple[str, str, str]] = []
    for row in pending_rows:
        city = _canonical_refresh_city_name(row[0])
        target_date = str(row[1] or "").strip()
        metric = _canonical_refresh_metric(row[2])
        if city and target_date and metric:
            families.append((city, target_date, metric))

    if not families:
        return {"status": "no_pending_families"}

    # FUNNEL-STARVATION FIX (2026-06-09): rotate the per-cycle starting offset so
    # EVERY live family is refreshed within one SWEEP PERIOD instead of the newest
    # 1-2 being re-refreshed forever while the rest starve.
    #
    # Root cause this completes: the warm cycle's wall-clock budget (~17s) cannot
    # reconstruct all ~150 live families in one tick — each family's
    # reconstruct_weather_market_from_static_topology is ~225ms (a sort over the
    # 1.5M-row executable_market_snapshots IN-list), so a full sweep is ~34s, two
    # cycles' worth. The pending-family query (_pending_family_rows_for_refresh)
    # returns families target_date DESC, so without rotation the SAME front slice
    # is processed every cycle and the tail (older target_dates / cities reached
    # later in the deterministic order) NEVER gets fresh books. Those families'
    # FORECAST_SNAPSHOT_READY events then retry EXECUTABLE_SNAPSHOT_PENDING until
    # the 8-attempt cap dead-letters them (EXECUTABLE_SNAPSHOT_BLOCKED) — the
    # operator-observed "25 events cycle in retried forever / dead=25" funnel.
    #
    # The fix is a fair rotating cursor: advance a module-global offset by the
    # number of families ACTUALLY processed last cycle so consecutive cycles cover
    # disjoint slices and the whole live set is swept within
    # ceil(len(families) / families_per_cycle) cycles — bounded minutes, not the
    # never-completing tail starvation. The order WITHIN the rotated list is still
    # the freshness-first deterministic order; we only choose a different *start*.
    # No family is dropped — rotation reorders, it does not filter — so a True from
    # the freshness check still captures and no candidate is starved.
    # FIX 3c — NEW-FAMILY WARMER PRIORITY (operator 2026-06-09):
    # Newly-discovered condition_ids (set by _new_listing_scout_cycle) jump to HEAD
    # of the rotation so they are warmed in the NEXT cycle rather than waiting at
    # the tail of the round-robin.  Translate condition_ids → (city, date, metric)
    # tuples via the topology DB, then prepend to families before cursor rotation.
    global _SUBSTRATE_REFRESH_CURSOR, _NEW_FAMILY_CONDITION_IDS, _GAMMA_EMPTY_BACKOFF_UNTIL
    new_priority_families: list[tuple[str, str, str]] = []
    if _NEW_FAMILY_CONDITION_IDS:
        try:
            new_cids_snapshot = set(_NEW_FAMILY_CONDITION_IDS)
            _NEW_FAMILY_CONDITION_IDS.clear()
            for cid in sorted(new_cids_snapshot):
                try:
                    row_q = world_conn.execute(
                        "SELECT city, target_date, temperature_metric FROM market_events WHERE condition_id = ? LIMIT 1",
                        (cid,),
                    ).fetchone()
                    if row_q is not None:
                        city_v, td_v, metric_v = (
                            _canonical_refresh_city_name(row_q[0]),
                            str(row_q[1] or "").strip(),
                            _canonical_refresh_metric(row_q[2]),
                        )
                        fk = _refresh_family_key(city_v, td_v, metric_v)
                        if fk not in {_refresh_family_key(*f) for f in families}:
                            new_priority_families.append((city_v, td_v, metric_v))
                except Exception:
                    pass
        except Exception:
            pass
    n_families = len(families)
    start_offset = _SUBSTRATE_REFRESH_CURSOR % n_families
    families = new_priority_families + families[start_offset:] + families[:start_offset]

    # Fitz #5 scheduler-liveness fix (2026-06-08): this wall-clock budget MUST be
    # STRICTLY LESS than the warm-cycle APScheduler interval (_EDLI_SUBSTRATE_WARM_
    # INTERVAL_SECONDS, 20s) and MUST stay within the 180s executable-price freshness
    # window. The prior 29.0 default predated the reactor→warm-cycle split (blame
    # 014408394f, sized for the old 1-min reactor interval) and was never re-aligned:
    # a 29s budget on a 20s interval guarantees the cycle overruns its own trigger,
    # so every subsequent run is "skipped: maximum number of running instances
    # reached (1)" (zeus-live.err 2026-06-08) and the universe-wide executable
    # substrate is never refreshed — coverage NONE, daemon starved of candidates.
    # The default now fits inside the interval with headroom for scheduler dispatch
    # and connection teardown; the internal capture reserve (snapshot_reserve_s) and
    # Gamma slice scale down off this budget below. Env-overridable, but the
    # interval-fit invariant is asserted at job registration (see add_job below).
    refresh_budget_s = max(
        5.0,
        float(os.environ.get("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "17.0")),
    )
    refresh_deadline = time.monotonic() + refresh_budget_s
    snapshot_reserve_s = min(
        max(1.0, float(os.environ.get("ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS", "12.0"))),
        max(0.1, refresh_budget_s - 0.1),
    )
    # FUTURE-NOT-LISTED WARM-BACKOFF (2026-06-15, #122): cooldown (seconds) a
    # no-topology, Gamma-empty family is parked before re-probing. 0 disables.
    _gamma_empty_backoff_s = max(
        0.0,
        float(os.environ.get("ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS", "300.0")),
    )
    topology_deadline = _topology_lookup_deadline_for_snapshot_refresh(
        refresh_deadline=refresh_deadline,
        refresh_budget_s=refresh_budget_s,
        snapshot_reserve_s=snapshot_reserve_s,
    )

    # Staleness pre-filter REMOVED (STEP 4, consolidated timeliness fix): the
    # local lexicographic _drop_stale_families is now redundant. Strictly-past
    # (already-settled) targets can no longer become pending events — the
    # emission floor (STEP 2, forecast_snapshot_ready.scan_committed_snapshots)
    # never emits them and the EventStore claim floor (STEP 3, fetch_pending)
    # never returns them — so no stale family reaches this refresh path. The
    # pending-event query above is the single source of families; it is sourced
    # from the same already-timeliness-filtered opportunity_events.

    # Throughput contract: never slice pending families by a fixed count. Live has
    # hundreds of active weather families across time zones; a hard family cap lets
    # a small prefix monopolise the freshness window. The wall-clock budget is the
    # only per-tick bound, with a reserved CLOB capture slice so Gamma lookup cannot
    # consume the whole tick and leave snapshot insertion at attempted=0.

    # Step 2: Cache-skip: for each family check whether ALL known condition_ids
    #         (from market_events topology) already have fresh snapshots.
    #         Families with ANY stale/missing bin still proceed to Gamma fetch.
    fresh_skipped = 0
    no_topology = 0
    no_topology_backed_off = 0
    venue_closed_skipped = 0
    gamma_refresh_families: list[tuple[str, str, str]] = []
    cached_topology_markets: list[dict] = []
    cached_topology_families = 0
    cached_topology_incomplete = 0
    topology_budget_exhausted = False
    topology_deferred_families = 0
    # FUNNEL-STARVATION FIX (2026-06-09): how many families this cycle actually
    # reached in the topology phase. Advances the rotating cursor so the NEXT
    # cycle resumes from where this one stopped, sweeping every live family within
    # a bounded number of cycles instead of re-processing the same front slice.
    families_processed_this_cycle = len(families)

    write_conn = get_trade_connection(write_class="live")
    try:
        for index, (city, target_date, metric) in enumerate(families):
            if time.monotonic() >= topology_deadline and (
                cached_topology_markets or gamma_refresh_families
            ):
                topology_budget_exhausted = True
                topology_deferred_families = len(families) - index
                # Resume from the first UNPROCESSED family next cycle.
                families_processed_this_cycle = index
                logger.info(
                    "refresh_pending_family_snapshots: topology time-box hit after %d/%d "
                    "families; reserving %.1fs for CLOB capture",
                    index,
                    len(families),
                    snapshot_reserve_s,
                )
                break
            # VENUE-CLOSE WARM-SKIP (2026-06-13): a family whose Polymarket weather
            # market has already entered POST_TRADING/RESOLVED (the F1 12:00-UTC
            # close of target_date) can produce no fresh executable book — its
            # capture froze at the last pre-close snapshot and Gamma returns an
            # empty event list. Re-probing it (topology lookup + Gamma slug fetch)
            # burns the bounded time-box that LIVE families (PRE_SETTLEMENT_DAY /
            # SETTLEMENT_DAY) need, starving the live inventory of fresh snapshots.
            #
            # This is the EARLIER-than-strictly-past horizon: the venue closes at
            # 12:00 UTC of target_date, hours before the target LOCAL-day end that
            # the claim floor (EventStore._strictly_past_in_tz) and the prior STEP-4
            # comment relied on. So a same-day-but-venue-closed family (e.g. a
            # 2026-06-13 family at 17:44Z, post the 12:00Z close, pre local
            # midnight) passes the claim floor and reaches this lane — exactly the
            # 202/319 closed families measured live 2026-06-13 17:51Z that the
            # 'gamma_slug_timebox_unattempted' tail re-probed for nothing.
            #
            # Authority: market_phase.family_venue_closed reuses the SAME F1
            # 12:00-UTC POST_TRADING anchor (market_open_at_decision /
            # market_phase_for_decision) the reactor's _venue_market_closed_horizon
            # uses — single authority, no new clock. Fail-SOFT: an unresolvable
            # city/tz/date returns False (NOT closed) so an uncertain family is
            # KEPT, never dropped (a tradeable family must never be skipped). This
            # is a focus/efficiency skip, NOT a cap or admission relaxation — it
            # removes only families whose venue is provably closed.
            if _family_venue_closed(
                city=city, target_date=target_date, now_utc=now_utc
            ):
                venue_closed_skipped += 1
                continue
            payload = {"city": city, "target_date": target_date, "metric": metric}
            topology_rows = _event_family_market_topology_rows(forecasts_conn, payload)
            if not topology_rows:
                no_topology += 1
                # FUTURE-NOT-LISTED WARM-BACKOFF (2026-06-15, #122): if this
                # no-topology family's last Gamma slug lookup returned an EMPTY
                # event list (no Polymarket market listed yet for this future
                # target_date) and we are still inside its cooldown, do NOT re-add
                # it to the Gamma probe set this cycle. Re-probing not-yet-listed
                # future families every ~20s warm tick returns empty every time,
                # exhausts the bounded Gamma time-box, and starves CLOB capture of
                # families that DO have topology (the fresh_executable_city_count
                # 0-oscillation, measured 200/200 backed-off were next-day
                # lows/highs). The family stays a pending event and is re-probed
                # the moment the cooldown expires — captured as soon as the market
                # lists. Symmetric twin of the _family_venue_closed past-skip: a
                # focus/efficiency skip, never a terminal drop. Env
                # ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS=0 disables.
                nb_key = _refresh_family_key(city, target_date, metric)
                if (
                    _gamma_empty_backoff_s > 0.0
                    and _GAMMA_EMPTY_BACKOFF_UNTIL.get(nb_key, 0.0) > time.monotonic()
                ):
                    no_topology_backed_off += 1
                    continue
                logger.debug(
                    "refresh_pending_family_snapshots: no market topology for %s/%s/%s "
                    "(no Polymarket market for this family — event will be rejected at gate)",
                    city, target_date, metric,
                )
                # Still include: Gamma may discover bins not yet in topology.
                gamma_refresh_families.append((city, target_date, metric))
                continue
            topology_rows = [
                {
                    **dict(trow),
                    "city": city,
                    "target_date": target_date,
                    "temperature_metric": metric,
                }
                for trow in topology_rows
            ]

            any_stale = False
            for trow in topology_rows:
                cid = str(trow.get("condition_id") or "").strip()
                if not cid:
                    continue
                if not _condition_buy_sides_fresh(write_conn, cid, now_iso):
                    any_stale = True
                    break

            if any_stale:
                reconstructed = reconstruct_weather_market_from_static_topology(
                    write_conn,
                    topology_rows=topology_rows,
                    now_utc=now_utc,
                )
                if reconstructed is not None:
                    cached_topology_markets.append(reconstructed)
                    cached_topology_families += 1
                else:
                    cached_topology_incomplete += 1
                    gamma_refresh_families.append((city, target_date, metric))
            else:
                fresh_skipped += 1

        # FUNNEL-STARVATION FIX (2026-06-09): advance the rotating cursor by the
        # families actually processed this cycle so the next cycle resumes at the
        # first family this one did not reach. This is what converts "always the
        # newest 1-2 families" into a fair round-robin that sweeps the whole live
        # set within ceil(n_families / families_per_cycle) cycles. Advanced HERE
        # (after the topology loop, before any downstream return) so every exit
        # path — all_fresh, refreshed, or a later gamma/capture error — advances
        # the cursor identically and no slice is ever skipped or double-swept.
        _SUBSTRATE_REFRESH_CURSOR = (
            start_offset + max(1, families_processed_this_cycle)
        ) % n_families

        if not gamma_refresh_families and not cached_topology_markets:
            logger.info(
                "refresh_pending_family_snapshots: all families fresh, skipped. "
                "families=%d fresh_skipped=%d no_topology=%d venue_closed_skipped=%d "
                "cached_topology_incomplete=%d",
                len(families), fresh_skipped, no_topology, venue_closed_skipped,
                cached_topology_incomplete,
            )
            return {
                "status": "all_fresh",
                "families_checked": len(families),
                "fresh_skipped": fresh_skipped,
                "no_topology": no_topology,
                "venue_closed_skipped": venue_closed_skipped,
                "cached_topology_incomplete": cached_topology_incomplete,
            }

        # Step 3: Targeted Gamma slug fetch — one request per pending family.
        #         Build the exact slug for each (city, date, metric) and fetch
        #         directly.  This is maximally bounded: N pending families = N
        #         Gamma calls (vs the background slug-pattern scanner which
        #         enumerates all 14 cities × all dates and is budget-capped).
        #         Uses the City's slug_names[0] for the slug fragment.
        gamma_deadline = _gamma_lookup_deadline_for_snapshot_refresh(
            refresh_deadline=refresh_deadline,
            refresh_budget_s=refresh_budget_s,
            snapshot_reserve_s=snapshot_reserve_s,
            cached_topology_count=len(cached_topology_markets),
        )
        skipped_not_found = 0
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
            from datetime import date as _date_cls
            from src.data.market_scanner import (
                _gamma_get,
                _parse_and_persist_weather_events,
            )

            def _date_to_slug_fragment(date_str: str) -> str:
                d = _date_cls.fromisoformat(date_str)
                return d.strftime("%B-%-d-%Y").lower()

            raw_events_seen: set = set()
            raw_events_collected: list[dict] = []
            gamma_slug_attempted = 0
            gamma_slug_empty = 0
            gamma_slug_http_non_200 = 0
            gamma_slug_failed = 0
            gamma_slug_invalid = 0
            gamma_slug_timebox_unattempted = 0
            gamma_empty_family_keys: set[tuple[str, str, str]] = set()
            # FDR-GATE PARSE INCIDENT FIX (2026-06-10): track HARVESTED (result
            # actually read), not merely SUBMITTED. A future cancelled / not drained
            # at the gamma time-box — whose HTTP often lands ~140ms LATER, since
            # `future.cancel()` cannot stop an already-running thread — was previously
            # counted as "attempted" yet never harvested. The match loop then reported
            # that transient timing miss as a permanent "did not parse — bin identity
            # unknown" verdict, pinning real families at the FDR gate forever. The
            # terminal verdict is now restricted to keys in this set; everything else
            # (submitted-but-un-harvested) is reported RETRYABLE so the next cycle
            # re-fetches it. Fail-closed is preserved: a harvested-but-unmatched family
            # still stays terminal.
            gamma_harvested_family_keys: set[tuple[str, str, str]] = set()

            gamma_jobs: list[dict] = []
            for fam_city, fam_date, fam_metric in gamma_refresh_families:
                family_key = _refresh_family_key(fam_city, fam_date, fam_metric)
                if time.monotonic() > gamma_deadline:
                    gamma_slug_timebox_unattempted += 1
                    continue
                city_obj = _refresh_cities_by_name.get(_canonical_refresh_city_name(fam_city))
                if city_obj is None:
                    gamma_slug_invalid += 1
                    logger.info(
                        "refresh_pending_family_snapshots: city %r not in config, skipping",
                        fam_city,
                    )
                    continue
                slug_fragment = city_obj.slug_names[0] if city_obj.slug_names else fam_city.lower().replace(" ", "-")
                try:
                    slug_date = _date_to_slug_fragment(fam_date)
                except (ValueError, TypeError):
                    gamma_slug_invalid += 1
                    logger.info(
                        "refresh_pending_family_snapshots: invalid date %r for %s, skipping",
                        fam_date, fam_city,
                    )
                    continue
                prefix = "lowest" if fam_metric == "low" else "highest"
                slug = f"{prefix}-temperature-in-{slug_fragment}-on-{slug_date}"
                gamma_jobs.append(
                    {
                        "city": fam_city,
                        "target_date": fam_date,
                        "metric": fam_metric,
                        "family_key": family_key,
                        "slug": slug,
                    }
                )

            def _fetch_gamma_slug(job: dict) -> dict:
                remaining = max(0.1, gamma_deadline - time.monotonic())
                _gamma_timeout = min(
                    max(1.0, float(os.environ.get("ZEUS_DISCOVERY_GAMMA_TIMEOUT_SECONDS", "10.0"))),
                    remaining,
                )
                slug = str(job["slug"])
                resp = _gamma_get("/events", params={"slug": slug}, timeout=_gamma_timeout)
                if resp.status_code != 200:
                    return {**job, "status": "http_non_200", "status_code": resp.status_code, "events": []}
                batch = resp.json()
                if not isinstance(batch, list):
                    batch = [batch] if isinstance(batch, dict) and batch else []
                events = [event for event in batch if isinstance(event, dict)]
                return {**job, "status": "ok" if events else "empty", "events": events}

            gamma_concurrency = max(
                1,
                min(32, int(os.environ.get("ZEUS_REACTOR_GAMMA_LOOKUP_CONCURRENCY", "8"))),
            )
            pending_futures: dict = {}
            next_job_index = 0

            def _submit_gamma_jobs(executor: ThreadPoolExecutor) -> None:
                nonlocal gamma_slug_attempted, next_job_index
                while (
                    len(pending_futures) < gamma_concurrency
                    and next_job_index < len(gamma_jobs)
                    and time.monotonic() <= gamma_deadline
                ):
                    job = gamma_jobs[next_job_index]
                    next_job_index += 1
                    gamma_slug_attempted += 1
                    pending_futures[executor.submit(_fetch_gamma_slug, job)] = job

            def _harvest_gamma_result(result: dict) -> None:
                """Record a Gamma future's RESULT (not its mere submission).

                Marking the family_key harvested here is what lets the downstream
                match loop distinguish "we read a response and it did not match this
                pending family" (terminal: stay at FDR gate, fail-closed) from "we
                never got to read a response" (retryable). Without this the two were
                conflated and every time-boxed family was reported as a hard parse
                failure.
                """
                nonlocal gamma_slug_http_non_200, gamma_slug_empty
                gamma_harvested_family_keys.add(result["family_key"])
                if result["status"] == "http_non_200":
                    gamma_slug_http_non_200 += 1
                    logger.debug(
                        "refresh_pending_family_snapshots: Gamma %s -> HTTP %s",
                        result["slug"], result.get("status_code"),
                    )
                elif result["status"] == "empty":
                    gamma_slug_empty += 1
                    gamma_empty_family_keys.add(result["family_key"])
                else:
                    for event in result["events"]:
                        event_id = event.get("id") or event.get("slug")
                        if event_id and event_id not in raw_events_seen:
                            raw_events_seen.add(event_id)
                            raw_events_collected.append(event)

            if gamma_jobs:
                with ThreadPoolExecutor(
                    max_workers=gamma_concurrency,
                    thread_name_prefix="zeus-gamma-refresh",
                ) as executor:
                    _submit_gamma_jobs(executor)
                    while pending_futures:
                        remaining = gamma_deadline - time.monotonic()
                        if remaining <= 0.0:
                            gamma_slug_timebox_unattempted += len(gamma_jobs) - next_job_index
                            logger.info(
                                "refresh_pending_family_snapshots: Gamma time-box %.0fs hit after %d/%d "
                                "submitted families; draining %d in-flight, reserving %.1fs for CLOB capture",
                                max(0.1, gamma_deadline - (refresh_deadline - refresh_budget_s)),
                                gamma_slug_attempted,
                                len(gamma_jobs),
                                len(pending_futures),
                                snapshot_reserve_s,
                            )
                            next_job_index = len(gamma_jobs)
                            # FDR-GATE PARSE INCIDENT FIX (2026-06-10): break WITHOUT
                            # cancel/clear here. The post-loop drain below harvests any
                            # futures whose HTTP lands within the bounded grace window
                            # (live: ~140ms after the time-box) so their bin identity is
                            # NOT discarded and mislabeled "did not parse". Whatever is
                            # still pending after the grace is then cancelled and left
                            # retryable (never harvested -> never terminal).
                            break
                        try:
                            future = next(
                                as_completed(
                                    tuple(pending_futures),
                                    timeout=max(0.05, min(remaining, 0.5)),
                                )
                            )
                        except FuturesTimeoutError:
                            continue
                        job = pending_futures.pop(future)
                        try:
                            result = future.result()
                        except Exception as _exc:
                            gamma_slug_failed += 1
                            logger.warning(
                                "refresh_pending_family_snapshots: Gamma fetch failed for %s: %s",
                                job["slug"], _exc,
                            )
                            _submit_gamma_jobs(executor)
                            continue
                        _harvest_gamma_result(result)
                        _submit_gamma_jobs(executor)

                    # FDR-GATE PARSE INCIDENT FIX (2026-06-10): drain futures that are
                    # ALREADY done but were left in pending_futures when the loop exited
                    # via the time-box. `future.cancel()` returns False for a running
                    # future — its worker thread keeps going and the HTTP completes a
                    # short moment after the time-box. The prior code cleared
                    # pending_futures without reading those landed results, discarding
                    # perfectly parseable responses and reporting them as parse failures.
                    # A small bounded grace lets the near-complete fetches land so their
                    # bin identity is harvested instead of thrown away. Bounded so the
                    # CLOB capture reserve is never consumed.
                    if pending_futures:
                        # 2.0s (was 1.5s): slice 2.0 + grace must clear the measured
                        # Gamma /events p95 (2.516s) x 1.5 = 3.774s effective recovery
                        # window; 1.5s left it at 3.5s — 0.27s short, the same miss
                        # class that pinned families at the FDR gate on 2026-06-10.
                        # Relation enforced by tests/test_time_semantics_relations.py.
                        grace_s = max(
                            0.0,
                            float(os.environ.get("ZEUS_REACTOR_GAMMA_DRAIN_GRACE_SECONDS", "2.0")),
                        )
                        # Cap the grace at the absolute refresh deadline so draining
                        # near-complete fetches never overruns the cycle's total
                        # wall-clock budget; this only borrows otherwise-idle wait time.
                        grace_deadline = min(
                            time.monotonic() + grace_s,
                            refresh_deadline,
                        )
                        # Harvest in COMPLETION order so the futures whose HTTP lands
                        # first (live: ~140ms after the time-box) are recovered before
                        # the grace runs out, regardless of dict iteration order.
                        while pending_futures:
                            remaining_grace = grace_deadline - time.monotonic()
                            if remaining_grace <= 0.0:
                                break
                            try:
                                future = next(
                                    as_completed(
                                        tuple(pending_futures),
                                        timeout=remaining_grace,
                                    )
                                )
                            except FuturesTimeoutError:
                                break
                            job = pending_futures.pop(future)
                            try:
                                result = future.result()
                            except Exception as _exc:
                                # Future raised (e.g. network error). Not harvested, so
                                # it stays retryable next cycle.
                                gamma_slug_failed += 1
                                logger.debug(
                                    "refresh_pending_family_snapshots: Gamma drain fetch failed for %s: %s",
                                    job["slug"], _exc,
                                )
                                continue
                            _harvest_gamma_result(result)
                        # Anything still unharvested after the grace is genuinely
                        # unresolved this cycle; cancel and leave it RETRYABLE (its
                        # key was never added to gamma_harvested_family_keys).
                        for future in pending_futures:
                            future.cancel()
                        pending_futures.clear()

            gamma_slug_timebox_unattempted += len(gamma_jobs) - next_job_index

            # FUTURE-NOT-LISTED WARM-BACKOFF (2026-06-15, #122): families whose
            # Gamma slug lookup returned an EMPTY event list this cycle have no
            # market listed yet; park them for the cooldown so they stop clogging
            # the Gamma time-box every warm tick. Only genuinely-probed-empty
            # families are parked — timebox_unattempted (never probed) families
            # stay immediately retryable next cycle.
            if _gamma_empty_backoff_s > 0.0 and gamma_empty_family_keys:
                _eb_deadline = time.monotonic() + _gamma_empty_backoff_s
                for _eb_key in gamma_empty_family_keys:
                    _GAMMA_EMPTY_BACKOFF_UNTIL[_eb_key] = _eb_deadline

            # 2026-06-06 throughput repair: keep this refresh truly scoped to pending
            # families. The old fallback called the global weather discovery scanner,
            # which performs a tag/slug sweep and routinely exhausts its
            # request budget before the warm job completes. Current Gamma slug payloads
            # include the required child fields (conditionId, acceptingOrders,
            # enableOrderBook, clobTokenIds), so the exact per-family slug responses are
            # sufficient for parsing, topology persistence, and CLOB snapshot capture.
            discovered_events = _parse_and_persist_weather_events(
                raw_events_collected,
                min_hours_to_resolution=0.0,
                now=now_utc,
            )
            logger.info(
                "refresh_pending_family_snapshots: slug fetch complete "
                "gamma_refresh_families=%d cached_topology_families=%d "
                "raw_events=%d discovered_events=%d attempted=%d empty=%d "
                "http_non_200=%d failed=%d invalid=%d timebox_unattempted=%d "
                "concurrency=%d",
                len(gamma_refresh_families), cached_topology_families,
                len(raw_events_collected), len(discovered_events),
                gamma_slug_attempted, gamma_slug_empty, gamma_slug_http_non_200,
                gamma_slug_failed, gamma_slug_invalid, gamma_slug_timebox_unattempted,
                gamma_concurrency,
            )
        except Exception as exc:
            logger.warning(
                "refresh_pending_family_snapshots: Gamma slug lookup failed: %s", exc
            )
            return {"status": "error_gamma_lookup", "reason": str(exc)}

        # Build a lookup: (city_name_lower, target_date, metric) -> parsed event dict.
        gamma_by_family: dict[tuple[str, str, str], dict] = {}
        for ev in discovered_events:
            city_obj = ev.get("city")
            city_name = getattr(city_obj, "name", None) or (city_obj if isinstance(city_obj, str) else "")
            td = str(ev.get("target_date") or "")
            metric_ev = str(ev.get("temperature_metric") or "")
            key = _refresh_family_key(city_name, td, metric_ev)
            gamma_by_family[key] = ev

        # Filter to ONLY the pending families (bounded CLOB calls, no universe sweep).
        markets: list[dict] = []
        markets.extend(cached_topology_markets)
        for city, target_date, metric in gamma_refresh_families:
            key = _refresh_family_key(city, target_date, metric)
            ev = gamma_by_family.get(key)
            if ev is None:
                # FDR-GATE PARSE INCIDENT FIX (2026-06-10): gate the TERMINAL "did not
                # parse — stay at FDR gate" verdict on whether the family's Gamma
                # future was actually HARVESTED (its result read), not merely
                # submitted. A family whose future was cancelled / not drained at the
                # time-box was "attempted" but never harvested; reporting it as a hard
                # parse failure pinned real families at the gate forever. Such families
                # are now reported RETRYABLE so the next cycle re-fetches them.
                if key in gamma_harvested_family_keys:
                    skipped_not_found += 1
                    if key in gamma_empty_family_keys:
                        logger.warning(
                            "refresh_pending_family_snapshots: Gamma returned empty event list for "
                            "%s/%s/%s — bin identity unknown, family will stay at FDR gate",
                            city, target_date, metric,
                        )
                    else:
                        logger.warning(
                            "refresh_pending_family_snapshots: Gamma response did not parse to pending family "
                            "%s/%s/%s — bin identity unknown, family will stay at FDR gate",
                            city, target_date, metric,
                        )
                else:
                    logger.info(
                        "refresh_pending_family_snapshots: Gamma fetch not harvested before time-box for "
                        "%s/%s/%s — family remains retryable",
                        city, target_date, metric,
                    )
                continue
            markets.append(ev)

        if not markets:
            logger.warning(
                "refresh_pending_family_snapshots: no Gamma events matched pending families; "
                "gamma_refresh_families=%d cached_topology_families=%d skipped_not_found=%d",
                len(gamma_refresh_families), cached_topology_families, skipped_not_found,
            )
            return {
                "status": "no_refreshable_markets",
                "families_needing_refresh": len(gamma_refresh_families) + cached_topology_families,
                "gamma_refresh_families": len(gamma_refresh_families),
                "cached_topology_families": cached_topology_families,
                "skipped_not_found": skipped_not_found,
            }

        # Step 4: CLOB fetch + cache write.
        #         max_outcomes=0 is the UNLIMITED sentinel: bypass the per-city cap so
        #         ALL bins of each pending family are captured in ONE cycle (e.g. an
        #         11-bin negRisk family needs all 11 — incl. non-tradeable tail bins —
        #         for the FDR full-family proof / entry gate). max_outcomes=None did NOT
        #         bypass the cap: it fell through to ZEUS_..._MAX_OUTCOMES (default 4),
        #         so families stalled at 4-of-22 candidates → EXECUTABLE_SNAPSHOT_BLOCKED
        #         (2026-06-04 root cause). This caller is scoped to pending families only
        #         (bounded set), so uncapped capture stays within the wall-clock budget.
        #         tolerate_missing_book=True is already hardwired inside
        #         refresh_executable_market_substrate_snapshots, so illiquid bins
        #         snapshot as top_ask=None / executable_allowed=False — never tradeable.
        _clob_timeout = max(
            1.0,
            float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "5.0")),
        )
        markets_for_refresh, fresh_condition_skipped, stale_condition_submitted = (
            _prune_fresh_market_outcomes_for_snapshot_refresh(
                write_conn,
                markets,
                fresh_at_iso=now_iso,
            )
        )
        if not markets_for_refresh:
            return {
                "status": "all_fresh",
                "families_checked": len(families),
                "families_needing_refresh": len(gamma_refresh_families) + cached_topology_families,
                "gamma_refresh_families": len(gamma_refresh_families),
                "cached_topology_families": cached_topology_families,
                "cached_topology_incomplete": cached_topology_incomplete,
                "no_topology": no_topology,
                "fresh_skipped": fresh_skipped,
                "venue_closed_skipped": venue_closed_skipped,
                "gamma_slug_attempted": gamma_slug_attempted,
                "gamma_slug_empty": gamma_slug_empty,
                "gamma_slug_http_non_200": gamma_slug_http_non_200,
                "gamma_slug_failed": gamma_slug_failed,
                "gamma_slug_invalid": gamma_slug_invalid,
                "gamma_slug_timebox_unattempted": gamma_slug_timebox_unattempted,
                "fresh_condition_skipped": fresh_condition_skipped,
                "stale_condition_submitted": stale_condition_submitted,
            }

        snapshot_budget_s = _snapshot_capture_budget_for_refresh(
            refresh_deadline=refresh_deadline,
            snapshot_reserve_s=snapshot_reserve_s,
        )
        with PolymarketClient(public_http_timeout=_clob_timeout) as clob:
            summary = refresh_executable_market_substrate_snapshots(
                write_conn,
                markets=markets_for_refresh,
                clob=clob,
                captured_at=datetime.now(timezone.utc),
                scan_authority="VERIFIED",
                max_outcomes=0,  # UNLIMITED: capture every bin of each pending family
                budget_seconds=snapshot_budget_s,
            )
        write_conn.commit()

    except Exception as exc:
        logger.warning("refresh_pending_family_snapshots: failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        write_conn.close()

    result = {
        "status": "refreshed",
        "families_checked": len(families),
        "families_needing_refresh": len(gamma_refresh_families) + cached_topology_families,
        "gamma_refresh_families": len(gamma_refresh_families),
        "cached_topology_families": cached_topology_families,
        "cached_topology_incomplete": cached_topology_incomplete,
        "no_topology": no_topology,
        "no_topology_backed_off": no_topology_backed_off,
        "fresh_skipped": fresh_skipped,
        "venue_closed_skipped": venue_closed_skipped,
        "topology_budget_exhausted": int(topology_budget_exhausted),
        "topology_deferred_families": topology_deferred_families,
        "skipped_not_found": skipped_not_found,
        "gamma_slug_attempted": gamma_slug_attempted,
        "gamma_slug_empty": gamma_slug_empty,
        "gamma_slug_http_non_200": gamma_slug_http_non_200,
        "gamma_slug_failed": gamma_slug_failed,
        "gamma_slug_invalid": gamma_slug_invalid,
        "gamma_slug_timebox_unattempted": gamma_slug_timebox_unattempted,
        "markets_submitted": len(markets_for_refresh),
        "fresh_condition_skipped": fresh_condition_skipped,
        "stale_condition_submitted": stale_condition_submitted,
        "refresh_budget_seconds": refresh_budget_s,
        "snapshot_reserve_seconds": snapshot_reserve_s,
        "snapshot_budget_seconds": snapshot_budget_s,
        **summary,
    }
    logger.info("refresh_pending_family_snapshots: %s", result)
    return result


def _edli_pending_opportunity_count() -> int:
    """Return current pending EDLI event count for background substrate arbitration."""

    from src.state.db import get_world_connection

    conn = get_world_connection()
    try:
        try:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM opportunity_event_processing
                WHERE consumer_name = 'edli_reactor_v1'
                  AND processing_status = 'pending'
                """
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "market_discovery pending-count read failed; continuing discovery: %r",
                exc,
            )
            return 0
        return int(row[0] or 0) if row is not None else 0
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _market_discovery_pending_fairness_seconds() -> float:
    return max(
        0.0,
        float(os.environ.get("ZEUS_MARKET_DISCOVERY_PENDING_FAIRNESS_SECONDS", "300.0")),
    )


@_scheduler_job("market_discovery")
def _market_discovery_cycle() -> None:
    """Refresh executable market substrate outside decision-cycle critical path."""

    global _market_discovery_last_completed_monotonic

    if _defer_for_held_position_monitor("market_discovery"):
        return
    if _edli_reactor_active():
        logger.info("market_discovery deferred: EDLI reactor active")
        return
    edli_cfg = _settings_section("edli", {})
    pending_count = 0
    defer_when_pending = str(
        os.environ.get("ZEUS_MARKET_DISCOVERY_DEFER_WHEN_EDLI_PENDING", "1")
    ).strip().lower() not in {"0", "false", "no", "off"}
    if edli_cfg.get("enabled") and defer_when_pending:
        try:
            pending_count = _edli_pending_opportunity_count()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "market_discovery pending-count arbitration failed; continuing discovery: %r",
                exc,
            )
            pending_count = 0
        fairness_s = _market_discovery_pending_fairness_seconds()
        last_completed = _market_discovery_last_completed_monotonic
        recent_discovery = (
            fairness_s > 0
            and last_completed is not None
            and (time.monotonic() - last_completed) < fairness_s
        )
        if pending_count > 0 and recent_discovery:
            logger.info(
                "market_discovery deferred: %d EDLI pending events need substrate warm priority "
                "(last discovery %.1fs ago, fairness %.1fs)",
                pending_count,
                time.monotonic() - last_completed,
                fairness_s,
            )
            return
    acquired = _market_discovery_lock.acquire(blocking=False)
    if not acquired:
        logger.warning("market_discovery skipped: previous market_discovery still running")
        return
    # ANTIBODY (2026-06-08, operator directive — kill the regression CATEGORY, not the instance):
    # executable-substrate capture is NEVER gated by the EDLI pending backlog. The old
    # "pending>0 -> topology-only (skip snapshot capture)" branch here was the coverage-collapse
    # regression: a growing pending working set (e.g. the channel-event flood when the prune
    # flag is off) kept market_discovery doing topology-only FOREVER, so families went
    # uncaptured, FSR events dead-lettered on the snapshot gate, and the system silently stopped
    # trading — with nothing connecting cause (a backlog) to effect (no coverage). Substrate
    # capture is gated ONLY by substrate STALENESS (the fairness early-return above, keyed on
    # _market_discovery_last_completed_monotonic), never by queue depth. Reaching here means the
    # substrate is stale (the fresh case already returned at the fairness check), so capture the
    # universe regardless of how many events are pending.
    substrate_acquired = _market_substrate_refresh_lock.acquire(blocking=False)
    if not substrate_acquired:
        _market_discovery_lock.release()
        logger.info("market_discovery deferred: executable substrate refresh already running")
        return
    try:
        from src.data.market_scanner import (
            find_weather_markets_or_raise,
            refresh_executable_market_substrate_snapshots,
        )
        from src.data.polymarket_client import PolymarketClient
        from src.state.db import get_trade_connection

        events = find_weather_markets_or_raise(
            min_hours_to_resolution=0.0,
            include_slug_pattern=True,
        )
        conn = get_trade_connection(write_class="live")
        try:
            _discovery_clob_timeout = max(
                1.0,
                float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "5.0")),
            )
            with PolymarketClient(public_http_timeout=_discovery_clob_timeout) as snapshot_clob:
                snapshot_summary = refresh_executable_market_substrate_snapshots(
                    conn,
                    markets=events,
                    clob=snapshot_clob,
                    captured_at=datetime.now(timezone.utc),
                    scan_authority="VERIFIED",
                )
            conn.commit()
        finally:
            conn.close()
        if snapshot_summary.get("attempted", 0) > 0 and snapshot_summary.get("inserted", 0) == 0:
            raise RuntimeError(
                "market_discovery refreshed events but captured no executable snapshots: "
                f"{snapshot_summary}"
            )
        logger.info(
            "market_discovery: refreshed %s weather events; executable_snapshots=%s",
            len(events),
            snapshot_summary,
        )
        _market_discovery_last_completed_monotonic = time.monotonic()
    finally:
        _market_substrate_refresh_lock.release()
        _market_discovery_lock.release()


@_scheduler_job("afternoon_snapshot_capture")
def _afternoon_snapshot_capture_cycle() -> None:
    """30-min dedicated capture for same-day SETTLEMENT_DAY markets (hours_to_resolution ≤12).

    Afternoon-capture fix (2026-06-14): the universe-wide market_discovery runs every
    5 min and the EDLI warm cycle runs every 20s — both target PENDING families and the
    full weather universe.  Neither explicitly targets the sub-12h same-day window that
    corresponds to the nowcast decision window (cities whose local-afternoon aligns with
    the pre-12:00 UTC capture window).  This job fills that gap by:

      1. Running a slug-pattern–only discovery scoped to TODAY (the slug fix above ensures
         today is always in the target-date list after UTC noon).
      2. Filtering to markets with hours_to_resolution in (0, 12] — the same-day
         afternoon window.
      3. Calling the standard rate-limited refresh_executable_market_substrate_snapshots
         so orderbook top-bid/ask + depth are recorded at ≥30-min cadence through the
         settlement window for every active same-day market.

    SAFETY / THROTTLE: reuses the existing CLOB capture path (refresh_executable_market_
    substrate_snapshots with a conservative 60s wall-clock budget).  max_outcomes=4 per
    city (the standard cap).  max_instances=1/coalesce prevents stacked CLOB fan-out.
    The job runs only when there are same-day markets open (hours_to_resolution > 0);
    if today's markets are already closed (past 12:00 UTC) find_slug_pattern_weather_
    markets returns [] and the job is a sub-100ms no-op.  NOT a trading or decision
    path — capture only, no belief/flag/order change.
    """
    if _defer_for_held_position_monitor("afternoon_snapshot_capture"):
        return
    acquired = _market_substrate_refresh_lock.acquire(blocking=False)
    if not acquired:
        logger.info("afternoon_snapshot_capture: skipped — substrate refresh already running")
        return
    try:
        from src.data.market_scanner import (
            find_slug_pattern_weather_markets,
            refresh_executable_market_substrate_snapshots,
        )
        from src.data.polymarket_client import PolymarketClient
        from src.state.db import get_trade_connection

        # Slug-pattern–only fetch scoped to same-day markets with ≤12h to resolution.
        # hours_to_resolution ≤12 catches the SETTLEMENT_DAY window; >0 excludes
        # already-expired markets (end_at <= now) which Gamma returns empty for.
        # find_slug_pattern_weather_markets always includes today (slug fix above).
        now_utc = datetime.now(timezone.utc)
        events = find_slug_pattern_weather_markets(min_hours_to_resolution=0.0)
        same_day_events = [
            e for e in events
            if isinstance(e, dict)
            and e.get("hours_to_resolution") is not None
            and 0 < float(e["hours_to_resolution"]) <= 12.0
        ]
        if not same_day_events:
            logger.debug("afternoon_snapshot_capture: no same-day markets open (hours_to_resolution ≤12), skipping")
            return
        conn = get_trade_connection(write_class="live")
        try:
            _clob_timeout = max(
                1.0,
                float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "5.0")),
            )
            with PolymarketClient(public_http_timeout=_clob_timeout) as clob:
                summary = refresh_executable_market_substrate_snapshots(
                    conn,
                    markets=same_day_events,
                    clob=clob,
                    captured_at=now_utc,
                    scan_authority="VERIFIED",
                    refresh_reason="afternoon_snapshot_capture",
                    # Conservative 60s budget — same-day markets are a subset of the
                    # universe, so this runs well within the interval (30 min).  The
                    # standard per-city cap (ZEUS_MARKET_DISCOVERY_SNAPSHOT_MAX_OUTCOMES)
                    # applies — no unbounded fan-out.
                    budget_seconds=float(
                        os.environ.get("ZEUS_AFTERNOON_CAPTURE_BUDGET_SECONDS", "60.0")
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        logger.info(
            "afternoon_snapshot_capture: same_day_markets=%d executable_snapshots=%s",
            len(same_day_events),
            summary,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft; next tick retries
        logger.error(
            "afternoon_snapshot_capture: capture raised (non-fatal): %r", exc
        )
    finally:
        try:
            _market_substrate_refresh_lock.release()
        except RuntimeError:
            pass


@_scheduler_job("new_listing_scout")
def _new_listing_scout_cycle() -> None:
    """Lightweight 60s new-listing scout: detect brand-new Polymarket weather markets.

    Upstream real-time discovery gap (dimension a/b/c, operator 2026-06-09):
    The standard market_discovery runs every 5 minutes.  A brand-new listing
    (startDate just past) therefore has a ≤5-min discovery lag and then must
    wait for the next 00Z/12Z opendata wave (hours) before a forecast_posterior
    exists.  This scout closes two gaps:

    (a) DISCOVERY CADENCE: probes Gamma `order=startDate&ascending=false&limit=10`
        every 60s — a head-page diff for NEW condition_ids.  Cost: one HTTP GET
        per cycle (<100ms); no full universe scan.

    (b) POSTERIOR FAST-LANE: stages a replacement_forecast materialization intent
        for each new family so the producer can prioritize it rather than waiting
        for the next scheduled 00Z/12Z opendata wave.

    (c) WARMER PRIORITY: inserts new condition_ids into _NEW_FAMILY_CONDITION_IDS so
        _refresh_pending_family_snapshots prepends them to the warmer rotation head
        (they are warmed next cycle, not at tail of the round-robin).

    Fail-open: any exception is caught and logged; the live path is never affected.
    EDLI-gated: only fires when edli.enabled is True.
    """
    global _SCOUT_KNOWN_CONDITION_IDS, _NEW_FAMILY_CONDITION_IDS

    if _defer_for_held_position_monitor("new_listing_scout"):
        return
    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return

    try:
        import httpx
        from src.data.market_scanner import GAMMA_BASE, _gamma_get
        from src.state.db import ZEUS_FORECASTS_DB_PATH

        # (a) Probe head page: most recently started events
        try:
            resp = _gamma_get(
                "/events",
                params={"order": "startDate", "ascending": "false", "limit": "10"},
                timeout=10.0,
                retries=2,
            )
            if resp.status_code != 200:
                logger.debug("new_listing_scout: Gamma probe returned %s", resp.status_code)
                return
            raw_events = resp.json() if isinstance(resp.json(), list) else []
        except Exception as exc:
            logger.debug("new_listing_scout: Gamma probe failed (non-fatal): %r", exc)
            return

        # Extract condition_ids from all markets across the head-page events
        probe_condition_ids: set[str] = set()
        for ev in raw_events:
            for market in (ev.get("markets") or []):
                cid = str(market.get("conditionId") or "").strip()
                if cid:
                    probe_condition_ids.add(cid)

        if not probe_condition_ids:
            return

        # Initialise known set from DB on first run (or when empty)
        if not _SCOUT_KNOWN_CONDITION_IDS:
            try:
                import sqlite3
                conn = sqlite3.connect(str(ZEUS_FORECASTS_DB_PATH), timeout=10)
                try:
                    rows = conn.execute("SELECT condition_id FROM market_events WHERE condition_id IS NOT NULL").fetchall()
                    _SCOUT_KNOWN_CONDITION_IDS = {str(r[0]) for r in rows if r[0]}
                finally:
                    conn.close()
            except Exception as exc:
                logger.debug("new_listing_scout: known-set init failed (non-fatal): %r", exc)

        new_cids = probe_condition_ids - _SCOUT_KNOWN_CONDITION_IDS
        if not new_cids:
            # Update known set to include any probe IDs we haven't seen
            _SCOUT_KNOWN_CONDITION_IDS.update(probe_condition_ids)
            return

        logger.info(
            "new_listing_scout: %d new condition_id(s) detected on head-page probe: %s",
            len(new_cids),
            sorted(new_cids),
        )

        # Persist new events to market_events via standard discovery path
        try:
            from src.data.market_scanner import find_weather_markets_or_raise, _persist_market_events_to_db
            new_events = find_weather_markets_or_raise(min_hours_to_resolution=0.0, include_slug_pattern=True)
            _persist_market_events_to_db(new_events, db_path=ZEUS_FORECASTS_DB_PATH)
        except Exception as exc:
            logger.warning("new_listing_scout: persist new events failed (non-fatal): %r", exc)

        # (b) POSTERIOR FAST-LANE: stage a scout INTENT for each new family.
        #
        # CONTRACT FIX (2026-06-10): scout intents are condition_id-only stubs
        # {source, condition_id, enqueued_at, reason}. They are NOT fully-resolved
        # materialization request payloads (which require city, temperature_metric,
        # target_date, source_cycle_time, aifs input, ...). Writing stubs directly into
        # the materializer requests/ dir crashed the subprocess (KeyError) on every cycle
        # and starved ALL legitimate posterior production (772 stubs / 4 posteriors/h on
        # 2026-06-10 — see /tmp/materializer_collapse_report.md). Stage intents in the
        # non-queue scout_intents/ directory instead.
        #
        # CONSUMED-BY TODO: the seed→request builder pipeline
        # (src.data.replacement_forecast_seed_discovery.discover_replacement_forecast_materialization_seeds
        # / build_replacement_forecast_current_target_plan) does NOT yet read scout_intents/.
        # Until that consumption side is wired (resolve condition_id → city+metric+target_date
        # via executable_market_snapshots/topology, include as a scope hint in the next seed
        # build, then delete the consumed intent), this directory is WRITE-ONLY staging and
        # the fast-lane latency benefit degrades gracefully to the normal 00Z/12Z wave cadence.
        try:
            from src.data.replacement_forecast_production import (
                _replacement_forecast_live_materialization_queue_config,
            )
            from src.data.replacement_forecast_shadow_materialization_queue import _write_request
            from src.contracts.replacement_pipeline_files import validate_scout_intent
            from pathlib import Path

            queue_cfg = _replacement_forecast_live_materialization_queue_config()
            request_dir = queue_cfg.get("request_dir")
            if request_dir is not None:
                # scout_intents/ is a sibling staging dir of requests/ — never the queue's input.
                intents_dir = Path(str(request_dir)).parent / "scout_intents"
                intents_dir.mkdir(parents=True, exist_ok=True)
                for cid in sorted(new_cids):
                    intent_path = intents_dir / f"new_listing_scout_{cid}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
                    # BOUNDARY CONTRACT (2026-06-10): validate the intent stub against the
                    # SCOUT_INTENT schema before writing. This is the producer half of the
                    # contract that prevents the 2026-06-10 starvation category: the scout
                    # can only emit a well-formed intent, and the intent shape is explicitly
                    # distinct from a materialization REQUEST (the REQUEST validator rejects
                    # exactly this shape). Authority basis: pipeline-contract project.
                    intent = validate_scout_intent({
                        "source": "new_listing_scout",
                        "condition_id": cid,
                        "enqueued_at": datetime.now(timezone.utc).isoformat(),
                        "reason": "NEW_LISTING_FAST_LANE",
                    })
                    _write_request(intent_path, intent.to_dict())
                    logger.info("new_listing_scout: staged intent for %s → scout_intents/%s", cid, intent_path.name)
        except Exception as exc:
            logger.warning("new_listing_scout: intent staging failed (non-fatal): %r", exc)

        # (c) WARMER PRIORITY: mark new condition_ids for head-of-rotation in next warm cycle
        _NEW_FAMILY_CONDITION_IDS.update(new_cids)

        # Update known set
        _SCOUT_KNOWN_CONDITION_IDS.update(probe_condition_ids)

    except Exception as exc:
        logger.warning("new_listing_scout: outer guard (non-fatal): %r", exc)


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

    Grace windows (by uptime):
      < 4h   : WARNING log. Normal deploy window; no action (daemon may not have
               restarted yet after a deploy).
      4–24h  : ERROR log + state/deployment_freshness.json flag + pause_entries
               (reason='deployment_freshness_4h_divergence'). Trading paused to
               prevent operating on stale pricing logic.
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

    if current_sha == _boot_sha:
        return  # No divergence.

    uptime_hours: float = (_now - _boot_ts).total_seconds() / 3600.0

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
    elif uptime_hours >= 4.0:
        logger.error(
            "deployment_freshness_diverged_total: boot_sha=%s current_sha=%s "
            "uptime_hours=%.1f — merged code not reloaded; pausing entries",
            _boot_sha[:8], current_sha[:8], uptime_hours,
        )
        # Write advisory flag to dedicated state/deployment_freshness.json.
        # NOT control_plane.json — that file is overwritten on every cycle by
        # _write_control_payload (control_plane.py:119) which writes only
        # {commands, acks}. A dedicated file survives all control_plane writes.
        df_path = state_path("deployment_freshness.json")
        try:
            _df: dict = {
                "boot_sha": _boot_sha,
                "current_sha": current_sha,
                "uptime_hours": round(uptime_hours, 2),
                "detected_at": _now.isoformat(),
            }
            _tmp = str(df_path) + ".tmp"
            with open(_tmp, "w") as _f:
                json.dump(_df, _f, indent=2)
            os.replace(_tmp, str(df_path))
        except Exception as _exc:
            logger.warning("deployment_freshness: failed to write flag file: %s", _exc)
        # Pause new entries — prevents trading 5h+ on stale pricing code
        # (the exact 2026-05-17 incident class). Idempotent if already paused.
        try:
            from src.control.control_plane import pause_entries
            # issued_by="system_auto_pause" activates the idempotency guard in
            # control_plane._has_active_auto_pause_override — prevents duplicate
            # control_overrides rows and alert spam on every 60s tick.
            pause_entries(
                "deployment_freshness_4h_divergence",
                issued_by="system_auto_pause",
                effective_until=None,
            )
        except Exception as _exc:
            logger.error(
                "deployment_freshness: pause_entries failed (%s); "
                "entries NOT paused despite 4h staleness", _exc,
            )
    else:
        logger.warning(
            "deployment_freshness_diverged_total: boot_sha=%s current_sha=%s "
            "uptime_hours=%.1f — within grace window, no action",
            _boot_sha[:8], current_sha[:8], uptime_hours,
        )


_DEPLOYMENT_FRESHNESS_PAUSE_REASON = "deployment_freshness_4h_divergence"


def _boot_deployment_freshness_auto_resume() -> None:
    """Boot-time auto-resume: clear a deployment_freshness_4h_divergence pause when
    the operator has restarted the daemon with the current git HEAD SHA.

    Called AFTER _assert_live_safe_strategies_or_exit() (which hydrates _control_state
    via refresh_control_state) so is_entries_paused() / get_entries_pause_reason()
    reflect durable DB state, not stale in-memory defaults.

    Logic:
    - If entries are NOT paused → no-op.
    - If entries are paused for a reason OTHER than deployment_freshness_4h_divergence → no-op;
      do not clear operator-issued or other system pauses.
    - If entries are paused with reason=deployment_freshness_4h_divergence AND the current
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
        if pause_reason != _DEPLOYMENT_FRESHNESS_PAUSE_REASON:
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
            "deployment_freshness_auto_resume: cleared deployment_freshness_4h_divergence pause "
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
    - Wallet gate (_startup_wallet_check): NEVER overridable; hard exit on
      failure.

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

    if not ZEUS_WORLD_DB_PATH.exists():
        raise FileNotFoundError(f"{ZEUS_WORLD_DB_PATH} does not exist")
    conn = sqlite3.connect(
        f"file:{ZEUS_WORLD_DB_PATH.resolve()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        conn.execute("PRAGMA query_only = ON")
        assert_schema_current(conn)
        present = frozenset(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        )
        missing = _CANONICAL_WORLD_TABLES - present
        if missing:
            raise RuntimeError(
                f"world DB missing canonical tables: {sorted(missing)}"
            )
        return "ready"
    finally:
        conn.close()


def _startup_world_db_schema_prepare() -> str:
    """Idempotently run init_schema() on the world DB before read-only boot proof.

    Runs the idempotent init_schema() unconditionally so that ensure_table
    migrations added without a version bump are always executed on live DBs.
    Missing DBs still fail closed. B2 (2026-05-28) cancelled the schema-version
    counter mechanism entirely.
    """
    import src.state.db as db_module

    path = db_module.ZEUS_WORLD_DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")

    conn = db_module.get_world_connection(write_class="live")
    try:
        db_module.init_schema(conn)
        conn.commit()
        logger.info("world DB schema prepared at live boot: init_schema complete")
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
        timeout=5.0,
    )
    try:
        conn.execute("PRAGMA query_only = ON")
        assert_schema_current_forecasts(conn)
        present = frozenset(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        )
        missing = _CANONICAL_FORECASTS_TABLES - present
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
    Schema currency is verified directly against zeus-world.db and
    zeus-forecasts.db. This avoids binding live startup to stale JSON sentinels
    from retired or split data-daemon processes.

    Retry pattern: 30 × 10s = 5 min (mirrors _startup_freshness_check).
    """
    import time
    from src.control.freshness_gate import BOOT_RETRY_INTERVAL_SECONDS, BOOT_RETRY_MAX_ATTEMPTS

    for attempt in range(1, BOOT_RETRY_MAX_ATTEMPTS + 1):
        missing = []
        try:
            _startup_world_db_schema_prepare()
            logger.info("world DB schema prepared (init_schema complete)")
            _startup_world_db_schema_ready_check()
            logger.info("world DB schema structural check: ready")
        except Exception as exc:
            logger.warning("world DB schema readiness check failed: %s — retrying", exc)
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
    """P7: Fail-closed wallet gate. Live daemon refuses to start if wallet query fails.

    Accepts an optional clob for testing. In production, creates a live
    PolymarketClient.

    Also installs the process-wide CollateralLedger singleton with a
    persistent ledger-owned conn (2026-05-13 remediation). Prior to this
    the singleton was published from `PolymarketClient.get_balance()` while
    that wrapper still owned the conn — the wrapper's `finally: conn.close()`
    immediately poisoned the singleton, blocking every downstream
    `assert_buy_preflight` / `assert_sell_preflight` with
    `collateral_ledger_unconfigured` or `sqlite3.ProgrammingError`.
    """
    if clob is not None:
        # TEST-INJECTION PATH: an explicit clob was supplied. Use it directly
        # and keep the same fail-closed semantics. Production never reaches here.
        try:
            balance = float(clob.get_balance())
            logger.info("Startup wallet check: $%.2f pUSD available", balance)
        except Exception as exc:
            logger.critical("FAIL-CLOSED: wallet query failed at daemon start: %s", exc)
            sys.exit("FATAL: Cannot start — wallet unreachable. Fix credentials or network and restart.")
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
        # a cold cache it does a real fetch and still fail-closes on None.
        if bankroll_record is _WALLET_RECORD_UNSET:
            from src.runtime.bankroll_provider import current as _bankroll_current

            rec = _bankroll_current()
        else:
            rec = bankroll_record
        if rec is None:
            logger.critical(
                "FAIL-CLOSED: wallet query failed at daemon start "
                "(bankroll_provider returned None)"
            )
            sys.exit("FATAL: Cannot start — wallet unreachable. Fix credentials or network and restart.")
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


def _startup_data_health_check(conn):
    """Warn about deferred data actions on every startup.

    This exists because bias correction activation and Platt recompute
    are easy to forget. The warnings persist until the actions are taken.
    """
    try:
        # 1. Bias correction reminder (legacy baseline/diagnostics chain flag,
        # renamed from bias_correction_enabled → baseline_bias_correction_enabled
        # in T0-3 to disambiguate from edli.edli_bias_correction_enabled).
        bias_enabled = settings.baseline_bias_correction_enabled
        bias_data = conn.execute(
            "SELECT COUNT(*) FROM model_bias WHERE source='ecmwf' AND n_samples >= 20"
        ).fetchone()[0]

        if not bias_enabled and bias_data > 0:
            logger.warning(
                "⚠ DEFERRED ACTION: baseline_bias_correction_enabled=false but %d ECMWF bias "
                "entries ready for the legacy baseline diagnostics chain. This is not the live "
                "EDLI replacement probability authority. To activate that legacy baseline path: "
                "1) Recompute calibration_pairs with bias correction 2) Refit legacy Platt models "
                "3) Set baseline_bias_correction_enabled=true 4) Run test_cross_module_invariants.py",
                bias_data,
            )

        forecast_city_count = conn.execute(
            "SELECT COUNT(DISTINCT city) FROM forecast_skill"
        ).fetchone()[0]
        bias_city_count = conn.execute(
            "SELECT COUNT(DISTINCT city) FROM model_bias WHERE source='ecmwf' AND n_samples >= 20"
        ).fetchone()[0]
        configured_city_count = len(cities_by_name)
        if forecast_city_count < configured_city_count or bias_city_count < configured_city_count:
            logger.warning(
                "⚠ DATA QUALITY GAP: forecast_skill covers %d/%d configured cities; "
                "mature ECMWF model_bias covers %d/%d. Missing bias data falls back "
                "to raw ensemble member maxes, archive quality is incomplete (raw ensemble member maxes only).",
                forecast_city_count,
                configured_city_count,
                bias_city_count,
                configured_city_count,
            )

        # 2. Data freshness check
        from datetime import datetime, timezone, timedelta

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
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if n == 0:
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
      enabled = {s for s in KNOWN_STRATEGIES if is_strategy_enabled(s)}
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
    from src.engine.cycle_runner import KNOWN_STRATEGIES
    if refresh_state:
        refresh_control_state()
    enabled_strategies = {s for s in KNOWN_STRATEGIES if is_strategy_enabled(s)}
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


# WIRING FIX (operator Point-1 directive 2026-06-08): the BAYES_PRECISION_FUSION/replacement forecast
# PRODUCTION functions (raw-input download + light shadow materialization) were moved
# VERBATIM to src/data/replacement_forecast_production.py and are now SCHEDULED on the
# forecast-live (data) daemon, NOT here. The ~365MB AIFS ensemble fetch must never run
# inside the live-trading process (it monopolized disk I/O -> DATA_DEGRADED flap). They
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
    _replacement_forecast_shadow_materialization_queue_config,
    _replacement_forecast_shadow_materialize_cycle,
)


def _replacement_forecast_refit_decision_from_settings():
    from src.config import PROJECT_ROOT
    from src.data.replacement_forecast_refit_handoff import refit_decision_from_handoff_payload

    cfg = _settings_section("replacement_forecast_live", {}) or {}
    if not cfg:
        cfg = _settings_section("replacement_forecast_shadow", {}) or {}
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
# fed the runtime-policy resolver / switch-decision evaluator — both of which IGNORE
# these objects post-operator-severance (commits b646f99339 + 54a53334a9: LIVE_AUTHORITY
# is FLAG-ONLY). The two live-adapter call sites now pass None (the adapter default),
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
    # Warm the in-process bankroll-of-record cache once per cycle so the per-event no-submit
    # Kelly proof can read bankroll_provider.cached() (it must NOT live-fetch per decision).
    # The on-chain wallet is the only bankroll truth; this is a cycle-level refresh, not a
    # per-event side effect. Non-fatal — Kelly fails closed (KELLY_PROOF_MISSING) if unwarm.
    try:
        from src.runtime import bankroll_provider as _bankroll_provider

        # 2026-05-31: FORCE a fresh on-chain fetch each cycle (max_age_seconds=0.0) so the
        # per-event no-submit Kelly read (bankroll_provider.cached(), 300s window) always sees
        # a fresh value. The prior plain current() used the default 30s freshness and could
        # return a stale cache-hit without re-fetching, letting _last_fetched_at age past 300s
        # → cached() None → KELLY_PROOF_MISSING:bankroll_provider_unavailable on every event.
        # Cycle-level (not per-decision) — preserves #45's "no per-decision wallet fetch".
        _bk_warm = _bankroll_provider.current(max_age_seconds=0.0)
        if _bk_warm is None:
            logger.error(
                "EDLI reactor: bankroll warm current() returned None — cache cold, Kelly will "
                "fail closed (KELLY_PROOF_MISSING). On-chain wallet fetch is failing."
            )
    except Exception as _bk_exc:  # noqa: BLE001
        logger.warning("EDLI reactor: bankroll cache warm failed (non-fatal): %r", _bk_exc)
    try:
        from src.state.db import world_write_mutex as _world_write_mutex

        now = datetime.now(timezone.utc)
        received_at = now.isoformat()
        forecast_emit_limit = _edli_positive_int_or_unbounded(
            edli_cfg, "forecast_snapshot_emit_limit", default=20, maximum=50
        )
        day0_emit_limit = _edli_bounded_positive_int(edli_cfg, "day0_catchup_emit_limit", default=20, maximum=100)
        # FUNNEL-STARVATION FIX (2026-06-09): raise the per-cycle evaluation ceiling
        # so the reactor can sweep the FULL live FORECAST_SNAPSHOT_READY set
        # (~200 events across ~50 cities × 3 target dates) within one or two cycles
        # once the substrate warmer (now round-robin, see _SUBSTRATE_REFRESH_CURSOR)
        # keeps books fresh. The prior maximum=50 capped a cycle at 50 evaluations
        # regardless of config, so even with fresh books the live family set could
        # not be fully swept per cadence — a throttle on EVALUATION COVERAGE, the
        # exact thing the operator directive forbids (every live family must be
        # evaluated, honest no-edge only after a FULL evaluation). The reactor's own
        # 30s wall-clock budget (ZEUS_REACTOR_CYCLE_BUDGET_SECONDS, in reactor.py)
        # remains the real safety bound on cycle length; this ceiling just stops
        # truncating the admissible queue below the live family count. Economic
        # gates (q_lcb, cost floor, Kelly, depth) are untouched.
        # Wave-1 2026-06-12: the no_submit_proof_limit production cap is DELETED. It
        # truncated how many pending families the reactor processed per cycle — and thus
        # how many no-submit proofs/receipts were persisted — which silently dropped the
        # tail of the admissible queue (an artificial throttle the operator law forbids).
        # proof_limit is now UNBOUNDED (None): every pending family is processed and every
        # proof persists. The reactor's own 30s wall-clock budget
        # (ZEUS_REACTOR_CYCLE_BUDGET_SECONDS, reactor.py) remains the real, honest safety
        # bound on cycle length; any family not reached this cycle requeues for the next.
        proof_limit = None
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
        # EDLI live contention fix (2026-05-31): the FSR/Day0/redecision
        # EMIT block writes opportunity_events to the WAL zeus-world.db shared
        # in-process with the market-channel ingestor. Serialize the whole
        # prune+emit+commit unit under the process-global world-DB write mutex so it
        # never holds the WAL write lock concurrently with the ingestor (no HTTP
        # is done inside this block — the emit reads forecasts/trade DBs and
        # writes world — so the mutex stays short and never spans a venue fetch).
        # Explicit acquire/finally (not ``with``) to avoid reindenting the block.
        _emit_mutex = _world_write_mutex()
        _emit_mutex.acquire()
        try:
            _edli_prune_pending_working_set(store, decision_time=now)
            if edli_cfg.get("forecast_snapshot_trigger_enabled"):
                # FAIL-SOFT (2026-05-31): the FSR event-emit is the queue-FILL step, writing
                # opportunity_events to the WAL world DB shared with the market-channel
                # ingestor and CollateralLedger heartbeat. Under live load that DB hits
                # transient "database is locked" past the 30s busy_timeout. A locked-out
                # emit must NOT crash the whole reactor cycle — the cycle should still drain
                # candidates already queued from prior cycles. Catch ONLY the transient lock
                # (narrow, by message) and continue; real schema/logic faults still propagate.
                try:
                    # COVERAGE-FAIRNESS (universe-collapse fix 2026-06-04; Wave-1 2026-06-12:
                    # now UNCONDITIONAL). The fairness round-robin rotates the selection
                    # window by cycle_index, parsed from a monotonic `cycle-N` source — fed
                    # here so every cycle advances the window and re-emits. Covers all
                    # (city,metric) families in ceil(N/limit) cycles. The former
                    # coverage_fairness_emit_enabled flag (and the None-source one-shot OFF
                    # path that left A-L permanently dark) is DELETED.
                    _fair_source = _edli_next_redecision_source()
                    _edli_emit_forecast_snapshot_events(
                        conn,
                        decision_time=now,
                        received_at=received_at,
                        limit=forecast_emit_limit,
                        source=_fair_source,
                    )
                except sqlite3.OperationalError as _emit_lock_exc:
                    if "locked" in str(_emit_lock_exc).lower() or "busy" in str(_emit_lock_exc).lower():
                        logger.warning(
                            "EDLI reactor: forecast-snapshot emit hit transient world-DB lock "
                            "(%r) — skipping emit this cycle, draining already-queued candidates.",
                            _emit_lock_exc,
                        )
                    else:
                        raise
            # Continuous re-decision (Wave-1 2026-06-12: now UNCONDITIONAL when event writing
            # is enabled — this is the fill-rate ORGAN, not an optional feature). Re-emit
            # FSR-equivalent events for committed market-backed families each cycle, with a
            # per-cycle distinct source so they do NOT dedup to the consumed FSR. Routing
            # through the pending path makes _refresh_pending_family_snapshots capture fresh
            # prices just-in-time → the reactor re-decides every ~60s instead of once per 12h
            # forecast. The former redecision_continuous_enabled gate and redecision_max_per_cycle
            # cap are DELETED. Coverage is governed by the WRAPPING fair cursor: a monotonic
            # `cycle-N` source advances the round-robin window (which now wraps modulo the
            # family count — see CoverageFairnessRequest.select_rows), so a fixed per-cycle
            # batch reaches EVERY family within ceil(N/batch) cycles and NONE is ever dropped.
            # already_pending skip avoids duplicate piling. Non-fatal: never breaks the cycle.
            if True:
                try:
                    # Fixed per-cycle batch fed to the wrapping fair cursor (no settings cap):
                    # full coverage in ceil(N/batch) cycles, no silent tail drop.
                    _rd_batch = _EDLI_REDECISION_FAIR_BATCH
                    _rd_source = _edli_next_redecision_source()
                    _rd_pending = _edli_pending_entity_keys(conn)
                    _rd_n = _edli_emit_forecast_snapshot_events(
                        conn,
                        decision_time=now,
                        received_at=received_at,
                        limit=_rd_batch,
                        source=_rd_source,
                        already_pending_keys=_rd_pending,
                    )
                    logger.info(
                        "edli_redecision: enqueued=%d batch=%d skipped_pending=%d (wrapping fair cursor)",
                        _rd_n, _rd_batch, len(_rd_pending),
                    )
                except Exception as _rd_exc:  # noqa: BLE001 — continuous re-decision is non-fatal
                    logger.warning("edli_redecision: enqueue failed (non-fatal): %r", _rd_exc)
            if (
                edli_cfg.get("day0_extreme_trigger_enabled")
                and edli_cfg.get("day0_authority_catchup_scanner_enabled", False)
            ):
                _day0_trade_conn = get_trade_connection_with_world_required(write_class=None)
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
                        # 2026-06-11 anti-starvation: stamp the scope-aware emission
                        # priority so day0_shadow events sub-sort below tradeable FSR.
                        day0_is_tradeable=day0_is_tradeable_for_scope(
                            str(edli_cfg.get("edli_live_scope") or "forecast_only")
                        ),
                    )
                finally:
                    _day0_trade_conn.close()
            # Commit the emit WRITE UNIT (FSR + redecision + day0 → opportunity_events)
            # while still holding the world-DB write mutex, so the WAL write lock is
            # released by the COMMIT before any other writer (ingestor / collateral
            # heartbeat) can interleave. No HTTP/venue work runs inside this block.
            conn.commit()
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
        reactor_mode = str(edli_cfg.get("reactor_mode", "live_no_submit"))
        edli_live_scope = str(edli_cfg.get("edli_live_scope") or "forecast_only")
        real_order_submit_enabled = bool(edli_cfg.get("real_order_submit_enabled", False))
        submit_disabled_effective_mode = reactor_mode == "live_no_submit"
        live_bridge_mode = reactor_mode in {"live", "submit_disabled_live_bridge"}
        real_submit_effective = real_order_submit_enabled if reactor_mode == "live" else False
        # Configure the process-wide risk allocator/governor BEFORE the submit adapter is
        # built so the live submit path's select_global_order_type does not raise
        # AllocationDenied("allocator_not_configured"). The legacy discover cycle wires this
        # via refresh_global_allocator; the EDLI cycle does not run that cycle, so without
        # this seam every canary order silently blocks (see /tmp/edli_submit_gate_trace.md).
        # FAIL-CLOSED: if the refresh cannot source a trustworthy drawdown (wallet unreachable
        # / baseline undefined / exception), degrade THIS cycle to the no-submit adapter rather
        # than submit live with an unconfigured-but-proceeding allocator.
        # SUBMIT-LANE STAMP (silent-trade-kill antibody 2026-06-12): track the TYPED
        # cause whenever a degrade clears live_submit_effective so the no-submit adapter
        # can name it on every full-pass receipt it consumes (single source of truth —
        # the same value that drove the selector off the live lane). None => no degrade
        # (the live lane was simply not configured for this reactor_mode).
        _live_lane_degrade_cause: str | None = None
        live_submit_effective = live_bridge_mode or submit_disabled_effective_mode
        if live_submit_effective:
            _alloc_refresh = _edli_refresh_global_allocator_for_live_bridge(trade_conn)
            if live_bridge_mode and not _alloc_refresh.get("configured"):
                live_submit_effective = False
                _alloc_reason = _alloc_refresh.get("entry", {}).get("reason") or "allocator_not_configured"
                _live_lane_degrade_cause = f"live_submit_effective_false:allocator_refresh:{_alloc_reason}"
                logger.error(
                    "EDLI reactor: live-bridge allocator refresh did not configure "
                    "(fail_closed=%r reason=%r) — degrading to NO-SUBMIT this cycle.",
                    _alloc_refresh.get("fail_closed"),
                    _alloc_refresh.get("entry", {}).get("reason"),
                )
        # Task #107 (portfolio/multi Kelly): source the PortfolioState ONCE per
        # reactor cycle (DB-only, microseconds) so per-event Kelly sizes against
        # the bankroll NET of correlation-weighted committed capital. The
        # provider closure hands the SAME cached snapshot to every event this
        # cycle (cycle-level read, not per-decision — mirrors the bankroll warm).
        # Shadow remains observational when the portfolio snapshot is unavailable.
        # Real-submit is different: never let the live path fall back to pre-#107
        # single-asset Kelly sizing, because that ignores open/pending/correlated
        # exposure.
        _portfolio_state_provider = None
        try:
            _portfolio_snapshot = load_portfolio()
            _portfolio_state_provider = lambda: _portfolio_snapshot  # noqa: E731 — cycle-scoped closure
        except Exception as _portfolio_exc:  # noqa: BLE001 — mode-sensitive fail-closed below
            logger.warning(
                "EDLI reactor: portfolio snapshot load failed; shadow may observe "
                "with single-asset sizing, but real-submit will fail closed: %r",
                _portfolio_exc,
            )
        if real_submit_effective and _portfolio_state_provider is None:
            live_submit_effective = False
            _live_lane_degrade_cause = "live_submit_effective_false:portfolio_state_unavailable"
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
        # switch-decision evaluator IGNORE these evidence objects post-severance
        # (LIVE_AUTHORITY is FLAG-ONLY). None is behavior-identical to the deleted parsers.
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
        # SUBMIT-LANE STAMP + CYCLE-LEVEL DEGRADE SIGNAL (silent-trade-kill antibody
        # 2026-06-12; /tmp/allpass_nosubmit_rootcause.md). The selector picks the live
        # adapter ONLY when (live_submit_effective AND operator_arm is not None); else
        # the no-submit (degrade) adapter. Resolve the TYPED cause once, here, so it is
        # the single source of truth threaded onto the degrade lane's receipts.
        _edli_live_operator_authorized = edli_cfg.get("edli_live_operator_authorized") is True
        _live_lane_selected = bool(live_submit_effective and operator_arm is not None)
        if operator_arm is None and _live_lane_degrade_cause is None:
            _live_lane_degrade_cause = "operator_arm_none"
        if _live_lane_degrade_cause is None and not _live_lane_selected:
            # live_submit_effective was False without a tracked degrade (the live lane is
            # simply not configured for this reactor_mode, e.g. live_no_submit/shadow).
            _live_lane_degrade_cause = f"live_lane_unselected:reactor_mode={reactor_mode}"
        _no_submit_degrade_cause = _live_lane_degrade_cause or "live_lane_unselected"
        # LOUD cycle-level degrade signal: the live lane is dark THIS cycle while the
        # operator has nominally armed it (reactor_mode=live + operator_authorized). The
        # crash-loop incident ran ~50 min on the no-submit lane with the arm on and NO
        # decision-lane signal. One ERROR per cycle here makes it impossible to miss.
        if not _live_lane_selected and _edli_live_operator_authorized and reactor_mode == "live":
            logger.error(
                "LIVE LANE DARK: no-submit adapter selected while operator arm is on "
                "(reactor_mode=live, edli_live_operator_authorized=True) — cause=%s. "
                "Full-pass candidates this cycle are consumed on the NO_SUBMIT_ADAPTER "
                "lane (receipts stamped with this cause); the live lane submitted nothing.",
                _no_submit_degrade_cause,
            )
        # Decision-triggered targeted family snapshot refresher (zero-order wall fix
        # 2026-06-11): when the adapter is about to decide and the SELECTED bin's
        # elected snapshot row is price-stale, it captures FRESH books for THAT family
        # NOW through the sanctioned warm-job capture path, then re-elects the latest
        # row. Synchronizes the warm-job's ~5.4min per-family cadence with the 30s
        # decision price-freshness window at the only point that matters. Topology
        # authority = forecasts_conn (owns market_events); snapshot WRITE = zeus_trades.
        _decision_family_snapshot_refresher = _edli_decision_family_snapshot_refresher(
            forecasts_conn
        )
        # ALWAYS-DECIDABLE invariant (operator law 2026-06-12): the reactor itself must make a
        # blocked event's substrate fresh, so a transient SUBSTRATE block can never requeue
        # forever without a refresh attempt. The SAME decision-time refresher is threaded INTO the
        # reactor (Build 1: executable-snapshot blocks AT the reactor gate now invoke it), plus a
        # single-family cycle-advance reseed enqueuer (Build 2: stale/absent replacement-posterior
        # blocks enqueue a targeted re-materialization). Both run from the reactor's end-of-cycle
        # drain — outside any world/trade txn (three-phase law), debounced, fail-soft.
        _reactor_cycle_advance_enqueuer = _edli_reactor_cycle_advance_enqueuer()
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
                live_cap_conn=conn,
                replacement_forecast_runtime_flags=replacement_forecast_runtime_flags,
                replacement_forecast_baseline_bundle_provider=replacement_forecast_baseline_bundle_provider,
                replacement_forecast_world_tables=replacement_forecast_world_tables,
                replacement_forecast_source_fact_status=replacement_forecast_source_fact_status,
                replacement_forecast_data_fact_status=replacement_forecast_data_fact_status,
                replacement_forecast_refit_decision=replacement_forecast_refit_decision,
                replacement_forecast_promotion_evidence=replacement_forecast_promotion_evidence,
                replacement_forecast_capital_objective_evidence=replacement_forecast_capital_objective_evidence,
                pre_submit_authority_provider=_edli_pre_submit_authority_provider_from_world_conn(
                    conn,
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
                # FIX-3 (P1): pass scope so the adapter can enforce
                # DAY0_SCOPE_SHADOW_ONLY at the final submit boundary.
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
                # SUBMIT-LANE STAMP: name the degrade cause that selected this lane so a
                # full-pass receipt consumed here can never be confused with a genuine
                # decision-declined no-submit (single source of truth from the selector).
                degrade_cause=_no_submit_degrade_cause,
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
            family_snapshot_refresher=_decision_family_snapshot_refresher,
            cycle_advance_enqueuer=_reactor_cycle_advance_enqueuer,
            # Held-position families are refreshed FIRST (money at risk); NO liquidity ordering
            # (operator correction 2026-06-12). Fail-soft read-only provider on zeus_trades.
            held_family_provider=_edli_reactor_held_family_provider(),
            config=ReactorConfig(
                reactor_mode=reactor_mode,
                real_order_submit_enabled=real_order_submit_enabled,
                # Task #102 book-wide edge-zone admission. Absent key => default
                # False => byte-identical legacy money-path (the operator owns
                # config/settings.json; this reads it without writing it).
                # Scope-aware claim tier (2026-06-11 anti-starvation): under
                # day0_shadow a DAY0_EXTREME_UPDATED event can only ever produce a
                # DAY0_SCOPE_SHADOW_ONLY receipt, so it must NOT outrank tradeable
                # FORECAST_SNAPSHOT_READY in the reactor claim. day0_is_tradeable
                # is True ONLY for the forecast_plus_day0 (day0-submittable) lane.
                day0_is_tradeable=day0_is_tradeable_for_scope(edli_live_scope),
                # SUBMIT-LANE PERSIST-BOUNDARY INVARIANT (silent-trade-kill antibody
                # 2026-06-12): the SAME operator-arm authority the selector above reads,
                # threaded so the reactor's no-submit persist boundary can recognise a
                # nominally-armed live daemon and refuse to silently book a LIVE-stamped
                # full-pass NO_SUBMIT. Not a second authority — the same flag value.
                edli_live_operator_authorized=_edli_live_operator_authorized,
            ),
        )
        _rr = reactor.process_pending(decision_time=process_pending_decision_time, limit=proof_limit)
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
    """Dedicated frequent (~60s) on-chain bankroll-of-record cache warmer.

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

    This job DECOUPLES freshness from the reactor cycle: it runs on its own ~60s
    cadence and forces a fresh on-chain fetch (``current(max_age_seconds=0.0)``),
    advancing ``_last_fetched_at`` so ``cached()`` (300s window) always resolves
    regardless of how long the reactor cycle takes. It does NOT widen the
    ``cached()`` window or weaken any fail-closed semantics — the consumers still
    fail-closed correctly when bankroll is genuinely unavailable.

    Not a DB writer (no table owned) — the @_scheduler_job decorator is the only
    wiring needed (B047 observability). Fail-soft: a transient wallet-RPC failure
    logs an ERROR but never crashes this job; a failed warm simply means this tick's
    freshness did not advance (the next tick retries in ~60s, and consumers stay
    fail-closed in the interim).
    """

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    from src.runtime import bankroll_provider as _bankroll_provider

    try:
        warm = _bankroll_provider.current(max_age_seconds=0.0)
    except Exception as exc:  # noqa: BLE001 — fail-soft; consumers fail-closed on None
        logger.error(
            "EDLI bankroll warm: on-chain wallet fetch raised (non-fatal, freshness "
            "did not advance this tick): %r",
            exc,
        )
        return
    if warm is None:
        logger.error(
            "EDLI bankroll warm: current() returned None — on-chain wallet fetch is "
            "failing; cached() will fail closed (KELLY_PROOF_MISSING) until it recovers."
        )


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

    summary = reconcile_unresolved_commands()
    if summary.get("scanned"):
        logger.info("edli_command_recovery: %s", summary)


def _escalation_families_from_cancelled(
    cancelled: list[dict],
    trade_conn,
    forecasts_conn,
) -> set[tuple[str, str, str]]:
    """Recover the ``(city, target_date, metric)`` family key for each just-cancelled
    escalation rest, from VENUE TRUTH (no cached-belief dependency).

    Path (both legs are the canonical, already-proven joins):
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
    token_ids = {str(e.get("token_id") or "") for e in cancelled if e.get("token_id")}
    if not token_ids:
        return set()
    cond_by_token: dict[str, str] = {}
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
    cond_ids = {c for c in cond_by_token.values() if c}
    if not cond_ids:
        return set()
    families: set[tuple[str, str, str]] = set()
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
        return set()
    return families


def _emit_escalation_cross_redecisions(
    families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
    received_at: str,
) -> int:
    """Emit ONE escalation-origin EDLI_REDECISION_PENDING per just-cancelled, ARMED
    family so the reactor re-decides it on the NEXT cycle (it jumps the 49-deep
    per-city round-robin via its Tier-0 claim, redecide-block fix 2026-06-16).

    Routed through the EXISTING FSR re-emit machinery (``scan_committed_snapshots``
    with ``restrict_to_families``), so the payload is the identical committed-snapshot
    FSR shape the forecast-decision pipeline already binds — only the trigger label
    (EDLI_REDECISION_PENDING) and the ``escalation_cross-`` source differ. The world
    DB event-write runs under the world-write mutex (mirrors ``_edli_emit_*`` and the
    continuous-redecision screen), so the WAL write lock is released by COMMIT before
    any other writer interleaves.

    ``already_pending_keys`` is DELIBERATELY NOT passed: a pending FSR for this family
    is exactly what is stuck behind the round-robin, so the escalation re-decision
    must be emitted regardless of it (the Tier-0 lane is what un-starves it). The
    phase/strictly-past floors inside scan_committed_snapshots still apply
    (fail-closed: a phase-closed family emits zero — the cross cannot happen anyway).
    """
    if not families:
        return 0
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
        emitted = trig.scan_committed_snapshots(
            forecasts_conn=forecasts_ro,
            decision_time=decision_time,
            received_at=received_at,
            limit=None,
            source=_edli_next_escalation_cross_source(),
            event_type="FORECAST_SNAPSHOT_READY",
            restrict_to_families=families,
        )
        world.commit()
        return len(emitted)
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


@_scheduler_job("maker_rest_escalation")
def _maker_rest_escalation_cycle() -> None:
    """K4.0 REST-THEN-CROSS deadline owner (consolidated overhaul 2026-06-11).

    Cancels post_only GTC ENTRY rests older than the measured escalation
    deadline (maker_rest_escalation_deadline, 2.0h MEASURED — KM hazard curve
    on n=108 resting facts). GTC rests have NO other TTL owner. The job is
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
    from src.execution.maker_rest_escalation import (
        find_expired_resting_entries,
        run_cancels_for_expired_rests,
    )
    from src.state.db import get_trade_connection_read_only

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
    finally:
        conn.close()

    clob = PolymarketClient()
    # ESCALATION RE-DECISION (redecide-block fix 2026-06-16): harvest the families
    # whose rest was CONFIRMED-cancelled so we can emit a Tier-0 re-decision for
    # each — the just-cancelled, ARMED family crosses as TAKER_ESCALATED_AFTER_REST
    # on the NEXT cycle instead of waiting ~2-3h for the 49-deep per-city
    # round-robin. The collect rides an out-parameter so `stats` stays byte-identical.
    cancelled_entries: list[dict] = []
    stats = run_cancels_for_expired_rests(
        expired, clob, collect_cancelled=cancelled_entries
    )
    if stats["scanned"]:
        logger.info("maker_rest_escalation: %s", stats)

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
            emitted = _emit_escalation_cross_redecisions(
                families, decision_time=now, received_at=now.isoformat()
            )
            logger.info(
                "maker_rest_escalation: escalation re-decision emit "
                "cancelled=%d families_resolved=%d events_emitted=%d",
                len(cancelled_entries), len(families), emitted,
            )
        except Exception as _redecide_exc:  # noqa: BLE001 — fail-closed: never crash the cancel job
            logger.warning(
                "maker_rest_escalation: escalation re-decision emit failed "
                "(non-fatal; family will wait for the round-robin): %r",
                _redecide_exc,
            )


def _edli_open_maker_rests_for_screen(trade_conn, world_conn) -> "list":
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
    placeholders = ",".join("?" for _ in OPEN_REST_FACT_STATES)
    rows = trade_conn.execute(
        f"""
        WITH latest_facts AS (
            SELECT venue_order_id, state,
                   ROW_NUMBER() OVER (
                       PARTITION BY venue_order_id ORDER BY local_sequence DESC
                   ) AS rn
            FROM venue_order_facts
        )
        SELECT vc.command_id, vc.venue_order_id, vc.token_id, vc.market_id,
               vc.side, vc.price, vc.snapshot_id, vc.created_at
        FROM venue_commands vc
        JOIN latest_facts lf
          ON lf.venue_order_id = vc.venue_order_id AND lf.rn = 1
        WHERE vc.intent_kind = 'ENTRY'
          AND vc.venue_order_id IS NOT NULL AND vc.venue_order_id != ''
          AND lf.state IN ({placeholders})
        """,
        tuple(OPEN_REST_FACT_STATES),
    ).fetchall()
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
        command_id, venue_order_id, token_id, market_id, side, price, snap_id, created_at = (
            str(r[0] or ""), str(r[1] or ""), str(r[2] or ""), str(r[3] or ""),
            str(r[4] or ""), r[5], str(r[6] or ""), str(r[7] or ""),
        )
        cond = cond_by_token.get(token_id, "")
        belief_hit = bin_by_cond.get(cond)
        family_id = belief_hit[0].family_id if belief_hit else ""
        bin_label = belief_hit[1] if belief_hit else ""
        resting_posterior = belief_hit[2] if belief_hit else 0.0
        # quote_age_ms from the command's creation (the order has rested since created_at).
        try:
            from datetime import datetime as _dt
            age_ms = max(0.0, (now - _dt.fromisoformat(created_at)).total_seconds() * 1000.0) if created_at else 0.0
        except Exception:  # noqa: BLE001
            age_ms = 0.0
        screen_side = side_by_token.get(token_id, "buy_yes")
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
    if str(edli_cfg.get("reactor_mode", "live_no_submit")) != "live":
        return
    if _defer_for_held_position_monitor("edli_redecision_screen"):
        return
    if not _edli_redecision_screen_lock.acquire(blocking=False):
        logger.info("edli_redecision_screen skipped: previous screen still running")
        return
    try:
        from datetime import datetime, timezone
        from src.events.continuous_redecision import (
            screen_entry_redecisions,
            screened_family_keys,
            screen_resting_orders,
            REDECISION_EVENT_TYPE,
        )
        from src.state.db import (
            get_world_connection_read_only,
            get_trade_connection_read_only,
            get_world_connection,
            ZEUS_FORECASTS_DB_PATH,
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
            redecisions = screen_entry_redecisions(
                world_ro,
                trade_ro,
                decision_time=received_at,
                min_edge=min_edge,
                acted_state=_edli_redecision_acted_state,
            )
            family_keys = screened_family_keys(world_ro, redecisions)
            open_rests = _edli_open_maker_rests_for_screen(trade_ro, world_ro)
            rest_pulls = screen_resting_orders(world_ro, trade_ro, open_rests=open_rests)
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
            from src.events.continuous_redecision import _all_latest_beliefs as _alb
            world_ro2 = get_world_connection_read_only()
            try:
                by_family = {
                    b.family_id: (b.city, b.target_date, b.metric) for b in _alb(world_ro2)
                }
            finally:
                try:
                    world_ro2.close()
                except Exception:  # noqa: BLE001
                    pass
            for rest, _decision in rest_pulls:
                key = by_family.get(rest.family_id)
                if key is not None and all(key):
                    rest_pull_families.add(key)
        all_families = set(family_keys) | rest_pull_families
        if not all_families:
            logger.info(
                "edli_redecision_screen: entry_fired=%d rest_pulls=%d families_reemitted=0 "
                "events_emitted=0 rests_cancelled=0 reason=no_screened_families",
                len(redecisions),
                len(rest_pulls),
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

        world = get_world_connection()
        try:
            _att = {row[1] for row in world.execute("PRAGMA database_list").fetchall()}
            if "forecasts" not in _att:
                world.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
        except Exception:  # noqa: BLE001
            pass
        forecasts_ro = get_forecasts_connection_read_only()
        emit_mutex = _world_write_mutex()
        emit_mutex.acquire()
        try:
            trig = ForecastSnapshotReadyTrigger(
                EventWriter(world),
                live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_ro),
            )
            pending = _edli_pending_entity_keys(world)
            emitted = trig.scan_committed_snapshots(
                forecasts_conn=forecasts_ro,
                decision_time=now,
                received_at=received_at,
                limit=rd_cap,
                source=_edli_next_redecision_source(),
                already_pending_keys=pending,
                event_type=REDECISION_EVENT_TYPE,
                restrict_to_families=all_families,
            )
            world.commit()
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

        # 3) CANCEL the pulled rests via the EXISTING maker_rest_escalation cancel path (no new
        #    venue call site). The next reactor cycle re-decides the re-emitted family at fresh price.
        cancelled = 0
        if rest_pulls and get_mode() == "live":
            from src.data.polymarket_client import PolymarketClient
            from src.execution.maker_rest_escalation import run_cancels_for_expired_rests

            to_cancel = [
                {"command_id": rest.command_id, "venue_order_id": rest.venue_order_id,
                 "created_at": "", "fact_state": "", "matched_size": None}
                for rest, _decision in rest_pulls
            ]
            cstats = run_cancels_for_expired_rests(to_cancel, PolymarketClient())
            cancelled = cstats.get("cancelled", 0)

        logger.info(
            "edli_redecision_screen: entry_fired=%d rest_pulls=%d families_reemitted=%d "
            "events_emitted=%d rests_cancelled=%d",
            len(redecisions), len(rest_pulls), len(all_families), len(emitted), cancelled,
        )
    finally:
        _edli_redecision_screen_lock.release()


@_scheduler_job("edli_market_substrate_warm")
def _edli_market_substrate_warm_cycle() -> None:
    """Dedicated EDLI executable-snapshot substrate warmer, DECOUPLED from the reactor.

    THROUGHPUT STRUCTURAL FIX (2026-06-01): _refresh_pending_family_snapshots makes a
    full-universe Gamma scan (find_weather_markets → _get_active_events, benchmarked
    ~76s COLD; TTL 300s so it re-ran nearly every cycle) + per-token CLOB /book capture
    across all pending-family bins. Running it INLINE at the top of
    _edli_event_reactor_cycle made the reactor's wall-clock blow past its 1-min
    APScheduler interval — with max_instances=1/coalesce=True, every overlapping trigger
    was skipped, so process_pending essentially never ran (23 min with ZERO completed
    cycles / ZERO trades observed on the live daemon, even though the submit path is
    CODE-CLEAR to the venue POST boundary).

    Moving the refresh here (mirroring _edli_bankroll_warm_cycle, #45) puts the expensive
    venue-I/O on its OWN cadence so the reactor reads ALREADY-captured snapshots
    (DB-only, microseconds) and reaches submit in seconds. This changes NO decision: the
    reactor's no-submit proof, full gate chain, and just-in-time submit /book are
    byte-for-byte unchanged — they just consume snapshots a background job produced.
    Fail-closed is preserved: a family not yet captured this tick requeues via the
    reactor's existing EXECUTABLE_SNAPSHOT_RETRY path.

    Not a DB writer of its own ledger — it delegates to _refresh_pending_family_snapshots,
    which owns its write trade connection + commit. The @_scheduler_job decorator is the
    only wiring needed (B047). Fail-soft: a transient Gamma/CLOB failure logs but never
    crashes this job (the next tick retries; consumers stay fail-closed in the interim).
    """

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    if _defer_for_held_position_monitor("EDLI market-substrate warm"):
        return
    from src.state.db import ZEUS_FORECASTS_DB_PATH, get_forecasts_connection_read_only, get_world_connection

    conn = get_world_connection()
    # K1: the snapshot refresh reads market topology off the forecasts DB (market_events).
    # Attach read-only (idempotent) so the family-topology lookup resolves, mirroring the
    # reactor's own ATTACH. _refresh_pending_family_snapshots opens its own WRITE trade
    # connection internally and commits — this conn is only the world-side pending-event
    # reader.
    try:
        _attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" not in _attached:
            conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
    except Exception as _attach_exc:  # noqa: BLE001 — non-fatal; refresh logs+skips on topology miss
        logger.warning(
            "EDLI market-substrate warm: ATTACH forecasts failed (non-fatal): %r", _attach_exc
        )
    forecasts_conn = get_forecasts_connection_read_only()
    substrate_acquired = _market_substrate_refresh_lock.acquire(blocking=False)
    if not substrate_acquired:
        logger.info("EDLI market-substrate warm skipped: executable substrate refresh already running")
        try:
            forecasts_conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        # _refresh_pending_family_snapshots never raises by contract (it logs+returns an
        # error dict), but wrap defensively so a venue-I/O failure can NEVER propagate out
        # of the scheduler job (the reactor stays decoupled and fail-closed regardless).
        summary = _refresh_pending_family_snapshots(conn, forecasts_conn)
        logger.info("EDLI market-substrate warm: refresh summary=%r", summary)
    except Exception as exc:  # noqa: BLE001 — fail-soft; next tick retries
        logger.error(
            "EDLI market-substrate warm: refresh raised (non-fatal, snapshots did not "
            "advance this tick): %r",
            exc,
        )
    finally:
        try:
            _market_substrate_refresh_lock.release()
        except RuntimeError:
            pass
        try:
            forecasts_conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


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
    from src.events.triggers.forecast_snapshot_ready import (
        ForecastSnapshotReadyTrigger,
        executable_forecast_live_eligible_reader,
    )
    from src.state.db import get_forecasts_connection_read_only

    forecasts_conn = get_forecasts_connection_read_only()
    try:
        trigger = ForecastSnapshotReadyTrigger(
            EventWriter(world_conn),
            live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_conn),
        )
        return len(
            trigger.scan_committed_snapshots(
                forecasts_conn=forecasts_conn,
                decision_time=decision_time,
                received_at=received_at,
                limit=limit,
                source=source,
                already_pending_keys=already_pending_keys,
            )
        )
    finally:
        forecasts_conn.close()


def _edli_pending_entity_keys(world_conn) -> set[str]:
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
        try:
            rows = world_conn.execute(
                """
                SELECT DISTINCT e.entity_key
                FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
                JOIN opportunity_events e ON e.event_id = p.event_id
                WHERE p.consumer_name = 'edli_reactor_v1'
                  AND p.processing_status IN ('pending', 'processing', 'claimed')
                  AND e.event_type = 'FORECAST_SNAPSHOT_READY'
            """
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


def _edli_prune_pending_working_set(store, *, decision_time: datetime) -> None:
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

    try:
        _archived = store.archive_expired_candidates(
            decision_time=decision_time.isoformat(),
            batch_limit=batch_limit,
        )
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
        _ch_archived = store.archive_superseded_channel_events(batch_limit=batch_limit)
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
        _d0_archived = store.archive_superseded_day0_events(batch_limit=batch_limit)
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
        _ch_ignored = store.ignore_channel_cache_events(batch_limit=batch_limit)
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
        _fsr_archived = store.archive_superseded_forecast_snapshot_events(
            batch_limit=batch_limit,
        )
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
        from src.data.day0_oracle_anomaly import wu_metar_anomaly_check

        prefetch = get_fast_obs_emitter().prefetch(
            cities=runtime_cities(),
            decision_time=decision_time,
            anomaly_check=wu_metar_anomaly_check,
        )
    except Exception as _fast_exc:  # noqa: BLE001 — fast lane is additive
        logger.warning(
            "EDLI day0 fast obs prefetch failed (non-fatal, catch-up lanes continue): %r",
            _fast_exc,
        )
    try:
        # High-res hourly vector refresh (30-min throttle per city inside the
        # module; ~17 in-domain cities). open-meteo HTTP + forecasts-DB write —
        # both forbidden under the world-write mutex, so it lives here.
        from src.config import runtime_cities as _rc
        from src.data.day0_hourly_vectors import maybe_refresh_day0_hourly_vectors

        maybe_refresh_day0_hourly_vectors(_rc(), decision_time=decision_time)
    except Exception as _vec_exc:  # noqa: BLE001 — additive lane, fail-soft
        logger.warning("EDLI day0 hourly-vector refresh failed (non-fatal): %r", _vec_exc)
    return prefetch


def _edli_emit_day0_extreme_events(
    world_conn,
    trade_conn,
    *,
    decision_time: datetime,
    received_at: str,
    limit: int,
    fast_prefetch=None,
    day0_is_tradeable: bool = True,
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

    fast_emitted = 0
    if fast_prefetch is not None:
        try:
            from src.data.day0_fast_obs import get_fast_obs_emitter

            fast_emitted = get_fast_obs_emitter().emit_prefetched(
                world_conn=world_conn,
                prefetch=fast_prefetch,
                received_at=received_at,
                limit=limit,
                day0_is_tradeable=day0_is_tradeable,
            )
        except Exception as _fast_exc:  # noqa: BLE001 — fast lane is additive; never block catch-up
            logger.warning(
                "EDLI day0 fast obs emit failed (non-fatal, catch-up lanes continue): %r",
                _fast_exc,
            )

    trigger = Day0ExtremeUpdatedTrigger(
        EventWriter(world_conn), day0_is_tradeable=day0_is_tradeable
    )
    authority_results = trigger.scan_authority_rows(
        observation_conn=trade_conn,
        settlement_semantics=_edli_day0_settlement_semantics,
        decision_time=decision_time,
        received_at=received_at,
        limit=limit,
    )
    observation_results = trigger.scan_observation_instants_rows(
        observation_conn=trade_conn,
        settlement_semantics=_edli_day0_settlement_semantics,
        decision_time=decision_time,
        received_at=received_at,
        limit=limit,
    )
    # Structured per-lane counters (PR#404 P2 observability fix).
    logger.info(
        "EDLI day0 emit: day0_fast_emitted=%d day0_authority_emitted=%d "
        "day0_observation_instants_emitted=%d",
        fast_emitted, len(authority_results), len(observation_results),
    )
    return fast_emitted + len(authority_results) + len(observation_results)


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
        return 3.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS=%r; using 3.0", raw)
        return 3.0
    if value <= 0:
        logger.warning("Invalid ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS=%r; using 3.0", raw)
        return 3.0
    return value


def _edli_pre_submit_jit_book_quote_provider():
    """Build the just-in-time single-token ``/book`` fetcher for the pre-submit
    authority (GATE #84). Returns a ``token_id -> dict`` callable that pulls the
    live CLOB book for exactly the selected candidate at submit time, or ``None``
    if a CLOB client cannot be constructed (caller then falls back to the DB row).

    The client is constructed per call so the provider holds no long-lived
    connection across reactor cycles; the call only fires for an actual submit
    candidate (rare, fully gated), so the per-call cost is negligible.
    """

    def _fetch(token_id: str) -> dict:
        from src.data.polymarket_client import PolymarketClient

        with PolymarketClient(public_http_timeout=_edli_pre_submit_clob_timeout_seconds()) as clob:
            return clob.get_orderbook_snapshot(token_id)

    return _fetch


def _edli_decision_family_snapshot_refresher(topology_conn):
    """Build the decision-triggered targeted family snapshot refresher (zero-order
    wall fix 2026-06-11).

    The substrate warm job refreshes ``executable_market_snapshots`` on a fair
    rotating cursor whose per-family cadence (~5.4 min live) is far slower than the
    30s price-freshness window the decision path enforces on the SELECTED bin. On
    that cadence any family is decidable only ~9% of wall-clock time and every
    transient requeue lands price-stale → the built maker final intents dead-letter
    MONEY_PATH_TRANSIENT_EXHAUSTED. This callable lets the adapter, AT decision time
    when the elected row is stale, capture FRESH books for THAT family NOW through
    the SANCTIONED warm-job capture path (topology reconstruct + CLOB /book +
    ``snapshot_repo.insert_snapshot``), scoped to ONE family.

    Built in main.py (the CLOB client lives here) and injected into the adapter as a
    plain callable, so the adapter never imports venue code (architecture ban,
    tests/engine/test_event_reactor_no_bypass.py).

    LOCK LAW (#95 / INV-37 / three-phase venue-sync): the refresher opens its OWN
    short-lived write trade connection; the adapter has ALREADY dropped its trade-DB
    read snapshot (``trade_conn.commit()``) before calling this, so the [NET] /book
    fetch is never wrapped by an open trade-DB txn — same posture as the submit-time
    JIT witness /book fetch. ``topology_conn`` (forecasts DB, read-only here) owns
    ``market_events``; the snapshot WRITE targets zeus_trades only.

    Returns True if it captured/persisted fresh rows, False on a fail-soft skip; the
    caller re-elects the latest row and the freshness gate still fail-closes if the
    re-elected row remains stale.
    """

    def _refresh(*, city, target_date, metric, condition_ids=(), selected_token_id=None):
        from src.data.market_scanner import (
            reconstruct_weather_market_from_static_topology,
            refresh_executable_market_substrate_snapshots,
        )
        from src.data.polymarket_client import PolymarketClient
        from src.engine.event_reactor_adapter import _event_family_market_topology_rows
        from src.state.db import get_trade_connection

        payload = {"city": city, "target_date": target_date, "metric": metric}
        try:
            topology_rows = _event_family_market_topology_rows(topology_conn, payload)
        except Exception as exc:  # noqa: BLE001 — fail-soft: stale rejection stands
            logger.warning(
                "decision family refresh: topology lookup failed for %s/%s/%s: %s",
                city, target_date, metric, exc,
            )
            return False
        if not topology_rows:
            return False
        # FAMILY-IDENTITY RE-INJECTION (freshness-throughput starvation fix
        # 2026-06-14, #92 / binding_wall.md). _event_family_market_topology_rows
        # binds city/target_date/temperature_metric in its WHERE clause but does NOT
        # SELECT them, so the returned rows carry NO city/target_date/metric columns.
        # reconstruct_weather_market_from_static_topology reads first.get("city") /
        # ("target_date") / ("temperature_metric") and returns None at market_scanner
        # L3535 (`if not (slug and city_name and target_date and metric)`) whenever
        # they are absent — which is ALWAYS for this path. That silent None made the
        # decision-triggered refresher return False for EVERY family (marker
        # "decision_triggered_targeted_refresh" at ZERO live 2026-06-14), so a STALE
        # live family could NEVER get a fresh row and requeued forever. The warm-job
        # lane (refresh_pending_family_snapshots, main.py ~3580) already re-injects
        # these three fields before calling reconstruct; mirror it here so the
        # decision-time refresh actually reconstructs and captures. Additive: a row
        # already carrying the fields is unchanged.
        topology_rows = [
            {
                **dict(trow),
                "city": city,
                "target_date": target_date,
                "temperature_metric": metric,
            }
            for trow in topology_rows
        ]

        _clob_timeout = max(
            1.0,
            float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "5.0")),
        )
        write_conn = get_trade_connection(write_class="live")
        try:
            market = reconstruct_weather_market_from_static_topology(
                write_conn,
                topology_rows=topology_rows,
                now_utc=datetime.now(timezone.utc),
            )
            if market is None:
                # Static topology cannot reconstruct the full token map (a sibling
                # lost executable identity). The warm-job Gamma slug path owns that
                # recovery; the decision-time fast path does NOT do a Gamma fetch.
                return False
            with PolymarketClient(public_http_timeout=_clob_timeout) as clob:
                summary = refresh_executable_market_substrate_snapshots(
                    write_conn,
                    markets=[market],
                    clob=clob,
                    captured_at=datetime.now(timezone.utc),
                    scan_authority="VERIFIED",
                    refresh_reason="decision_triggered_targeted_refresh",
                    # UNLIMITED: capture EVERY bin of THIS family (siblings feed the
                    # FDR full-family proof + capital-efficiency economics; refreshing
                    # only the selected bin would leave stale sibling prices in q/FDR).
                    max_outcomes=0,
                )
            write_conn.commit()
            return int(summary.get("inserted", 0) or 0) > 0
        except Exception as exc:  # noqa: BLE001 — fail-soft: never block the decision
            logger.warning(
                "decision family refresh: capture failed for %s/%s/%s: %s",
                city, target_date, metric, exc,
            )
            return False
        finally:
            write_conn.close()

    return _refresh


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

    Reads forecast_db / seed_dir / raw_manifest_dir from the shadow-materialization queue config
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


def _edli_pre_submit_book_from_jit_fetch(book_quote_provider, *, token_id: str):
    """JIT single-token book fetch for the SELECTED candidate at submit time.

    GATE #84 root cause: the shared market-channel feasibility feed stamps
    ``quote_seen_at`` with the venue book-CHANGE timestamp (1s resolution, often
    minutes stale for slow weather books), and only refreshes a given token when
    its WS tick arrives (median per-candidate gap ~11s). The 1000ms pre-submit
    bound is a SUBMIT-TIME observation-freshness bound, so for the one selected
    candidate we pull its live book ``now`` and anchor freshness to OUR
    observation time — the FOK crosses against exactly this book.

    Returns ``(best_bid, best_ask, book_hash)`` on a usable two-sided book, or
    ``None`` when the fetch fails or the book is empty/crossed (fail-closed —
    the caller then falls back to a genuinely-fresh DB row or raises).
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
    if best_bid is None or best_ask is None or not book_hash:
        return None
    if best_bid >= best_ask:
        # Crossed/locked book is not a usable pre-submit authority.
        return None
    return best_bid, best_ask, book_hash


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


def _edli_pre_submit_authority_provider_from_world_conn(
    world_conn, edli_cfg, *, book_quote_provider=None
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
    collateral_payload_cache: dict[str, object] | None = None

    def _cached_venue_summary(checked_at: datetime) -> dict[str, object]:
        nonlocal venue_summary_cache
        if venue_summary_cache is None:
            venue_summary_cache = _edli_venue_connectivity_authority_summary(checked_at)
        return venue_summary_cache

    def _cached_collateral_payload() -> dict[str, object]:
        nonlocal collateral_payload_cache
        if collateral_payload_cache is None:
            from src.data.polymarket_client import PolymarketClient

            with PolymarketClient(public_http_timeout=_edli_pre_submit_clob_timeout_seconds()) as clob:
                collateral_payload_cache = dict(clob._ensure_v2_adapter().get_collateral_payload())
        return collateral_payload_cache

    def _provider(final_intent, _executable_snapshot, decision_time):
        checked_at = decision_time.astimezone(timezone.utc)
        intent = final_intent.payload
        token_id = str(intent["token_id"])

        # PRIMARY: just-in-time live book for the selected candidate. Freshness is
        # anchored to OUR observation time (checked_at) — the FOK crosses against
        # exactly this book — so quote_age_ms is the observation-to-submit latency.
        jit = _edli_pre_submit_book_from_jit_fetch(book_quote_provider, token_id=token_id)
        if jit is not None:
            best_bid, best_ask, book_hash = jit
            quote_seen_at = checked_at.isoformat()
            book_authority_id = "clob_jit_book"
        else:
            # FAIL-CLOSED FALLBACK: the shared feasibility feed. Accept the latest
            # row ONLY if it is itself within the freshness bound; a venue-stale row
            # (the GATE #84 pathology) must NOT be emitted as a fresh quote.
            row = _edli_latest_pre_submit_book_row(
                world_conn,
                token_id=token_id,
                decision_time=checked_at,
            )
            if row is None:
                raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_MISSING")
            quote_seen_at = str(_row_get(row, "quote_seen_at") or "")
            book_hash = str(_row_get(row, "book_hash_before") or "")
            best_bid = _row_float(row, "best_bid_before")
            best_ask = _row_float(row, "best_ask_before")
            if not quote_seen_at or not book_hash or best_bid is None or best_ask is None:
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
            collateral_payload=_cached_collateral_payload(),
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


def _edli_latest_pre_submit_book_row(world_conn, *, token_id: str, decision_time: datetime):
    return world_conn.execute(
        """
        SELECT quote_seen_at, book_hash_before, best_bid_before, best_ask_before
        FROM execution_feasibility_evidence
        WHERE token_id = ?
          AND quote_seen_at <= ?
          AND best_bid_before IS NOT NULL
          AND best_ask_before IS NOT NULL
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

    with PolymarketClient(public_http_timeout=_edli_pre_submit_clob_timeout_seconds()) as clob:
        clob.v2_preflight()
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
        with PolymarketClient(public_http_timeout=_edli_pre_submit_clob_timeout_seconds()) as clob:
            collateral = clob._ensure_v2_adapter().get_collateral_payload()
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


def _edli_durable_fill_bridge_scan(conn, *, now=None, limit: int = 500) -> int:
    """MF-1: durable, idempotent, self-healing EDLI fill -> position_current scan.

    THE authoritative bridge trigger (replaces the transient
    ``_edli_fill_bridge_aggregate_ids`` set as the source of truth). Finds every
    aggregate in ``edli_live_order_events`` carrying a ``UserTradeObserved`` with
    ``fill_authority_state == 'FILL_CONFIRMED'`` whose deterministic
    ``edli_bridge_position_id`` has NO ``position_current`` row, and materialises
    each via the idempotent canonical bridge.

    Why this closes the orphan window (the verified DEFECT): the old path only
    bridged aggregates that went PENDING->PROCESSED *this cycle*, holding them in
    an in-memory set. A daemon death OR a swallowed bridge exception between the
    inbox PROCESSED commit and the separate bridge commit left a FILL_CONFIRMED
    aggregate with no position_current row; on restart the set was empty and
    nothing re-bridged it -> capital orphaned. This scan re-derives the work set
    durably from ``edli_live_order_events`` (the persisted truth), so it heals any
    such orphan on the very next cycle AND at boot, regardless of process restarts.

    Idempotency: ``materialize_position_current_from_edli_fill`` upserts
    ``position_current`` (ON CONFLICT(position_id) DO UPDATE) and appends
    ``position_events`` keyed UNIQUE(position_id, sequence_no) — re-bridging an
    already-bridged fill is a no-op for events and a safe UPDATE for the
    projection. The absence filter below ALSO skips already-bridged aggregates so
    a healthy daemon does no redundant work.

    INV-37 / transaction ownership: reads ``edli_live_order_events`` and writes
    ``position_current`` / ``position_events`` ON THE SAME connection ``conn``
    (in production a trade connection with ``world`` ATTACHed). Performs NO
    independent connection and does NOT commit — the caller owns the transaction
    boundary (the cycle / boot wrapper commits once after the scan).

    Returns the number of orphaned fills bridged this pass.
    """
    from src.events.edli_position_bridge import (
        DISPOSITION_QUARANTINED,
        DISPOSITION_SETTLED_MARKET,
        _QUARANTINE_THRESHOLD,
        _aggregate_event_rows,
        _edli_events_table,
        _has_confirmed_fill,
        _increment_failure_count,
        _latest_payload,
        _market_is_settled,
        _quarantine_aggregate,
        _record_settled_disposition,
        edli_bridge_position_id,
        edli_bridge_position_id_legacy,
        get_fill_bridge_disposition,
        materialize_position_current_from_edli_fill,
    )

    now = now or datetime.now(timezone.utc)
    now_str = now.isoformat()
    today_utc = now_str[:10]

    table = _edli_events_table(conn)
    try:
        if table == "world.edli_live_order_events":
            sql = """
            SELECT DISTINCT aggregate_id
            FROM world.edli_live_order_events
            WHERE event_type = 'UserTradeObserved'
              AND json_extract(payload_json, '$.fill_authority_state') = 'FILL_CONFIRMED'
            ORDER BY aggregate_id ASC
            """
        elif table == "edli_live_order_events":
            sql = """
            SELECT DISTINCT aggregate_id
            FROM edli_live_order_events
            WHERE event_type = 'UserTradeObserved'
              AND json_extract(payload_json, '$.fill_authority_state') = 'FILL_CONFIRMED'
            ORDER BY aggregate_id ASC
            """
        else:
            raise ValueError(f"unexpected EDLI events table: {table!r}")

        candidate_rows = conn.execute(sql).fetchall()
    except Exception as exc:  # noqa: BLE001
        # Missing table / attach (e.g. a degraded boot) must not crash the
        # caller — the EDLI events persist and the next cycle retries.
        logger.error(
            "EDLI durable fill-bridge scan: candidate query failed "
            "(non-fatal; retries next cycle): %s",
            exc,
            exc_info=True,
        )
        return 0

    bridged = 0
    new_fills_seen = 0  # counts only aggregates that need bridging (limit gate)
    for row in candidate_rows:
        aggregate_id = str(_row_get(row, "aggregate_id"))
        position_id = edli_bridge_position_id(aggregate_id)
        # Dual-probe: check BOTH the wide (new, 68-char) ID and the legacy
        # narrow (old, 11-char) ID.  The 101 rows written before FIX #96
        # carry the old short ID; probing only the wide ID would miss them
        # and re-bridge the same aggregate into a second position_current row
        # (duplicate position identity = live-money hazard).
        legacy_position_id = edli_bridge_position_id_legacy(aggregate_id)
        existing = conn.execute(
            "SELECT 1 FROM position_current WHERE position_id IN (?, ?) LIMIT 1",
            (position_id, legacy_position_id),
        ).fetchone()
        if existing is not None:
            # Already bridged (wide or legacy id) — idempotent skip.
            continue

        # Disposition check: skip terminally routed aggregates (settled or quarantined).
        # These do NOT count against the new-fill budget.
        prior_disposition = get_fill_bridge_disposition(conn, aggregate_id)
        if prior_disposition in (DISPOSITION_SETTLED_MARKET, DISPOSITION_QUARANTINED):
            continue

        # --- Settled-market routing (category-kill 1) ---
        # Before attempting to bridge, check whether the market has settled.
        # Read the PreSubmitRevalidated payload to get identity fields; if the
        # aggregate lacks one or the EDLI events are absent, fall through to normal
        # bridge logic (which will raise/fail on its own terms).
        try:
            events = _aggregate_event_rows(conn, aggregate_id)
            if events and _has_confirmed_fill(events):
                pre_submit = _latest_payload(events, "PreSubmitRevalidated") or {}
                city = str(pre_submit.get("city") or "").strip()
                target_date = str(pre_submit.get("target_date") or "").strip()
                metric = str(pre_submit.get("metric") or pre_submit.get("temperature_metric") or "").strip().lower()
                if target_date:  # only run settled check when we have a target_date
                    is_settled, evidence = _market_is_settled(
                        conn,
                        city=city,
                        target_date=target_date,
                        temperature_metric=metric,
                        today_utc=today_utc,
                    )
                    if is_settled:
                        logger.warning(
                            "EDLI fill-bridge: SETTLED_MARKET_FILL_BOOKED — "
                            "aggregate=%s market already settled (%s); "
                            "booked for accounting, no position_current row created",
                            aggregate_id,
                            evidence,
                        )
                        _record_settled_disposition(conn, aggregate_id, evidence, now_str)
                        continue  # does NOT count against new_fills_seen budget
        except Exception as _settle_exc:  # noqa: BLE001
            # Settlement check is best-effort. If it fails, fall through to normal bridge
            # logic (which will handle the failure via quarantine path below).
            logger.debug(
                "EDLI fill-bridge: settled-market check failed for %s (non-fatal): %s",
                aggregate_id,
                _settle_exc,
            )

        # --- New-fill budget gate (applied AFTER skipping disposed/settled aggregates) ---
        # This ensures persistent failures do not starve new real fills in the budget.
        if new_fills_seen >= max(0, limit):
            break
        new_fills_seen += 1

        try:
            result = materialize_position_current_from_edli_fill(
                conn, aggregate_id, now=now
            )
            if result is not None:
                bridged += 1
                logger.warning(
                    "EDLI durable fill-bridge: HEALED orphaned confirmed fill "
                    "aggregate=%s -> position_id=%s shares=%s cost_basis_usd=%s",
                    aggregate_id,
                    result.get("position_id"),
                    result.get("shares"),
                    result.get("cost_basis_usd"),
                )
        except Exception as exc:  # noqa: BLE001
            # --- Bounded-retry quarantine (category-kill 2) ---
            # Track consecutive failures; quarantine after _QUARANTINE_THRESHOLD attempts.
            # Transient faults will clear on the next cycle (attempt_count resets on success
            # would require extra state; here we accept that count is monotone — the category
            # of interest is aggregates that NEVER succeed, not those that occasionally fail).
            error_str = str(exc)
            try:
                attempt_count = _increment_failure_count(conn, aggregate_id, error_str, now_str)
            except Exception:  # noqa: BLE001
                attempt_count = 1

            if attempt_count >= _QUARANTINE_THRESHOLD:
                logger.error(
                    "EDLI fill-bridge: QUARANTINED aggregate=%s after %d consecutive failures "
                    "(excluded from future scans); last_error=%s",
                    aggregate_id,
                    attempt_count,
                    error_str[:500],
                )
                try:
                    _quarantine_aggregate(conn, aggregate_id, error_str, attempt_count, now_str)
                except Exception:  # noqa: BLE001
                    pass
            else:
                # Still within retry window — log at error level but don't quarantine yet.
                logger.error(
                    "EDLI durable fill-bridge: failed to bridge aggregate %s "
                    "(attempt %d/%d; EDLI events persist, next scan retries): %s",
                    aggregate_id,
                    attempt_count,
                    _QUARANTINE_THRESHOLD,
                    exc,
                    exc_info=True,
                )
    return bridged


def _edli_user_channel_reconcile_runtime_enabled(edli_cfg: dict) -> bool:
    if not edli_cfg.get("enabled"):
        return False
    if bool(edli_cfg.get("edli_user_channel_reconcile_enabled", False)):
        return True
    return (
        _live_execution_mode(edli_cfg) == "edli_shadow_no_submit"
        and _truthy_env("ZEUS_USER_CHANNEL_WS_ENABLED")
    )


@_scheduler_job("edli_user_channel_reconcile")
def _edli_user_channel_reconcile_cycle() -> None:
    """EDLI user-channel/reconcile service boundary.

    Disabled by default. The live-order aggregate may only accept fill/lifecycle
    facts from authenticated user channel or explicit reconcile writers; public
    market-channel data remains quote evidence only.
    """

    edli_cfg = _settings_section("edli", {})
    if not _edli_user_channel_reconcile_runtime_enabled(edli_cfg):
        return
    max_messages = int(edli_cfg.get("edli_user_channel_reconcile_max_messages", 50))
    pending_limit = int(edli_cfg.get("edli_user_channel_reconcile_pending_limit", 50))
    now = datetime.now(timezone.utc)
    message_count = 0
    reconcile_count = 0
    # DEFECT-1: aggregates whose user-channel TRADE message was processed this
    # cycle. After the world-conn commit, the bridge materialises a canonical
    # position_current row for each that reached FILL_CONFIRMED.
    _edli_fill_bridge_aggregate_ids: set[str] = set()
    from src.events.live_order_aggregate import LiveOrderAggregateLedger
    from src.events.live_order_reconcile import append_reconciled
    from src.events.triggers.user_channel_ingestor import (
        INBOX_DUPLICATE,
        INBOX_FAILED,
        INBOX_PROCESSED,
        INBOX_STALE_REJECTED,
        append_user_channel_message,
        enqueue_user_channel_inbox_message,
        inbox_row_to_user_channel_message,
        mark_user_channel_inbox_status,
        pending_user_channel_inbox_messages,
    )

    conn = get_world_connection(write_class="live")
    try:
        ledger = LiveOrderAggregateLedger(conn)
        user_channel_reader = _edli_user_channel_reader(edli_cfg)
        for message in user_channel_reader.poll(max_messages=max_messages):
            aggregate_id = _resolve_edli_user_channel_aggregate_id(conn, message)
            message_hash = str(message.get("message_hash") or "").strip()
            if not message_hash:
                raise RuntimeError("EDLI_USER_CHANNEL_MESSAGE_HASH_REQUIRED")
            occurred_at = _parse_edli_runtime_time(message, default=now)
            enqueue_user_channel_inbox_message(
                conn,
                message=message,
                aggregate_id=aggregate_id,
                occurred_at=occurred_at,
                received_at=now,
            )

        for inbox_row in pending_user_channel_inbox_messages(conn, limit=max_messages):
            message_hash = str(_row_get(inbox_row, "message_hash"))
            aggregate_id = str(_row_get(inbox_row, "aggregate_id"))
            try:
                message = inbox_row_to_user_channel_message(inbox_row)
                occurred_at = _parse_edli_runtime_time(
                    {"occurred_at": _row_get(inbox_row, "occurred_at")},
                    default=now,
                )
                _edli_user_channel_message_not_stale(conn, aggregate_id=aggregate_id, occurred_at=occurred_at)
                if _edli_user_channel_message_seen(conn, aggregate_id=aggregate_id, message_hash=message_hash):
                    mark_user_channel_inbox_status(
                        conn,
                        message_hash=message_hash,
                        status=INBOX_DUPLICATE,
                        processed_at=now,
                    )
                    continue
                append_user_channel_message(
                    ledger,
                    aggregate_id=aggregate_id,
                    message=message,
                    occurred_at=occurred_at,
                )
                mark_user_channel_inbox_status(
                    conn,
                    message_hash=message_hash,
                    status=INBOX_PROCESSED,
                    processed_at=now,
                )
                message_count += 1
                # DEFECT-1 bridge (capital recoverability): a confirmed EDLI
                # fill must materialise a canonical position_current row so
                # chain-reconciliation / exit-lifecycle / harvester / redeem can
                # see it. The actual cross-DB write happens AFTER this world-conn
                # commit, on a trade-connection-with-world-attached (INV-37) —
                # here we only record which aggregates received a trade message.
                _message_kind = str(message.get("message_type") or message.get("type") or "").lower()
                if _message_kind == "trade":
                    _edli_fill_bridge_aggregate_ids.add(aggregate_id)
            except RuntimeError as exc:
                status = INBOX_STALE_REJECTED if "STALE" in str(exc) else INBOX_FAILED
                mark_user_channel_inbox_status(
                    conn,
                    message_hash=message_hash,
                    status=status,
                    processed_at=now,
                    error=str(exc),
                )
            except Exception as exc:
                mark_user_channel_inbox_status(
                    conn,
                    message_hash=message_hash,
                    status=INBOX_FAILED,
                    processed_at=now,
                    error=str(exc),
                )

        venue_reconcile_reader = _edli_venue_reconcile_reader(edli_cfg)
        for pending in _edli_pending_reconcile_aggregates(conn, limit=pending_limit):
            fact = venue_reconcile_reader.reconcile(pending)
            if not fact:
                continue
            append_reconciled(
                ledger,
                aggregate_id=str(_row_get(pending, "aggregate_id")),
                event_id=str(fact.get("event_id") or _row_get(pending, "event_id")),
                final_intent_id=str(fact.get("final_intent_id") or _row_get(pending, "final_intent_id")),
                source=str(fact.get("source") or "venue_reconcile"),
                pending_reconcile=_parse_edli_runtime_bool(fact.get("pending_reconcile"), default=False),
                occurred_at=_parse_edli_runtime_time(fact, default=now),
                payload=fact.get("payload") if isinstance(fact.get("payload"), dict) else None,
            )
            reconcile_count += 1
        from src.events.edli_trade_fact_bridge import (
            append_confirmed_trade_facts_to_edli,
            append_rest_filled_orphan_trade_facts_to_edli,
        )

        reconcile_count += append_confirmed_trade_facts_to_edli(conn, now=now)
        # Fill-orphan recovery (HK 30C 2026-06-12 incident): a venue fill whose
        # WS_USER CONFIRMED message was lost to a user-channel dropout exists
        # only as a REST trade fact and can never reach FILL_CONFIRMED through
        # the bridge above — the position is never materialised and the P&L is
        # never booked. The recovery lane asserts fill truth under the explicit
        # RECONCILE_SOURCE authority (cmd terminal FILLED/PARTIAL + REST fact +
        # grace window for the user channel), with full provenance in payload.
        if bool(_settings_section("edli", {}).get("edli_rest_filled_bridge_enabled", True)):
            try:
                reconcile_count += append_rest_filled_orphan_trade_facts_to_edli(conn, now=now)
            except Exception as exc:  # noqa: BLE001 — recovery lane must not break WS truth path
                logger.error(
                    "EDLI rest-filled orphan bridge failed (non-fatal): %s", exc, exc_info=True
                )
        conn.commit()
    finally:
        conn.close()

    # MF-1 / DEFECT-1 bridge pass (capital recoverability). The EDLI events are
    # now durable on world.db. Materialise a canonical position_current row for
    # any aggregate that reached FILL_CONFIRMED so the legacy lifecycle
    # (chain-reconciliation / exit / harvester / redeem) can see and recover the
    # position.
    #
    # AUTHORITATIVE TRIGGER = the durable, idempotent scan
    # (_edli_durable_fill_bridge_scan): it re-derives the work set from the
    # persisted edli_live_order_events on EVERY cycle, so a confirmed fill orphaned
    # by a daemon death / swallowed exception between the inbox PROCESSED commit
    # and this bridge commit is healed on the next cycle regardless of process
    # restarts. The transient `_edli_fill_bridge_aggregate_ids` set is kept ONLY
    # as a fast in-cycle optimisation (bridges the just-processed fills with zero
    # extra scan cost); it is NO LONGER the source of truth, so the orphan window
    # is closed. Both run on the SAME bridge connection within the SAME commit.
    #
    # INV-37: runs on a trade connection with world ATTACHed — the bridge reads
    # world.edli_live_order_events and writes position_current / position_events on
    # the SAME connection (ATTACH + SAVEPOINT, no independent connection).
    # Idempotent: replay UPDATEs the same row, never duplicates; the durable scan
    # skips aggregates that already have a position_current row.
    # Fail-safe: a bridge error must not crash the scheduler job — log and retry
    # next cycle (the EDLI events persist; the next durable scan re-runs).
    bridged_positions = 0
    if True:  # always run the durable scan; the fast set is an optimisation only
        from src.events.edli_position_bridge import (
            materialize_position_current_from_edli_fill,
        )
        from src.state.db import get_trade_connection_with_world_required

        bridge_conn = None
        try:
            bridge_conn = get_trade_connection_with_world_required(write_class="live")
            # Fast in-cycle path: bridge the fills processed THIS cycle first
            # (zero extra scan). These will already exist by the time the durable
            # scan runs, so the scan's absence filter skips them — no double work.
            for _agg_id in sorted(_edli_fill_bridge_aggregate_ids):
                try:
                    result = materialize_position_current_from_edli_fill(
                        bridge_conn, _agg_id, now=now
                    )
                    if result is not None:
                        bridged_positions += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "EDLI position bridge failed for aggregate %s (non-fatal; "
                        "EDLI events persist, durable scan retries): %s",
                        _agg_id,
                        exc,
                        exc_info=True,
                    )
            # Authoritative durable scan: heal ANY orphaned confirmed fill,
            # including ones stranded by a prior restart / swallowed exception.
            bridged_positions += _edli_durable_fill_bridge_scan(bridge_conn, now=now)
            bridge_conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "EDLI position bridge pass failed (non-fatal): %s", exc, exc_info=True
            )
        finally:
            if bridge_conn is not None:
                try:
                    bridge_conn.close()
                except Exception:  # noqa: BLE001
                    pass

    _write_scheduler_health(
        "edli_user_channel_reconcile",
        failed=False,
        extra={
            "status": "processed_user_channel_reconcile_cycle",
            "fill_authority": "user_channel_or_reconcile_only",
            "public_market_channel_fill_truth": "forbidden",
            "user_channel_messages": message_count,
            "venue_reconciliations": reconcile_count,
            "edli_positions_bridged": bridged_positions,
        },
    )


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
    """守護 (2026-06-03): drain already-stuck settled-but-active positions AT BOOT.

    The harvester now runs hourly in EDLI modes, but on restart we should not wait
    up to an hour to clear positions whose target_date already has a VERIFIED
    settlement_outcomes row yet still sit phase=active (memory #56, Shanghai
    cca68b44). One synchronous _harvester_cycle() pass at boot consumes that truth
    immediately: marks the positions settled and enqueues their REDEEM_INTENT_CREATED
    so the redeem pollers can pick them up on their first tick.

    Shadow-safe: _harvester_cycle does no on-chain work (the on-chain redeem POST is
    the separately-gated _redeem_submitter_cycle), and resolve_pnl_for_settled_markets
    is itself a no-op unless ZEUS_HARVESTER_LIVE_ENABLED=1, so this boot pass cannot
    settle anything when the operator has the resolver disabled.

    Gate: same modes as the scheduled job (_harvester_should_register). Fully
    fail-open — any error is logged, never fatal; the hourly scheduled job retries.
    """
    try:
        edli_cfg = _settings_section("edli", {})
        live_execution_mode = _live_execution_mode(edli_cfg)
        if not _harvester_should_register(live_execution_mode):
            return
        if live_execution_mode in EDLI_EVENT_DRIVEN_MODES and not edli_cfg.get("enabled"):
            return
        _harvester_cycle()
        logger.info(
            "守護 boot settlement-redeem recovery: ran one harvester pass before "
            "entering the trading loop (mode=%s)",
            live_execution_mode,
        )
    except Exception as exc:  # noqa: BLE001
        # Boot recovery is best-effort: the hourly scheduled harvester is the safety
        # net, so a boot-time hiccup must never block the daemon from starting.
        logger.error(
            "守護 boot settlement-redeem recovery failed (non-fatal; hourly harvester "
            "retries): %s",
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


@_scheduler_job("edli_market_channel_ingestor")
def _edli_market_channel_ingestor_cycle() -> None:
    """EDLI market-channel online data-service bootstrap.

    This daemon-side job discovers active weather tokens and prepares the public
    market-channel ingestor/quote cache. Actual fills remain user-channel or
    reconcile authority only.
    """

    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled") or not edli_cfg.get("market_channel_ingestor_enabled"):
        return
    global _edli_market_channel_thread
    if _edli_market_channel_thread is not None and _edli_market_channel_thread.is_alive():
        _write_scheduler_health(
            "edli_market_channel_ingestor",
            failed=False,
            extra={
                "thread": "alive",
                "quote_cache_enabled": bool(edli_cfg.get("market_channel_quote_cache_enabled", False)),
                "fill_authority": "user_channel_or_reconcile_only",
            },
        )
        return

    from src.events.triggers.market_channel_ingestor import active_weather_token_metadata_from_snapshots
    from src.state.db import get_trade_connection, get_world_connection

    # Candidate universe (Blocker #52): tokens the reactor recently decided on must
    # be PINNED into the ingestor universe so each has a fresh execution_feasibility_
    # evidence row before the pre-submit witness reads it. The full latest-per-market
    # universe is captured up to the cap; candidates are never dropped by the cap.
    priority_token_ids: set[str] = set()
    world_read = get_world_connection(write_class=None)
    try:
        priority_token_ids = _edli_candidate_priority_token_ids(world_read)
    except Exception as exc:  # noqa: BLE001 - priority pinning is best-effort, universe still captured
        logger.warning("EDLI ingestor candidate-priority read failed (non-fatal): %s", exc)
    finally:
        if world_read is not None:
            world_read.close()

    universe_cap = _edli_bounded_positive_int(
        edli_cfg,
        "market_channel_universe_max_tokens",
        default=2000,
        maximum=8000,
    )

    trade_conn = get_trade_connection(write_class=None)
    try:
        token_metadata = active_weather_token_metadata_from_snapshots(
            trade_conn,
            limit=universe_cap,
            priority_token_ids=priority_token_ids,
        )
        token_ids = set(token_metadata)
    finally:
        trade_conn.close()

    if not token_ids:
        _write_scheduler_health(
            "edli_market_channel_ingestor",
            failed=False,
            extra={
                "active_weather_token_ids": 0,
                "quote_cache_enabled": bool(edli_cfg.get("market_channel_quote_cache_enabled", False)),
                "fill_authority": "user_channel_or_reconcile_only",
                "skipped": "no_active_weather_tokens",
            },
        )
        return

    def _runner() -> None:
        from src.data.polymarket_client import PolymarketClient
        from src.events.event_coalescer import EventCoalescer
        from src.events.event_writer import EventWriter
        from src.events.triggers.market_channel_ingestor import (
            MarketChannelAction,
            MarketChannelIngestor,
            MarketChannelOnlineService,
            invalidate_executable_snapshots_for_market_channel_action,
            run_market_channel_service_forever,
        )
        from src.state.db import get_world_connection

        world_conn = get_world_connection(write_class="live")
        try:
            def _invalidate_snapshot_action(action: MarketChannelAction) -> None:
                from src.state.db import get_trade_connection

                trade_conn = get_trade_connection(write_class="live")
                try:
                    invalidated = invalidate_executable_snapshots_for_market_channel_action(
                        trade_conn,
                        action,
                        invalidated_at=datetime.now(timezone.utc),
                    )
                    if invalidated:
                        trade_conn.commit()
                finally:
                    trade_conn.close()

            def _refresh_snapshot_action(action: MarketChannelAction) -> None:
                from src.data.market_scanner import (
                    MarketEventsPersistenceError,
                    find_weather_markets_or_raise,
                    refresh_executable_market_substrate_snapshots,
                )
                from src.state.db import get_trade_connection

                if _defer_for_held_position_monitor("EDLI market-channel substrate refresh"):
                    return
                substrate_acquired = _market_substrate_refresh_lock.acquire(blocking=False)
                if not substrate_acquired:
                    logger.info(
                        "EDLI market-channel refresh skipped: executable substrate refresh already running"
                    )
                    return
                trade_conn = None
                try:
                    trade_conn = get_trade_connection(write_class="live")
                    try:
                        markets = find_weather_markets_or_raise(
                            min_hours_to_resolution=0.0,
                            include_slug_pattern=True,
                        )
                    except MarketEventsPersistenceError as _persistence_exc:
                        logger.error(
                            "EDLI market-channel refresh aborted: market_events persistence "
                            "failure — snapshot substrate not refreshed: %s",
                            _persistence_exc,
                        )
                        return
                    if action.condition_id:
                        markets = _edli_filter_markets_for_condition(markets, action.condition_id)
                        if not markets:
                            logger.warning(
                                "EDLI market-channel refresh skipped: condition_id=%s not found in active weather markets",
                                action.condition_id,
                            )
                            return
                    summary = refresh_executable_market_substrate_snapshots(
                        trade_conn,
                        **_edli_market_channel_refresh_kwargs(
                            action, markets, clob, datetime.now(timezone.utc)
                        ),
                    )
                    trade_conn.commit()
                finally:
                    try:
                        if trade_conn is not None:
                            trade_conn.close()
                    finally:
                        _market_substrate_refresh_lock.release()
                logger.info(
                    "EDLI market-channel refreshed executable snapshots: reason=%s token_id=%s condition_id=%s summary=%s",
                    action.reason,
                    action.token_id,
                    action.condition_id,
                    summary,
                )

            with PolymarketClient() as clob:
                service = MarketChannelOnlineService(
                    MarketChannelIngestor(
                        EventWriter(world_conn),
                        active_token_ids=token_ids,
                        token_metadata=token_metadata,
                        coalescer=EventCoalescer(max_market_keys=1000),
                    ),
                    fetch_orderbook=clob.get_orderbook_snapshot,
                    invalidate_snapshot=_invalidate_snapshot_action,
                    refresh_snapshot=_refresh_snapshot_action,
                    max_refresh_actions_per_window=_edli_bounded_positive_int(
                        edli_cfg,
                        "market_channel_refresh_max_actions_per_window",
                        default=5,
                        maximum=20,
                    ),
                    refresh_window_seconds=float(edli_cfg.get("market_channel_refresh_window_seconds", 60.0) or 60.0),
                )
                run_market_channel_service_forever(
                    service,
                    logger=logger,
                    commit=world_conn.commit,
                )
        finally:
            world_conn.close()

    _edli_market_channel_thread = threading.Thread(
        target=_runner,
        name="edli-market-channel",
        daemon=True,
    )
    _edli_market_channel_thread.start()
    _write_scheduler_health(
        "edli_market_channel_ingestor",
        failed=False,
        extra={
            "active_weather_token_ids": len(token_ids),
            "quote_cache_enabled": bool(edli_cfg.get("market_channel_quote_cache_enabled", False)),
            "fill_authority": "user_channel_or_reconcile_only",
            "thread": "started",
            "rest_seed_status": "polymarket_public_orderbook",
            "websocket_endpoint": "polymarket_public_market_channel",
        },
    )


@_scheduler_job("chain_sync_and_exit_monitor")
def _chain_sync_and_exit_monitor_cycle() -> None:
    """Standalone chain-truth sync + exit-lifecycle monitoring job.

    Wired under BOTH legacy_cron AND EDLI_EVENT_DRIVEN_MODES (Blocker #56).

    In legacy_cron mode the same logic also runs embedded inside run_cycle();
    this standalone job is a belt-and-suspenders addition that ensures chain_shares
    stays populated and exit_pending_missing / settled-but-active positions are
    resolved regardless of which execution mode the daemon is in.

    In EDLI event-driven modes this is the ONLY path that fires chain sync and exit
    monitoring — run_cycle() is never called in those modes.

    Submit safety: exit_order_submit_enabled follows real_order_submit_enabled.
    When armed, the pre-chain held-position pass may submit exits before the
    slower chain-sync scan completes; when unarmed, it still evaluates and
    records state without placing venue orders.

    run_chain_sync uses the Polymarket REST Data API
    (data-api.polymarket.com/positions). If funder_address is absent from Keychain,
    PolymarketClient degrades gracefully — the call returns None/raises, which is
    caught here and logged without crashing the daemon.

    Created: 2026-05-31
    Authority: Blocker #56 fix — /tmp/exit_chain_dx.md root cause analysis.
    """
    from src.data.polymarket_client import PolymarketClient
    from src.engine.cycle_runner import (
        _run_chain_sync,
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
        logger.warning("chain_sync_and_exit_monitor skipped: previous monitor cycle is still running")
        return
    _held_position_monitor_active.set()

    conn = get_connection()
    if conn is None:
        logger.warning("chain_sync_and_exit_monitor: DB write-lock degrade — skipping cycle")
        _mark_held_position_monitor_complete()
        return

    summary: dict = {"monitors": 0, "exits": 0}
    try:
        portfolio = load_portfolio()
        with PolymarketClient() as clob:
            tracker = get_tracker()
            pre_chain_artifact = CycleArtifact(
                mode="held_position_monitor_pre_chain",
                started_at=datetime.now(timezone.utc).isoformat(),
                summary=summary,
            )
            pre_chain_portfolio_dirty = False
            pre_chain_tracker_dirty = False
            try:
                pre_chain_portfolio_dirty, pre_chain_tracker_dirty = _execute_monitoring_phase(
                    conn,
                    clob,
                    portfolio,
                    pre_chain_artifact,
                    tracker,
                    summary,
                    exit_order_submit_enabled=real_order_submit_enabled,
                    run_exit_preflight=True,
                )
            except Exception as exc:
                logger.error(
                    "chain_sync_and_exit_monitor: pre-chain held-position monitor failed (non-fatal): %s",
                    exc,
                    exc_info=True,
                )
                summary["pre_chain_monitoring_error"] = str(exc)

            try:
                _pre_chain_aid_box: list = [None]

                def _pre_chain_db_op():
                    _pre_chain_aid_box[0] = store_artifact(conn, pre_chain_artifact)
                    return _pre_chain_aid_box[0]

                def _pre_chain_export_portfolio():
                    if pre_chain_portfolio_dirty:
                        save_portfolio(
                            portfolio,
                            last_committed_artifact_id=_pre_chain_aid_box[0],
                            source="held_position_monitor_pre_chain",
                        )

                def _pre_chain_export_tracker():
                    if pre_chain_tracker_dirty:
                        save_tracker(tracker)

                commit_then_export(
                    conn,
                    db_op=_pre_chain_db_op,
                    json_exports=[_pre_chain_export_portfolio, _pre_chain_export_tracker],
                )
            except Exception as exc:
                logger.error(
                    "chain_sync_and_exit_monitor: pre-chain held-position monitor commit failed (non-fatal): %s",
                    exc,
                    exc_info=True,
                )
                summary["pre_chain_monitoring_commit_error"] = str(exc)
            finally:
                _mark_held_position_monitor_complete()

            # Phase 1: chain-truth sync — updates chain_shares / chain_avg_price / chain_state.
            # Degrades gracefully if Keychain funder_address is absent (REST call fails → caught).
            try:
                chain_stats, _ = _run_chain_sync(portfolio, clob, conn)
                if chain_stats:
                    summary["chain_sync"] = chain_stats
            except Exception as exc:
                logger.error(
                    "chain_sync_and_exit_monitor: chain sync failed (non-fatal): %s", exc, exc_info=True
                )
                summary["chain_sync_error"] = str(exc)

            # WAL WRITE-LOCK RELEASE (2026-06-08 riskguard-flaps structural fix):
            # Phase 1 (chain sync) opened an implicit DEFERRED txn on the first DML
            # (chain_shares / chain_state updates) which upgrades to the exclusive WAL
            # write lock on zeus_trades.db. With a single trailing commit the lock was
            # held across ALL of Phase 2's per-position HTTP monitor calls — up to 5+
            # minutes — starving riskguard.tick() (30s busy_timeout → DATA_DEGRADED),
            # CollateralLedger heartbeat, and market_scanner snapshot inserts.
            # Fix: commit chain-sync writes HERE, before Phase 2 HTTP calls begin, so
            # the WAL write lock is released between the two phases. The world_write_lock
            # docstring (db.py:295) establishes the same invariant: MUST NOT hold a DB
            # write lock across blocking network/HTTP calls.
            # INV-17 / DT#1 is preserved: chain-sync state is committed atomically
            # before monitoring state, and the final commit_then_export below commits
            # monitoring state before JSON export. The two phases are logically
            # independent — chain_state is ground-truth from the REST API and does not
            # need to be co-transactional with the monitoring state transitions.
            try:
                conn.commit()
            except Exception as exc:
                logger.warning(
                    "chain_sync_and_exit_monitor: chain-sync interim commit failed (non-fatal): %s", exc
                )

            # Phase 2: exit-lifecycle monitoring — resolves exit_pending_missing,
            # checks pending exit fills, runs monitor refresh for active positions.
            # exit_order_submit_enabled=False in submit-disabled modes: state
            # transitions run but no real sell orders are placed.
            artifact = CycleArtifact(
                mode="chain_sync_monitor",
                started_at=datetime.now(timezone.utc).isoformat(),
                summary=summary,
            )
            portfolio_dirty = tracker_dirty = False
            try:
                portfolio_dirty, tracker_dirty = _execute_monitoring_phase(
                    conn, clob, portfolio, artifact, tracker, summary,
                    exit_order_submit_enabled=real_order_submit_enabled,
                )
            except Exception as exc:
                logger.error(
                    "chain_sync_and_exit_monitor: monitoring phase failed (non-fatal): %s",
                    exc,
                    exc_info=True,
                )
                summary["monitoring_error"] = str(exc)

            # Phase 2b: DAY0 resting-order cancel sweep (adversarial review
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
                        "chain_sync_and_exit_monitor: day0 dead-bin cancel sweep failed (non-fatal): %s",
                        exc,
                    )

        # INV-17 / DT#1: commit the DB transaction (monitoring state transitions) FIRST,
        # then export the derived portfolio/tracker JSON with the committed artifact id —
        # so canonical_write.detect_stale_portfolio's marker stays valid and JSON can
        # never lead the DB. (Chain-sync writes were already committed above.)
        _aid_box: list = [None]

        def _db_op():
            _aid_box[0] = store_artifact(conn, artifact)
            return _aid_box[0]

        def _export_portfolio():
            if portfolio_dirty:
                save_portfolio(
                    portfolio,
                    last_committed_artifact_id=_aid_box[0],
                    source="chain_sync_monitor",
                )

        def _export_tracker():
            if tracker_dirty:
                save_tracker(tracker)

        commit_then_export(
            conn, db_op=_db_op, json_exports=[_export_portfolio, _export_tracker]
        )
    except Exception as exc:
        logger.error(
            "chain_sync_and_exit_monitor: unexpected error: %s", exc, exc_info=True
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
    # This chain-sync job runs under ALL EDLI modes, so emit a genuine business-plane
    # status pulse here each cycle. write_cycle_pulse re-reads the live DB read model
    # (open orders, risk, portfolio, capability) -> it reflects REAL current state,
    # never a hardcoded healthy value. Non-fatal: a pulse failure must not abort the
    # chain-sync job. Authority: fix/edli-stage-readiness-2026-05-31 (status_summary).
    try:
        from src.observability.status_summary import write_cycle_pulse
        write_cycle_pulse(summary)
    except Exception as exc:
        logger.error(
            "chain_sync_and_exit_monitor: status pulse failed (non-fatal): %s",
            exc,
            exc_info=True,
        )

    _write_scheduler_health(
        "chain_sync_and_exit_monitor",
        failed=False,
        extra={
            "exit_order_submit_enabled": real_order_submit_enabled,
            "monitors": summary.get("monitors", 0),
            "exits": summary.get("exits", 0),
            "chain_sync_summary": summary.get("chain_sync", {}),
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

    # Efficiency #3 — boot wallet warm-overlap. The single on-chain wallet RPC
    # (#1 collapsed two into one) is network-bound (5-30s, ~38/hr blips); the
    # schema-ready gate / registry assert / f109 consolidator / freshness /
    # boot-guards below are DB-bound. Warm the wallet on a daemon thread NOW so
    # those DB steps run CONCURRENTLY with the RPC; we JOIN immediately before
    # the wallet gate so the gate stays deterministic (warm cache, no race).
    # MUST stay AFTER the venue heartbeat (heartbeat-before-boot-http invariant)
    # and BEFORE the DB-bound boot work. A warm-thread failure is swallowed →
    # cold cache → the wallet gate fail-closes (fail-safe; boot never hangs).
    _wallet_warm_thread, _wallet_warm_holder = _start_boot_wallet_warm()

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

    # N2 — S2 deployment gate (PR-S1, Bug #3).
    # If S1 is deployed but S2 has not been deployed within 4h, refuse boot.
    # Prevents the daemon running with partial fix coverage beyond the SLA window.
    # Operator override: ZEUS_ACCEPT_S1_ALONE=1 (emergency use only).
    _check_s1_without_s2_sla()

    # §3.1 Data freshness gate — WARN-only at boot (Phase 2: warn; Phase 3: enforce).
    # Runs BEFORE strategy gate so operator sees freshness diagnostics even when
    # strategy gate refuses. GATE SPLIT (§3.7): data gate is operator-overridable
    # via state/control_plane.json::force_ignore_freshness: ["source_name"].
    # Wallet gate (_startup_wallet_check below) is NEVER overridable.
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

    # Efficiency #3 — JOIN the boot wallet warm thread NOW (the DB-bound boot
    # work above ran concurrently with the wallet RPC). After the join the warm
    # record is deterministic (present, or None if the warm failed/timed out),
    # so the wallet gate sees a settled value with no race. One capital log,
    # emitted once the value is known, right before the gate.
    _join_boot_wallet_warm(_wallet_warm_thread)
    _warm_rec = _wallet_warm_holder.record
    _capital_str = (
        f"${_warm_rec.value_usd:.2f}" if _warm_rec is not None else "<wallet_unreachable>"
    )
    logger.info("Capital (on-chain): %s | Kelly: %.0f%%",
                _capital_str,
                settings["sizing"]["kelly_multiplier"] * 100)

    # P7: Fail-closed wallet gate — must run before first cycle.
    # GATE SPLIT (§3.7): wallet failure is ALWAYS fatal, no operator override.
    # Consume the warm record (efficiency #3): warm + gate = exactly ONE
    # current() acquisition. A None warm record → the gate fail-closes.
    _startup_wallet_check(bankroll_record=_warm_rec)
    _start_user_channel_ingestor_if_enabled()

    # MF-1: durable self-healing capital spine — AT BOOT, before any new trading,
    # bridge any EDLI confirmed fill that was orphaned (no position_current) by a
    # prior daemon death / swallowed bridge exception. Closes the restart-specific
    # orphan window immediately so stuck capital is visible to chain-reconcile /
    # exit / harvester / redeem before the first entry wave. Fail-open (never
    # blocks boot); the per-cycle durable scan is the continuous safety net.
    _edli_boot_fill_bridge_recovery()

    # 守護 (2026-06-03): immediately consume any VERIFIED settlement truth that is
    # already on disk for FILLED positions still sitting phase=active (memory #56,
    # Shanghai cca68b44), instead of waiting up to an hour for the scheduled
    # harvester. Runs AFTER the fill-bridge recovery (so freshly-bridged positions
    # are visible) and BEFORE the trading loop. Fail-open; no on-chain side effect.
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
    from apscheduler.executors.pool import ThreadPoolExecutor as _APThreadPoolExecutor

    scheduler = BlockingScheduler(
        timezone=ZoneInfo("UTC"),
        executors={
            "default": _APThreadPoolExecutor(20),
            "reactor": _APThreadPoolExecutor(2),
        },
    )
    discovery = settings["discovery"]

    # All modes use the SAME CycleRunner with different DiscoveryMode values
    # max_instances=1: prevent concurrent execution if previous cycle still running
    edli_cfg = _settings_section("edli", {})
    live_execution_mode = _assert_live_execution_mode_contract(edli_cfg)
    _assert_edli_stage_readiness(edli_cfg)
    _assert_emos_ci_license_seasonal_coverage(edli_cfg)
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
        scheduler.add_job(
            _market_discovery_cycle,
            "interval",
            minutes=5,
            id="market_discovery",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 90.0),
            max_instances=1,
            coalesce=True,
        )
        # AFTERNOON CAPTURE — legacy_cron mode (2026-06-14): see EDLI registration below.
        scheduler.add_job(
            _afternoon_snapshot_capture_cycle,
            "interval",
            minutes=30,
            id="afternoon_snapshot_capture",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 120.0),
            max_instances=1,
            coalesce=True,
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
        # K4.0 REST-THEN-CROSS deadline owner: cancels GTC maker entry rests older
        # than the measured escalation deadline (2.0h). 5-min cadence is well inside
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
        # THROUGHPUT + FRESHNESS STRUCTURAL FIX: dedicated executable-snapshot substrate
        # warmer, DECOUPLED from the reactor decision cycle. It must run inside the
        # 30s executable-price freshness window and start before the first reactor tick;
        # otherwise the reactor reads valid-but-expired price rows and every candidate
        # rejects as EXECUTABLE_SNAPSHOT_STALE. The refresh is scoped to pending families
        # (not a global weather scan) and max_instances=1/coalesce prevents stacked venue
        # I/O. Data-only (no orders); fail-soft.
        # Fitz #5 interval-fit invariant: the refresh budget MUST be strictly less
        # than the interval so the cycle cannot overrun its own trigger (the live
        # "skipped: maximum number of running instances reached" starvation). Asserted
        # here at registration so a future env/default drift that re-breaks the
        # relationship fails LOUDLY at boot instead of silently re-starving coverage.
        _warm_refresh_budget_s = max(
            5.0,
            float(os.environ.get("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "17.0")),
        )
        if _warm_refresh_budget_s >= _EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS:
            raise RuntimeError(
                "EDLI market-substrate warm budget-vs-interval misconfiguration: "
                f"ZEUS_REACTOR_REFRESH_BUDGET_SECONDS={_warm_refresh_budget_s}s must be "
                f"STRICTLY LESS than the {_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS}s warm "
                "interval, else every overlapping cycle is skipped and the executable "
                "substrate is never refreshed (coverage NONE, daemon starved)."
            )
        scheduler.add_job(
            _edli_market_substrate_warm_cycle,
            "interval",
            seconds=_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS,
            id="edli_market_substrate_warm",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 1.0),
            max_instances=1,
            coalesce=True,
        )
        # NEW-LISTING SCOUT (operator 2026-06-09, dimensions a/b/c): lightweight 60s
        # head-page probe for brand-new Polymarket weather listings.  Detects new
        # condition_ids, stages a forecast-materialization fast-lane intent, and marks
        # families for head-of-rotation in the next substrate warm cycle.
        # Fail-open: any exception is caught inside the job.  Data-only; no orders.
        if edli_cfg.get("new_listing_scout_enabled", True):
            scheduler.add_job(
                _new_listing_scout_cycle,
                "interval",
                seconds=60,
                id="new_listing_scout",
                next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 45.0),
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
        # WIRING FIX (operator Point-1 directive 2026-06-08): the replacement-forecast
        # download + shadow-materialize jobs were REMOVED from this live-trading scheduler
        # and moved to the forecast-live (data) daemon. The ~365MB AIFS ensemble fetch
        # (~11.5 min) monopolized disk I/O on the trading process, starving the reactor +
        # market_scanner and locking riskguard dependency reads -> DATA_DEGRADED flap that
        # blocked all trades. They now run on the forecast-live daemon's lane, download
        # cron-driven at publish time (00Z/12Z + release_lag) — see
        # src/ingest/forecast_live_daemon.py and src/data/replacement_forecast_production.py.
        # STRUCTURAL FIX (2026-05-31, #52 follow-up): executable_market_snapshots
        # (EMS) substrate refresh in EDLI modes. market_discovery is the ONLY
        # universe-wide writer of executable_market_snapshots (architecture/
        # db_table_ownership.yaml::executable_market_snapshots; failure_chains.yaml::
        # market_discovery_coverage_collapse) — but it was registered ONLY in
        # legacy_cron (see legacy_cron block above), so in EDLI event-driven modes
        # NOTHING refreshed the EMS substrate across the candidate universe. The
        # edli_market_channel_ingestor (#52) writes execution_feasibility_evidence
        # for the PRE-SUBMIT witness, NOT executable_market_snapshots, which the
        # cert build's QUOTE_FEASIBILITY / executable-snapshot selection requires
        # (src/engine/event_reactor_adapter.py::_latest_snapshot_rows_for_event_family
        # → _passive_maker_context_from_authorities reads orderbook_top_bid/ask off
        # the selected EMS row). With EMS frozen/aging, candidate families lost a
        # fresh active-open snapshot for the selected bin → every live cert build
        # failed EDLI_LIVE_CERTIFICATE_BUILD_FAILED:QUOTE_FEASIBILITY_BID_ASK_REQUIRED
        # (and intermittently EXECUTABLE_SNAPSHOT_BLOCKED) → proof_accepted=0.
        # market_discovery is a DATA-ONLY substrate writer: it submits no orders and
        # touches no arming flags. Wave-1 2026-06-12: the market_substrate_refresh_enabled
        # gate is DELETED — the EMS substrate refresh is structural plumbing the live cert
        # build depends on (without it every cert build fails BID_ASK_REQUIRED), never an
        # optional knob, so it is now ALWAYS registered.
        scheduler.add_job(
            _market_discovery_cycle,
            "interval",
            minutes=5,
            id="market_discovery",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 90.0),
            max_instances=1,
            coalesce=True,
        )
        # AFTERNOON CAPTURE (2026-06-14): dedicated 30-min capture for same-day
        # SETTLEMENT_DAY markets (hours_to_resolution ≤12).  The universe-wide
        # market_discovery and EDLI warm cycle target PENDING families; neither
        # explicitly sweeps the sub-12h same-day window.  This job ensures
        # orderbook snapshots are recorded at ≥30-min cadence through the local-
        # afternoon / pre-close window for backtesting and microstructure analysis.
        # Capture-only (no decision/order); fail-soft; max_instances=1/coalesce.
        scheduler.add_job(
            _afternoon_snapshot_capture_cycle,
            "interval",
            minutes=30,
            id="afternoon_snapshot_capture",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 120.0),
            max_instances=1,
            coalesce=True,
        )
    if live_execution_mode in EDLI_EVENT_DRIVEN_MODES and edli_cfg.get("market_channel_ingestor_enabled"):
        scheduler.add_job(
            _edli_market_channel_ingestor_cycle,
            "interval",
            minutes=1,
            id="edli_market_channel_ingestor",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 20.0),
            max_instances=1,
            coalesce=True,
        )
    if live_execution_mode in EDLI_EVENT_DRIVEN_MODES and _edli_user_channel_reconcile_runtime_enabled(edli_cfg):
        scheduler.add_job(
            _edli_user_channel_reconcile_cycle,
            "interval",
            minutes=1,
            id="edli_user_channel_reconcile",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 25.0),
            max_instances=1,
            coalesce=True,
        )
    # Blocker #56: chain-truth sync + exit-lifecycle monitoring. Registered in BOTH
    # legacy_cron AND EDLI event-driven modes — in EDLI modes run_cycle() never fires,
    # so this standalone job is the ONLY path that populates chain_shares/chain_avg_price
    # and resolves exit_pending_missing / settled-but-active positions. Submit-safe:
    # the job runs the monitoring phase with exit_order_submit_enabled=real_order_submit_enabled
    # (False when live submit is disabled -> DB state transitions only, no real sell orders).
    scheduler.add_job(
        _chain_sync_and_exit_monitor_cycle,
        "interval",
        minutes=2,
        id="chain_sync_and_exit_monitor",
        next_run_time=_utc_run_time_after(HELD_POSITION_MONITOR_FIRST_DELAY_SECONDS),
        max_instances=1,
        coalesce=True,
    )
    # 守護 (2026-06-03): settlement P&L + redeem-intent resolver. Registered in BOTH
    # legacy_cron AND EDLI event-driven modes (see _harvester_should_register). In EDLI
    # modes run_cycle() never fires, so this standalone hourly job is the ONLY producer
    # that consumes VERIFIED settlement_outcomes → marks settled positions closed →
    # enqueues REDEEM_INTENT_CREATED for the redeem pollers. Without it a FILLED position
    # rides to settlement and sits phase=active forever (capital stuck). Shadow-safe: the
    # resolver does no on-chain work; the on-chain redeem POST is the separately-gated
    # _redeem_submitter_cycle, and the resolver is additionally gated by
    # ZEUS_HARVESTER_LIVE_ENABLED (default-OFF no-op).
    if _harvester_should_register(live_execution_mode):
        scheduler.add_job(
            _harvester_cycle, "interval", hours=1, id="harvester",
            max_instances=1, coalesce=True,
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

    # 2026-05-16 PR-I C3 — F14 + F16 cascade-liveness pollers per SCAFFOLD §K v5
    # + architecture/cascade_liveness_contract.yaml. Insertion site is here per
    # SCAFFOLD §K.8 v5 (after L988 venue_heartbeat block; pre-existing K2 jobs
    # below were already migrated to src/ingest_main.py).
    scheduler.add_job(
        _redeem_submitter_cycle, "interval", minutes=5, id="redeem_submitter",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _redeem_reconciler_cycle, "interval", minutes=10, id="redeem_reconciler",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _wrap_intent_creator_cycle, "interval", minutes=5,
        id="wrap_intent_creator", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _wrap_submitter_cycle, "interval", minutes=2,
        id="wrap_submitter", max_instances=1, coalesce=True,
    )
    # P0-3: fast conditional cadence (30s). The reconciler early-exits cheaply
    # (a single state-count query, BEFORE any credential resolution or adapter
    # construction) when no *_TX_HASHED row exists — so the expensive RPC path
    # only fires when there is in-flight wrap work to finalize. The same-tick
    # path now leaves freshly-submitted txs in a TX_HASHED state for THIS job to
    # confirm within ~30s, instead of synchronously blocking the redeem ticks.
    scheduler.add_job(
        _wrap_reconciler_cycle, "interval", seconds=30,
        id="wrap_reconciler", max_instances=1, coalesce=True,
    )
    # PR-S6: deployment freshness gate — runs every 60s, fail-closed at 24h uptime.
    scheduler.add_job(
        _check_deployment_freshness, "interval", seconds=60,
        id="deployment_freshness", max_instances=1, coalesce=True,
    )
    # K2 WU daily collection — hourly tick; WuDailyScheduler gates per-city.
    # Cluster L wiring per G4_CLEANUP_DESIGN.md §2 L (2026-05-18).
    scheduler.add_job(
        _wu_daily_dispatch, "interval", hours=1, id="wu_daily",
        max_instances=1, coalesce=True,
    )
    # Daily 守護 settlement-guard scorecard — runs at 09:15 UTC, after the
    # 07:30 forecasts tick and the hourly settlement-truth writes have landed.
    # Read-only over graded tables; writes state/settlement_guard_report.json +
    # docs/evidence/settlement_guard/<date>.md + a one-line INFO summary.
    scheduler.add_job(
        _settlement_guard_report_tick, "cron", hour=9, minute=15,
        id="settlement_guard_report", max_instances=1, coalesce=True,
    )
    # Standing shadow-vs-live comparator — 09:20 UTC, just after the settlement-
    # guard tick (same WORLD+forecasts read shape, settlement truth already
    # landed). Read-only; writes state/shadow_comparator.json +
    # docs/evidence/shadow_comparisons/<date>.md + a one-line verdict per
    # candidate. INSUFFICIENT_N (honest absence) until a shadow lane persists a
    # comparable q.
    scheduler.add_job(
        _shadow_comparator_tick, "cron", hour=9, minute=20,
        id="shadow_comparator", max_instances=1, coalesce=True,
    )
    # Day0 shadow-receipt outcome enrichment — 09:25 UTC, after the shadow
    # comparator (settlement truth already landed). Grades SETTLED candidate-bearing
    # day0 receipts (later_outcome / would_have_won) against VERIFIED truth via the
    # canonical grade_receipt. PURE-DB, idempotent, never-submit/never-fabricate.
    # Closes the 0%-populated day0 grading layer (operator 2026-06-11).
    scheduler.add_job(
        _day0_shadow_enrichment_tick, "cron", hour=9, minute=25,
        id="day0_shadow_enrichment", max_instances=1, coalesce=True,
    )
    # Settlement skill-attribution — 09:30 UTC, after settlement harvesting +
    # day0 enrichment (settlement truth already landed). Grades each settled
    # position into a skill category (SKILL_WIN / LUCKY_WIN / SKILL_LOSS /
    # MISCALIBRATED_LOSS / STALE_DECISION) so a lucky win can no longer fake
    # system health (operator 2026-06-12 law). Idempotent per position; backfills
    # history on first run. Sole writer of settlement_attribution.
    scheduler.add_job(
        _settlement_skill_attribution_tick, "cron", hour=9, minute=30,
        id="settlement_skill_attribution", max_instances=1, coalesce=True,
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
