"""Zeus main entry point — trading daemon only (Phase 3).

Live entries run through the EDLI event-reactor path (`_edli_event_reactor_cycle`)
only. The legacy `legacy_cron`/DiscoveryMode scheduler path and its manual
entrypoints (`_run_mode`, `run_single_cycle`/`--once`) were retired 2026-07-06
(legacy discovery pipeline deletion) — see src/engine/cycle_runtime.py history
for the deleted `execute_discovery_phase`.

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
import hashlib
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
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo


def _bind_canonical_main_module(module_name: str, module: object) -> None:
    """Keep ``python -m src.main`` process state under one module identity."""

    if module_name == "__main__":
        sys.modules["src.main"] = module


_bind_canonical_main_module(__name__, sys.modules[__name__])

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
_day0_urgent_wake_pending = threading.Event()
_edli_reactor_wake_thread: threading.Thread | None = None
_edli_last_reactor_wake_id: str | None = None
_HELD_POSITION_MONITOR_DEFER_JOBS = frozenset(
    {
        "market_discovery",
        "EDLI mainstream warm",
    }
)
_market_discovery_last_completed_monotonic: float | None = None
OPENING_HUNT_FIRST_DELAY_SECONDS = 30.0
_EDLI_COMMAND_RECOVERY_INTERVAL_SECONDS = 60.0
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






def _substrate_refresh_canonical_metric(metric: object) -> str:
    text = _substrate_refresh_family_text_key(metric)
    if text in {"low", "lowest", "min", "minimum", "tmin"} or text.startswith("lowest "):
        return "low"
    if text in {"high", "highest", "max", "maximum", "tmax"} or text.startswith("highest "):
        return "high"
    return text


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
        "edli_stage_source_health_json",
        "edli_stage_status_json",
    ),
}

# Immutable process identity populated in main() at boot for receipts and operators.
# Tests monkeypatch this dict directly; the observer reads it each tick.
_BOOT_STATE: dict = {"sha": None, "ts": None, "identity_source": "unavailable"}


def _is_full_git_sha(value: object) -> bool:
    text = str(value or "").strip()
    return len(text) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in text)


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


def _defer_for_held_position_monitor(job_name: str) -> bool:
    """Return True when the held-position monitor should pre-empt discretionary jobs.

    The monitor itself is non-reentrant, but it must not globally stop the live
    money path. Targeted EDLI jobs stay on the continuous decision line while
    broad scans yield to the monitor bootstrap.
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

    # T5 MIGRATION (docs/rebuild/quarantine_excision_2026-07-11.md, deliverable
    # B): mixed schema_epoch across the three DBs means a partially-applied
    # scripts/migrations/2026_07_quarantine_phase_retirement.py run or a
    # crash mid-migration — the same guard the real boot path in main()
    # enforces unconditionally, exercised here for the read-only smoke too.
    try:
        from src.state.db import assert_schema_epoch_not_mixed, read_schema_epoch

        def _epoch(path):
            if not path.exists():
                return None
            _c = _ro_conn(path)
            try:
                return read_schema_epoch(_c)
            finally:
                _c.close()

        assert_schema_epoch_not_mixed(
            world_epoch=_epoch(ZEUS_WORLD_DB_PATH),
            forecasts_epoch=_epoch(ZEUS_FORECASTS_DB_PATH),
            trade_epoch=_epoch(_zeus_trade_db_path()),
        )
        results.append(("schema_epoch", True, "schema_epoch not mixed — OK"))
    except Exception as exc:
        results.append(("schema_epoch", False, str(exc)))

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
        identity_observations = _edli_stage_loaded_sha_observations(loaded_sha_file)
        if identity_observations:
            logger.warning(
                "EDLI stage code identity observed: %s",
                ",".join(identity_observations),
            )
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


def _edli_stage_loaded_sha_observations(path: str) -> list[str]:
    file_path = Path(path)
    if not file_path.exists():
        return [f"EDLI_STAGE_LOADED_SHA_MISSING:{path}"]
    try:
        payload = json.loads(file_path.read_text())
    except json.JSONDecodeError:
        return [f"EDLI_STAGE_LOADED_SHA_INVALID_JSON:{path}"]
    loaded_sha = str(payload.get("loaded_sha") or payload.get("boot_sha") or payload.get("current_sha") or "").strip()
    expected_sha = str(_BOOT_STATE.get("sha") or "").strip()
    if not loaded_sha:
        return ["EDLI_STAGE_LOADED_SHA_MISSING_VALUE"]
    if not _is_full_git_sha(loaded_sha):
        return [f"EDLI_STAGE_LOADED_SHA_INVALID_VALUE:{loaded_sha}"]
    if expected_sha and not _is_full_git_sha(expected_sha):
        return [f"EDLI_STAGE_EXPECTED_SHA_INVALID_VALUE:{expected_sha}"]
    if expected_sha and loaded_sha and loaded_sha != expected_sha:
        return [f"EDLI_STAGE_LOADED_SHA_MISMATCH:loaded={loaded_sha}:expected={expected_sha}"]
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
                _write_scheduler_health(job_name, failed=False, started=True)
                result = fn(*args, **kwargs)
                _write_scheduler_health(job_name, failed=False)
                return result
            except Exception as exc:
                logger.error("%s failed: %s", job_name, exc, exc_info=True)
                _write_scheduler_health(job_name, failed=True, reason=str(exc))

        return _wrapper

    return _decorator


def _scheduler_max_instance_skip_listener(event: Any) -> None:
    """Surface APScheduler max-instance skips as live scheduler health."""

    job_name = str(getattr(event, "job_id", "") or "").strip()
    if not job_name:
        return
    logger.warning("scheduler job skipped: job=%s reason=max_instances_reached", job_name)
    _write_scheduler_health(
        job_name,
        failed=False,
        skipped=True,
        skip_reason="max_instances_reached",
    )





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


@_scheduler_job("live_health_composite")
def _live_health_composite_cycle() -> None:
    """Refresh composite live-health without blocking the heartbeat pulse."""

    from src.control.live_health import compute_composite_live_health

    compute_composite_live_health()


_venue_heartbeat_supervisor = None
_venue_heartbeat_adapter = None
_venue_heartbeat_thread = None
_edli_reactor_active_lock = threading.Lock()
_EXIT_MONITOR_REACTOR_HANDOFF_SECONDS = 30.0
_venue_background_maintenance_lock = threading.Lock()
_last_venue_background_maintenance_attempt_at = None
VENUE_BACKGROUND_MAINTENANCE_SECONDS = 30.0
_collateral_background_refresh_lock = threading.Lock()
_last_collateral_heartbeat_refresh_attempt_at = None
COLLATERAL_HEARTBEAT_REFRESH_SECONDS = 30.0

# Continuous re-decision P2 (resurrection 2026-06-12): the cheap-screen job's advisory lock (so
# overlapping triggers never double-run the screen) and the PROCESS-GLOBAL act-once-per-edge dedup
# state (held across cycles so a bare price wiggle does not re-fire — R6). Plain dict mutated only
# under the lock-held job; no cross-thread contention beyond the advisory acquire. The grace-second
# constants moved to src.events.reactor with the R4-b4 cluster extraction (2026-07-08): no other
# main.py reader.
_edli_redecision_screen_lock = threading.Lock()
_edli_redecision_acted_state: dict = {}


def _venue_heartbeat_mode() -> str:
    return os.environ.get("ZEUS_VENUE_HEARTBEAT_MODE", "internal").strip().lower()


def _external_venue_heartbeat_enabled() -> bool:
    return _venue_heartbeat_mode() == "external"


def _edli_reactor_active() -> bool:
    return _edli_reactor_active_lock.locked()


def _defer_for_active_entry_reactor(job_name: str) -> bool:
    """Keep lower-priority DB scans off the active entry-reactor read path."""

    if not _edli_reactor_active():
        return False
    logger.info("%s deferred: EDLI reactor active", job_name)
    return True


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
            from src.execution.exit_lifecycle import (
                _release_ws_gap_blocked_exit_retries_after_m5_clear,
            )

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


# R4-b (2026-07-08): _release_ws_gap_blocked_exit_retries_after_m5_clear,
# _append_exit_retry_release_events_and_update_projection, and
# _release_allocator_config_blocked_exit_retries_after_refresh moved to
# src.execution.exit_lifecycle (owning module for exit-retry-release state).
# See that module's R4-b section header. The one other call site
# (_run_ws_gap_reconcile_if_required above) imports from there directly.


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
            SELECT p.event_id,
                   p.last_error
            FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
            JOIN opportunity_events e ON e.event_id = p.event_id
            WHERE p.consumer_name = ?
              AND (
                    p.processing_status = 'pending'
                    OR (
                        p.processing_status = 'processing'
                        AND COALESCE(p.last_error, '') <> ''
                    )
                  )
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
                END) AS refresh_urgency,
            MAX(CASE
                  WHEN e.event_type = 'DAY0_EXTREME_UPDATED'
                   AND COALESCE(p.last_error, '') LIKE '%DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE%'
                  THEN 1 ELSE 0
                END) AS day0_hourly_blocked
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
            MAX(CASE
                  WHEN e.event_type = 'DAY0_EXTREME_UPDATED'
                   AND COALESCE(p.last_error, '') LIKE '%DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE%'
                  THEN 1 ELSE 0
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




def _runtime_source_fingerprint(repo_root: Path) -> str | None:
    """Return a stable 40-hex identity when Git metadata is unavailable."""

    from src.control.runtime_code_plane import RUNTIME_SCRIPT_FILES

    paths = [
        path
        for root in (repo_root / "src", repo_root / "config")
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    paths.extend(
        repo_root / relative
        for relative in (
            *sorted(RUNTIME_SCRIPT_FILES),
            "architecture/db_table_ownership.yaml",
            "architecture/runtime_posture.yaml",
            "architecture/strategy_profile_registry.yaml",
        )
        if (repo_root / relative).is_file()
    )
    if not paths:
        return None
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        try:
            relative = path.relative_to(repo_root).as_posix()
            content = path.read_bytes()
        except OSError:
            return None
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()[:40]


def _capture_boot_state() -> dict:
    """Capture a code identity without making Git availability a boot gate."""
    import subprocess

    from src.config import PROJECT_ROOT

    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).strip().decode()
        return {
            "sha": sha,
            "ts": datetime.now(timezone.utc),
            "identity_source": "git_head",
        }
    except Exception as exc:
        fingerprint = _runtime_source_fingerprint(PROJECT_ROOT)
        logger.warning(
            "runtime_identity: git HEAD unavailable (%s); source_fingerprint=%s",
            exc,
            fingerprint[:8] if fingerprint else "unavailable",
        )
        return {
            "sha": fingerprint,
            "ts": datetime.now(timezone.utc),
            "identity_source": "runtime_source_fingerprint" if fingerprint else "unavailable",
        }


def _write_loaded_sha_state(boot_sha: str | None) -> None:
    """Persist the process code identity once at boot for receipts and operators."""
    if not boot_sha:
        logger.warning(
            "loaded_sha: process identity unavailable; skipping loaded_sha.json write"
        )
        return
    if not _is_full_git_sha(boot_sha):
        logger.error(
            "loaded_sha: refusing to write invalid process identity %r",
            boot_sha,
        )
        return
    from src.config import state_path

    out_path = state_path("loaded_sha.json")
    payload = {
        "loaded_sha": boot_sha,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "identity_source": str(_BOOT_STATE.get("identity_source") or "git_head"),
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
    """Report loaded-code/worktree drift without converting it into trading authority.

    Compares the git HEAD SHA at daemon boot vs the current working-tree HEAD.
    Divergence means the worktree changed after boot; it does not prove that a
    probability, quote, position, or settlement fact is invalid. The process
    boot SHA remains the decision-code identity, while this job emits operator
    evidence for an intentional restart.

    State is written to state/deployment_freshness.json, never to the control
    plane. This observer never pauses entries or terminates the daemon.

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
    try:
        from src.control.runtime_code_plane import (
            dirty_runtime_worktree_paths,
            runtime_code_plane_diff,
        )

        code_plane = runtime_code_plane_diff(
            _repo_root,
            boot_sha=_boot_sha,
            current_sha=current_sha,
            timeout=5,
        )
        dirty_runtime_paths = dirty_runtime_worktree_paths(_repo_root, timeout=5)
    except Exception as exc:  # noqa: BLE001
        code_plane = None
        dirty_runtime_paths = ()
        logger.warning(
            "deployment_freshness: runtime code-plane classification failed (%s); "
            "recording SHA drift as unclassified observation",
            exc,
        )

    def _write_deployment_freshness_state(payload: dict[str, object]) -> None:
        try:
            _tmp = str(df_path) + ".tmp"
            with open(_tmp, "w") as _f:
                json.dump(payload, _f, indent=2)
            os.replace(_tmp, str(df_path))
        except Exception as _exc:
            logger.warning("deployment_freshness: failed to write flag file: %s", _exc)

    if current_sha == _boot_sha and not dirty_runtime_paths:
        if df_path.exists():
            _write_deployment_freshness_state(
                {
                    "boot_sha": _boot_sha,
                    "current_sha": current_sha,
                    "uptime_hours": round(uptime_hours, 2),
                    "detected_at": _now.isoformat(),
                    "pause_reason": None,
                    "status": "fresh",
                    "code_plane_status": "same_sha",
                    "runtime_code_changed": False,
                }
            )
        return  # No divergence.

    if code_plane is not None and not code_plane.runtime_code_changed and not dirty_runtime_paths:
        _write_deployment_freshness_state(
            {
                "boot_sha": _boot_sha,
                "current_sha": current_sha,
                "uptime_hours": round(uptime_hours, 2),
                "detected_at": _now.isoformat(),
                "pause_reason": None,
                "status": "fresh",
                "code_plane_status": code_plane.status,
                "runtime_code_changed": False,
                "changed_paths_sample": list(code_plane.changed_paths[:20]),
            }
        )
        logger.info(
            "deployment_freshness: HEAD drift is non-runtime-only; "
            "observed only (boot_sha=%s current_sha=%s paths=%s)",
            _boot_sha[:8],
            current_sha[:8],
            list(code_plane.changed_paths[:5]),
        )
        return

    stale_status = "dirty_runtime_worktree" if dirty_runtime_paths else "mismatch"
    changed_paths_sample = (
        list(code_plane.changed_paths[:20]) if code_plane is not None else []
    )
    dirty_paths_sample = list(dirty_runtime_paths[:20])
    logger.warning(
        "deployment_freshness_observed: boot_sha=%s current_sha=%s "
        "uptime_hours=%.1f status=%s dirty_runtime_paths=%s",
        _boot_sha[:8],
        current_sha[:8],
        uptime_hours,
        stale_status,
        dirty_paths_sample[:5],
    )
    _write_deployment_freshness_state(
        {
            "boot_sha": _boot_sha,
            "current_sha": current_sha,
            "uptime_hours": round(uptime_hours, 2),
            "detected_at": _now.isoformat(),
            "pause_reason": None,
            "status": stale_status,
            "code_plane_status": (
                code_plane.status if code_plane is not None else "classification_failed"
            ),
            "runtime_code_changed": True,
            "changed_paths_sample": changed_paths_sample,
            "worktree_runtime_dirty": bool(dirty_runtime_paths),
            "dirty_runtime_paths_sample": dirty_paths_sample,
        }
    )


_DEPLOYMENT_FRESHNESS_PAUSE_REASON = "deployment_freshness_mismatch"
_DEPLOYMENT_FRESHNESS_LEGACY_PAUSE_REASONS = frozenset(
    {"deployment_freshness_4h_divergence"}
)
_LIVE_SIDECAR_BOOT_HEARTBEATS = (
    ("forecast-live", "forecast-live-heartbeat.json", 120.0),
    ("substrate-observer", "daemon-heartbeat-substrate-observer.json", 180.0),
    ("price-channel-ingest", "daemon-heartbeat-price-channel-ingest.json", 180.0),
    ("post-trade-capital", "daemon-heartbeat-post-trade-capital.json", 180.0),
)
_LIVE_SIDECAR_BOOT_CLOCK_SKEW_SECONDS = 5.0


def _boot_deployment_freshness_auto_resume() -> None:
    """Retire only obsolete deployment-freshness pauses, then refresh evidence.

    Deployment/worktree identity is observability, so an old pause with one of
    the exact retired reasons must not survive a boot. Operator, risk, source,
    and any other pause reason remain untouched.
    """
    from src.control.control_plane import (
        retire_entries_pause_for_reasons,
    )

    try:
        retired = {
            _DEPLOYMENT_FRESHNESS_PAUSE_REASON,
            *_DEPLOYMENT_FRESHNESS_LEGACY_PAUSE_REASONS,
        }
        if retire_entries_pause_for_reasons(
            retired,
            retirement_reason="deployment_freshness_pause_retired",
        ):
            logger.info(
                "deployment_freshness_auto_resume: retired obsolete deployment pause"
            )
    except Exception as exc:
        logger.warning(
            "deployment_freshness_auto_resume: pause retirement failed (%s)",
            exc,
            exc_info=True,
        )
    _check_deployment_freshness()


def _parse_sidecar_heartbeat_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _git_head_matches_boot(boot_sha: str, heartbeat_sha: str) -> bool:
    boot = str(boot_sha or "").strip()
    heartbeat = str(heartbeat_sha or "").strip()
    if not boot or not heartbeat:
        return False
    if boot == heartbeat:
        return True
    return len(heartbeat) >= 7 and boot.startswith(heartbeat)


def _boot_blocked_action_capability(
    *,
    action: str,
    capability: str,
    reason: str,
    timestamp: str,
) -> dict[str, Any]:
    component_name = f"{capability}:live_boot_prerequisite"
    return {
        "action": action,
        "capability": capability,
        "status": "unavailable",
        "global_allow_submit": False,
        "components": [
            {
                "component": component_name,
                "capability": capability,
                "allowed": False,
                "reason": reason,
                "observed_at": timestamp,
            }
        ],
        "unavailable_components": [component_name],
    }


def _boot_blocked_execution_capability(*, reason: str, timestamp: str) -> dict[str, Any]:
    """Derived operator proof that startup never reached executable submit gates."""

    return {
        "schema_version": 1,
        "authority": "startup_boot_blocked_operator_visibility",
        "derived_only": True,
        "live_action_authorized": False,
        "entry": _boot_blocked_action_capability(
            action="entry",
            capability="live_venue_submit",
            reason=reason,
            timestamp=timestamp,
        ),
        "exit": _boot_blocked_action_capability(
            action="exit",
            capability="reduce_only_exit_submit",
            reason=reason,
            timestamp=timestamp,
        ),
    }


def _write_startup_boot_blocked_operator_status(
    *,
    state_root: Path,
    boot_sha: str,
    detail: str,
    checked_at: datetime,
) -> None:
    """Write fresh operator projections when live boot fails before schedulers start."""

    timestamp = checked_at.astimezone(timezone.utc).isoformat()
    reason = f"LIVE_SIDECAR_BOOT_BLOCKED: {detail}"
    heartbeat_payload = {
        "alive": False,
        "timestamp": timestamp,
        "mode": get_mode(),
        "pid": os.getpid(),
        "process": "src.main",
        "daemon_health": "BOOT_BLOCKED",
        "boot_blocked": True,
        "failure_reason": reason,
        "loaded_sha": boot_sha,
    }
    status_payload = {
        "timestamp": timestamp,
        "generated_at": timestamp,
        "mode": get_mode(),
        "status": "BOOT_BLOCKED",
        "live_action_authorized": False,
        "process": {
            "pid": os.getpid(),
            "mode": get_mode(),
            "process": "src.main",
            "boot_sha": boot_sha,
            "boot_blocked": True,
        },
        "cycle": {
            "mode": "boot_blocked",
            "started_at": timestamp,
            "completed_at": timestamp,
            "candidates": 0,
            "trades": 0,
            "no_trades": 0,
            "entry_orders_submitted": 0,
            "exits": 0,
            "rejection_reason_counts": {"live_boot_blocked": 1},
            "entries_blocked_reason": reason,
        },
        "risk": {
            "infrastructure_level": "RED",
            "infrastructure_scope": "startup",
            "infrastructure_issues": ["live_sidecar_boot_blocked"],
        },
        "failure_reason": reason,
        "live_boot": {
            "ok": False,
            "issue": "LIVE_SIDECAR_BOOT_BLOCKED",
            "detail": detail,
            "boot_sha": boot_sha,
        },
        "execution_capability": _boot_blocked_execution_capability(
            reason=reason,
            timestamp=timestamp,
        ),
    }
    state_root.mkdir(parents=True, exist_ok=True)
    for filename, payload in (
        ("daemon-heartbeat.json", heartbeat_payload),
        ("status_summary.json", status_payload),
    ):
        path = state_root / filename
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp_path.write_text(json.dumps(payload, sort_keys=True) + "\n")
            tmp_path.replace(path)
        except OSError:
            logger.exception(
                "failed to write boot-blocked operator status file: %s",
                path,
            )


def _startup_required_sidecar_head_check(
    *,
    boot_sha: str | None = None,
    state_dir: Path | str | None = None,
    now: datetime | None = None,
) -> None:
    """Fail live boot only when required sidecar liveness is unavailable.

    The operator preflight already checks this, but launchd can still be loaded
    directly. The live order daemon consumes substrate, price-channel, forecast,
    and capital surfaces produced by these sidecars, so startup must prove they
    are present and fresh before any entry path can arm. Their reported code
    identities remain observable but do not prove market data invalidity.
    """

    if get_mode() != "live":
        return

    expected_sha = str(boot_sha or _BOOT_STATE.get("sha") or "").strip()

    from src.config import STATE_DIR

    state_root = (
        Path(state_dir).expanduser().resolve()
        if state_dir is not None
        else Path(STATE_DIR)
    )
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    failures: list[str] = []
    identity_observations: list[str] = []
    ok: list[str] = []
    for daemon, filename, max_age_seconds in _LIVE_SIDECAR_BOOT_HEARTBEATS:
        path = state_root / filename
        if not path.exists():
            failures.append(f"{daemon}:missing:{path}")
            continue
        try:
            payload = json.loads(path.read_text())
        except Exception as exc:
            failures.append(f"{daemon}:unreadable:{exc.__class__.__name__}")
            continue
        heartbeat_sha = str(payload.get("git_head") or "").strip()
        if expected_sha and not _git_head_matches_boot(expected_sha, heartbeat_sha):
            identity_observations.append(
                f"{daemon}:git_head_mismatch heartbeat={heartbeat_sha or '<missing>'} "
                f"boot={expected_sha[:8]}"
            )
        heartbeat_at = _parse_sidecar_heartbeat_time(
            payload.get("alive_at") or payload.get("written_at") or payload.get("timestamp")
        )
        if heartbeat_at is None:
            failures.append(f"{daemon}:timestamp_invalid")
            continue
        age_seconds = (checked_at - heartbeat_at).total_seconds()
        if (
            age_seconds < -_LIVE_SIDECAR_BOOT_CLOCK_SKEW_SECONDS
            or age_seconds > max_age_seconds
        ):
            failures.append(
                f"{daemon}:stale age_seconds={age_seconds:.1f} max={max_age_seconds:.1f}"
            )
            continue
        ok.append(f"{daemon}@{heartbeat_sha[:8] or 'unknown'} age={age_seconds:.1f}s")

    if failures:
        detail = "; ".join(failures)
        _write_startup_boot_blocked_operator_status(
            state_root=state_root,
            boot_sha=expected_sha,
            detail=detail,
            checked_at=checked_at,
        )
        logger.critical("LIVE_SIDECAR_BOOT_BLOCKED: %s", detail)
        raise SystemExit(f"LIVE_SIDECAR_BOOT_BLOCKED: {detail}")

    if identity_observations:
        logger.warning(
            "live sidecar code identity observed: %s",
            "; ".join(identity_observations),
        )
    logger.info("live sidecar boot freshness: OK (%s)", ", ".join(ok))


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


def _edli_refresh_global_allocator_for_live_bridge(conn, *, portfolio_snapshot=None) -> dict:
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

        _portfolio = portfolio_snapshot if portfolio_snapshot is not None else load_portfolio()
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


# R4-b (2026-07-08): _refresh_global_allocator_for_held_position_monitor moved
# to src.execution.exit_lifecycle (single caller was _exit_monitor_cycle,
# also moved there as run_exit_monitor_cycle).


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




# DEAD-PROMOTION-APPARATUS REMOVAL (2026-06-16): the promotion / capital-objective
# evidence parsers (_replacement_forecast_{promotion,capital_objective}_evidence_from_
# settings) were REMOVED. They imported the deleted go_live_report verdict module and
# fed the runtime-policy resolver / switch-decision evaluator — both of which ignore
# these objects after the live runtime flag path moved to runtime_layer='live'. The two
# live-adapter call sites now pass None (the adapter default),
# which is behavior-identical. See docs/evidence/timing_audit/.




# WIRING FIX (operator Point-1 directive 2026-06-08): _replacement_forecast_download_cycle
# and _replacement_forecast_live_materialize_cycle were MOVED to
# src/data/replacement_forecast_production.py and are now SCHEDULED on the forecast-live
# (data) daemon (src/ingest/forecast_live_daemon.py). They are imported back into this
# module (top of file) for by-name resolution only; the live-trading scheduler no longer
# registers them.


@_scheduler_job("edli_event_reactor")
def _edli_event_reactor_cycle(
    *,
    producer_wake_reason: str | None = None,
    producer_wake_event_ids: tuple[str, ...] = (),
    producer_wake_families: tuple[tuple[str, str, str], ...] = (),
) -> bool:
    """Scheduler hook -- body owned by src.events.reactor (R4-b3 reactor+prune
    cluster extraction, 2026-07-08) as ``run_edli_event_reactor_cycle``. See
    that function's docstring for the full EDLI decision cycle it runs
    (forecast-snapshot / Day0 discovery, prune, process_pending, submit).

    ``_edli_reactor_active_lock`` is a cross-job scheduling-coordination
    primitive (5+ other EDLI jobs read ``_edli_reactor_active()`` off it), so
    main.py -- the dispatcher -- retains ownership and injects the Lock
    object itself into the extracted cycle, which owns its own
    acquire/release lifecycle exactly as it did inline.
    """
    from src.events.reactor import run_edli_event_reactor_cycle

    _start_edli_reactor_wake_listener()
    return run_edli_event_reactor_cycle(
        active_lock=_edli_reactor_active_lock,
        producer_wake_reason=producer_wake_reason,
        producer_wake_event_ids=producer_wake_event_ids,
        producer_wake_families=producer_wake_families,
        urgent_day0_pending=_day0_urgent_wake_pending.is_set,
    )


def _edli_initialize_reactor_wake_cursor() -> None:
    global _edli_last_reactor_wake_id

    _edli_last_reactor_wake_id = None
    _day0_urgent_wake_pending.clear()


def _day0_wake_target_families(
    event_ids: tuple[str, ...],
) -> frozenset[tuple[str, str, str]] | None:
    clean_event_ids = tuple(
        dict.fromkeys(
            event_id
            for raw_event_id in event_ids
            if (event_id := str(raw_event_id or "").strip())
        )
    )
    if not clean_event_ids:
        return None

    conn = None
    try:
        conn = get_world_connection_read_only()
        placeholders = ",".join("?" for _ in clean_event_ids)
        rows = conn.execute(
            f"""
            SELECT event_id, event_type, payload_json
              FROM opportunity_events
             WHERE event_id IN ({placeholders})
            """,
            clean_event_ids,
        ).fetchall()
    except Exception:
        logger.warning(
            "Day0 wake family scope unavailable; using full exit monitor",
            exc_info=True,
        )
        return None
    finally:
        if conn is not None:
            conn.close()

    if len(rows) != len(clean_event_ids):
        logger.warning(
            "Day0 wake family scope incomplete events=%d rows=%d; "
            "using full exit monitor",
            len(clean_event_ids),
            len(rows),
        )
        return None

    families: set[tuple[str, str, str]] = set()
    try:
        for _event_id, event_type, payload_json in rows:
            if str(event_type or "") != "DAY0_EXTREME_UPDATED":
                return None
            payload = json.loads(str(payload_json or ""))
            city = str(payload.get("city") or "").strip()
            target_date = date.fromisoformat(
                str(payload.get("target_date") or "").strip()[:10]
            ).isoformat()
            metric = str(payload.get("metric") or "").strip().lower()
            if not city or metric not in {"high", "low"}:
                return None
            families.add((city, target_date, metric))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning(
            "Day0 wake family payload invalid; using full exit monitor",
            exc_info=True,
        )
        return None
    return frozenset(families) or None


def _day0_wake_requires_exit_monitor(
    target_families: frozenset[tuple[str, str, str]] | None,
) -> bool:
    """Fail closed unless the target families have no position or resting entry."""

    if not target_families:
        return True
    family_keys = {
        (
            str(city or "").strip().casefold(),
            str(target_date or "").strip()[:10],
            str(metric or "").strip().lower(),
        )
        for city, target_date, metric in target_families
    }
    conn = None
    try:
        from src.execution.day0_hard_fact_exit import _target_family_entry_orders
        from src.state.db import (
            OPEN_EXPOSURE_PHASES,
            get_trade_connection_read_only,
        )

        conn = get_trade_connection_read_only()
        placeholders = ",".join("?" for _ in OPEN_EXPOSURE_PHASES)
        positions = conn.execute(
            f"""
            SELECT city, target_date, temperature_metric
              FROM position_current
             WHERE phase IN ({placeholders})
            """,
            tuple(OPEN_EXPOSURE_PHASES),
        ).fetchall()
        if any(
            (
                str(city or "").strip().casefold(),
                str(target_date or "").strip()[:10],
                str(metric or "").strip().lower(),
            )
            in family_keys
            for city, target_date, metric in positions
        ):
            return True
        open_entries = _target_family_entry_orders(conn, family_keys)
        return open_entries is None or bool(open_entries)
    except Exception:
        logger.warning(
            "Day0 wake exit-work probe unavailable; using full exit monitor",
            exc_info=True,
        )
        return True
    finally:
        if conn is not None:
            conn.close()


def _edli_reactor_wake_poll_once() -> bool:
    """Run the canonical reactor once for a new durable-producer wake hint."""

    global _edli_last_reactor_wake_id

    from src.runtime.reactor_wake import (
        acknowledge_reactor_wake,
        acknowledge_reactor_wakes,
        coalescible_reactor_wakes,
        read_reactor_wake,
        reactor_urgent_wake_identity,
    )

    wake = read_reactor_wake()
    if wake is None or wake.wake_id == _edli_last_reactor_wake_id:
        return False
    wakes = coalescible_reactor_wakes(wake)
    wake_event_ids = tuple(
        dict.fromkeys(event_id for queued in wakes for event_id in queued.event_ids)
    )
    wake_families = tuple(
        dict.fromkeys(
            family for queued in wakes for family in queued.forecast_families
        )
    )
    day0_wake = wake.reason == "day0_extreme_event_committed"
    substrate_refresh_wake = wake.reason == "money_path_substrate_refreshed"
    if day0_wake:
        _day0_urgent_wake_pending.set()
    if day0_wake and _held_position_monitor_active.is_set():
        return False
    if substrate_refresh_wake:
        if (
            _edli_reactor_active_lock.locked()
            or _edli_redecision_screen_lock.locked()
        ):
            return False
        _edli_continuous_redecision_screen_cycle()
        ran = True
    else:
        ran = False
    if (
        not substrate_refresh_wake
        and not day0_wake
        and _edli_reactor_active_lock.locked()
    ):
        return False
    if day0_wake and not substrate_refresh_wake:
        target_families = _day0_wake_target_families(wake_event_ids)
        if _day0_wake_requires_exit_monitor(target_families):
            try:
                monitor_ran = _exit_monitor_cycle(
                    target_families=target_families,
                    urgent_day0=True,
                )
            except Exception:
                logger.exception(
                    "Day0 reactor wake held-position monitor failed; "
                    "wake remains queued for retry"
                )
                return False
            if monitor_ran is not True:
                return False
        else:
            logger.info(
                "Day0 reactor wake bypassed exit monitor: "
                "target families have no runtime exposure or resting entry"
            )
    if not substrate_refresh_wake:
        ran = _edli_event_reactor_cycle(
            producer_wake_reason=wake.reason,
            producer_wake_event_ids=wake_event_ids,
            producer_wake_families=wake_families,
        )
    if ran is not True:
        return False
    acknowledged = (
        acknowledge_reactor_wake(wake)
        if len(wakes) == 1
        else acknowledge_reactor_wakes(wakes)
    )
    if not acknowledged:
        logger.warning(
            "EDLI reactor processed wake id=%s batch=%d but queue acknowledgement failed; "
            "leaving it pending for retry",
            wake.wake_id,
            len(wakes),
        )
        return False
    if day0_wake:
        try:
            next_urgent_identity = reactor_urgent_wake_identity()
        except Exception:
            logger.warning(
                "Day0 urgent wake state could not be refreshed after acknowledgement; "
                "keeping periodic monitor preemption armed",
                exc_info=True,
            )
        else:
            if (
                next_urgent_identity is not None
                and next_urgent_identity[0] != wake.wake_id
                and next_urgent_identity[1] == "day0_extreme_event_committed"
            ):
                _day0_urgent_wake_pending.set()
            else:
                _day0_urgent_wake_pending.clear()
    _edli_last_reactor_wake_id = wake.wake_id
    logger.info(
        "EDLI reactor consumed wake id=%s source=%s reason=%s batch=%d events=%d families=%d",
        wake.wake_id,
        wake.source,
        wake.reason,
        len(wakes),
        len(wake_event_ids),
        len(wake_families),
    )
    return True


def _run_edli_reactor_wake_listener(
    *,
    stop_event: threading.Event,
    poll_seconds: float = 1.0,
) -> None:
    from src.runtime.reactor_wake import reactor_wake_listener_socket

    fallback_seconds = max(0.05, float(poll_seconds))
    with reactor_wake_listener_socket() as notifier:
        if notifier is not None:
            notifier.settimeout(fallback_seconds)
        while not stop_event.is_set():
            if notifier is None:
                if stop_event.wait(fallback_seconds):
                    break
            else:
                try:
                    notifier.recv(1)
                except TimeoutError:
                    pass
                except OSError:
                    logger.exception("EDLI reactor wake notifier receive failed")
                    if stop_event.wait(fallback_seconds):
                        break
            try:
                _edli_reactor_wake_poll_once()
            except Exception:
                logger.exception("EDLI reactor wake listener poll failed")


def _start_edli_reactor_wake_listener() -> None:
    global _edli_reactor_wake_thread

    if _edli_reactor_wake_thread is not None and _edli_reactor_wake_thread.is_alive():
        return
    _edli_initialize_reactor_wake_cursor()
    stop_event = threading.Event()
    _edli_reactor_wake_thread = threading.Thread(
        target=_run_edli_reactor_wake_listener,
        kwargs={"stop_event": stop_event},
        name="edli-reactor-wake",
        daemon=True,
    )
    _edli_reactor_wake_thread.start()


@_scheduler_job("edli_bankroll_warm")
def _edli_bankroll_warm_cycle() -> None:
    """Scheduler hook — body owned by src.runtime.bankroll_provider (R4-b
    extraction, 2026-07-08). See that module's ``run_warm_cycle`` docstring
    for the structural fix this job implements (#45 follow-up)."""
    from src.runtime.bankroll_provider import run_warm_cycle

    run_warm_cycle()


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
    one cadence ahead of the next entry auction. This lets already-persisted
    WS/REST fill facts clear capital ambiguity before the next decision without
    polling faster than the 60-second decision clock. The sweep itself is
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


@_scheduler_job("chain_mirror_reconcile")
def _chain_mirror_reconcile_cycle() -> None:
    """Scheduler hook — body owned by src.state.chain_mirror_reconciler (R4-b
    extraction, 2026-07-08). See that module's ``run_cycle`` docstring for the
    chain-mirror invariant (operator directive 2026-07-04)."""
    if _defer_for_active_entry_reactor("chain_mirror_reconcile"):
        return
    if _edli_redecision_screen_lock.locked():
        logger.info("chain_mirror_reconcile deferred: redecision screen active")
        return
    if _held_position_monitor_active.is_set():
        logger.info("chain_mirror_reconcile deferred: held-position monitor active")
        return

    from src.state.chain_mirror_reconciler import run_cycle

    run_cycle()


def _edli_boot_event_claim_recovery(*, boot_at: datetime) -> int:
    """Return dead prior-runtime claims to the exactly-once queue before scheduling."""

    if boot_at.tzinfo is None:
        raise ValueError("EDLI_BOOT_AT_NAIVE")
    from src.events.event_store import EventStore
    from src.state.db import world_write_lock

    world = get_world_connection()
    try:
        with world_write_lock(world):
            recovered = EventStore(world).requeue_processing_before_boot(
                boot_at=boot_at.astimezone(timezone.utc).isoformat()
            )
    finally:
        world.close()
    if recovered:
        logger.warning(
            "edli_boot_event_claim_recovery: requeued prior-runtime processing claims=%d",
            recovered,
        )
    return recovered


def _edli_boot_command_recovery_once(*, boot_at: datetime | None = None) -> None:
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
    _edli_boot_event_claim_recovery(
        boot_at=boot_at or datetime.now(timezone.utc)
    )
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
    from src.execution.venue_cancel_journal import run_persisted_cancels_for_expired_rests
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
    emit_lock_timeout_s = _edli_emit_lock_timeout_seconds(_settings_section("edli", {}) or {})
    emit_acquired = False
    try:
        emit_acquired = _edli_acquire_mutex(emit_mutex, timeout=emit_lock_timeout_s)
        if not emit_acquired:
            logger.warning(
                "live redecision emit skipped for origin=%s families=%d: "
                "world write mutex unavailable after %.3fs; next cadence will retry.",
                origin,
                len(families),
                emit_lock_timeout_s,
            )
            return 0
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
        if emit_acquired:
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


_C3_STALENESS_CANCEL_CONSUMER = "c3_staleness_cancel_v1"
_c3_staleness_rate_budget = None


def _get_c3_staleness_rate_budget():
    """Lazily-constructed, cycle-persistent VenueRateBudget (W2.3) singleton.

    The token bucket must accumulate across ticks to mean anything, so this is
    memoized at module scope rather than rebuilt per cycle (a fresh bucket every
    5 minutes would always start full and the cancel-priority reserve floor would
    never matter). First real production wiring of this module (W4.2) — see its
    own docstring for why a single shared bucket, not per-class ones.
    """
    global _c3_staleness_rate_budget
    if _c3_staleness_rate_budget is None:
        from src.venue.rate_budget import VenueRateBudget

        _c3_staleness_rate_budget = VenueRateBudget()
    return _c3_staleness_rate_budget


@_scheduler_job("c3_staleness_cancel")
def _c3_staleness_cancel_cycle() -> None:
    """W4.2 C3 staleness cancel path (SCH-W1.2-ORDER-STATE wiring).

    TTL/q-staleness successor to the retired ``maker_rest_escalation``. Two
    independent clocks, composed as two passes inside
    ``run_c3_staleness_cancel_cycle`` (not gated on each other):

    - TTL (``rest_deadline_exceeded``) is the GLOBAL, UNCONDITIONAL GTC deadline
      owner — it scans EVERY open ENTRY rest and runs on EVERY scheduled tick,
      regardless of whether any ``SOURCE_RUN_ARRIVED`` event is pending. This is
      the exact behavior the retired maker_rest_escalation job had; gating it
      behind an event claim would strand expired rests during quiet forecast
      periods (the orphaned-GTC bug this composition must not reintroduce).
    - q-version staleness (``is_stale_pending_cancel``) is SCOPED to the
      ``affected_cities`` of ``SOURCE_RUN_ARRIVED`` events claimed through their
      own dedicated lane (``_C3_STALENESS_CANCEL_CONSUMER``, isolated from the
      main reactor's ``edli_reactor_v1`` queue) — it only runs when such events
      exist, and only against the cities they name.

    Cancels go out through the W2.1 batch cancel gateway (cutover_guard-gated;
    W2.3 rate budget consulted at CANCEL priority), then a reconciled
    ``EDLI_REDECISION_PENDING`` is emitted for every family whose cancel is
    DURABLY confirmed.
    """
    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled"):
        return
    if get_mode() != "live":
        return
    if _defer_for_held_position_monitor("c3_staleness_cancel"):
        return
    from src.events.event_store import EventStore
    from src.state.db import get_world_connection

    now = datetime.now(timezone.utc)

    # Recurring invalid-entry-authority cancel (carried over unchanged from the
    # retired maker_rest_escalation cycle, which piggy-backed this same lane at
    # its 5-min cadence). Unconditional — independent of whether any
    # SOURCE_RUN_ARRIVED events are pending this tick, so this lane's cadence is
    # unaffected by C3 staleness volume. Not itself part of the W4.2 staleness/TTL
    # scope; only relocated so it keeps a recurring caller after the deletion.
    from src.data.polymarket_client import PolymarketClient as _PolymarketClient
    from src.execution.command_recovery import find_invalid_pending_entry_authority_cancels
    from src.execution.venue_cancel_journal import run_persisted_cancels_for_expired_rests
    from src.state.db import get_trade_connection as _get_trade_rw, get_trade_connection_read_only as _get_trade_ro

    authority_ro = _get_trade_ro()
    try:
        invalid_authority_pending = find_invalid_pending_entry_authority_cancels(authority_ro)
    finally:
        authority_ro.close()
    if invalid_authority_pending:
        authority_stats = run_persisted_cancels_for_expired_rests(
            invalid_authority_pending,
            _PolymarketClient(),
            conn_factory=lambda: _get_trade_rw(write_class="live"),
        )
        logger.info(
            "c3_staleness_cancel: invalid_authority_pending=%d %s",
            len(invalid_authority_pending),
            authority_stats,
        )

    claimed_ids: list[str] = []
    affected_cities: set[str] = set()
    try:
        world = get_world_connection()
        try:
            store = EventStore(world, consumer_name=_C3_STALENESS_CANCEL_CONSUMER)
            events = store.fetch_pending_by_event_type(
                event_type="SOURCE_RUN_ARRIVED", decision_time=now.isoformat(), limit=25
            )
            for event in events:
                if not store.claim(event.event_id):
                    continue
                claimed_ids.append(event.event_id)
                try:
                    payload = json.loads(event.payload_json or "{}")
                except Exception:  # noqa: BLE001
                    payload = {}
                affected_cities.update(str(c) for c in payload.get("affected_cities") or [])
            world.commit()
        finally:
            world.close()
    except Exception as _event_lane_exc:  # noqa: BLE001 — FAIL-SOFT: the retired
        # maker_rest_escalation TTL owner never depended on the event lane at
        # all; a fault here (connection, schema, EventStore) must degrade to
        # "no source event claimed this tick," never take down the TTL pass
        # below (that would be an availability regression versus the deleted
        # job this one replaces).
        logger.warning(
            "c3_staleness_cancel: SOURCE_RUN_ARRIVED claim lane failed "
            "(degrading to TTL-only this tick): %r",
            _event_lane_exc,
        )
        claimed_ids = []
        affected_cities = set()

    # UNCONDITIONAL: the TTL pass inside run_c3_staleness_cancel_cycle must run
    # every tick regardless of claimed_ids — an empty claim this tick means "no
    # q-version staleness pass," never "skip the GTC deadline scan." Zero
    # claimed_ids still exercises the TTL pass over every open rest.
    from src.data.polymarket_client import PolymarketClient
    from src.execution.staleness_cancel import run_c3_staleness_cancel_cycle
    from src.state.db import get_forecasts_connection_read_only, get_trade_connection, get_trade_connection_read_only

    trade_ro = get_trade_connection_read_only()
    trade_rw = get_trade_connection(write_class="live")
    forecasts_ro = get_forecasts_connection_read_only()
    try:
        stats = run_c3_staleness_cancel_cycle(
            trade_ro,
            trade_rw,
            forecasts_ro,
            PolymarketClient(),
            affected_cities=frozenset(affected_cities) if affected_cities else None,
            now=now,
            rate_budget=_get_c3_staleness_rate_budget(),
        )
    finally:
        for c in (trade_ro, trade_rw, forecasts_ro):
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    if claimed_ids:
        world2 = get_world_connection()
        try:
            store2 = EventStore(world2, consumer_name=_C3_STALENESS_CANCEL_CONSUMER)
            for event_id in claimed_ids:
                store2.mark_processed(event_id)
            world2.commit()
        finally:
            world2.close()

    logger.info(
        "c3_staleness_cancel: events=%d scanned=%d cancel_set=%d confirmed_families=%d",
        len(claimed_ids),
        stats["scanned"],
        stats["cancel_set_size"],
        len(stats["confirmed_families"]),
    )

    # FAIL-CLOSED on the re-decision emit: any error here must NOT crash the cancel
    # job (the cancels already succeeded; the worst case without the re-decision is
    # the family waits for the round-robin).
    if stats["confirmed_families"] and edli_cfg.get("event_writer_enabled"):
        try:
            emitted = _emit_live_redecision_events_for_families(
                stats["confirmed_families"],
                decision_time=now,
                received_at=now.isoformat(),
                origin="c3_staleness_cancel",
            )
            logger.info(
                "c3_staleness_cancel: re-decision emit families=%d events_emitted=%d",
                len(stats["confirmed_families"]), emitted,
            )
        except Exception as _redecide_exc:  # noqa: BLE001 — fail-closed: never crash the cancel job
            logger.warning(
                "c3_staleness_cancel: re-decision emit failed "
                "(non-fatal; family will wait for the round-robin): %r",
                _redecide_exc,
            )




@_scheduler_job("edli_continuous_redecision_screen")
def _edli_continuous_redecision_screen_cycle() -> None:
    """Scheduler hook -- body owned by src.events.reactor (R4-b4 continuous-
    redecision-screen cluster extraction, 2026-07-08) as
    ``run_edli_continuous_redecision_screen_cycle``. See that function's
    docstring for the P2 cheap-screen + rest-management lane it runs.

    ``_edli_redecision_screen_lock`` is a cross-job scheduling-coordination
    primitive (main.py -- the dispatcher -- owns it; settlement attribution and
    the day0-hourly-refresh cluster also read its ``.locked()`` state), so it
    is injected into the extracted function rather than reached back into.
    ``_edli_redecision_acted_state`` stays reach-back-imported by the extracted
    function itself: it is a plain mutable dict (no lock lifecycle), still
    mutated directly by the command-recovery cluster here in main.py.
    """
    if _defer_for_active_entry_reactor("edli_redecision_screen"):
        return

    from src.events.reactor import run_edli_continuous_redecision_screen_cycle

    run_edli_continuous_redecision_screen_cycle(screen_lock=_edli_redecision_screen_lock)




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




def _edli_emit_lock_timeout_seconds(config: dict) -> float:
    raw = config.get("reactor_emit_lock_timeout_seconds", 0.5)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.5
    return max(0.0, min(value, 5.0))


def _edli_acquire_mutex(mutex: Any, *, timeout: float) -> bool:
    """Acquire a runtime mutex with a bounded wait.

    Some unit tests use tiny fake mutexes whose ``acquire`` method accepts no
    timeout and returns ``None``. Treat that shape as acquired so tests can
    verify call routing without depending on ``threading.Lock`` internals.
    """

    try:
        result = mutex.acquire(timeout=timeout)
    except TypeError:
        mutex.acquire()
        return True
    return True if result is None else bool(result)


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
    restrict_to_families: set[tuple[str, str, str]] | None = None,
    cancelled: Callable[[], bool] | None = None,
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
    _edli_install_sqlite_deadline(
        world_conn,
        deadline_monotonic=deadline_monotonic,
        cancelled=cancelled,
    )
    forecasts_conn = get_forecasts_connection_read_only()
    _edli_install_sqlite_deadline(
        forecasts_conn,
        deadline_monotonic=deadline_monotonic,
        cancelled=cancelled,
    )
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
            restrict_to_families=restrict_to_families,
        )
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            if cancelled is not None and cancelled():
                logger.info(
                    "EDLI forecast-snapshot build preempted by urgent producer wake"
                )
            else:
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




def _redecision_event_with_origin(event: Any, origin: str) -> Any:
    """Return an equivalent immutable redecision event with explicit scheduler origin."""

    try:
        from src.events.opportunity_event import make_opportunity_event
        from src.strategy.live_inference.mode_consistent_ev import (
            POLICY_TAKER_ESCALATED_AFTER_REST,
        )

        payload = json.loads(str(event.payload_json or "{}"))
        if not isinstance(payload, dict):
            return event
        origin_text = str(origin)
        payload["redecision_origin"] = origin_text
        if origin_text in {"terminal_no_fill", "rest_pull"}:
            payload.setdefault("rest_then_cross_policy", POLICY_TAKER_ESCALATED_AFTER_REST)
            payload["rest_then_cross_escalated_after_rest"] = True
            payload["rest_then_cross_escalation_source"] = origin_text
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






def _edli_pending_entity_keys(
    world_conn,
    *,
    event_types: tuple[str, ...] = ("FORECAST_SNAPSHOT_READY",),
    max_rows_per_status: int = 5_000,
    deadline_monotonic: float | None = None,
    cancelled: Callable[[], bool] | None = None,
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
    deadline_installed = deadline_monotonic is not None
    try:
        if deadline_installed:
            _edli_install_sqlite_deadline(
                world_conn,
                deadline_monotonic=deadline_monotonic,
                cancelled=cancelled,
            )
        try:
            row = world_conn.execute("PRAGMA busy_timeout").fetchone()
            saved_busy_timeout_ms = int(row[0]) if row is not None else None
            world_conn.execute("PRAGMA busy_timeout = 250")
        except Exception:  # noqa: BLE001
            saved_busy_timeout_ms = None
        event_type_values = tuple(str(t).strip() for t in event_types if str(t).strip())
        if not event_type_values:
            return set()
        bounded_rows = max(1, min(int(max_rows_per_status or 1), 50_000))
        placeholders = ",".join("?" for _ in event_type_values)
        try:
            rows = world_conn.execute(
                f"""
                WITH active(event_id) AS MATERIALIZED (
                    SELECT event_id
                      FROM (
                            SELECT event_id
                              FROM opportunity_event_processing
                                   INDEXED BY idx_opportunity_event_processing_status
                             WHERE consumer_name = 'edli_reactor_v1'
                               AND processing_status = 'pending'
                             ORDER BY updated_at DESC
                             LIMIT ?
                           )
                    UNION ALL
                    SELECT event_id
                      FROM (
                            SELECT event_id
                              FROM opportunity_event_processing
                                   INDEXED BY idx_opportunity_event_processing_status
                             WHERE consumer_name = 'edli_reactor_v1'
                               AND processing_status = 'processing'
                             ORDER BY updated_at DESC
                             LIMIT ?
                           )
                )
                SELECT DISTINCT e.entity_key
                  FROM active p
                  CROSS JOIN opportunity_events e
                 WHERE e.event_id = p.event_id
                   AND e.event_type IN ({placeholders})
            """,
                (bounded_rows, bounded_rows, *event_type_values),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                logger.warning(
                    "EDLI pending-entity scan deadline exhausted; using an empty "
                    "skip set so the bounded event builder can continue"
                )
                return set()
            return set()
        except Exception:  # noqa: BLE001 — fail-open: no skip set (cap still bounds)
            return set()
        return {str(r[0]) for r in rows}
    finally:
        if deadline_installed:
            _edli_clear_sqlite_progress_handler(world_conn)
        if saved_busy_timeout_ms is not None:
            try:
                world_conn.execute("PRAGMA busy_timeout = %d" % saved_busy_timeout_ms)
            except Exception:  # noqa: BLE001 — restore best-effort; next get_world_connection reapplies
                pass




_EDLI_LAST_PRUNE_MONOTONIC: float | None = None














def _edli_install_sqlite_deadline(
    conn,
    *,
    deadline_monotonic: float | None,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Interrupt SQLite when its budget expires or higher-value work arrives."""

    if deadline_monotonic is None and cancelled is None:
        return

    def _deadline_progress() -> int:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return 1
        if cancelled is not None:
            try:
                return 1 if cancelled() else 0
            except Exception:  # noqa: BLE001 - cancellation is an optimization.
                return 0
        return 0

    conn.set_progress_handler(_deadline_progress, 1_000)


def _edli_clear_sqlite_progress_handler(conn) -> None:
    try:
        conn.set_progress_handler(None, 0)
    except Exception:  # noqa: BLE001
        pass


























@_scheduler_job("edli_day0_hourly_refresh")
def _edli_day0_hourly_refresh_cycle() -> None:
    """Scheduler hook — body owned by src.events.reactor (R4-b2 day0-hourly-
    refresh cluster extraction, 2026-07-08) as ``run_edli_day0_hourly_refresh_cycle``.
    See that function's docstring for the vector-refresh lane it runs.

    The reactor lock, redecision lock, and held-monitor Event are dispatcher-owned
    scheduling primitives. This hook atomically admits the background refresh on
    the shared reactor lane and injects only the resulting active/inactive state.
    """
    from src.events.reactor import run_edli_day0_hourly_refresh_cycle

    trading_lane_active = (
        _held_position_monitor_active.is_set()
        or _edli_redecision_screen_lock.locked()
    )
    if trading_lane_active or not _edli_reactor_active_lock.acquire(blocking=False):
        run_edli_day0_hourly_refresh_cycle(trading_lane_active=True)
        return
    try:
        # Recheck after admission so an exit-monitor priority claim cannot race
        # the first check while this background job acquires the shared lane.
        run_edli_day0_hourly_refresh_cycle(
            trading_lane_active=(
                _held_position_monitor_active.is_set()
                or _edli_redecision_screen_lock.locked()
            ),
        )
    finally:
        _edli_reactor_active_lock.release()








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






@_scheduler_job("edli_presubmit_jit_keepalive")
def _edli_pre_submit_jit_keepalive_tick() -> None:
    """Scheduler hook -- body owned by src.events.reactor (R4-b4 pre-submit-JIT
    cluster extraction, 2026-07-08) as ``run_edli_presubmit_jit_keepalive_cycle``.
    The warm CLOB client singleton (construct/reset) moved with it: R4-b3 kept
    it in main.py because it was shared with this pinger; now that the pinger
    has moved too, every consumer (this tick + reactor's book-quote provider)
    lives in the same module, so the singleton has no more cross-module reader.
    """
    from src.events.reactor import run_edli_presubmit_jit_keepalive_cycle

    run_edli_presubmit_jit_keepalive_cycle()






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
























def _row_get(row, key: str):
    try:
        return row[key]
    except Exception:
        try:
            return getattr(row, key)
        except Exception:
            return None




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


# FIX 2c (2026-06-20): monitor-cadence watchdog. exit_monitor runs on a 2-min
# interval (see scheduler.add_job(..., minutes=2, id="exit_monitor")) and is the
# sole writer of MONITOR_REFRESHED. The live book observed whole-book silences of
# 8.8h and 11.8h (2026-06-18/19) during which belief AND the live bid collapsed
# unobserved, killing the only realized reversal exit. The multi-hour cause is a
# daemon/APScheduler process gap — that supervision is OPERATOR INFRA, out of
# code. What code CAN do is flag the gap on the first cycle after recovery: if
# the newest MONITOR_REFRESHED is older than ~2× the interval, the cadence broke.
# This is detection only; it does not (and must not) re-drive the schedule.
# R4-b (2026-07-08): _EXIT_MONITOR_INTERVAL_SECONDS, _MONITOR_CADENCE_GAP_FACTOR,
# _check_monitor_cadence_watchdog moved to src.execution.exit_lifecycle
# (single caller was _exit_monitor_cycle, also moved there).


@_scheduler_job("exit_monitor")
def _exit_monitor_cycle(
    *,
    target_families: frozenset[tuple[str, str, str]] | None = None,
    urgent_day0: bool = False,
) -> bool:
    """Scheduler hook — body owned by src.execution.exit_lifecycle (R4-b
    extraction, 2026-07-08) as ``run_exit_monitor_cycle``. See that function's
    docstring for the held-position monitoring / exit-submit lane it runs.

    The held-position-monitor Event and its completion callback are cross-job
    scheduling coordination state (5 other EDLI jobs defer while this one
    runs via ``_defer_for_held_position_monitor``), so they stay owned here
    and are injected into the extracted function.
    """
    from src.execution.exit_lifecycle import run_exit_monitor_cycle

    if not urgent_day0 and _day0_urgent_wake_pending.is_set():
        logger.info("periodic exit_monitor yielded to pending Day0 urgent wake")
        return True
    if _held_position_monitor_active.is_set():
        logger.warning("exit_monitor skipped: previous monitor cycle is still running")
        return False

    # Claim exit priority before waiting. New reactor ticks defer on this Event;
    # the current reactor finishes without a competing SQLite traversal.
    _held_position_monitor_active.set()
    reactor_idle = _edli_reactor_active_lock.acquire(
        timeout=_EXIT_MONITOR_REACTOR_HANDOFF_SECONDS
    )
    if not reactor_idle:
        logger.warning(
            "exit_monitor deferred: active EDLI reactor did not finish within %.1fs",
            _EXIT_MONITOR_REACTOR_HANDOFF_SECONDS,
        )
        _held_position_monitor_active.clear()
        return False
    _edli_reactor_active_lock.release()
    if not urgent_day0 and _day0_urgent_wake_pending.is_set():
        logger.info(
            "periodic exit_monitor yielded after reactor handoff to pending Day0 urgent wake"
        )
        _mark_held_position_monitor_complete()
        return True
    try:
        monitor_succeeded = run_exit_monitor_cycle(
            held_position_monitor_active=_held_position_monitor_active,
            mark_held_position_monitor_complete=_mark_held_position_monitor_complete,
            monitor_claimed=True,
            target_families=target_families,
            should_preempt_for_urgent_day0=(
                None if urgent_day0 else _day0_urgent_wake_pending.is_set
            ),
        )
        if monitor_succeeded is not True:
            raise RuntimeError("EXIT_MONITOR_CYCLE_INCOMPLETE")
        return True
    finally:
        if _held_position_monitor_active.is_set():
            _mark_held_position_monitor_complete()


def main():
    _start = time.monotonic()  # F86: process start time for SIGTERM elapsed log
    boot_at = datetime.now(timezone.utc)
    global BlockingScheduler
    if BlockingScheduler is None:
        from apscheduler.schedulers.blocking import BlockingScheduler as _BlockingScheduler

        BlockingScheduler = _BlockingScheduler
    mode = get_mode()

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

    logger.info("Zeus starting in %s mode", mode)

    # Capture immutable process identity early. Git is preferred; a source
    # fingerprint keeps identity observable when repository metadata is absent.
    _boot = _capture_boot_state()
    _BOOT_STATE.update(_boot)
    if _boot.get("sha"):
        logger.info("deployment_freshness: boot_sha=%s", _boot["sha"][:8])
        os.environ["ZEUS_PROCESS_BOOT_SHA"] = str(_boot["sha"])
    # Persist the identity once so receipts and deployment observability can name
    # the exact running process without treating later worktree drift as authority.
    _write_loaded_sha_state(_boot.get("sha"))

    _startup_required_sidecar_head_check(boot_sha=_boot.get("sha"))

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

    # T5 MIGRATION (docs/rebuild/quarantine_excision_2026-07-11.md, deliverable
    # B): fail-closed refusal if the three canonical DBs carry a MIXED
    # schema_epoch (a partially-applied scripts/migrations/
    # 2026_07_quarantine_phase_retirement.py run or a crash mid-migration).
    # Unconditional — never gated behind ZEUS_BOOT_REGISTRY_ASSERT_ENABLED
    # (that env var only exists for intentional table-set registry drift
    # windows, not for booting past a half-migrated DB set).
    from src.state.db import (
        ZEUS_FORECASTS_DB_PATH,
        ZEUS_WORLD_DB_PATH,
        _zeus_trade_db_path,
        assert_schema_epoch_not_mixed,
        read_schema_epoch,
    )

    def _read_epoch_ro(path):
        if not path.exists():
            return None
        _c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return read_schema_epoch(_c)
        finally:
            _c.close()

    assert_schema_epoch_not_mixed(
        world_epoch=_read_epoch_ro(ZEUS_WORLD_DB_PATH),
        forecasts_epoch=_read_epoch_ro(ZEUS_FORECASTS_DB_PATH),
        trade_epoch=_read_epoch_ro(_zeus_trade_db_path()),
    )

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

    # Retire only obsolete deployment-freshness pauses. Must run after control
    # state hydration so operator/risk/source pauses remain untouched.
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
            "observability": _APThreadPoolExecutor(1),
        }
    except ModuleNotFoundError:
        if BlockingScheduler is None or getattr(BlockingScheduler, "__module__", "").startswith("apscheduler"):
            raise

    scheduler = BlockingScheduler(**scheduler_kwargs)
    try:
        from apscheduler.events import EVENT_JOB_MAX_INSTANCES

        scheduler.add_listener(_scheduler_max_instance_skip_listener, EVENT_JOB_MAX_INSTANCES)
    except ModuleNotFoundError:
        if BlockingScheduler is None or getattr(BlockingScheduler, "__module__", "").startswith("apscheduler"):
            raise

    # max_instances=1: prevent concurrent execution if previous cycle still running
    edli_cfg = _settings_section("edli", {})
    live_execution_mode = _assert_live_execution_mode_contract(edli_cfg)
    _assert_edli_stage_readiness(edli_cfg)
    _edli_boot_command_recovery_once(boot_at=boot_at)
    _edli_boot_invalid_pending_entry_authority_cancel_once()
    # SINGLE TRUTH (bias-maze strip 2026-06-17): the EMOS-CI license boot guard is REMOVED
    # (the override it guarded is gone). The legacy bias/Platt calibration-coverage contract
    # is now an unconditional logged no-op (not applicable under single-truth).
    _assert_calibration_coverage_contract(edli_cfg)
    if live_execution_mode in EDLI_EVENT_DRIVEN_MODES and edli_cfg.get("enabled"):
        # The interval remains the durable recovery/backlog scan. Forecast materialization
        # also publishes a best-effort cross-process wake after its DB commit; the listener
        # above invokes this same canonical reactor immediately for that hint. A lost or
        # malformed hint therefore delays work only until this scan and never becomes truth.
        _edli_reactor_scan_interval_seconds = int(
            edli_cfg.get("reactor_scan_interval_seconds", 60) or 60
        )
        scheduler.add_job(
            _edli_event_reactor_cycle,
            "interval",
            seconds=_edli_reactor_scan_interval_seconds,
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
            seconds=int(os.environ.get("ZEUS_DAY0_HOURLY_REFRESH_JOB_SECONDS", "45")),
            id="edli_day0_hourly_refresh",
            # Keep the 45s producer off the exit monitor's 120s phase. Their
            # periods share gcd=15s; the former +35s offset collided every 6m.
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 36.0),
            max_instances=1,
            coalesce=True,
        )
        # W4.2 C3 staleness cancel path (SCH-W1.2-ORDER-STATE wiring): the TTL/
        # q-staleness successor to the retired maker_rest_escalation. Cancels GTC
        # maker entry rests whose q_version has gone stale (SOURCE_RUN_ARRIVED) OR
        # that have aged past the deadline (rest_deadline_exceeded — the same
        # unconditional per-order backstop maker_rest_escalation used to own,
        # 20min). 5-min cadence is well inside the deadline's 60-min derivation
        # slack (taker_immediate_event_end_floor relation in the time-semantics
        # registry). Also carries the recurring invalid-entry-authority cancel
        # lane forward unchanged.
        scheduler.add_job(
            _c3_staleness_cancel_cycle,
            "interval",
            minutes=5,
            id="c3_staleness_cancel",
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
            seconds=_EDLI_COMMAND_RECOVERY_INTERVAL_SECONDS,
            id="edli_command_recovery",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 60.0),
            max_instances=1,
            coalesce=True,
        )
        # Chain-mirror reconcile (operator directive 2026-07-04, design doc
        # docs/rebuild/chain_mirror_state_model_2026-07-04.md): the standing
        # invariant that keeps position_current mirroring on-chain state so
        # quarantined/stale rows do not accumulate forever. Read-only venue
        # call (data-api GET /positions) + local DB read/repair; no order
        # construction, no signing, no redeem submission. 10-minute cadence:
        # frequent enough that a settlement/redeem sweep is absorbed within
        # one cycle, sparse enough to never compete with the entry/exit
        # money-path jobs for the trade-DB write lock.
        scheduler.add_job(
            _chain_mirror_reconcile_cycle,
            "interval",
            minutes=10,
            id="chain_mirror_reconcile",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 90.0),
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
    scheduler.add_job(
        _live_health_composite_cycle,
        "interval",
        seconds=60,
        id="live_health_composite",
        max_instances=1,
        coalesce=True,
        executor="observability",
    )
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

    # Loaded-code/worktree observability; never a submit or process-liveness gate.
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
