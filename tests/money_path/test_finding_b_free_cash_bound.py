# Created: 2026-06-12
# Last reused/audited: 2026-06-12
# Authority basis: external deep code review 2026-06-12 FINDING-B (operator direct-fix
#   order). The free-cash one-time bound silently VANISHED (free_cash_usd=None, no
#   clamp) whenever a bankroll_usd_provider was injected. The fix threads a companion
#   free_cash_usd_provider; a live free-cash authority that returns None is a typed
#   transient fault (BANKROLL_FREE_CASH_MISSING), never a silent unclamped submit.
"""FINDING-B relationship invariant: when the bankroll basis comes from an injected
provider AND a companion free-cash authority is wired, the chosen stake is bounded by
free cash (min, applied once); a free-cash authority that cannot resolve fails CLOSED
with a typed TRANSIENT reason rather than sizing unclamped.

These reuse the full receipt-sizing harness from test_event_reactor_no_bypass (the same
fixtures that exercise the live Kelly path), so the assertions pin the END-TO-END
relationship (provider in -> bounded stake / typed fault out), not a unit shim.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.events.reactor import _is_transient_money_path_reason

# Reuse the proven receipt-sizing harness + fixtures.
from tests.engine.test_event_reactor_no_bypass import (  # noqa: E402
    _bound_forecast_event,
    _receipt,
    _trade_conn_with_snapshot,
)


@pytest.fixture(autouse=True)
def _isolate_edli_settings(monkeypatch):
    """Mirror the no_bypass module's isolation so the fixture reaches the Kelly path
    (EMOS sole-calibrator / bias-correction / soft-anchor trade authority forced OFF —
    the fixture has no calibration/bias rows and must not be overridden by live flags)."""
    from src.config import settings

    edli = dict(settings._data["edli"])
    edli["edli_emos_sole_calibrator_enabled"] = False
    edli["edli_bias_correction_enabled"] = False
    monkeypatch.setitem(settings._data, "edli", edli)
    feature_flags = dict(settings._data["feature_flags"])
    feature_flags["openmeteo_ecmwf_ifs9_bayes_fusion_live_enabled"] = False
    feature_flags["openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled"] = False
    feature_flags["qkernel_spine_enabled"] = True
    monkeypatch.setitem(settings._data, "feature_flags", feature_flags)

    from src.engine import event_reactor_adapter as adapter
    from src.engine import qkernel_spine_bridge

    def _qkernel_proofs(*, family, snapshot_rows, **_kwargs):
        candidate = family.candidates[0]
        row = dict(snapshot_rows[0])
        bin_id = str(candidate.condition_id)
        qkernel_economics = {
            "source": "qkernel_spine",
            "candidate_id": f"DIRECT_YES:{bin_id}",
            "route_id": f"DIRECT_YES:{bin_id}@proof",
            "side": "YES",
            "bin_id": bin_id,
            "payoff_q_point": 0.95,
            "payoff_q_lcb": 0.90,
            "q_dot_payoff": 0.95,
            "edge_lcb": 0.50,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": "14.0",
            "optimal_delta_u": 0.02,
            "cost": 0.40,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
            "q_lcb_guard_basis": "OOF_WILSON_95",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "free_cash_bound_q_lcb",
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_cell_key": "free_cash_bound_selection",
            "selection_guard_n": 80,
            "selection_guard_q_safe": 0.90,
        }
        return (
            adapter._CandidateProof(
                candidate=candidate,
                token_id=candidate.yes_token_id,
                direction="buy_yes",
                row=row,
                executable_snapshot_id=str(row["snapshot_id"]),
                execution_price=ExecutionPrice(
                    0.40,
                    "ask",
                    fee_deducted=True,
                    currency="probability_units",
                ),
                q_posterior=0.95,
                q_lcb_5pct=0.90,
                c_cost_95pct=0.40,
                p_fill_lcb=0.90,
                trade_score=1.0,
                p_value=0.01,
                passed_prefilter=True,
                native_quote_available=True,
                p_cal_vector_hash="cal-hash",
                p_live_vector_hash="live-hash",
                q_source="qkernel_spine",
                selection_authority_applied="qkernel_spine",
                qkernel_execution_economics=qkernel_economics,
            ),
        )

    monkeypatch.setattr(adapter, "_generate_candidate_proofs", _qkernel_proofs)
    monkeypatch.setattr(
        adapter,
        "_record_qkernel_selection_family_facts",
        lambda *_args, **_kwargs: {"status": "written", "families": 1, "hypotheses": 1},
    )
    monkeypatch.setattr(
        adapter,
        "evaluate_fdr_full_family",
        lambda *, family_id, all_hypothesis_ids, selected_hypothesis_ids, **_kwargs: SimpleNamespace(
            passed=True,
            fdr_family_id=family_id,
            attempted_hypotheses=len(tuple(all_hypothesis_ids)),
            selected_hypotheses=tuple(selected_hypothesis_ids),
            selected_post_fdr=tuple(selected_hypothesis_ids),
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_evaluate_submit_recapture_for_selected",
        lambda *, free_cash_usd=None, **_kwargs: (
            adapter.SubmitRecaptureDecision(
                state=adapter.CandidateLifecycleState.READY_TO_SUBMIT,
                may_submit=True,
                detail="free_cash_test_recapture_allowed",
            ),
            min(14.0, float(free_cash_usd)) if free_cash_usd is not None else 14.0,
            ExecutionPrice(
                0.40,
                "ask",
                fee_deducted=True,
                currency="probability_units",
            ),
        ),
    )
    monkeypatch.setattr(adapter._shift_bin_wiring, "active_shift_lease_for_family", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(qkernel_spine_bridge, "qkernel_spine_enabled", lambda: True)
    def _decide_family_via_spine(**kwargs):
        proof = kwargs["proofs"][0]
        economics_payload = dict(proof.qkernel_execution_economics)
        bin_id = str(economics_payload["bin_id"])
        side = str(economics_payload["side"])
        economics = SimpleNamespace(**economics_payload)
        decision = SimpleNamespace(
            decision_id="free-cash-qkernel-decision",
            receipt_hash="free-cash-qkernel-receipt",
            selected=SimpleNamespace(candidate_id=economics_payload["candidate_id"]),
            omega=SimpleNamespace(
                bins=(SimpleNamespace(bin_id=bin_id, label="80-82"),)
            ),
            candidate_decisions=(
                SimpleNamespace(
                    route=SimpleNamespace(side=side, bin_id=bin_id),
                    economics=economics,
                ),
            ),
            economics_by_key={(bin_id, side): economics_payload},
        )
        return SimpleNamespace(
            selected_proof=proof,
            no_trade_reason=None,
            decision=decision,
        )

    monkeypatch.setattr(qkernel_spine_bridge, "decide_family_via_spine", _decide_family_via_spine)
    monkeypatch.setattr(
        qkernel_spine_bridge,
        "qkernel_candidate_economics_by_bin_side",
        lambda decision: getattr(decision, "economics_by_key", {}),
    )


def test_free_cash_provider_binds_stake_to_free_cash():
    """Provider total equity = 1000, free cash = 5, strong edge whose unclamped stake is
    ~14 USD: the chosen stake MUST be clamped to <= free cash (the one-time cash bound)."""
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: 1000.0,
        free_cash_usd_provider=lambda: 5.0,
    )
    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd is not None
    assert receipt.kelly_size_usd <= 5.0 + 1e-9


def test_free_cash_above_stake_does_not_inflate():
    """When free cash exceeds the fractional-Kelly stake, the bound is a no-op (min):
    the stake stays at its equity-scaled value, never raised to free cash."""
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: 1000.0,
        free_cash_usd_provider=lambda: 1000.0,
    )
    assert receipt.kelly_pass is True
    # The unclamped equity-scaled stake for this fixture is ~14 USD (well below 1000).
    assert receipt.kelly_size_usd is not None
    assert 0.0 < receipt.kelly_size_usd < 1000.0


def test_free_cash_unresolvable_under_live_provider_fails_closed_transient():
    """A wired free-cash authority that returns None is a TYPED FAULT, never a silent
    unclamped submit. The receipt does not pass, and the reason is classified TRANSIENT
    (requeue) so the next warm cycle re-resolves the wallet rather than terminal-burning."""
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: 1000.0,
        free_cash_usd_provider=lambda: None,
    )
    assert receipt.kelly_pass is False
    assert "BANKROLL_FREE_CASH_MISSING" in (receipt.reason or "")
    assert _is_transient_money_path_reason(receipt.reason) is True


def test_no_free_cash_provider_is_legacy_no_clamp():
    """Back-compat: a bankroll provider WITHOUT a free-cash provider (proof-only / tool
    injection that wired no cash authority) keeps the legacy no-clamp behavior — it does
    NOT fail closed, so existing proof-only callers are unaffected."""
    receipt = _receipt(
        _bound_forecast_event(),
        _trade_conn_with_snapshot(),
        bankroll_usd_provider=lambda: 1000.0,
    )
    assert receipt.kelly_pass is True
    assert receipt.kelly_size_usd is not None
    assert receipt.kelly_size_usd > 0.0
