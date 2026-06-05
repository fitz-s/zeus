# Created: 2026-06-04
# Last reused or audited: 2026-06-04
# Authority basis: Settlement-grounded loser audit (buy_no on cheap bins 0/12, -71%) +
#   Fitz Constraint #4 (data provenance / overconfident q tail) / iron-rule-5 (overconfidence=ruin) /
#   iron-rule-2 (never weaken a gate to manufacture trades — this gate only DEMOTES).
"""Antibody: a buy_no candidate on a bin the market prices as LIKELY is unconstructable.

ROOT (settled outcomes, NOT shadow homework): across 15 days of zero qualifying
trades, the candidates that DID form were dominated by a single structural loser
— the system bought NO on CHEAP bins (market prices YES>0.8 / NO<0.2). The market
is confident the bin settles there; the system bet against it on a miscalibrated,
OVERCONFIDENT q tail (assigns ~0 mass to a bin the market+reality say is likely).
buy_no at NO-price<0.2 went 0/12, the dominant capital bleed (-71%).

The structural fix (Fitz #1 / #4): make "buy_no against a confident market without
overwhelming settlement-licensed evidence" UNCONSTRUCTABLE at the trade_score /
direction stage. ``_market_disagreement_demotes_buy_no`` is the predicate; the
reactor forces score→0 and prefilter False when it fires, so the proof can never
be selected as the actionable candidate.

This is a CONJUNCTION guard, not a blanket ban:
  * It never touches buy_yes.
  * It never touches a buy_no whose NO price is NOT cheap (the market is not
    confident → legitimate disagreement is fine).
  * It never touches a buy_no whose q_lcb is genuinely EXTREME — an independent,
    settlement-licensed lower bound (not the point estimate) saying the bin truly
    will not settle. High-conviction disagreement survives.

These tests assert the RELATIONSHIP across the cheap-NO / weak-evidence boundary,
not a single function value — they fail the moment the demotion conjunction is
loosened in either direction.
"""

from __future__ import annotations

import pytest

from src.engine.event_reactor_adapter import (
    _MARKET_DISAGREE_NO_PRICE_MAX,
    _MARKET_DISAGREE_QLCB_EXTREME,
    _market_disagreement_demotes_buy_no,
)


def _demoted(*, direction="buy_no", market_no_price, q_lcb_5pct):
    return _market_disagreement_demotes_buy_no(
        direction=direction,
        market_no_price=market_no_price,
        q_lcb_5pct=q_lcb_5pct,
    )


# ── The losing pattern is now unconstructable ────────────────────────────────


def test_cheap_no_with_nonextreme_qlcb_is_demoted():
    """The exact 0/12 loser: market prices NO at 0.10 (confident YES), q_lcb not
    extreme → the contrarian buy_no MUST be demoted."""
    assert _demoted(market_no_price=0.10, q_lcb_5pct=0.30) is True


def test_cheap_no_just_under_threshold_is_demoted():
    assert _demoted(market_no_price=_MARKET_DISAGREE_NO_PRICE_MAX - 0.001, q_lcb_5pct=0.20) is True


# ── Legitimate trades are NOT touched (no false demotions) ───────────────────


def test_buy_yes_is_never_touched():
    """The guard is direction-asymmetric: buying YES on a cheap-NO bin is buying
    WITH the confident market — always allowed."""
    assert _demoted(direction="buy_yes", market_no_price=0.10, q_lcb_5pct=0.30) is False


def test_non_cheap_no_is_not_demoted():
    """When the market does NOT confidently favor YES (NO price above threshold),
    a buy_no is legitimate disagreement and survives."""
    assert _demoted(market_no_price=_MARKET_DISAGREE_NO_PRICE_MAX + 0.001, q_lcb_5pct=0.30) is False
    assert _demoted(market_no_price=0.45, q_lcb_5pct=0.30) is False


def test_high_conviction_disagreement_survives():
    """Cheap NO BUT extreme independently-grounded q_lcb → the lower bound itself
    licenses the bet; high-conviction disagreement is NOT demoted (we do not
    hard-exclude legitimate contrarian alpha)."""
    assert _demoted(market_no_price=0.05, q_lcb_5pct=_MARKET_DISAGREE_QLCB_EXTREME) is False
    assert _demoted(market_no_price=0.05, q_lcb_5pct=_MARKET_DISAGREE_QLCB_EXTREME - 0.01) is False


def test_missing_price_defers_to_upstream():
    """No executable price → the proof is already non-tradeable (missing_reason);
    the guard must not claim a demotion it cannot justify."""
    assert _demoted(market_no_price=None, q_lcb_5pct=0.30) is False


# ── Relationship / boundary invariants (the durable part) ────────────────────


@pytest.mark.parametrize("q_lcb", [0.06, 0.10, 0.30, 0.50, 0.90])
def test_demotion_monotone_in_evidence_at_fixed_cheap_price(q_lcb):
    """At a fixed CHEAP NO price, the bet is demoted for EVERY q_lcb above the
    extreme threshold. The only escape is overwhelming independent evidence —
    never a slightly-better point estimate. This is the overconfidence antibody:
    a high q_lcb (the tail thinks the bin won't settle) is exactly the
    miscalibration we distrust against a confident market."""
    assert _demoted(market_no_price=0.08, q_lcb_5pct=q_lcb) is True


def test_the_escape_is_the_lower_bound_not_the_market():
    """Cross-boundary relationship: holding evidence weak (q_lcb non-extreme), the
    demotion flips OFF precisely when the market stops being confident — i.e. the
    guard keys on the MARKET's confidence, while the escape keys on INDEPENDENT
    evidence. The two conditions are orthogonal and both required."""
    weak_q = 0.30
    # Confident market + weak evidence → demoted.
    assert _demoted(market_no_price=0.10, q_lcb_5pct=weak_q) is True
    # Same weak evidence, market no longer confident → allowed.
    assert _demoted(market_no_price=0.50, q_lcb_5pct=weak_q) is False
    # Confident market again, but now overwhelming evidence → allowed.
    assert _demoted(market_no_price=0.10, q_lcb_5pct=_MARKET_DISAGREE_QLCB_EXTREME) is False
