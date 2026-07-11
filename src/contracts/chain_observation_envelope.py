# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   BLOCKER-3 (GPT-5.6 Pro deep review, adopted target shape).

"""ChainObservationEnvelope — the observation-quality contract BLOCKER-3 requires
before any chain observation may cast a "confirmed absence" vote.

BLOCKER-3 (quarantine excision consult adjudication): "two mirror misses" does not
equal confirmed absence without an observation contract. Chain observations feeding
a force-void decision must carry: account/network scope, completeness (not
paginated/truncated/rate-limited), freshness bound, a post-command watermark,
independence interval, finality, and must never be contradicted by venue
trade/balance/open-order evidence. Stale or incomplete observation is DATA_DEGRADED,
never an absence vote; a positive chain observation always overrides local absence
evidence.

MINIMAL SHAPE (2026-07-11, T4 packet): this is the narrowest honest envelope
buildable from what ``src.state.chain_reconciliation`` / ``src.state.
chain_mirror_reconciler`` actually persist onto ``Position``/``position_current``
today (``last_chain_absence_observed_at`` / ``chain_verified_at``) — there is no
pagination/truncation/rate-limit signal surfaced anywhere yet, so ``complete`` is
never assumed True; it is only set when a caller has POSITIVE evidence the read was
a full, unfiltered snapshot (chain_reconciliation.py's whole-wallet
``get_positions_from_api()`` reads qualify; nothing else does today). Upgrade path:
when the chain snapshot source starts surfacing an explicit
pagination/truncation/rate-limit flag, thread it into ``complete`` here instead of
this construction-site heuristic.

INV-37: this module has no DB access; envelopes are built by callers from already
loaded Position/row fields.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChainObservationEnvelope:
    """One observation-quality verdict for a single subject's chain read.

    ``qualifies_for_absence_vote`` is the ONLY question this type answers: may
    this observation be used to conclude "the chain does not hold this token"?
    A positive (presence) observation is never represented by this type at
    all — callers that see current chain presence must treat that as an
    unconditional override, never construct an envelope for it.
    """

    account_scope: str  # e.g. "wallet:<funder_address>" or "" if unknown
    fetched_at: str  # ISO-8601 UTC; "" if never observed
    complete: bool  # True only with positive evidence of a full, unfiltered read
    post_command_watermark: bool  # True only when fetched_at is confirmed AFTER the command in question
    source: str  # e.g. "chain_reconciliation" | "chain_mirror_reconciler" | "unknown"

    def qualifies_for_absence_vote(self) -> bool:
        """Conservative AND of every required dimension. Missing any one —
        including simply never having observed the chain at all — means this
        envelope may not support a void/force-close decision.
        """
        return bool(self.fetched_at) and bool(self.complete) and bool(self.post_command_watermark)


UNOBSERVED_CHAIN_ENVELOPE = ChainObservationEnvelope(
    account_scope="",
    fetched_at="",
    complete=False,
    post_command_watermark=False,
    source="unknown",
)


__all__ = ["ChainObservationEnvelope", "UNOBSERVED_CHAIN_ENVELOPE"]
