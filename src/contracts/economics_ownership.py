# Created: 2026-07-13
# Last audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md (LX-0R "契约+激活控制")
#   + docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt
#   (BLOCKER "cutover authority" — activation unit is ownership of the forbidden
#   columns themselves, not the projection funnel; census §精化 #1/#2 bypass-writer set).

"""Forbidden economics-column ownership contract + trade-DB truth-epoch vocabulary.

LX-0R deliverable 1 (local_ledger_excision_2026-07-12.md): a central, typed definition
of every economics column that is CHAIN-DERIVABLE (a copy or local derivation of a
chain/venue fact) and therefore forbidden as a durable local authority once the trade
DB's truth epoch reaches ACTIVE_NEW. Mirrors the idiom of
``src.contracts.canonical_lifecycle`` (A1-A6 typed vocabulary, one module owning the
raw-vs-typed boundary) — this module is the single place a later LX-3R activation
packet (write firewall, selector migration, manifest-drift CI gate) looks up "is this
column forbidden" and "who may write it right now".

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

This module defines the SHAPE and the EPOCH-SCOPED PERMISSION RULE ONLY. It does not
open a connection, does not enforce anything at write time (that is LX-3R's DB-level
BEFORE INSERT/UPDATE guard, explicitly deferred per the round-2 delta: "defense in
depth, not the migration mechanism"), and does not change any live writer in this
packet — see src/state/truth_epoch.py for the epoch machinery this feeds, which stays
LEGACY until a future activation packet flips it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


# --------------------------------------------------------------------------- #
# Truth-epoch vocabulary (LX-0R)                                              #
# --------------------------------------------------------------------------- #

class TruthEpoch(StrEnum):
    """Trade-DB truth epoch (docs/rebuild/local_ledger_excision_2026-07-12.md
    "修订执行序 LX-0R..5R"). Monotonic — a later stage may only move forward:
    LEGACY -> PREPARE -> ACTIVE_NEW, never backward, never skipped.

    LEGACY:     today's default. Existing writers (the projection funnel + the
                census-named bypass UPDATEs) remain the permitted economics
                authority. Inert state for this whole packet.
    PREPARE:    LX-3R fenced cutover window — new entries paused, exits continue
                from command/trade facts, dual-capable code appends facts only
                under this branch; legacy economics writers are still the record
                of truth until activation completes.
    ACTIVE_NEW: the deterministic reducer / read-model is the sole economics
                authority. Forbidden columns admit ONLY reducer writes; every
                legacy writer named in this module's docstring must have already
                been converted (LX-2R) before a real deployment reaches this
                state — the DB-level guard at LX-3R is defense in depth, not the
                cutover mechanism itself.
    """

    LEGACY = "LEGACY"
    PREPARE = "PREPARE"
    ACTIVE_NEW = "ACTIVE_NEW"


# Declared forward order — the ONLY source of monotonic rank. Never re-derive
# this from enum declaration order implicitly elsewhere; import _TRUTH_EPOCH_ORDER
# or call truth_epoch_rank() so a reordering here cannot silently invert the guard.
_TRUTH_EPOCH_ORDER: tuple[TruthEpoch, ...] = (
    TruthEpoch.LEGACY,
    TruthEpoch.PREPARE,
    TruthEpoch.ACTIVE_NEW,
)


def truth_epoch_rank(epoch: TruthEpoch) -> int:
    """Monotonic rank of ``epoch`` (0=LEGACY, 1=PREPARE, 2=ACTIVE_NEW). Raises
    ValueError on anything not a member of ``_TRUTH_EPOCH_ORDER`` rather than
    guessing a rank for an unknown value."""
    try:
        return _TRUTH_EPOCH_ORDER.index(TruthEpoch(epoch))
    except ValueError:
        raise ValueError(f"unknown TruthEpoch: {epoch!r}") from None


class EconomicsWriterRole(StrEnum):
    """Who is permitted to write a forbidden economics column, for a given
    truth epoch. Exactly one role is permitted per epoch (see
    ``permitted_writer_role``) — there is never a dual-writer window."""

    # Today's shape: the projection funnel (src.state.projection) plus the
    # census-named direct/dynamic UPDATE bypass sites. Permitted ONLY while the
    # trade DB's truth epoch is LEGACY or PREPARE.
    LEGACY_PROJECTION_WRITER = "LEGACY_PROJECTION_WRITER"
    # The future single authority (LX-2R read-model reducer). The ONLY role
    # permitted to write a forbidden column once the trade DB's truth epoch is
    # ACTIVE_NEW.
    DETERMINISTIC_REDUCER = "DETERMINISTIC_REDUCER"


def permitted_writer_role(epoch: TruthEpoch) -> EconomicsWriterRole:
    """The single writer role permitted to write a forbidden economics column
    while the trade DB carries ``epoch``. ACTIVE_NEW is the only epoch that
    hands authority to the reducer; LEGACY and PREPARE both still recognize
    today's legacy writers (PREPARE is a fenced-entry window, not yet a writer
    cutover — the cutover is the ACTIVE_NEW publish itself, per LX-3R)."""
    if TruthEpoch(epoch) is TruthEpoch.ACTIVE_NEW:
        return EconomicsWriterRole.DETERMINISTIC_REDUCER
    return EconomicsWriterRole.LEGACY_PROJECTION_WRITER


def is_writer_role_permitted(*, epoch: TruthEpoch, role: EconomicsWriterRole) -> bool:
    """True iff ``role`` is the role permitted to write forbidden economics
    columns under ``epoch``. Pure predicate — no DB access, no enforcement;
    LX-3R's BEFORE INSERT/UPDATE guard is the actual enforcement point."""
    return role is permitted_writer_role(epoch)


# --------------------------------------------------------------------------- #
# Forbidden economics-column set                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ForbiddenEconomicsColumn:
    """One column that is CHAIN-DERIVABLE (disease test per
    docs/rebuild/local_ledger_excision_2026-07-12.md "Disease definition") and
    therefore forbidden as a durable local economics authority once the trade
    DB's truth epoch reaches ACTIVE_NEW."""

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
    "TruthEpoch",
    "truth_epoch_rank",
    "EconomicsWriterRole",
    "permitted_writer_role",
    "is_writer_role_permitted",
    "ForbiddenEconomicsColumn",
    "FORBIDDEN_ECONOMICS_COLUMNS",
    "FORBIDDEN_COLUMNS_BY_TABLE",
    "is_forbidden_economics_column",
]
