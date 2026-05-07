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
    # NOTE: test_pre_commit_secrets_* and test_hook_pre_merge_* are intentionally
    # NOT in this set. Those tests cover active dispatch.py hook logic changed in
    # PR #72 and must remain live. Only context_pack import-residue tests belong here.
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


# ---------------------------------------------------------------------------
# SQLite Writer-Lock Antibody — Phase 0 (v4 plan §10).
#
# Collection-time enforcement that scans src/ + scripts/ for:
#   1. Direct sqlite3.connect() outside the canonical-shim allowlist.
#   2. (Reserved) _connect() calls without write_class kwarg in scope —
#      activated in Phase 1 once retrofit lands; Phase 0 is non-blocking.
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
# Phase 0 posture: this antibody warns/reports rather than blocks for
# checks (2) and (3) so Phase 0 ships without forcing the 32+ BULK and
# 14 LIVE caller retrofits. Check (1) (direct sqlite3.connect outside
# allowlist) is also reported but not fatal yet — Phase 1.0 / 1.x will
# tighten the failure mode once the canonical shim is the only writer.
# ---------------------------------------------------------------------------

import ast as _wla_ast
import json as _wla_json
from pathlib import Path as _wla_Path

_WLA_REPO_ROOT = _wla_Path(__file__).resolve().parent.parent
_WLA_SCAN_ROOTS = (_WLA_REPO_ROOT / "src", _WLA_REPO_ROOT / "scripts")
_WLA_CACHE_PATH = _WLA_REPO_ROOT / ".pytest_cache" / "writer_lock_antibody.json"

# Allowlisted files where direct ``sqlite3.connect`` is permitted. Phase 0
# captures the canonical shim and the lock helper itself; Phase 1.x will
# tighten this list as production callers are migrated.
_WLA_SQLITE_CONNECT_ALLOWLIST = frozenset({
    "src/state/db.py",                  # canonical shim
    "src/state/db_writer_lock.py",      # helper (does not connect)
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

    Phase 0 posture: report findings via warning, do NOT fail the run. The
    antibody hardens to a fail-the-run posture in Phase 1.x once production
    retrofits are in place (plan §5 step 1.x).
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
        # Phase 0: report via warning only; not a CI gate yet.
        # Format the allowlist directly into the message so it stays in
        # sync with `_WLA_SQLITE_CONNECT_ALLOWLIST` if entries are added
        # in Phase 1.x (PR #81 review feedback).
        allowlist_str = ", ".join(sorted(_WLA_SQLITE_CONNECT_ALLOWLIST))
        config.issue_config_time_warning(
            UserWarning(
                "writer-lock antibody (Phase 0 informational): "
                f"{len(findings)} direct sqlite3.connect() site(s) outside "
                f"allowlist [{allowlist_str}]. Examples: "
                f"{findings[:3]}. Phase 1.x will tighten this to fail-CI."
            ),
            stacklevel=1,
        )
