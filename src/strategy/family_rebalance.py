# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) — D1 fill-up "re-decide held exposure". The same-token "never increase
#   exposure" invariant in src/strategy/family_exclusive_dedup.py guards the
#   2026-06-16 double-rest defect (a family double-resting a SECOND concurrent
#   entry). The consult's SAFE lift relaxes that invariant ONLY for a RESIDUAL
#   resize of the EXISTING held same-token position — never a second full entry.
"""Family-rebalance (fill-up) admission — the critical safety primitive for D1.

``decide_fill_up`` is the pure money-path predicate. It answers: given a held
position whose belief has STRENGTHENED, may we add to it, and by how much?

The single load-bearing safety property: the submitted amount is the RESIDUAL to
the fresh target, NOT a fresh full-target stake —

    delta_entry_usd = target_total_exposure_usd
                      - current_live_exposure_usd
                      - same_token_pending_entry_usd

submit ONLY if delta_entry_usd clears the venue minimum. The final live stake
kernel emits a family-TOTAL single-leg Kelly stake (not a residual), so fractional
Kelly does NOT by itself prevent a same-token re-entry from submitting another full
target — this residualizer MUST run before any order is emitted (consult
[HIGH] fill-up sizing). Fill-up also fails CLOSED on any unowned pending/unknown
entry in the family (the double-submit hazard) and is gated on a same-token+bin+
direction selection with a certified entry q_lcb that the fresh q_lcb exceeds.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FillUpDecision:
    """Whether a held same-token position may be topped up, and by how much."""

    allow: bool
    delta_entry_usd: float
    reason: str


def decide_fill_up(
    *,
    is_redecision_event: bool,
    selected_token_id: str,
    selected_bin_id: str,
    selected_direction: str,
    held_token_id: str | None,
    held_bin_id: str | None,
    held_direction: str | None,
    q_current_lcb: float | None,
    q_entry_lcb: float | None,
    target_total_exposure_usd: float,
    current_live_exposure_usd: float,
    same_token_pending_entry_usd: float,
    venue_min_increment_usd: float,
    has_unowned_pending_or_unknown_entry: bool,
    q_strengthening_floor: float = 0.0,
) -> FillUpDecision:
    """Decide whether to fill up (top up) an existing same-token held position.

    Returns a residual ``delta_entry_usd`` to submit only when ALL hold:
    redecision event; a single held same-token+bin+direction exposure exists; no
    unowned pending/unknown entry in the family; the held side carries a certified
    entry q_lcb that the fresh q_lcb exceeds (by an optional hysteresis floor); and
    the residual to the fresh target clears the venue minimum. Fails CLOSED.
    """

    deny = lambda reason: FillUpDecision(allow=False, delta_entry_usd=0.0, reason=reason)

    if not is_redecision_event:
        return deny("NOT_REDECISION_EVENT")
    if not held_token_id:
        return deny("NO_HELD_EXPOSURE")  # fresh entry, not a fill-up
    # Fail closed on the double-submit hazard.
    if has_unowned_pending_or_unknown_entry:
        return deny("UNOWNED_PENDING_OR_UNKNOWN_ENTRY")
    # A different bin/token/direction is SHIFT-BIN, not fill-up.
    if (
        selected_token_id != held_token_id
        or selected_bin_id != held_bin_id
        or selected_direction != held_direction
    ):
        return deny("NOT_SAME_TOKEN_SIBLING_IS_SHIFT_BIN")
    # v1: do not fill-up a held position lacking a certified entry q_lcb authority.
    if q_entry_lcb is None or q_current_lcb is None:
        return deny("ENTRY_Q_LCB_MISSING")
    if q_current_lcb <= q_entry_lcb + float(q_strengthening_floor):
        return deny("BELIEF_NOT_STRENGTHENED")

    # THE safety lift: residual to the fresh target, never a fresh full stake.
    delta_entry_usd = (
        float(target_total_exposure_usd)
        - float(current_live_exposure_usd)
        - float(same_token_pending_entry_usd)
    )
    if delta_entry_usd <= 0.0:
        return FillUpDecision(False, delta_entry_usd, "NO_RESIDUAL_AT_OR_OVER_TARGET")
    if delta_entry_usd < float(venue_min_increment_usd):
        return FillUpDecision(False, delta_entry_usd, "RESIDUAL_BELOW_VENUE_MIN")

    return FillUpDecision(
        allow=True,
        delta_entry_usd=delta_entry_usd,
        reason="FILL_UP_RESIDUAL",
    )
