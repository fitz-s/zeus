# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: /Users/leofitz/.claude/jobs/9ea6f95c/findings/f26_allowlist_audit.md
#                  F26 follow-up brief: migrate 38 (actual 42) CURRENT_REUSABLE entries
"""F26 allowlist migration antibody.

Asserts that every CURRENT_REUSABLE entry that was migrated from
tests/conftest._WLA_SQLITE_CONNECT_ALLOWLIST is now:
  1. Present in src.state.db_writer_lock.SQLITE_CONNECT_ALLOWLIST
  2. Absent from the conftest-only residual set (_WLA_SQLITE_CONNECT_ALLOWLIST
     before the union is applied).

Sed-break protocol: remove one entry from SQLITE_CONNECT_ALLOWLIST in
db_writer_lock.py → this test fails naming the missing entry; restore → pass.
"""

from __future__ import annotations

import pytest

from src.state.db_writer_lock import SQLITE_CONNECT_ALLOWLIST

# ---------------------------------------------------------------------------
# Canonical list of the 42 migrated CURRENT_REUSABLE entries.
# Source: conftest.py audit (F26, 2026-05-18).
# ---------------------------------------------------------------------------
MIGRATED_ENTRIES: frozenset[str] = frozenset(
    {
        # src/ daemon sites (CURRENT_REUSABLE)
        "src/ingest_main.py",
        "src/main.py",
        "src/observability/status_summary.py",
        "src/control/cli/promote_entry_forecast.py",
        # scripts: read-only, named in PR #86
        "scripts/attribution_drift_weekly.py",
        "scripts/audit_divergence_exit_counterfactual.py",
        "scripts/audit_realtime_pnl.py",
        "scripts/bridge_oracle_to_calibration.py",
        "scripts/build_correlation_matrix.py",
        "scripts/compare_diurnal_v1_v2.py",
        "scripts/deep_heartbeat.py",
        "scripts/healthcheck.py",
        "scripts/replay_parity.py",
        "scripts/venus_sensing_report.py",
        # scripts: additional read-only / ro-URI
        "scripts/audit_observation_instants_v2.py",
        "scripts/calibration_observation_weekly.py",
        "scripts/ddd_v1_v2_replay.py",
        "scripts/diagnose_low_high_alignment.py",
        "scripts/diagnose_truth_surfaces.py",
        "scripts/edge_observation_weekly.py",
        "scripts/generate_monthly_bounds.py",
        "scripts/learning_loop_observation_weekly.py",
        "scripts/check_schema_version.py",
        "scripts/check_data_pipeline_live_e2e.py",
        "scripts/check_forecast_live_ready.py",
        "scripts/check_live_order_e2e.py",
        "scripts/produce_activation_evidence.py",
        "scripts/replay_correctness_gate.py",
        "scripts/state_census.py",
        "scripts/topology_doctor_code_review_graph.py",
        "scripts/ws_poll_reaction_weekly.py",
        # scripts: mixed (already_guarded + read_only + in_memory_only)
        "scripts/repro_antibodies.py",
        # scripts: promote (RO inspect / RW with --commit)
        "scripts/promote_calibration_v2_stage_to_prod.py",
        "scripts/promote_platt_models_v2.py",
        "scripts/promote_calibration_pairs_v2.py",
        # scripts: operator-mediated K1 migrations
        "scripts/migrate_world_to_forecasts.py",
        "scripts/migrate_world_observations_to_forecasts.py",
        # scripts: CI hook + operator_invoked
        "scripts/check_table_registry_coherence.py",
        "scripts/drop_world_ghost_tables.py",
        "scripts/migrations/202605_add_redeem_operator_required_state.py",
        "scripts/migrations/__main__.py",
        "scripts/migrations/202605_position_current_bridge_required_trigger.py",
    }
)

# The conftest residual (STALE_REWRITE + QUARANTINED + canonical infra only).
# These must NOT be in the migrated set.
_CONFTEST_STALE_REWRITE: frozenset[str] = frozenset(
    {
        "src/data/market_scanner.py",
        "scripts/backfill_forecast_issue_time.py",
        "scripts/backfill_london_f_to_c_2026_05_08.py",
        "scripts/backfill_low_contract_window_evidence.py",
        "scripts/backfill_obs_v2.py",
        "scripts/obs_v2_live_tick.py",
        "scripts/backfill_ogimet_metar.py",
        "scripts/backfill_outcome_fact.py",
        "scripts/backfill_tigge_snapshot_p_raw_v2.py",
        "scripts/backfill_wu_daily_all.py",
        "scripts/cleanup_ghost_positions.py",
        "scripts/etl_forecasts_v2_from_legacy.py",
        "scripts/fill_obs_v2_dst_gaps.py",
        "scripts/fill_obs_v2_meteostat.py",
        "scripts/force_cycle_with_healthy_gates.py",
        "scripts/hko_ingest_tick.py",
        "scripts/ingest_grib_to_snapshots.py",
        "scripts/migrate_add_authority_column.py",
        "scripts/migrate_b070_control_overrides_to_history.py",
        "scripts/migrate_backtest_runs_lane_constraint_2026_05_07.py",
        "scripts/migrate_ensemble_snapshots_v2_add_ingest_backend.py",
        "scripts/migrate_forecasts_availability_provenance.py",
        "scripts/migrate_observations_k1.py",
        "scripts/nuke_rebuild_projections.py",
        "scripts/rebuild_calibration_pairs_canonical.py",
        "scripts/rebuild_calibration_pairs_v2.py",
        "scripts/rebuild_settlements.py",
        "scripts/reevaluate_readiness_2026_05_07.py",
        "scripts/refit_platt_v2.py",
        "scripts/_zeus_emergency_k2_obs_backfill_2026_05_10.py",
    }
)
_CONFTEST_QUARANTINED: frozenset[str] = frozenset({"scripts/verify_truth_surfaces.py"})
_CONFTEST_CANONICAL_INFRA: frozenset[str] = frozenset(
    {"src/state/db.py", "src/state/collateral_ledger.py"}
)

# The full conftest residual set (what should remain in conftest ONLY, not migrated).
_CONFTEST_RESIDUAL_ONLY = _CONFTEST_STALE_REWRITE | _CONFTEST_QUARANTINED | _CONFTEST_CANONICAL_INFRA


@pytest.mark.parametrize("entry", sorted(MIGRATED_ENTRIES))
def test_migrated_entry_present_in_production_allowlist(entry: str) -> None:
    """Each CURRENT_REUSABLE entry must exist in the production allowlist.

    Sed-break: delete the entry from db_writer_lock.SQLITE_CONNECT_ALLOWLIST
    → this parametrized case fails naming the missing entry.
    Restore → passes.
    """
    assert entry in SQLITE_CONNECT_ALLOWLIST, (
        f"MIGRATION REGRESSION: '{entry}' is listed as a CURRENT_REUSABLE "
        f"migrated entry but is absent from "
        f"src.state.db_writer_lock.SQLITE_CONNECT_ALLOWLIST. "
        f"Add it back to db_writer_lock.py with an appropriate reason tag."
    )


@pytest.mark.parametrize("entry", sorted(MIGRATED_ENTRIES))
def test_migrated_entry_absent_from_conftest_residual(entry: str) -> None:
    """Each migrated entry must NOT remain in the conftest-only residual set.

    This guards against accidental re-addition of a CURRENT_REUSABLE entry
    to the conftest STALE_REWRITE / QUARANTINED block.
    """
    assert entry not in _CONFTEST_RESIDUAL_ONLY, (
        f"MIGRATION REGRESSION: '{entry}' appears in the conftest-only residual "
        f"set. CURRENT_REUSABLE entries live in db_writer_lock.py only."
    )


def test_stale_rewrite_entries_not_in_production_allowlist() -> None:
    """STALE_REWRITE entries must remain in conftest residual only.

    They require per-entry rewrite decisions (Track A.6) and must not be
    silently promoted to the permanent production allowlist.
    """
    leaked = _CONFTEST_STALE_REWRITE & SQLITE_CONNECT_ALLOWLIST
    assert not leaked, (
        f"SCOPE CREEP: STALE_REWRITE entries found in the production allowlist: "
        f"{sorted(leaked)}. These need Track A.6 retrofit before promotion."
    )


def test_quarantined_entry_not_in_production_allowlist() -> None:
    """The QUARANTINED entry (verify_truth_surfaces.py) must not be promoted."""
    leaked = _CONFTEST_QUARANTINED & SQLITE_CONNECT_ALLOWLIST
    assert not leaked, (
        f"SCOPE CREEP: QUARANTINED entry found in production allowlist: "
        f"{sorted(leaked)}. This requires a non-mechanical rewrite."
    )
