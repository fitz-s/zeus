# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: /Users/leofitz/.claude/jobs/9ea6f95c/findings/f26_allowlist_audit.md
#                  F26 follow-up brief: migrate 38 (actual 42) CURRENT_REUSABLE entries
"""F26 allowlist migration antibody.

Asserts that every CURRENT_REUSABLE entry that was migrated from
tests/conftest._WLA_RESIDUAL_ALLOWLIST is now:
  1. Present in src.state.db_writer_lock.SQLITE_CONNECT_ALLOWLIST
  2. Absent from the conftest-only residual set
     (tests/conftest._WLA_RESIDUAL_ALLOWLIST, imported directly so any
     re-addition in conftest is observed without a parallel update here).

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
    """Conftest residual (STALE_REWRITE + QUARANTINED + canonical infra) must
    not intersect with the production allowlist.

    STALE_REWRITE entries need Track A.6 per-entry rewrite decisions; the
    QUARANTINED entry needs a non-mechanical rewrite; canonical infra
    intentionally lives outside db_writer_lock. None should be silently
    promoted into the production allowlist.
    """
    leaked = _WLA_RESIDUAL_ALLOWLIST & SQLITE_CONNECT_ALLOWLIST
    assert not leaked, (
        f"SCOPE CREEP: conftest residual entries found in the production "
        f"allowlist: {sorted(leaked)}. STALE_REWRITE entries need Track A.6 "
        f"retrofit; QUARANTINED needs non-mechanical rewrite; canonical infra "
        f"is by design outside db_writer_lock."
    )
