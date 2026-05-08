# Created: 2026-04-27
# Last reused/audited: 2026-05-07
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/T1.yaml
#                  + docs/operations/task_2026-05-01_bankroll_truth_chain/architect_memo.md §7
"""Shared pytest fixtures for R3 T1 fake venue parity tests."""

from __future__ import annotations

import os

import pytest

from tests.fakes.polymarket_v2 import FakeClock, FakeCollateralLedger, FakePolymarketVenue


os.environ.setdefault("ZEUS_MODE", "live")


@pytest.fixture(autouse=True)
def _bankroll_provider_test_isolation(monkeypatch):
    """P0-A antibody: deterministic bankroll, no live wallet fetches in tests.

    The bankroll provider wraps an on-chain wallet query. Without this fixture
    every ``riskguard.tick()`` codepath would silently dial out to the live
    Polymarket endpoint during pytest collection, AND the module-level cache
    would leak real wallet values across tests.

    Default behaviour: every test gets a deterministic non-config wallet
    fixture with canonical authority. The value is deliberately not tied to
    historical capital-base settings; tests that need a different wallet value
    monkeypatch ``src.runtime.bankroll_provider.current`` over this default.
    Live fetches are explicitly forbidden — ``_fetch_balance`` raises if any
    path slips through the default.
    """
    from datetime import datetime, timezone

    from src.runtime import bankroll_provider

    bankroll_provider.reset_cache_for_tests()

    def _default_current(**_kwargs):
        return bankroll_provider.BankrollOfRecord(
            value_usd=211.37,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source="polymarket_wallet",
            authority="canonical",
            staleness_seconds=0.0,
            cached=False,
        )

    def _forbid_live_fetch():
        raise AssertionError(
            "bankroll_provider._fetch_balance was invoked from a test. "
            "Live wallet queries are forbidden in unit tests; monkeypatch "
            "bankroll_provider.current() with a BankrollOfRecord fixture."
        )

    monkeypatch.setattr(bankroll_provider, "current", _default_current)
    monkeypatch.setattr(bankroll_provider, "_fetch_balance", _forbid_live_fetch)
    yield
    bankroll_provider.reset_cache_for_tests()


@pytest.fixture
def fake_venue() -> FakePolymarketVenue:
    return FakePolymarketVenue(ledger=FakeCollateralLedger(), clock=FakeClock())


@pytest.fixture
def failure_injector(fake_venue: FakePolymarketVenue):
    def _inject(mode, **params):
        fake_venue.inject(mode, **params)
        return fake_venue

    return _inject


@pytest.fixture(autouse=True)
def r3_default_risk_allocator_for_unit_tests():
    """Keep legacy live-executor unit tests focused on their targeted guard.

    Production defaults fail closed when the A2 allocator has not been
    refreshed by the cycle runner.  Older executor/collateral/heartbeat tests
    predate A2 and patch only their local guard under test; this fixture gives
    those tests an explicit healthy allocator baseline while still allowing
    individual risk tests to call ``clear_global_allocator()`` and assert the
    fail-closed default directly.
    """

    from src.control.heartbeat_supervisor import HeartbeatHealth
    from src.control import ws_gap_guard
    from src.risk_allocator import (
        AllocationDecision,
        GovernorState,
        RiskAllocator,
        clear_global_allocator,
        configure_global_allocator,
    )

    class UnitTestRiskAllocator(RiskAllocator):
        def can_allocate(self, intent, governor_state):  # type: ignore[override]
            return AllocationDecision(True, "unit_test_default", 0)

        def maker_or_taker(self, snapshot, governor_state):  # type: ignore[override]
            return "MAKER"

        def kill_switch_reason(self, governor_state):  # type: ignore[override]
            return None

        def reduce_only_mode_active(self, governor_state):  # type: ignore[override]
            return False

    ws_gap_guard.clear_for_test()
    configure_global_allocator(
        UnitTestRiskAllocator(),
        GovernorState(
            current_drawdown_pct=0.0,
            heartbeat_health=HeartbeatHealth.HEALTHY,
            ws_gap_active=False,
            ws_gap_seconds=0,
            unknown_side_effect_count=0,
            reconcile_finding_count=0,
        ),
    )
    try:
        yield
    finally:
        clear_global_allocator()
        ws_gap_guard.clear_for_test()


# ---------------------------------------------------------------------------
# SQLite Writer-Lock Antibody — Track A.3 (v4 plan §10).
#
# Collection-time enforcement that scans src/ + scripts/ for:
#   1. Direct sqlite3.connect() outside the canonical-shim allowlist.
#   2. (Reserved) _connect() calls without write_class kwarg in scope —
#      activated in Phase 1 once retrofit lands.
#   3. (Reserved) Raw subprocess.{Popen,run,...} outside the helper
#      allowlist — activated in Phase 1.y.
#
# Scope: src/ + scripts/ only (NOT repo-wide rglob). Empirical Phase 0
# baseline: 433 files / 157 KLOC parses cold in ≤ 1 s; mtime-keyed cache
# brings steady-state to ≤ 200 ms.
#
# Bypass: ZEUS_DISABLE_WRITER_LOCK_ANTIBODY=1 disables the antibody
# (documented as emergency-only; CI builds set =0 explicitly).
#
# Track A.3 posture (PR #92): check (1) is now FAIL-CI.  Any new
# sqlite3.connect() site outside this allowlist fails the test run
# immediately, preventing unreviewed direct connections from landing.
# Add to allowlist only with a cited reason (read_only / pending_track_a6
# / already_guarded).
# ---------------------------------------------------------------------------

import ast as _wla_ast
import json as _wla_json
from pathlib import Path as _wla_Path

_WLA_REPO_ROOT = _wla_Path(__file__).resolve().parent.parent
_WLA_SCAN_ROOTS = (_WLA_REPO_ROOT / "src", _WLA_REPO_ROOT / "scripts")
_WLA_CACHE_PATH = _WLA_REPO_ROOT / ".pytest_cache" / "writer_lock_antibody.json"

# Allowlisted files where direct ``sqlite3.connect`` is permitted.
#
# Reason tags used in comments:
#   canonical_shim      — the canonical DB helper; direct connect is the point
#   read_only           — file contains only SELECT queries; verified PR #86
#   read_only_ro_uri    — file uses sqlite3.connect("file:...?mode=ro", uri=True)
#   in_memory_only      — file connects only to ":memory:" (no on-disk writes)
#   already_guarded     — call is inside a db_writer_lock() context (repro_antibodies)
#   pending_track_a6    — daemon-level src/ site; full retrofit deferred to Track A.6 (#246)
#   pending_track_a6_scripts — write script not yet retrofit; deferred to Track A.6 batch
#   deferred_nonmechanical   — verify_truth_surfaces: non-mechanical rewrite, separate phase
#
_WLA_SQLITE_CONNECT_ALLOWLIST = frozenset({
    # --- canonical infrastructure ---
    "src/state/db.py",                              # canonical_shim
    # NOTE: src/state/db_writer_lock.py is intentionally NOT allowlisted. The file
    # has no sqlite3.connect() call sites today; if a future edit introduces one,
    # the antibody SHOULD fire so this module stays a coordination layer (not a
    # connect path). Allowlisting a no-connect file would weaken the gate.

    # --- src/ daemon sites: pending Track A.6 (#246) ---
    "src/data/market_scanner.py",                   # pending_track_a6
    # src/ingest_main.py, src/observability/status_summary.py,
    # src/riskguard/discord_alerts.py — reclassified in Track A.6 (#246) from
    # pending_track_a6 to permanent exemptions: each is either read-only on the
    # world DB or writes a separate DB (risk_state.db). Rationale comments are
    # co-located in SQLITE_CONNECT_ALLOWLIST inside src/state/db_writer_lock.py.
    "src/ingest_main.py",                           # RO: reads condition_id for UMA listener (Track A.6 #246)
    "src/observability/status_summary.py",          # RO: status dashboard read-only (Track A.6 #246)
    "src/riskguard/discord_alerts.py",              # WRITE risk_state.db only; not world-db BULK scope (Track A.6 #246)

    # --- read-only scripts: verified SELECT-only, named in PR #86 ---
    "scripts/attribution_drift_weekly.py",          # read_only (PR #86)
    "scripts/audit_divergence_exit_counterfactual.py",  # read_only (PR #86)
    "scripts/audit_realtime_pnl.py",               # read_only (PR #86)
    "scripts/bridge_oracle_to_calibration.py",      # read_only (PR #86)
    "scripts/build_correlation_matrix.py",          # read_only (PR #86)
    "scripts/compare_diurnal_v1_v2.py",            # read_only (PR #86)
    "scripts/deep_heartbeat.py",                    # read_only (PR #86)
    "scripts/healthcheck.py",                       # read_only (PR #86)
    "scripts/replay_parity.py",                     # read_only (PR #86)
    "scripts/venus_sensing_report.py",              # read_only (PR #86)

    # --- additional read-only / ro-URI scripts ---
    "scripts/audit_observation_instants_v2.py",     # read_only (SELECT-only, no INSERT/UPDATE/DELETE)
    "scripts/calibration_observation_weekly.py",    # read_only_ro_uri
    "scripts/ddd_v1_v2_replay.py",                 # read_only_ro_uri
    "scripts/diagnose_low_high_alignment.py",       # read_only (SELECT-only)
    "scripts/diagnose_truth_surfaces.py",           # read_only (SELECT-only, no INSERT/UPDATE/DELETE)
    "scripts/edge_observation_weekly.py",           # read_only_ro_uri
    "scripts/generate_monthly_bounds.py",           # read_only_ro_uri
    "scripts/learning_loop_observation_weekly.py",  # read_only_ro_uri
    "scripts/produce_activation_evidence.py",       # in_memory_only (":memory:" only)
    "scripts/replay_correctness_gate.py",           # read_only (SELECT-only)
    "scripts/state_census.py",                      # read_only_ro_uri
    "scripts/topology_doctor_code_review_graph.py", # read_only_ro_uri
    "scripts/ws_poll_reaction_weekly.py",           # read_only_ro_uri

    # --- repro_antibodies.py: mixed; all sites verified safe ---
    # Lines 77,108: inside db_writer_lock() context (already_guarded)
    # Lines 369,448: SELECT-only reads (read_only)
    # Line 543: ":memory:" only (in_memory_only)
    "scripts/repro_antibodies.py",                  # already_guarded + read_only + in_memory_only

    # --- write scripts: pending Track A.6 batch retrofit ---
    "scripts/backfill_forecast_issue_time.py",      # pending_track_a6_scripts
    "scripts/backfill_london_f_to_c_2026_05_08.py",  # pending_track_a6_scripts (fix #263 Phase D)
    "scripts/backfill_low_contract_window_evidence.py",  # pending_track_a6_scripts
    "scripts/backfill_obs_v2.py",                   # pending_track_a6_scripts
    "scripts/backfill_ogimet_metar.py",             # pending_track_a6_scripts
    "scripts/backfill_outcome_fact.py",             # pending_track_a6_scripts
    "scripts/backfill_tigge_snapshot_p_raw_v2.py",  # pending_track_a6_scripts
    "scripts/backfill_wu_daily_all.py",             # pending_track_a6_scripts
    "scripts/cleanup_ghost_positions.py",           # pending_track_a6_scripts
    "scripts/etl_forecasts_v2_from_legacy.py",      # pending_track_a6_scripts
    "scripts/fill_obs_v2_dst_gaps.py",              # pending_track_a6_scripts
    "scripts/fill_obs_v2_meteostat.py",             # pending_track_a6_scripts
    "scripts/force_cycle_with_healthy_gates.py",    # pending_track_a6_scripts
    "scripts/hko_ingest_tick.py",                   # pending_track_a6_scripts
    "scripts/ingest_grib_to_snapshots.py",          # pending_track_a6_scripts
    "scripts/migrate_add_authority_column.py",      # pending_track_a6_scripts
    "scripts/migrate_b070_control_overrides_to_history.py",  # pending_track_a6_scripts
    "scripts/migrate_backtest_runs_lane_constraint_2026_05_07.py",  # pending_track_a6_scripts
    "scripts/migrate_ensemble_snapshots_v2_add_ingest_backend.py",  # pending_track_a6_scripts
    "scripts/migrate_forecasts_availability_provenance.py",  # pending_track_a6_scripts
    "scripts/migrate_observations_k1.py",           # pending_track_a6_scripts
    "scripts/nuke_rebuild_projections.py",          # pending_track_a6_scripts
    "scripts/rebuild_calibration_pairs_canonical.py",  # pending_track_a6_scripts
    "scripts/rebuild_calibration_pairs_v2.py",      # pending_track_a6_scripts
    "scripts/rebuild_settlements.py",               # pending_track_a6_scripts
    "scripts/reevaluate_readiness_2026_05_07.py",   # pending_track_a6_scripts
    "scripts/refit_platt_v2.py",                    # pending_track_a6_scripts

    # --- deferred non-mechanical rewrite (separate phase, cited PR #86) ---
    "scripts/verify_truth_surfaces.py",             # deferred_nonmechanical (PR #86)
})


def _wla_is_bypassed() -> bool:
    """Honor operator emergency bypass via env-var."""
    return os.environ.get("ZEUS_DISABLE_WRITER_LOCK_ANTIBODY") == "1"


def _wla_load_cache() -> dict:
    if not _WLA_CACHE_PATH.exists():
        return {}
    try:
        return _wla_json.loads(_WLA_CACHE_PATH.read_text())
    except (OSError, _wla_json.JSONDecodeError):
        return {}


def _wla_save_cache(cache: dict) -> None:
    try:
        _WLA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WLA_CACHE_PATH.write_text(_wla_json.dumps(cache))
    except OSError:
        # Cache failure is non-fatal — Phase 0 antibody must not break CI.
        pass


def _wla_scan_file(py_file: _wla_Path) -> dict:
    """Parse a single file and return (rel-path-keyed) violations dict."""
    rel = py_file.relative_to(_WLA_REPO_ROOT).as_posix()
    out: dict = {"direct_sqlite_connect": []}
    try:
        source = py_file.read_text()
    except (OSError, UnicodeDecodeError):
        return out
    try:
        tree = _wla_ast.parse(source, filename=rel)
    except SyntaxError:
        return out
    for node in _wla_ast.walk(tree):
        if (
            rel not in _WLA_SQLITE_CONNECT_ALLOWLIST
            and isinstance(node, _wla_ast.Call)
            and isinstance(node.func, _wla_ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, _wla_ast.Name)
            and node.func.value.id == "sqlite3"
        ):
            out["direct_sqlite_connect"].append(node.lineno)
    return out


def _wla_scan_all() -> dict:
    """Scan src/ + scripts/ with mtime-keyed cache; return aggregated violations."""
    cache = _wla_load_cache()
    new_cache: dict = {}
    aggregate: dict = {"direct_sqlite_connect": []}
    for root in _WLA_SCAN_ROOTS:
        if not root.exists():
            continue
        for py_file in root.rglob("*.py"):
            try:
                mtime = py_file.stat().st_mtime
            except OSError:
                continue
            rel = py_file.relative_to(_WLA_REPO_ROOT).as_posix()
            cached = cache.get(rel)
            if cached and cached.get("mtime") == mtime:
                violations = cached["violations"]
            else:
                violations = _wla_scan_file(py_file)
            new_cache[rel] = {"mtime": mtime, "violations": violations}
            for kind, linenos in violations.items():
                for lineno in linenos:
                    aggregate.setdefault(kind, []).append(f"{rel}:{lineno}")
    _wla_save_cache(new_cache)
    return aggregate


def pytest_configure(config) -> None:
    """Run the writer-lock antibody once at session-configure time.

    Track A.3 posture (PR #92): FAIL-CI on any direct sqlite3.connect()
    outside the allowlist.  Advisory→fail-CI upgrade per Track A plan.
    Add to _WLA_SQLITE_CONNECT_ALLOWLIST with a cited reason tag to
    suppress a specific site during staged rollout.
    """
    if _wla_is_bypassed():
        config.issue_config_time_warning(
            UserWarning(
                "writer-lock antibody bypassed via "
                "ZEUS_DISABLE_WRITER_LOCK_ANTIBODY=1"
            ),
            stacklevel=1,
        )
        return
    aggregate = _wla_scan_all()
    findings = aggregate.get("direct_sqlite_connect", [])
    if findings:
        # Track A.3: fail-CI — any unallowlisted site is a hard error.
        allowlist_size = len(_WLA_SQLITE_CONNECT_ALLOWLIST)
        raise pytest.UsageError(
            f"writer-lock antibody (Track A.3 FAIL-CI): "
            f"{len(findings)} direct sqlite3.connect() site(s) outside "
            f"allowlist ({allowlist_size} entries). "
            f"Add to _WLA_SQLITE_CONNECT_ALLOWLIST in tests/conftest.py "
            f"with a cited reason tag (read_only / pending_track_a6 / etc). "
            f"Violations: {findings[:5]}"
            + (f" ... and {len(findings) - 5} more" if len(findings) > 5 else "")
        )
