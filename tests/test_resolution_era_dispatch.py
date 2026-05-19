# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-17_strategy_vnext_phase0/PHASE_0_V4_ULTRAPLAN.md §D.1
#                  docs/operations/task_2026-05-17_strategy_vnext_phase0/critic_1_pr1_settlement.md P1-P7
"""
Relationship tests R-1.1 and R-1.2: ResolutionEra dispatch correctness.

SCAFFOLD — test bodies not yet implemented (xfail markers pending implementation).

RELATIONSHIP INVARIANT (cross-module):
    When execution/harvester.py or ingest/harvester_truth_writer.py processes
    a market and calls write_settlement_v2_with_era_provenance(), the era
    assigned in the resulting provenance_json MUST match the era determined
    solely by the market's settled_at date vs ERA_CUTOVER_DATE.

    This is a relationship test: it verifies the MODULE BOUNDARY property
    that the era dispatch in settlement_writers.py produces the same era
    classification as the gate logic in harvester.py / harvester_truth_writer.py.

TESTS (SCAFFOLD — bodies are docstrings only):

R-1.1 (post-cutover market → INTERNAL_RESOLVER era):
    A synthetic market with:
        umaResolutionStatus = "resolved"
        automaticallyResolved = True
        negRiskMarketID is set (not None)
        settled_at >= 2026-02-21 (post-cutover)
    MUST be classified ResolutionEra.INTERNAL_RESOLVER_POST_2026_02_21
    by dispatch_era_basis(settled_at.date()).
    The resulting provenance_json MUST NOT contain 'harvester_live_uma_vote'.
    The resulting provenance_json MUST contain era='internal_resolver_post_2026_02_21'.

R-1.2 (pre-cutover market → UMA_OO_V2 era):
    A synthetic market with the same fields but settled_at < 2026-02-21
    MUST be classified ResolutionEra.UMA_OO_V2 by dispatch_era_basis().
    The resulting provenance_json MUST contain era='uma_oo_v2'.
    The resulting provenance_json MUST NOT contain 'harvester_live_uma_vote'
    (era provenance field replaces legacy reconstruction_method tag).

ERA_DEAD WATERMARK (Critic P4):
    When uma_resolution_listener returns empty log windows
    (no events in window), the dispatcher MUST NOT silently fall through
    to UMA_OO_V2. It MUST use the explicit ERA_DEAD watermark signal.
    Test R-1.2 extension: inject empty uma_resolution lookup; assert
    ERA_DEAD is raised rather than silent INTERNAL_RESOLVER fallback.
"""
import pytest

# SCAFFOLD: import will succeed after implementation
# from src.contracts.resolution_era import ResolutionEra, ERA_CUTOVER_DATE
# from src.state.settlement_writers import dispatch_era_basis, ERA_BASIS_INTERNAL_RESOLVER, ERA_BASIS_UMA_OO_V2


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_r1_1_post_cutover_market_dispatches_internal_resolver_era():
    """R-1.1: A post-cutover market (settled_at >= 2026-02-21) must dispatch to
    INTERNAL_RESOLVER_POST_2026_02_21 era. The provenance_json must NOT contain
    'harvester_live_uma_vote' and MUST contain era='internal_resolver_post_2026_02_21'.
    """
    ...


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_r1_2_pre_cutover_market_dispatches_uma_oo_v2_era():
    """R-1.2: A pre-cutover market (settled_at < 2026-02-21) must dispatch to UMA_OO_V2 era.
    The provenance_json must contain era='uma_oo_v2'. Legacy 'harvester_live_uma_vote' tag
    must not appear in the new provenance format.
    """
    ...


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_r1_2_extension_era_dead_watermark_on_empty_uma_window():
    """R-1.2 extension (Critic P4): when uma_resolution_listener returns an empty
    log window for a pre-cutover market, the dispatcher must raise or return
    ERA_DEAD signal — NOT silently fall through to INTERNAL_RESOLVER era.
    """
    ...


@pytest.mark.xfail(reason="SCAFFOLD: implementation not yet written — PR 1 body phase")
def test_era_cutover_boundary_exclusive():
    """Boundary: settled_at == ERA_CUTOVER_DATE (2026-02-21) maps to
    INTERNAL_RESOLVER_POST_2026_02_21 (inclusive start of new era).
    settled_at == ERA_CUTOVER_DATE - 1 day maps to UMA_OO_V2.
    """
    ...
