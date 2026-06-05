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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
OPENING_HUNT_FIRST_DELAY_SECONDS = 30.0
LIVE_EXECUTION_MODES = {
    "legacy_cron",
    "edli_shadow_no_submit",
    "edli_submit_disabled_bridge",
    "edli_live_canary",
    "edli_live",
    "disabled",
}
EDLI_EVENT_DRIVEN_MODES = {
    "edli_shadow_no_submit",
    "edli_submit_disabled_bridge",
    "edli_live_canary",
    "edli_live",
}
REACTOR_MODE_BY_LIVE_STAGE = {
    "legacy_cron": "disabled",
    "disabled": "disabled",
    "edli_shadow_no_submit": "live_no_submit",
    "edli_submit_disabled_bridge": "submit_disabled_live_bridge",
    "edli_live_canary": "live",
    "edli_live": "live",
}
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
REQUIRED_STAGE_FILES_BY_MODE = {
    "edli_submit_disabled_bridge": (
        "edli_stage_loaded_sha_file",
        "edli_stage_source_health_json",
        "edli_stage_status_json",
    ),
    "edli_live_canary": (
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


def _live_execution_mode(edli_cfg: dict) -> str:
    mode = str(edli_cfg.get("live_execution_mode") or "legacy_cron")
    if mode not in LIVE_EXECUTION_MODES:
        raise ValueError(f"UNSUPPORTED_LIVE_EXECUTION_MODE:{mode}")
    return mode


def _harvester_should_register(live_execution_mode: str) -> bool:
    """Whether the settlement P&L + redeem-intent resolver (_harvester_cycle) is
    scheduled for this live-execution mode.

    守護 blocker (2026-06-03): the harvester was gated to ``legacy_cron`` ONLY, so
    in EDLI event-driven modes (edli_shadow_no_submit, edli_submit_disabled_bridge,
    edli_live_canary, edli_live) a FILLED position that rode to market settlement
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
      world_db_schema    — PRAGMA user_version + assert_schema_current
      forecasts_db_schema — PRAGMA user_version + assert_schema_current_forecasts
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
            row = conn.execute("PRAGMA user_version").fetchone()
            results.append(("world_db_schema", True, f"user_version={row[0] if row else '?'} — OK"))
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
            row = conn.execute("PRAGMA user_version").fetchone()
            results.append(("forecasts_db_schema", True, f"user_version={row[0] if row else '?'} — OK"))
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
    # F1 rename (PR-2 B): edli_live_scaleout_enabled -> edli_live_operator_authorized.
    # The flag's real semantic is the operator ARM kill-switch for edli_live, not a
    # scale-out knob. Renamed so the name matches the control it actually performs.
    if not bool(edli_cfg.get("edli_live_operator_authorized", False)):
        raise RuntimeError("EDLI_LIVE_REQUIRES_EDLI_LIVE_OPERATOR_AUTHORIZED")
    if not bool(edli_cfg.get("edli_live_promotion_artifact_required", True)):
        raise RuntimeError("EDLI_LIVE_REQUIRES_PROMOTION_ARTIFACT_REQUIRED")

    artifact_path = str(edli_cfg.get("edli_live_promotion_artifact_path") or "").strip()
    if not artifact_path:
        raise RuntimeError("EDLI_LIVE_REQUIRES_PROMOTION_ARTIFACT")
    try:
        artifact = json.loads(Path(artifact_path).read_text())
    except FileNotFoundError as exc:
        raise RuntimeError("EDLI_LIVE_REQUIRES_PROMOTION_ARTIFACT") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("EDLI_LIVE_PROMOTION_ARTIFACT_INVALID_JSON") from exc

    min_canary_count = int(edli_cfg.get("edli_live_min_canary_count", 1))
    max_unresolved_unknowns = int(edli_cfg.get("edli_live_max_unresolved_unknowns", 0))
    min_realized_edge_bps = float(edli_cfg.get("edli_live_min_realized_edge_bps", 0.0))

    from src.events.live_profit_audit import verify_edli_live_promotion_artifact

    conn = get_world_connection_read_only()
    try:
        verified = verify_edli_live_promotion_artifact(
            conn,
            artifact,
            min_canary_count=min_canary_count,
            max_unresolved_unknowns=max_unresolved_unknowns,
            min_realized_edge_bps=min_realized_edge_bps,
        )
    finally:
        conn.close()
    if not verified.ok:
        raise RuntimeError(verified.reason)


def _assert_edli_arm_gate_artifact(edli_cfg: dict) -> None:
    """PR-2 (A) / F1 Option C: bind the live/canary ARM to settlement-grounded evidence.

    Whenever the daemon is about to arm (real_order_submit_enabled — true for BOTH
    edli_live_canary AND edli_live), it MUST find a state/edli_arm_gate_artifact.json
    proving — on THIS commit (commit_sha == booted HEAD) — a positive
    capital-weighted after-cost settlement EV with coverage licensed. The artifact
    is produced by scripts/measure_arm_gate_settlement.py (PR-1). Here we ENFORCE it.

    ANTIBODY: flipping ``real_order_submit_enabled=true`` without that artifact is now a
    BOOT FAILURE (RuntimeError ``EDLI_LIVE_PROMOTION_ARM_GATE_*``), not a silent runtime
    path. The whole category "armed without proven edge" becomes unconstructable at boot.

    Fail-closed: ``edli_arm_gate_artifact_required`` defaults to True; a missing flag
    still requires the artifact. Only the explicit literal False — set by an operator
    who is knowingly de-binding — relaxes it, and even then the existing
    promotion-artifact gate and the live-cap hard ceiling remain in force.
    """
    if not bool(edli_cfg.get("edli_arm_gate_artifact_required", True)):
        return

    artifact_path = str(edli_cfg.get("edli_arm_gate_artifact_path") or "").strip()
    if not artifact_path:
        raise RuntimeError("EDLI_LIVE_PROMOTION_ARM_GATE_ARTIFACT_PATH_MISSING")
    try:
        artifact = json.loads(Path(artifact_path).read_text())
    except FileNotFoundError as exc:
        raise RuntimeError("EDLI_LIVE_PROMOTION_ARM_GATE_ARTIFACT_MISSING") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("EDLI_LIVE_PROMOTION_ARM_GATE_ARTIFACT_INVALID_JSON") from exc

    head_sha = str(_capture_boot_state().get("sha") or "").strip()

    from src.events.live_profit_audit import verify_edli_arm_gate_artifact

    verified = verify_edli_arm_gate_artifact(artifact, head_sha=head_sha)
    if not verified.ok:
        raise RuntimeError(verified.reason)


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
    canary_artifact_path: str | None = None,
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
        if stage == "edli_live_canary":
            from scripts.check_edli_live_canary_gate import (
                CANARY_PROFIT_PASS,
                CANARY_SAFETY_PASS,
                FAIL,
                WAITING_FOR_QUALIFYING_EVENT,
                evaluate_canary_artifact,
                load_canary_artifact,
            )

            artifact = load_canary_artifact(canary_artifact_path) if canary_artifact_path else None
            canary = evaluate_canary_artifact(
                artifact,
                max_quote_age_ms=int(_settings_section("edli_v1", {}).get("pre_submit_max_quote_age_ms", 1000)),
                conn=conn if artifact is not None else None,
            )
            if canary.status == FAIL:
                reasons.extend(f"EDLI_STAGE_CANARY_GATE:{reason}" for reason in canary.reasons)
            elif canary.status == WAITING_FOR_QUALIFYING_EVENT:
                if not reasons:
                    return EdliStageReadiness(
                        stage=stage,
                        status=EDLI_STAGE_WAITING,
                        live_entries_allowed=True,
                        submit_allowed=True,
                        scaleout_allowed=False,
                        reasons=tuple(canary.reasons),
                    )
            elif canary.status not in {CANARY_SAFETY_PASS, CANARY_PROFIT_PASS}:
                reasons.append(f"EDLI_STAGE_CANARY_GATE_UNSUPPORTED:{canary.status}")
            elif not reasons:
                return EdliStageReadiness(
                    stage=stage,
                    status=EDLI_STAGE_PASS,
                    live_entries_allowed=True,
                    submit_allowed=True,
                    scaleout_allowed=False,
                    reasons=(canary.status,),
                )
    finally:
        conn.close()

    if reasons:
        return EdliStageReadiness(stage=stage, status=EDLI_STAGE_FAIL, live_entries_allowed=False, reasons=tuple(reasons))
    if stage == "edli_submit_disabled_bridge":
        return EdliStageReadiness(stage=stage, status=EDLI_STAGE_PASS, live_entries_allowed=False)
    if stage == "edli_shadow_no_submit":
        return EdliStageReadiness(stage=stage, status=EDLI_STAGE_PASS, live_entries_allowed=False)
    if stage == "edli_live_canary":
        return EdliStageReadiness(
            stage=stage,
            status=EDLI_STAGE_PASS,
            live_entries_allowed=True,
            submit_allowed=True,
            scaleout_allowed=False,
        )
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
        canary_artifact_path=str(edli_cfg.get("edli_live_canary_artifact_path") or ""),
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
    if stage == "edli_live_canary":
        risk_reasons = [reason for reason in report.reasons if reason.startswith(EDLI_STAGE_RISK_REASON_PREFIXES)]
        if report.status not in {EDLI_STAGE_PASS, EDLI_STAGE_WAITING} or risk_reasons:
            raise RuntimeError("EDLI_LIVE_CANARY_READINESS_FAIL:" + ",".join(report.reasons or (report.status,)))
        if report.submit_allowed is not True:
            raise RuntimeError("EDLI_LIVE_CANARY_SUBMIT_NOT_ALLOWED")
        return report
    if stage == "edli_live" and (report.status != EDLI_STAGE_PASS or report.scaleout_allowed is not True):
        raise RuntimeError("EDLI_LIVE_SCALEOUT_READINESS_FAIL:" + ",".join(report.reasons or (report.status,)))
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
    if scope != "forecast_only":
        raise RuntimeError(f"UNSUPPORTED_EDLI_LIVE_SCOPE:{scope}")
    if bool(edli_cfg.get("day0_extreme_trigger_enabled", False)) or bool(
        edli_cfg.get("day0_hard_fact_live_enabled", False)
    ):
        raise RuntimeError("DAY0_OUT_OF_SCOPE_FOR_PR332")


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
    if mode in {"edli_live_canary", "edli_live"}:
        _require_edli_flags(
            edli_cfg,
            mode,
            (
                "market_channel_ingestor_enabled",
                "edli_user_channel_reconcile_enabled",
                "real_order_submit_enabled",
                "live_canary_enabled",
            ),
        )
    if mode == "edli_live":
        _assert_edli_live_promotion_artifact(edli_cfg)
    if mode in {"edli_live_canary", "edli_live"}:
        # PR-2 (A) / F1 Option C: ANY armed mode (canary OR live —
        # real_order_submit_enabled is required for both) must ALSO carry the
        # settlement-grounded ARM evidence artifact bound to THIS commit. Missing
        # / SHA-mismatch / ev<=0 / not-coverage-licensed -> boot RuntimeError.
        # Ordered AFTER the (edli_live-only) promotion-artifact gate so the
        # promotion gate's specific reason still surfaces first for edli_live.
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
            try:
                creds = resolve_polymarket_credentials()
            except RuntimeError as exc:
                raise RuntimeError(
                    f"redeem_submitter: credentials unavailable (fail-closed): {exc}"
                ) from exc
            q1_egress_evidence = _resolve_q1_egress_evidence_path(
                default=DEFAULT_Q1_EGRESS_EVIDENCE,
                env_name=Q1_EGRESS_EVIDENCE_ENV,
            )
            adapter = PolymarketV2Adapter(
                host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
                funder_address=creds["funder_address"],
                signer_key=creds["private_key"],
                chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
                signature_type=_resolve_clob_v2_signature_type(),
                polygon_rpc_url=os.environ.get(
                    "POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL
                ),
                api_creds=creds.get("api_creds"),
                q1_egress_evidence_path=q1_egress_evidence,
            )
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

    with acquire_lock("wrap_intent_creator") as acquired:
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

    with acquire_lock("wrap_submitter") as acquired:
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

    with acquire_lock("wrap_reconciler") as acquired:
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
_venue_background_maintenance_lock = threading.Lock()
_last_venue_background_maintenance_attempt_at = None
VENUE_BACKGROUND_MAINTENANCE_SECONDS = 30.0
_collateral_background_refresh_lock = threading.Lock()
_last_collateral_heartbeat_refresh_attempt_at = None
COLLATERAL_HEARTBEAT_REFRESH_SECONDS = 30.0


def _venue_heartbeat_mode() -> str:
    return os.environ.get("ZEUS_VENUE_HEARTBEAT_MODE", "internal").strip().lower()


def _external_venue_heartbeat_enabled() -> bool:
    return _venue_heartbeat_mode() == "external"


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
    if _cycle_lock.locked():
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
    if _cycle_lock.locked():
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

    if _cycle_lock.locked():
        return {"status": "deferred_cycle_running"}
    active_adapter = adapter or _venue_heartbeat_adapter
    if active_adapter is None:
        return {"status": "adapter_unavailable"}
    return {
        "status": "ok",
        "ws_gap_reconcile": _run_ws_gap_reconcile_if_required(active_adapter),
        "reconcile_findings_refresh": _refresh_reconcile_findings_if_required(active_adapter),
        "collateral_refreshed": _refresh_global_collateral_snapshot_if_due(active_adapter),
    }


def _start_collateral_background_refresh_async(adapter=None) -> str:
    """Refresh collateral on an independent lane from slower venue maintenance."""

    if _cycle_lock.locked():
        return "deferred_cycle_running"
    active_adapter = adapter or _venue_heartbeat_adapter
    if active_adapter is None:
        return "adapter_unavailable"
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
    if _cycle_lock.locked():
        return "deferred_cycle_running"
    active_adapter = adapter or _venue_heartbeat_adapter
    if active_adapter is None:
        return "adapter_unavailable"
    now = datetime.now(timezone.utc)
    if (
        _last_venue_background_maintenance_attempt_at is not None
        and (now - _last_venue_background_maintenance_attempt_at).total_seconds()
        < VENUE_BACKGROUND_MAINTENANCE_SECONDS
    ):
        return "throttled"
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
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
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
    condition IDs plus L2 API credentials. If enabled but misconfigured, the
    WS guard records an auth/config gap so new submits fail closed.

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

    # Boot-time transient failures from create_or_derive_api_key() (e.g., Polymarket
    # /auth/api-key returning 400) used to latch AUTH_FAILED forever because the
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
        # fresh create_or_derive_api_key() rather than reusing a cached client
        # whose creds were None from a prior failed boot
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
                    "(create_or_derive_api_key() failed; likely transient /auth/api-key error)"
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
        if _cycle_lock.locked():
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


def _pending_family_rows_for_refresh(world_conn, *, consumer_name: str):
    return world_conn.execute(
        """
        SELECT
            json_extract(e.payload_json, '$.city')        AS city,
            json_extract(e.payload_json, '$.target_date') AS target_date,
            json_extract(e.payload_json, '$.metric')      AS metric
        FROM opportunity_event_processing p INDEXED BY idx_opportunity_event_processing_status
        JOIN opportunity_events e ON e.event_id = p.event_id
        WHERE p.consumer_name = ? AND p.processing_status = 'pending'
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
        (consumer_name,),
    ).fetchall()


def _refresh_pending_family_snapshots(
    world_conn,
    forecasts_conn,
    *,
    consumer_name: str = "edli_reactor_v1",
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
        refresh_executable_market_substrate_snapshots,
    )
    from src.data.polymarket_client import PolymarketClient
    from src.engine.event_reactor_adapter import _event_family_market_topology_rows
    from src.state.db import get_trade_connection

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    # Step 1: Collect distinct (city, target_date, metric) for pending events.
    try:
        pending_rows = _pending_family_rows_for_refresh(
            world_conn, consumer_name=consumer_name
        )
    except Exception as exc:
        logger.warning("refresh_pending_family_snapshots: pending-event query failed: %s", exc)
        return {"status": "error", "reason": str(exc)}

    families: list[tuple[str, str, str]] = []
    for row in pending_rows:
        city = str(row[0] or "").strip()
        target_date = str(row[1] or "").strip()
        metric = str(row[2] or "").strip()
        if city and target_date and metric:
            families.append((city, target_date, metric))

    if not families:
        return {"status": "no_pending_families"}

    # Staleness pre-filter REMOVED (STEP 4, consolidated timeliness fix): the
    # local lexicographic _drop_stale_families is now redundant. Strictly-past
    # (already-settled) targets can no longer become pending events — the
    # emission floor (STEP 2, forecast_snapshot_ready.scan_committed_snapshots)
    # never emits them and the EventStore claim floor (STEP 3, fetch_pending)
    # never returns them — so no stale family reaches this refresh path. The
    # pending-event query above is the single source of families; it is sourced
    # from the same already-timeliness-filtered opportunity_events.

    # A2 throughput (2026-05-31): cap per-cycle capture so a cycle COMPLETES fast.
    # Each family's capture makes serial per-token order-book fetches (uncacheable,
    # ~1s each); refreshing ALL pending families serially made cycles 10-30 min →
    # process_pending never ran → 0 receipts. `families` is priority-ordered (query
    # ORDER BY priority DESC, available_at ASC), so the highest-priority families are
    # captured first; the remainder are picked up on subsequent cycles. The reactor's
    # own proof_limit still bounds decisions; this bounds the venue-I/O per cycle.
    # 2026-06-04: with the coverage-fairness emit now covering all ~49 cities, this cap is the
    # second throttle on the simultaneously-tradeable set (each family = 1 serial JIT /book
    # fetch). 50 overran the 60s reactor cycle ("max running instances reached"); 8 was the old
    # 3-city-era value. 16 ≈ 16s of fetches inside a 60s cycle — a safe ~2x of the fresh universe
    # without overrun. The proper fix (decouple market-identity universe sizing from the 30s
    # price-freshness TTL — size off identity, enforce freshness only at submit) is the follow-up.
    # 2026-06-05: prioritize newest target_date before applying the cap. Strictly
    # stale pending rows can still exist in the processing ledger after Gamma no
    # longer returns the market, and letting those rows spend the cap starves fresh
    # family snapshots.
    _FAMILY_REFRESH_CAP = 16
    if len(families) > _FAMILY_REFRESH_CAP:
        families = families[:_FAMILY_REFRESH_CAP]

    # Step 2: Cache-skip: for each family check whether ALL known condition_ids
    #         (from market_events topology) already have fresh snapshots.
    #         Families with ANY stale/missing bin still proceed to Gamma fetch.
    fresh_skipped = 0
    no_topology = 0
    families_needing_refresh: list[tuple[str, str, str]] = []

    write_conn = get_trade_connection(write_class="live")
    try:
        for city, target_date, metric in families:
            payload = {"city": city, "target_date": target_date, "metric": metric}
            topology_rows = _event_family_market_topology_rows(forecasts_conn, payload)
            if not topology_rows:
                no_topology += 1
                logger.debug(
                    "refresh_pending_family_snapshots: no market topology for %s/%s/%s "
                    "(no Polymarket market for this family — event will be rejected at gate)",
                    city, target_date, metric,
                )
                # Still include: Gamma may discover bins not yet in topology.
                families_needing_refresh.append((city, target_date, metric))
                continue

            any_stale = False
            for trow in topology_rows:
                cid = str(trow.get("condition_id") or "").strip()
                if not cid:
                    continue
                fresh = write_conn.execute(
                    """
                    SELECT 1 FROM executable_market_snapshots
                    WHERE condition_id = ? AND freshness_deadline >= ?
                    LIMIT 1
                    """,
                    (cid, now_iso),
                ).fetchone()
                if not fresh:
                    any_stale = True
                    break

            if any_stale:
                families_needing_refresh.append((city, target_date, metric))
            else:
                fresh_skipped += 1

        if not families_needing_refresh:
            logger.info(
                "refresh_pending_family_snapshots: all families fresh, skipped. "
                "families=%d fresh_skipped=%d no_topology=%d",
                len(families), fresh_skipped, no_topology,
            )
            return {
                "status": "all_fresh",
                "families_checked": len(families),
                "fresh_skipped": fresh_skipped,
                "no_topology": no_topology,
            }

        # Step 3: Targeted Gamma slug fetch — one request per pending family.
        #         Build the exact slug for each (city, date, metric) and fetch
        #         directly.  This is maximally bounded: N pending families = N
        #         Gamma calls (vs the background slug-pattern scanner which
        #         enumerates all 14 cities × all dates and is budget-capped).
        #         Uses the City's slug_names[0] for the slug fragment.
        try:
            from datetime import date as _date_cls
            from src.config import cities_by_name as _cities_by_name
            from src.data.market_scanner import (
                _gamma_get,
                _parse_and_persist_weather_events,
            )
            _cbm = _cities_by_name

            def _date_to_slug_fragment(date_str: str) -> str:
                d = _date_cls.fromisoformat(date_str)
                return d.strftime("%B-%-d-%Y").lower()

            raw_events_seen: set = set()
            raw_events_collected: list[dict] = []
            # Time-box the per-family Gamma fetch (2026-06-04 reactor-overrun antibody):
            # each _gamma_get has up to a 10s timeout; 16 families x slow Gamma can blow
            # past the 60s reactor interval ("max running instances reached"), so the
            # cycle never reaches FSR-emit + process_pending -> 0 receipts. Bound the
            # refresh phase to a deadline that ALWAYS leaves budget for the downstream
            # emit+process; uncaptured families are picked up next cycle (priority-ordered).
            _refresh_budget_s = max(5.0, float(os.environ.get("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "25.0")))
            _refresh_deadline = time.monotonic() + _refresh_budget_s
            _refreshed_n = 0
            for fam_city, fam_date, fam_metric in families_needing_refresh:
                if time.monotonic() > _refresh_deadline:
                    logger.info(
                        "refresh_pending_family_snapshots: time-box %.0fs hit after %d/%d "
                        "families; deferring rest to next cycle (leaves budget for emit+process)",
                        _refresh_budget_s, _refreshed_n, len(families_needing_refresh),
                    )
                    break
                _refreshed_n += 1
                city_obj = _cbm.get(fam_city)
                if city_obj is None:
                    logger.warning(
                        "refresh_pending_family_snapshots: city %r not in config, skipping",
                        fam_city,
                    )
                    continue
                slug_fragment = city_obj.slug_names[0] if city_obj.slug_names else fam_city.lower().replace(" ", "-")
                try:
                    slug_date = _date_to_slug_fragment(fam_date)
                except (ValueError, TypeError):
                    logger.warning(
                        "refresh_pending_family_snapshots: invalid date %r for %s, skipping",
                        fam_date, fam_city,
                    )
                    continue
                prefix = "lowest" if fam_metric == "low" else "highest"
                slug = f"{prefix}-temperature-in-{slug_fragment}-on-{slug_date}"
                _gamma_timeout = max(
                    1.0,
                    float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "10.0")),
                )
                try:
                    resp = _gamma_get("/events", params={"slug": slug}, timeout=_gamma_timeout)
                    if resp.status_code != 200:
                        logger.debug(
                            "refresh_pending_family_snapshots: Gamma %s → HTTP %s",
                            slug, resp.status_code,
                        )
                        continue
                    batch = resp.json()
                    if not isinstance(batch, list):
                        batch = [batch] if isinstance(batch, dict) and batch else []
                    for event in batch:
                        if not isinstance(event, dict):
                            continue
                        event_id = event.get("id") or event.get("slug")
                        if event_id and event_id not in raw_events_seen:
                            raw_events_seen.add(event_id)
                            raw_events_collected.append(event)
                except Exception as _exc:
                    logger.warning(
                        "refresh_pending_family_snapshots: Gamma fetch failed for %s: %s",
                        slug, _exc,
                    )
                    continue

            # #35: the per-family /events?slug= fetch above omits enableOrderBook on
            # child markets → substrate capture refused ("required boolean fact missing").
            # find_weather_markets uses the fully-enriched _get_active_events path that
            # the liquid-bin capture already relies on. Use it as the discovery source so
            # every bin (incl never-seen illiquid MECE tails) captures; the downstream
            # gamma_by_family filter scopes CLOB capture to the pending families.
            from src.data.market_scanner import find_weather_markets_or_raise as _fwm
            discovered_events = _fwm(min_hours_to_resolution=0.0)
            logger.info(
                "refresh_pending_family_snapshots: slug fetch complete "
                "families_needing_refresh=%d raw_events=%d discovered_events=%d",
                len(families_needing_refresh), len(raw_events_collected), len(discovered_events),
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
            key = (city_name.lower(), td, metric_ev)
            gamma_by_family[key] = ev

        # Filter to ONLY the pending families (bounded CLOB calls, no universe sweep).
        markets: list[dict] = []
        skipped_not_found = 0
        for city, target_date, metric in families_needing_refresh:
            key = (city.lower(), target_date, metric)
            ev = gamma_by_family.get(key)
            if ev is None:
                skipped_not_found += 1
                logger.warning(
                    "refresh_pending_family_snapshots: Gamma did not return event for "
                    "%s/%s/%s — bin identity unknown, family will stay at FDR gate",
                    city, target_date, metric,
                )
                continue
            markets.append(ev)

        if not markets:
            logger.warning(
                "refresh_pending_family_snapshots: no Gamma events matched pending families; "
                "families_needing_refresh=%d skipped_not_found=%d",
                len(families_needing_refresh), skipped_not_found,
            )
            return {
                "status": "no_refreshable_markets",
                "families_needing_refresh": len(families_needing_refresh),
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
        with PolymarketClient(public_http_timeout=_clob_timeout) as clob:
            summary = refresh_executable_market_substrate_snapshots(
                write_conn,
                markets=markets,
                clob=clob,
                captured_at=now_utc,
                scan_authority="VERIFIED",
                max_outcomes=0,  # UNLIMITED: capture every bin of each pending family
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
        "families_needing_refresh": len(families_needing_refresh),
        "no_topology": no_topology,
        "fresh_skipped": fresh_skipped,
        "markets_submitted": len(markets),
        **summary,
    }
    logger.info("refresh_pending_family_snapshots: %s", result)
    return result


@_scheduler_job("market_discovery")
def _market_discovery_cycle() -> None:
    """Refresh executable market substrate outside decision-cycle critical path."""

    acquired = _market_discovery_lock.acquire(blocking=False)
    if not acquired:
        logger.warning("market_discovery skipped: previous market_discovery still running")
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
    finally:
        _market_discovery_lock.release()


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
    cannot prove current `PRAGMA user_version` after retries.
    This is the Phase 2→Phase 3 enforcement promotion per architect audit A-2.

    K1 split 2026-05-11: this function now delegates to _startup_db_schema_ready_check,
    which checks both canonical DB files directly. The old data-ingest sentinel
    is no longer authority for live boot because live forecast production moved
    to forecast-live while com.zeus.data-ingest is not a required live process.
    Kept for API compat; do not remove.
    """
    _startup_db_schema_ready_check()


def _startup_world_db_schema_ready_check() -> str:
    """Read-only world DB schema currency check for live startup."""
    import sqlite3

    from src.state.db import ZEUS_WORLD_DB_PATH, assert_schema_current

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
        row = conn.execute("PRAGMA user_version").fetchone()
        return str(row[0] if row else "unknown")
    finally:
        conn.close()


def _startup_world_db_schema_prepare() -> str:
    """Idempotently migrate an existing world DB before read-only boot proof.

    Live boot previously proved schema currency in read-only mode before any
    sanctioned world DB initialization could run. A merged SCHEMA_VERSION bump
    could therefore wedge the daemon indefinitely until an operator ran manual
    schema preparation. This helper keeps the final authority as the read-only
    user_version proof while allowing existing stale world DBs to pass through
    the normal idempotent init_schema() path first. This may perform bounded
    startup DDL on ``state/zeus-world.db`` when code has advanced ahead of the
    DB user_version; missing DBs and future-schema DBs still fail closed.
    """
    import src.state.db as db_module

    path = db_module.ZEUS_WORLD_DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")

    # B2 (2026-05-28): SCHEMA_VERSION counter cancelled. Run idempotent init_schema()
    # unconditionally as a preparatory step; PRAGMA user_version is set by init_schema
    # to the frozen value 43 and is used for logging only.
    conn = db_module.get_world_connection(write_class="live")
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        current_version = int(row[0]) if row and row[0] is not None else 0
        if current_version == 43:
            return str(current_version)

        logger.warning(
            "world DB schema stale at live boot: user_version=%s — running idempotent init_schema()",
            current_version,
        )
        db_module.init_schema(conn)
        conn.commit()
        row = conn.execute("PRAGMA user_version").fetchone()
        prepared_version = int(row[0]) if row and row[0] is not None else 0
        logger.info("world DB schema prepared at live boot: user_version=%s", prepared_version)
        return str(prepared_version)
    finally:
        conn.close()


def _startup_forecasts_schema_ready_check() -> str:
    """Read-only forecast DB schema currency check for forecast-live split authority."""
    import sqlite3

    from src.state.db import ZEUS_FORECASTS_DB_PATH, assert_schema_current_forecasts

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
        row = conn.execute("PRAGMA user_version").fetchone()
        return str(row[0] if row else "unknown")
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
            prepared_world_schema_version = _startup_world_db_schema_prepare()
            logger.info(
                "world DB schema prepared/unchanged before proof: user_version=%s",
                prepared_world_schema_version,
            )
            world_schema_version = _startup_world_db_schema_ready_check()
            logger.info(
                "world DB schema current: user_version=%s",
                world_schema_version,
            )
        except Exception as exc:
            logger.warning("world DB schema readiness check failed: %s — retrying", exc)
            missing.append("world")
        try:
            forecast_schema_version = _startup_forecasts_schema_ready_check()
            logger.info(
                "forecasts DB schema current: user_version=%s",
                forecast_schema_version,
            )
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
        "(zeus-world.db + zeus-forecasts.db user_version). "
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
        # 1. Bias correction reminder
        bias_enabled = settings.bias_correction_enabled
        bias_data = conn.execute(
            "SELECT COUNT(*) FROM model_bias WHERE source='ecmwf' AND n_samples >= 20"
        ).fetchone()[0]

        if not bias_enabled and bias_data > 0:
            logger.warning(
                "⚠ DEFERRED ACTION: bias_correction_enabled=false but %d ECMWF bias "
                "entries ready. To activate: 1) Recompute calibration_pairs with bias "
                "correction 2) Refit Platt models 3) Set bias_correction_enabled=true "
                "4) Run test_cross_module_invariants.py",
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


@_scheduler_job("edli_event_reactor")
def _edli_event_reactor_cycle() -> None:
    """EDLI event-reactor scheduler hook.

    Cut 10 wires daemon scheduling and schema/config readiness. The live-money
    submit adapter still uses injected gates; until an event is explicitly
    accepted by those gates, this job is conservative and side-effect free.
    """

    edli_cfg = _settings_section("edli_v1", {})
    if not edli_cfg.get("enabled") or not edli_cfg.get("event_writer_enabled"):
        return
    import sqlite3  # transient world-DB lock classification for fail-soft emit boundary
    from src.engine.event_reactor_adapter import (
        edli_source_truth_gate,
        event_bound_live_adapter_from_trade_conn,
        event_bound_no_submit_adapter_from_trade_conn,
        executable_snapshot_gate_from_trade_conn,
        riskguard_allows_new_entries,
    )
    from src.engine.event_bound_final_intent import submit_event_bound_final_intent_via_existing_executor
    from src.events.event_store import EventStore
    from src.events.reactor import OpportunityEventReactor, ReactorConfig
    from src.riskguard.riskguard import get_current_level
    from src.state.db import ZEUS_FORECASTS_DB_PATH, get_forecasts_connection_read_only, get_trade_connection_with_world_required, get_world_connection
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

    conn = get_world_connection()
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
    forecasts_conn = get_forecasts_connection_read_only()
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
        forecast_emit_limit = _edli_bounded_positive_int(edli_cfg, "forecast_snapshot_emit_limit", default=20, maximum=50)
        day0_emit_limit = _edli_bounded_positive_int(edli_cfg, "day0_catchup_emit_limit", default=20, maximum=100)
        proof_limit = _edli_bounded_positive_int(edli_cfg, "no_submit_proof_limit", default=10, maximum=50)
        # EDLI live-canary contention fix (2026-05-31): the FSR/Day0/redecision
        # EMIT block writes opportunity_events to the WAL zeus-world.db shared
        # in-process with the market-channel ingestor. Serialize the whole
        # emit+commit unit under the process-global world-DB write mutex so it
        # never holds the WAL write lock concurrently with the ingestor (no HTTP
        # is done inside this block — the emit reads forecasts/trade DBs and
        # writes world — so the mutex stays short and never spans a venue fetch).
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
                    # COVERAGE-FAIRNESS (universe-collapse fix 2026-06-04): the emit is
                    # ORDER BY ... snapshot_id DESC LIMIT N. Under one batch write all families
                    # share computed_at, so snapshot_id-DESC deterministically emits only the
                    # alphabetic TAIL (M-W) every cycle and starves A-L forever. The fairness
                    # round-robin rotates the window by cycle_index, parsed from a monotonic
                    # `cycle-N` source — so it must be FED that source here (the plain emit
                    # previously passed none -> cycle_index frozen at 0 -> A-L permanently dark).
                    # Bounded by `limit` per cycle; covers all 108 (city,metric) families in
                    # ceil(108/limit) cycles. Gated by edli_v1.coverage_fairness_emit_enabled.
                    # source ONLY when fairness is ON: a per-cycle `cycle-N` source advances the
                    # round-robin window AND makes the emit re-emit each cycle. Flag-OFF -> None ->
                    # the original one-shot catch-up (byte-identical to pre-fix behavior).
                    _fair_source = (
                        _edli_next_redecision_source()
                        if bool(edli_cfg.get("coverage_fairness_emit_enabled", False))
                        else None
                    )
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
            # Continuous re-decision (DEFAULT OFF — redecision_continuous_enabled): re-emit
            # FSR-equivalent events for committed market-backed families each cycle, with a per-cycle
            # distinct source so they do NOT dedup to the consumed FSR. Routing through the pending path
            # makes _refresh_pending_family_snapshots capture fresh prices just-in-time → the reactor
            # re-decides every ~60s instead of once per 12h forecast. Fixes EDLI-mode "hours per order".
            # already_pending skip + cap bound the queue. Non-fatal: never breaks the reactor cycle.
            if bool(edli_cfg.get("redecision_continuous_enabled", False)):
                try:
                    _rd_cap = _edli_bounded_positive_int(edli_cfg, "redecision_max_per_cycle", default=50, maximum=200)
                    # B4 (Phase-2): a monotonic `cycle-N` source so the coverage-fairness
                    # round-robin (int(source.split('-')[-1])) advances its window each cycle
                    # and reaches all cities within ceil(N/limit) cycles. Still distinct per
                    # cycle (re-emit idempotency). The prior ISO-timestamp source raised
                    # ValueError in the parse -> cycle_index frozen at 0 -> cities 21..N dark.
                    _rd_source = _edli_next_redecision_source()
                    _rd_pending = _edli_pending_entity_keys(conn)
                    _rd_n = _edli_emit_forecast_snapshot_events(
                        conn,
                        decision_time=now,
                        received_at=received_at,
                        limit=_rd_cap,
                        source=_rd_source,
                        already_pending_keys=_rd_pending,
                    )
                    logger.info(
                        "edli_redecision: enqueued=%d cap=%d skipped_pending=%d",
                        _rd_n, _rd_cap, len(_rd_pending),
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
        store = EventStore(conn)

        # ARCHIVE SWEEP (operator directive 2026-06-04): before the reactor scans,
        # prune candidates whose target LOCAL day has ENDED in their OWN city tz
        # (Oceania-frontier anchored, never raw UTC) to terminal 'expired' status so
        # the active scan (fetch_pending + warm-cache family queries) stops re-reading
        # ~1.7M already-settled rows every cycle. Marks the MUTABLE processing row
        # only — the append-only event log (provenance) is untouched. batch_limit
        # bounds the one-time backlog drain so a giant first sweep does not blow the
        # 60s cycle budget; subsequent cycles see a steady trickle. Fail-soft: a
        # sweep error must never crash a decision cycle (the read floor in
        # fetch_pending still independently drops strictly-past rows from this cycle).
        try:
            _archived = store.archive_expired_candidates(decision_time=now.isoformat())
            if _archived:
                logger.info(
                    "EDLI reactor: archived %d expired (target-local-day-ended) "
                    "candidates → 'expired' (excluded from future scans)",
                    _archived,
                )
        except Exception as _sweep_exc:  # noqa: BLE001 — fail-soft; read floor still guards
            logger.warning(
                "EDLI reactor: archive_expired_candidates sweep failed (non-fatal; "
                "fetch_pending read floor still drops strictly-past rows): %r",
                _sweep_exc,
            )

        # CHANNEL EVENT SWEEP (operator directive 2026-06-04 companion): prune
        # superseded BEST_BID_ASK_CHANGED / BOOK_SNAPSHOT / NEW_MARKET_DISCOVERED
        # pending rows. For each (event_type, token_id) group only the LATEST
        # available_at survives; all older ones are superseded state and marked
        # 'expired'. The 1.7M pending channel-event backlog (1743 distinct tokens
        # × ~990 ticks each) is the main fetch_pending JOIN cost; this sweep
        # reduces it to ~1 row per token. batch_limit bounds the per-cycle work so
        # the backlog drains across cycles rather than in one giant transaction.
        # Fail-soft: a sweep error must never crash a decision cycle.
        try:
            _ch_archived = store.archive_superseded_channel_events()
            if _ch_archived:
                logger.info(
                    "EDLI reactor: archived %d superseded channel events "
                    "(BEST_BID_ASK_CHANGED/BOOK_SNAPSHOT/NEW_MARKET_DISCOVERED) → "
                    "'expired'; pending channel-event scan reduced",
                    _ch_archived,
                )
        except Exception as _ch_sweep_exc:  # noqa: BLE001 — fail-soft
            logger.warning(
                "EDLI reactor: archive_superseded_channel_events sweep failed "
                "(non-fatal): %r",
                _ch_sweep_exc,
            )

        regret_ledger = NoTradeRegretLedger(conn)
        reactor_mode = str(edli_cfg.get("reactor_mode", "live_no_submit"))
        real_order_submit_enabled = bool(edli_cfg.get("real_order_submit_enabled", False))
        live_bridge_mode = reactor_mode in {"live", "submit_disabled_live_bridge"}
        # Configure the process-wide risk allocator/governor BEFORE the submit adapter is
        # built so the live submit path's select_global_order_type does not raise
        # AllocationDenied("allocator_not_configured"). The legacy discover cycle wires this
        # via refresh_global_allocator; the EDLI cycle does not run that cycle, so without
        # this seam every canary order silently blocks (see /tmp/edli_submit_gate_trace.md).
        # FAIL-CLOSED: if the refresh cannot source a trustworthy drawdown (wallet unreachable
        # / baseline undefined / exception), degrade THIS cycle to the no-submit adapter rather
        # than submit live with an unconfigured-but-proceeding allocator.
        live_submit_effective = live_bridge_mode
        if live_bridge_mode:
            _alloc_refresh = _edli_refresh_global_allocator_for_live_bridge(conn)
            if not _alloc_refresh.get("configured"):
                live_submit_effective = False
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
        # FAIL-SOFT: if load_portfolio raises, the provider stays None and Kelly
        # sizing falls back to pre-#107 single-Kelly (no crash, no over-size vs
        # the old behaviour). The in-flight reservation accumulator (INV-K7) is
        # closure-held inside the adapter factory, fresh per cycle.
        _portfolio_state_provider = None
        try:
            _portfolio_snapshot = load_portfolio()
            _portfolio_state_provider = lambda: _portfolio_snapshot  # noqa: E731 — cycle-scoped closure
        except Exception as _portfolio_exc:  # noqa: BLE001 — fail-soft to single-Kelly
            logger.warning(
                "EDLI reactor: portfolio snapshot load failed (non-fatal); Kelly "
                "sizing falls back to single-asset (full-bankroll) this cycle: %r",
                _portfolio_exc,
            )
        submit_adapter = (
            event_bound_live_adapter_from_trade_conn(
                trade_conn,
                forecast_conn=forecasts_conn,
                topology_conn=forecasts_conn,
                calibration_conn=conn,
                get_current_level=get_current_level,
                portfolio_state_provider=_portfolio_state_provider,
                real_order_submit_enabled=real_order_submit_enabled if reactor_mode == "live" else False,
                live_canary_enabled=bool(edli_cfg.get("live_canary_enabled", False)),
                taker_fok_fak_live_enabled=bool(edli_cfg.get("taker_fok_fak_live_enabled", False)),
                durable_submit_outbox_enabled=bool(edli_cfg.get("durable_submit_outbox_enabled", False)),
                tiny_live_max_notional_usd=float(edli_cfg.get("tiny_live_max_notional_usd", 5.0)),
                live_cap_conn=conn,
                canary_force_taker_provider=_edli_canary_force_taker_provider(conn, edli_cfg),
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
                    decision_time=now,
                ),
            )
            if live_submit_effective
            else event_bound_no_submit_adapter_from_trade_conn(
                trade_conn,
                forecast_conn=forecasts_conn,
                topology_conn=forecasts_conn,
                calibration_conn=conn,
                get_current_level=get_current_level,
                portfolio_state_provider=_portfolio_state_provider,
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
            config=ReactorConfig(
                reactor_mode=reactor_mode,
                real_order_submit_enabled=real_order_submit_enabled,
                taker_fok_fak_live_enabled=bool(edli_cfg.get("taker_fok_fak_live_enabled", False)),
                tiny_live_max_notional_usd=float(edli_cfg.get("tiny_live_max_notional_usd", 5.0)),
                tiny_live_max_orders_per_day=int(edli_cfg.get("tiny_live_max_orders_per_day", 1)),
                tiny_live_max_orders_per_window=int(edli_cfg.get("tiny_live_max_orders_per_window", 1)),
                # Task #102 book-wide edge-zone admission. Absent key => default
                # False => byte-identical legacy money-path (the operator owns
                # config/settings.json; this reads it without writing it).
                edge_zone_admission_enabled=bool(edli_cfg.get("edge_zone_admission_enabled", False)),
                edge_zone_min_ev_per_dollar=float(edli_cfg.get("edge_zone_min_ev_per_dollar", 0.0)),
            ),
        )
        _rr = reactor.process_pending(decision_time=now, limit=proof_limit)
        logger.info(
            "EDLI reactor cycle result: processed=%d proof_accepted=%d rejected=%d retried=%d dead=%d reasons=%r",
            _rr.processed, _rr.proof_accepted, _rr.rejected, _rr.retried, _rr.dead_lettered, _rr.rejection_reasons[:8],
        )
        conn.commit()
    finally:
        try:
            trade_conn.close()
        except NameError:
            pass
        forecasts_conn.close()
        conn.close()


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

    edli_cfg = _settings_section("edli_v1", {})
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

    edli_cfg = _settings_section("edli_v1", {})
    if not edli_cfg.get("enabled"):
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

    NOTE: ``mainstream_agreement_reference_enabled`` is currently OFF in config
    (operator unblock). This warmer is safe to run regardless — when the flag is
    OFF the reactor never reads the cache, and when it is re-enabled the cache is
    already warm. Not a DB writer (no table owned); the @_scheduler_job decorator
    is the only wiring needed (B047). Fail-soft: a transient Open-Meteo failure
    logs but never crashes this job (consumers fail-closed in the interim).
    """

    edli_cfg = _settings_section("edli_v1", {})
    if not edli_cfg.get("enabled"):
        return
    from src.data.mainstream_forecast_source import warm_mainstream_point
    from src.state.db import get_world_connection

    conn = get_world_connection()
    try:
        pending_rows = conn.execute(
            """
            SELECT DISTINCT
                json_extract(e.payload_json, '$.city')        AS city,
                json_extract(e.payload_json, '$.target_date') AS target_date,
                json_extract(e.payload_json, '$.metric')      AS metric
            FROM opportunity_events e
            JOIN opportunity_event_processing p ON p.event_id = e.event_id
            WHERE p.consumer_name = 'edli_reactor_v1'
              AND p.processing_status = 'pending'
              AND e.event_type = 'FORECAST_SNAPSHOT_READY'
            """
        ).fetchall()
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
    logger.info("EDLI mainstream warm: warmed=%d of %d pending families", warmed, len(pending_rows))


@_scheduler_job("arm_gate_emit")
def _arm_gate_emit_cycle() -> None:
    """Iron-rule-4 antibody: AUTO-RE-EMIT the settlement-grounded ARM-gate artifact.

    THE BREAK THIS MAKES IMPOSSIBLE: the settlement→ARM-evidence loop was WIRED
    (producer ``scripts/measure_arm_gate_settlement.py --emit-artifact`` +
    consumer ``_assert_edli_arm_gate_artifact`` / ``verify_edli_arm_gate_artifact``)
    but never AUTOMATED — nothing RAN the producer. So
    ``state/edli_arm_gate_artifact.json`` could go missing, or its ``commit_sha``
    could fall behind the running HEAD on every deploy. Either makes the live/canary
    boot gate fail-closed (``ARM_GATE_ARTIFACT_MISSING`` / ``COMMIT_SHA_MISMATCH``) —
    the system is structurally un-armable AND cannot even boot. This job re-emits
    the artifact on startup (re-stamping ``commit_sha`` to the running HEAD) and on
    a ~6h interval (refreshing as settlements accrue), so the break can never recur.

    HONESTY PRESERVED: it runs the SAME producer, which is DENIED-safe by
    construction — it NEVER emits an ARM_ELIGIBLE artifact on denied/insufficient
    data (ev<=0 / coverage_licensed:false → the consumer rejects). This job does
    NOT touch the arm verdict logic, the arm threshold, or config — it only
    re-stamps + refreshes the evidence the consumer already enforces.

    SUBPROCESS (not in-process): the producer does heavy aggregation reading
    state/zeus-world.db (receipts) + state/zeus-forecasts.db (settlements). Running
    it as a child process avoids any world/forecasts DB-lock contention with the
    live reactor running under world_write_mutex in THIS process.

    Not a DB writer (writes a FILE, not a table) — the @_scheduler_job decorator is
    the only wiring needed (B047 observability); it owns no db_table_ownership entry,
    so it is OUT of assert_writer_jobs_registered's scope and cannot trip that
    FATAL boot guard. Flag-gated by ``edli_arm_gate_emit_enabled`` (default True so
    the antibody is ACTIVE; explicit False == today's no-op for a safe rollback).

    FAILURE ISOLATION: the @_scheduler_job decorator already swallows + marks
    FAILED on any raise, but this fn additionally wraps the producer call in
    try/except and RETURNS on failure (fail-soft, mirroring the warm jobs) so a
    transient git/DB/subprocess hiccup never crashes the daemon and never leaves a
    half-written artifact (the producer writes atomically via os.replace).
    """
    edli_cfg = _settings_section("edli_v1", {})
    if not edli_cfg.get("enabled"):
        return
    if not bool(edli_cfg.get("edli_arm_gate_emit_enabled", True)):
        # Flag-OFF: strict no-op (byte-identical to pre-antibody behavior).
        return

    artifact_path = str(edli_cfg.get("edli_arm_gate_artifact_path") or "").strip()
    if not artifact_path:
        logger.error(
            "arm_gate_emit: edli_arm_gate_artifact_path unset — cannot emit (the boot "
            "gate reads this same key; arming stays blocked). No-op this tick."
        )
        return

    repo_root = Path(__file__).resolve().parent.parent
    producer = repo_root / "scripts" / "measure_arm_gate_settlement.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(producer), "--emit-artifact", artifact_path],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft; next tick / next boot retries
        logger.error(
            "arm_gate_emit: producer subprocess raised (non-fatal; artifact NOT "
            "refreshed this tick, consumers stay fail-closed): %r",
            exc,
        )
        return

    if completed.returncode != 0:
        logger.error(
            "arm_gate_emit: producer exited rc=%d (non-fatal; artifact NOT refreshed "
            "this tick). stderr tail: %s",
            completed.returncode,
            (completed.stderr or "")[-500:],
        )
        return

    logger.info(
        "arm_gate_emit: re-emitted ARM-gate artifact → %s (commit_sha re-stamped to "
        "running HEAD; verdict remains the producer's honest settlement-grounded "
        "DENIED/ELIGIBLE — never fabricated)",
        artifact_path,
    )


@_scheduler_job("world_wal_checkpoint")
def _world_wal_checkpoint_cycle() -> None:
    """Periodic zeus-world.db WAL TRUNCATE backstop (2026-06-04, part 2).

    Root (critic-proven, live): ``state/zeus-world.db-wal`` grew to GBs because
    long-lived READER connections held a WAL snapshot across cycles, pinning the
    WAL floor so ``wal_checkpoint`` returned BUSY ``(1,-1,-1)`` and never
    truncated → unbounded growth → eventual lock-starvation of opportunity_events
    emission (30-min ZERO candidates). Part 1 releases each long-lived reader's
    snapshot per cycle so the floor advances; THIS job is the periodic backstop
    that reclaims the freed frames via ``PRAGMA wal_checkpoint(TRUNCATE)``.

    Observability: the ``(busy, log_frames, checkpointed_frames)`` triple is
    ALWAYS logged. ``busy == 0`` = truncated; a CHRONIC ``busy == 1`` is a loud
    signal that a reader is still pinning the floor (a part-1 regression) — it is
    NOT silenced. Not a table writer; ``checkpoint_world_wal`` uses a dedicated
    short-lived connection and does NOT take the world write mutex (a checkpoint
    is not a write txn; SQLite serializes checkpoints internally), so it never
    blocks world writers for its duration. Fail-soft via the decorator.
    """
    from src.state.db import checkpoint_world_wal

    busy, log_frames, ckpt_frames = checkpoint_world_wal()
    if busy == 0:
        logger.info(
            "world WAL checkpoint(TRUNCATE): OK busy=%d log_frames=%d checkpointed=%d",
            busy, log_frames, ckpt_frames,
        )
    else:
        # BUSY = a reader still pins the WAL floor; TRUNCATE could not run.
        # Loud (warning) so chronic starvation is visible, not silent.
        logger.warning(
            "world WAL checkpoint(TRUNCATE): BUSY busy=%d log_frames=%d checkpointed=%d "
            "— a reader is pinning the WAL floor (part-1 per-cycle release regression?)",
            busy, log_frames, ckpt_frames,
        )


def _edli_bounded_positive_int(config: dict, key: str, *, default: int, maximum: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))


def _edli_emit_forecast_snapshot_events(
    world_conn,
    *,
    decision_time: datetime,
    received_at: str,
    limit: int,
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
    self-regulates to families the reactor has already drained)."""
    try:
        rows = world_conn.execute(
            """
            SELECT DISTINCT e.entity_key
            FROM opportunity_events e
            JOIN opportunity_event_processing p ON p.event_id = e.event_id
            WHERE p.consumer_name = 'edli_reactor_v1'
              AND p.processing_status IN ('pending', 'processing', 'claimed')
            """
        ).fetchall()
    except Exception:  # noqa: BLE001 — fail-open: no skip set (cap still bounds)
        return set()
    return {str(r[0]) for r in rows}


def _edli_emit_day0_extreme_events(
    world_conn,
    trade_conn,
    *,
    decision_time: datetime,
    received_at: str,
    limit: int,
) -> int:
    """Emit EDLI Day0 extreme events from durable observation authority rows.

    This is an operator catch-up/evidence scanner only. The no-submit EDLI
    reactor does not enable it by default because
    settlement_day_observation_authority is an observability table written by
    the existing cycle runtime, not the online source hook.
    """

    from src.events.event_writer import EventWriter
    from src.events.triggers.day0_extreme_updated import Day0ExtremeUpdatedTrigger

    trigger = Day0ExtremeUpdatedTrigger(EventWriter(world_conn))
    return len(
        trigger.scan_authority_rows(
            observation_conn=trade_conn,
            settlement_semantics=_edli_day0_settlement_semantics,
            decision_time=decision_time,
            received_at=received_at,
            limit=limit,
        )
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


def _edli_canary_force_taker_provider(world_conn, edli_cfg):
    """Build the canary force-taker gate: True iff canary-active AND fills < min.

    Design §4 item 7: the canary FORCES the taker branch (bypassing the
    governor's maker/taker CHOICE, never its NO_TRADE/risk gates) only while the
    live-canary stage is enabled AND the proven canary fill count is still below
    ``edli_live_min_canary_count``. Once the min fills land, the gate returns
    False and order-type selection reverts to the governor + EV boundary (§1-§2).

    The confirmed-fill count is sourced from the world DB EDLI live-order audit
    (``edli_live_profit_audit`` / ``edli_live_order_events``, db: world). When the
    count cannot be computed (tables absent at canary genesis, or a read error),
    the gate fails OPEN to force-taker: the canary stage is, by definition, not
    yet proven, so forcing the deterministic FOK proof is the conservative
    canary-stage default (Fitz #4: do not infer "canary complete" from a missing
    count).
    """

    if not bool(edli_cfg.get("live_canary_enabled", False)):
        return lambda: False
    min_canary_count = int(edli_cfg.get("edli_live_min_canary_count", 1))

    def _provider() -> bool:
        try:
            from src.events.live_profit_audit import (
                _canonical_promotion_rows,
                _promotion_summary_from_rows,
            )

            rows = _canonical_promotion_rows(world_conn)
            summary = _promotion_summary_from_rows(world_conn, rows)
            confirmed = int(summary.confirmed_fill_count)
        except Exception:
            # Count unavailable -> canary not proven -> force the taker proof.
            return True
        return confirmed < min_canary_count

    return _provider


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

        with PolymarketClient() as clob:
            return clob.get_orderbook_snapshot(token_id)

    return _fetch


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
        venue_summary = _edli_venue_connectivity_authority_summary(checked_at)
        balance_status, balance_authority_id = _edli_balance_allowance_status(
            final_intent,
            checked_at,
            enabled=balance_check_enabled,
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

    with PolymarketClient() as clob:
        clob.v2_preflight()
    return {
        "authority_id": "polymarket_v2_preflight",
        "allow_submit": True,
        "checked_at": checked_at.isoformat(),
    }


def _edli_balance_allowance_status(final_intent, checked_at: datetime, *, enabled: bool) -> tuple[str, str]:
    if not enabled:
        raise ValueError("PRE_SUBMIT_ALLOWANCE_CHECK_DISABLED")
    from src.data.polymarket_client import PolymarketClient

    intent = final_intent.payload
    side = str(intent.get("side") or "").upper()
    token_id = str(intent.get("token_id") or "")
    size = float(intent.get("size") or 0.0)
    notional = float(intent.get("notional_usd") or 0.0)
    with PolymarketClient() as clob:
        collateral = clob._ensure_v2_adapter().get_collateral_payload()
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
        _edli_events_table,
        edli_bridge_position_id,
        edli_bridge_position_id_legacy,
        materialize_position_current_from_edli_fill,
    )

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
    orphaned_seen = 0
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
        if orphaned_seen >= max(0, limit):
            break
        orphaned_seen += 1
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
            # One bad aggregate must not block healing the rest. The EDLI events
            # persist; the next scan retries this aggregate.
            logger.error(
                "EDLI durable fill-bridge: failed to bridge aggregate %s "
                "(non-fatal; EDLI events persist, next scan retries): %s",
                aggregate_id,
                exc,
                exc_info=True,
            )
    return bridged


@_scheduler_job("edli_user_channel_reconcile")
def _edli_user_channel_reconcile_cycle() -> None:
    """EDLI user-channel/reconcile service boundary.

    Disabled by default. The live-order aggregate may only accept fill/lifecycle
    facts from authenticated user channel or explicit reconcile writers; public
    market-channel data remains quote evidence only.
    """

    edli_cfg = _settings_section("edli_v1", {})
    if not edli_cfg.get("enabled") or not edli_cfg.get("edli_user_channel_reconcile_enabled", False):
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
        edli_cfg = _settings_section("edli_v1", {})
        if not edli_cfg.get("enabled") or not edli_cfg.get(
            "edli_user_channel_reconcile_enabled", False
        ):
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
        edli_cfg = _settings_section("edli_v1", {})
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

    edli_cfg = _settings_section("edli_v1", {})
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

                trade_conn = get_trade_connection(write_class="live")
                try:
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
                    trade_conn.close()
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

    In EDLI shadow/no-submit modes (edli_shadow_no_submit, edli_submit_disabled_bridge,
    edli_live_canary, edli_live) this is the ONLY path that fires chain sync and exit
    monitoring — run_cycle() is never called in those modes.

    Shadow-mode safety: exit_order_submit_enabled is set to False when
    real_order_submit_enabled is False, preventing real sell orders from being
    placed by the monitoring phase while the daemon is in shadow/no-submit mode.
    State transitions (exit_pending_missing resolution, chain_state updates) still
    run — they are read + DB-state-only operations, not order submissions.

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
    from src.state.decision_chain import CycleArtifact

    edli_cfg = _settings_section("edli_v1", {})
    real_order_submit_enabled = bool(edli_cfg.get("real_order_submit_enabled", False))

    conn = get_connection()
    if conn is None:
        logger.warning("chain_sync_and_exit_monitor: DB write-lock degrade — skipping cycle")
        return

    summary: dict = {"monitors": 0, "exits": 0}
    try:
        portfolio = load_portfolio()
        with PolymarketClient() as clob:
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

            # Phase 2: exit-lifecycle monitoring — resolves exit_pending_missing,
            # checks pending exit fills, runs monitor refresh for active positions.
            # exit_order_submit_enabled=False in shadow/no-submit modes: state
            # transitions run but no real sell orders are placed.
            tracker = get_tracker()
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

        # INV-17 / DT#1: commit the DB transaction (chain-sync + monitoring state
        # transitions) FIRST, then export the derived portfolio/tracker JSON with the
        # committed artifact id — so canonical_write.detect_stale_portfolio's marker
        # stays valid and JSON can never lead the DB.
        from src.state.canonical_write import commit_then_export
        from src.state.decision_chain import store_artifact
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
    # above by direct read-only user_version checks on the canonical DB files.
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
    edli_cfg = _settings_section("edli_v1", {})
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
    if live_execution_mode in EDLI_EVENT_DRIVEN_MODES and edli_cfg.get("enabled"):
        scheduler.add_job(
            _edli_event_reactor_cycle,
            "interval",
            minutes=1,
            id="edli_event_reactor",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 45.0),
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
        # THROUGHPUT STRUCTURAL FIX (2026-06-01): dedicated executable-snapshot substrate
        # warmer, DECOUPLED from the reactor decision cycle. The refresh
        # (_refresh_pending_family_snapshots) does a ~76s-cold universe Gamma scan +
        # per-token CLOB capture; running it inline in _edli_event_reactor_cycle blew the
        # reactor's 1-min interval (overlapping triggers coalesced/skipped → 0 completed
        # cycles → 0 trades). On its own cadence the reactor reads already-captured
        # snapshots (DB-only) and reaches submit in seconds. Runs on a longer interval than
        # the reactor (the universe scan is TTL-cached 300s; ~90s keeps pending families
        # fresh without re-scanning every reactor tick). max_instances=1/coalesce so a slow
        # warm never stacks. Data-only (no orders); fail-soft.
        scheduler.add_job(
            _edli_market_substrate_warm_cycle,
            "interval",
            seconds=90,
            id="edli_market_substrate_warm",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 25.0),
            max_instances=1,
            coalesce=True,
        )
        # MAINSTREAM WARM (E2 / operator directive 2026-06-04 #2): dedicated off-mutex
        # warmer for the mainstream-forecast point cache (read_mainstream_point_cached),
        # mirroring _edli_market_substrate_warm_cycle. The reactor proof path now ALWAYS
        # annotates the mainstream/bias agreement value on every candidate (decoupled from
        # mainstream_agreement_reference_enabled), reading the WARM CACHE only — so this job
        # MUST run for the cache to populate, else every receipt carries
        # mainstream_*=None (unknown). Gated only by edli_v1.enabled (inside the job), NOT
        # by the reference flag — warming the cache is just a read, off-mutex, safe. The
        # fetch applies Retry-After backoff on 429s; on its own cadence it never serializes
        # a world write. Data-only (no orders); fail-soft. Display-only: the value it warms
        # is NEVER a decision input (the enforce/arm coupling is deleted).
        scheduler.add_job(
            _edli_mainstream_warm_cycle,
            "interval",
            seconds=90,
            id="edli_mainstream_warm",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 28.0),
            max_instances=1,
            coalesce=True,
        )
        # IRON-RULE-4 ANTIBODY (2026-06-04): AUTO-RE-EMIT the settlement-grounded
        # ARM-gate artifact (state/edli_arm_gate_artifact.json). The producer/consumer
        # loop existed but was never AUTOMATED — nothing RAN the producer, so the
        # artifact could go missing or its commit_sha fall behind HEAD on a deploy →
        # the boot gate (_assert_edli_arm_gate_artifact) fail-closes ARM_GATE_ARTIFACT_
        # MISSING / COMMIT_SHA_MISMATCH → un-armable AND un-bootable in canary/live.
        # This job re-stamps on startup (~40s after boot, after the warm jobs) and
        # refreshes every 6h as 06-04/05/06/07 settle. DENIED-safe: it runs the same
        # producer, which NEVER emits ARM_ELIGIBLE on denied data — it does not touch
        # the arm verdict/threshold. Writes a FILE (no DB table) so it is out of
        # assert_writer_jobs_registered's scope. Flag-gated by edli_arm_gate_emit_enabled
        # (default True; explicit False == today's no-op for safe rollback). Fail-soft:
        # a producer error logs + marks the @_scheduler_job FAILED but never crashes
        # the daemon. SUBPROCESS so the heavy world/forecasts aggregation never
        # contends with the live reactor's world_write_mutex in THIS process.
        scheduler.add_job(
            _arm_gate_emit_cycle,
            "interval",
            hours=6,
            id="arm_gate_emit",
            next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 40.0),
            max_instances=1,
            coalesce=True,
        )
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
        # touches no arming flags. Gated by market_substrate_refresh_enabled
        # (default True) so the operator can disable without code change.
        if edli_cfg.get("market_substrate_refresh_enabled", True):
            scheduler.add_job(
                _market_discovery_cycle,
                "interval",
                minutes=5,
                id="market_discovery",
                next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 35.0),
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
    if live_execution_mode in EDLI_EVENT_DRIVEN_MODES and edli_cfg.get("edli_user_channel_reconcile_enabled"):
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
    # and resolves exit_pending_missing / settled-but-active positions. Shadow-safe:
    # the job runs the monitoring phase with exit_order_submit_enabled=real_order_submit_enabled
    # (False in shadow → DB state transitions only, no real sell orders).
    scheduler.add_job(
        _chain_sync_and_exit_monitor_cycle,
        "interval",
        minutes=2,
        id="chain_sync_and_exit_monitor",
        next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS + 60.0),
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
    scheduler.add_job(
        _wrap_reconciler_cycle, "interval", minutes=2,
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
