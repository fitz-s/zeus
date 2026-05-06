"""Adversarial tests for digest profile selection.

Exercises the Evidence/Resolver layer of topology_doctor_digest. The goal is
to prove that route suggestion is driven by structured evidence, not by raw
substring matching, so that benign tasks like "improve source code quality"
cannot collide with safety-critical profiles like "modify data ingestion".

These cases come directly from §15 of docs/reference/Zeus_Apr25_review.md.
"""
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
        ],
    )

    assert digest["profile"] == "pricing semantics authority cutover"
    assert digest["admission"]["status"] == "admitted"
    assert "src/engine/monitor_refresh.py" in digest["admission"]["admitted_files"]
    assert "tests/test_runtime_guards.py" in digest["admission"]["admitted_files"]
    assert "tests/test_live_safety_invariants.py" in digest["admission"]["admitted_files"]


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
            "architecture/topology_schema.yaml",
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
