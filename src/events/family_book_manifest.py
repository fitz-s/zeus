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
    no_executable_snapshot_id: Optional[str] = None
    no_raw_orderbook_hash: Optional[str] = None
    no_source_captured_at: Optional[str] = None


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


def project_global_selection_observation_envelope(
    *,
    family: Any,
    omega: Any,
    probability_witness: Any,
    book_epoch: Any,
    selected: Any,
    decision_time: datetime,
    causal_snapshot_id: Optional[str],
) -> Optional[ObservationEnvelope]:
    """Project one actual global-auction input cut without re-deciding it.

    This is deliberately a telemetry-only reconstruction from the selected
    cut's typed probability witness and native YES/NO curves.  A missing or
    mismatched native side leaves the family structurally incomplete and never
    manufactures a market-implied distribution.
    """
    if book_epoch is None:
        return None
    bindings = tuple(getattr(probability_witness, "bindings", ()) or ())
    if not bindings or str(getattr(probability_witness, "family_key", "")) != str(
        getattr(family, "family_id", "")
    ):
        return None
    if str(getattr(probability_witness, "topology_identity", "")) != str(
        getattr(omega, "topology_hash", "")
    ):
        return None

    expected: dict[tuple[str, str], tuple[str, str, str]] = {}
    for binding in bindings:
        bin_id = str(getattr(binding, "bin_id", "") or "")
        condition_id = str(getattr(binding, "condition_id", "") or "")
        yes_token = str(getattr(binding, "yes_token_id", "") or "")
        no_token = str(getattr(binding, "no_token_id", "") or "")
        if not all((bin_id, condition_id, yes_token, no_token)):
            return None
        expected[(bin_id, "YES")] = (condition_id, yes_token, no_token)
        expected[(bin_id, "NO")] = (condition_id, yes_token, no_token)

    family_key = str(getattr(probability_witness, "family_key", "") or "")
    state_keys = {
        (str(row[1]), str(row[3]), str(row[2]), str(row[4]))
        for row in tuple(getattr(book_epoch, "asset_states", ()) or ())
        if len(row) >= 5 and str(row[0]) == family_key
    }
    assets = {
        (str(asset.bin_id), str(asset.side)): asset
        for asset in tuple(getattr(book_epoch, "assets", ()) or ())
        if str(getattr(asset, "family_key", "") or "") == family_key
    }

    from src.decision.market_coherence import build_market_implied_q
    from src.execution.family_book import ExecutableLadder, MarketBook, build_family_book
    from src.strategy.live_inference.executable_cost import QuoteLevel

    markets: dict[str, MarketBook] = {}
    complete_book = True
    bin_rows: list[_BinProjection] = []
    omega_by_id = {str(outcome.bin_id): outcome for outcome in tuple(omega.bins)}
    if set(omega_by_id) != {str(getattr(binding, "bin_id", "")) for binding in bindings}:
        return None

    def append_incomplete_bin(
        *,
        bin_id: str,
        outcome: Any,
        condition_id: str,
        yes_token: str,
        no_token: str,
    ) -> None:
        bin_rows.append(
            _BinProjection(
                bin_id=bin_id,
                executable=bool(getattr(outcome, "executable", False)),
                lower_native=getattr(outcome, "lower_native", None),
                upper_native=getattr(outcome, "upper_native", None),
                condition_id=condition_id,
                yes_token_id=yes_token,
                no_token_id=no_token,
                neg_risk=False,
                min_tick_size="",
                min_order_size="",
                fee_rate=0.0,
                best_yes_ask=None,
                best_yes_bid=None,
                executable_snapshot_id=None,
                raw_orderbook_hash=None,
                source_captured_at=None,
            )
        )

    for binding in bindings:
        bin_id = str(binding.bin_id)
        condition_id, yes_token, no_token = expected[(bin_id, "YES")]
        yes_asset = assets.get((bin_id, "YES"))
        no_asset = assets.get((bin_id, "NO"))
        valid_sides = (
            yes_asset is not None
            and no_asset is not None
            and (bin_id, "YES", condition_id, yes_token) in state_keys
            and (bin_id, "NO", condition_id, no_token) in state_keys
            and str(getattr(yes_asset, "condition_id", "")) == condition_id
            and str(getattr(no_asset, "condition_id", "")) == condition_id
            and str(getattr(yes_asset, "token_id", "")) == yes_token
            and str(getattr(no_asset, "token_id", "")) == no_token
        )
        outcome = omega_by_id[bin_id]
        if not valid_sides:
            complete_book = False
            append_incomplete_bin(
                bin_id=bin_id,
                outcome=outcome,
                condition_id=condition_id,
                yes_token=yes_token,
                no_token=no_token,
            )
            continue
        yes_curve = yes_asset.curve
        no_curve = no_asset.curve
        if (
            yes_curve.min_tick != no_curve.min_tick
            or yes_curve.min_order_size != no_curve.min_order_size
            or yes_curve.fee_model.fee_rate != no_curve.fee_model.fee_rate
            or bool(yes_asset.neg_risk) != bool(no_asset.neg_risk)
        ):
            complete_book = False
            append_incomplete_bin(
                bin_id=bin_id,
                outcome=outcome,
                condition_id=condition_id,
                yes_token=yes_token,
                no_token=no_token,
            )
            continue
        fee_rate = float(yes_curve.fee_model.fee_rate)
        def ladder(levels: Sequence[Any], side: Literal["ask", "bid"]):
            return ExecutableLadder(
                levels=tuple(QuoteLevel(level.price, level.size) for level in levels),
                side=side,
                fee_rate=fee_rate,
                min_tick_size=yes_curve.min_tick,
                min_order_size=yes_curve.min_order_size,
            )
        markets[bin_id] = MarketBook(
            condition_id=condition_id,
            bin_id=bin_id,
            yes_token_id=yes_token,
            no_token_id=no_token,
            yes_asks=ladder(yes_curve.levels, "ask"),
            yes_bids=ladder(yes_asset.bid_levels, "bid"),
            no_asks=ladder(no_curve.levels, "ask"),
            no_bids=ladder(no_asset.bid_levels, "bid"),
            neg_risk=bool(yes_asset.neg_risk),
        )
        bin_rows.append(
            _BinProjection(
                bin_id=bin_id,
                executable=bool(getattr(outcome, "executable", False)),
                lower_native=getattr(outcome, "lower_native", None),
                upper_native=getattr(outcome, "upper_native", None),
                condition_id=condition_id,
                yes_token_id=yes_token,
                no_token_id=no_token,
                neg_risk=bool(yes_asset.neg_risk),
                min_tick_size=str(yes_curve.min_tick),
                min_order_size=str(yes_curve.min_order_size),
                fee_rate=fee_rate,
                best_yes_ask=float(yes_curve.levels[0].price),
                best_yes_bid=(
                    float(yes_asset.bid_levels[0].price)
                    if yes_asset.bid_levels else None
                ),
                executable_snapshot_id=str(yes_curve.snapshot_id),
                raw_orderbook_hash=str(yes_curve.book_hash),
                source_captured_at=yes_asset.captured_at_utc.isoformat(),
                no_executable_snapshot_id=str(no_curve.snapshot_id),
                no_raw_orderbook_hash=str(no_curve.book_hash),
                no_source_captured_at=no_asset.captured_at_utc.isoformat(),
            )
        )

    family_book = build_family_book(
        omega=omega,
        markets=markets,
        captured_at_utc=book_epoch.captured_at_utc,
    )
    complete_book = complete_book and family_book.complete_book
    implied = build_market_implied_q(family_book) if complete_book else None
    candidate = getattr(getattr(selected, "decision", None), "candidate", None)
    selected_bin_id = (
        str(getattr(candidate, "bin_id", ""))
        if candidate is not None and str(getattr(candidate, "family_key", "")) == family_key
        else None
    )
    selected_side = (
        str(getattr(candidate, "side", ""))
        if selected_bin_id is not None else None
    )
    from src.solve.solver import DeterministicBinPayoffWitness

    if isinstance(probability_witness, DeterministicBinPayoffWitness):
        # ``model_q_json`` is an ordered full-family simplex. A partial Day0
        # witness names exact facts, not a model distribution, so its unknown
        # siblings must remain NULL rather than looking like a sparse q vector.
        exact_q_by_bin_id = {
            str(bin_id): float(value)
            for bin_id, value in probability_witness.exact_yes_payoffs
        }
        q_by_bin_id = (
            exact_q_by_bin_id
            if set(exact_q_by_bin_id) == {str(binding.bin_id) for binding in bindings}
            and sum(exact_q_by_bin_id.values()) == 1.0
            else None
        )
    else:
        point_q = tuple(getattr(probability_witness, "yes_point_q", ()))
        if len(point_q) != len(bindings):
            return None
        q_by_bin_id = {
            str(binding.bin_id): float(value)
            for binding, value in zip(bindings, point_q, strict=True)
        }
    return ObservationEnvelope(
        family_id=family_key,
        city=str(getattr(family, "city", "")),
        target_date=str(getattr(family, "target_date", "")),
        temperature_metric=str(getattr(family, "metric", "")),
        decision_id=str(getattr(probability_witness, "witness_identity", "")),
        receipt_hash=str(getattr(probability_witness, "witness_identity", "")),
        topology_hash=str(getattr(omega, "topology_hash", "")),
        complete_book=complete_book,
        measurement_unit=str(getattr(getattr(omega, "resolution", None), "measurement_unit", "")),
        our_mu_native=None,
        our_sigma_native=None,
        predictive_identity_hash=None,
        model_q_by_bin_id=q_by_bin_id,
        model_q_identity_hash=str(getattr(probability_witness, "probability_content_identity", "")),
        market_q_by_bin_id=(
            {binding.bin_id: float(value) for binding, value in zip(bindings, implied.q, strict=True)}
            if implied is not None else None
        ),
        market_q_basis=implied.basis if implied is not None else None,
        market_q_depth_score=float(implied.depth_score) if implied is not None else None,
        market_q_spread_score=float(implied.spread_score) if implied is not None else None,
        market_q_projection_error=float(implied.projection_error) if implied is not None else None,
        market_q_book_hash=implied.book_hash if implied is not None else None,
        pre_veto_selected=selected_bin_id is not None,
        selected_bin_id=selected_bin_id,
        selected_side=selected_side,
        bins=tuple(bin_rows),
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
    "no_raw_orderbook_hash", "neg_risk", "min_tick_size", "min_order_size", "fee_rate",
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
                "lower_native": b.lower_native,
                "upper_native": b.upper_native,
                "executable_snapshot_id": b.executable_snapshot_id,
                "raw_orderbook_hash": b.raw_orderbook_hash,
                "source_captured_at": b.source_captured_at,
                "no_executable_snapshot_id": b.no_executable_snapshot_id,
                "no_raw_orderbook_hash": b.no_raw_orderbook_hash,
                "no_source_captured_at": b.no_source_captured_at,
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
