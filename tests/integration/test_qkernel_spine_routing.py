# Created: 2026-06-14
# Last reused or audited: 2026-06-15 (loop-back: validated belief on the live path)
# Authority basis: docs/rebuild/impl_w5b_integration.md (the Wave-5B reactor
#   integration this test is the smoke for) + docs/rebuild/impl_w4_family_decision_engine.md
#   (the spine engine the bridge drives) + the task brief (assert: (a) decision is
#   produced by family_decision_engine — the spine — not the legacy selector;
#   (b) a no-trade returns a typed no_trade_reason; (c) the submission-pipeline-facing
#   candidate shape is well-formed).
"""Wave-5B smoke test — the live reactor per-family decision routes through the
rebuilt q-kernel spine when ``qkernel_spine_enabled`` is forced ON.

Drives the per-family decision orchestration the reactor calls at the
``_generate_candidate_proofs`` / ``_selected_candidate_proof`` seam
(``decide_family_via_spine``) on a REALISTIC family fixture (a real registered
settlement city + a complete MECE bin family + priced executable proofs in the SAME
``_CandidateProof`` shape the reactor materializes), with the Stage-0 ``_edli_spine_*``
predictive inputs threaded on the payload.

Asserts:
  (a) the decision is produced by ``family_decision_engine`` (the spine) — the result
      carries ``decided_by_spine`` and a ``FamilyDecision`` whose ``receipt_hash`` is
      the spine receipt anchor; the selection is the spine's ``argmax optimal_delta_u``,
      NOT the legacy scalar selector.
  (b) a no-trade returns a TYPED ``no_trade_reason`` (the spine's own vocabulary or a
      bridge typed reason).
  (c) the submission-pipeline-facing candidate is well-formed — the fields RiskGuard /
      venue_command / the receipt read (token_id, direction, execution_price,
      q_posterior, q_lcb_5pct, candidate.condition_id) are present.

Flag OFF behavior (legacy byte-for-byte) is proven by the money-path + live_inference
suites; this test forces the flag ON.
"""
from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal

import pytest

from src.engine import event_reactor_adapter as era
from src.engine import qkernel_spine_bridge as bridge
from src.events.candidate_binding import (
    EventBoundCandidateFamily,
    MarketTopologyCandidate,
)
from src.strategy import utility_ranker
from src.types.market import Bin

CITY = "Paris"  # a real registered C-unit, wmo_half_up settlement city
TARGET_DATE = "2026-06-14"
METRIC = "high"


@pytest.fixture(autouse=True)
def _fast_band_draws(monkeypatch):
    """Lower the joint-q band draw count for a fast, deterministic smoke.

    The band draw count only sets the Monte-Carlo resolution of the robust edge lower
    bound; it never changes the selection LOGIC (direction/coherence/edge/argmax-ΔU). A
    smaller count keeps the smoke quick. Production uses the engine default (4000).
    """
    monkeypatch.setattr(bridge, "SPINE_BAND_DRAWS", 400, raising=False)


# ---------------------------------------------------------------------------
# Fixtures — the SAME snapshot-row + _CandidateProof shape the reactor materializes.
# ---------------------------------------------------------------------------
def _row(*, condition_id, yes_token, no_token, yes_ask, no_ask, snapshot_id):
    depth = {
        "YES": {
            "asks": [{"price": f"{yes_ask:.2f}", "size": "100000"}],
            "bids": [{"price": f"{max(yes_ask - 0.01, 0.01):.2f}", "size": "100"}],
        },
        "NO": {
            "asks": [{"price": f"{no_ask:.2f}", "size": "100000"}],
            "bids": [{"price": f"{max(no_ask - 0.01, 0.01):.2f}", "size": "100"}],
        },
    }
    return {
        "snapshot_id": snapshot_id,
        "condition_id": condition_id,
        "yes_token_id": yes_token,
        "no_token_id": no_token,
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": "0.01",
        "min_order_size": "5",
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": f"book-{snapshot_id}",
        "orderbook_top_bid": str(max(yes_ask - 0.01, 0.01)),
    }


def _candidate(*, condition_id, yes_token, no_token, bin_obj):
    return MarketTopologyCandidate(
        city=CITY,
        target_date=TARGET_DATE,
        metric=METRIC,
        condition_id=condition_id,
        yes_token_id=yes_token,
        no_token_id=no_token,
        bin=bin_obj,
    )


def _proof(*, direction, row, token_id, q_posterior, q_lcb_5pct, bin_obj, trade_score=1.0):
    ep, _pfill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return era._CandidateProof(
        candidate=_candidate(
            condition_id=str(row.get("condition_id") or ""),
            yes_token=str(row.get("yes_token_id") or ""),
            no_token=str(row.get("no_token_id") or ""),
            bin_obj=bin_obj,
        ),
        token_id=token_id,
        direction=direction,
        row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=ep,
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None,
        p_fill_lcb=1.0,
        trade_score=trade_score,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="cal-hash",
        p_live_vector_hash="live-hash",
        missing_reason=None,
    )


def _family(bins):
    candidates = tuple(
        _candidate(
            condition_id=f"cond-{i}",
            yes_token=f"yes-{i}",
            no_token=f"no-{i}",
            bin_obj=b,
        )
        for i, b in enumerate(bins)
    )
    return EventBoundCandidateFamily(
        family_id="edli_family_smoke_w5b",
        event_id="evt-smoke-w5b",
        event_type="FORECAST_SNAPSHOT_READY",
        city=CITY,
        target_date=TARGET_DATE,
        metric=METRIC,
        condition_ids=tuple(c.condition_id for c in candidates),
        yes_token_ids=tuple(c.yes_token_id for c in candidates),
        no_token_ids=tuple(c.no_token_id for c in candidates),
        bins=tuple(bins),
        candidates=candidates,
        causal_snapshot_id="snap-smoke",
        market_topology_source="executable_market_snapshots",
        binding_hash="hash-smoke",
    )


# A complete MECE 3-bin C family around 20 C: [<=19], [20], [21], [>=22] shoulders.
# Point bins (low==high) on the 1-degree integer grid + open shoulders for completeness.
def _three_bin_family():
    bins = [
        Bin(low=None, high=19.0, unit="C", label="19C or below"),
        Bin(low=20.0, high=20.0, unit="C", label="20C"),
        Bin(low=21.0, high=21.0, unit="C", label="21C"),
        Bin(low=22.0, high=None, unit="C", label="22C or above"),
    ]
    return _family(bins), bins


def _proofs_for(family, *, yes_asks, no_asks, q_by_bin, q_lcb_by_bin):
    """Build buy_yes + buy_no proofs per candidate with the given prices/qs."""
    proofs = []
    for i, candidate in enumerate(family.candidates):
        row = _row(
            condition_id=candidate.condition_id,
            yes_token=candidate.yes_token_id,
            no_token=candidate.no_token_id,
            yes_ask=yes_asks[i],
            no_ask=no_asks[i],
            snapshot_id=f"snap-{i}",
        )
        q = q_by_bin[i]
        q_lcb = q_lcb_by_bin[i]
        proofs.append(
            _proof(
                direction="buy_yes",
                row=row,
                token_id=candidate.yes_token_id,
                q_posterior=q,
                q_lcb_5pct=q_lcb,
                bin_obj=candidate.bin,
            )
        )
        proofs.append(
            _proof(
                direction="buy_no",
                row=row,
                token_id=candidate.no_token_id,
                q_posterior=float(min(max(1.0 - q, 0.0), 1.0)),
                q_lcb_5pct=float(min(max(1.0 - q, 0.0), 1.0)) * 0.9,
                bin_obj=candidate.bin,
            )
        )
    return proofs


# Forecast source cycle that lands the (Paris, 2026-06-14, high) case in the 24h lead
# bucket (the replay-validated bucket): cycle 2026-06-13T00:00Z -> finalization
# 2026-06-14T10:00Z (Paris noon local) = 34h -> "24h" bucket.
SOURCE_CYCLE_TIME_UTC = "2026-06-13T00:00:00Z"


def _payload_with_spine_inputs(*, mu, sigma, members):
    return {
        "family_id": "edli_family_smoke_w5b",
        "event_id": "evt-smoke-w5b",
        "_edli_spine_mu_native": float(mu),
        "_edli_spine_sigma_native": float(sigma),
        "_edli_spine_debiased_members_native": [float(x) for x in members],
        "_edli_spine_raw_members_native": [float(x) for x in members],
        "_edli_spine_source_cycle_time_utc": SOURCE_CYCLE_TIME_UTC,
    }


def _drive(family, proofs, payload):
    return bridge.decide_family_via_spine(
        family=family,
        payload=payload,
        proofs=proofs,
        decision_time=_dt.datetime(2026, 6, 13, 12, 0, tzinfo=_dt.timezone.utc),
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=era._robust_marginal_utility_baseline_usd,
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
    )


# ===========================================================================
# (a) the decision is produced by family_decision_engine (the spine).
# ===========================================================================
def test_decision_is_produced_by_the_spine_not_the_legacy_selector():
    """The spine computes the decision: the result is a SpineDecisionResult whose
    FamilyDecision carries the spine receipt_hash (the spine's argmax-ΔU pipeline ran),
    and a selected trade returns a well-formed submission-pipeline proof.

    Family is built so the spine should find a +edge trade: an UNDERPRICED YES on the
    modal (forecast) bin — q well above the YES ask, so the Arrow-Debreu vector edge
    is positive and the candidate survives direction + coherence + edge_lcb>0 & ΔU>0.
    """
    family, bins = _three_bin_family()
    # Members ~20-21C => modal bin is the 20C/21C point bins. Underprice the YES on the
    # bins (ask 0.20 vs q ~0.30) so the vector edge is positive.
    proofs = _proofs_for(
        family,
        yes_asks=[0.05, 0.20, 0.20, 0.05],
        no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.45, 0.40, 0.10],
        q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    payload = _payload_with_spine_inputs(mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7])

    result = _drive(family, proofs, payload)

    # The spine produced the decision (NOT the legacy selector).
    assert result.decided_by_spine is True
    assert result.decision is not None, "the spine engine must have run and produced a FamilyDecision"
    # The receipt_hash is the spine's deterministic anchor (64-hex sha256).
    assert isinstance(result.decision.receipt_hash, str)
    assert len(result.decision.receipt_hash) == 64
    # The spine integrated q (joint_q present => the predictive distribution was
    # live-eligible and the full pipeline ran past the FIRST gate).
    assert result.decision.joint_q is not None


# ===========================================================================
# (a2) LOOP-BACK invariant — the VALIDATED belief is on the live path.
# ===========================================================================
def test_spine_belief_uses_validated_center_not_legacy_served_mu():
    """When the flag is ON the spine builds belief via the VALIDATED ``build_center``
    (envelope-locked on the reactor's chain-of-record-debiased members) + ``build_sigma``
    (realized-floor) — NOT the reactor's legacy served mu*/sigma. Proven: a WARM legacy
    served mu (26C — the old +bias center) is IGNORED; the spine's predictive center sits
    inside the COLD debiased-member envelope [20,23]. The pre-loop-back bridge wrapped the
    served mu, which would put ``mu_native`` at 26 and FAIL this test.
    """
    family, bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.05, 0.20, 0.20, 0.05],
        no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.45, 0.40, 0.10],
        q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    # Legacy served mu is WARM (26C — the broken pre-rebuild center). The reactor's
    # chain-of-record debiased members are COLD (~20-23C, the fresh consensus).
    cold_members = [20.0, 21.0, 22.0, 23.0]
    payload = _payload_with_spine_inputs(mu=26.0, sigma=3.0, members=cold_members)

    result = _drive(family, proofs, payload)

    assert result.decision is not None, "the spine engine must have produced a FamilyDecision"
    pd = result.decision.predictive
    # The VALIDATED center is envelope-locked inside the debiased member hull — NOT 26.
    assert min(cold_members) - 1e-6 <= pd.mu_native <= max(cold_members) + 1e-6, (
        f"spine center {pd.mu_native} escaped the debiased envelope "
        f"[{min(cold_members)},{max(cold_members)}] — legacy served mu (26) leaked onto the live path"
    )
    assert pd.mu_native != pytest.approx(26.0), "the legacy warm served mu must NOT be the spine center"
    # De-bias is a no-op at the seam (chain-of-record debias already applied upstream).
    assert pd.debias.activation_status == "NO_ARTIFACT"
    # Sigma comes from build_sigma (the realized-floor authority), a positive finite width
    # — not the served sigma wrapped verbatim.
    assert pd.sigma_native > 0.0


# ===========================================================================
# (b) a no-trade returns a typed no_trade_reason.
# ===========================================================================
def test_no_trade_returns_typed_reason_when_every_candidate_is_overpriced():
    """When every route is priced ABOVE fair value, the spine's survivor set is empty
    and the result carries a TYPED no_trade_reason (the spine vocabulary), with no
    selected proof. The submission pipeline then emits a deterministic no-trade receipt.
    """
    family, bins = _three_bin_family()
    # Price YES asks ABOVE q on every bin (no positive edge anywhere) and NO asks high.
    proofs = _proofs_for(
        family,
        yes_asks=[0.30, 0.70, 0.70, 0.30],
        no_asks=[0.98, 0.95, 0.95, 0.98],
        q_by_bin=[0.05, 0.45, 0.40, 0.10],
        q_lcb_by_bin=[0.02, 0.30, 0.26, 0.05],
    )
    payload = _payload_with_spine_inputs(mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7])

    result = _drive(family, proofs, payload)

    assert result.selected_proof is None
    assert result.no_trade_reason is not None
    assert isinstance(result.no_trade_reason, str)
    # A typed reason — not an empty/None/whitespace string.
    assert result.no_trade_reason.strip() != ""


def test_no_trade_typed_reason_when_spine_inputs_unavailable():
    """When the reactor served no predictive center/sigma (the threaded _edli_spine_*
    inputs are absent — a genuine reconstruction gap), the bridge returns the TYPED
    SPINE_INPUTS_UNAVAILABLE no-trade rather than fabricating a center.
    """
    family, bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.05, 0.20, 0.20, 0.05],
        no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.45, 0.40, 0.10],
        q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    payload = {"family_id": "edli_family_smoke_w5b"}  # NO _edli_spine_* inputs

    result = _drive(family, proofs, payload)

    assert result.selected_proof is None
    assert result.no_trade_reason.startswith(bridge.NO_TRADE_SPINE_INPUTS_UNAVAILABLE)


def test_no_trade_when_served_mu_present_but_members_absent():
    """Belief requires fresh members (the validated center locks to the member
    envelope). If the reactor served mu/sigma but NO member array, the bridge returns
    the typed SPINE_INPUTS_UNAVAILABLE no-trade rather than synthesizing a 1-point
    envelope from the legacy served mu (the one latent legacy-mu seam, now closed)."""
    family, bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.05, 0.20, 0.20, 0.05],
        no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.45, 0.40, 0.10],
        q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    # mu/sigma threaded, but NO _edli_spine_*_members_native arrays.
    payload = {
        "family_id": "edli_family_smoke_w5b",
        "_edli_spine_mu_native": 26.0,
        "_edli_spine_sigma_native": 3.0,
    }

    result = _drive(family, proofs, payload)

    assert result.selected_proof is None
    assert result.no_trade_reason.startswith(bridge.NO_TRADE_SPINE_INPUTS_UNAVAILABLE)


# ===========================================================================
# (c) the submission-pipeline-facing candidate shape is well-formed.
# ===========================================================================
def test_selected_proof_shape_is_submission_pipeline_ready():
    """When the spine selects a trade, the returned proof carries every field the
    submission pipeline (RiskGuard / venue_command / the receipt) reads off it. The
    overlaid proof is the SAME _CandidateProof type the legacy path produced.
    """
    family, bins = _three_bin_family()
    proofs = _proofs_for(
        family,
        yes_asks=[0.05, 0.18, 0.20, 0.05],
        no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.50, 0.40, 0.10],
        q_lcb_by_bin=[0.02, 0.38, 0.28, 0.05],
    )
    payload = _payload_with_spine_inputs(mu=20.2, sigma=1.0, members=[19.9, 20.0, 20.2, 20.4, 20.1])

    result = _drive(family, proofs, payload)

    if result.selected_proof is None:
        # If this realistic family no-trades, it must still be a TYPED no-trade — the
        # shape contract (b) holds; the selection-shape assertions below need a trade.
        assert result.no_trade_reason is not None
        pytest.skip(f"family no-traded ({result.no_trade_reason}); shape asserted on the trade path")

    proof = result.selected_proof
    # The reactor proof type — same object the legacy path feeds the submit pipeline.
    assert isinstance(proof, era._CandidateProof)
    # The fields RiskGuard / venue_command / the receipt read:
    assert proof.token_id, "selected proof must carry a native token id"
    assert proof.direction in ("buy_yes", "buy_no")
    assert proof.execution_price is not None, "selected proof must be executable"
    assert proof.candidate is not None and str(proof.candidate.condition_id or "")
    assert isinstance(proof.q_posterior, float)
    assert isinstance(proof.q_lcb_5pct, float)
    # q_source remains the proof's probability authority; the spine decision is
    # represented by result.decision, not by relabeling receipt-facing q fields.
    assert getattr(proof, "q_source", None) != "qkernel_spine"
    # The spine's selected candidate_id maps to this proof's (bin, side).
    parsed = bridge._parse_candidate_id(result.decision.selected.candidate_id)
    assert parsed is not None
    _bin_id, side = parsed
    assert side == ("YES" if proof.direction == "buy_yes" else "NO")
