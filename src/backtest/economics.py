"""ECONOMICS purpose tombstone.

Per packet 2026-04-27 §01 §3.C: the ECONOMICS lane is structurally
impossible until upstream data unblocks (market_events_v2 populated +
parity contracts pass). Rather than emit `pnl_available: False`
limitation flags from a loop that runs anyway, we refuse to run.

When data-layer P4.A unblocks, this module's body fills in. Until
then, callers see the unblock pointer in the error itself.
"""

from typing import NoReturn

from src.backtest.purpose import PurposeContractViolation


def run_economics(*_, **__) -> NoReturn:
    raise PurposeContractViolation(
        "ECONOMICS purpose is tombstoned. It requires populated "
        "market_events_v2 + market_price_history + parity contracts "
        "(market_price_linkage='full', Sizing.KELLY_BOOTSTRAP, "
        "Selection.BH_FDR). See unblock plan at "
        "docs/operations/task_2026-04-27_backtest_first_principles_review/"
        "02_blocker_handling_plan.md §3.B."
    )
