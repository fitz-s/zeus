# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: architecture doc §1 exits row (C5 marginal rule b·Σq_j/W_j > q_i/W_i;
#   ExitContext plumbing REUSE, rule body replace); W3.EXIT brief (W_j state is greenfield,
#   evaluate_exit precedence-chain split, exit q_version gap at executor.py:4394);
#   CONSULT REV-2 rulings 2026-07-03 (wealth from the ledger snapshot on the joint atom axis;
#   ZeroWealthOutcomeError typed fail-closed; ExitPrecheckResult tripwire precedence BEFORE
#   economics run).
"""Exits as the same solve — the C5 marginal interface.

The exit decision is not a separate rule lane: a held position is an ENDOWMENT the solver
re-evaluates every event. Selling a claim with terminal payoff vector ``h`` is menu item
``sell_holding``; the C5 marginal condition is the mathematical form of "does the solve want
to exchange this claim for current cash":

    sell one unit at bid b iff   b · Σ_j q_j / W_j  >  Σ_j q_j h_j / W_j

(marginal log-utility of the cash proceeds spread over all outcomes exceeds the marginal
log-utility of the surrendered claim). For YES_i, only ``h_i=1``; for NO_i, ``h_j=1`` for
every ``j != i``. This single vector form is side-symmetric.

WHAT SURVIVES vs WHAT THIS REPLACES (W3.EXIT brief; portfolio.py:946-1467):
* REUSE: ExitContext/ExitDecision dataclasses, _build_exit_context threading,
  exit_lifecycle.py order mechanics, and the fail-closed PRECEDENCE TRIPWIRES that are axioms
  not economics — RED force-exit, EVIDENCE_UNAVAILABLE, missing-authority, day0-zero-probability,
  settlement-imminent routing. These run BEFORE the marginal rule, unchanged, and are now a
  TYPED input (``ExitPrecheckResult``, consult REV-2) so the economic predicate can never run
  after a hard tripwire fires.
* REPLACE: the economic rule body — win-rate floor, forward_edge thresholds, 2-consecutive
  EDGE_REVERSAL counters, HoldValue EV gates. NOTE the floor constant has 3 consumers OUTSIDE
  portfolio.py (entry-side admission) — exits stop READING it; deleting the constant is the W5
  taker-quality-floors packet, not this one.

W_a STATE IS GREENFIELD (consult REV-2 blocker): no per-outcome-atom holdings vector exists
today, and it must come from the CAS ledger snapshot — open positions grouped by joint atom
PLUS spendable cash net of pending reservations, resting orders, and unsettled proceeds — not
per-family bins. ``build_wealth_by_atom`` derives it per evaluation (derive-don't-store); a
zero/negative-wealth atom is a typed ``ZeroWealthOutcomeError`` (log undefined), never a clamp.

The pure wealth builder and C5 marginal predicate are implemented here. Runtime integration
still requires the monitor to provide a current complete q and the exact reconciled ledger
snapshot; the pure core never reaches into either authority itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional

from src.solve.types import WealthStateByAtom


class ZeroWealthOutcomeError(ValueError):
    """A wealth state implies a non-positive endowment in some outcome atom.

    Log-utility is undefined at zero wealth, so a solve cannot proceed against such a
    baseline. Fail-closed (typed error), never a silent clamp to epsilon — a position set
    that zeroes an outcome's wealth is a data/derivation fault to surface (maps to a
    deterministic no-trade / global-RED upstream), not to paper over (consult REV-2:
    exits.py must define the typed error + fail-closed mapping before any log() call).
    """


@dataclass(frozen=True)
class ExitPrecheckResult:
    """The fail-closed tripwire verdict, computed BEFORE any economic marginal (consult REV-2).

    When ``hard_tripwire_fired`` is True the economic predicate MUST NOT run — the exit is
    decided by the axiom (``force_exit`` routes a RED/settlement-imminent close; a
    missing-authority / evidence-unavailable gate routes no-trade). ``tripwire_reason`` names
    the gate (RED / EVIDENCE_UNAVAILABLE / MISSING_AUTHORITY / DAY0_ZERO_PROB /
    SETTLEMENT_IMMINENT).
    """

    hard_tripwire_fired: bool
    tripwire_reason: Optional[str]
    force_exit: bool


def build_wealth_by_atom(
    *,
    family_key: str,
    atom_ids: tuple[str, ...],
    holdings_payout_by_atom_id: Mapping[str, float],
    spendable_cash_usd: float,
    reservations_usd: float = 0.0,
    ledger_snapshot_id: Optional[str] = None,
    source_positions: tuple[str, ...] = (),
) -> WealthStateByAtom:
    """Entry-side W_a: spendable cash (already NET of reservations) + per-atom holdings payout.

    Pure core with INJECTED inputs (W3.3 ruling): the caller supplies ``spendable_cash_usd`` —
    the CAS ledger's published spendable quantity, already net of pending-order reservations —
    and ``holdings_payout_by_atom_id`` — the payout the family's currently-held claims deliver in
    each joint outcome atom (shares × per-atom payout; a held YES on bin b pays in atom b, a held
    NO pays in every atom but b). This module reaches NO ledger connection itself; the seam-swap
    packet threads the real read at the bridge, and the shim/tests inject these values directly.

    Contract: ``W_a = spendable_cash + holdings_payout(a)`` for every atom in ``atom_ids`` (missing
    → 0 payout); wealth strictly positive in every atom (a zero/negative-wealth atom makes
    log-utility undefined — surface ``ZeroWealthOutcomeError``, never a silent clamp);
    ``ledger_snapshot_id`` stamped so the wealth state ties to the ledger read it came from.
    """
    cash = float(spendable_cash_usd)
    wealth_by_atom = {a: cash + float(holdings_payout_by_atom_id.get(a, 0.0)) for a in atom_ids}
    nonpos = [a for a in atom_ids if not wealth_by_atom[a] > 0.0]
    if nonpos:
        raise ZeroWealthOutcomeError(
            f"non-positive endowment wealth in atoms {nonpos} for family {family_key!r} "
            f"(spendable_cash={cash}); log-utility undefined — fail closed, never clamp"
        )
    return WealthStateByAtom(
        atom_ids=tuple(atom_ids),
        wealth_by_atom=wealth_by_atom,
        cash_usd=cash,
        reservations_usd=float(reservations_usd),
        ledger_snapshot_id=ledger_snapshot_id,
        source_positions=tuple(source_positions),
    )


def marginal_exit_condition(
    *,
    precheck: ExitPrecheckResult,
    bid: float,
    held_payoff_by_atom_id: Mapping[str, float],
    q_by_atom_id: Mapping[str, float],
    wealth: WealthStateByAtom,
) -> bool:
    """The C5 marginal condition for one unit: b·Σ_a q_a/W_a > Σ_a q_a h_a/W_a.

    ``precheck`` is consumed FIRST (consult REV-2): if a hard tripwire fired the economic
    predicate must not run. Pure predicate otherwise — the solver applies it implicitly through
    the objective (a sell_holding menu item with positive marginal ΔU); this explicit form
    exists for the monitor lane's cheap screen and for property tests (the two must agree on
    marginal direction at the current holdings point).
    """
    if not isinstance(precheck, ExitPrecheckResult):
        raise TypeError("precheck must be ExitPrecheckResult")
    if precheck.hard_tripwire_fired:
        if not str(precheck.tripwire_reason or "").strip():
            raise ValueError("active exit precheck requires a tripwire reason")
        return bool(precheck.force_exit)
    if precheck.force_exit or precheck.tripwire_reason is not None:
        raise ValueError("inactive exit precheck cannot carry a tripwire decision")

    bid_value = float(bid)
    if not math.isfinite(bid_value) or not 0.0 <= bid_value <= 1.0:
        raise ValueError("bid must be finite in [0, 1]")

    if not isinstance(wealth, WealthStateByAtom):
        raise TypeError("wealth must be WealthStateByAtom")
    atom_ids = tuple(wealth.atom_ids)
    if not atom_ids or len(set(atom_ids)) != len(atom_ids):
        raise ValueError("wealth atom axis must be non-empty and unique")
    atom_set = set(atom_ids)
    if (
        set(q_by_atom_id) != atom_set
        or set(held_payoff_by_atom_id) != atom_set
        or set(wealth.wealth_by_atom) != atom_set
    ):
        raise ValueError("q, held payoff, and wealth must cover the same complete atom axis")

    q: dict[str, float] = {}
    h: dict[str, float] = {}
    w: dict[str, float] = {}
    for atom_id in atom_ids:
        q_value = float(q_by_atom_id[atom_id])
        payoff_value = float(held_payoff_by_atom_id[atom_id])
        wealth_value = float(wealth.wealth_by_atom[atom_id])
        if not math.isfinite(q_value) or not 0.0 <= q_value <= 1.0:
            raise ValueError("atom probabilities must be finite in [0, 1]")
        if not math.isfinite(payoff_value) or not 0.0 <= payoff_value <= 1.0:
            raise ValueError("held claim payoffs must be finite in [0, 1]")
        if not math.isfinite(wealth_value) or wealth_value <= 0.0:
            raise ZeroWealthOutcomeError(
                f"non-positive endowment wealth in atom {atom_id!r}; log-utility undefined"
            )
        q[atom_id] = q_value
        h[atom_id] = payoff_value
        w[atom_id] = wealth_value
    if not math.isclose(sum(q.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("atom probabilities must sum to one")

    cash_marginal = bid_value * sum(q[a] / w[a] for a in atom_ids)
    claim_marginal = sum(q[a] * h[a] / w[a] for a in atom_ids)
    return cash_marginal > claim_marginal
