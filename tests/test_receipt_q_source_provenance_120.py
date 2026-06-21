# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: #120 — persist the calibrator that produced q on each receipt.
#   Live q is per-cell EMOS(330)/maze(70) (sole_calibrator ON, era.py:3735-3818);
#   receipts carried only scalar q_live with NO calibrator tag, so settlement
#   could not attribute EMOS-cells vs maze-cells (the per-city PROMOTE evidence).
#   _edli_q_source was computed (era.py:3772) but never persisted (audit A5).
# Lifecycle: created=2026-06-04; last_reviewed=2026-06-05; last_reused=2026-06-05
# Purpose: Relationship antibody — q_source (EMOS vs maze calibrator) rides the receipt_json blob; q_source=None is hash-stable vs the pre-#120 baseline (#120).
# Reuse: Re-run when receipt serialization or _edli_q_source persistence changes.
"""Relationship tests for #120 — q_source rides the receipt_json blob.

Two invariants:
  1. HASH STABILITY: a receipt with q_source=None serializes byte-identically to
     the pre-#120 baseline (the field is omitted) — no EdliReceiptHashDrift on the
     ~60k existing shadow receipts.
  2. PROVENANCE: a receipt with q_source set carries it in receipt_json, so the
     calibrator is auditable from the DB forever (json_extract).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.events.reactor import EventSubmissionReceipt
from src.events.no_submit_receipts import _receipt_json


def _receipt(**overrides) -> EventSubmissionReceipt:
    base = dict(
        submitted=False,
        event_id="evt-1",
        causal_snapshot_id="snap-1",
        side_effect_status="NO_SUBMIT",
        proof_accepted=True,
        q_live=0.93,
        direction="buy_no",
        final_intent_id="fi-1",
    )
    base.update(overrides)
    return EventSubmissionReceipt(**base)


def test_q_source_absent_is_hash_stable():
    """q_source=None → 'q_source' NOT in receipt_json (byte-identical baseline)."""
    rj = _receipt_json(_receipt(q_source=None))
    assert '"q_source"' not in rj, (
        "q_source=None must be omitted from receipt_json for hash stability "
        f"vs pre-#120 receipts; got: {rj}"
    )


def test_q_source_present_is_recorded():
    """q_source='emos' → recoverable from the receipt_json blob."""
    rj = _receipt_json(_receipt(q_source="emos"))
    payload = json.loads(rj)
    assert payload.get("q_source") == "emos", f"q_source not persisted: {rj}"


def test_non_qkernel_receipt_omits_qkernel_execution_economics():
    payload = json.loads(
        _receipt_json(
            _receipt(
                q_source="emos",
                qkernel_execution_economics={},
            )
        )
    )

    assert "qkernel_execution_economics" not in payload


def test_replacement_forecast_receipt_tag_is_hash_stable_when_absent_and_recorded_when_set():
    """replacement_forecast=None must not drift pre-replacement receipt hashes."""
    no_tag = json.loads(_receipt_json(_receipt(replacement_forecast=None)))
    assert "replacement_forecast" not in no_tag

    tagged = json.loads(
        _receipt_json(
            _receipt(
                replacement_forecast={
                    "status": "SHADOW_VETO_ONLY",
                    "reason": "REPLACEMENT_FORECAST_ALLOWED",
                }
            )
        )
    )
    assert tagged["replacement_forecast"]["status"] == "SHADOW_VETO_ONLY"


def test_q_source_maze_value_recorded():
    rj = _receipt_json(_receipt(q_source="bias_platt"))
    assert json.loads(rj).get("q_source") == "bias_platt"


def test_q_source_does_not_perturb_other_fields():
    """Adding q_source must not change any other serialized field."""
    base = json.loads(_receipt_json(_receipt(q_source=None)))
    withsrc = json.loads(_receipt_json(_receipt(q_source="emos")))
    withsrc.pop("q_source", None)
    assert base == withsrc, "q_source addition perturbed other receipt fields"


# ---------------------------------------------------------------------------
# Integration: proof construction carries payload["_edli_q_source"] → proof.q_source
# (the instance-safe channel — same payload the #149 fix threads). Mirrors the
# harness in test_qlcb_le_qlive_restore_boundary_176.
# ---------------------------------------------------------------------------

import types  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from unittest.mock import patch  # noqa: E402

from src.calibration.qlcb_provenance import QlcbByDirection, QlcbProvenance  # noqa: E402
from src.events.candidate_binding import MarketTopologyCandidate  # noqa: E402
from src.types.market import Bin  # noqa: E402


def _run_proofs(q_source_value):
    b = Bin(low=None, high=32, label="32 or below", unit="F")
    c = MarketTopologyCandidate(
        city="Chicago", target_date="2026-06-04", metric="high",
        condition_id="cond-A", yes_token_id="tok-A-yes", no_token_id="tok-A-no", bin=b,
    )
    family = types.SimpleNamespace(candidates=(c,))
    lcb = QlcbByDirection()
    lcb[("cond-A", "buy_yes")] = QlcbProvenance(q_lcb=-0.02, calibration_source="FORECAST_BOOTSTRAP")
    lcb[("cond-A", "buy_no")] = QlcbProvenance(q_lcb=-0.02, calibration_source="FORECAST_BOOTSTRAP")
    mock_return = (
        {"cond-A": 0.5}, lcb,
        {("cond-A", "buy_yes"): 0.9, ("cond-A", "buy_no"): 0.9}, {},
        {"p_cal_vector_hash": "h", "p_live_vector_hash": "h"},
    )
    from src.engine.event_reactor_adapter import _generate_candidate_proofs
    event = types.SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY")
    payload = {} if q_source_value is None else {"_edli_q_source": q_source_value}
    sentinel = object()
    with patch("src.engine.event_reactor_adapter._live_yes_probabilities", return_value=mock_return):
        with patch("src.engine.event_reactor_adapter._native_costs_by_candidate_direction", return_value={}):
            return _generate_candidate_proofs(
                event=event, payload=payload, family=family, snapshot_rows=[],
                trade_conn=sentinel, forecast_conn=sentinel, calibration_conn=sentinel,
                decision_time=datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc),
            )


def test_proof_carries_q_source_from_payload():
    """The ONE-CALIBRATOR SEAM sets payload['_edli_q_source']; the proof must carry it."""
    proofs = _run_proofs("emos")
    assert proofs, "no proofs"
    assert all(p.q_source == "emos" for p in proofs), (
        f"proof.q_source not threaded from payload: {[p.q_source for p in proofs]}"
    )


def test_proof_q_source_none_when_unset():
    """No _edli_q_source in payload → proof.q_source is None (honest, not fabricated)."""
    proofs = _run_proofs(None)
    assert proofs and all(p.q_source is None for p in proofs)
