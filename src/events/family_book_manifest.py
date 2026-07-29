# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   round-3 review fixes: H1 compact envelope (a frozen dataclass holding the
#   whole FamilyDecision/family/proofs graph keeps band.samples -- gigabytes
#   at queue depth -- alive for as long as the item sits queued; this module's
#   ``project_observation_envelope`` does the ONE-TIME extraction of only the
#   small scalars/mappings the writer needs, called on the decision thread),
#   X3 per-observation provenance (state content_hash/canonical_payload now
#   carry ONLY content-identity fields; executable_snapshot_id/
#   source_captured_at move to a per-observation source_manifest_json so a
#   later heartbeat/selection observation records ITS OWN capture provenance,
#   not the first-seen state's).
"""Compact envelope projection + state/observation evidence fields.

``project_observation_envelope`` is the ONLY function in this module called
from the live decision thread -- it does cheap attribute extraction (no JSON,
no hashing, no I/O) and returns a small, fully immutable ``ObservationEnvelope``
that holds no reference to the FamilyDecision/family/proofs graph (so nothing
in that graph -- crucially ``FamilyDecision.band.samples``, a large NumPy
draw matrix -- stays reachable from the queue). Every other function here
runs on the writer thread, operating only on the already-extracted envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, Optional, Sequence

from src.events.idempotency import canonical_json, sha256_text
from src.state.schema.family_book_states_schema import HASH_VERSION, PAYLOAD_SCHEMA_VERSION

if TYPE_CHECKING:
    from src.decision.family_decision_engine import FamilyDecision
    from src.events.candidate_binding import EventBoundCandidateFamily

MarketCenterStatus = Literal["OK", "INCOMPLETE_BOOK", "INSUFFICIENT_COVERAGE"]


@dataclass(frozen=True)
class _BinProjection:
    """Everything the writer needs for ONE bin, extracted once, on the
    decision thread. Deliberately excludes ladder depth beyond best bid/ask
    (never persisted; used only for the market_center diagnostic) and any
    reference back to the original MarketBook/OutcomeBin/proof objects."""

    bin_id: str
    executable: bool
    lower_native: Optional[float]
    upper_native: Optional[float]
    condition_id: str
    yes_token_id: str
    no_token_id: str
    neg_risk: bool
    min_tick_size: str
    min_order_size: str
    fee_rate: float
    best_yes_ask: Optional[float]
    best_yes_bid: Optional[float]
    executable_snapshot_id: Optional[str]
    raw_orderbook_hash: Optional[str]
    source_captured_at: Optional[str]


@dataclass(frozen=True)
class ObservationEnvelope:
    """The ONLY thing the decision thread hands to the writer -- small,
    flat, and holds no reference to FamilyDecision/family/proofs (H1)."""

    family_id: str
    city: str
    target_date: str
    temperature_metric: str
    decision_id: str
    receipt_hash: str
    topology_hash: str
    complete_book: bool
    measurement_unit: str
    our_mu_native: Optional[float]
    our_sigma_native: Optional[float]
    predictive_identity_hash: Optional[str]
    model_q_by_bin_id: Optional[Mapping[str, float]]
    model_q_identity_hash: Optional[str]
    market_q_by_bin_id: Optional[Mapping[str, float]]
    market_q_basis: Optional[str]
    market_q_depth_score: Optional[float]
    market_q_spread_score: Optional[float]
    market_q_projection_error: Optional[float]
    market_q_book_hash: Optional[str]
    pre_veto_selected: bool
    selected_bin_id: Optional[str]
    selected_side: Optional[str]
    bins: tuple[_BinProjection, ...]
    decision_time: datetime
    causal_snapshot_id: Optional[str]


def project_observation_envelope(
    *,
    decision: "Optional[FamilyDecision]",
    family: "EventBoundCandidateFamily",
    active_proofs: Sequence[Any],
    candidate_bin_id: Callable[[Any], str],
    decision_time: datetime,
    causal_snapshot_id: Optional[str],
) -> Optional[ObservationEnvelope]:
    """Decision-thread-side projection. Cheap (attribute reads only, no JSON/
    hashing/I/O); returns None exactly when there is nothing to capture
    (decision missing or no family_book -- the ineligible/no-q path)."""
    if decision is None or decision.family_book is None:
        return None
    family_book = decision.family_book

    proof_by_bin: dict[str, Any] = {}
    for proof in active_proofs:
        bin_id = candidate_bin_id(proof)
        if bin_id not in proof_by_bin:
            proof_by_bin[bin_id] = proof

    bins: list[_BinProjection] = []
    for outcome_bin in family_book.omega.bins:
        market = family_book.markets.get(outcome_bin.bin_id)
        if market is None:
            continue
        proof = proof_by_bin.get(outcome_bin.bin_id)
        row = getattr(proof, "row", None) if proof is not None else None
        row = row if isinstance(row, Mapping) else {}
        ask_levels = market.yes_asks.levels
        bid_levels = market.yes_bids.levels
        bins.append(
            _BinProjection(
                bin_id=outcome_bin.bin_id,
                executable=outcome_bin.executable,
                lower_native=outcome_bin.lower_native,
                upper_native=outcome_bin.upper_native,
                condition_id=market.condition_id,
                yes_token_id=market.yes_token_id,
                no_token_id=market.no_token_id,
                neg_risk=bool(market.neg_risk),
                min_tick_size=str(market.yes_asks.min_tick_size),
                min_order_size=str(market.yes_asks.min_order_size),
                fee_rate=market.yes_asks.fee_rate,
                best_yes_ask=float(ask_levels[0].price) if ask_levels else None,
                best_yes_bid=float(bid_levels[0].price) if bid_levels else None,
                executable_snapshot_id=getattr(proof, "executable_snapshot_id", None),
                raw_orderbook_hash=row.get("raw_orderbook_hash"),
                source_captured_at=row.get("captured_at"),
            )
        )

    joint_q = decision.joint_q
    model_q_by_bin_id = dict(joint_q.q_by_bin_id) if joint_q is not None else None
    model_q_identity_hash = joint_q.identity_hash if joint_q is not None else None

    market_q_by_bin_id = market_q_basis = None
    market_q_depth_score = market_q_spread_score = market_q_projection_error = None
    market_q_book_hash = None
    miq = decision.market_implied_q
    if miq is not None:
        bin_ids = [b.bin_id for b in decision.omega.bins]
        market_q_by_bin_id = {bid: float(v) for bid, v in zip(bin_ids, miq.q)}
        market_q_basis = miq.basis
        market_q_depth_score = float(miq.depth_score)
        market_q_spread_score = float(miq.spread_score)
        market_q_projection_error = float(miq.projection_error)
        market_q_book_hash = miq.book_hash

    pre_veto_selected = decision.selected is not None
    selected_bin_id: Optional[str] = None
    selected_side: Optional[str] = None
    if pre_veto_selected:
        for cd in decision.candidate_decisions:
            if cd.economics.candidate_id == decision.selected.candidate_id:
                selected_bin_id = cd.route.bin_id
                selected_side = cd.route.side
                break

    return ObservationEnvelope(
        family_id=family.family_id,
        city=family.city,
        target_date=family.target_date,
        temperature_metric=family.metric,
        decision_id=decision.decision_id,
        receipt_hash=decision.receipt_hash,
        topology_hash=decision.omega.topology_hash,
        complete_book=family_book.complete_book,
        measurement_unit=decision.case.resolution.measurement_unit,
        our_mu_native=decision.predictive.mu_native,
        our_sigma_native=decision.predictive.sigma_native,
        predictive_identity_hash=decision.predictive.identity_hash,
        model_q_by_bin_id=model_q_by_bin_id,
        model_q_identity_hash=model_q_identity_hash,
        market_q_by_bin_id=market_q_by_bin_id,
        market_q_basis=market_q_basis,
        market_q_depth_score=market_q_depth_score,
        market_q_spread_score=market_q_spread_score,
        market_q_projection_error=market_q_projection_error,
        market_q_book_hash=market_q_book_hash,
        pre_veto_selected=pre_veto_selected,
        selected_bin_id=selected_bin_id,
        selected_side=selected_side,
        bins=tuple(bins),
        decision_time=decision_time,
        causal_snapshot_id=causal_snapshot_id,
    )


# ---------------------------------------------------------------------------
# Writer-side (off decision thread): state identity + per-observation
# provenance, both derived from the already-extracted envelope.
# ---------------------------------------------------------------------------

# Content-identity fields ONLY -- deliberately excludes executable_snapshot_id
# / source_captured_at (X3: those belong to EACH observation, not the shared
# state, or a later re-observation of the same content silently inherits the
# FIRST capture's provenance).
_HASH_FIELDS = (
    "bin_id", "raw_orderbook_hash", "condition_id", "yes_token_id", "no_token_id",
    "neg_risk", "min_tick_size", "min_order_size", "fee_rate",
)


def _content_bins(envelope: ObservationEnvelope) -> list[dict]:
    return [
        {field: getattr(b, field) for field in _HASH_FIELDS}
        for b in sorted(envelope.bins, key=lambda b: b.bin_id)
    ]


def compute_state_identity(envelope: ObservationEnvelope) -> tuple[str, str, str]:
    """Return (state_id, content_hash, canonical_payload) -- content-only,
    timestamp-free, snapshot-identity-free (X3)."""
    content_bins = _content_bins(envelope)
    hash_preimage = {
        "hash_version": HASH_VERSION,
        "family_id": envelope.family_id,
        "topology_hash": envelope.topology_hash,
        "complete_book": bool(envelope.complete_book),
        "bins": content_bins,
    }
    content_hash = sha256_text(canonical_json(hash_preimage))
    state_id = sha256_text(f"{envelope.family_id}|{content_hash}")
    canonical_payload = canonical_json(
        {
            "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
            "family_id": envelope.family_id,
            "topology_hash": envelope.topology_hash,
            "complete_book": bool(envelope.complete_book),
            "bins": content_bins,
        }
    )
    return state_id, content_hash, canonical_payload


def build_source_manifest(envelope: ObservationEnvelope) -> str:
    """Per-observation provenance (X3): THIS capture's snapshot identity and
    source time per bin -- persisted on every observation, never on the
    shared (potentially long-lived, first-seen) state row."""
    return canonical_json(
        {
            b.bin_id: {
                "executable_snapshot_id": b.executable_snapshot_id,
                "source_captured_at": b.source_captured_at,
            }
            for b in sorted(envelope.bins, key=lambda b: b.bin_id)
        }
    )


def market_center_and_status(envelope: ObservationEnvelope) -> tuple[Optional[float], MarketCenterStatus]:
    """Price-weighted midpoint center, native settlement unit -- a demoted,
    versioned diagnostic, never authority.

    Requires full quote coverage over every EXECUTABLE bin (tail/shoulder
    bins are known-illiquid by design and exempt from the coverage
    requirement). Non-executable shoulder bins are ALWAYS excluded from the
    weighted sum itself, regardless of whether they happen to be quoted
    (M3: two status=OK centers must use the same support -- a shoulder
    bin's boundary-substitution bias must not silently vary the basis).
    """
    if not envelope.complete_book:
        return None, "INCOMPLETE_BOOK"

    weighted_sum = 0.0
    total_weight = 0.0
    for b in envelope.bins:
        two_sided = b.best_yes_ask is not None and b.best_yes_bid is not None
        if b.executable and not two_sided:
            return None, "INSUFFICIENT_COVERAGE"
        if not b.executable or not two_sided:
            continue

        yes_mid = (b.best_yes_ask + b.best_yes_bid) / 2.0
        if b.lower_native is not None and b.upper_native is not None:
            rep_native = (b.lower_native + b.upper_native) / 2.0
        elif b.lower_native is not None:
            rep_native = b.lower_native
        elif b.upper_native is not None:
            rep_native = b.upper_native
        else:
            continue

        weighted_sum += yes_mid * rep_native
        total_weight += yes_mid

    if total_weight <= 0.0:
        return None, "INSUFFICIENT_COVERAGE"
    return weighted_sum / total_weight, "OK"


def model_q_json(envelope: ObservationEnvelope) -> Optional[str]:
    if envelope.model_q_by_bin_id is None:
        return None
    return canonical_json(dict(envelope.model_q_by_bin_id))


def market_q_json(envelope: ObservationEnvelope) -> Optional[str]:
    if envelope.market_q_by_bin_id is None:
        return None
    return canonical_json(dict(envelope.market_q_by_bin_id))
