# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-b (read-model materialization). src/reduce/position_economics.py's
#   module docstring names exactly this gap: "It does not resolve
#   token_id -> (condition_id, outcome_index). No production join for that
#   exists yet ... Callers supply condition_id/outcome_index explicitly; a
#   position that still holds shares without that attribution is a refusal
#   (ConditionAttributionMissingError), never a guess." This module is that
#   caller-side resolution step -- it never touches the reducer's fold logic.
#
#   Convention validated by prior read-only analysis (chain-truth scoreboard,
#   /private/tmp/claude-501/-Users-leofitz-zeus/38b02358-fa77-4484-a335-663cba02ef1d/
#   scratchpad/chain_truth_scoreboard.md): buy_yes -> outcome_index 0,
#   buy_no -> outcome_index 1, cross-checked against independent WORLD
#   settlement joins: "411 clean matches, 21 apparent mismatches, 32
#   unjoinable. The 21 mismatches are a bug in [the] validation join (keyed
#   city+date only, not temperature_metric) ... NOT convention errors."
"""Resolve a Zeus position identity to the (condition_id, outcome_index) pair
``src.reduce.position_economics.reduce_position_economics`` needs to price an
open position's payout.

DELIBERATELY NOT IMPLEMENTED -- two fallback paths were evaluated against the
live trade DB (read-only) while building this module and rejected on the
evidence, not by assumption (Occam's razor: no machinery for a case that
cannot occur):

- ``venue_commands.market_id`` as a condition_id substitute (the shape
  floated by this packet's own build brief). Empirically wrong: of 542
  distinct live ``market_id`` values, only 3 are even condition_id-shaped
  (``0x`` + 64 hex chars), and an 8-sample spot check against positions with
  a real ``condition_id`` found ZERO matches. ``market_id`` is a different
  identifier space (Polymarket CLOB market id / CTF outcome token id, not
  the CTF ``condition_id``) -- joining on it would silently mis-attribute
  payouts, which is a worse failure mode than refusing.
- ``ctf_token_registry`` (``token_id -> condition_id``, built for exactly
  this kind of resolution gap) as a second fallback via
  ``venue_commands.token_id``. Real and reusable in principle, but dead code
  against the live corpus TODAY: every position lacking ``condition_id`` on
  ``position_current`` also has zero ``venue_commands`` rows (verified by
  query), so there is no ``token_id`` to look up either -- and a position
  with zero commands folds to ``net_shares == 0`` in the reducer regardless
  of whether this module can name its condition, so the refusal below costs
  no coverage. If a future position ever needs this path (commands exist,
  condition_id doesn't), extend this module then, against that evidence --
  not now, against a hypothetical.

CONVENTION
----------
``position_current.direction`` is Zeus's own submitted-order attribution
(``buy_yes`` / ``buy_no`` / ``unknown``), not a chain fact -- but it is
Zeus's intent evidence for which CTF outcome token a position's shares are
denominated in, which is exactly the ``outcome_index`` the reducer needs.
``direction='unknown'`` is a legal CHECK value with no safe mapping; it
refuses rather than guessing a side.
"""
from __future__ import annotations

from dataclasses import dataclass

# Binary-market convention (validated -- see module docstring).
DIRECTION_TO_OUTCOME_INDEX: dict[str, int] = {
    "buy_yes": 0,
    "buy_no": 1,
}


class ConditionResolutionRefusal(Exception):
    """Base class for every fail-closed refusal this resolver can raise.

    Mirrors src.reduce.position_economics.ReducerRefusal's idiom: never
    caught to synthesize a fallback pair, only to decide condition_id/
    outcome_index are unavailable for this position (see
    src.reduce.materialize, which passes that through to the reducer and
    lets ITS OWN refusal fire only when the position actually needs
    payout truth -- net_shares > 0).
    """


class PositionNotFoundError(ConditionResolutionRefusal):
    """No ``position_current`` row for this position_id."""


class MissingConditionIdError(ConditionResolutionRefusal):
    """``position_current.condition_id`` is NULL/blank and no other
    evidence-backed source resolves it for this position (see module
    docstring for the two fallbacks considered and rejected on evidence)."""


class UnrecognizedDirectionError(ConditionResolutionRefusal):
    """``position_current.direction`` is NULL, ``'unknown'``, or any value
    outside ``DIRECTION_TO_OUTCOME_INDEX`` -- no safe outcome_index mapping."""


@dataclass(frozen=True)
class ConditionResolution:
    """A position's resolved (condition_id, outcome_index) pair, with the
    provenance (direction) that produced it."""

    position_id: str
    condition_id: str
    outcome_index: int
    direction: str


def resolve_condition_outcome(conn, position_id: str) -> ConditionResolution:
    """Resolve ``position_id`` to its (condition_id, outcome_index).

    Raises a ``ConditionResolutionRefusal`` subclass (never a silent guess)
    when ``position_current`` lacks the row, the condition_id, or a
    recognized direction. Read-only -- writes nothing.
    """
    row = conn.execute(
        "SELECT condition_id, direction FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    if row is None:
        raise PositionNotFoundError(
            f"position_id={position_id!r} has no position_current row -- "
            "cannot resolve condition/outcome for a position that doesn't exist"
        )

    condition_id = row["condition_id"] if hasattr(row, "keys") else row[0]
    direction = row["direction"] if hasattr(row, "keys") else row[1]

    if condition_id is None or not condition_id.strip():
        raise MissingConditionIdError(
            f"position_id={position_id!r} has no condition_id on "
            "position_current -- payout truth cannot be looked up without it"
        )

    if direction not in DIRECTION_TO_OUTCOME_INDEX:
        raise UnrecognizedDirectionError(
            f"position_id={position_id!r} has direction={direction!r}, not "
            f"one of {sorted(DIRECTION_TO_OUTCOME_INDEX)} -- no safe "
            "outcome_index mapping"
        )

    return ConditionResolution(
        position_id=position_id,
        condition_id=condition_id,
        outcome_index=DIRECTION_TO_OUTCOME_INDEX[direction],
        direction=direction,
    )


__all__ = [
    "DIRECTION_TO_OUTCOME_INDEX",
    "ConditionResolutionRefusal",
    "PositionNotFoundError",
    "MissingConditionIdError",
    "UnrecognizedDirectionError",
    "ConditionResolution",
    "resolve_condition_outcome",
]
