# Created: 2026-06-10
# Last reused or audited: 2026-06-17
# Authority basis: qkernel forecast authority replacement for the old Milan direction-law
# antibody. Replays the incident family through _generate_candidate_proofs (the live proof
# seam), asserts the old rounded-mu direction veto no longer fires, and pins that legacy
# scalar selection is not a sufficient live forecast authority.
"""Milan-incident replay: proof generation no longer uses rounded-mu direction vetoes.

Relationship under test: posterior provenance (anchor_value_c /
bayes_precision_fusion.predictive_sigma_c) -> _live_yes_probabilities evidence ->
_generate_candidate_proofs. The q_lcb pathology that produced the incident
(q_lcb == q on far-left bins) is reproduced VERBATIM. That pathology must be handled by
qkernel payoff-vector authority, not by a modal-bin direction heuristic.
"""
from __future__ import annotations

import json
import types
from datetime import datetime, timezone
from unittest.mock import patch

from src.calibration.qlcb_provenance import QlcbByDirection, QlcbProvenance
from src.engine.event_reactor_adapter import (
    _direction_law_family_center,
    _generate_candidate_proofs,
)
from src.events.candidate_binding import MarketTopologyCandidate
from src.types.market import Bin

# Incident posterior 929 facts (state/zeus-forecasts.db).
INCIDENT_MU_C = 26.42049946463696
INCIDENT_SIGMA_C = 1.2630268963735225
# The FULL incident family (posterior 929 q_json + the certificate's q_lcb values).
# (condition, bin_low, bin_high, label, fused q, incident q_lcb_yes, yes ask, no ask)
_WILSON_FLOOR = 1.7110739380733428e-05
INCIDENT_BINS = (
    ("cond-21b", None, 21.0, "21°C or below", 0.06587363154189045, _WILSON_FLOOR, "0.002", "0.999"),
    ("cond-22", 22.0, 22.0, "22°C", 0.049016055976234776, 0.0046678, "0.003", "0.999"),
    ("cond-23", 23.0, 23.0, "23°C", 0.07060881547515835, 0.07060881547515835, "0.004", "0.998"),
    ("cond-24", 24.0, 24.0, "24°C", 0.09267120287031377, 0.09267120287031377, "0.016", "0.991"),
    ("cond-25", 25.0, 25.0, "25°C", 0.11081455148968361, 0.11081455148968361, "0.177", "0.856"),
    # cond-26 NO is given a CHEAP ask + a positive-EV NO q_lcb so that nothing but
    # the direction law can reject it (the discriminating near-NO probe).
    ("cond-26", 26.0, 26.0, "26°C", 0.12073006564240046, _WILSON_FLOOR, "0.44", "0.55"),
    ("cond-27", 27.0, 27.0, "27°C", 0.11984, _WILSON_FLOOR, "0.35", "0.68"),
    ("cond-28", 28.0, 28.0, "28°C", 0.10838, _WILSON_FLOOR, "0.084", "0.933"),
    ("cond-29", 29.0, 29.0, "29°C", 0.08930, _WILSON_FLOOR, "0.013", "0.993"),
    ("cond-30", 30.0, 30.0, "30°C", 0.06704, _WILSON_FLOOR, "0.004", "0.998"),
    ("cond-31p", 31.0, None, "31°C or higher", 0.10572, _WILSON_FLOOR, "0.003", "0.999"),
)
_NEAR_NO_QLCB = {"cond-26": 0.60}


def _row(condition_id: str, yes_ask: str, no_ask: str) -> dict:
    depth = {
        "YES": {
            "asks": [{"price": yes_ask, "size": "1000"}],
            "bids": [{"price": "0.009", "size": "100"}],
        },
        "NO": {
            "asks": [{"price": no_ask, "size": "1000"}],
            "bids": [{"price": "0.5", "size": "100"}],
        },
    }
    return {
        "snapshot_id": f"snap-{condition_id}",
        "condition_id": condition_id,
        "yes_token_id": f"{condition_id}-yes",
        "no_token_id": f"{condition_id}-no",
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": "0.001",
        "min_order_size": "5",
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": f"book-{condition_id}",
    }


def _family():
    candidates = tuple(
        MarketTopologyCandidate(
            city="Milan",
            target_date="2026-06-11",
            metric="high",
            condition_id=condition_id,
            yes_token_id=f"{condition_id}-yes",
            no_token_id=f"{condition_id}-no",
            bin=Bin(low=low, high=high, unit="C", label=f"{label}"),
        )
        for condition_id, low, high, label, _q, _lcb, _ya, _na in INCIDENT_BINS
    )
    return types.SimpleNamespace(candidates=candidates, city="Milan",
                                 target_date="2026-06-11", metric="high")


def _incident_probability_mock(*, with_fusion_center: bool):
    q_by_condition = {c: q for c, _l, _h, _lbl, q, _lcb, _ya, _na in INCIDENT_BINS}
    lcb = QlcbByDirection()
    p_values = {}
    for condition_id, _l, _h, _lbl, _q, q_lcb, _ya, _na in INCIDENT_BINS:
        lcb[(condition_id, "buy_yes")] = QlcbProvenance(
            q_lcb=q_lcb, calibration_source="FORECAST_BOOTSTRAP"
        )
        lcb[(condition_id, "buy_no")] = QlcbProvenance(
            q_lcb=_NEAR_NO_QLCB.get(condition_id, 0.0),
            calibration_source="FORECAST_BOOTSTRAP",
        )
        p_values[(condition_id, "buy_yes")] = 0.0
        p_values[(condition_id, "buy_no")] = 1.0
    evidence = {"p_cal_vector_hash": "h", "p_live_vector_hash": "h"}
    if with_fusion_center:
        evidence["forecast_mu_c"] = INCIDENT_MU_C
        evidence["forecast_predictive_sigma_c"] = INCIDENT_SIGMA_C
    return (q_by_condition, lcb, p_values, {}, evidence)


def _run(with_fusion_center: bool = True):
    family = _family()
    rows = [_row(c, ya, na) for c, _l, _h, _lbl, _q, _lcb, ya, na in INCIDENT_BINS]
    sentinel = object()
    with patch(
        "src.engine.event_reactor_adapter._live_yes_probabilities",
        return_value=_incident_probability_mock(with_fusion_center=with_fusion_center),
    ):
        proofs = _generate_candidate_proofs(
            event=types.SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            payload={},
            family=family,
            snapshot_rows=rows,
            trade_conn=sentinel,
            forecast_conn=sentinel,
            calibration_conn=sentinel,
            decision_time=datetime(2026, 6, 10, 2, 57, tzinfo=timezone.utc),
        )
    return {(p.candidate.condition_id, p.direction): p for p in proofs}


def test_day0_absorbing_no_at_999_is_untradeable_before_selection():
    """A true Day0 absorbing NO belief is not itself a trade opportunity when the
    available entry price is already 0.999. The proof must carry the deterministic
    near-settled rejection instead of entering selection with a tiny positive EV."""

    candidate = MarketTopologyCandidate(
        city="Shanghai",
        target_date="2026-06-18",
        metric="low",
        condition_id="cond-25",
        yes_token_id="cond-25-yes",
        no_token_id="cond-25-no",
        bin=Bin(low=25.0, high=25.0, unit="C", label="25°C"),
    )
    family = types.SimpleNamespace(
        candidates=(candidate,),
        city="Shanghai",
        target_date="2026-06-18",
        metric="low",
    )
    lcb = QlcbByDirection()
    lcb[("cond-25", "buy_yes")] = QlcbProvenance(
        q_lcb=0.0, calibration_source="SETTLEMENT_ISOTONIC"
    )
    lcb[("cond-25", "buy_no")] = QlcbProvenance(
        q_lcb=1.0, calibration_source="SETTLEMENT_ISOTONIC"
    )
    p_values = {("cond-25", "buy_yes"): 1.0, ("cond-25", "buy_no"): 0.0}
    generated_prefilter = {("cond-25", "buy_yes"): True, ("cond-25", "buy_no"): True}
    mock_return = (
        {"cond-25": 0.0},
        lcb,
        p_values,
        generated_prefilter,
        {
            "p_cal_vector_hash": "day0-hard-fact",
            "p_live_vector_hash": "day0-hard-fact",
            "probability_authority": "day0_absorbing_hard_fact",
        },
    )
    sentinel = object()
    with patch(
        "src.engine.event_reactor_adapter._live_yes_probabilities",
        return_value=mock_return,
    ):
        proofs = _generate_candidate_proofs(
            event=types.SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
            payload={},
            family=family,
            snapshot_rows=[_row("cond-25", "0.001", "0.999")],
            trade_conn=sentinel,
            forecast_conn=sentinel,
            calibration_conn=sentinel,
            decision_time=datetime(2026, 6, 17, 16, 10, tzinfo=timezone.utc),
        )

    by_side = {(p.candidate.condition_id, p.direction): p for p in proofs}
    no_proof = by_side[("cond-25", "buy_no")]
    assert no_proof.q_lcb_5pct == 1.0
    assert no_proof.execution_price is not None
    assert float(no_proof.execution_price.value) == 0.999
    assert no_proof.missing_reason is not None
    assert no_proof.missing_reason.startswith("ADMISSION_NEAR_SETTLED_PRICE:")
    assert no_proof.trade_score == 0.0
    assert no_proof.passed_prefilter is False


def test_incident_24c_buy_yes_is_not_killed_by_legacy_direction_law():
    """The old modal-bin direction law must not reject the 24C buy_yes proof."""
    proofs = _run()
    p = proofs[("cond-24", "buy_yes")]
    assert p.missing_reason is None
    assert p.passed_prefilter is True
    # The #2-ranked incident candidate (23C) is likewise left to qkernel economics.
    p23 = proofs[("cond-23", "buy_yes")]
    assert p23.missing_reason is None


def test_forecast_adjacent_yes_is_not_direction_law_rejected():
    """26C (contains-mu* adjacent bin): the law must NOT reject it — whatever else
    (capital efficiency on its crushed q_lcb) does is that gate's business."""
    proofs = _run()
    p = proofs[("cond-26", "buy_yes")]
    assert p.missing_reason is None or not p.missing_reason.startswith(
        "DIRECTION_LAW_BIN_FORECAST_MISMATCH"
    )


def test_buy_no_bins_are_not_rejected_by_legacy_direction_law():
    proofs = _run()
    far_no = proofs[("cond-24", "buy_no")]
    assert far_no.missing_reason is None or not far_no.missing_reason.startswith(
        "DIRECTION_LAW_BIN_FORECAST_MISMATCH"
    )
    near_no = proofs[("cond-26", "buy_no")]
    assert near_no.missing_reason is None or not near_no.missing_reason.startswith(
        "DIRECTION_LAW_BIN_FORECAST_MISMATCH"
    )


def test_legacy_fallback_q_mean_does_not_create_direction_veto():
    """Rows without fusion provenance do not revive the old rounded-mu veto."""
    proofs = _run(with_fusion_center=False)
    p = proofs[("cond-24", "buy_yes")]
    assert p.missing_reason is None


def test_family_center_provenance_order():
    """Evidence center wins over the q-mean; q-mean engages only when absent."""
    family = _family()
    q = {c: qv for c, _l, _h, _lbl, qv, _lcb, _ya, _na in INCIDENT_BINS}
    mu, sigma = _direction_law_family_center(
        family=family, q_by_condition=q,
        probability_evidence={"forecast_mu_c": INCIDENT_MU_C,
                              "forecast_predictive_sigma_c": INCIDENT_SIGMA_C},
    )
    assert mu == INCIDENT_MU_C and sigma == INCIDENT_SIGMA_C
    mu_fallback, sigma_fallback = _direction_law_family_center(
        family=family, q_by_condition=q, probability_evidence={},
    )
    # q-weighted mean over the full incident family (open-ended bins contribute
    # their single bound) ~= 26.4 — far side of 24C even without fusion provenance.
    centers = {c: (l if l is not None else h) if (l is None or h is None) else (l + h) / 2.0
               for c, l, h, _lbl, _q, _lcb, _ya, _na in INCIDENT_BINS}
    expected = sum(q[c] * centers[c] for c in q) / sum(q.values())
    assert abs(mu_fallback - expected) < 1e-9
    assert sigma_fallback is None


# ---------------------------------------------------------------------------
# Selector hardening: gate-rejected proofs are unrankable, not merely
# unsubmittable — and the verbatim incident family therefore NO-TRADES.
# ---------------------------------------------------------------------------
def test_incident_family_shows_legacy_selector_is_not_forecast_authority():
    """The old scalar selector can still choose the incident leg, so it is not live authority."""
    from src.engine.event_reactor_adapter import _selected_candidate_proof

    proofs = tuple(_run().values())
    selected = _selected_candidate_proof({"family_id": "milan-incident"}, proofs)
    assert selected is not None
    assert selected.candidate.condition_id == "cond-24"
    assert selected.direction == "buy_yes"
    assert selected.selection_authority_applied is None


def test_legacy_selector_would_starve_sibling_without_qkernel_authority():
    """A corrupt high-q_lcb legacy proof demonstrates why forecast live requires qkernel."""
    import json as _json
    import types as _types
    from datetime import datetime as _dt, timezone as _tz
    from unittest.mock import patch as _patch

    from src.engine.event_reactor_adapter import (
        _generate_candidate_proofs as _gen,
        _selected_candidate_proof as _select,
    )

    def _mk_row(condition_id, yes_ask):
        depth = {
            "YES": {"asks": [{"price": yes_ask, "size": "1000"}],
                    "bids": [{"price": "0.009", "size": "100"}]},
            "NO": {"asks": [{"price": "0.99", "size": "1000"}],
                   "bids": [{"price": "0.95", "size": "100"}]},
        }
        return {
            "snapshot_id": f"snap-{condition_id}", "condition_id": condition_id,
            "yes_token_id": f"{condition_id}-yes", "no_token_id": f"{condition_id}-no",
            "selected_outcome_token_id": "", "outcome_label": "",
            "min_tick_size": "0.001", "min_order_size": "5",
            "fee_details_json": _json.dumps({"fee_rate_fraction": 0.0}),
            "neg_risk": 0, "orderbook_depth_json": _json.dumps(depth),
            "tradeability_status_json": "{}", "book_hash": f"book-{condition_id}",
        }

    candidates = tuple(
        MarketTopologyCandidate(
            city="Milan", target_date="2026-06-11", metric="high",
            condition_id=cid, yes_token_id=f"{cid}-yes", no_token_id=f"{cid}-no",
            bin=Bin(low=low, high=low, unit="C", label=f"{int(low)}°C"),
        )
        for cid, low in (("cond-24", 24.0), ("cond-26", 26.0))
    )
    family = _types.SimpleNamespace(candidates=candidates, city="Milan",
                                    target_date="2026-06-11", metric="high")
    lcb = QlcbByDirection()
    # 24C: corrupt high q_lcb that dominates the legacy selector.
    lcb[("cond-24", "buy_yes")] = QlcbProvenance(q_lcb=0.30, calibration_source="FORECAST_BOOTSTRAP")
    lcb[("cond-24", "buy_no")] = QlcbProvenance(q_lcb=0.0, calibration_source="FORECAST_BOOTSTRAP")
    # 26C: modest licensed edge sibling.
    lcb[("cond-26", "buy_yes")] = QlcbProvenance(q_lcb=0.12, calibration_source="SETTLEMENT_ISOTONIC")
    lcb[("cond-26", "buy_no")] = QlcbProvenance(q_lcb=0.0, calibration_source="SETTLEMENT_ISOTONIC")
    mock_return = (
        {"cond-24": 0.30, "cond-26": 0.13}, lcb,
        {("cond-24", "buy_yes"): 0.0, ("cond-24", "buy_no"): 1.0,
         ("cond-26", "buy_yes"): 0.0, ("cond-26", "buy_no"): 1.0},
        {},
        {"p_cal_vector_hash": "h", "p_live_vector_hash": "h",
         "forecast_mu_c": INCIDENT_MU_C,
         "forecast_predictive_sigma_c": INCIDENT_SIGMA_C},
    )
    rows = [_mk_row("cond-24", "0.016"), _mk_row("cond-26", "0.03")]
    sentinel = object()
    with _patch("src.engine.event_reactor_adapter._live_yes_probabilities",
                return_value=mock_return):
        proofs = _gen(
            event=_types.SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
            payload={}, family=family, snapshot_rows=rows,
            trade_conn=sentinel, forecast_conn=sentinel, calibration_conn=sentinel,
            decision_time=_dt(2026, 6, 10, 3, 0, tzinfo=_tz.utc),
        )
    selected = _select({"family_id": "milan-starve"}, proofs)
    assert selected is not None
    assert selected.candidate.condition_id == "cond-24"
    assert selected.direction == "buy_yes"
    assert selected.selection_authority_applied is None
