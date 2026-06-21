# Created: 2026-06-21
# Last audited: 2026-06-21
# Authority basis: docs/evidence/live_order_pathology/2026-06-21_forward_chain_diagnosis.md
#   "CHOSEN FIX (consult-validated, two layers)" — LAYER 2 monitor read-through.
"""ANTIBODY: a non-day0 held position with a STALE/MISSING cached posterior must
attempt a SYNCHRONOUS same-authority read-through recompute BEFORE fail-closing.

The disease (live −$27.63): a held family's cached forecast_posteriors row goes
stale and the monitor fail-closes to HOLD (BELIEF_AUTHORITY_FAULT) FOREVER —
never recomputing — so the conservative CI_SEPARATED_REVERSAL exit is starved and
the position rides physics reversals to full settlement loss. These tests pin:

1. When the read-through yields a FRESH posterior, the monitor returns is_fresh=True
   (probability authority restored → the exit organ can arm the reversal this cycle).
2. When inputs are genuinely insufficient, the monitor STILL fail-closes (is_fresh
   not True) AND records a DURABLE, RETRYABLE belief_debt marker — never a silent
   permanent freeze.
3. NO FALSE EXIT: the monitor only supplies a fresh belief; it never itself decides
   an exit. A freshly-recomputed belief that has NOT reversed simply becomes fresh
   authority (HOLD is still decided downstream by the untouched CI gate).

These are antibodies: deleting the read-through call from monitor_probability_refresh
makes (1) and (3) fail (is_fresh would be False on a recompute-eligible family), and
removing the belief_debt record makes (2) fail.
"""
from __future__ import annotations

import pytest

from src.contracts import EntryMethod

BIN = "Will the highest temperature in Karachi be 37°C on June 12?"


def _pos():
    from src.state.portfolio import Position

    return Position(
        trade_id="t-readthrough-1",
        market_id="m1",
        city="Karachi",
        cluster="Karachi",
        target_date="2026-06-12",
        bin_label=BIN,
        direction="buy_no",
        unit="C",
        temperature_metric="high",
        entry_method="ens_member_counting",
        entry_price=0.66,
        p_posterior=0.855,
    )


def _stale_belief():
    from src.engine.position_belief import ReplacementBelief

    return ReplacementBelief(
        held_side_prob=0.758, q_yes_bin=0.242, posterior_id="p9",
        computed_at="2026-06-12T00:00:00+00:00", age_hours=99.0,
        fresh=False, bin_key=BIN, direction="buy_no",
    )


def test_readthrough_fresh_recompute_restores_probability_authority(monkeypatch):
    """Stale cached belief + a successful read-through recompute → is_fresh True.

    Antibody: without the read-through call this returns is_fresh False (the live
    freeze). The recompute yields the held-side prob and the monitor attests it.
    """
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: _stale_belief())
    # The legacy chain must NEVER be the freshness source.
    monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))
    # Read-through recompute succeeds and returns the held-side prob (e.g. NO has
    # collapsed to 0.30 — a reversal the frozen 0.758 belief could never see).
    monkeypatch.setattr(
        mr, "_attempt_held_belief_readthrough", lambda *a, **k: 0.30
    )

    pos = _pos()
    prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
        pos, conn=None, city=object(), target_d=None,
    )

    assert is_fresh is True
    assert prob == pytest.approx(0.30)
    # The belief is branded as a same-authority read-through, never a legacy substitution.
    assert any(
        "readthrough" in v or "read_through" in v
        for v in refresh_pos.applied_validations
    )
    assert not any(v == "legacy_belief_substitution_suppressed" for v in refresh_pos.applied_validations)


def test_readthrough_insufficient_inputs_failclose_with_durable_belief_debt(monkeypatch):
    """Stale cached belief + read-through NOT eligible → fail-close AND a durable,
    retryable belief_debt marker (family/reason/first_failed_at/attempts).

    Antibody: removing the belief_debt record makes this assertion fail — a silent
    permanent freeze (the chronic Karachi case) would be undetectable.
    """
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: _stale_belief())
    monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))
    # Read-through cannot honestly recompute (no current single_runs / no on-disk anchor).
    monkeypatch.setattr(mr, "_attempt_held_belief_readthrough", lambda *a, **k: None)
    reseed_called: list[tuple] = []
    monkeypatch.setattr(
        mr, "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kw: reseed_called.append((kw.get("city"), kw.get("target_date"), kw.get("metric"))) or None,
    )

    pos = _pos()
    prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
        pos, conn=None, city=object(), target_d=None,
    )

    assert is_fresh is not True
    # Still fail-closed under the belief-authority guard.
    assert any(v == "BELIEF_AUTHORITY_FAULT" for v in pos.applied_validations)
    # Durable, retryable belief-debt record exists and carries the family + reason.
    debt = [v for v in pos.applied_validations if v.startswith("belief_debt")]
    assert debt, f"no belief_debt marker recorded: {pos.applied_validations}"
    marker = debt[0]
    assert "Karachi" in marker
    assert "2026-06-12" in marker
    assert "high" in marker
    # The existing reseed repair lane still fires (NOT a silent freeze).
    assert reseed_called == [("Karachi", "2026-06-12", "high")]


def test_readthrough_does_not_itself_decide_an_exit(monkeypatch):
    """NO FALSE EXIT: a fresh recompute only supplies belief; the monitor returns
    a probability + is_fresh, never an exit verdict. The CI separation conservatism
    lives entirely downstream and is untouched here."""
    import src.engine.monitor_refresh as mr
    import src.engine.position_belief as pb

    monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: _stale_belief())
    monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))
    # A fresh belief that has NOT reversed (still favors the held NO side).
    monkeypatch.setattr(mr, "_attempt_held_belief_readthrough", lambda *a, **k: 0.80)

    pos = _pos()
    result = mr.monitor_probability_refresh(pos, conn=None, city=object(), target_d=None)

    # The contract is exactly (prob, Position, is_fresh) — a belief, not an exit.
    assert isinstance(result, tuple) and len(result) == 3
    prob, refresh_pos, is_fresh = result
    assert is_fresh is True
    assert prob == pytest.approx(0.80)
    from src.state.portfolio import Position
    assert isinstance(refresh_pos, Position)
