# Created: 2026-07-13
# Last audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md (LX-0R "契约+激活控制")
#   + docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt
#   (BLOCKER "cutover authority" — activation unit is ownership of the forbidden
#   columns themselves, not the projection funnel; census §精化 #1/#2 bypass-writer set).

"""Inventory of economics columns derivable from chain and venue facts.

This module owns only the static column set consumed by writer-census tooling. It
does not define an activation state, writer mode, or runtime authorization path.

Column set (seed, adjudicated by census §精化 #1/#2 + consult round-2 delta,
2026-07-13; ~11 named bypass writers below the projection funnel, plus the EDLI
clobber twin):
  - ``position_current``: shares, cost_basis_usd, entry_price, size_usd,
    chain_shares, chain_avg_price, chain_cost_basis_usd, realized_pnl_usd,
    exit_price, settlement_price — all CHAIN-DERIVABLE per the excision plan's
    disease test (a copy of, or a deterministic function over, chain-knowable fills/
    balances/payouts + Zeus order attribution).
  - ``edli_live_profit_audit``: pnl_usd, realized_edge, edge_value_usd,
    settlement_outcome, promotion_eligible — the "new disease surface" (census
    精化 #2): a second local P&L clobber twin of position_current's, written by
    settlement grading (src/analysis/settlement_skill_attribution.py) and
    src/events/live_profit_audit.py. Round-2 delta verdict: logical excision here
    (LX-T3, stop writing/treating as authority), physical table retirement stays R7
    (rehome the certificate/evidence links first).

The inventory is descriptive input for static analysis; live write authority remains
with the canonical executable paths and their current invariants.
"""

from __future__ import annotations

from dataclasses import dataclass


# --------------------------------------------------------------------------- #
# Forbidden economics-column set                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ForbiddenEconomicsColumn:
    """One column that is CHAIN-DERIVABLE (disease test per
    docs/rebuild/local_ledger_excision_2026-07-12.md "Disease definition") and
    therefore not a durable local economics authority."""

    table: str
    column: str
    description: str


FORBIDDEN_ECONOMICS_COLUMNS: tuple[ForbiddenEconomicsColumn, ...] = (
    # position_current — census §精化 #1: INSERT single funnel
    # (src/state/projection.py), but ~11 direct/dynamic UPDATE bypass sites
    # write these columns outside that funnel today.
    ForbiddenEconomicsColumn(
        "position_current", "shares",
        "Zeus-attributed share count — derivable from attributed fills.",
    ),
    ForbiddenEconomicsColumn(
        "position_current", "cost_basis_usd",
        "Cost basis — derivable from attributed fills + fees.",
    ),
    ForbiddenEconomicsColumn(
        "position_current", "entry_price",
        "Average entry price — derivable from attributed entry fills.",
    ),
    ForbiddenEconomicsColumn(
        "position_current", "size_usd",
        "Notional size — derivable from attributed fills.",
    ),
    ForbiddenEconomicsColumn(
        "position_current", "chain_shares",
        "Chain-observed share mirror — a copy of a chain-knowable balance.",
    ),
    ForbiddenEconomicsColumn(
        "position_current", "chain_avg_price",
        "Chain-observed average price mirror — derivable from chain fills.",
    ),
    ForbiddenEconomicsColumn(
        "position_current", "chain_cost_basis_usd",
        "Chain-observed cost basis mirror — derivable from chain fills.",
    ),
    ForbiddenEconomicsColumn(
        "position_current", "realized_pnl_usd",
        "Realized P&L — derivable from attributed fills + fees + payout "
        "(Exhibit A settled-clobber bug lives on this column).",
    ),
    ForbiddenEconomicsColumn(
        "position_current", "exit_price",
        "Exit fill price — derivable from attributed exit fills.",
    ),
    ForbiddenEconomicsColumn(
        "position_current", "settlement_price",
        "Settlement/payout price — derivable from chain payout observations.",
    ),
    # edli_live_profit_audit — census §精化 #2: new disease surface, same
    # clobber class as position_current.realized_pnl_usd. Round-2 delta:
    # logical excision at LX-T3 (stop write/authority), physical retirement R7.
    ForbiddenEconomicsColumn(
        "edli_live_profit_audit", "pnl_usd",
        "World-grade hold-to-settlement P&L label written at grading time — "
        "not chain-realized wallet P&L; misnamed authority per round-2 delta.",
    ),
    ForbiddenEconomicsColumn(
        "edli_live_profit_audit", "realized_edge",
        "Execution-quality metric (decision q vs fill price) computed at "
        "settlement time; legitimate analytical value, wrong mutable home.",
    ),
    ForbiddenEconomicsColumn(
        "edli_live_profit_audit", "edge_value_usd",
        "realized_edge * filled_size — same mutable-home defect as realized_edge.",
    ),
    ForbiddenEconomicsColumn(
        "edli_live_profit_audit", "settlement_outcome",
        "WU/world grade outcome written into a mutable audit row — belongs in "
        "a versioned, append-only settlement_learning_receipt (round-2 delta).",
    ),
    ForbiddenEconomicsColumn(
        "edli_live_profit_audit", "promotion_eligible",
        "Derived from the mutable columns above; inherits their clobber risk.",
    ),
)


FORBIDDEN_COLUMNS_BY_TABLE: dict[str, frozenset[str]] = {}
for _col in FORBIDDEN_ECONOMICS_COLUMNS:
    FORBIDDEN_COLUMNS_BY_TABLE.setdefault(_col.table, set()).add(_col.column)  # type: ignore[arg-type]
FORBIDDEN_COLUMNS_BY_TABLE = {
    table: frozenset(cols) for table, cols in FORBIDDEN_COLUMNS_BY_TABLE.items()
}
del _col


def is_forbidden_economics_column(table: str, column: str) -> bool:
    """True iff ``column`` on ``table`` is a member of
    ``FORBIDDEN_ECONOMICS_COLUMNS``. Pure lookup, case-sensitive (matches the
    live schema's exact column spelling)."""
    return column in FORBIDDEN_COLUMNS_BY_TABLE.get(table, frozenset())


__all__ = [
    "ForbiddenEconomicsColumn",
    "FORBIDDEN_ECONOMICS_COLUMNS",
    "FORBIDDEN_COLUMNS_BY_TABLE",
    "is_forbidden_economics_column",
]
