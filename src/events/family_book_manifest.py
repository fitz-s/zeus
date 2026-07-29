# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO. Pure, off-decision-thread functions:
#   called ONLY from src/events/family_book_telemetry_writer.py's background
#   writer thread, never from the live decision path.
"""Manifest construction, content identity, and evidence fields for the
family_book_states / family_book_observations tables.

Every function here is pure and does no I/O -- serialization cost belongs off
the decision thread (see family_book_telemetry_writer.py). The manifest
references existing ``executable_market_snapshots`` rows (via the proofs that
already carry ``executable_snapshot_id`` / the row's ``raw_orderbook_hash`` /
``captured_at``) rather than duplicating ladder levels: that table is already
immutable and retained (append-only triggers; explicitly excluded from the
one archival/delete tool in the repo, scripts/ops/archive_pre_epoch_trades.py
EXCLUDED_TABLES) -- see PLAN.md STEP 0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, Optional, Sequence

from src.events.idempotency import canonical_json, sha256_text
from src.state.schema.family_book_states_schema import HASH_VERSION, PAYLOAD_SCHEMA_VERSION

if TYPE_CHECKING:
    from src.decision.family_decision_engine import FamilyDecision
    from src.execution.family_book import FamilyBook

MarketCenterStatus = Literal["OK", "INCOMPLETE_BOOK", "INSUFFICIENT_COVERAGE"]


# ---------------------------------------------------------------------------
# Manifest: (bin_id -> referenced snapshot identity + execution metadata).
# ---------------------------------------------------------------------------

def build_manifest(
    decision: "FamilyDecision",
    *,
    active_proofs: Sequence[Any],
    candidate_bin_id: Callable[[Any], str],
) -> list[dict]:
    """Ordered (by bin_id) manifest entries over the decided FamilyBook's bins.

    Identity/hash/source-time fields come from the PROOF (the FamilyBook's own
    MarketBook does not carry snapshot identity -- see PLAN.md STEP 0);
    execution metadata (tick/min_order/fee/neg_risk/token ids) comes from the
    FamilyBook's MarketBook, which is exactly what ``_family_book_builder_from_proofs``
    (src/engine/qkernel_spine_bridge.py) built from these SAME ``active_proofs``.
    A bin with no matching proof (defensive; should not occur since the book
    was built from these proofs) gets null identity fields, never a dropped bin.
    """
    proof_by_bin: dict[str, Any] = {}
    for proof in active_proofs:
        bin_id = candidate_bin_id(proof)
        if bin_id not in proof_by_bin:
            proof_by_bin[bin_id] = proof

    family_book = decision.family_book
    entries: list[dict] = []
    for bin_id in sorted(family_book.markets.keys()):
        market = family_book.markets[bin_id]
        proof = proof_by_bin.get(bin_id)
        row = getattr(proof, "row", None) if proof is not None else None
        row = row if isinstance(row, Mapping) else {}
        entries.append(
            {
                "bin_id": bin_id,
                "executable_snapshot_id": getattr(proof, "executable_snapshot_id", None),
                "raw_orderbook_hash": row.get("raw_orderbook_hash"),
                "source_captured_at": row.get("captured_at"),
                "condition_id": market.condition_id,
                "yes_token_id": market.yes_token_id,
                "no_token_id": market.no_token_id,
                "neg_risk": bool(market.neg_risk),
                "min_tick_size": str(market.yes_asks.min_tick_size),
                "min_order_size": str(market.yes_asks.min_order_size),
                "fee_rate": market.yes_asks.fee_rate,
            }
        )
    return entries


# Fields that identify observable book CONTENT (drive dedup). Deliberately
# excludes executable_snapshot_id/source_captured_at: those change on every
# fresh capture even when the book's content is byte-identical -- hashing them
# would reproduce exactly the bug this table replaces (FamilyBook.book_hash
# hashing captured_at_utc).
_HASH_FIELDS = (
    "bin_id", "raw_orderbook_hash", "condition_id", "yes_token_id", "no_token_id",
    "neg_risk", "min_tick_size", "min_order_size", "fee_rate",
)


def compute_state_identity(
    *,
    family_id: str,
    topology_hash: str,
    complete_book: bool,
    manifest: list[dict],
) -> tuple[str, str, str]:
    """Return (state_id, content_hash, canonical_payload) for this manifest.

    ``content_hash`` covers ONLY ``_HASH_FIELDS`` (content identity) --
    ``canonical_payload`` (the stored row) carries every manifest field,
    including the non-hashed provenance fields.
    """
    hash_preimage = {
        "hash_version": HASH_VERSION,
        "family_id": family_id,
        "topology_hash": topology_hash,
        "complete_book": bool(complete_book),
        "bins": [{k: entry[k] for k in _HASH_FIELDS} for entry in manifest],
    }
    content_hash = sha256_text(canonical_json(hash_preimage))
    state_id = sha256_text(f"{family_id}|{content_hash}")
    canonical_payload = canonical_json(
        {
            "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
            "family_id": family_id,
            "topology_hash": topology_hash,
            "complete_book": bool(complete_book),
            "bins": manifest,
        }
    )
    return state_id, content_hash, canonical_payload


# ---------------------------------------------------------------------------
# market_center_native -- demoted, versioned diagnostic (never authority).
# ---------------------------------------------------------------------------

def _market_center_and_status(family_book: "FamilyBook") -> tuple[Optional[float], MarketCenterStatus]:
    """Price-weighted midpoint center, native settlement unit, with a status.

    Requires FULL quote coverage over every EXECUTABLE bin (the tail/shoulder
    bins are known-illiquid by design, see OutcomeBin docstring, and are exempt)
    -- a family where only a couple of interior bins are quoted is NOT an
    identified family-wide expectation (deep-review 2026-07-29 finding), so
    partial coverage now returns NULL/INSUFFICIENT_COVERAGE rather than a
    seemingly-precise number silently computed from a subset.
    """
    if not family_book.complete_book:
        return None, "INCOMPLETE_BOOK"

    weighted_sum = 0.0
    total_weight = 0.0
    for outcome_bin in family_book.omega.bins:
        market = family_book.markets.get(outcome_bin.bin_id)
        ask_levels = market.yes_asks.levels if market is not None else ()
        bid_levels = market.yes_bids.levels if market is not None else ()
        two_sided = bool(ask_levels) and bool(bid_levels)
        if outcome_bin.executable and not two_sided:
            return None, "INSUFFICIENT_COVERAGE"
        if not two_sided:
            continue

        yes_mid = (float(ask_levels[0].price) + float(bid_levels[0].price)) / 2.0
        lower, upper = outcome_bin.lower_native, outcome_bin.upper_native
        if lower is not None and upper is not None:
            rep_native = (lower + upper) / 2.0
        elif lower is not None:
            rep_native = lower
        elif upper is not None:
            rep_native = upper
        else:
            continue

        weighted_sum += yes_mid * rep_native
        total_weight += yes_mid

    if total_weight <= 0.0:
        return None, "INSUFFICIENT_COVERAGE"
    return weighted_sum / total_weight, "OK"


def market_center_native(family_book: "FamilyBook") -> Optional[float]:
    return _market_center_and_status(family_book)[0]


def market_center_status(family_book: "FamilyBook") -> MarketCenterStatus:
    return _market_center_and_status(family_book)[1]


# ---------------------------------------------------------------------------
# Probability surfaces: ordered model q / market-implied q (the evidence
# authority the deep review requires in place of the scalar center).
# ---------------------------------------------------------------------------

def model_q_fields(decision: "FamilyDecision") -> tuple[Optional[str], Optional[str]]:
    """(model_q_json, model_q_identity_hash); both None when joint_q is None
    (the ineligible/no-q path)."""
    joint_q = decision.joint_q
    if joint_q is None:
        return None, None
    return canonical_json(dict(joint_q.q_by_bin_id)), joint_q.identity_hash


def market_q_fields(decision: "FamilyDecision") -> dict:
    """market_q_* observation columns; all None when market_implied_q is None
    (current_state_solve path, or NO_MARKET_Q -- see build_market_implied_q)."""
    miq = decision.market_implied_q
    if miq is None:
        return {
            "market_q_json": None, "market_q_basis": None, "market_q_depth_score": None,
            "market_q_spread_score": None, "market_q_projection_error": None,
            "market_q_book_hash": None,
        }
    bin_ids = [b.bin_id for b in decision.omega.bins]
    q_by_bin_id = {bin_id: float(v) for bin_id, v in zip(bin_ids, miq.q)}
    return {
        "market_q_json": canonical_json(q_by_bin_id),
        "market_q_basis": miq.basis,
        "market_q_depth_score": float(miq.depth_score),
        "market_q_spread_score": float(miq.spread_score),
        "market_q_projection_error": float(miq.projection_error),
        "market_q_book_hash": miq.book_hash,
    }
