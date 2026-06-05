# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 HARDEN-2 (adversarial re-review finding). The MAJOR-1
#   selection-byte-identity fix (use _qlcb_raw_float instead of _qlcb_float at
#   event_reactor_adapter.py:3135-3136) was proven correct but the existing test
#   only used a _FakeProof — it did NOT exercise the real _generate_candidate_proofs
#   -> _selected_candidate_proof path. A future refactor reverting :3136 back to
#   _qlcb_float would NOT be caught by the FakeProof test. This file adds an
#   INTEGRATION test that drives the real path with a controlled QlcbByDirection
#   (two deep-OTM bins, distinct negative raw q_lcb values -0.05/-0.02) and
#   asserts the selected proof's token_id matches the legacy raw-negative ordering.
#
#   ANTIBODY PROOF (required before merging):
#   - With _qlcb_raw_float (the fix): selected = tok-B-yes (raw -0.02 > -0.05). GREEN.
#   - With _qlcb_float (the revert): both bins clamp to 0.0, max() picks first =
#     tok-A-yes. RED. The test fails on the revert, protecting the fix.
"""K3 selection byte-identity — INTEGRATION test on the real adapter path.

RELATIONSHIP under test:
  _generate_candidate_proofs (event_reactor_adapter) extracts q_lcb via
  _qlcb_raw_float at line 3135-3136 and stores it as _CandidateProof.q_lcb_5pct.
  _selected_candidate_proof then ranks non-executable proofs by max(q_lcb_5pct).
  When two deep-OTM bins have distinct negative raw q_lcb values (-0.05, -0.02),
  the selected proof must be the one with the LESS-negative (higher) raw value —
  byte-identical to legacy (plain-float carrier, no clamp).

  If _qlcb_float (clamped, returns 0.0 for both) were used instead, the selection
  key collapses to a tie and max() picks the first proof (tok-A-yes) — a different
  bin than legacy (tok-B-yes). That drift touches the no-submit receipt substrate
  the measurement spine grades.
"""
from __future__ import annotations

import types
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.calibration.qlcb_provenance import (
    QlcbByDirection,
    QlcbProvenance,
    _qlcb_float,
)
from src.events.candidate_binding import MarketTopologyCandidate
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Shared fixture: two deep-OTM bins with distinct negative raw q_lcb values.
# Bin A: raw=-0.05 (more negative = lower in legacy ordering)
# Bin B: raw=-0.02 (less negative = higher in legacy ordering -> selected)
# ---------------------------------------------------------------------------

def _make_family_and_lcb():
    """Return (family, lcb_by_direction, mock_live_probs_return)."""
    # Fahrenheit shoulder + interior bins to satisfy Bin validation.
    b_A = Bin(low=None, high=32, label="32 or below", unit="F")
    b_B = Bin(low=33, high=34, label="33-34", unit="F")

    c_A = MarketTopologyCandidate(
        city="Chicago", target_date="2026-06-04", metric="high",
        condition_id="cond-A", yes_token_id="tok-A-yes", no_token_id="tok-A-no",
        bin=b_A,
    )
    c_B = MarketTopologyCandidate(
        city="Chicago", target_date="2026-06-04", metric="high",
        condition_id="cond-B", yes_token_id="tok-B-yes", no_token_id="tok-B-no",
        bin=b_B,
    )
    family = types.SimpleNamespace(candidates=(c_A, c_B))

    # Bin-A: raw q_lcb = -0.05 (deep-OTM; more negative)
    # Bin-B: raw q_lcb = -0.02 (deep-OTM; less negative -> legacy picks this bin)
    lcb = QlcbByDirection()
    lcb[("cond-A", "buy_yes")] = QlcbProvenance(q_lcb=-0.05, calibration_source="FORECAST_BOOTSTRAP")
    lcb[("cond-A", "buy_no")]  = QlcbProvenance(q_lcb=-0.05, calibration_source="FORECAST_BOOTSTRAP")
    lcb[("cond-B", "buy_yes")] = QlcbProvenance(q_lcb=-0.02, calibration_source="FORECAST_BOOTSTRAP")
    lcb[("cond-B", "buy_no")]  = QlcbProvenance(q_lcb=-0.02, calibration_source="FORECAST_BOOTSTRAP")

    mock_return = (
        {"cond-A": 0.05, "cond-B": 0.03},        # q_by_condition (deep-OTM posteriors)
        lcb,                                        # q_lcb_by_direction (QlcbByDirection)
        {("cond-A", "buy_yes"): 0.9, ("cond-A", "buy_no"): 0.9,
         ("cond-B", "buy_yes"): 0.9, ("cond-B", "buy_no"): 0.9},
        {},                                         # prefilter
        {"p_cal_vector_hash": "h_cal", "p_live_vector_hash": "h_live"},
    )
    return family, lcb, mock_return


def _run_real_path(mock_return, family, *, use_clamped_reader: bool = False):
    """Drive _generate_candidate_proofs -> _selected_candidate_proof with real code.

    snapshot_rows=[] forces execution_price=None for all proofs -> all non-executable
    -> selector falls to max(proofs, key=lambda p: p.q_lcb_5pct), exactly the path
    where clamping two distinct negatives to 0.0 would tie and flip the selection.

    When use_clamped_reader=True we patch _qlcb_raw_float -> _qlcb_float to simulate
    the pre-fix broken state (the revert that the antibody must catch).
    """
    from src.engine.event_reactor_adapter import (
        _generate_candidate_proofs,
        _selected_candidate_proof,
    )

    event = types.SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY")
    payload: dict = {}
    sentinel = object()
    decision_time = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)

    def _run(live_probs_mock, native_costs_mock):
        proofs = _generate_candidate_proofs(
            event=event,
            payload=payload,
            family=family,
            snapshot_rows=[],           # execution_price=None for all -> non-executable
            trade_conn=sentinel,        # type: ignore[arg-type]
            forecast_conn=sentinel,     # type: ignore[arg-type]
            calibration_conn=sentinel,  # type: ignore[arg-type]
            decision_time=decision_time,
        )
        return proofs

    with patch("src.engine.event_reactor_adapter._live_yes_probabilities",
               return_value=mock_return):
        with patch("src.engine.event_reactor_adapter._native_costs_by_candidate_direction",
                   return_value={}):
            if use_clamped_reader:
                # Simulate the revert: patch _qlcb_raw_float -> _qlcb_float so both
                # distinct-negative values clamp to 0.0, collapsing the selection key.
                with patch("src.calibration.qlcb_provenance._qlcb_raw_float",
                           side_effect=_qlcb_float):
                    proofs = _run(None, None)
            else:
                proofs = _run(None, None)

    return _selected_candidate_proof(payload, proofs), proofs


# ---------------------------------------------------------------------------
# GREEN test — the real path with _qlcb_raw_float selects the correct bin.
# ---------------------------------------------------------------------------

def test_real_path_selects_legacy_bin_using_raw_q_lcb():
    """INTEGRATION: _generate_candidate_proofs uses _qlcb_raw_float so the
    selected proof's q_lcb_5pct carries the RAW pre-clamp value. Two bins with
    distinct negative raw q_lcb (-0.05, -0.02) must produce the SAME selection
    as legacy plain-float ordering: tok-B-yes (raw -0.02 > -0.05 -> selected).

    This is the GREEN state — the fix is in place. If a refactor reverts
    event_reactor_adapter.py:3136 from _qlcb_raw_float to _qlcb_float this
    test goes RED (see test_broken_path_*  below for the RED proof)."""
    family, _, mock_return = _make_family_and_lcb()
    selected, proofs = _run_real_path(mock_return, family, use_clamped_reader=False)

    assert selected is not None, "selector returned None — all proofs were lost"

    # The q_lcb_5pct values on the proofs must be the raw (pre-clamp) values.
    q_values = {p.token_id: p.q_lcb_5pct for p in proofs}
    assert q_values["tok-A-yes"] == pytest.approx(-0.05), (
        f"bin-A raw q_lcb must be -0.05, got {q_values['tok-A-yes']!r}. "
        "If this is 0.0 the clamped value leaked into the selection path."
    )
    assert q_values["tok-B-yes"] == pytest.approx(-0.02), (
        f"bin-B raw q_lcb must be -0.02, got {q_values['tok-B-yes']!r}."
    )

    # Legacy plain-float ordering: -0.02 > -0.05 -> tok-B-yes is selected.
    assert selected.token_id == "tok-B-yes", (
        f"Expected legacy selection tok-B-yes (raw -0.02 > -0.05), "
        f"got {selected.token_id!r} with q_lcb_5pct={selected.q_lcb_5pct!r}. "
        "This means the raw q_lcb is NOT reaching the selection key — the fix "
        "at event_reactor_adapter.py:3136 (_qlcb_raw_float) may have been reverted."
    )


# ---------------------------------------------------------------------------
# RED proof — the BROKEN path (simulated revert) selects the WRONG bin.
# This test documents that the integration test IS a real antibody.
# ---------------------------------------------------------------------------

def test_broken_path_selects_wrong_bin_when_clamped_value_used():
    """ANTIBODY PROOF (RED-on-revert): when the extraction at adapter:3136 uses
    _qlcb_float (clamped, returns 0.0) instead of _qlcb_raw_float, both bins get
    q_lcb_5pct=0.0. The max() tiebreak on identical values picks the FIRST proof
    (tok-A-yes), not tok-B-yes. This is the wrong selection relative to legacy.

    The companion test test_real_path_selects_legacy_bin_using_raw_q_lcb must
    GREEN while this test proves what the broken state looks like. Together they
    confirm the integration test is a REAL antibody against the revert."""
    family, _, mock_return = _make_family_and_lcb()
    selected_broken, proofs_broken = _run_real_path(
        mock_return, family, use_clamped_reader=True
    )

    # All q_lcb_5pct values are 0.0 (clamped): -0.05 -> 0.0, -0.02 -> 0.0.
    q_values = {p.token_id: p.q_lcb_5pct for p in proofs_broken}
    assert q_values["tok-A-yes"] == pytest.approx(0.0), (
        "Expected clamped 0.0 for bin-A on the broken path"
    )
    assert q_values["tok-B-yes"] == pytest.approx(0.0), (
        "Expected clamped 0.0 for bin-B on the broken path"
    )

    # With all zeros, max() picks the first proof: tok-A-yes — the WRONG bin.
    # (tok-B-yes is the legacy-correct answer; tok-A-yes is the drift.)
    assert selected_broken is not None
    assert selected_broken.token_id == "tok-A-yes", (
        f"Expected broken path to pick tok-A-yes (clamped tie -> first element), "
        f"got {selected_broken.token_id!r}. If this fails the mock patch may not "
        "be intercepting the local import at adapter:3135 correctly."
    )
    # Confirm the broken selection DIFFERS from the correct (legacy) selection.
    assert selected_broken.token_id != "tok-B-yes", (
        "Broken and correct paths should select DIFFERENT bins — this documents "
        "that the integration test guards real selection-ordering behaviour."
    )
