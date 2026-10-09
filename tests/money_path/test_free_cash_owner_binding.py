# Created: 2026-10-09
# Last reused/audited: 2026-10-09
# Authority basis: FINDING-B owner-cash port review, frozen global winner binding.
"""Current cash revalidates the same normal, persisted global proposal.

Reuse the owner's complete HKO custody/materialization/selection fixture. The
receipt wrapper calls the real adapter throughout; it changes only the external
cash provider while the owner's private connections and authority remain live.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def same_winner_cash_receipts(tmp_path, monkeypatch):
    from src.engine import event_reactor_adapter as adapter
    from tests.money_path import test_finding_b_free_cash_bound as owner

    build_receipt = adapter.build_event_bound_no_submit_receipt
    captured = {}

    def compare_current_cash(event, **kwargs):
        baseline = build_receipt(event, **kwargs)
        actuation = kwargs["global_actuation"]
        if actuation.selection_epoch_identity != "cash-baseline":
            return baseline

        assert not captured
        assert baseline.kelly_pass is True, baseline.reason
        selected_spend = float(actuation.decision.max_spend_usd)
        original_cash = float(kwargs["free_cash_usd_provider"]())
        assert 0.0 < selected_spend < original_cash
        receipt_ref = actuation.auction_receipt_ref
        assert receipt_ref is not None
        trade = kwargs["trade_conn"]

        def stored_winner():
            row = trade.execute(
                "SELECT mode, artifact_json FROM decision_log WHERE id = ?",
                (receipt_ref.decision_log_id,),
            ).fetchone()
            assert row is not None
            return tuple(row)

        persisted_before = stored_winner()
        identity_before = (actuation.actuation_identity, actuation.economic_identity)
        captured.update(baseline=baseline, selected_spend=selected_spend,
                        original_cash=original_cash)
        # Both ample values are distinct and within the original wallet wealth.
        # Selection is not rerun under either provider: this is one fixed winner.
        for name, cash in (("drop", selected_spend / 2.0),
                           ("ample", (selected_spend + original_cash) / 2.0)):
            calls = []

            def cash_provider():
                calls.append(cash)
                return cash

            changed = {**kwargs, "free_cash_usd_provider": cash_provider}
            assert changed["global_actuation"] is actuation
            captured[name] = build_receipt(event, **changed)
            captured[f"{name}_cash"] = cash
            assert calls and set(calls) == {cash}
            assert actuation.auction_receipt_ref is receipt_ref
            assert (actuation.actuation_identity, actuation.economic_identity) == identity_before
            assert float(actuation.decision.max_spend_usd) == selected_spend
            assert stored_winner() == persisted_before
        return baseline

    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", compare_current_cash)
    owner.cash_matrix.__wrapped__(tmp_path)
    assert captured
    return captured


def test_finite_cash_drop_rejects_same_persisted_winner(same_winner_cash_receipts):
    cases = same_winner_cash_receipts
    assert 0.0 < cases["drop_cash"] < cases["selected_spend"]
    receipt = cases["drop"]
    assert receipt.reason == "KELLY_PROOF_MISSING:GLOBAL_ACTUATION_FREE_CASH_SUPERSEDED"
    assert receipt.kelly_pass is False
    assert receipt.kelly_size_usd == 0.0
    assert receipt.final_intent_id is None
    assert receipt.submitted is False
    assert receipt.side_effect_status == "NO_SUBMIT"


def test_distinct_ample_cash_preserves_same_winner_stake(same_winner_cash_receipts):
    cases = same_winner_cash_receipts
    assert cases["selected_spend"] < cases["ample_cash"] < cases["original_cash"]
    baseline, ample = cases["baseline"], cases["ample"]
    assert ample.kelly_pass is True, ample.reason
    assert 0.0 < baseline.kelly_size_usd == ample.kelly_size_usd == cases["selected_spend"]
    assert ample.token_id == baseline.token_id
    assert ample.qkernel_execution_economics == baseline.qkernel_execution_economics
    assert ample.submitted is baseline.submitted is False
    assert ample.side_effect_status == baseline.side_effect_status == "NO_SUBMIT"


def test_legacy_hko_fixture_stops_at_missing_maker_witness_before_cash(tmp_path, monkeypatch):
    """Characterize the genuine nonglobal fixture's earlier selection rejection.

    This fixture stops before cash resolution and submit recapture. It does not
    establish omitted-provider legacy compatibility or authorize bypassing the
    missing current maker-fill witness.
    """
    from src.engine import event_reactor_adapter as adapter
    from tests.money_path import test_finding_b_free_cash_bound as owner

    build_receipt = adapter.build_event_bound_no_submit_receipt
    recapture = adapter._evaluate_submit_recapture_for_selected
    captured = []
    recapture_calls = []

    def observe_recapture(*args, **kwargs):
        recapture_calls.append((args, kwargs))
        return recapture(*args, **kwargs)

    def replay_without_global_actuation(event, **kwargs):
        baseline = build_receipt(event, **kwargs)
        if kwargs["global_actuation"].selection_epoch_identity == "cash-baseline":
            assert baseline.kelly_pass is True, baseline.reason
            legacy_kwargs = {key: value for key, value in kwargs.items()
                             if key not in {"global_actuation", "free_cash_usd_provider"}}
            captured.append(build_receipt(event, **legacy_kwargs))
        return baseline

    monkeypatch.setattr(adapter, "_evaluate_submit_recapture_for_selected", observe_recapture)
    monkeypatch.setattr(adapter, "build_event_bound_no_submit_receipt", replay_without_global_actuation)
    owner.cash_matrix.__wrapped__(tmp_path)
    assert len(captured) == 1
    receipt = captured[0]
    assert receipt.reason == (
        "EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=6 other=6; "
        "best_rejected=cool buy_yes reason_class=other "
        "missing_reason=CURRENT_MAKER_FILL_WITNESS_UNAVAILABLE "
        "q_lcb=0.0983 price=0.0600 rejected_ev_per_dollar=0.6388"
    )
    assert recapture_calls == []
    assert receipt.kelly_pass is False
    assert receipt.kelly_size_usd == 0.0
    assert receipt.final_intent_id is None
    assert receipt.submitted is False
    assert receipt.side_effect_status == "NO_SUBMIT"
