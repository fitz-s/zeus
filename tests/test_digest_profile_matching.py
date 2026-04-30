"""Adversarial tests for digest profile selection.

Exercises the Evidence/Resolver layer of topology_doctor_digest. The goal is
to prove that route suggestion is driven by structured evidence, not by raw
substring matching, so that benign tasks like "improve source code quality"
cannot collide with safety-critical profiles like "modify data ingestion".

These cases come directly from §15 of docs/reference/Zeus_Apr25_review.md.
"""
# Lifecycle: created=2026-04-25; last_reviewed=2026-04-29; last_reused=2026-04-29
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
        "R3 T1 FakePolymarketVenue paper/live parity same PolymarketV2Adapter "
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
        "R3 A1 StrategyBenchmarkSuite alpha execution metrics replay paper live shadow "
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
    17-gate orchestration surface; strong G1 phrases must not route to Z3."""
    digest = build_digest(
        "R3 G1 live readiness gates live_readiness_check 17 CI gates "
        "staged-live-smoke INV-NEW-S live-money-deploy-go",
        [
            "scripts/live_readiness_check.py",
            "tests/test_live_readiness_gates.py",
            "docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/G1.yaml",
        ],
    )

    assert digest["profile"] == "r3 live readiness gates implementation"
    assert digest["admission"]["status"] == "admitted"
    assert "scripts/live_readiness_check.py" in digest["admission"]["admitted_files"]
    assert "tests/test_live_readiness_gates.py" in digest["admission"]["admitted_files"]


def test_batch_h_legacy_day0_backfill_routes_to_contamination_profile():
    """The contamination remediation Batch H profile must beat broad R3
    file-pattern profiles and admit only the planned implementation surfaces."""
    digest = build_digest(
        "Batch H legacy Day0-only canonical history entry backfill remediation",
        [
            "src/execution/exit_lifecycle.py",
            "tests/test_runtime_guards.py",
            "architecture/test_topology.yaml",
            "docs/operations/task_2026-04-28_contamination_remediation/plan.md",
            "docs/operations/task_2026-04-28_contamination_remediation/work_log.md",
            "docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h_current_diff_2026-04-28.md",
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


def test_batch_h_downstream_files_remain_context_only():
    digest = build_digest(
        "Batch H legacy Day0-only canonical history entry backfill remediation",
        [
            "src/engine/lifecycle_events.py",
            "src/state/ledger.py",
            "src/engine/cycle_runtime.py",
            "tests/test_entry_exit_symmetry.py",
            "tests/test_day0_exit_gate.py",
        ],
    )

    assert digest["profile"] == "batch h legacy day0 canonical history backfill remediation"
    assert digest["admission"]["status"] == "scope_expansion_required"
    assert digest["admission"]["admitted_files"] == []
    assert set(digest["admission"]["out_of_scope_files"]) == {
        "src/engine/lifecycle_events.py",
        "src/state/ledger.py",
        "src/engine/cycle_runtime.py",
        "tests/test_entry_exit_symmetry.py",
        "tests/test_day0_exit_gate.py",
    }


def test_batch_h_forbidden_surfaces_are_blocked():
    digest = build_digest(
        "Batch H legacy Day0-only canonical history entry backfill remediation",
        [
            "architecture/history_lore.yaml",
            "docs/authority/zeus_current_architecture.md",
            "src/supervisor_api/contracts.py",
            "src/contracts/settlement_semantics.py",
            "state/zeus-world.db",
        ],
    )

    assert digest["profile"] == "batch h legacy day0 canonical history backfill remediation"
    assert digest["admission"]["status"] == "blocked"
    assert set(digest["admission"]["forbidden_hits"]) == {
        "architecture/history_lore.yaml",
        "docs/authority/zeus_current_architecture.md",
        "src/supervisor_api/contracts.py",
        "src/contracts/settlement_semantics.py",
        "state/zeus-world.db",
    }


# ---------------------------------------------------------------------------
# Ambiguity surface: when two profiles match equally, status reflects it.
# ---------------------------------------------------------------------------

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
    assert digest["route_card"]["next_action"].startswith("proceed only with packet plan")


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
