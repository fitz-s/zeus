# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R0-a
#                  docs/rebuild/whole_system_first_principles_2026-07-07.md §2.6
"""Single source of truth for realized-P&L computation on position close.

R0-a (close-economics unification, 2026-07-08): before this module, five
terminal position-close paths existed and the realized-P&L formula (shares *
exit_price - cost_basis_usd, guarded by entry_price > 0) was implemented
independently in up to four places:

  1. src.state.portfolio._compute_realized_pnl (in-memory Position close,
     used by compute_economic_close / compute_settlement_close, consumed by
     src.execution.exit_lifecycle's normal exit-fill dual-write and
     src.execution.harvester's Gamma-capture settlement dual-write). This was
     always correct.
  2. src.execution.command_recovery._append_exit_filled_projection (restart
     recovery re-projects a filled exit from the raw position_current row via
     a SimpleNamespace stand-in). Originally forgot to set "pnl" at all
     (Bug A, found 2026-07-07); patched inline with a hand-rolled Decimal
     copy of the formula at commit 4502173671.
  3. src.execution.exchange_reconcile._ensure_exit_fill_position_event (same
     SimpleNamespace-stand-in shape as #2, same Bug A, same commit).
  4. src.state.chain_mirror_reconciler._apply_settlement_finding (chain-
     discovered settlement writes the projection dict directly, bypassing
     build_position_current_projection entirely). Computed a pnl value for
     its audit payload but originally never copied it into the durable
     projection (Bug B, same commit).

Bug A/B were patched individually (three independent inline reimplementations
of the same math). That leaves the class of bug -- "a close path forgets the
pnl key" -- one refactor away from recurring the next time a sixth close path
is added. This module is the one place the formula may live; every close
path calls compute_realized_pnl_usd() instead of reimplementing it, and
src.state.projection.upsert_position_current (the single funnel all five
paths write position_current through, directly or via
src.state.ledger.append_many_and_project) fails loudly
(MissingRealizedPnlOnCloseError) if a position transitions into
economically_closed/settled without one -- see that module for the
structural backstop.
"""
from __future__ import annotations


def compute_realized_pnl_usd(
    *,
    shares: float,
    exit_price: float,
    cost_basis_usd: float,
    entry_price: float | None = None,
) -> float:
    """The one true realized-P&L formula for a closed position.

    Mirrors the formula src.state.portfolio._compute_realized_pnl used
    before this module existed: round(shares * exit_price - cost_basis_usd, 2).

    entry_price is an optional guard: when provided and <= 0 (a position that
    was never actually filled has no meaningful realized economics), this
    returns 0.0 rather than a misleading nonzero number -- this mirrors the
    guard portfolio.py / command_recovery.py / exchange_reconcile.py already
    apply. When entry_price is omitted (None), no guard is applied; this
    matches chain_mirror_reconciler's pre-existing behavior, which grades a
    chain-verified settlement (won/lost) without an entry_price check.
    """
    if entry_price is not None and entry_price <= 0:
        return 0.0
    return round(float(shares) * float(exit_price) - float(cost_basis_usd), 2)
