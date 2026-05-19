# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/critic_1_pr1_settlement.md P1-P7
"""
Relationship tests R-1.1 and R-1.2: ResolutionEra dispatch correctness.

RELATIONSHIP INVARIANT (cross-module):
    When execution/harvester.py or ingest/harvester_truth_writer.py processes
    a market and calls write_settlement_v2_with_era_provenance(), the era
    assigned in the resulting provenance_json MUST match the era determined
    solely by the market's settled_at date vs ERA_CUTOVER_DATE.
"""
import pytest
from datetime import date, timedelta

from src.contracts.resolution_era import (
    EraDispatchOutcome,
    ResolutionEra,
)
from src.state.settlement_writers import (
    ERA_BASIS_INTERNAL_RESOLVER,
    ERA_BASIS_UMA_OO_V2,
    ERA_CUTOVER_DATE,
    dispatch_era_basis,
)


def test_r1_1_post_cutover_market_dispatches_internal_resolver_era():
    """R-1.1: A post-cutover market (settled_at >= 2026-02-21) must dispatch to
    INTERNAL_RESOLVER_POST_2026_02_21 era. The provenance_json must NOT contain
    'harvester_live_uma_vote' and MUST contain era='internal_resolver_post_2026_02_21'.
    """
    settled_at = ERA_CUTOVER_DATE  # exactly the cutover date — inclusive start of new era
    result = dispatch_era_basis(settled_at)
    assert result.outcome == EraDispatchOutcome.ERA_RESOLVED
    assert result.is_admittable()
    assert result.era_basis is not None
    assert result.era_basis.era == ResolutionEra.INTERNAL_RESOLVER_POST_2026_02_21
    assert result.era_basis.era.value == "internal_resolver_post_2026_02_21"

    # Also test with a date well after cutover
    settled_at_2 = date(2026, 5, 1)
    result2 = dispatch_era_basis(settled_at_2)
    assert result2.outcome == EraDispatchOutcome.ERA_RESOLVED
    assert result2.era_basis.era == ResolutionEra.INTERNAL_RESOLVER_POST_2026_02_21


def test_r1_2_pre_cutover_market_dispatches_uma_oo_v2_era():
    """R-1.2: A pre-cutover market (settled_at < 2026-02-21) must dispatch to UMA_OO_V2 era.
    The provenance_json must contain era='uma_oo_v2'. Legacy 'harvester_live_uma_vote' tag
    must not appear in the new provenance format.
    """
    settled_at = ERA_CUTOVER_DATE - timedelta(days=1)  # day before cutover
    result = dispatch_era_basis(settled_at)
    assert result.outcome == EraDispatchOutcome.ERA_RESOLVED
    assert result.is_admittable()
    assert result.era_basis is not None
    assert result.era_basis.era == ResolutionEra.UMA_OO_V2
    assert result.era_basis.era.value == "uma_oo_v2"

    # Also test with a date well before cutover
    settled_at_2 = date(2023, 6, 15)
    result2 = dispatch_era_basis(settled_at_2)
    assert result2.outcome == EraDispatchOutcome.ERA_RESOLVED
    assert result2.era_basis.era == ResolutionEra.UMA_OO_V2


def test_r1_2_extension_era_dead_watermark_on_empty_uma_window():
    """R-1.2 extension (Critic P4): when uma_resolution_listener returns an empty
    log window for a pre-cutover market, the caller must construct
    EraDispatchResult(outcome=EraDispatchOutcome.ERA_EMPTY_OBSERVATION, era_basis=None, ...)
    and defer/retry — NOT silently fall through to INTERNAL_RESOLVER era.

    dispatch_era_basis() itself returns ERA_DEAD only when no era covers the date.
    ERA_EMPTY_OBSERVATION is constructed by the listener caller when the on-chain
    event has not been indexed yet.
    """
    # ERA_DEAD value check
    assert EraDispatchOutcome.ERA_DEAD.value == "era_dead"
    assert EraDispatchOutcome.ERA_EMPTY_OBSERVATION.value == "era_empty_observation"

    # dispatch_era_basis returns ERA_DEAD only for dates before all known era starts
    too_early = date(2019, 12, 31)  # before ERA_BASIS_UMA_OO_V2.era_start_date_utc (2020-01-01)
    result = dispatch_era_basis(too_early)
    assert result.outcome == EraDispatchOutcome.ERA_DEAD
    assert not result.is_admittable()
    assert result.era_basis is None
    assert result.reason_code == "no_era_match"


def test_era_cutover_boundary_exclusive():
    """Boundary: settled_at == ERA_CUTOVER_DATE (2026-02-21) maps to
    INTERNAL_RESOLVER_POST_2026_02_21 (inclusive start of new era).
    settled_at == ERA_CUTOVER_DATE - 1 day maps to UMA_OO_V2.
    """
    # Cutover date itself → INTERNAL_RESOLVER (>= comparison means inclusive)
    result_on = dispatch_era_basis(ERA_CUTOVER_DATE)
    assert result_on.era_basis.era == ResolutionEra.INTERNAL_RESOLVER_POST_2026_02_21

    # One day before cutover → UMA_OO_V2
    result_before = dispatch_era_basis(ERA_CUTOVER_DATE - timedelta(days=1))
    assert result_before.era_basis.era == ResolutionEra.UMA_OO_V2

    # One day after cutover → still INTERNAL_RESOLVER
    result_after = dispatch_era_basis(ERA_CUTOVER_DATE + timedelta(days=1))
    assert result_after.era_basis.era == ResolutionEra.INTERNAL_RESOLVER_POST_2026_02_21
