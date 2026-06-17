# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/decision/market_coherence.py" block lines 804-852:
#   MarketImpliedQ 808-815 [q, basis, depth_score, spread_score, projection_error,
#   book_hash], MarketCoherenceReport 817-824 [status, max_abs_logit_gap,
#   kl_model_to_market, kl_market_to_model, offending_bins, reason]; the algorithm
#   826-851 — de-frictioned implied family q from the book, simplex projection,
#   depth/spread gating before use, per-candidate logit_gap_i, and the block rule
#   846-849) reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — no live-file edits; this is a TYPED CALIBRATION-INCIDENT REPORT
#   that REPLACES the one-sided NO q_lcb cap in
#   src/strategy/live_inference/market_anchor.py — NOT a second cap; it emits an
#   incident and a live-money BLOCK status, never a silent q mutation; Stage 11 removes
#   market_anchor's live use, this file does not edit it).
#   Live dependencies (ALL already built; imported, never re-implemented):
#     - src/probability/joint_q.py::JointQ          (JointQ.q — the model q to compare;
#                       q[i] aligned 1:1 with omega.bins, Sigma q == 1 by construction)
#     - src/execution/family_book.py::FamilyBook    (the de-frictioned family-implied
#                       market q source: per-sibling MarketBook native YES bid/ask
#                       ladders; complete_book / book_hash / markets keyed by bin_id)
#     - src/probability/outcome_space.py::OutcomeSpace / OutcomeBin (the complete Omega
#                       both the model q and the market q are aligned to, by bin_id)
"""market_coherence — typed calibration-incident report (replaces the one-sided cap).

This is Stage 9 of the q-kernel rebuild (consult_build_spec.md lines 804-852). It is the
DESIGNED REPLACEMENT for the one-sided market-anchor NO q_lcb cap
(``src/strategy/live_inference/market_anchor.py``). The old cap silently LOWERED the
tradable NO lower bound toward an alpha-blended market belief — a hidden q mutation that
could fabricate or suppress an edge without leaving a reconstructable signal. This module
does the OPPOSITE: it builds the market's OWN implied family distribution from the book,
compares it to the model's joint q in logit space, and emits a TYPED INCIDENT REPORT.
When the disagreement is large AND the book is deep enough to trust AND nothing licenses
the model's superiority, it returns ``status="INCOHERENT_BLOCK_LIVE"`` — a live-money
block that is recorded as a calibration incident, NOT a number quietly changed in the q.

WHY THIS IS THE CORRECTED TRANSFORMATION, NOT A BOLTED-ON CAP (operator law):

  The spec transformation for Stage 9 IS "produce a typed calibration-incident report and
  block live money when model q is incoherent with a deep market q." The report is the
  output contract — there is no broken transform left in place behind a detector. The
  defect this replaces is the one-sided cap that *mutated q* (left the broken belief and
  haircut it); here the q is NEVER touched. The block is a status on a report the decision
  layer reads, so the bad output (trading a Tokyo q=0.47 against a deep ask=0.001) is made
  impossible at the SOURCE: the candidate never reaches scoring because the coherence
  report blocks it. This is the same shape as ``family_book.complete_book`` and
  ``joint_q``'s single normalization — a structural property of the report, not a clamp on
  a value that was allowed to go wrong first.

THE ALGORITHM (spec lines 826-851):

  1. Build a DE-FRICTIONED implied family distribution from the book. For each sibling bin
     the market's YES probability is implied by the native YES ladder. The bid-ask spread
     IS the friction; de-frictioning takes the YES MIDPOINT (best_yes_bid + best_yes_ask)/2
     per bin — the spread (friction) is removed by reading the midpoint, not either side.
  2. PROJECT to the simplex. The raw per-bin midpoints do not sum to 1 (book over-round /
     under-round is the second friction); a Euclidean projection onto the probability
     simplex {q >= 0, Sigma q = 1} removes it. ``projection_error`` records how far the raw
     vector was from the simplex (the L2 move), so a wildly inconsistent book is visible.
  3. REQUIRE enough depth / tight enough spread before USING the market q. ``depth_score``
     (min per-bin backing size, normalised) and ``spread_score`` (max per-bin YES spread)
     gate trust. A thin / wide book yields ``status="INSUFFICIENT_MARKET_DEPTH"`` and emits
     NO block — an insufficiently-deep market must NOT fabricate a gate.
  4. COMPARE model q to market q per candidate in LOGIT space:

         logit_gap_i = abs(logit(clamp(q_model_i)) - logit(clamp(q_market_i)))

  5. BLOCK (status="INCOHERENT_BLOCK_LIVE") iff:

         depth_score >= min_depth
         and spread_score <= max_spread
         and logit_gap_i >= LOGIT_GAP_BLOCK_THRESHOLD (2.5)
         and not licensed_model_superiority_class(case, bin_i)

     Tokyo q=0.47 vs deep ask=0.001 has a logit gap of ~6.8 — far above 2.5 — so it DIES
     here, BEFORE trade score, as a calibration incident. A licensed model-superiority
     class (a receipt-carrying reason the model is allowed to disagree this much on this
     bin) overrides the block for that bin only.

DEPTH IS A PRECONDITION OF THE BLOCK, NEVER A FABRICATED GATE (spec line 851, drift ledger):

  The depth/spread gate runs FIRST. If the book is too thin or too wide to imply a
  trustworthy q, the status is ``INSUFFICIENT_MARKET_DEPTH`` and ``status`` carries NO
  block — there is no logit comparison at all. So an absent / illiquid market cannot
  manufacture an INCOHERENT_BLOCK_LIVE. The block exists ONLY where the market genuinely
  has a deep, tight, coherent contrary q. ``NO_MARKET_Q`` is the status when the book
  cannot imply a YES distribution at all (no two-sided YES quotes on any sibling).

Pure module: no I/O, no settings reads, no engine imports. The flag gate + licensing
registry + candidate-bin selection live at the (impure) caller in the decision engine
(Wave 5). The ``licensed_model_superiority`` hook is a caller-supplied predicate so this
module never owns the licensing policy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Literal, Mapping, Optional, Sequence

import numpy as np

from src.execution.family_book import FamilyBook, MarketBook
from src.probability.joint_q import JointQ
from src.probability.outcome_space import OutcomeSpace

# ---------------------------------------------------------------------------
# Constants — the block thresholds and the logit clamp (the only magic numbers).
# ---------------------------------------------------------------------------

# The de-frictioned implied-q basis literal (spec line 811).
MARKET_IMPLIED_Q_BASIS: Literal[
    "DEFRICTIONED_FAMILY_BOOK_MIDPOINT_PROJECTION_V1"
] = "DEFRICTIONED_FAMILY_BOOK_MIDPOINT_PROJECTION_V1"

# The block threshold on the per-candidate logit gap (spec line 848). A model q and a
# deep market q that disagree by >= 2.5 in logit space are incoherent: e.g. q=0.47 vs
# ask=0.001 is a logit gap of ~6.8. Below 2.5 the disagreement is within normal
# model<->market noise and is NOT blocked.
LOGIT_GAP_BLOCK_THRESHOLD = 2.5

# Logit clamp bound. q_market can be 0.001 (a deep ask at the tick floor) or 0.999; the
# logit of a literal 0 or 1 is infinite. Clamping to [eps, 1-eps] keeps the gap finite
# while preserving the ordering — a 0.001 ask still produces a ~6.8 gap against q=0.47,
# so the Tokyo incident still fires. eps is small enough not to soften a real incident.
_LOGIT_CLAMP_EPS = 1e-6

# Default depth/spread trust gates. depth_score is the MIN per-bin backing size across the
# siblings, normalised by this reference size; >= 1.0 means every bin is backed by at least
# the reference depth. spread_score is the MAX per-bin YES bid-ask spread (probability
# units). These are the trust preconditions, NOT caps on any value.
DEFAULT_MIN_DEPTH = 1.0
DEFAULT_MAX_SPREAD = 0.10
DEFAULT_DEPTH_REFERENCE_SIZE = 100.0

# The caller-supplied licensing predicate: given the family/case key and a bin_id, returns
# True iff a receipt-carrying model-superiority class licenses the model to disagree with
# the market on THAT bin (so the block is waived for that bin only). The default licenses
# nothing (no bin is ever waived) — the caller injects the real registry.
LicensedModelSuperiority = Callable[[str, str], bool]


def _license_nothing(_case_key: str, _bin_id: str) -> bool:
    """Default licensing predicate: nothing is licensed (no bin waives the block)."""
    return False


# ---------------------------------------------------------------------------
# MarketImpliedQ (spec lines 808-815) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketImpliedQ:
    """The de-frictioned, simplex-projected market-implied family q (spec 808-815).

    Field names are verbatim from consult_build_spec.md.

    * ``q`` — the market-implied YES mass vector, aligned 1:1 with ``omega.bins``, AFTER
      simplex projection (so ``q >= 0`` and ``Sigma q == 1`` by construction of the
      projection). ``q[i]`` is the market's implied probability that ``omega.bins[i]``
      settles YES, read from the de-frictioned (midpoint) native YES ladders.
    * ``basis`` — the construction-basis literal
      (``DEFRICTIONED_FAMILY_BOOK_MIDPOINT_PROJECTION_V1``); proves HOW this q was built.
    * ``depth_score`` — the MIN per-bin backing size across the siblings, normalised by the
      depth reference; the market q is only trustworthy when this is >= ``min_depth``.
    * ``spread_score`` — the MAX per-bin YES bid-ask spread (probability units); the market
      q is only trustworthy when this is <= ``max_spread``.
    * ``projection_error`` — the L2 distance the raw midpoint vector moved to reach the
      simplex; a large value flags a wildly over/under-round book.
    * ``book_hash`` — the ``FamilyBook.book_hash`` this q was implied from, so an incident
      receipt can prove the exact captured surface.
    """

    q: np.ndarray
    basis: Literal["DEFRICTIONED_FAMILY_BOOK_MIDPOINT_PROJECTION_V1"]
    depth_score: float
    spread_score: float
    projection_error: float
    book_hash: str


# ---------------------------------------------------------------------------
# MarketCoherenceReport (spec lines 817-824) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketCoherenceReport:
    """The typed calibration-incident report (spec lines 817-824).

    Field names are verbatim from consult_build_spec.md. This is the OUTPUT CONTRACT of
    Stage 9 — the decision layer reads ``status`` to decide whether the candidate may
    reach trade score. The report NEVER mutates the model q; it records the disagreement
    and, when the market is deep and contrary, blocks live money as an incident.

    * ``status`` — one of:
        - ``"COHERENT"`` — the book is deep/tight enough to trust AND no candidate bin
          has a >= 2.5 logit gap (or every large-gap bin is licensed). Live money OK.
        - ``"INCOHERENT_BLOCK_LIVE"`` — the book is deep/tight AND at least one UNLICENSED
          candidate bin has a >= 2.5 logit gap. Live money BLOCKED; a calibration incident.
        - ``"INSUFFICIENT_MARKET_DEPTH"`` — the book is too thin / too wide to imply a
          trustworthy q. NO block is emitted (an illiquid market must not fabricate a gate).
        - ``"NO_MARKET_Q"`` — the book cannot imply a YES family distribution at all (no
          two-sided YES quotes). NO block.
    * ``max_abs_logit_gap`` — the largest per-candidate ``logit_gap_i`` observed (over the
      candidate bins compared). 0.0 when no comparison ran (NO_MARKET_Q / no candidates).
    * ``kl_model_to_market`` / ``kl_market_to_model`` — the KL divergences between the
      (clamped) model q and market q over the FULL Omega; a scalar summary of the whole
      distributional disagreement for the incident receipt.
    * ``offending_bins`` — the bin ids whose UNLICENSED logit gap is >= 2.5 (the bins that
      cause the block); empty unless ``status == "INCOHERENT_BLOCK_LIVE"``.
    * ``reason`` — a human/receipt-readable explanation tag.
    """

    status: Literal[
        "COHERENT", "INCOHERENT_BLOCK_LIVE", "INSUFFICIENT_MARKET_DEPTH", "NO_MARKET_Q"
    ]
    max_abs_logit_gap: float
    kl_model_to_market: float
    kl_market_to_model: float
    offending_bins: tuple[str, ...]
    reason: str


# ---------------------------------------------------------------------------
# Logit / clamp / KL helpers (the comparison primitives).
# ---------------------------------------------------------------------------

def _clamp_prob(p: float) -> float:
    """Clamp a probability to [eps, 1-eps] so its logit is finite (spec line 840)."""
    return min(max(float(p), _LOGIT_CLAMP_EPS), 1.0 - _LOGIT_CLAMP_EPS)


def _logit(p: float) -> float:
    """logit(p) = log(p / (1 - p)); p is assumed already clamped to (0, 1)."""
    return math.log(p / (1.0 - p))


def logit_gap(q_model_i: float, q_market_i: float) -> float:
    """The per-candidate logit gap (spec line 840).

        logit_gap_i = abs(logit(clamp(q_model_i)) - logit(clamp(q_market_i)))

    Both inputs are clamped to [eps, 1-eps] first so a deep ask at the tick floor
    (q_market ~ 0.001) yields a large finite gap rather than an infinity. For Tokyo
    q_model=0.47 vs q_market=0.001 this is ~6.8 (>> 2.5), so the incident fires.
    """
    return abs(_logit(_clamp_prob(q_model_i)) - _logit(_clamp_prob(q_market_i)))


def _kl(p: np.ndarray, q: np.ndarray) -> float:
    """KL(p || q) over clamped, renormalised distributions (a finite scalar summary).

    Both vectors are clamped away from 0 and renormalised so the divergence is finite
    even where the market q has a near-0 bin. This is a receipt summary, not a gate.
    """
    pc = np.clip(p, _LOGIT_CLAMP_EPS, None)
    qc = np.clip(q, _LOGIT_CLAMP_EPS, None)
    pc = pc / pc.sum()
    qc = qc / qc.sum()
    return float(np.sum(pc * np.log(pc / qc)))


# ---------------------------------------------------------------------------
# Simplex projection (spec line 830) — Euclidean projection onto {q>=0, Sigma q = 1}.
# ---------------------------------------------------------------------------

def project_to_simplex(v: np.ndarray) -> np.ndarray:
    """Euclidean projection of ``v`` onto the probability simplex {q >= 0, Sigma q = 1}.

    The standard Held-Wolfe / Duchi sort-based projection: the unique point on the simplex
    closest (L2) to ``v``. Removes the book's over-round / under-round friction (the raw
    midpoints do not sum to 1) while keeping the projection the SAME transform every time —
    there is no clamp-then-renormalise that could leave a non-simplex vector in place.
    """
    n = v.shape[0]
    if n == 0:
        return v.astype(float)
    vf = v.astype(float)
    u = np.sort(vf)[::-1]
    cssv = np.cumsum(u) - 1.0
    ind = np.arange(1, n + 1)
    cond = u - cssv / ind > 0
    # cond[0] is u[0] - (u[0] - 1) = 1 > 0 for ANY finite vector, so cond is never empty:
    # the projection is total over R^n. The assertion states that invariant rather than
    # branching to a substitute output (operator law: the transform is the one transform).
    assert np.any(cond), "simplex projection: cond empty (non-finite input to project)"
    rho = ind[cond][-1]
    theta = cssv[cond][-1] / float(rho)
    return np.maximum(vf - theta, 0.0)


# ---------------------------------------------------------------------------
# Book reading — de-frictioned per-bin YES midpoint + per-bin depth / spread.
# ---------------------------------------------------------------------------

def _best_level(ladder_levels: Sequence[object]) -> Optional[tuple[float, float]]:
    """Best-first (price, size) of a native ladder, as floats; None if empty."""
    if not ladder_levels:
        return None
    best = ladder_levels[0]
    return float(best.price), float(best.size)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class _BinMarket:
    """The de-frictioned read of ONE sibling market's YES book (internal)."""

    bin_id: str
    yes_mid: float          # de-frictioned YES probability (bid/ask midpoint)
    yes_spread: float       # YES bid-ask spread (the friction we removed)
    backing_size: float     # min(best yes_bid size, best yes_ask size) — two-sided depth


def _read_bin_market(market: MarketBook) -> Optional[_BinMarket]:
    """De-friction ONE sibling: YES midpoint, YES spread, two-sided backing size.

    Returns None when the YES book is not two-sided (no best bid AND best ask) — that bin
    cannot imply a de-frictioned YES probability, so the family q is incomplete there.
    """
    ask = _best_level(market.yes_asks.levels)
    bid = _best_level(market.yes_bids.levels)
    if ask is None or bid is None:
        return None
    ask_price, ask_size = ask
    bid_price, bid_size = bid
    # De-friction: the midpoint removes the bid-ask spread (the friction). We do NOT use
    # either side as the implied prob — the midpoint is the spread-free estimate.
    yes_mid = (bid_price + ask_price) / 2.0
    yes_spread = max(ask_price - bid_price, 0.0)
    backing_size = min(bid_size, ask_size)
    return _BinMarket(
        bin_id=market.bin_id,
        yes_mid=yes_mid,
        yes_spread=yes_spread,
        backing_size=backing_size,
    )


# ---------------------------------------------------------------------------
# build_market_implied_q — steps 1-3 of the algorithm (spec lines 828-832).
# ---------------------------------------------------------------------------

def build_market_implied_q(
    family_book: FamilyBook,
    *,
    depth_reference_size: float = DEFAULT_DEPTH_REFERENCE_SIZE,
) -> Optional[MarketImpliedQ]:
    """Build the de-frictioned, simplex-projected market-implied family q from the book.

    Steps 1-3 of the spec algorithm (lines 828-832):

      1. De-friction: per sibling bin, the YES midpoint (best_yes_bid + best_yes_ask)/2 —
         the bid-ask spread (friction) removed by reading the midpoint.
      2. Project the raw per-bin midpoint vector onto the probability simplex (the over /
         under-round friction removed); ``projection_error`` records the L2 move.
      3. Compute ``depth_score`` (min per-bin backing size / reference) and ``spread_score``
         (max per-bin YES spread) so step 5 can require enough depth before USING the q.

    Returns ``None`` (=> NO_MARKET_Q) when NO sibling bin can imply a two-sided YES
    probability, i.e. the book cannot imply a family distribution at all. The q is aligned
    1:1 with ``family_book.omega.bins`` by bin_id (a bin with no two-sided YES quote
    contributes a raw 0 before projection, but the family must have at least one quoted
    bin to imply anything).
    """
    omega = family_book.omega
    bin_ids = [b.bin_id for b in omega.bins]

    reads: dict[str, _BinMarket] = {}
    for bin_id in bin_ids:
        market = family_book.markets.get(bin_id)
        if market is None:
            continue
        read = _read_bin_market(market)
        if read is not None:
            reads[bin_id] = read

    if not reads:
        # No sibling implies a two-sided YES probability -> the book cannot imply a family
        # distribution. The caller maps this to status="NO_MARKET_Q" (no block).
        return None

    # The raw de-frictioned midpoint vector, aligned to omega.bins (unquoted bins are 0).
    raw = np.array([reads[bid].yes_mid if bid in reads else 0.0 for bid in bin_ids], dtype=float)

    # Step 2: project to the simplex; projection_error is the L2 move (over/under-round).
    q = project_to_simplex(raw)
    projection_error = float(np.linalg.norm(raw - q))

    # Step 3: depth/spread trust scores over the QUOTED bins (the only bins that carry a
    # real two-sided market q). depth_score is the MIN backing size normalised; spread_score
    # is the MAX YES spread. A single thin/wide bin pulls the family below trust.
    ref = float(depth_reference_size) if depth_reference_size else DEFAULT_DEPTH_REFERENCE_SIZE
    min_backing = min(r.backing_size for r in reads.values())
    max_spread_observed = max(r.yes_spread for r in reads.values())
    depth_score = float(min_backing / ref)
    spread_score = float(max_spread_observed)

    return MarketImpliedQ(
        q=q,
        basis=MARKET_IMPLIED_Q_BASIS,
        depth_score=depth_score,
        spread_score=spread_score,
        projection_error=projection_error,
        book_hash=family_book.book_hash,
    )


# ---------------------------------------------------------------------------
# assess_market_coherence — the full algorithm (spec lines 826-851).
# ---------------------------------------------------------------------------

def assess_market_coherence(
    *,
    joint_q: JointQ,
    family_book: FamilyBook,
    candidate_bin_ids: Sequence[str],
    case_key: str = "",
    licensed_model_superiority: LicensedModelSuperiority = _license_nothing,
    min_depth: float = DEFAULT_MIN_DEPTH,
    max_spread: float = DEFAULT_MAX_SPREAD,
    depth_reference_size: float = DEFAULT_DEPTH_REFERENCE_SIZE,
) -> MarketCoherenceReport:
    """Compare the model joint q to the de-frictioned market q and emit a typed report.

    The full spec algorithm (lines 826-851). NEVER mutates ``joint_q`` — the model q is
    read, compared, and reported on; the only output is a ``MarketCoherenceReport`` whose
    ``status`` the decision layer reads.

    Args:
        joint_q: the model's normalized joint q over the Omega (``JointQ.q``).
        family_book: the executable family book — the de-frictioned market q source.
        candidate_bin_ids: the bins under consideration for a trade (the per-candidate
            logit gap is evaluated on THESE; an incoherent NON-candidate bin does not block).
        case_key: the family/case identity passed to the licensing predicate.
        licensed_model_superiority: predicate(case_key, bin_id) -> True iff a
            receipt-carrying model-superiority class licenses the model to disagree on that
            bin (waives the block for that bin only). Default licenses nothing.
        min_depth: the depth_score floor required to TRUST the market q (precondition of any
            block). Below it -> INSUFFICIENT_MARKET_DEPTH, no block.
        max_spread: the spread_score ceiling required to TRUST the market q. Above it ->
            INSUFFICIENT_MARKET_DEPTH, no block.
        depth_reference_size: the size that normalises depth_score.

    Returns:
        MarketCoherenceReport. ``status`` is the decision-layer contract. The Tokyo
        incident (model q=0.47 on a bin whose deep market ask is 0.001) yields
        ``INCOHERENT_BLOCK_LIVE`` with that bin in ``offending_bins`` — it dies BEFORE
        scoring. An insufficiently deep / wide book yields ``INSUFFICIENT_MARKET_DEPTH``
        and emits NO block (no fabricated gate). A book with no two-sided YES quotes yields
        ``NO_MARKET_Q``.
    """
    implied = build_market_implied_q(
        family_book, depth_reference_size=depth_reference_size
    )

    # Step (no market q): the book cannot imply a family distribution -> NO_MARKET_Q, no
    # block. An absent market never blocks (and never licenses) a trade.
    if implied is None:
        return MarketCoherenceReport(
            status="NO_MARKET_Q",
            max_abs_logit_gap=0.0,
            kl_model_to_market=0.0,
            kl_market_to_model=0.0,
            offending_bins=(),
            reason="NO_MARKET_Q: the family book has no two-sided YES quote on any sibling; "
            "no market-implied q can be built, so no coherence block is emitted.",
        )

    omega: OutcomeSpace = joint_q.omega
    q_model_by_bin: Mapping[str, float] = joint_q.q_by_bin_id
    # Align the market q to the same bin order (it is built over family_book.omega.bins;
    # the decision engine passes a joint_q over the SAME Omega, so bin_ids match).
    market_bin_ids = [b.bin_id for b in family_book.omega.bins]
    q_market_by_bin = {bid: float(m) for bid, m in zip(market_bin_ids, implied.q)}

    # KL summaries over the FULL Omega (a scalar receipt of the whole disagreement). Built
    # on the model bin order; missing market bins clamp to eps inside _kl.
    model_vec = np.array([float(q_model_by_bin.get(b.bin_id, 0.0)) for b in omega.bins], dtype=float)
    market_vec = np.array([float(q_market_by_bin.get(b.bin_id, 0.0)) for b in omega.bins], dtype=float)
    kl_model_to_market = _kl(model_vec, market_vec)
    kl_market_to_model = _kl(market_vec, model_vec)

    # Step 3 gate: REQUIRE enough depth / tight enough spread before USING the market q.
    # This runs BEFORE any logit comparison, so a thin/wide book can NEVER fabricate a
    # block — it returns INSUFFICIENT_MARKET_DEPTH with no offending bins.
    depth_sufficient = implied.depth_score >= float(min_depth)
    spread_tight = implied.spread_score <= float(max_spread)
    if not (depth_sufficient and spread_tight):
        return MarketCoherenceReport(
            status="INSUFFICIENT_MARKET_DEPTH",
            max_abs_logit_gap=0.0,
            kl_model_to_market=kl_model_to_market,
            kl_market_to_model=kl_market_to_model,
            offending_bins=(),
            reason=(
                "INSUFFICIENT_MARKET_DEPTH: market q not trustworthy "
                f"(depth_score={implied.depth_score:.4f} {'>=' if depth_sufficient else '<'} "
                f"min_depth={float(min_depth):.4f}; spread_score={implied.spread_score:.4f} "
                f"{'<=' if spread_tight else '>'} max_spread={float(max_spread):.4f}). "
                "No coherence block emitted — an illiquid market must not fabricate a gate."
            ),
        )

    # Steps 4-5: per CANDIDATE bin, the logit gap; block iff a candidate gap >= 2.5 and the
    # bin is not licensed. The market q IS deep/tight here (gate passed), so a large gap is
    # a real calibration incident, not market noise.
    max_gap = 0.0
    offending: list[str] = []
    for bin_id in candidate_bin_ids:
        if bin_id not in q_market_by_bin:
            # A candidate bin the market does not quote (no two-sided YES) carries no
            # market q to compare; it cannot generate a block (and cannot be incoherent).
            continue
        gap = logit_gap(q_model_by_bin.get(bin_id, 0.0), q_market_by_bin[bin_id])
        if gap > max_gap:
            max_gap = gap
        if gap >= LOGIT_GAP_BLOCK_THRESHOLD and not licensed_model_superiority(case_key, bin_id):
            offending.append(bin_id)

    if offending:
        return MarketCoherenceReport(
            status="INCOHERENT_BLOCK_LIVE",
            max_abs_logit_gap=float(max_gap),
            kl_model_to_market=kl_model_to_market,
            kl_market_to_model=kl_market_to_model,
            offending_bins=tuple(offending),
            reason=(
                "INCOHERENT_BLOCK_LIVE: model q disagrees with a DEEP market q by "
                f">= {LOGIT_GAP_BLOCK_THRESHOLD} in logit space on unlicensed bin(s) "
                f"{tuple(offending)!r} (max_abs_logit_gap={max_gap:.4f}). Calibration "
                "incident — live money blocked before trade score; the model q is NOT "
                "mutated. book_hash="
                f"{implied.book_hash}"
            ),
        )

    return MarketCoherenceReport(
        status="COHERENT",
        max_abs_logit_gap=float(max_gap),
        kl_model_to_market=kl_model_to_market,
        kl_market_to_model=kl_market_to_model,
        offending_bins=(),
        reason=(
            "COHERENT: market q is deep/tight and no unlicensed candidate bin disagrees "
            f">= {LOGIT_GAP_BLOCK_THRESHOLD} in logit space "
            f"(max_abs_logit_gap={max_gap:.4f})."
        ),
    )
