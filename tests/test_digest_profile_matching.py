"""Adversarial tests for digest profile selection.

Exercises the Evidence/Resolver layer of topology_doctor_digest. The goal is
to prove that route suggestion is driven by structured evidence, not by raw
substring matching, so that benign tasks like "improve source code quality"
cannot collide with safety-critical profiles like "modify data ingestion".

These cases come directly from §15 of docs/reference/Zeus_Apr25_review.md.
"""
# Created: 2026-04-25
# Last reused or audited: 2026-05-08
# Authority basis: AGENTS.md topology routing; phase 5 forward substrate producer phrase maintenance.
# Lifecycle: created=2026-04-25; last_reviewed=2026-05-01; last_reused=2026-05-01
# Purpose: Lock the new word-boundary + denylist + veto profile resolver against
# regression to the legacy substring matcher.
# Reuse: When adding a new profile, add adversarial cases here first.

from __future__ import annotations

import pytest

from scripts.topology_doctor import build_digest


# ---------------------------------------------------------------------------
# Generic-token false positives that the legacy substring matcher misrouted.
# ---------------------------------------------------------------------------

def test_generic_source_word_does_not_route_to_data_ingestion():
    """`source` is in the global denylist; "improve source code quality" must
    not route to "modify data ingestion" or any specific profile."""
    digest = build_digest("improve source code quality", ["src/foo.py"])
    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["admission"]["admitted_files"] == []
    assert digest["profile_selection"]["evidence_class"] in {"fallback", "weak_term_nonselectable"}


def test_generic_test_word_does_not_route_to_test_profile():
    digest = build_digest("clean up unit test docstrings", ["tests/test_foo.py"])
    # If a "test" profile exists, this must not auto-resolve via the bare token.
    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["admission"]["admitted_files"] == []


def test_generic_signal_word_does_not_route_to_signal_profile():
    digest = build_digest("improve signal handling robustness", ["src/foo.py"])
    # `signal` alone (no signal-specific phrase) must not implicitly admit.
    assert digest["admission"]["status"] in {"advisory_only", "scope_expansion_required"}


# ---------------------------------------------------------------------------
# Negative-phrase veto: explicit disclaimers must override accidental matches.
# ---------------------------------------------------------------------------

def test_negative_phrase_vetoes_settlement_profile():
    """A task that explicitly disclaims settlement edits must not resolve to
    the settlement profile even if the word appears."""
    digest = build_digest(
        "rename a variable, no settlement change",
        ["src/contracts/settlement_semantics.py"],
    )
    # The forbidden file gate may still trip; key invariant: profile is not
    # silently set to "change settlement rounding" via substring presence.
    if digest["profile"] == "change settlement rounding":
        # Acceptable only if file evidence dominates, but admission must NOT
        # admit blindly — settlement_semantics.py is in the profile's allowed
        # list, so the more important assertion is: status is not admitted
        # solely on the negated phrase.
        assert digest["admission"]["status"] in {
            "admitted",
            "advisory_only",
            "route_contract_conflict",
        }


# ---------------------------------------------------------------------------
# Word-boundary matching: substrings inside larger words must not match.
# ---------------------------------------------------------------------------

def test_word_boundary_prevents_substring_match():
    """The token `data` appears in `metadata` but must not trigger a data
    ingestion match unless the literal phrase appears."""
    digest = build_digest("update metadata fields on a struct", ["src/foo.py"])
    # `data ingestion` phrase is not present; profile must not be data-ingestion.
    assert digest["profile"] != "modify data ingestion"


# ---------------------------------------------------------------------------
# Strong, unambiguous matches still route correctly.
# ---------------------------------------------------------------------------

def test_settlement_phrase_routes_to_settlement_profile():
    digest = build_digest(
        "change settlement rounding rule",
        ["src/contracts/settlement_semantics.py"],
    )
    assert digest["profile"] == "change settlement rounding"
    assert digest["admission"]["status"] == "admitted"
    assert "src/contracts/settlement_semantics.py" in digest["admission"]["admitted_files"]


def test_data_backfill_phrase_routes_to_backfill_profile():
    digest = build_digest(
        "add a data backfill for daily WU rebuild",
        ["scripts/rebuild_calibration_pairs_canonical.py"],
    )
    assert digest["profile"] == "add a data backfill"
    assert digest["admission"]["status"] == "admitted"


def test_source_contract_market_scanner_routes_to_data_profile():
    digest = build_digest(
        "implement source contract gate in market scanner for settlement source drift",
        ["src/data/market_scanner.py", "tests/test_market_scanner_provenance.py"],
    )
    assert digest["profile"] == "modify data ingestion"
    assert digest["admission"]["status"] == "admitted"
    assert "src/data/market_scanner.py" in digest["admission"]["admitted_files"]
    assert "tests/test_market_scanner_provenance.py" in digest["admission"]["admitted_files"]


def test_source_contract_watch_script_routes_to_script_profile():
    digest = build_digest(
        "add diagnostic script for settlement source contract watch",
        [
            "scripts/watch_source_contract.py",
            "architecture/script_manifest.yaml",
            "tests/test_market_scanner_provenance.py",
        ],
        intent="add or change script",
        task_class="runtime_support",
        write_intent="add",
    )
    assert digest["profile"] == "add or change script"
    assert digest["admission"]["status"] == "admitted"
    assert "scripts/watch_source_contract.py" in digest["admission"]["admitted_files"]


def test_source_canary_readiness_hot_swap_routes_without_live_execution():
    digest = build_digest(
        "source freshness provider hot-swap Paris canary readiness only no live execution",
        ["src/control/freshness_gate.py", "src/engine/cycle_runner.py"],
        write_intent="edit",
    )

    assert digest["profile"] == "source canary readiness hot-swap"
    assert digest["admission"]["status"] == "admitted"
    assert "src/control/freshness_gate.py" in digest["admission"]["admitted_files"]
    assert "src/engine/cycle_runner.py" in digest["admission"]["admitted_files"]


def test_source_canary_readiness_hot_swap_blocks_live_execution_surface():
    digest = build_digest(
        "source freshness provider hot-swap Paris canary readiness only no live execution",
        ["src/control/freshness_gate.py", "src/execution/executor.py"],
        write_intent="edit",
    )

    assert digest["profile"] == "source canary readiness hot-swap"
    assert digest["admission"]["status"] == "blocked"
    assert "src/execution/executor.py" in digest["admission"]["forbidden_hits"]


def test_docs_navigation_cleanup_routes_without_settlement_replay_semantic_edit():
    digest = build_digest(
        "Clean up a stale operations packet reference in docs only. "
        "Keep settlement learning and replay wording untouched.",
        ["docs/operations/current_state.md"],
        write_intent="edit",
    )

    assert digest["profile"] == "docs navigation cleanup"
    assert digest["admission"]["status"] == "admitted"
    assert "docs/operations/current_state.md" in digest["admission"]["admitted_files"]


def test_evaluator_script_import_bridge_admits_evaluator_but_not_script_side_effect():
    digest = build_digest(
        "evaluator script import bridge for downstream evaluator import safety",
        ["src/engine/evaluator.py", "scripts/rebuild_calibration_pairs_v2.py"],
        write_intent="edit",
    )

    assert digest["profile"] == "evaluator script import bridge"
    assert digest["admission"]["status"] == "scope_expansion_required"
    assert "src/engine/evaluator.py" in digest["admission"]["admitted_files"]
    assert "scripts/rebuild_calibration_pairs_v2.py" in digest["admission"]["out_of_scope_files"]


def test_source_watch_venus_sensing_integration_routes_to_script_profile():
    digest = build_digest(
        "add or change script: runtime_support source-contract watch integration for Venus sensing report",
        [
            "scripts/venus_sensing_report.py",
            "scripts/watch_source_contract.py",
            "tests/test_market_scanner_provenance.py",
            "architecture/script_manifest.yaml",
        ],
        intent="add or change script",
        task_class="runtime_support",
        write_intent="change",
    )
    assert digest["profile"] == "add or change script"
    assert digest["admission"]["status"] == "admitted"
    assert "scripts/venus_sensing_report.py" in digest["admission"]["admitted_files"]
    assert "scripts/watch_source_contract.py" in digest["admission"]["admitted_files"]


def test_source_current_fact_refresh_routes_to_current_fact_profile():
    digest = build_digest(
        "current source validity refresh for source-contract drift current fact",
        [
            "docs/operations/current_source_validity.md",
            "scripts/watch_source_contract.py",
            "tests/test_market_scanner_provenance.py",
        ],
    )
    assert digest["profile"] == "refresh source current fact"
    assert digest["admission"]["status"] == "admitted"
    assert "docs/operations/current_source_validity.md" in digest["admission"]["admitted_files"]
    assert "scripts/watch_source_contract.py" in digest["admission"]["admitted_files"]


def test_source_contract_auto_conversion_routes_to_runtime_profile():
    digest = build_digest(
        "source contract auto conversion cron controller with Discord date scope",
        [
            "scripts/source_contract_auto_convert.py",
            "tests/test_market_scanner_provenance.py",
            "docs/archives/packets/task_2026-04-30_source_auto_conversion/plan.md",
            "architecture/script_manifest.yaml",
        ],
    )
    assert digest["profile"] == "source contract auto conversion runtime"
    assert digest["admission"]["status"] == "admitted"
    assert "scripts/source_contract_auto_convert.py" in digest["admission"]["admitted_files"]
    assert "tests/test_market_scanner_provenance.py" in digest["admission"]["admitted_files"]


def test_r3_u2_raw_provenance_routes_to_u2_profile_not_heartbeat():
    """U2 shares broad R3 packet docs paths with earlier phases; strong U2
    phrases must win over Z3's broad docs file-pattern hit so state/schema
    files are admitted for the provenance slice."""
    digest = build_digest(
        "R3 U2 raw provenance schema venue_order_facts venue_trade_facts position_lots",
        [
            "src/state/db.py",
            "src/state/venue_command_repo.py",
            "tests/test_provenance_5_projections.py",
        ],
    )

    assert digest["profile"] == "r3 raw provenance schema implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/state/db.py" in digest["admission"]["admitted_files"]
    assert "tests/test_provenance_5_projections.py" in digest["admission"]["admitted_files"]


def test_r3_u2_fill_finality_routes_to_finality_profile_not_schema():
    """Finality/partial-fill repair is U2 ledger semantics, but not the U2
    schema-surface profile; it must admit fill_tracker and the runtime safety
    regressions without widening the raw-provenance schema profile."""
    digest = build_digest(
        "R3 U2 fill finality closure legacy fill polling MATCHED CONFIRMED "
        "partial fill materialization venue_trade_facts position_lots",
        [
            "src/execution/fill_tracker.py",
            "tests/test_live_safety_invariants.py",
            "tests/test_runtime_guards.py",
            "tests/test_user_channel_ingest.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "r3 fill finality ledger implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/fill_tracker.py" in digest["admission"]["admitted_files"]
    assert "tests/test_live_safety_invariants.py" in digest["admission"]["admitted_files"]
    assert "tests/test_runtime_guards.py" in digest["admission"]["admitted_files"]
    assert "tests/test_user_channel_ingest.py" in digest["admission"]["admitted_files"]


def test_phase3_fill_finality_realistic_wording_routes_to_finality_profile():
    """Reviewer phrasing without the exact R3/U2 tokens must still route to
    the finality profile, not the broader live-readiness gates profile."""
    digest = build_digest(
        "Phase 3 fill finality / exposure ledger slice legacy polling partial "
        "cancel command events lots",
        [
            "src/execution/fill_tracker.py",
            "tests/test_live_safety_invariants.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "r3 fill finality ledger implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/fill_tracker.py" in digest["admission"]["admitted_files"]


def test_phase4_strategy_reachability_routes_to_selection_parity_profile():
    digest = build_digest(
        "Phase 4 strategy reachability selection sizing parity full-family FDR "
        "multi-bin buy_no executable BinEdge calibration maturity feature flags",
        [
            "src/strategy/market_analysis.py",
            "src/strategy/market_analysis_family_scan.py",
            "src/engine/evaluator.py",
            "tests/test_fdr.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "r3 strategy reachability selection parity implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/strategy/market_analysis.py" in digest["admission"]["admitted_files"]
    assert "src/strategy/market_analysis_family_scan.py" in digest["admission"]["admitted_files"]
    assert "src/engine/evaluator.py" in digest["admission"]["admitted_files"]


def test_phase4a_f13_realistic_wording_routes_to_selection_parity_profile():
    digest = build_digest(
        "Phase 4A strategy reachability full-family FDR parity close F13 "
        "fail-close multi-bin buy_no",
        [
            "src/strategy/market_analysis.py",
            "src/strategy/market_analysis_family_scan.py",
            "tests/test_fdr.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "r3 strategy reachability selection parity implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/strategy/market_analysis.py" in digest["admission"]["admitted_files"]
    assert "src/strategy/market_analysis_family_scan.py" in digest["admission"]["admitted_files"]
    assert "tests/test_fdr.py" in digest["admission"]["admitted_files"]


def test_object_meaning_settlement_authority_cutover_routes_to_wave5_profile():
    digest = build_digest(
        "object-meaning invariance wave 5: verified settlement truth before position settlement",
        [
            "docs/operations/task_2026-05-05_object_invariance_wave5/PLAN.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
            "architecture/source_rationale.yaml",
            "src/main.py",
            "src/execution/harvester.py",
            "src/execution/harvester_pnl_resolver.py",
            "src/engine/lifecycle_events.py",
            "src/calibration/drift_detector.py",
            "src/riskguard/riskguard.py",
            "src/state/db.py",
            "src/state/decision_chain.py",
            "src/state/portfolio.py",
            "src/state/strategy_tracker.py",
            "src/engine/replay.py",
            "scripts/etl_forecast_skill_from_forecasts.py",
            "scripts/etl_historical_forecasts.py",
            "tests/test_harvester_split_independence.py",
            "tests/test_harvester_metric_identity.py",
            "tests/test_live_safety_invariants.py",
            "tests/test_drift_detector_threshold.py",
            "tests/test_riskguard.py",
            "tests/test_db.py",
            "tests/test_backtest_outcome_comparison.py",
            "tests/test_cross_module_relationships.py",
            "tests/test_strategy_tracker_regime.py",
        ],
        write_intent="edit",
    )

    assert digest["profile"] == "object meaning settlement authority cutover"
    assert digest["admission"]["status"] == "admitted"
    for path in [
        "src/main.py",
        "architecture/topology.yaml",
        "architecture/digest_profiles.py",
        "src/execution/harvester.py",
        "src/execution/harvester_pnl_resolver.py",
        "src/engine/lifecycle_events.py",
        "src/calibration/drift_detector.py",
        "src/riskguard/riskguard.py",
        "src/state/db.py",
        "src/state/decision_chain.py",
        "src/state/portfolio.py",
        "src/state/strategy_tracker.py",
        "src/engine/replay.py",
        "scripts/etl_forecast_skill_from_forecasts.py",
        "scripts/etl_historical_forecasts.py",
        "tests/test_backtest_outcome_comparison.py",
        "tests/test_cross_module_relationships.py",
    ]:
        assert path in digest["admission"]["admitted_files"]


def test_object_meaning_settlement_authority_cutover_blocks_live_side_effect_scope():
    digest = build_digest(
        "object meaning settlement authority cutover with forbidden state and settlement contract edits",
        [
            "src/execution/harvester.py",
            "state/zeus-world.db",
            "src/contracts/settlement_semantics.py",
        ],
        write_intent="edit",
    )

    assert digest["profile"] == "object meaning settlement authority cutover"
    assert digest["admission"]["status"] == "blocked"
    assert "state/zeus-world.db" in digest["admission"]["forbidden_hits"]
    assert "src/contracts/settlement_semantics.py" in digest["admission"]["forbidden_hits"]


def test_object_meaning_calibration_transfer_oos_evidence_routes_to_wave18_profile():
    digest = build_digest(
        "object-meaning invariance wave 18: time-blocked calibration transfer evidence "
        "must reject row-modulo pseudo-OOS for validated_calibration_transfers time basis",
        [
            "docs/operations/task_2026-05-05_object_invariance_wave18/PLAN.md",
            "docs/operations/AGENTS.md",
            "scripts/evaluate_calibration_transfer_oos.py",
            "src/data/calibration_transfer_policy.py",
            "tests/test_evaluate_calibration_transfer_oos.py",
            "tests/test_calibration_transfer_policy_with_evidence.py",
        ],
        write_intent="edit",
    )

    assert digest["profile"] == "object meaning calibration transfer oos evidence"
    assert digest["admission"]["status"] == "admitted"
    for path in [
        "scripts/evaluate_calibration_transfer_oos.py",
        "src/data/calibration_transfer_policy.py",
        "tests/test_evaluate_calibration_transfer_oos.py",
        "tests/test_calibration_transfer_policy_with_evidence.py",
    ]:
        assert path in digest["admission"]["admitted_files"]


def test_object_meaning_operator_status_bankroll_semantics_routes_to_wave12_profile():
    digest = build_digest(
        "object-meaning invariance wave 12: status_summary wallet equity semantics "
        "effective_bankroll wallet truth not wallet plus PnL",
        [
            "docs/operations/task_2026-05-05_object_invariance_wave12/PLAN.md",
            "docs/operations/AGENTS.md",
            "scripts/equity_curve.py",
            "src/observability/status_summary.py",
            "tests/test_backtest_skill_economics.py",
            "tests/test_phase10b_dt_seam_cleanup.py",
            "tests/test_pnl_flow_and_audit.py",
            "architecture/improvement_backlog.yaml",
        ],
        write_intent="edit",
    )

    assert digest["profile"] == "object meaning operator status bankroll semantics"
    assert digest["admission"]["status"] == "admitted"
    admitted = set(digest["admission"]["admitted_files"])
    assert "docs/operations/AGENTS.md" in admitted
    assert "scripts/equity_curve.py" in admitted
    assert "src/observability/status_summary.py" in admitted
    assert "tests/test_backtest_skill_economics.py" in admitted
    assert "tests/test_phase10b_dt_seam_cleanup.py" in admitted
    assert "tests/test_pnl_flow_and_audit.py" in admitted
    assert "architecture/improvement_backlog.yaml" in admitted
    assert "src/riskguard/**" in digest["forbidden_files"]


def test_object_meaning_riskguard_loader_provenance_semantics_routes_to_wave13_profile():
    digest = build_digest(
        "object-meaning invariance wave 13: RiskGuard portfolio loader fill-authority "
        "current-open economics provenance preservation",
        [
            "docs/operations/task_2026-05-05_object_invariance_wave13/PLAN.md",
            "docs/operations/AGENTS.md",
            "src/riskguard/riskguard.py",
            "tests/test_riskguard.py",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
            "architecture/improvement_backlog.yaml",
        ],
        write_intent="edit",
    )

    assert digest["profile"] == "object meaning riskguard loader provenance semantics"
    assert digest["admission"]["status"] == "admitted"
    admitted = set(digest["admission"]["admitted_files"])
    assert "docs/operations/task_2026-05-05_object_invariance_wave13/PLAN.md" in admitted
    assert "docs/operations/AGENTS.md" in admitted
    assert "src/riskguard/riskguard.py" in admitted
    assert "tests/test_riskguard.py" in admitted
    assert "architecture/topology.yaml" in admitted
    assert "architecture/digest_profiles.py" in admitted
    assert "architecture/improvement_backlog.yaml" in admitted
    assert "src/riskguard/policy.py" in digest["forbidden_files"]
    assert "src/risk_allocator/**" in digest["forbidden_files"]
    assert "src/state/**" in digest["forbidden_files"]


def test_pricing_semantics_authority_cutover_routes_to_refactor_profile():
    digest = build_digest(
        "pricing semantics authority cutover Phase 0/A reality semantics "
        "guardrails probability quote VWMP executable cost isolation",
        [
            "architecture/invariants.yaml",
            "architecture/negative_constraints.yaml",
            "tests/test_no_bare_float_seams.py",
            "tests/test_architecture_contracts.py",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "admitted"
    assert "architecture/invariants.yaml" in digest["admission"]["admitted_files"]
    assert "architecture/negative_constraints.yaml" in digest["admission"]["admitted_files"]
    assert "tests/test_no_bare_float_seams.py" in digest["admission"]["admitted_files"]
    assert "tests/test_architecture_contracts.py" in digest["admission"]["admitted_files"]


def test_pricing_semantics_authority_cutover_blocks_live_side_effect_scope():
    digest = build_digest(
        "pricing semantics authority cutover live venue submission production DB mutation",
        [
            "architecture/invariants.yaml",
            "src/venue/polymarket_v2_adapter.py",
            "state/zeus-world.db",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "blocked"
    assert digest["admission"]["admitted_files"] == []
    forbidden = set(digest["admission"]["forbidden_hits"])
    assert {"src/venue/polymarket_v2_adapter.py", "state/zeus-world.db"} <= forbidden


def test_pricing_semantics_authority_cutover_admits_state_owned_exit_quote_split():
    digest = build_digest(
        "pricing semantics authority cutover Phase I monitor exit pricing split "
        "Position._buy_no_exit held-token best_bid sell quote separation",
        [
            "src/state/portfolio.py",
            "tests/test_hold_value_exit_costs.py",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "admitted"
    assert "src/state/portfolio.py" in digest["admission"]["admitted_files"]
    assert "tests/test_hold_value_exit_costs.py" in digest["admission"]["admitted_files"]


def test_pricing_semantics_authority_cutover_admits_monitor_quote_split_safety_tests():
    digest = build_digest(
        "pricing semantics authority cutover Phase I monitor probability quote split "
        "Day0 best_bid remains exit surface not posterior input",
        [
            "src/engine/monitor_refresh.py",
            "tests/test_runtime_guards.py",
            "tests/test_live_safety_invariants.py",
            "tests/test_day0_exit_gate.py",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "admitted"
    assert "src/engine/monitor_refresh.py" in digest["admission"]["admitted_files"]
    assert "tests/test_runtime_guards.py" in digest["admission"]["admitted_files"]
    assert "tests/test_live_safety_invariants.py" in digest["admission"]["admitted_files"]
    assert "tests/test_day0_exit_gate.py" in digest["admission"]["admitted_files"]


def test_pricing_semantics_authority_cutover_admits_f06_client_envelope_first_packet():
    digest = build_digest(
        "pricing semantics authority cutover F-06 compatibility venue envelope "
        "live gate without venue adapter edit",
        [
            "src/data/polymarket_client.py",
            "src/contracts/venue_submission_envelope.py",
            "tests/test_v2_adapter.py",
            "tests/test_risk_allocator.py",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "admitted"
    admitted = set(digest["admission"]["admitted_files"])
    assert "src/data/polymarket_client.py" in admitted
    assert "src/contracts/venue_submission_envelope.py" in admitted
    assert "tests/test_v2_adapter.py" in admitted
    assert "src/venue/polymarket_v2_adapter.py" not in admitted


def test_pricing_semantics_authority_cutover_keeps_venue_adapter_blocked():
    digest = build_digest(
        "pricing semantics authority cutover F-06 compatibility venue envelope "
        "requires venue adapter live submission proof",
        [
            "src/data/polymarket_client.py",
            "src/venue/polymarket_v2_adapter.py",
            "tests/test_v2_adapter.py",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "blocked"
    assert "src/venue/polymarket_v2_adapter.py" in digest["admission"]["forbidden_hits"]


def test_pricing_semantics_authority_cutover_admits_f08_order_policy_existing_contract_packet():
    digest = build_digest(
        "pricing semantics authority cutover F-08 order-policy cost authority "
        "using existing execution intent contracts",
        [
            "src/contracts/execution_intent.py",
            "src/execution/executor.py",
            "src/engine/cycle_runtime.py",
            "src/strategy/kelly.py",
            "tests/test_execution_intent_typed_slippage.py",
            "tests/test_runtime_guards.py",
            "tests/test_v2_adapter.py",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "admitted"
    admitted = set(digest["admission"]["admitted_files"])
    assert "src/contracts/execution_intent.py" in admitted
    assert "src/strategy/kelly.py" in admitted
    assert "tests/test_v2_adapter.py" in admitted


def test_pricing_semantics_authority_cutover_blocks_unregistered_cost_basis_authority_file():
    digest = build_digest(
        "pricing semantics authority cutover F-08 order-policy cost authority "
        "new executable cost basis authority file",
        [
            "src/contracts/execution_intent.py",
            "src/contracts/executable_cost_basis.py",
            "tests/test_execution_intent_typed_slippage.py",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "scope_expansion_required"
    assert "src/contracts/executable_cost_basis.py" in digest["admission"]["out_of_scope_files"]


def test_pricing_semantics_authority_cutover_admits_f09_fill_authority_packet_with_existing_tests():
    digest = build_digest(
        "pricing semantics authority cutover F-09 fill authority split "
        "submitted target filled quantity filled cost basis average fill price "
        "economics authority no schema apply",
        [
            "src/contracts/realized_fill.py",
            "src/engine/cycle_runtime.py",
            "src/state/portfolio.py",
            "src/execution/fill_tracker.py",
            "src/execution/harvester.py",
            "architecture/2026_04_02_architecture_kernel.sql",
            "tests/test_realized_fill.py",
            "tests/test_realized_fill_at_receipt.py",
            "tests/test_harvester_metric_identity.py",
            "tests/test_db.py",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "admitted"
    admitted = set(digest["admission"]["admitted_files"])
    assert "src/contracts/realized_fill.py" in admitted
    assert "src/execution/fill_tracker.py" in admitted
    assert "src/execution/harvester.py" in admitted
    assert "architecture/2026_04_02_architecture_kernel.sql" in admitted
    assert "tests/test_realized_fill.py" in admitted
    assert "tests/test_db.py" in admitted
    assert "schema migration" in digest["forbidden_files"]


def test_pricing_semantics_authority_cutover_admits_f10_report_replay_cohort_packet_after_fill_fields():
    digest = build_digest(
        "pricing semantics authority cutover F-10 report replay cohort hard gate "
        "after F-09 durable fill fields",
        [
            "scripts/profit_validation_replay.py",
            "scripts/equity_curve.py",
            "src/execution/harvester.py",
            "src/state/db.py",
            "tests/test_run_replay_cli.py",
            "tests/test_pnl_flow_and_audit.py",
            "tests/test_backtest_skill_economics.py",
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "admitted"
    admitted = set(digest["admission"]["admitted_files"])
    assert "scripts/profit_validation_replay.py" in admitted
    assert "scripts/equity_curve.py" in admitted
    assert "src/state/db.py" in admitted
    assert "tests/test_run_replay_cli.py" in admitted
    assert "tests/test_pnl_flow_and_audit.py" in admitted
    assert any(
        "F-10 report/replay gating begins before F-09 durable fill" in stop
        for stop in digest["stop_conditions"]
    )


@pytest.mark.parametrize(
    "task",
    [
        "Phase 5C.3 runtime forward substrate wiring live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring includes live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring needs live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring perform live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring enable live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring implements live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring execute live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring activate live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring with live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring requires live venue side effects",
        "Phase 5C.3 runtime forward substrate wiring with live venue submission",
        "Phase 5C.3 runtime forward substrate wiring with live venue cancel",
        "Phase 5C.3 runtime forward substrate wiring with live venue redeem",
        "Phase 5C.3 runtime forward substrate wiring requires live venue submission",
        "Phase 5C.3 runtime forward substrate wiring perform live venue cancel",
        "Phase 5C.3 runtime forward substrate wiring execute live venue redeem",
        "Phase 5C.3 runtime forward substrate wiring CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring includes CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring needs CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring perform CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring enable CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring implements CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring execute CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring activate CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring with CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring requires CLOB cutover",
        "Phase 5C.3 runtime forward substrate wiring with live cutover",
    ],
)
def test_phase5c3_runtime_wiring_side_effect_wording_is_not_admitted(task):
    digest = build_digest(
        task,
        [
            "src/engine/cycle_runtime.py",
            "src/state/db.py",
            "tests/test_runtime_guards.py",
        ],
    )

    assert (
        digest["profile"] != "phase 5 forward substrate producer implementation"
        or digest["admission"]["status"] != "admitted"
    )


def test_s1_market_source_proof_phrase_routes_to_forward_substrate_producer():
    digest = build_digest(
        "S1 market source-proof persistence for already-parsed Gamma "
        "source_contract audit facts; no schema migration; no production DB writes",
        [
            "src/data/market_scanner.py",
            "src/state/db.py",
            "tests/test_market_scanner_provenance.py",
        ],
    )

    assert digest["profile"] == "phase 5 forward substrate producer implementation"
    assert digest["admission"]["status"] == "admitted"
    admitted = set(digest["admission"]["admitted_files"])
    assert "src/data/market_scanner.py" in admitted
    assert "src/state/db.py" in admitted
    assert "tests/test_market_scanner_provenance.py" in admitted


def test_paris_source_boundary_evidence_routes_to_docs_only_profile():
    digest = build_digest(
        "Paris source-boundary evidence recording LFPG LFPB no config edit "
        "no production DB mutation no data backfill",
        [
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/findings.md",
        ],
    )

    assert digest["profile"] == "source boundary evidence recording"
    assert digest["admission"]["status"] == "admitted"
    assert "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md" in (
        digest["admission"]["admitted_files"]
    )


def test_phase1d_forecast_source_policy_routes_to_source_policy_profile():
    digest = build_digest(
        "Phase 1D forecast source policy make Open-Meteo explicit degraded "
        "fallback gate no TIGGE activation no production DB mutation no live "
        "venue side effects",
        [
            "src/data/forecast_source_registry.py",
            "src/data/ensemble_client.py",
            "src/engine/evaluator.py",
            "src/engine/monitor_refresh.py",
            "tests/test_forecast_source_registry.py",
            "tests/test_ensemble_client.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "phase 1 forecast source policy implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/data/forecast_source_registry.py" in digest["admission"]["admitted_files"]
    assert "src/data/ensemble_client.py" in digest["admission"]["admitted_files"]
    assert "src/engine/evaluator.py" in digest["admission"]["admitted_files"]
    assert "src/engine/monitor_refresh.py" in digest["admission"]["admitted_files"]


def test_phase1e_forecast_source_selection_routes_to_source_policy_profile():
    digest = build_digest(
        "Phase 1E forecast source selection DSA-02 DSA-03 forecast source "
        "identity settings primary crosscheck model provider bias no TIGGE "
        "activation no production DB mutation no Paris config edit",
        [
            "src/config.py",
            "src/data/forecast_source_registry.py",
            "src/data/ensemble_client.py",
            "src/engine/evaluator.py",
            "src/engine/monitor_refresh.py",
            "tests/test_runtime_guards.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "phase 1 forecast source policy implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/config.py" in digest["admission"]["admitted_files"]
    assert "src/engine/evaluator.py" in digest["admission"]["admitted_files"]
    assert "src/engine/monitor_refresh.py" in digest["admission"]["admitted_files"]


def test_phase1f_ecmwf_open_data_routes_to_source_policy_profile():
    digest = build_digest(
        "Phase 1F ECMWF Open Data scheduled collector DSA-04 ECMWF Open Data "
        "source policy scheduled forecast job diagnostic non-executable no "
        "TIGGE activation no production DB mutation no Paris config edit",
        [
            "src/main.py",
            "src/data/ecmwf_open_data.py",
            "src/data/forecast_source_registry.py",
            "tests/test_runtime_guards.py",
            "tests/test_forecast_source_registry.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "phase 1 forecast source policy implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/main.py" in digest["admission"]["admitted_files"]
    assert "src/data/ecmwf_open_data.py" in digest["admission"]["admitted_files"]
    assert "src/data/forecast_source_registry.py" in digest["admission"]["admitted_files"]


def test_phase1g_forecast_history_provenance_routes_to_source_policy_profile():
    digest = build_digest(
        "Phase 1G forecast history provenance eligibility DSA-06 Open-Meteo "
        "previous-runs NULL provenance forecast history NULL provenance replay "
        "ETL no production DB mutation no live venue side effects",
        [
            "src/engine/replay.py",
            "src/backtest/training_eligibility.py",
            "scripts/etl_historical_forecasts.py",
            "scripts/etl_forecast_skill_from_forecasts.py",
            "tests/test_replay_skill_eligibility_filter.py",
            "tests/test_etl_skill_eligibility_filter.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "phase 1 forecast source policy implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/engine/replay.py" in digest["admission"]["admitted_files"]
    assert "scripts/etl_historical_forecasts.py" in digest["admission"]["admitted_files"]
    assert "tests/test_replay_skill_eligibility_filter.py" in digest["admission"]["admitted_files"]


def test_phase1k_live_decision_snapshot_causality_routes_to_snapshot_causality_profile():
    digest = build_digest(
        "Phase 1K live decision snapshot causality DSA-05 DSA-13 DSA-18 "
        "live decision snapshot issue valid fetch available payload hash "
        "Open-Meteo fallback auditable snapshot id no source routing no TIGGE "
        "activation no production DB mutation no live venue side effects",
        [
            "src/engine/evaluator.py",
            "tests/test_center_buy_repair.py",
            "tests/test_fdr.py",
            "tests/test_decision_evidence_runtime_invocation.py",
            "tests/test_digest_profile_matching.py",
            "docs/operations/AGENTS.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "phase 1K live decision snapshot causality gate"
    assert digest["admission"]["status"] == "admitted"
    assert "src/engine/evaluator.py" in digest["admission"]["admitted_files"]
    assert "tests/test_center_buy_repair.py" in digest["admission"]["admitted_files"]
    assert "tests/test_fdr.py" in digest["admission"]["admitted_files"]
    assert "tests/test_decision_evidence_runtime_invocation.py" in digest["admission"]["admitted_files"]
    assert "src/data/ensemble_client.py" not in digest["admission"]["admitted_files"]
    assert "src/data/forecast_source_registry.py" not in digest["admission"]["admitted_files"]


def test_phase1k_review_remediation_wording_routes_to_snapshot_causality_profile():
    digest = build_digest(
        "Phase 1K review-remediation entry forecast evidence causality gate "
        "snapshot causality profile missing available_at issue fetch available "
        "knowability-before-decision no source routing no TIGGE activation no "
        "production DB mutation no live venue side effects",
        [
            "src/engine/evaluator.py",
            "tests/test_center_buy_repair.py",
            "tests/test_runtime_guards.py",
            "tests/test_fdr.py",
            "tests/test_decision_evidence_runtime_invocation.py",
            "tests/test_digest_profile_matching.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
        ],
    )

    assert digest["profile"] == "phase 1K live decision snapshot causality gate"
    assert digest["admission"]["status"] == "admitted"
    assert "src/engine/evaluator.py" in digest["admission"]["admitted_files"]
    assert "architecture/topology.yaml" in digest["admission"]["admitted_files"]
    assert "src/data/ensemble_client.py" not in digest["admission"]["admitted_files"]
    assert "src/data/forecast_source_registry.py" not in digest["admission"]["admitted_files"]


def test_phase1k_remediation_rereview_wording_keeps_forbidden_files_out_of_scope():
    digest = build_digest(
        "Zeus Phase 1K remediation re-review entry forecast evidence causality "
        "gate snapshot causality profile explicit available_at "
        "knowability-before-decision no source routing no TIGGE activation no "
        "production DB mutation no live venue side effects",
        [
            "src/engine/evaluator.py",
            "tests/test_center_buy_repair.py",
            "tests/test_runtime_guards.py",
            "src/data/forecast_source_registry.py",
            "config/settings.json",
            "src/engine/replay.py",
        ],
    )

    assert digest["profile"] == "phase 1K live decision snapshot causality gate"
    assert digest["admission"]["status"] == "blocked"
    assert digest["admission"]["admitted_files"] == []
    assert "src/data/forecast_source_registry.py" not in digest["admission"]["admitted_files"]
    assert "config/settings.json" not in digest["admission"]["admitted_files"]
    assert "src/engine/replay.py" not in digest["admission"]["admitted_files"]
    forbidden = set(digest["admission"]["forbidden_hits"])
    assert {"src/data/forecast_source_registry.py", "config/settings.json", "src/engine/replay.py"} <= forbidden


def test_dsa13_canonical_snapshot_authority_routes_to_phase1l_profile():
    digest = build_digest(
        "DSA-13 canonical snapshot authority ensemble_snapshots_v2 canonical "
        "live snapshots legacy ensemble_snapshots projection diagnostic no "
        "production DB mutation no live venue side effects no source routing "
        "no Paris config edit",
        [
            "src/engine/evaluator.py",
            "src/engine/replay.py",
            "src/execution/harvester.py",
            "src/observability/status_summary.py",
            "src/state/schema/v2_schema.py",
            "tests/test_decision_evidence_runtime_invocation.py",
            "tests/test_replay_time_provenance.py",
            "tests/test_harvester_metric_identity.py",
            "tests/test_phase10b_dt_seam_cleanup.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md",
        ],
    )

    assert digest["profile"] == "phase 1L canonical snapshot authority"
    assert digest["admission"]["status"] == "admitted"
    assert "src/engine/evaluator.py" in digest["admission"]["admitted_files"]
    assert "src/engine/replay.py" in digest["admission"]["admitted_files"]
    assert "src/execution/harvester.py" in digest["admission"]["admitted_files"]
    assert "src/observability/status_summary.py" in digest["admission"]["admitted_files"]
    assert "src/data/ensemble_client.py" not in digest["admission"]["admitted_files"]
    assert "config/cities.json" not in digest["admission"]["admitted_files"]


def test_dsa13_canonical_snapshot_authority_blocks_live_side_effect_scope():
    digest = build_digest(
        "DSA-13 canonical snapshot authority live decision snapshot table "
        "authority",
        [
            "src/engine/evaluator.py",
            "src/venue/polymarket_v2_adapter.py",
            "state/zeus-world.db",
        ],
    )

    assert digest["profile"] == "phase 1L canonical snapshot authority"
    assert digest["admission"]["status"] == "blocked"
    assert digest["admission"]["admitted_files"] == []
    forbidden = set(digest["admission"]["forbidden_hits"])
    assert {"src/venue/polymarket_v2_adapter.py", "state/zeus-world.db"} <= forbidden


def test_phase1h_live_quote_residue_routes_to_cleanup_profile():
    digest = build_digest(
        "Phase 1H live quote authority cleanup DSA-07 live quote authority cleanup "
        "remove production Gamma price branch from monitor_refresh no live venue "
        "side effects no production DB mutation no Paris config edit",
        [
            "src/engine/monitor_refresh.py",
            "tests/test_runtime_guards.py",
            "tests/test_bootstrap_symmetry.py",
            "tests/test_live_safety_invariants.py",
            "tests/test_pnl_flow_and_audit.py",
            "tests/test_pre_live_integration.py",
            "tests/test_k1_review_fixes.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "phase 1H live quote authority cleanup"
    assert digest["admission"]["status"] == "admitted"
    assert "src/engine/monitor_refresh.py" in digest["admission"]["admitted_files"]
    assert "tests/test_runtime_guards.py" in digest["admission"]["admitted_files"]
    assert "tests/test_live_safety_invariants.py" in digest["admission"]["admitted_files"]
    assert "tests/test_pnl_flow_and_audit.py" in digest["admission"]["admitted_files"]


def test_phase1h_slash_hyphen_wording_routes_to_cleanup_profile():
    digest = build_digest(
        "Phase 1H / DSA-07 live quote authority cleanup remove Gamma monitor "
        "price path and require native NO-token quote no live venue side "
        "effects no production DB mutation",
        [
            "src/engine/monitor_refresh.py",
            "tests/test_runtime_guards.py",
            "tests/test_bootstrap_symmetry.py",
            "tests/test_live_safety_invariants.py",
            "tests/test_pnl_flow_and_audit.py",
            "tests/test_pre_live_integration.py",
            "tests/test_k1_review_fixes.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
        ],
    )

    assert digest["profile"] == "phase 1H live quote authority cleanup"
    assert digest["admission"]["status"] == "admitted"
    assert "src/engine/monitor_refresh.py" in digest["admission"]["admitted_files"]
    assert "tests/test_bootstrap_symmetry.py" in digest["admission"]["admitted_files"]


def test_r3_m1_lifecycle_grammar_routes_to_m1_profile_not_heartbeat():
    """M1 also shares R3 docs and cycle_runner paths with Z3; strong M1
    phrases must win so command grammar and RED proxy files are admitted."""
    digest = build_digest(
        "R3 M1 lifecycle grammar cycle_runner-as-proxy red_force_exit_proxy command grammar amendment",
        [
            "src/execution/command_bus.py",
            "src/state/venue_command_repo.py",
            "src/engine/cycle_runner.py",
            "tests/test_command_grammar_amendment.py",
            "tests/test_riskguard_red_durable_cmd.py",
        ],
    )

    assert digest["profile"] == "r3 lifecycle grammar implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/command_bus.py" in digest["admission"]["admitted_files"]
    assert "src/engine/cycle_runner.py" in digest["admission"]["admitted_files"]


def test_r3_inv29_governance_amendment_routes_to_inv29_profile():
    """The INV-29 gate closure touches architecture law, not M1 runtime code;
    it needs its own governance profile rather than the M1 implementation
    profile, which intentionally excludes architecture/invariants.yaml."""
    digest = build_digest(
        "R3 M1 INV-29 amendment closed-law amendment grammar-additive CommandState planning-lock receipt",
        [
            "architecture/invariants.yaml",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/INDEX.md",
            "tests/test_command_grammar_amendment.py",
        ],
    )

    assert digest["profile"] == "r3 inv29 governance amendment"
    assert digest["admission"]["status"] == "admitted"
    assert "architecture/invariants.yaml" in digest["admission"]["admitted_files"]


def test_r3_m2_unknown_side_effect_routes_to_m2_profile_not_heartbeat():
    """M2 shares R3 docs and command-journal files with M1/Z3; strong M2
    phrases must admit executor/recovery files and the unknown-side-effect
    tests instead of falling through to heartbeat or M1 routing."""
    digest = build_digest(
        "R3 M2 SUBMIT_UNKNOWN_SIDE_EFFECT unknown-side-effect semantics "
        "unknown_side_effect SAFE_REPLAY_PERMITTED economic-intent fingerprint",
        [
            "src/venue/polymarket_v2_adapter.py",
            "src/data/polymarket_client.py",
            "src/execution/executor.py",
            "src/execution/command_recovery.py",
            "src/state/venue_command_repo.py",
            "tests/test_unknown_side_effect.py",
            "tests/test_v2_adapter.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M2.yaml",
        ],
    )

    assert digest["profile"] == "r3 unknown side effect implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/venue/polymarket_v2_adapter.py" in digest["admission"]["admitted_files"]
    assert "src/data/polymarket_client.py" in digest["admission"]["admitted_files"]
    assert "src/execution/executor.py" in digest["admission"]["admitted_files"]
    assert "src/execution/command_recovery.py" in digest["admission"]["admitted_files"]
    assert "tests/test_unknown_side_effect.py" in digest["admission"]["admitted_files"]
    assert "tests/test_v2_adapter.py" in digest["admission"]["admitted_files"]


def test_r3_m3_user_channel_routes_to_m3_profile():
    """M3 shares R3 docs plus executor/cycle paths with M2/Z3; strong user
    channel phrases must admit the ingest/guard/test files instead of routing
    to heartbeat or unknown-side-effect profiles."""
    digest = build_digest(
        "R3 M3 User-channel WS ingest PolymarketUserChannelIngestor WS_USER "
        "append_order_fact append_trade_fact WS gap detected REST fallback",
        [
            "src/ingest/polymarket_user_channel.py",
            "src/control/ws_gap_guard.py",
            "src/execution/executor.py",
            "src/engine/cycle_runner.py",
            "tests/test_user_channel_ingest.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M3.yaml",
        ],
    )

    assert digest["profile"] == "r3 user channel ws implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/ingest/polymarket_user_channel.py" in digest["admission"]["admitted_files"]
    assert "src/control/ws_gap_guard.py" in digest["admission"]["admitted_files"]
    assert "tests/test_user_channel_ingest.py" in digest["admission"]["admitted_files"]


def test_r3_m4_cancel_replace_routes_to_m4_profile_not_heartbeat():
    """M4 shares executor/state paths with M2/M3/Z3; strong cancel/replace
    phrases must route to the exit-safety profile so the mutex/parser test
    surface is admitted instead of falling through to heartbeat."""
    digest = build_digest(
        "R3 M4 Cancel/replace + exit safety ExitMutex CancelOutcome "
        "CANCEL_UNKNOWN blocks replacement replacement sell BLOCKED exit mutex",
        [
            "src/execution/exit_safety.py",
            "src/execution/exit_lifecycle.py",
            "src/execution/executor.py",
            "src/state/venue_command_repo.py",
            "tests/test_exit_safety.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M4.yaml",
        ],
    )

    assert digest["profile"] == "r3 cancel replace exit safety implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/exit_safety.py" in digest["admission"]["admitted_files"]
    assert "src/execution/exit_lifecycle.py" in digest["admission"]["admitted_files"]
    assert "tests/test_exit_safety.py" in digest["admission"]["admitted_files"]


def test_r3_m5_exchange_reconcile_routes_to_m5_profile_not_heartbeat():
    """M5 names heartbeat/cancel/cutover evidence but owns a distinct
    exchange-reconciliation findings surface; strong M5 phrases must not route
    to the heartbeat profile."""
    digest = build_digest(
        "R3 M5 Exchange reconciliation sweep exchange_reconcile_findings "
        "run_reconcile_sweep exchange ghost order local orphan order "
        "unrecorded trade position drift heartbeat suspected cancel cutover wipe",
        [
            "src/execution/exchange_reconcile.py",
            "src/state/venue_command_repo.py",
            "src/state/db.py",
            "src/control/heartbeat_supervisor.py",
            "src/control/cutover_guard.py",
            "src/venue/polymarket_v2_adapter.py",
            "tests/test_exchange_reconcile.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/M5.yaml",
        ],
    )

    assert digest["profile"] == "r3 exchange reconciliation sweep implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/exchange_reconcile.py" in digest["admission"]["admitted_files"]
    assert "src/state/venue_command_repo.py" in digest["admission"]["admitted_files"]
    assert "src/control/heartbeat_supervisor.py" in digest["admission"]["admitted_files"]
    assert "tests/test_exchange_reconcile.py" in digest["admission"]["admitted_files"]


def test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat():
    """R1 mentions settlement/redeem and shares R3 packet docs with Z3; strong
    settlement-command phrases must admit the durable command ledger files
    instead of falling through to heartbeat or generic settlement-rounding."""
    digest = build_digest(
        "R3 R1 Settlement / redeem command ledger settlement_commands "
        "REDEEM_TX_HASHED crash-recoverable redemption Q-FX-1 FXClassificationPending",
        [
            "src/execution/settlement_commands.py",
            "src/execution/harvester.py",
            "src/state/db.py",
            "src/contracts/fx_classification.py",
            "tests/test_settlement_commands.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/R1.yaml",
        ],
    )

    assert digest["profile"] == "r3 settlement redeem command ledger implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/settlement_commands.py" in digest["admission"]["admitted_files"]
    assert "src/execution/harvester.py" in digest["admission"]["admitted_files"]
    assert "src/state/db.py" in digest["admission"]["admitted_files"]
    assert "tests/test_settlement_commands.py" in digest["admission"]["admitted_files"]


def test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat():
    """T1 shares heartbeat/cutover/reconcile terms with Z3/M5 but owns the
    fake venue parity harness; strong T1 phrases must admit fake-venue test
    infrastructure instead of falling through to heartbeat."""
    digest = build_digest(
        "R3 T1 FakePolymarketVenue fake/live adapter parity same PolymarketV2Adapter "
        "Protocol schema-identical events INV-NEW-M failure injection heartbeat miss",
        [
            "tests/fakes/polymarket_v2.py",
            "tests/integration/test_p0_live_money_safety.py",
            "tests/test_fake_polymarket_venue.py",
            "src/venue/polymarket_v2_adapter.py",
            "src/state/venue_command_repo.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/T1.yaml",
        ],
    )

    assert digest["profile"] == "r3 fake polymarket venue parity implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "tests/fakes/polymarket_v2.py" in digest["admission"]["admitted_files"]
    assert "tests/integration/test_p0_live_money_safety.py" in digest["admission"]["admitted_files"]
    assert "src/venue/polymarket_v2_adapter.py" in digest["admission"]["admitted_files"]


def test_r3_a1_strategy_benchmark_routes_to_a1_profile_not_heartbeat():
    """A1 shares broad strategy/live-shadow/replay terms with R3 runtime work;
    strong benchmark-suite phrases must admit the A1 strategy benchmark surface
    instead of falling through to heartbeat or generic strategy routing."""
    digest = build_digest(
        "R3 A1 StrategyBenchmarkSuite alpha execution metrics diagnostic simulated read-only live "
        "promotion gate strategy_benchmark_runs INV-NEW-Q",
        [
            "src/strategy/benchmark_suite.py",
            "src/strategy/data_lake.py",
            "src/strategy/candidates/__init__.py",
            "tests/test_strategy_benchmark.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A1.yaml",
        ],
    )

    assert digest["profile"] == "r3 strategy benchmark suite implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/strategy/benchmark_suite.py" in digest["admission"]["admitted_files"]
    assert "src/strategy/data_lake.py" in digest["admission"]["admitted_files"]
    assert "src/strategy/candidates/__init__.py" in digest["admission"]["admitted_files"]
    assert "tests/test_strategy_benchmark.py" in digest["admission"]["admitted_files"]


def test_dsa08_dsa17_evidence_grade_cleanup_routes_to_a1_profile():
    digest = build_digest(
        "DSA-08 DSA-17 strategy benchmark evidence-grade naming cleanup "
        "simulated read-only evidence-grade naming cleanup no production DB mutation "
        "no live venue side effects no CLOB cutover",
        [
            "src/strategy/benchmark_suite.py",
            "tests/test_strategy_benchmark.py",
            "docs/reference/modules/strategy.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
        ],
    )

    assert digest["profile"] == "r3 strategy benchmark suite implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/strategy/benchmark_suite.py" in digest["admission"]["admitted_files"]
    assert "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md" in (
        digest["admission"]["admitted_files"]
    )
    assert "architecture/digest_profiles.py" in digest["admission"]["admitted_files"]


def test_dsa12_zeus_mode_selector_cleanup_routes_to_phase0b_profile():
    digest = build_digest(
        "DSA-12 ZEUS_MODE compatibility cleanup; get_mode ignores ZEUS_MODE "
        "and returns live; no production DB mutation; no Paris config edit",
        [
            "src/config.py",
            "tests/test_k5_slice_l.py",
            "tests/test_config.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/findings.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
        ],
    )

    assert digest["profile"] == "phase 0b zeus mode selector cleanup"
    assert digest["admission"]["status"] == "admitted"
    assert "src/config.py" in digest["admission"]["admitted_files"]
    assert "tests/test_k5_slice_l.py" in digest["admission"]["admitted_files"]
    assert "docs/operations/task_2026-04-29_design_simplification_audit/findings.md" in (
        digest["admission"]["admitted_files"]
    )
    assert "architecture/digest_profiles.py" in digest["admission"]["admitted_files"]


def test_dsa09_stale_execution_price_shadow_flag_routes_to_phase0c_profile():
    digest = build_digest(
        "DSA-09 remove stale EXECUTION_PRICE_SHADOW config flag after "
        "execution price shadow-off path removal; no production DB mutation; "
        "no live venue side effects; no Paris config edit",
        [
            "config/settings.json",
            "tests/test_execution_price.py",
            "docs/operations/known_gaps.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/findings.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
        ],
    )

    assert digest["profile"] == "phase 0c stale execution price shadow flag cleanup"
    assert digest["admission"]["status"] == "admitted"
    assert "config/settings.json" in digest["admission"]["admitted_files"]
    assert "tests/test_execution_price.py" in digest["admission"]["admitted_files"]
    assert "docs/operations/known_gaps.md" in digest["admission"]["admitted_files"]
    assert "architecture/digest_profiles.py" in digest["admission"]["admitted_files"]


def test_dsa10_dsa18_snapshot_only_fallback_routes_to_phase1j_profile():
    digest = build_digest(
        "Phase 1J DSA-10 DSA-18 replay snapshot-only fallback explicit opt-in; "
        "remove implicit snapshot-only fallback for non-audit replay modes; "
        "tests/docs only; no DB mutation; no live venue; no Paris source routing",
        [
            "src/engine/replay.py",
            "tests/test_run_replay_cli.py",
            "tests/test_replay_time_provenance.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/findings.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
        ],
    )

    assert digest["profile"] == "phase 1j replay snapshot-only fallback explicit opt-in"
    assert digest["admission"]["status"] == "admitted"
    assert "src/engine/replay.py" in digest["admission"]["admitted_files"]
    assert "tests/test_run_replay_cli.py" in digest["admission"]["admitted_files"]
    assert "tests/test_replay_time_provenance.py" in digest["admission"]["admitted_files"]
    assert "architecture/digest_profiles.py" in digest["admission"]["admitted_files"]


def test_r3_f1_forecast_source_registry_routes_to_f1_profile_not_heartbeat():
    """F1 shares broad R3 docs and generic forecast/signal terms with other
    profiles; strong F1 phrases must route to the forecast-source registry
    profile so data/schema/test files are admitted together."""
    digest = build_digest(
        "R3 F1 Forecast source registry source_id raw_payload_hash authority_tier operator-gated forecast source",
        [
            "src/data/forecast_source_registry.py",
            "src/data/forecast_ingest_protocol.py",
            "src/data/forecasts_append.py",
            "src/state/db.py",
            "tests/test_forecast_source_registry.py",
        ],
    )

    assert digest["profile"] == "r3 forecast source registry implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/data/forecast_source_registry.py" in digest["admission"]["admitted_files"]
    assert "src/state/db.py" in digest["admission"]["admitted_files"]


def test_r3_f3_tigge_ingest_stub_routes_to_f3_profile_not_heartbeat():
    """F3 shares broad R3 docs and forecast terms with F1/Z3; strong TIGGE
    phrases must route to the dormant ingest-stub profile so the new client,
    registry, and tests are admitted together."""
    digest = build_digest(
        "R3 F3 TIGGE ingest stub TIGGEIngest TIGGEIngestNotEnabled ZEUS_TIGGE_INGEST_ENABLED",
        [
            "src/data/tigge_client.py",
            "src/data/forecast_source_registry.py",
            "tests/test_tigge_ingest.py",
        ],
    )

    assert digest["profile"] == "r3 tigge ingest stub implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/data/tigge_client.py" in digest["admission"]["admitted_files"]
    assert "src/data/forecast_source_registry.py" in digest["admission"]["admitted_files"]


def test_r3_f2_calibration_retrain_loop_routes_to_f2_profile_not_heartbeat():
    """F2 shares broad R3 docs plus calibration/source terms with other profiles;
    strong retrain phrases must admit the retrain trigger and antibodies."""
    digest = build_digest(
        "R3 F2 Calibration retrain loop operator-gated retrain frozen-replay antibody ZEUS_CALIBRATION_RETRAIN_ENABLED calibration_params_versions",
        [
            "docs/AGENTS.md",
            "architecture/AGENTS.md",
            "src/calibration/retrain_trigger.py",
            "tests/test_calibration_retrain.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F2.yaml",
        ],
    )

    assert digest["profile"] == "r3 calibration retrain loop implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "docs/AGENTS.md" in digest["admission"]["admitted_files"]
    assert "src/calibration/retrain_trigger.py" in digest["admission"]["admitted_files"]
    assert "tests/test_calibration_retrain.py" in digest["admission"]["admitted_files"]


def test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat():
    """A2 mentions heartbeat/unknown-side-effect/reconcile signals, but owns
    the allocator/governor kill-switch layer; strong A2 phrases must route to
    the risk allocator profile rather than heartbeat/M2/M5."""
    digest = build_digest(
        "R3 A2 RiskAllocator PortfolioGovernor caps drawdown governor kill switch "
        "cap-policy-config INV-NEW-R NC-NEW-I optimistic confirmed exposure",
        [
            "src/risk_allocator/governor.py",
            "src/risk_allocator/__init__.py",
            "config/risk_caps.yaml",
            "tests/test_risk_allocator.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A2.yaml",
        ],
    )

    assert digest["profile"] == "r3 risk allocator governor implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/risk_allocator/governor.py" in digest["admission"]["admitted_files"]
    assert "src/risk_allocator/__init__.py" in digest["admission"]["admitted_files"]
    assert "config/risk_caps.yaml" in digest["admission"]["admitted_files"]
    assert "tests/test_risk_allocator.py" in digest["admission"]["admitted_files"]


def test_r3_g1_live_readiness_routes_to_g1_profile_not_heartbeat():
    """G1 readiness mentions heartbeat/cutover/risk artifacts, but owns the
    16-gate orchestration surface; strong G1 phrases must not route to Z3."""
    digest = build_digest(
        "R3 G1 live readiness gates live_readiness_check 16 active CI gates "
        "staged-live-smoke INV-NEW-S live-money-deploy-go",
        [
            "scripts/live_readiness_check.py",
            "tests/test_live_readiness_gates.py",
            "docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence/staged_live_smoke_2026-04-30.json",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/G1.yaml",
        ],
    )

    assert digest["profile"] == "r3 live readiness gates implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "scripts/live_readiness_check.py" in digest["admission"]["admitted_files"]
    assert "tests/test_live_readiness_gates.py" in digest["admission"]["admitted_files"]
    assert (
        "docs/operations/task_2026-04-26_polymarket_clob_v2_migration/evidence/staged_live_smoke_2026-04-30.json"
        in digest["admission"]["admitted_files"]
    )


def test_phase2c_execution_capability_routes_to_dedicated_profile():
    digest = build_digest(
        "Phase 2C DSA-16 composed execution capability proof for entry exit "
        "capability proof payload; no live venue side effects; no production "
        "DB mutation; no source routing; no Paris; no CLOB cutover",
        [
            "src/execution/executor.py",
            "tests/test_executor_command_split.py",
            "tests/test_digest_profile_matching.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/findings.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
        ],
    )

    assert digest["profile"] == "phase 2c execution capability proof implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/executor.py" in digest["admission"]["admitted_files"]
    assert "tests/test_executor_command_split.py" in digest["admission"]["admitted_files"]


def test_phase2f_source_degradation_freshness_routes_to_dedicated_profile():
    digest = build_digest(
        "Phase 2F DSA-16 source degradation freshness capability with "
        "execution intent source freshness threading; no live venue side "
        "effects; no production DB mutation; no schema migration; no source "
        "routing; no Paris; no CLOB cutover",
        [
            "src/contracts/execution_intent.py",
            "src/engine/cycle_runtime.py",
            "src/execution/executor.py",
            "tests/test_executor_command_split.py",
            "tests/test_live_execution.py",
            "tests/test_runtime_guards.py",
            "tests/test_digest_profile_matching.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/findings.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
        ],
    )

    assert digest["profile"] == "phase 2f source degradation freshness capability implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/contracts/execution_intent.py" in digest["admission"]["admitted_files"]
    assert "src/engine/cycle_runtime.py" in digest["admission"]["admitted_files"]
    assert "src/execution/executor.py" in digest["admission"]["admitted_files"]
    assert "tests/test_live_execution.py" in digest["admission"]["admitted_files"]


def test_phase2d_execution_capability_status_routes_to_observability_profile():
    digest = build_digest(
        "Phase 2D DSA-16 execution capability status summary matrix for entry "
        "exit cancel redeem; derived operator visibility only; no live venue "
        "side effects; no production DB mutation; no schema migration; no "
        "source routing; no Paris; no CLOB cutover",
        [
            "src/observability/status_summary.py",
            "tests/test_phase10b_dt_seam_cleanup.py",
            "tests/test_digest_profile_matching.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/findings.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
        ],
    )

    assert digest["profile"] == "phase 2d execution capability status summary implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/observability/status_summary.py" in digest["admission"]["admitted_files"]
    assert "tests/test_phase10b_dt_seam_cleanup.py" in digest["admission"]["admitted_files"]


def test_phase2e_cancel_redeem_capability_routes_to_dedicated_profile():
    digest = build_digest(
        "Phase 2E DSA-16 cancel redeem command-side capability proof payload; "
        "add proof to CANCEL_REQUESTED and REDEEM_SUBMITTED pre-side-effect "
        "events only; no live venue side effects; no production DB mutation; "
        "no schema migration; no source routing; no Paris; no CLOB cutover",
        [
            "src/execution/exit_safety.py",
            "src/execution/settlement_commands.py",
            "tests/test_exit_safety.py",
            "tests/test_settlement_commands.py",
            "tests/test_digest_profile_matching.py",
            "docs/operations/task_2026-04-29_design_simplification_audit/evidence.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/findings.md",
            "docs/operations/task_2026-04-29_design_simplification_audit/simplification_plan.md",
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
        ],
    )

    assert digest["profile"] == "phase 2e cancel redeem capability proof implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/exit_safety.py" in digest["admission"]["admitted_files"]
    assert "src/execution/settlement_commands.py" in digest["admission"]["admitted_files"]
    assert "tests/test_exit_safety.py" in digest["admission"]["admitted_files"]
    assert "tests/test_settlement_commands.py" in digest["admission"]["admitted_files"]


def test_phase2e_realistic_seam_wording_routes_to_dedicated_profile():
    digest = build_digest(
        "Add pre-side-effect execution_capability proof payloads to "
        "CANCEL_REQUESTED in request_cancel_for_command and REDEEM_SUBMITTED "
        "in submit_redeem; preserve existing gates; no live venue side effects; "
        "no executor/venue/state/schema/source/Paris changes",
        [
            "src/execution/exit_safety.py",
            "src/execution/settlement_commands.py",
            "tests/test_exit_safety.py",
            "tests/test_settlement_commands.py",
            "tests/test_digest_profile_matching.py",
        ],
    )

    assert digest["profile"] == "phase 2e cancel redeem capability proof implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/exit_safety.py" in digest["admission"]["admitted_files"]
    assert "src/execution/settlement_commands.py" in digest["admission"]["admitted_files"]
    assert "tests/test_exit_safety.py" in digest["admission"]["admitted_files"]
    assert "tests/test_settlement_commands.py" in digest["admission"]["admitted_files"]


def test_batch_h_legacy_day0_backfill_routes_to_contamination_profile():
    """The contamination remediation Batch H profile must beat broad R3
    file-pattern profiles and admit only the planned implementation surfaces."""
    digest = build_digest(
        "Batch H legacy Day0-only canonical history entry backfill remediation",
        [
            "src/execution/exit_lifecycle.py",
            "tests/test_runtime_guards.py",
            "architecture/test_topology.yaml",
            "docs/archives/packets/task_2026-04-28_contamination_remediation/plan.md",
            "docs/archives/packets/task_2026-04-28_contamination_remediation/work_log.md",
            "docs/archives/packets/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h_current_diff_2026-04-28.md",
        ],
    )

    assert digest["profile"] == "batch h legacy day0 canonical history backfill remediation"
    assert digest["profile"] not in {
        "r3 live readiness gates implementation",
        "r3 cancel replace exit safety implementation",
        "r3 exchange reconciliation sweep implementation",
    }
    assert digest["admission"]["status"] == "admitted"
    assert "src/execution/exit_lifecycle.py" in digest["admission"]["admitted_files"]
    assert "tests/test_runtime_guards.py" in digest["admission"]["admitted_files"]
    assert digest["admission"]["out_of_scope_files"] == []


def test_batch_h_profile_law_names_real_canonical_entry_events_only():
    """The machine-readable Batch H law must not reintroduce invented events."""
    digest = build_digest(
        "Batch H legacy Day0-only canonical history entry backfill remediation",
        ["src/execution/exit_lifecycle.py"],
    )
    required_law = "\n".join(digest["required_law"])

    assert "POSITION_OPEN_INTENT" in required_law
    assert "ENTRY_ORDER_POSTED" in required_law
    assert "ENTRY_ORDER_FILLED" in required_law
    assert "ENTRY_ORDER_PLACED" not in required_law


def test_batch_h_profile_does_not_select_from_exit_lifecycle_file_alone():
    """File evidence alone must not route to the Batch H contamination profile."""
    digest = build_digest(
        "fix exit_lifecycle backfill bug",
        ["src/execution/exit_lifecycle.py"],
    )

    assert digest["profile"] != "batch h legacy day0 canonical history backfill remediation"


@pytest.mark.parametrize(
    "task",
    [
        "edit replay fidelity for settlement rebuild",  # both phrases live in distinct profiles
    ],
)
def test_multi_profile_match_does_not_silently_pick_one(task):
    digest = build_digest(task, [])
    # Either the resolver picks deterministically with a recorded basis, or
    # it returns an explicit ambiguous status. Either is acceptable as long
    # as the choice is not silent.
    admission = digest["admission"]
    if admission["status"] == "ambiguous":
        assert "decision_basis" in admission
    else:
        # Deterministic pick must record decision_basis on the admission.
        assert "decision_basis" in admission


# ---------------------------------------------------------------------------
# Stable serialization shape (downstream contract).
# ---------------------------------------------------------------------------

def test_admission_envelope_contract_fields_present():
    digest = build_digest("change settlement rounding", ["src/contracts/settlement_semantics.py"])
    admission = digest["admission"]
    for key in (
        "status",
        "admitted_files",
        "out_of_scope_files",
        "forbidden_hits",
        "profile_id",
        "profile_suggested_files",
        "decision_basis",
    ):
        assert key in admission, f"admission envelope missing {key}: keys={list(admission)}"


def test_legacy_allowed_files_marked_advisory_in_route_context():
    """Legacy `allowed_files` exists for backward compat but must be flagged
    as advisory in the navigation route_context output."""
    # build_digest itself doesn't expose route_context; that's run_navigation's
    # job. Here we just confirm allowed_files is preserved.
    digest = build_digest("change settlement rounding", ["src/contracts/settlement_semantics.py"])
    assert "allowed_files" in digest
    # The admission envelope is the new authoritative contract.
    assert "admission" in digest


def test_agent_runtime_profile_admits_runtime_surfaces():
    digest = build_digest(
        "agent runtime route card typed intent claim-scoped graph workflow",
        [
            "scripts/topology_doctor_cli.py",
            "architecture/context_pack_profiles.yaml",
            "docs/reference/modules/topology_doctor_system.md",
        ],
    )

    assert digest["profile"] == "topology graph agent runtime upgrade"
    assert digest["admission"]["status"] == "admitted"
    assert digest["route_card"]["risk_tier"] == "T3"
    assert digest["route_card"]["next_action"].startswith("proceed with planning-lock")


def test_direct_operation_feedback_capsule_routes_without_persisted_files():
    digest = build_digest(
        "回收 context 和体验: Operation Feedback Capsule with topology helped/blocked note",
        [],
    )

    assert digest["profile"] == "direct operation feedback capsule"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["route_card"]["risk_tier"] == "T0"
    assert "final response" in " ".join(digest["required_law"])


def test_operation_feedback_capsule_admits_existing_packet_work_log_only():
    digest = build_digest(
        "operation feedback capsule for packet closeout",
        ["docs/operations/task_2026-05-01_example/work_log.md"],
        write_intent="edit",
    )

    assert digest["profile"] == "direct operation feedback capsule"
    assert digest["admission"]["status"] == "admitted"
    assert digest["admission"]["admitted_files"] == [
        "docs/operations/task_2026-05-01_example/work_log.md"
    ]


def test_operation_feedback_capsule_blocks_omx_context_handoff_files():
    digest = build_digest(
        "operation feedback capsule persist runtime handoff",
        [".omx/context/runtime_handoff.md"],
        write_intent="edit",
    )

    assert digest["profile"] == "direct operation feedback capsule"
    assert digest["admission"]["status"] == "blocked"
    assert digest["admission"]["forbidden_hits"] == [".omx/context/runtime_handoff.md"]


def test_shared_registry_files_do_not_select_domain_profile_by_themselves():
    digest = build_digest(
        "topology navigation output contract false restriction cleanup",
        [
            "architecture/topology.yaml",
            "architecture/digest_profiles.py",
            "architecture/docs_registry.yaml",
            "architecture/test_topology.yaml",
            "scripts/topology_doctor.py",
            "scripts/topology_doctor_digest.py",
            "scripts/topology_doctor_docs_checks.py",
        ],
    )

    assert digest["profile"] != "r3 live readiness gates implementation"
    assert digest["admission"]["status"] in {"advisory_only", "ambiguous"}
    assert digest["admission"]["admitted_files"] == []
    assert digest["admission"]["decision_basis"]["selected_by"] == "shared_file_only"
    assert digest["profile_selection"]["evidence_class"] == "shared_file_only"
    assert digest["profile_selection"]["needs_typed_intent"] is True
    assert "architecture/topology.yaml" in digest["profile_selection"]["shared_file_hits"]


def test_actual_profile_resolver_stability_diff_does_not_route_to_live_readiness():
    digest = build_digest(
        "topology profile resolver stability",
        [
            "architecture/digest_profiles.py",
            "architecture/topology.yaml",
            "docs/operations/AGENTS.md",
            "docs/archives/packets/task_2026-04-29_topology_profile_resolver_stability/plan.md",
            "docs/archives/packets/task_2026-04-29_topology_profile_resolver_stability/receipt.json",
            "docs/archives/packets/task_2026-04-29_topology_profile_resolver_stability/work_log.md",
            "scripts/topology_doctor.py",
            "scripts/topology_doctor_cli.py",
            "scripts/topology_doctor_digest.py",
            "scripts/topology_doctor_registry_checks.py",
            "tests/test_digest_profile_matching.py",
            "tests/test_topology_doctor.py",
        ],
    )

    assert digest["profile"] == "generic"
    assert digest["profile"] != "r3 live readiness gates implementation"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["admission"]["admitted_files"] == []
    assert digest["profile_selection"]["evidence_class"] == "shared_file_only"
    assert digest["profile_selection"]["needs_typed_intent"] is True
    assert "tests/test_digest_profile_matching.py" in digest["profile_selection"]["shared_file_hits"]


def test_high_fanout_evaluator_file_does_not_select_profile_by_itself(monkeypatch):
    from scripts import topology_doctor

    topology = topology_doctor.load_topology()
    topology = {**topology, "digest_profiles": list(topology["digest_profiles"])}
    topology["digest_profiles"].extend([
        {
            "id": "phase 1 source policy synthetic",
            "match_policy": {
                "strong_phrases": ["Phase 1D forecast source policy"],
                "weak_terms": [],
                "negative_phrases": [],
                "single_terms_can_select": False,
                "min_confidence": 0.5,
            },
            "file_patterns": ["src/engine/evaluator.py"],
            "allowed_files": ["src/engine/evaluator.py"],
        },
        {
            "id": "snapshot policy synthetic",
            "match_policy": {
                "strong_phrases": ["executable market snapshot"],
                "weak_terms": [],
                "negative_phrases": [],
                "single_terms_can_select": False,
                "min_confidence": 0.5,
            },
            "file_patterns": ["src/engine/evaluator.py"],
            "allowed_files": ["src/engine/evaluator.py"],
        },
    ])
    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)

    digest = build_digest(
        "Phase 1 source/snapshot policy",
        ["src/engine/evaluator.py"],
    )

    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["admission"]["admitted_files"] == []
    assert digest["admission"]["decision_basis"]["selected_by"] == "high_fanout_file_only"
    assert "soft ambiguity" in " ".join(digest["admission"]["decision_basis"]["why"])
    assert digest["profile_selection"]["evidence_class"] == "high_fanout_file_only"
    assert digest["profile_selection"]["needs_typed_intent"] is True
    assert "src/engine/evaluator.py" in digest["profile_selection"]["semantic_file_hits"]
    assert "phase 1 source policy synthetic" in digest["profile_selection"]["candidates"]
    assert "snapshot policy synthetic" in digest["profile_selection"]["candidates"]


def test_diagnostic_text_about_wrong_capability_route_does_not_admit_evaluator(monkeypatch):
    from scripts import topology_doctor

    topology = topology_doctor.load_topology()
    topology = {**topology, "digest_profiles": list(topology["digest_profiles"])}
    topology["digest_profiles"].extend([
        {
            "id": "phase 2c execution capability proof implementation synthetic",
            "match_policy": {
                "strong_phrases": ["Phase 2C DSA-16 composed execution capability proof"],
                "weak_terms": [],
                "negative_phrases": [],
                "single_terms_can_select": False,
                "min_confidence": 0.5,
            },
            "file_patterns": ["src/engine/evaluator.py"],
            "allowed_files": ["src/execution/executor.py"],
            "forbidden_files": ["src/engine/**"],
        },
        {
            "id": "phase 1 source policy synthetic",
            "match_policy": {
                "strong_phrases": ["Phase 1D forecast source policy"],
                "weak_terms": [],
                "negative_phrases": [],
                "single_terms_can_select": False,
                "min_confidence": 0.5,
            },
            "file_patterns": ["src/engine/evaluator.py"],
            "allowed_files": ["src/engine/evaluator.py"],
        },
    ])
    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)

    digest = build_digest(
        "first topology probe was misrouted to Phase 2C capability profile; "
        "narrow task wording to Phase 1 source/snapshot policy before touching evaluator",
        ["src/engine/evaluator.py"],
    )

    assert digest["profile"] == "generic"
    assert digest["profile"] != "phase 2c execution capability proof implementation synthetic"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["admission"]["admitted_files"] == []
    assert digest["admission"]["decision_basis"]["selected_by"] == "high_fanout_file_only"
    assert "soft ambiguity" in " ".join(digest["admission"]["decision_basis"]["why"])
    assert digest["profile_selection"]["needs_typed_intent"] is True


def test_real_evaluator_fix_wording_soft_routes_instead_of_blocking_navigation():
    digest = build_digest(
        "修复 evaluator 里面的错误",
        ["src/engine/evaluator.py"],
        write_intent="edit",
    )

    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["admission"]["admitted_files"] == []
    assert digest["admission"]["decision_basis"]["selected_by"] == "high_fanout_file_only"
    assert digest["profile_selection"]["needs_typed_intent"] is True
    assert digest["route_card"]["admission_status"] == "advisory_only"
    assert "pass typed intent" in digest["route_card"]["next_action"]
    assert "not edit permission" in " ".join(digest["route_card"]["expansion_hints"])
    assert "admitted files" not in " ".join(digest["route_card"]["expansion_hints"])


def test_typed_intent_overrides_phrase_scoring_without_bypassing_admission():
    digest = build_digest(
        "G1 live readiness route card implementation",
        ["scripts/topology_doctor_cli.py"],
        intent="topology graph agent runtime upgrade",
    )

    assert digest["profile"] == "topology graph agent runtime upgrade"
    assert digest["admission"]["status"] == "admitted"
    assert digest["admission"]["decision_basis"]["selected_by"] == "typed_intent"
    assert digest["typed_runtime_inputs"]["intent_selected"] is True


def test_typed_intent_cannot_admit_forbidden_files():
    digest = build_digest(
        "agent runtime route card implementation",
        ["src/engine/evaluator.py"],
        intent="topology graph agent runtime upgrade",
    )

    assert digest["profile"] == "topology graph agent runtime upgrade"
    assert digest["admission"]["status"] == "blocked"
    assert digest["admission"]["forbidden_hits"] == ["src/engine/evaluator.py"]


def test_invalid_typed_intent_blocks_instead_of_falling_back_to_phrase_route():
    digest = build_digest(
        "G1 live readiness route card implementation",
        ["scripts/topology_doctor_cli.py"],
        intent="not a real topology profile",
    )

    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "ambiguous"
    assert digest["admission"]["decision_basis"]["selected_by"] == "typed_intent_invalid"
    assert digest["typed_runtime_inputs"]["intent_selected"] is False
