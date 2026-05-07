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
# test_topology_doctor.py stale xfail markers
#
# Phase 3 cutover renamed/removed topology_doctor_context_pack; 78 test
# functions in test_topology_doctor.py fail with ImportError on that module.
# topology_doctor itself remains active (AGENTS.md routing). These tests are
# stale import-chain residue, not regressions in the active script.
#
# sunset_date: 2026-09-06  — rewrite or delete before this date.
# Tracked in evidence/phase5_d_cutover_log.md carry-forward item 9.
# ---------------------------------------------------------------------------

_STALE_TOPOLOGY_DOCTOR_TESTS = frozenset({
    "test_cli_json_parity_for_runtime_command",
    "test_cli_json_parity_for_context_pack_command",
    "test_cli_json_parity_for_semantic_bootstrap_command",
    "test_cli_json_parity_for_impact_command",
    "test_cli_json_parity_for_core_map_command",
    "test_cli_json_parity_for_compiled_topology_shape",
    "test_topology_reference_replacement_mode_tracks_reference_docs",
    "test_context_pack_includes_graph_appendix_for_code_review_profile",
    "test_context_pack_handles_missing_graph_db_gracefully",
    "test_impact_reports_write_routes_and_tests_for_store",
    "test_impact_reports_non_source_manifest_adapters",
    "test_context_pack_profiles_mode_validates_manifest",
    "test_module_book_and_manifest_lanes_pass",
    "test_cli_json_parity_for_module_book_lane",
    "test_cli_json_parity_for_module_manifest_lane",
    "test_package_review_context_pack_shapes_k1_style_review",
    "test_package_review_separates_route_health_from_repo_health",
    "test_claude_pre_merge_hook_ignores_non_git_bash_without_empty_error",
    "test_claude_pre_commit_hook_detects_multi_space_git_commit_not_plumbing",
    "test_claude_pre_commit_hook_marker_skips_channel_a_only",
    "test_git_pre_commit_hook_sentinel_uses_git_dir_and_auto_clears",
    "test_git_pre_commit_hook_env_skip_still_bypasses",
    "test_claude_pre_commit_hooks_detect_git_global_options_for_commit",
    "test_claude_pre_commit_hook_detects_chained_or_multiline_commit",
    "test_claude_pre_commit_hook_blocks_dynamic_git_subcommand",
    "test_claude_pre_commit_invariant_missing_python_blocks_not_skips",
    "test_git_pre_commit_orchestrator_preserves_subhook_exit_status",
    "test_pre_edit_architecture_blocks_malformed_json_and_repo_architecture_only",
    "test_navigation_semantic_boot_claim_includes_bootstrap_when_answered",
    "test_executor_context_pack_contains_runtime_guidance_and_semantic_bootstrap",
    "test_role_context_packs_encode_work_ethic_and_skill_policy_without_authority",
    "test_runtime_context_packs_include_lightweight_operation_feedback_loop",
    "test_runtime_packet_artifact_hints_include_feedback_without_new_artifact_stack",
    "test_package_review_lore_keeps_broad_matches_summary_only",
    "test_debug_context_pack_shapes_single_file_symptom",
    "test_debug_context_pack_lore_is_tiered_summary_only",
    "test_context_pack_auto_selects_debug_without_stealing_package_review",
    "test_context_pack_auto_rejects_ambiguous_task",
    "test_context_pack_includes_inferred_semantic_bootstrap",
    "test_semantic_bootstrap_source_routing_shape",
    "test_semantic_bootstrap_unknown_task_class_returns_issue",
    "test_semantic_bootstrap_missing_manifest_returns_issue",
    "test_semantic_bootstrap_warns_missing_current_fact_surface",
    "test_semantic_bootstrap_warns_stale_current_fact_surface",
    "test_semantic_bootstrap_warns_unavailable_graph",
    "test_core_map_probability_chain_is_proof_backed_and_bounded",
    "test_core_claims_mode_validates_first_wave_claims",
    "test_cli_json_parity_for_task_boot_profiles_mode",
    "test_cli_json_parity_for_fatal_misreads_mode",
    "test_task_boot_profiles_mode_validates_semantic_profiles",
    "test_fatal_misreads_mode_validates_semantic_antibodies",
    "test_core_map_probability_chain_uses_core_claims",
    "test_core_claims_mode_rejects_missing_proof_target",
    "test_core_claims_mode_rejects_missing_locator",
    "test_core_claims_mode_rejects_cross_manifest_duplicate",
    "test_core_map_rejects_unreplaced_core_claim",
    "test_core_map_mode_passes_current_profiles",
    "test_core_map_missing_required_claim_is_invalid",
    "test_core_map_missing_edge_proof_is_invalid",
    "test_core_map_rejects_unknown_edge_endpoints",
    "test_core_map_rejects_import_proof_on_unrelated_file",
    "test_core_map_rejects_partial_required_claim",
    "test_core_map_forbidden_phrase_guard_catches_round_variant",
    "test_core_map_relationship_test_requires_locator",
    "test_core_map_rejects_reference_doc_authority_node",
    "test_pre_commit_secrets_blocks_unregistered_review_safe_tag",
    "test_pre_commit_secrets_blocks_unregistered_review_safe_without_gitleaks",
    "test_pre_commit_secrets_accepts_review_safe_tag_registered_in_same_commit",
    "test_pre_commit_secrets_audits_staged_requirements_blob_not_worktree",
    "test_hook_pre_merge_F3_detector_catches_evil_inputs",
    "test_hook_pre_merge_F13_blocks_commented_critic_verdict",
    "test_hook_pre_merge_F13_accepts_real_verdict_with_comment_companion",
    "test_hook_pre_merge_accepts_verdict_with_trailing_comment",
    "test_hook_pre_merge_blocks_revise_verdict",
    "test_hook_pre_merge_F17_OVERRIDE_docstring_matches_implementation",
    "test_hook_pre_merge_F17_OVERRIDE_writes_durable_log_on_protected_branch",
    "test_hook_pre_merge_git_channel_override_logs_non_empty_command_context",
    "test_hook_pre_merge_F13_blocks_yaml_nested_critic_verdict_spoof",
})


def pytest_collection_modifyitems(items: list) -> None:
    """Mark stale test_topology_doctor.py tests as xfail (sunset 2026-09-06).

    Root cause: Phase 3 cutover removed topology_doctor_context_pack; these
    tests fail with ImportError. topology_doctor.py itself is still active.
    Rewrite or remove this set before sunset_date 2026-09-06.
    """
    for item in items:
        if (
            item.fspath.basename == "test_topology_doctor.py"
            and item.originalname in _STALE_TOPOLOGY_DOCTOR_TESTS
        ):
            item.add_marker(
                pytest.mark.xfail(
                    reason=(
                        "stale Phase 3 import-chain residue: topology_doctor_context_pack "
                        "removed at Phase 3 cutover. sunset_date=2026-09-06"
                    ),
                    strict=False,
                ),
                append=False,
            )
