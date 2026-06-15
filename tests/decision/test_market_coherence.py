# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/decision/market_coherence.py" block lines 804-852: MarketImpliedQ
#   808-815, MarketCoherenceReport 817-824, the de-frictioned implied-q + simplex
#   projection + depth/spread gating + per-candidate logit_gap block 826-851; the
#   Tokyo q=0.47 vs ask=0.001 ~6.8-logit incident dying before scoring, line 851)
#   reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — this is the typed calibration-incident REPORT that REPLACES the
#   one-sided NO q_lcb cap in market_anchor.py; it BLOCKS live money with a typed status
#   and emits an incident, it NEVER mutates the model q; insufficient depth must NOT
#   fabricate a gate).
"""RED-on-revert contract tests for market_coherence (Stage 9 calibration incident).

Three spec-named tests fail if the corrected transformation is reverted to the broken
behavior the spec replaces (the one-sided market-anchor cap that silently mutated q):

  * ``test_tokyo_q_047_vs_deep_ask_0001_blocks_before_scoring`` — a model q of 0.47 on a
    candidate bin whose DEEP market YES book implies ~0.001 has a logit gap of ~6.8 (>> the
    2.5 block threshold), so the report is ``INCOHERENT_BLOCK_LIVE`` and that bin is an
    offending bin — the candidate dies BEFORE trade score. RED-on-revert: if the block is
    softened back into a one-sided q haircut (the old cap) or the threshold/logit transform
    is removed, the Tokyo incident no longer blocks and the test fails. Also asserts the
    model q is NOT mutated (the report carries no new q; the JointQ passed in is unchanged).

  * ``test_insufficient_depth_does_not_fabricate_market_gate`` — when the book is too thin
    (or too wide) to imply a trustworthy q, the SAME 0.47-vs-0.001 disagreement yields
    ``INSUFFICIENT_MARKET_DEPTH`` and NO block (no offending bins). RED-on-revert: if the
    depth/spread precondition is dropped (or evaluated AFTER the logit comparison), a thin
    illiquid market would manufacture an INCOHERENT_BLOCK_LIVE — a fabricated gate. The
    test holds the disagreement fixed and only removes the depth, proving depth is a
    precondition of the block, not a separate cap.

  * ``test_licensed_model_superiority_class_can_override_with_receipt`` — the SAME deep,
    incoherent Tokyo bin is WAIVED when a receipt-carrying licensing predicate returns True
    for that (case, bin): the report is ``COHERENT`` (or at least NOT blocked on that bin)
    and the bin is not in offending_bins. RED-on-revert: if the licensing override is
    removed (block fires unconditionally) or applied family-wide instead of per-bin, the
    licensed override stops working and the test fails. The test also proves the license is
    PER-BIN: a second unlicensed deep-incoherent bin still blocks.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Mapping, Optional

import numpy as np
import pytest

from src.config import City
from src.decision.market_coherence import (
    LOGIT_GAP_BLOCK_THRESHOLD,
    MARKET_IMPLIED_Q_BASIS,
    MarketCoherenceReport,
    assess_market_coherence,
    build_market_implied_q,
    logit_gap,
    project_to_simplex,
)
from src.execution.family_book import (
    ExecutableLadder,
    MarketBook,
    build_family_book,
)
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.joint_q import JointQ
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)
from src.strategy.live_inference.executable_cost import QuoteLevel


_CAPTURED = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures — a real complete Omega (built the SAME way the joint_q / family_book
# contract tests build it) and a real FamilyBook whose per-bin YES ladders we control.
# ---------------------------------------------------------------------------

def _resolution(city_name: str = "Tokyo", metric: str = "high") -> EventResolution:
    city = City(
        name=city_name,
        lat=35.68,
        lon=139.69,
        timezone="Asia/Tokyo",
        settlement_unit="C",
        cluster="asia",
        wu_station="RJTT",
        settlement_source_type="wu_icao",
    )
    return event_resolution_for_city(city, date(2026, 6, 14), metric)


def _bin(bin_id: str, lo, hi, label: str, rule: str, *, executable: bool = True) -> OutcomeBin:
    return OutcomeBin(
        bin_id=bin_id,
        condition_id=f"cond-{bin_id}",
        label=label,
        lower_native=lo,
        upper_native=hi,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        executable=executable,
        rounding_rule=rule,
    )


def _complete_bins(rule: str) -> tuple[OutcomeBin, ...]:
    """A complete °C integer partition: (-inf,20], 21..29, [30,+inf)."""
    bins = [_bin("b_low", None, 20.0, "20°C or below", rule, executable=False)]
    for t in range(21, 30):
        bins.append(_bin(f"b{t}", float(t), float(t), f"{t}°C", rule))
    bins.append(_bin("b_high", 30.0, None, "30°C or above", rule, executable=False))
    return tuple(bins)


def _outcome_space(family_id: str = "tokyo-high") -> OutcomeSpace:
    resolution = _resolution()
    rule = resolution.rounding_rule
    bins = _complete_bins(rule)
    space = OutcomeSpace(
        family_id=family_id,
        resolution=resolution,
        bins=bins,
        topology_hash=compute_topology_hash(family_id, resolution, bins),
    )
    space.validate()
    return space


def _ladder(side: str, price: float, size: float) -> ExecutableLadder:
    """A one-level native ladder at (price, size) — best level is all the test needs."""
    return ExecutableLadder(
        levels=(QuoteLevel(Decimal(str(price)), Decimal(str(size))),),
        side=side,  # type: ignore[arg-type]
        fee_rate=0.05,
        min_tick_size=Decimal("0.001"),
        min_order_size=Decimal("1.0"),
    )


def _market_book(
    bin_id: str,
    *,
    yes_bid: float,
    yes_ask: float,
    yes_size: float,
    neg_risk: bool = False,
) -> MarketBook:
    """A MarketBook whose YES book has a controlled best bid/ask and backing size.

    The de-frictioned YES probability the coherence module reads is the YES midpoint
    (yes_bid + yes_ask)/2; ``yes_size`` is the backing on both YES sides (drives depth).
    The NO ladders are present (so the carrier is valid) but unused by this module.
    """
    return MarketBook(
        condition_id=f"cond-{bin_id}",
        bin_id=bin_id,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        yes_asks=_ladder("ask", yes_ask, yes_size),
        yes_bids=_ladder("bid", yes_bid, yes_size),
        no_asks=_ladder("ask", round(1.0 - yes_bid, 6), yes_size),
        no_bids=_ladder("bid", round(1.0 - yes_ask, 6), yes_size),
        neg_risk=neg_risk,
    )


def _family_book(space: OutcomeSpace, market_for_bin):
    """Build a complete FamilyBook from a per-bin MarketBook factory."""
    markets = {b.bin_id: market_for_bin(b.bin_id) for b in space.bins}
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


def _joint_q(space: OutcomeSpace, q_by_bin: Mapping[str, float]) -> JointQ:
    """A real JointQ over the Omega with a controlled (normalized) mass vector.

    ``q_by_bin`` supplies the masses we care about; the remainder is spread over the other
    bins so Sigma q == 1 (JointQ.assert_valid passes). This is the MODEL q the module
    compares to the market q — it is read, never mutated.
    """
    bin_ids = [b.bin_id for b in space.bins]
    explicit = {bid: float(q_by_bin.get(bid, 0.0)) for bid in bin_ids}
    explicit_total = sum(q_by_bin.values())
    assert explicit_total <= 1.0 + 1e-9, "explicit masses exceed 1"
    free_bins = [bid for bid in bin_ids if bid not in q_by_bin]
    residual = max(1.0 - explicit_total, 0.0)
    per_free = residual / len(free_bins) if free_bins else 0.0
    masses = {bid: (explicit[bid] if bid in q_by_bin else per_free) for bid in bin_ids}
    q = np.array([masses[bid] for bid in bin_ids], dtype=float)
    q = q / q.sum()
    q_by_bin_id = {bid: float(m) for bid, m in zip(bin_ids, q)}
    jq = JointQ(
        omega=space,
        q=q,
        q_by_bin_id=q_by_bin_id,
        predictive_distribution_id="pd-test",
        q_source="SETTLEMENT_STATION_NORMAL_V1",
        q_sum=float(q.sum()),
        identity_hash="jq-test-identity",
    )
    jq.assert_valid()
    return jq


# ---------------------------------------------------------------------------
# Supporting primitive checks (logit gap, simplex projection, market-implied q).
# ---------------------------------------------------------------------------

def test_logit_gap_tokyo_is_about_6_8():
    """The Tokyo disagreement (q_model=0.47 vs q_market=0.001) is ~6.8 in logit space."""
    gap = logit_gap(0.47, 0.001)
    assert 6.0 < gap < 7.5, f"expected ~6.8 logit gap, got {gap}"
    assert gap >= LOGIT_GAP_BLOCK_THRESHOLD


def test_project_to_simplex_is_a_true_projection():
    """project_to_simplex returns a point on {q>=0, Sigma q=1} (the over/under-round fix)."""
    raw = np.array([0.6, 0.6, 0.6])  # over-round (sums to 1.8) — the book friction
    q = project_to_simplex(raw)
    assert np.all(q >= 0.0)
    assert abs(float(q.sum()) - 1.0) <= 1e-9
    # An already-on-simplex vector is unchanged (idempotent projection).
    already = np.array([0.2, 0.3, 0.5])
    assert np.allclose(project_to_simplex(already), already)


def test_build_market_implied_q_defrictions_to_yes_midpoint():
    """The implied q reads the YES MIDPOINT per bin (the spread is the friction removed)."""
    space = _outcome_space()
    # b25 quoted tight around 0.50 (mid), everything else thin around uniform.
    def factory(bin_id):
        if bin_id == "b25":
            return _market_book(bin_id, yes_bid=0.48, yes_ask=0.52, yes_size=500.0)
        return _market_book(bin_id, yes_bid=0.04, yes_ask=0.06, yes_size=500.0)

    fb = _family_book(space, factory)
    implied = build_market_implied_q(fb)
    assert implied is not None
    assert implied.basis == MARKET_IMPLIED_Q_BASIS
    assert implied.book_hash == fb.book_hash
    # Post-projection q sums to 1 and is non-negative.
    assert np.all(implied.q >= 0.0)
    assert abs(float(implied.q.sum()) - 1.0) <= 1e-9


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #1: Tokyo q=0.47 vs deep ask=0.001 blocks BEFORE scoring.
# ---------------------------------------------------------------------------

def test_tokyo_q_047_vs_deep_ask_0001_blocks_before_scoring():
    """A model q of 0.47 against a DEEP market q of ~0.001 is an INCOHERENT_BLOCK_LIVE.

    The load-bearing contract (spec line 851): the Tokyo q=0.47 vs ask=0.001 disagreement
    (logit gap ~6.8 >> 2.5) is a CALIBRATION INCIDENT that dies BEFORE trade score — the
    coherence report returns ``INCOHERENT_BLOCK_LIVE`` with the candidate bin in
    ``offending_bins``. The market q is built de-frictioned from a DEEP book (large backing
    size, tight spread), so the depth precondition is satisfied and the block is real.

    RED-on-revert: if the typed block is reverted to the old one-sided q haircut (which
    silently lowered q_lcb and would let the candidate proceed at a mutated number), or the
    >= 2.5 logit threshold / logit transform is removed, the incident no longer blocks and
    this test fails. The model q is asserted UNCHANGED (no silent mutation).
    """
    space = _outcome_space()

    # The market's DEEP, tight implied YES on b25 is ~0.001: best yes_bid/ask both at the
    # tick floor (0.001), backed by a large two-sided size. Every other sibling is uniformly
    # thin-but-deep so the family projects to put almost all YES mass elsewhere -> the b25
    # market YES prob stays ~0.001 after projection. The depth is genuine (size 5000).
    def factory(bin_id):
        if bin_id == "b25":
            return _market_book(bin_id, yes_bid=0.001, yes_ask=0.001, yes_size=5000.0)
        # Spread the rest of the market YES mass across the other bins, deep and tight.
        return _market_book(bin_id, yes_bid=0.09, yes_ask=0.10, yes_size=5000.0)

    fb = _family_book(space, factory)

    # The MODEL says b25 is the favorite at q=0.47 — wildly more than the market's ~0.001.
    jq = _joint_q(space, {"b25": 0.47})
    q_model_before = dict(jq.q_by_bin_id)  # snapshot to prove no mutation

    report = assess_market_coherence(
        joint_q=jq,
        family_book=fb,
        candidate_bin_ids=["b25"],
        case_key="tokyo-high-2026-06-14",
    )

    assert isinstance(report, MarketCoherenceReport)
    assert report.status == "INCOHERENT_BLOCK_LIVE", (
        f"Tokyo q=0.47 vs deep ask=0.001 must block live money before scoring; "
        f"got {report.status} (reason={report.reason})"
    )
    assert "b25" in report.offending_bins
    # The gap is the Tokyo incident, far above the 2.5 block threshold. The spec quotes
    # ~6.8 for the RAW 0.47-vs-0.001 pair (asserted directly in test_logit_gap_tokyo_is_
    # about_6_8); the report's gap is on the FULL-Omega projected values (the market 0.001
    # is nudged up slightly by the simplex projection across 11 bins, and the model 0.47 is
    # its normalized mass), so the realized gap is ~5.1 — still a large, unambiguous
    # calibration incident well above threshold, never softened to noise.
    assert report.max_abs_logit_gap >= LOGIT_GAP_BLOCK_THRESHOLD
    assert report.max_abs_logit_gap > 4.5

    # The model q was NEVER mutated — this is a typed report, not the old one-sided cap.
    assert jq.q_by_bin_id == q_model_before
    jq.assert_valid()


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #2: insufficient depth does NOT fabricate a market gate.
# ---------------------------------------------------------------------------

def test_insufficient_depth_does_not_fabricate_market_gate():
    """The SAME 0.47-vs-0.001 disagreement on a THIN book yields no block.

    The load-bearing contract (spec line 851, drift ledger): the depth/spread precondition
    runs BEFORE the logit comparison. When the book is too thin to imply a trustworthy q,
    the status is ``INSUFFICIENT_MARKET_DEPTH`` and NO block is emitted — an illiquid market
    must not fabricate a gate.

    RED-on-revert: hold the disagreement IDENTICAL to test #1 and only remove the depth
    (tiny backing size). If the depth precondition is dropped, or evaluated after the logit
    comparison, the thin book would manufacture an INCOHERENT_BLOCK_LIVE. This test proves
    depth is a precondition of the block, not a separate cap layered on top.
    """
    space = _outcome_space()

    # IDENTICAL implied-q geometry to test #1 (b25 ~0.001, rest ~0.10) but the book is THIN:
    # backing size 1 << the depth reference (100), so depth_score = 1/100 = 0.01 < min_depth.
    def factory(bin_id):
        if bin_id == "b25":
            return _market_book(bin_id, yes_bid=0.001, yes_ask=0.001, yes_size=1.0)
        return _market_book(bin_id, yes_bid=0.09, yes_ask=0.10, yes_size=1.0)

    fb = _family_book(space, factory)
    jq = _joint_q(space, {"b25": 0.47})

    report = assess_market_coherence(
        joint_q=jq,
        family_book=fb,
        candidate_bin_ids=["b25"],
        case_key="tokyo-high-2026-06-14",
    )

    assert report.status == "INSUFFICIENT_MARKET_DEPTH", (
        f"a thin book must NOT fabricate a coherence gate; got {report.status} "
        f"(reason={report.reason})"
    )
    # No block -> no offending bins, regardless of the (untrusted) disagreement.
    assert report.offending_bins == ()

    # Control: the EXACT same disagreement on a DEEP book DOES block (proves it is the depth
    # that flips the outcome, nothing else).
    def deep_factory(bin_id):
        if bin_id == "b25":
            return _market_book(bin_id, yes_bid=0.001, yes_ask=0.001, yes_size=5000.0)
        return _market_book(bin_id, yes_bid=0.09, yes_ask=0.10, yes_size=5000.0)

    deep_fb = _family_book(space, deep_factory)
    deep_report = assess_market_coherence(
        joint_q=jq,
        family_book=deep_fb,
        candidate_bin_ids=["b25"],
        case_key="tokyo-high-2026-06-14",
    )
    assert deep_report.status == "INCOHERENT_BLOCK_LIVE", (
        "the same disagreement on a DEEP book MUST block — proving depth is the precondition"
    )


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #3: a licensed model-superiority class overrides with a receipt.
# ---------------------------------------------------------------------------

def test_licensed_model_superiority_class_can_override_with_receipt():
    """A receipt-carrying license waives the block on the licensed bin (per-bin).

    The load-bearing contract (spec line 849): the block fires only when there is NO
    licensed model-superiority class for (case, bin). A licensing predicate that returns
    True for the deep-incoherent bin WAIVES the block for THAT bin — the report is not
    blocked on it (the model is licensed to disagree there, with a receipt).

    RED-on-revert: if the licensing override is removed (the block fires unconditionally) or
    applied family-wide instead of per-bin, the licensed override stops working. The test
    proves the license is PER-BIN by adding a SECOND deep-incoherent bin that is NOT
    licensed — it still blocks, so a family-wide waiver (which would clear it too) fails.
    """
    space = _outcome_space()

    # TWO deep, incoherent bins: b25 (model 0.47 vs market ~0.001) and b26 (model 0.40 vs
    # market ~0.001). Both have ~6+ logit gaps. We license ONLY b25.
    def factory(bin_id):
        if bin_id in ("b25", "b26"):
            return _market_book(bin_id, yes_bid=0.001, yes_ask=0.001, yes_size=5000.0)
        return _market_book(bin_id, yes_bid=0.09, yes_ask=0.10, yes_size=5000.0)

    fb = _family_book(space, factory)
    jq = _joint_q(space, {"b25": 0.47, "b26": 0.40})

    # (A) With BOTH bins as candidates and ONLY b25 licensed: b26 still blocks (the license
    #     is per-bin, not family-wide), and b25 is NOT among the offending bins.
    def license_only_b25(case_key: str, bin_id: str) -> bool:
        return case_key == "tokyo-high-2026-06-14" and bin_id == "b25"

    report_mixed = assess_market_coherence(
        joint_q=jq,
        family_book=fb,
        candidate_bin_ids=["b25", "b26"],
        case_key="tokyo-high-2026-06-14",
        licensed_model_superiority=license_only_b25,
    )
    assert report_mixed.status == "INCOHERENT_BLOCK_LIVE", (
        "the UNLICENSED b26 must still block — the license is per-bin, not family-wide"
    )
    assert "b26" in report_mixed.offending_bins
    assert "b25" not in report_mixed.offending_bins, (
        "the LICENSED b25 must NOT be an offending bin (its block is waived by the receipt)"
    )

    # (B) With ONLY the licensed bin as the candidate: the report is COHERENT (the sole
    #     incoherent candidate is licensed, so nothing blocks).
    report_licensed = assess_market_coherence(
        joint_q=jq,
        family_book=fb,
        candidate_bin_ids=["b25"],
        case_key="tokyo-high-2026-06-14",
        licensed_model_superiority=license_only_b25,
    )
    assert report_licensed.status == "COHERENT", (
        f"a fully-licensed candidate set must be COHERENT (no block); got "
        f"{report_licensed.status} (reason={report_licensed.reason})"
    )
    assert report_licensed.offending_bins == ()

    # (C) Control: with NO license, the same sole b25 candidate DOES block — proving the
    #     license is what flips the outcome.
    report_unlicensed = assess_market_coherence(
        joint_q=jq,
        family_book=fb,
        candidate_bin_ids=["b25"],
        case_key="tokyo-high-2026-06-14",
    )
    assert report_unlicensed.status == "INCOHERENT_BLOCK_LIVE"
    assert "b25" in report_unlicensed.offending_bins


# ---------------------------------------------------------------------------
# Supporting status checks (NO_MARKET_Q / COHERENT happy path).
# ---------------------------------------------------------------------------

def test_no_two_sided_yes_quotes_yields_no_market_q():
    """A book with no two-sided YES quotes implies no family q -> NO_MARKET_Q, no block."""
    space = _outcome_space()

    def factory(bin_id):
        # YES asks present but YES bids EMPTY -> not two-sided -> no implied YES prob.
        mb = _market_book(bin_id, yes_bid=0.10, yes_ask=0.12, yes_size=500.0)
        return MarketBook(
            condition_id=mb.condition_id,
            bin_id=mb.bin_id,
            yes_token_id=mb.yes_token_id,
            no_token_id=mb.no_token_id,
            yes_asks=mb.yes_asks,
            yes_bids=ExecutableLadder(
                levels=(),
                side="bid",
                fee_rate=0.05,
                min_tick_size=Decimal("0.001"),
                min_order_size=Decimal("1.0"),
            ),
            no_asks=mb.no_asks,
            no_bids=mb.no_bids,
            neg_risk=False,
        )

    fb = _family_book(space, factory)
    jq = _joint_q(space, {"b25": 0.47})
    report = assess_market_coherence(
        joint_q=jq, family_book=fb, candidate_bin_ids=["b25"], case_key="c"
    )
    assert report.status == "NO_MARKET_Q"
    assert report.offending_bins == ()
    assert build_market_implied_q(fb) is None


def test_model_agreeing_with_deep_market_is_coherent():
    """When the model q agrees with a deep market q on the candidate, status is COHERENT."""
    space = _outcome_space()

    # b25 deep market YES ~0.50; model also ~0.50 -> tiny logit gap -> COHERENT.
    def factory(bin_id):
        if bin_id == "b25":
            return _market_book(bin_id, yes_bid=0.49, yes_ask=0.51, yes_size=5000.0)
        return _market_book(bin_id, yes_bid=0.05, yes_ask=0.06, yes_size=5000.0)

    fb = _family_book(space, factory)
    implied = build_market_implied_q(fb)
    assert implied is not None
    # Build a model q matching the projected market q on b25 (read it back to agree).
    market_b25 = {b.bin_id: float(m) for b, m in zip(space.bins, implied.q)}["b25"]
    jq = _joint_q(space, {"b25": market_b25})
    report = assess_market_coherence(
        joint_q=jq, family_book=fb, candidate_bin_ids=["b25"], case_key="c"
    )
    assert report.status == "COHERENT", f"got {report.status} (reason={report.reason})"
    assert report.offending_bins == ()
    assert report.max_abs_logit_gap < LOGIT_GAP_BLOCK_THRESHOLD
