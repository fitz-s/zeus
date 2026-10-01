# Created: 2026-09-30
# Last audited: 2026-09-30
# Authority basis: thin early market law 2026-09-30 (terminal-gain ranking,
#   settlement-hold BUY admission, maker price menu); global_auction_receipt.
"""The capital-selection revision names the law the running selector applies.

Capital evidence, the decision certificate and RiskGuard cohort by this exact
string, so a law change without a bump would pool fills from two different
selectors. A bump starts a new evidence cohort (and clears the probation gate);
that consequence is deliberate and reported, never silent.
"""

from src.contracts.global_auction_receipt import (
    CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION,
)


def test_selection_revision_names_the_current_law():
    assert CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION == (
        "global_single_order_terminal_gain_settlement_hold_maker_menu_v7"
    )


def test_every_consumer_reads_the_one_shared_revision():
    from src.engine import event_reactor_adapter, global_batch_runtime
    from src.riskguard import riskguard
    from src.strategy.live_inference import no_trade_regret

    for module in (event_reactor_adapter, global_batch_runtime, riskguard, no_trade_regret):
        assert module.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION is (
            CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        ), module.__name__
