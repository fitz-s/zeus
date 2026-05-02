# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45a relationship-test contract pack.
"""PR45a data daemon readiness-state relationship contracts.

This module is an executable reference contract for the future readiness repo
and builder. Later phases should route these assertions through production code
without weakening the expected status/reason relationships.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
SHADOW_ONLY = "SHADOW_ONLY"
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class DependencyFacts:
    source_health_fresh: bool = True
    source_run_present: bool = True
    source_run_status: str = "SUCCESS"
    source_run_partial: bool = False
    data_coverage_status: str = "WRITTEN"
    release_provenance_present: bool = True
    origin_mode: str = "SCHEDULED_LIVE"
    causal_live_proof: bool = False
    active_hole: bool = False
    source_contract_status: str = "MATCH"
    market_topology_status: str = "CURRENT"
    quote_status: str = "NOT_CHECKED"
    settlement_time_law_ready: bool = False


@dataclass(frozen=True)
class ReadinessDecision:
    status: str
    reason_codes: tuple[str, ...]


def _evaluate_reference_readiness(
    facts: DependencyFacts,
    *,
    strategy_key: str = "opening_inertia",
) -> ReadinessDecision:
    if strategy_key == "settlement_capture" and not facts.settlement_time_law_ready:
        return ReadinessDecision(SHADOW_ONLY, ("SETTLEMENT_TIME_LAW_PENDING",))
    if not facts.source_run_present:
        return ReadinessDecision(BLOCKED, ("SOURCE_RUN_MISSING",))
    if not facts.release_provenance_present:
        return ReadinessDecision(BLOCKED, ("SOURCE_RELEASE_PROVENANCE_MISSING",))
    if facts.source_run_status == "FAILED":
        return ReadinessDecision(BLOCKED, ("SOURCE_RUN_FAILED",))
    if facts.source_run_status == "SKIPPED_NOT_RELEASED":
        return ReadinessDecision(BLOCKED, ("SOURCE_RUN_NOT_RELEASED",))
    if facts.source_run_partial:
        return ReadinessDecision(BLOCKED, ("SOURCE_RUN_PARTIAL",))
    if facts.data_coverage_status != "WRITTEN":
        return ReadinessDecision(BLOCKED, (f"DATA_COVERAGE_{facts.data_coverage_status}",))
    if facts.origin_mode != "SCHEDULED_LIVE" and not facts.causal_live_proof:
        return ReadinessDecision(SHADOW_ONLY, ("BACKFILL_ONLY",))
    if facts.active_hole:
        return ReadinessDecision(BLOCKED, ("DATA_COVERAGE_HOLE_ACTIVE",))
    if facts.source_contract_status != "MATCH":
        return ReadinessDecision(BLOCKED, ("SOURCE_CONTRACT_MISMATCH",))
    if facts.market_topology_status != "CURRENT":
        return ReadinessDecision(BLOCKED, (f"MARKET_TOPOLOGY_{facts.market_topology_status}",))
    if not facts.source_health_fresh:
        return ReadinessDecision(BLOCKED, ("SOURCE_HEALTH_STALE",))
    return ReadinessDecision(LIVE_ELIGIBLE, ("QUOTE_NOT_APPLICABLE_AT_ENTRY",))


def _apply_dependency_update(
    previous: ReadinessDecision,
    facts: DependencyFacts,
    *,
    strategy_key: str = "opening_inertia",
) -> ReadinessDecision:
    updated = _evaluate_reference_readiness(facts, strategy_key=strategy_key)
    if previous.status == LIVE_ELIGIBLE and updated.status == LIVE_ELIGIBLE:
        return previous
    return updated


def test_failed_source_run_invalidates_prior_live_eligible() -> None:
    previous = ReadinessDecision(LIVE_ELIGIBLE, ("ALL_DEPENDENCIES_READY",))

    updated = _apply_dependency_update(
        previous,
        replace(DependencyFacts(), source_run_status="FAILED"),
    )

    assert updated.status == BLOCKED
    assert "SOURCE_RUN_FAILED" in updated.reason_codes


def test_partial_run_overwrites_green_to_blocked() -> None:
    previous = ReadinessDecision(LIVE_ELIGIBLE, ("ALL_DEPENDENCIES_READY",))

    updated = _apply_dependency_update(
        previous,
        replace(DependencyFacts(), source_run_partial=True),
    )

    assert updated.status == BLOCKED
    assert "SOURCE_RUN_PARTIAL" in updated.reason_codes


def test_hole_detection_clears_green_scope() -> None:
    previous = ReadinessDecision(LIVE_ELIGIBLE, ("ALL_DEPENDENCIES_READY",))

    updated = _apply_dependency_update(
        previous,
        replace(DependencyFacts(), active_hole=True),
    )

    assert updated.status == BLOCKED
    assert "DATA_COVERAGE_HOLE_ACTIVE" in updated.reason_codes


def test_source_contract_mismatch_overwrites_green_scope() -> None:
    previous = ReadinessDecision(LIVE_ELIGIBLE, ("ALL_DEPENDENCIES_READY",))

    updated = _apply_dependency_update(
        previous,
        replace(DependencyFacts(), source_contract_status="MISMATCH"),
    )

    assert updated.status == BLOCKED
    assert "SOURCE_CONTRACT_MISMATCH" in updated.reason_codes


def test_backfill_origin_defaults_shadow_only_without_causal_proof() -> None:
    decision = _evaluate_reference_readiness(
        replace(DependencyFacts(), origin_mode="HOLE_BACKFILL", causal_live_proof=False),
    )

    assert decision.status == SHADOW_ONLY
    assert "BACKFILL_ONLY" in decision.reason_codes


def test_market_topology_stale_or_empty_fallback_blocks_entry_scope() -> None:
    for topology_status in ("STALE", "EMPTY_FALLBACK"):
        decision = _evaluate_reference_readiness(
            replace(DependencyFacts(), market_topology_status=topology_status),
        )

        assert decision.status == BLOCKED
        assert f"MARKET_TOPOLOGY_{topology_status}" in decision.reason_codes


def test_quote_freshness_is_not_entry_readiness() -> None:
    unchecked_quote = _evaluate_reference_readiness(
        replace(DependencyFacts(), quote_status="NOT_CHECKED"),
    )
    stale_quote = _evaluate_reference_readiness(
        replace(DependencyFacts(), quote_status="STALE"),
    )

    assert unchecked_quote.status == LIVE_ELIGIBLE
    assert stale_quote.status == LIVE_ELIGIBLE
    assert unchecked_quote.reason_codes == ("QUOTE_NOT_APPLICABLE_AT_ENTRY",)
    assert stale_quote.reason_codes == ("QUOTE_NOT_APPLICABLE_AT_ENTRY",)


def test_settlement_capture_forced_blocked_until_settlement_time_law() -> None:
    pending = _evaluate_reference_readiness(
        DependencyFacts(),
        strategy_key="settlement_capture",
    )
    ready = _evaluate_reference_readiness(
        replace(DependencyFacts(), settlement_time_law_ready=True),
        strategy_key="settlement_capture",
    )

    assert pending.status == SHADOW_ONLY
    assert "SETTLEMENT_TIME_LAW_PENDING" in pending.reason_codes
    assert ready.status == LIVE_ELIGIBLE
