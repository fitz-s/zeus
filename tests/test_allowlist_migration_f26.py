# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=2026-05-18
# Purpose: Antibody asserting F26 allowlist migration (Phase 1) and cleanup (Phase 2)
#          outcomes; catches two-truth drift between conftest residual and production allowlist.
# Reuse: Inspect F26_CLEANUP_PROMOTED / F26_CLEANUP_DROPPED sets against actual
#        db_writer_lock.SQLITE_CONNECT_ALLOWLIST before relying on test counts.
# Authority basis: /Users/leofitz/.claude/jobs/9ea6f95c/findings/f26_allowlist_audit.md
#                  F26 follow-up brief: migrate 38 (actual 42) CURRENT_REUSABLE entries
#                  F26 cleanup brief (2026-05-18): resolve 29 STALE_REWRITE + 1 QUARANTINED
"""F26 allowlist migration + cleanup antibody.

Two phases covered:

Phase 1 (PR #157): 42 CURRENT_REUSABLE entries migrated from conftest residual
  to src.state.db_writer_lock.SQLITE_CONNECT_ALLOWLIST.

Phase 2 (this PR): 29 STALE_REWRITE + 1 QUARANTINED entries resolved:
  - 21 already_guarded backfill/ingest scripts promoted to production allowlist
  - 5 operator_invoked migration scripts promoted to production allowlist
  - 1 script retrofitted with db_writer_lock wrap then promoted
  - 1 one-shot operator script promoted
  - 1 QUARANTINED (verify_truth_surfaces) promoted as read_only (SELECT-only, no writes)
  - 1 dropped (_zeus_emergency_k2_obs_backfill_2026_05_10.py — file deleted)
  - 2 daemon src/ sites remain in residual pending Track A.6 (#246)
  Total: 29 promoted + 1 dropped = 30 entries resolved (F26_CLEANUP_PROMOTED has 29 entries)

Sed-break protocols:
  A. Remove one entry from SQLITE_CONNECT_ALLOWLIST in db_writer_lock.py
     → test_migrated_entry_present_in_production_allowlist fails naming the
     missing entry; restore → pass.
  B. Re-add a migrated entry to _WLA_RESIDUAL_ALLOWLIST in tests/conftest.py
     → test_migrated_entry_absent_from_conftest_residual fails naming the
     re-added entry; restore → pass. This is the two-truth drift Codex
     called out on PR #157; the antibody must catch it.
  C. Add a residual entry to SQLITE_CONNECT_ALLOWLIST in db_writer_lock.py
     → test_residual_does_not_intersect_production_allowlist fails; restore
     → pass.
  D. Re-add a dropped entry to SQLITE_CONNECT_ALLOWLIST in db_writer_lock.py
     → test_dropped_entry_absent_from_production_allowlist fails; restore → pass.
"""

from __future__ import annotations

import pytest

from src.state.db_writer_lock import SQLITE_CONNECT_ALLOWLIST
from tests.conftest import _WLA_RESIDUAL_ALLOWLIST

# ---------------------------------------------------------------------------
# Canonical list of the 42 migrated CURRENT_REUSABLE entries.
# Source: conftest.py audit (F26, 2026-05-18).
#
# `_WLA_RESIDUAL_ALLOWLIST` (imported above) is the SINGLE source of truth
# for the conftest-only residual (STALE_REWRITE + QUARANTINED + canonical
# infra). Tests read it directly rather than maintaining a duplicate copy
# here — that is the two-truth allowlist drift this F26 antibody is
# designed to catch (PR #157 review feedback).
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
    to the conftest STALE_REWRITE / QUARANTINED block. The residual set is
    read DIRECTLY from conftest._WLA_RESIDUAL_ALLOWLIST so any re-addition
    in conftest is observed without a parallel update here.
    """
    assert entry not in _WLA_RESIDUAL_ALLOWLIST, (
        f"MIGRATION REGRESSION: '{entry}' appears in the conftest-only residual "
        f"set (_WLA_RESIDUAL_ALLOWLIST in tests/conftest.py). "
        f"CURRENT_REUSABLE entries live in db_writer_lock.py only."
    )


def test_residual_does_not_intersect_production_allowlist() -> None:
    """Conftest residual (pending Track A.6 daemon sites) must not intersect
    with the production allowlist.

    Residual entries are daemon src/ sites pending Track A.6 retrofit.
    They must not be silently promoted into the production allowlist without
    a principled per-entry decision (F26 cleanup process).
    """
    leaked = _WLA_RESIDUAL_ALLOWLIST & SQLITE_CONNECT_ALLOWLIST
    assert not leaked, (
        f"SCOPE CREEP: conftest residual entries found in the production "
        f"allowlist: {sorted(leaked)}. Residual entries are pending Track A.6 "
        f"retrofit and require a principled decision before promotion to the "
        f"production allowlist."
    )


# ---------------------------------------------------------------------------
# F26 cleanup (2026-05-18): STALE_REWRITE + QUARANTINED entries resolved.
# 29 entries promoted to production allowlist, 1 dropped (file deleted).
# ---------------------------------------------------------------------------

# Entries promoted from conftest residual to production allowlist in F26 cleanup.
F26_CLEANUP_PROMOTED: frozenset[str] = frozenset(
    {
        # already_guarded backfill/ingest scripts
        "scripts/backfill_forecast_issue_time.py",
        "scripts/backfill_london_f_to_c_2026_05_08.py",
        "scripts/backfill_low_contract_window_evidence.py",
        "scripts/backfill_obs_v2.py",
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
        "scripts/nuke_rebuild_projections.py",
        "scripts/obs_v2_live_tick.py",
        "scripts/rebuild_calibration_pairs_canonical.py",
        "scripts/rebuild_calibration_pairs_v2.py",
        "scripts/rebuild_settlements.py",
        "scripts/refit_platt_v2.py",
        # operator_invoked migration scripts (already_guarded)
        "scripts/migrate_add_authority_column.py",
        "scripts/migrate_b070_control_overrides_to_history.py",
        "scripts/migrate_ensemble_snapshots_v2_add_ingest_backend.py",
        "scripts/migrate_forecasts_availability_provenance.py",
        "scripts/migrate_observations_k1.py",
        # retrofitted: db_writer_lock wrap added in F26 cleanup
        "scripts/migrate_backtest_runs_lane_constraint_2026_05_07.py",
        # one-shot idempotent operator script
        "scripts/reevaluate_readiness_2026_05_07.py",
        # QUARANTINED resolved: read_only (0 DML writes; all connects SELECT-only;
        # RISK_DB/DEFAULT_TRADE_DB/SHARED_DB switched to mode=ro URIs in F26 cleanup)
        "scripts/verify_truth_surfaces.py",
    }
)

# Entries dropped entirely in F26 cleanup (file deleted post-run, no longer in repo).
F26_CLEANUP_DROPPED: frozenset[str] = frozenset(
    {
        "scripts/_zeus_emergency_k2_obs_backfill_2026_05_10.py",
    }
)


@pytest.mark.parametrize("entry", sorted(F26_CLEANUP_PROMOTED))
def test_cleanup_promoted_entry_present_in_production_allowlist(entry: str) -> None:
    """Each F26-cleanup-promoted entry must exist in the production allowlist.

    Sed-break: delete the entry from db_writer_lock.SQLITE_CONNECT_ALLOWLIST
    → this parametrized case fails naming the missing entry.
    Restore → passes.
    """
    assert entry in SQLITE_CONNECT_ALLOWLIST, (
        f"CLEANUP REGRESSION: '{entry}' was resolved from conftest residual "
        f"in F26 cleanup but is absent from "
        f"src.state.db_writer_lock.SQLITE_CONNECT_ALLOWLIST. "
        f"Add it back to db_writer_lock.py with its reason tag."
    )


@pytest.mark.parametrize("entry", sorted(F26_CLEANUP_PROMOTED))
def test_cleanup_promoted_entry_absent_from_conftest_residual(entry: str) -> None:
    """Each F26-cleanup-promoted entry must NOT remain in the conftest residual.

    Guards against re-addition of a resolved entry to the conftest residual set.
    The residual set is read DIRECTLY from conftest._WLA_RESIDUAL_ALLOWLIST so
    any re-addition is observed without a parallel update here.
    """
    assert entry not in _WLA_RESIDUAL_ALLOWLIST, (
        f"CLEANUP REGRESSION: '{entry}' appears in the conftest-only residual "
        f"set (_WLA_RESIDUAL_ALLOWLIST in tests/conftest.py). "
        f"This entry was resolved in F26 cleanup; it must only live in "
        f"db_writer_lock.py."
    )


@pytest.mark.parametrize("entry", sorted(F26_CLEANUP_DROPPED))
def test_dropped_entry_absent_from_production_allowlist(entry: str) -> None:
    """Each dropped entry must NOT appear in the production allowlist.

    Dropped entries correspond to files that were deleted from the repo.
    Adding them back to the production allowlist would be a scope-creep
    regression (allowlisting a non-existent file).

    Sed-break: add the entry to db_writer_lock.SQLITE_CONNECT_ALLOWLIST
    → this parametrized case fails; restore → passes.
    """
    assert entry not in SQLITE_CONNECT_ALLOWLIST, (
        f"SCOPE CREEP: '{entry}' is a dropped entry (file deleted) but "
        f"appears in src.state.db_writer_lock.SQLITE_CONNECT_ALLOWLIST. "
        f"Remove it from the production allowlist."
    )


@pytest.mark.parametrize("entry", sorted(F26_CLEANUP_DROPPED))
def test_dropped_entry_absent_from_conftest_residual(entry: str) -> None:
    """Each dropped entry must NOT appear in the conftest residual.

    Dropped entries correspond to deleted files; keeping them in the residual
    is dead weight that weakens the 'every residual entry has a live file'
    invariant.
    """
    assert entry not in _WLA_RESIDUAL_ALLOWLIST, (
        f"DEAD ENTRY: '{entry}' is a dropped entry (file deleted) but "
        f"remains in the conftest residual set. Remove it."
    )
