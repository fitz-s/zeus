# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: Relationship + function tests for the mainstream-agreement gate (#135).
#   Antibody against cold/warm-bias false positives reaching arm candidates.
#   Covers: gate logic, tolerance-aware check-3, adapter-fires, hash stability.
# Reuse: run with live venv (.venv/bin/python -m pytest tests/test_mainstream_agreement_gate.py).
#   Before relying on antibody tests, confirm MarketAnalysis.member_maxes property exists
#   (BUG-1 fix) and _receipt_json strips None mainstream_* fields (BUG-2 fix).
# Authority basis: Task #135 mainstream-forecast direction-agreement gate
#   (/tmp/arm-truth.md keystone finding); DIRECTION LAW
#   (feedback_buy_direction_semantic); operator ARM criterion.
"""Relationship + function tests for the mainstream-agreement gate (#135).

REFERENCE-ONLY (operator directive 2026-06-03). The gate computes an independent
cross-check — does our forecast AGREE with an external mainstream within tolerance,
direction-consistent with both the mainstream-implied bin and our own modal bin —
and RECORDS the verdict on the receipt to inform the ARM decision. It takes NO part
in production selection: production trades on the FORECAST (trade_score / q_lcb);
the verdict can never exclude a candidate. The tests verify (a) the pass/fail
SIGNAL is computed correctly (the ARM reference), and (b) the reference-only
contract at the selector — a gate-failed candidate is STILL selected when it is the
forecast's best (test_mainstream_gate_is_reference_only_never_excludes_from_selection).

The #135-B provenance fields (raw vs corrected divergence) are recorded but NO
LONGER demote: the grid-to-point investigation proved the cold-bias corrections are
OOS-validated legitimate, so a correction-dependent agreement is annotated, not
failed (test_agreement_via_large_bias_correction_is_recorded_not_demoted).

Per the operator selection-fix rule, every relationship test below uses a DISTINCT
per-candidate fixture (real SF / Tel Aviv / Wellington / Panama numbers from the
live shadow board).
"""
from __future__ import annotations

import pytest

from src.types.market import Bin
from src.contracts.forecast_sharpness import ForecastSharpnessEvidence


# ---------------------------------------------------------------------------
# Fixtures — distinct per-candidate, drawn from the live 2026-06-03 board
# (/tmp/arm-truth.md). Each builds the family bin set + traded bin so the
# gate sees a complete, realistic topology.
# ---------------------------------------------------------------------------


def _f_bins(lows_highs, *, open_low_label, open_high_label):
    """Build a complete °F family: open-low shoulder, range bins, open-high shoulder."""
    bins = [Bin(low=None, high=lows_highs[0][0] - 1, label=open_low_label, unit="F")]
    for lo, hi in lows_highs:
        bins.append(Bin(low=lo, high=hi, label=f"{lo}-{hi}°F", unit="F"))
    bins.append(Bin(low=lows_highs[-1][1] + 1, high=None, label=open_high_label, unit="F"))
    return bins


def _c_point_bins(values, *, open_low_label, open_high_label):
    """Build a complete °C family of point bins plus shoulders."""
    bins = [Bin(low=None, high=values[0] - 1, label=open_low_label, unit="C")]
    for v in values:
        bins.append(Bin(low=v, high=v, label=f"{v}°C", unit="C"))
    bins.append(Bin(low=values[-1] + 1, high=None, label=open_high_label, unit="C"))
    return bins


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 1 — cold-biased SF buy_no over a warm bin ⇒ gate FAILS.
# Our ECMWF reads 61.5°F; mainstream says 66°F. The traded bin "≥66°F" is the
# bias projecting false confidence that SF can't hit a temp mainstream says it
# WILL. |our − mainstream| = 4.5°F > 2°F tolerance ⇒ mainstream_close=False.
# ---------------------------------------------------------------------------


def test_cold_biased_buy_no_over_warm_bin_fails_mainstream_close():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _f_bins(
        [(62, 63), (64, 65)],
        open_low_label="61°F or below",
        open_high_label="66°F or higher",
    )
    traded_bin = bins[-1]  # "66°F or higher"
    verdict = evaluate_mainstream_agreement(
        city="San Francisco",
        target_date="2026-06-03",
        unit="F",
        our_point=61.5,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_no",
        members=[60.0, 61.0, 61.5, 62.0, 61.0],  # modal bin = 61 or below
        mainstream_point=66.0,
    )
    assert verdict.mainstream_available is True
    assert verdict.mainstream_close is False  # 4.5°F > 2°F tolerance
    assert verdict.passed is False
    assert verdict.fail_reason == "MAINSTREAM_NOT_CLOSE"


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 2 — agreeing Wellington buy_yes ⇒ gate PASSES.
# Our 15.2°C ≈ modal 15°C ≈ mainstream 15.8°C (Δ=0.6°C; mainstream rounds to 16
# under the old hard bin-equality, but with tolerance-aware check: 15.8 is 0.8°C
# from the 15°C point bin ≤ 1.5°C tolerance → direction_agrees_mainstream=True).
# This is the exact live Wellington 06-04 case the verifier flagged as CRITICAL.
# ---------------------------------------------------------------------------


def test_agreeing_forecast_correct_direction_buy_yes_passes():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "15°C")
    # mainstream=15.8: our_point Δ=0.6°C (close ✓); 15.8 is 0.8°C from 15°C bin ≤ 1.5°C tol;
    # under old hard-equality: bin_containing(15.8) = 16°C ≠ traded 15°C → direction fail.
    # Under tolerance-aware: mainstream_within_tolerance_of_bin(15.8, [15,15], 1.5) = True → pass.
    verdict = evaluate_mainstream_agreement(
        city="Wellington",
        target_date="2026-06-04",
        unit="C",
        our_point=15.2,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_yes",
        members=[15.0, 15.1, 15.2, 15.3, 15.0],  # modal bin = 15
        mainstream_point=15.8,  # the live Wellington value the verifier flagged
    )
    assert verdict.mainstream_available is True
    assert verdict.mainstream_close is True     # |15.2 - 15.8| = 0.6 ≤ 1.5
    assert verdict.direction_agrees_mainstream is True  # 15.8 within ±1.5 of 15°C bin
    assert verdict.direction_agrees_our_modal is True
    assert verdict.passed is True


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 3 — buy_yes whose traded bin ≠ our own modal bin ⇒ FAILS.
# Tel Aviv 06-03: our modal bin is 26°C but the candidate is buy_yes on 25°C.
# This is the direction-inversion the operator flagged — buy_yes must be on
# our modal bin.
# ---------------------------------------------------------------------------


def test_buy_yes_on_non_modal_bin_fails_inversion():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [24, 25, 26, 27],
        open_low_label="23°C or below",
        open_high_label="28°C or higher",
    )
    # traded_bin = 26°C; mainstream_point = 26.0 → mainstream bin = 26 = traded_bin
    # → check 3 (direction_agrees_mainstream) PASSES for buy_yes (traded IS mainstream bin).
    # our_point = 25.4 → |25.4 - 26.0| = 0.6 ≤ 1.5 → check 2 PASSES.
    # members all round to 25 → our modal = 25 ≠ traded 26 → check 4 FAILS.
    # Inversion is the SOLE failure, cleanly isolated.
    traded_bin = next(b for b in bins if b.label == "26°C")
    verdict = evaluate_mainstream_agreement(
        city="Tel Aviv",
        target_date="2026-06-03",
        unit="C",
        our_point=25.4,
        bins=bins,
        # all members round (WMO half-up) to 25 → modal bin = 25 ≠ traded 26
        members=[25.4, 25.3, 25.2, 25.0, 24.9],
        traded_bin=traded_bin,
        direction="buy_yes",
        mainstream_point=26.0,  # Δ = 25.4 - 26.0 = -0.6 ≤ 1.5 → close
    )
    assert verdict.mainstream_close is True   # checks 1+2 pass
    assert verdict.direction_agrees_mainstream is True  # check 3 passes: traded=26=mainstream bin
    assert verdict.direction_agrees_our_modal is False  # check 4 fails: our modal=25 ≠ traded=26
    assert verdict.passed is False
    assert verdict.fail_reason == "DIRECTION_INVERSION_VS_OUR_MODAL"


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 4 — mainstream unavailable ⇒ gate FAIL_CLOSED (not pass).
# No-mainstream = ineligible, NEVER auto-pass.
# ---------------------------------------------------------------------------


def test_mainstream_unavailable_fails_closed():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "15°C")
    verdict = evaluate_mainstream_agreement(
        city="Wellington",
        target_date="2026-06-04",
        unit="C",
        our_point=15.2,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_yes",
        members=[15.0, 15.1, 15.2, 15.3, 15.0],
        mainstream_point=None,  # unavailable / stale
    )
    assert verdict.mainstream_available is False
    assert verdict.passed is False
    assert verdict.fail_reason == "MAINSTREAM_FAIL_CLOSED"


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 5 — buy_no on a bin == mainstream modal ⇒ FAILS.
# We'd be shorting the likely outcome. Mainstream point lands inside the
# traded bin, so a buy_no there contradicts mainstream.
# ---------------------------------------------------------------------------


def test_buy_no_on_mainstream_modal_bin_fails():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [27, 28, 29, 30],
        open_low_label="26°C or below",
        open_high_label="31°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "28°C")
    verdict = evaluate_mainstream_agreement(
        city="Panama City",
        target_date="2026-06-04",
        unit="C",
        our_point=28.4,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_no",  # shorting 28°C while mainstream says 28°C
        members=[28.0, 28.1, 28.4, 28.6, 28.0],
        mainstream_point=28.0,
    )
    assert verdict.mainstream_close is True  # forecast agrees with mainstream
    assert verdict.direction_agrees_mainstream is False  # but shorting the modal
    assert verdict.passed is False
    assert verdict.fail_reason == "DIRECTION_AGREES_MAINSTREAM_SHORTING_LIKELY"


# ---------------------------------------------------------------------------
# POSITIVE CONTROL — Panama ≥31°C buy_no (the genuine arm candidate).
# Our 28.4°C ≈ mainstream 28°C; we short ≥31°C which mainstream says won't
# settle (28 ≪ 31). Correct direction both ways ⇒ PASSES.
# ---------------------------------------------------------------------------


def test_panama_short_unlikely_high_bin_passes():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [27, 28, 29, 30],
        open_low_label="26°C or below",
        open_high_label="31°C or higher",
    )
    traded_bin = bins[-1]  # "31°C or higher"
    verdict = evaluate_mainstream_agreement(
        city="Panama City",
        target_date="2026-06-04",
        unit="C",
        our_point=28.4,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_no",
        members=[28.0, 28.1, 28.4, 28.6, 28.0],
        mainstream_point=28.0,
    )
    assert verdict.mainstream_close is True
    assert verdict.direction_agrees_mainstream is True  # 28 not in ≥31 bin
    assert verdict.direction_agrees_our_modal is True   # 28 not our modal 28? see below
    assert verdict.passed is True


# ---------------------------------------------------------------------------
# FUNCTION TESTS — tolerance boundaries, unit handling, shoulder bins.
# ---------------------------------------------------------------------------


def test_tolerance_boundary_celsius_exactly_at_limit_passes():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement, TOLERANCE_C

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "15°C")
    # our_point exactly TOLERANCE_C away from mainstream → close (<=)
    verdict = evaluate_mainstream_agreement(
        city="X",
        target_date="2026-06-04",
        unit="C",
        our_point=15.0,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_yes",
        members=[15.0, 15.0, 15.0, 15.0, 15.0],
        mainstream_point=15.0 + TOLERANCE_C,
    )
    assert verdict.mainstream_close is True


def test_tolerance_boundary_celsius_just_over_limit_fails():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement, TOLERANCE_C

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "15°C")
    verdict = evaluate_mainstream_agreement(
        city="X",
        target_date="2026-06-04",
        unit="C",
        our_point=15.0,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_yes",
        members=[15.0, 15.0, 15.0, 15.0, 15.0],
        mainstream_point=15.0 + TOLERANCE_C + 0.01,
    )
    assert verdict.mainstream_close is False


def test_fahrenheit_tolerance_is_two_degrees():
    from src.strategy.mainstream_agreement import TOLERANCE_F, TOLERANCE_C

    assert TOLERANCE_F == 2.0
    assert TOLERANCE_C == 1.5


def test_deltas_recorded_in_verdict():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _f_bins(
        [(62, 63), (64, 65)],
        open_low_label="61°F or below",
        open_high_label="66°F or higher",
    )
    traded_bin = bins[-1]
    verdict = evaluate_mainstream_agreement(
        city="San Francisco",
        target_date="2026-06-03",
        unit="F",
        our_point=61.5,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_no",
        members=[60.0, 61.0, 61.5, 62.0, 61.0],
        mainstream_point=66.0,
    )
    # delta must be signed our - mainstream and exposed for the receipt tag
    assert verdict.forecast_delta == pytest.approx(61.5 - 66.0)
    d = verdict.to_dict()
    assert d["mainstream_agreement_pass"] is False
    assert d["mainstream_point"] == 66.0
    assert d["our_point"] == 61.5
    assert d["forecast_delta"] == pytest.approx(-4.5)
    assert d["fail_reason"] == "MAINSTREAM_NOT_CLOSE"


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 6 — Wellington knife-edge boundary: mainstream 15.49 and 15.50
# must classify identically (both buy_yes passes for 15°C traded bin with 1.5°C tol).
# Validates that the tolerance-aware check has no rounding knife-edge.
# (Previously: mainstream 15.5 rounds to 16 → bin_containing gave 16°C bin ≠ 15 →
# buy_yes on 15°C was BLOCKED as DIRECTION_DISAGREES_MAINSTREAM_BUY_YES_OFF_BIN.
# After fix: check is continuous — 15.5 is within 0.5°C of the 15°C bin ≤ 1.5°C
# tolerance → direction_agrees_mainstream=True → PASSES.)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mainstream_pt", [15.49, 15.50, 15.8, 15.99])
def test_wellington_buy_yes_tolerance_aware_no_knife_edge(mainstream_pt):
    """Mainstream 15.x should all PASS for buy_yes on 15°C bin (Δ ≤ 1.5°C from our 15.2°C)."""
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "15°C")
    verdict = evaluate_mainstream_agreement(
        city="Wellington",
        target_date="2026-06-04",
        unit="C",
        our_point=15.2,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_yes",
        members=[15.0, 15.1, 15.2, 15.3, 15.0],  # modal bin = 15
        mainstream_point=mainstream_pt,
    )
    delta = abs(15.2 - mainstream_pt)
    assert verdict.mainstream_close is True, (
        f"mainstream {mainstream_pt} Δ={delta:.2f} should be close (≤1.5°C)"
    )
    assert verdict.direction_agrees_mainstream is True, (
        f"mainstream {mainstream_pt} near 15°C bin (within {1.5}°C tol) → buy_yes must agree. "
        f"fail_reason={verdict.fail_reason}"
    )
    assert verdict.passed is True, (
        f"Wellington buy_yes on 15°C with mainstream {mainstream_pt} must PASS. "
        f"fail_reason={verdict.fail_reason}"
    )


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 7 — SF cold-bias: with coords now in cities.json, the
# closeness gate catches SF via actual Δ logic (not coord-gap fail-closed).
# our_point=61.5°F, mainstream=66°F, Δ=4.5°F > 2°F tolerance → NOT_CLOSE.
# (Regression: previously SF demoted only because lat/lon=None → fail-closed
# before closeness check; this test pins that the closeness PATH fires.)
# ---------------------------------------------------------------------------


def test_sf_cold_bias_demoted_by_closeness_not_coord_gap():
    """SF demotes because closeness fails (Δ=4.5°F > 2°F), not because coords are missing."""
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _f_bins(
        [(62, 63), (64, 65)],
        open_low_label="61°F or below",
        open_high_label="66°F or higher",
    )
    traded_bin = bins[-1]  # "66°F or higher"
    verdict = evaluate_mainstream_agreement(
        city="San Francisco",
        target_date="2026-06-03",
        unit="F",
        our_point=61.5,     # our cold ECMWF
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_no",
        members=[60.0, 61.0, 61.5, 62.0, 61.0],
        mainstream_point=66.0,  # mainstream agrees with the warm reality
    )
    # Gate must have a real mainstream point (NOT fail-closed due to missing coords)
    assert verdict.mainstream_available is True, (
        "mainstream_point was provided; gate must evaluate closeness, not fail-closed"
    )
    assert verdict.mainstream_close is False  # 4.5°F > 2°F tolerance
    assert verdict.fail_reason == "MAINSTREAM_NOT_CLOSE"
    assert verdict.passed is False


# ---------------------------------------------------------------------------
# ANTIBODY TEST A — BUG-1: gate fires through adapter with real MarketAnalysis.
# Pre-fix: analysis._member_maxes/._unit/._precision were private → AttributeError
# swallowed by outer try/except → gate silently disabled (fail-open).
# Post-fix: public @property accessors on MarketAnalysis → verdicts populated.
# This test proves the gate ACTUALLY FIRES and does NOT fail-open.
# ---------------------------------------------------------------------------


def test_gate_fires_through_adapter_not_fail_open():
    """_evaluate_and_store_mainstream_agreement populates verdicts with real MarketAnalysis."""
    import numpy as np
    from types import SimpleNamespace
    from unittest.mock import patch
    from src.strategy.market_analysis import MarketAnalysis
    from src.types.market import Bin
    from src.engine.event_reactor_adapter import _evaluate_and_store_mainstream_agreement

    bins = [
        Bin(low=None, high=14, unit="C", label="14°C or below"),
        Bin(low=15, high=15, unit="C", label="15°C"),
        Bin(low=16, high=None, unit="C", label="16°C or higher"),
    ]
    members = np.array([15.0, 15.1, 14.9, 15.2, 15.0])
    p_raw = np.array([0.1, 0.7, 0.2])
    p_cal = np.array([0.1, 0.7, 0.2])

    analysis = MarketAnalysis(forecast_sharpness=ForecastSharpnessEvidence.exempt(unit="C"), 
        p_raw=p_raw, p_cal=p_cal, p_market=None,
        alpha=1.0, bins=bins, member_maxes=members,
        unit="C", precision=1.0,
    )

    event = SimpleNamespace(event_id="evt-antibody-001")
    candidate_stub = SimpleNamespace(condition_id="cond-antibody-1", bin=bins[1])
    family = SimpleNamespace(
        city="Wellington",
        target_date="2026-06-04",
        candidates=[candidate_stub],
    )
    payload: dict = {}

    mainstream_result = {
        "point": 15.8,
        "unit": "C",
        "source": "open_meteo_standard_forecast",
        "authority_tier": "mainstream",
        "fetched_at_utc": "2026-06-03T10:00:00+00:00",
        "latitude": -41.325,
        "longitude": 174.792,
        "target_date": "2026-06-04",
    }
    with patch("src.data.mainstream_forecast_source.fetch_mainstream_point", return_value=mainstream_result):
        _evaluate_and_store_mainstream_agreement(
            event=event,
            family=family,
            analysis=analysis,
            payload=payload,
        )

    # Gate must have fired — verdicts must be present (not silently swallowed).
    assert "_mainstream_agreement_verdicts" in payload, (
        "Gate did not fire — payload missing _mainstream_agreement_verdicts. "
        "AttributeError on analysis.member_maxes/.unit/.precision was swallowed "
        "by outer try/except (fail-open regression — BUG-1)."
    )
    verdicts = payload["_mainstream_agreement_verdicts"]
    assert len(verdicts) >= 1, "No verdicts stored — gate is fail-open"
    buy_yes_key = ("cond-antibody-1", "buy_yes")
    assert buy_yes_key in verdicts, (
        f"Expected buy_yes verdict for candidate, got keys: {list(verdicts.keys())}"
    )
    v = verdicts[buy_yes_key]
    assert v.get("mainstream_agreement_pass") is True, (
        f"Wellington 15°C buy_yes with mainstream 15.8°C must PASS. verdict={v}"
    )


# ---------------------------------------------------------------------------
# ANTIBODY TEST B — BUG-2: receipt_hash stable when gate NOT evaluated.
# Pre-fix: asdict(receipt) serialized null mainstream_* fields → different JSON
# → EdliReceiptHashDriftError on retry for pre-existing shadow receipts.
# Post-fix: _receipt_json strips None mainstream_* fields → hash byte-identical.
# ---------------------------------------------------------------------------


def test_receipt_hash_stable_when_gate_not_evaluated():
    """_receipt_json hash is byte-identical when all mainstream_* fields are None (gate OFF)."""
    import json
    import hashlib
    from src.events.reactor import EventSubmissionReceipt
    from src.events.no_submit_receipts import _receipt_json

    # Minimal pre-gate receipt (no mainstream_ fields at all).
    base_receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="evt-hash-test",
        causal_snapshot_id="snap-hash-test",
        city="Wellington",
        target_date="2026-06-04",
        metric="high",
        family_id="fam-1",
    )
    base_json = _receipt_json(base_receipt)
    base_hash = hashlib.sha256(base_json.encode("utf-8")).hexdigest()

    # Confirm mainstream fields are ABSENT from the JSON (not serialized as null).
    parsed = json.loads(base_json)
    for field in (
        "mainstream_agreement_pass", "mainstream_agreement_fail_reason",
        "mainstream_point", "mainstream_delta", "mainstream_bin_label",
        "mainstream_source", "mainstream_fetched_at_utc",
    ):
        assert field not in parsed, (
            f"Field {field!r} present as null in receipt_json — "
            "would cause hash drift vs pre-gate baseline (BUG-2 regression)."
        )

    # Post-gate receipt with all mainstream_* = None (flag OFF / not evaluated).
    gate_off_receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="evt-hash-test",
        causal_snapshot_id="snap-hash-test",
        city="Wellington",
        target_date="2026-06-04",
        metric="high",
        family_id="fam-1",
        mainstream_agreement_pass=None,
        mainstream_agreement_fail_reason=None,
        mainstream_point=None,
        mainstream_delta=None,
        mainstream_bin_label=None,
        mainstream_source=None,
        mainstream_fetched_at_utc=None,
    )
    gate_off_json = _receipt_json(gate_off_receipt)
    gate_off_hash = hashlib.sha256(gate_off_json.encode("utf-8")).hexdigest()

    assert base_json == gate_off_json, (
        "receipt_json differs when mainstream_* are None vs absent — "
        "EdliReceiptHashDrift regression on retry of pre-gate shadow receipt (BUG-2).\n"
        f"  base:     {base_json[:120]}\n"
        f"  gate_off: {gate_off_json[:120]}"
    )
    assert base_hash == gate_off_hash, "receipt_hash drifts when gate not evaluated (BUG-2)"

    # Sanity: gate-ON receipt has DIFFERENT hash (gate evaluation is detectable).
    gate_on_receipt = EventSubmissionReceipt(
        submitted=False,
        event_id="evt-hash-test",
        causal_snapshot_id="snap-hash-test",
        city="Wellington",
        target_date="2026-06-04",
        metric="high",
        family_id="fam-1",
        mainstream_agreement_pass=True,
        mainstream_agreement_fail_reason="PASS",
        mainstream_point=15.8,
        mainstream_delta=-0.6,
        mainstream_bin_label="15°C",
        mainstream_source="open_meteo_standard_forecast",
        mainstream_fetched_at_utc="2026-06-03T10:00:00+00:00",
    )
    gate_on_json = _receipt_json(gate_on_receipt)
    assert gate_on_json != base_json, "Gate-ON receipt must have different hash (sanity check)"

# ---------------------------------------------------------------------------
# ANTIBODY TEST C — BUG-3: verdict must survive the producer→consumer boundary.
# THE LIVE FAILURE (2026-06-03 shadow): ANTIBODY A proves the eval populates a
# dict you HAND it — but the live producer (`_canonical_probability_and_fdr_proof`)
# stored the verdict into `_payload(event)`, which RE-PARSES event.payload_json
# into a FRESH dict every call (src .../event_reactor_adapter.py:_payload). The
# consumer (`build_event_bound_no_submit_receipt` @605 → @730/@1085) reads a
# DIFFERENT `_payload(event)` instance. So the verdict evaporated at the A→B
# boundary: 0 receipts tagged, 0 gate logs, gate silently fail-OPEN despite
# flag=True. ANTIBODY A stayed GREEN because it tests the eval in isolation with
# its own dict — it never crosses the boundary. (Fitz constraint #2: the function
# worked; the RELATIONSHIP across the module boundary lost the semantic.)
#
# The structural antibody: the verdict must travel via the CALLER's threaded
# payload, never a fresh `_payload(event)` re-parse. This makes the
# instance-divergence category unconstructable.
# ---------------------------------------------------------------------------

def test_gate_verdict_survives_producer_to_consumer_payload_boundary():
    """The proof builder must thread the caller's payload to the gate store +
    read sites — never re-parse `_payload(event)` for the verdict (instance
    divergence = silent fail-open, the live 2026-06-03 bug)."""
    import inspect
    from src.engine import event_reactor_adapter as era

    # (1) Signature contract: the canonical proof builder MUST accept the caller's
    # payload, so the verdict it stores reaches the receipt builder's dict.
    sig = inspect.signature(era._canonical_probability_and_fdr_proof)
    assert "payload" in sig.parameters, (
        "_canonical_probability_and_fdr_proof must accept the caller's `payload` so "
        "the mainstream-gate verdict lands in the SAME dict the receipt builder "
        "reads — not a throwaway _payload(event) re-parse (BUG-3 silent fail-open)."
    )

    # (2) The gate STORE site must use the threaded payload, never _payload(event).
    canon_src = inspect.getsource(era._canonical_probability_and_fdr_proof)
    assert "_evaluate_and_store_mainstream_agreement" in canon_src, (
        "gate eval must be invoked from the canonical proof builder"
    )
    after_call = canon_src.split("_evaluate_and_store_mainstream_agreement", 1)[1][:240]
    assert "payload=payload" in after_call, (
        "gate eval must be passed the THREADED payload (payload=payload)"
    )
    assert "_payload(event)" not in after_call, (
        "gate eval must NOT store into a fresh _payload(event) re-parse — that "
        "fresh dict is discarded and the receipt builder never sees the verdict."
    )

    # (3) The per-candidate proof-attach must READ the threaded payload, not a
    # fresh _payload(event) (instance C in the live triage).
    gen_src = inspect.getsource(era._generate_candidate_proofs)
    attach = gen_src.split("mainstream_agreement=", 1)[1][:160]
    assert "_payload(event)" not in attach, (
        "proof-attach must read the threaded `payload` for the verdict, not a "
        "fresh _payload(event) — different instance has no verdict (fail-open)."
    )

    # (4) Threading contract: every _canonical call site in _live_yes_probabilities
    # must forward the payload.
    lyp_src = inspect.getsource(era._live_yes_probabilities)
    canon_calls = lyp_src.count("_canonical_probability_and_fdr_proof(")
    canon_calls_with_payload = lyp_src.count("payload=payload")
    assert canon_calls >= 1 and canon_calls_with_payload >= canon_calls, (
        "_live_yes_probabilities must pass payload=payload to every "
        f"_canonical_probability_and_fdr_proof call (calls={canon_calls}, "
        f"with-payload={canon_calls_with_payload})"
    )

# ---------------------------------------------------------------------------
# ANTIBODY TEST D — BUG-4 (RESOLVED): agreement manufactured by a large bias
# correction — the raw forecast would NOT agree, the corrected one does.
# THE LIVE FINDING (2026-06-03 shadow): the #1 candidate Tel Aviv 06-04 buy_no
# 32°C passed the gate with our=29.9 vs mainstream=29.5 (Δ0.4). The RAW ECMWF
# ensemble mean was 25.9°C — a +4.0°C bias correction pulled the forecast to
# ~mainstream. The 2026-06-03 grid-to-point investigation proved this correction
# is OOS-validated legitimate (raw is the biased number; 29.9 is the better
# estimate). Therefore the gate is REFERENCE-ONLY: correction dependence is
# RECORDED as provenance (agreement_correction_dependent=True) but does NOT
# demote. The test below asserts provenance-only semantics, NOT demotion.
# ---------------------------------------------------------------------------

def test_agreement_via_large_bias_correction_is_recorded_not_demoted():
    """Live Tel Aviv 06-04: raw 25.9°C, +4°C bias → 29.9°C, mainstream 29.5°C.
    The correction-dependence is RECORDED (raw disagrees, corrected agrees) as
    provenance, but does NOT demote: the 2026-06-03 grid-to-point investigation
    proved the +4°C correction is OOS-validated legitimate (raw is the biased
    number; 29.9 is the better estimate we trade). Demoting Tel Aviv for carrying
    a validated correction would penalise the CORRECT forecast — so passed reflects
    that our traded forecast (29.9) genuinely agrees with mainstream + correct
    direction. (Reference-only either way — never gates production.)"""
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [28, 29, 30, 31, 32, 33, 34],
        open_low_label="27°C or below",
        open_high_label="35°C or higher",
    )
    traded = next(b for b in bins if b.label == "32°C")  # far-OTM buy_no
    v = evaluate_mainstream_agreement(
        city="Tel Aviv",
        target_date="2026-06-04",
        unit="C",
        our_point=29.9,        # bias-corrected forecast (the one we trade)
        raw_our_point=25.9,    # raw ECMWF ensemble mean (the biased number)
        bins=bins,
        traded_bin=traded,
        direction="buy_no",
        members=[29.6, 29.8, 29.9, 30.0, 30.2],  # corrected members → modal 30
        mainstream_point=29.5,
    )
    # Provenance recorded (informational): the agreement relies on the correction.
    assert v.mainstream_close is True            # corrected IS close to mainstream
    assert v.agrees_on_raw is False              # raw is NOT close (3.6 > 1.5 tol)
    assert v.agreement_correction_dependent is True
    assert v.bias_applied == pytest.approx(4.0)
    # But NOT demoted — the traded forecast agrees with mainstream + correct dir.
    assert v.passed is True
    assert v.fail_reason == "PASS"

def test_agreement_with_small_correction_raw_also_agrees_passes():
    """Wuhan-like: raw 32.0°C, corrected 31.6°C, mainstream 32.0°C. Both raw and
    corrected agree with mainstream → NOT correction-dependent → passes (the
    correction is not what created the agreement)."""
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [30, 31, 32, 33, 34, 35, 36],
        open_low_label="29°C or below",
        open_high_label="37°C or higher",
    )
    traded = next(b for b in bins if b.label == "34°C")  # buy_no on far bin
    v = evaluate_mainstream_agreement(
        city="Wuhan",
        target_date="2026-06-04",
        unit="C",
        our_point=31.6,
        raw_our_point=32.0,    # raw ALSO close to mainstream
        bins=bins,
        traded_bin=traded,
        direction="buy_no",
        members=[31.4, 31.6, 31.7, 31.9, 32.1],
        mainstream_point=32.0,
    )
    assert v.agrees_on_raw is True
    assert v.agreement_correction_dependent is False
    assert v.passed is True

def test_raw_our_point_absent_skips_correction_check_backward_compatible():
    """When raw_our_point is not supplied (legacy callers), the correction check is
    skipped — backward compatible, never spuriously demotes."""
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded = next(b for b in bins if b.label == "15°C")
    v = evaluate_mainstream_agreement(
        city="Wellington",
        target_date="2026-06-04",
        unit="C",
        our_point=15.2,
        bins=bins,
        traded_bin=traded,
        direction="buy_yes",
        members=[15.0, 15.1, 15.2, 15.3, 15.0],
        mainstream_point=15.8,
    )
    assert v.agreement_correction_dependent is False
    assert v.agrees_on_raw is None
    assert v.passed is True


# ---------------------------------------------------------------------------
# REFERENCE-ONLY CONTRACT (operator directive 2026-06-03).
# The mainstream-agreement gate is a REFERENCE for the ARM decision — it
# annotates the receipt so the operator can see whether the forecast's top
# candidate agrees with an independent mainstream. It takes NO part in
# production selection: production trades on the forecast (trade_score/q_lcb).
# A gate verdict (pass/fail, incl. the #135-B bias-correction-dependent flag)
# can NEVER exclude a candidate from selection. "We trade on the forecast; the
# only reason we are in shadow is the candidates don't reflect real Polymarket
# trades — not the mainstream gate." This relationship test crosses the
# verdict→selection boundary that prose cannot guarantee.
# ---------------------------------------------------------------------------
def test_mainstream_gate_is_reference_only_never_excludes_from_selection(monkeypatch):
    """A candidate whose gate verdict FAILED must STILL be selected when it is
    the forecast's best by (trade_score, q_lcb). Pre-fix RED: _gate_eligible
    dropped the failed proof, so the selector returned the lower-trade_score
    proof instead of the forecast's true pick."""
    from types import SimpleNamespace
    from src.config import settings
    from src.engine.event_reactor_adapter import _selected_candidate_proof

    # Gate flag ON — under the OLD (excluding) contract this is exactly the
    # condition that dropped the failed proof. Reference-only must ignore it.
    monkeypatch.setitem(settings["edli_v1"], "mainstream_agreement_reference_enabled", True)

    best_but_gate_failed = SimpleNamespace(
        token_id="tok-best",
        candidate=SimpleNamespace(condition_id="cond-best"),
        execution_price=object(),
        q_lcb_5pct=0.40,
        trade_score=0.20,
        mainstream_agreement={
            "mainstream_agreement_pass": False,
            "mainstream_agreement_fail_reason": "MAINSTREAM_NOT_CLOSE",
        },
    )
    worse_gate_passed = SimpleNamespace(
        token_id="tok-worse",
        candidate=SimpleNamespace(condition_id="cond-worse"),
        execution_price=object(),
        q_lcb_5pct=0.30,
        trade_score=0.05,
        mainstream_agreement={
            "mainstream_agreement_pass": True,
            "mainstream_agreement_fail_reason": "PASS",
        },
    )

    selected = _selected_candidate_proof({}, (best_but_gate_failed, worse_gate_passed))
    assert selected is best_but_gate_failed, (
        "reference-only gate must NOT exclude the forecast's best candidate; "
        "production selection trades on trade_score/q_lcb, not the gate verdict"
    )
    # The failed verdict is still CARRIED on the selected proof — recorded on the
    # receipt as the ARM-decision reference annotation, not used to gate.
    assert selected.mainstream_agreement["mainstream_agreement_pass"] is False
