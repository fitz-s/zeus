# Created: 2026-07-08
# Last reused or audited: 2026-07-08
# Authority basis: docs/rebuild/EXECUTION_MASTER_2026-07-07.md §E R2-a
#                  docs/rebuild/whole_system_first_principles_2026-07-07.md §2.4
"""ONE canonical local-truth snapshot: what Zeus itself believes it holds.

Pre-R2 survey (three competing "local truth" definitions this contract
subsumes -- each file independently joins a subset of {venue_commands,
position_current, collateral_reservations} for its own narrow purpose and
carries its own copy of the fill-dedup CTE this module now imports instead
of copying):

    file:line                                          | subsumed content
    ----------------------------------------------------|---------------------------------
    src/execution/command_recovery.py:273               | canonical fill view per command
      (``_canonical_trade_fact_cte`` + ~20 repair-       | (used to decide whether
      candidate queries, e.g. :5577 entry-lot            | position_current/venue_commands
      materialization, :9517 partial-remainder fill      | projection has drifted from the
      coverage)                                          | venue's own fill evidence)
    src/execution/exchange_reconcile.py:173              | canonical fill view per command,
      (``_canonical_trade_fact_cte`` + :516               | scoped to the M5 reconcile-sweep
      ``run_reconcile_sweep``)                           | local-vs-exchange diff
    src/state/venue_command_repo.py:2329                 | canonical fill view for a single
      (``canonical_trade_fact`` inlined in                | (command_id, trade_id, venue_
      ``_reconstruct_trade``)                            | order_id) triplet (deterministic
                                                          | command-state reconstruction)

None of the three joins ``collateral_reservations`` into its local-truth
view (reservation state is checked ad hoc, elsewhere, per repair pass) --
this contract is the first to unify command state + position projection +
reservation lifecycle into one snapshot, which is exactly what the R0
verifier's reservation-orphan finding (see
src/reconcile/diff_engine.py::_predicate_reservation_orphan) needs to see in
one place. The venue's OWN fill fact stream (canonical_trade_fact_cte
output) deliberately does NOT live here -- it is external evidence, not
Zeus's own bookkeeping, and belongs to :mod:`src.reconcile.chain_truth`
instead (see that module's docstring for the local/chain split rationale).

Public surface:
    LocalCommandTruth   -- one venue_commands row + its (optional) reservation.
    LocalPositionTruth  -- one position_current row, read-only view.
    LocalTruthSnapshot  -- the full snapshot: commands + positions, keyed by id.
    load_local_truth_snapshot(conn) -> LocalTruthSnapshot
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class LocalCommandTruth:
    """One venue_commands row, joined with its collateral_reservations row
    (if any -- reservations are 1:1 with command_id, the reservation table's
    primary key). Reservation fields are None when no reservation was ever
    created for this command (e.g. a pre-CAS-ledger legacy command).
    """

    command_id: str
    position_id: str
    decision_id: str
    intent_kind: str
    side: str
    state: str
    token_id: str
    market_id: str
    size: float
    price: float
    venue_order_id: Optional[str]
    created_at: str
    updated_at: str
    reservation_type: Optional[str] = None
    reservation_amount: Optional[int] = None
    reservation_converted_amount: Optional[int] = None
    reservation_created_at: Optional[str] = None
    reservation_released_at: Optional[str] = None
    reservation_release_reason: Optional[str] = None

    @property
    def has_reservation(self) -> bool:
        return self.reservation_amount is not None

    @property
    def reservation_released(self) -> bool:
        return self.reservation_released_at is not None


@dataclass(frozen=True)
class LocalPositionTruth:
    """Read-only view of a position_current row (Zeus's own close-economics
    projection included -- realized_pnl_usd/exit_price/settled_at).
    """

    position_id: str
    phase: str
    chain_state: str
    city: str
    target_date: str
    temperature_metric: str
    bin_label: str
    direction: str
    token_id: str
    no_token_id: str
    condition_id: str
    chain_shares: Optional[float]
    shares: Optional[float]
    cost_basis_usd: Optional[float]
    entry_price: Optional[float]
    realized_pnl_usd: Optional[float]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    settled_at: Optional[str]
    strategy_key: str
    updated_at: str

    def held_token_id(self) -> str:
        if self.direction == "buy_no":
            return self.no_token_id
        return self.token_id


@dataclass
class LocalTruthSnapshot:
    generated_at: str
    commands: dict[str, LocalCommandTruth] = field(default_factory=dict)
    positions: dict[str, LocalPositionTruth] = field(default_factory=dict)

    def commands_for_position(self, position_id: str) -> list[LocalCommandTruth]:
        return [c for c in self.commands.values() if c.position_id == position_id]


_COMMAND_SQL = """
    SELECT
        vc.command_id, vc.position_id, vc.decision_id, vc.intent_kind, vc.side,
        vc.state, vc.token_id, vc.market_id, vc.size, vc.price,
        vc.venue_order_id, vc.created_at, vc.updated_at,
        cr.reservation_type, cr.amount AS reservation_amount,
        cr.converted_amount AS reservation_converted_amount,
        cr.created_at AS reservation_created_at,
        cr.released_at AS reservation_released_at,
        cr.release_reason AS reservation_release_reason
      FROM venue_commands vc
      LEFT JOIN collateral_reservations cr ON cr.command_id = vc.command_id
"""

_POSITION_SQL = """
    SELECT
        position_id, phase, chain_state, city, target_date, temperature_metric,
        bin_label, direction, token_id, no_token_id, condition_id, chain_shares,
        shares, cost_basis_usd, entry_price, realized_pnl_usd, exit_price,
        exit_reason, settled_at, strategy_key, updated_at
      FROM position_current
"""


def load_local_truth_snapshot(
    conn: sqlite3.Connection, *, now: Optional[datetime] = None
) -> LocalTruthSnapshot:
    """Load the full local-truth snapshot. Read-only; no network I/O."""

    now = now or datetime.now(timezone.utc)
    snapshot = LocalTruthSnapshot(generated_at=now.isoformat())

    for row in conn.execute(_COMMAND_SQL).fetchall():
        command_id = str(row["command_id"])
        snapshot.commands[command_id] = LocalCommandTruth(
            command_id=command_id,
            position_id=str(row["position_id"] or ""),
            decision_id=str(row["decision_id"] or ""),
            intent_kind=str(row["intent_kind"] or ""),
            side=str(row["side"] or ""),
            state=str(row["state"] or ""),
            token_id=str(row["token_id"] or ""),
            market_id=str(row["market_id"] or ""),
            size=float(row["size"] or 0.0),
            price=float(row["price"] or 0.0),
            venue_order_id=(str(row["venue_order_id"]) if row["venue_order_id"] is not None else None),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            reservation_type=(str(row["reservation_type"]) if row["reservation_type"] is not None else None),
            reservation_amount=(int(row["reservation_amount"]) if row["reservation_amount"] is not None else None),
            reservation_converted_amount=(
                int(row["reservation_converted_amount"])
                if row["reservation_converted_amount"] is not None
                else None
            ),
            reservation_created_at=(
                str(row["reservation_created_at"]) if row["reservation_created_at"] is not None else None
            ),
            reservation_released_at=(
                str(row["reservation_released_at"]) if row["reservation_released_at"] is not None else None
            ),
            reservation_release_reason=(
                str(row["reservation_release_reason"]) if row["reservation_release_reason"] is not None else None
            ),
        )

    for row in conn.execute(_POSITION_SQL).fetchall():
        position_id = str(row["position_id"])
        snapshot.positions[position_id] = LocalPositionTruth(
            position_id=position_id,
            phase=str(row["phase"] or ""),
            chain_state=str(row["chain_state"] or ""),
            city=str(row["city"] or ""),
            target_date=str(row["target_date"] or ""),
            temperature_metric=str(row["temperature_metric"] or "high"),
            bin_label=str(row["bin_label"] or ""),
            direction=str(row["direction"] or ""),
            token_id=str(row["token_id"] or ""),
            no_token_id=str(row["no_token_id"] or ""),
            condition_id=str(row["condition_id"] or ""),
            chain_shares=(float(row["chain_shares"]) if row["chain_shares"] is not None else None),
            shares=(float(row["shares"]) if row["shares"] is not None else None),
            cost_basis_usd=(float(row["cost_basis_usd"]) if row["cost_basis_usd"] is not None else None),
            entry_price=(float(row["entry_price"]) if row["entry_price"] is not None else None),
            realized_pnl_usd=(float(row["realized_pnl_usd"]) if row["realized_pnl_usd"] is not None else None),
            exit_price=(float(row["exit_price"]) if row["exit_price"] is not None else None),
            exit_reason=(str(row["exit_reason"]) if row["exit_reason"] is not None else None),
            settled_at=(str(row["settled_at"]) if row["settled_at"] is not None else None),
            strategy_key=str(row["strategy_key"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    return snapshot
