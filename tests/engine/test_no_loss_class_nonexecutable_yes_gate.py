# Created: 2026-06-13
# Last reused/audited: 2026-06-13
# Authority basis: settled-data loss-class replay 2026-06-13, n=485, 98.1% admit; git
#   regression 745aa10c6f. The canonical builder's NON-executable-YES else-branch must
#   emit a NON-actionable buy_no (q_lcb_no=0.0 / p_value=1.0 / prefilter=False). Commit
#   745aa10c6f (2026-06-12 22:18Z) replaced that gate with a forecast-derived q_lcb_no
#   (analysis.forecast_yes_probability_samples -> _side_q_lcb_from_yes_samples ->
#   _native_no_edge_positivity), re-opening the far favorite-longshot NO harvest that the
#   settled record proves is the real-capital loss class (HK/Karachi/KL "NO on winning
#   ring bin"): of 485 settled winning bins, the forecast NO lower bound on the bin that
#   ACTUALLY WON was > 0.5 on 476/485 (98.1%) = a guaranteed loss.
"""RED-on-revert antibody for the buy-NO loss-class gate in
``_canonical_probability_and_fdr_proof``.

THE RELATIONSHIP (cross-branch invariant on the canonical probability builder):

  * EXECUTABLE-YES bin  -> the buy_no leg is reconciled via the native NO authority
    (``_native_no_edge_positivity`` over q_lcb_no = 1 - q_ucb_yes against the bin's OWN
    native NO cost). This is the mid-price, mainstream-agreed NO that produced Zeus's
    GOOD fills (HK 30C NO, Karachi 37C NO, 0.2-0.6 band). UNCHANGED by this gate.

  * NON-executable-YES bin (scan_full_hypothesis_family could not score it -> a FAR bin
    off the forecast center) -> the buy_no leg is NON-actionable: q_lcb_no = 0.0,
    p_value = 1.0, prefilter = False. A forecast-derived q_lcb_no here re-opens the loss
    class. This is the profitable-era gate.

The test drives the REAL ``_canonical_probability_and_fdr_proof`` with the heavy DB/
calibration seams monkeypatched to synthetic inputs, so it exercises the actual
branch-SELECTION code (the ``if yes_executable: ... else: ...`` split), not just a
helper. RED-on-revert: restoring the 745aa10c6f forecast-NO else-branch makes
q_lcb_no(far bin) > 0 and prefilter possibly True -> the assertions below fail.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

import src.engine.event_reactor_adapter as era
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis


# --- synthetic family ------------------------------------------------------------------
# Two bins. Bin 0 is the executable-YES bin (forecast favors it). Bin 1 is the FAR bin
# whose YES side has no executable market and whose forecast UNDER-rates the eventual
# winner (q_yes_pt low) — exactly the 745aa10c6f loss-class shape (winner is a
# forecast-underrated far bin; the favorite-longshot NO band sits at ~0.79).

_EXEC_COND = "cond-exec-bin0"
_FAR_COND = "cond-far-bin1"

# Forecast YES point per bin: bin0 favored (0.62), bin1 a forecast-underrated far bin
# (0.18 -> NO point 0.82, the favorite-longshot band the loss class harvests).
_P_POSTERIOR = np.array([0.62, 0.18], dtype=float)
_P_CAL = np.array([0.60, 0.20], dtype=float)

# YES probability bootstrap samples per bin (only the executable bin's are consumed; the
# far bin must NOT reach a sample-consuming path under the gate). Bin1's samples are
# provided anyway to prove the gate does not consume them.
_YES_SAMPLES = {
    0: np.full(64, 0.62, dtype=float),
    1: np.full(64, 0.18, dtype=float),  # would yield q_lcb_no ~ 0.82 if (wrongly) used
}


def _make_candidate(condition_id: str, label: str):
    return SimpleNamespace(condition_id=condition_id, bin=SimpleNamespace(label=label))


def _make_family():
    return SimpleNamespace(
        city="TestCity",
        target_date="2026-06-13",
        metric="high",
        candidates=[
            _make_candidate(_EXEC_COND, "30-31C"),
            _make_candidate(_FAR_COND, "44-45C"),  # far bin, no executable YES market
        ],
    )


class _FakeAnalysis:
    """Minimal MarketAnalysis surface consumed by _canonical_probability_and_fdr_proof:
    p_posterior, p_cal, and bin_yes_probability_samples(index, n)."""

    def __init__(self) -> None:
        self.p_posterior = _P_POSTERIOR
        self.p_cal = _P_CAL

    def bin_yes_probability_samples(self, index: int, n: int) -> np.ndarray:
        return _YES_SAMPLES[index]


def _exec_yes_only_hypotheses(analysis, n_bootstrap):  # noqa: ANN001
    """scan_full_hypothesis_family stand-in: emit a scored buy_yes hypothesis ONLY for the
    executable bin (label '30-31C'). The far bin ('44-45C') gets NO hypothesis at all,
    which is precisely what makes ``yes_executable`` False for it in the builder.

    Mirrors the real scan: it emits ONLY buy_yes hypotheses (the buy_no loop body is a
    bare ``continue``), so the NO leg is reconciled by the native-NO seam, never by a NO
    hypothesis."""
    return [
        FullFamilyHypothesis(
            index=0,
            range_label="30-31C",
            direction="buy_yes",
            edge=0.05,
            ci_lower=0.02,
            ci_upper=0.10,
            p_value=0.001,
            p_model=0.62,
            p_market=0.60,
            p_posterior=0.62,
            entry_price=0.60,
            is_shoulder=False,
            passed_prefilter=True,
        )
    ]


def _native_costs():
    """Native cost ladder. The EXECUTABLE bin (bin0) carries a native YES ask AND a native
    NO ask cheap enough that its native NO edge is positive (q_lcb_no clears the NO cost)
    -> proves the executable buy_no path is UNCHANGED (reconciled via native NO edge).
    The FAR bin (bin1) carries a native NO ask too (a NO book can exist with no YES book);
    under the gate it must STILL be non-actionable because its YES side is non-executable.
    """
    exec_yes = SimpleNamespace(value=0.60)
    exec_no = SimpleNamespace(value=0.30)  # bin0 NO cost; q_lcb_no(bin0)~0.38 > 0.30 -> edge
    far_no = SimpleNamespace(value=0.20)   # bin1 NO cost cheap; a forecast-NO (~0.82) would admit
    return {
        (_EXEC_COND, "buy_yes"): (None, exec_yes, 0.0, None, None),
        (_EXEC_COND, "buy_no"): (None, exec_no, 0.0, None, None),
        (_FAR_COND, "buy_no"): (None, far_no, 0.0, None, None),
        # NOTE: no (_FAR_COND, "buy_yes") entry -> the far YES side is non-executable.
    }


@pytest.fixture
def _patched_canonical_seams(monkeypatch):
    """Patch the heavy DB/calibration seams so the canonical builder runs on synthetic
    inputs while its real branch-selection logic executes unchanged."""
    fake_analysis = _FakeAnalysis()

    monkeypatch.setattr(
        era,
        "_forecast_snapshot_row_for_event",
        lambda *a, **k: {"snapshot_id": "synthetic-1"},
    )
    monkeypatch.setattr(
        era,
        "_market_analysis_from_event_snapshot",
        lambda *a, **k: fake_analysis,
    )
    monkeypatch.setattr(
        "src.strategy.market_analysis_family_scan.scan_full_hypothesis_family",
        _exec_yes_only_hypotheses,
    )
    # The post-loop EMOS-CI override and settlement-coverage shrink are flag-gated and
    # default OFF (pure no-op), so they are left real — exercising the genuine code path.
    return fake_analysis


def _run_builder():
    event = SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY", payload_json="{}")
    q_by_condition, lcb_by_direction, p_values, prefilter, evidence = (
        era._canonical_probability_and_fdr_proof(
            event=event,
            payload={},
            family=_make_family(),
            conn=None,
            calibration_conn=None,
            native_costs=_native_costs(),
            decision_time=datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc),
        )
    )
    return q_by_condition, lcb_by_direction, p_values, prefilter, evidence


def test_nonexecutable_yes_bin_buy_no_is_nonactionable(_patched_canonical_seams):
    """THE loss-class gate: the FAR bin (non-executable YES, forecast under-rates the
    eventual winner) must emit a NON-actionable buy_no — q_lcb_no=0.0, p_value=1.0,
    prefilter=False — even though a forecast-derived q_lcb_no (~0.82) would clear the
    cheap NO cost (0.20). RED-on-revert: 745aa10c6f makes q_lcb_no>0 here."""
    _q, lcb_by_direction, p_values, prefilter, _e = _run_builder()

    far_no_lcb = float(lcb_by_direction[(_FAR_COND, "buy_no")].q_lcb)
    assert far_no_lcb == 0.0, (
        f"non-executable-YES far bin buy_no q_lcb_no must be 0.0 (loss-class gate), "
        f"got {far_no_lcb} — the 745aa10c6f forecast-NO else-branch was restored"
    )
    assert p_values[(_FAR_COND, "buy_no")] == 1.0
    assert prefilter[(_FAR_COND, "buy_no")] is False


def test_nonexecutable_yes_bin_buy_yes_is_also_nonactionable(_patched_canonical_seams):
    """The buy_yes leg on the non-executable-YES bin is non-actionable too (no executable
    YES market): p_value=1.0, prefilter=False. q_lcb_yes carries the honest forecast
    point (not zeroed) but the leg is rejected downstream by the missing YES price."""
    _q, _lcb, p_values, prefilter, _e = _run_builder()
    assert p_values[(_FAR_COND, "buy_yes")] == 1.0
    assert prefilter[(_FAR_COND, "buy_yes")] is False


def test_executable_yes_bin_buy_no_reconciled_via_native_no_edge(_patched_canonical_seams):
    """UNCHANGED GOOD-fill path: the EXECUTABLE-YES bin's buy_no is reconciled via the
    native NO authority (q_lcb_no = 1 - q_ucb_yes vs its OWN native NO cost). Here the
    native NO bound clears the cheap NO cost (0.30) -> admissible (p=0.0, prefilter True),
    and q_lcb_no is a POSITIVE native bound (NOT the zeroed gate). This proves the gate
    touches ONLY the non-executable-YES branch."""
    _q, lcb_by_direction, p_values, prefilter, _e = _run_builder()

    exec_no_lcb = float(lcb_by_direction[(_EXEC_COND, "buy_no")].q_lcb)
    assert exec_no_lcb > 0.0, (
        "executable-YES bin buy_no must carry the POSITIVE native NO bound "
        "(1 - q_ucb_yes), not the zeroed loss-class gate"
    )
    # native NO bound (~0.38 for a 0.62-favored YES) clears the 0.30 NO cost -> edge.
    assert p_values[(_EXEC_COND, "buy_no")] == 0.0
    assert prefilter[(_EXEC_COND, "buy_no")] is True


def test_executable_yes_bin_buy_yes_unchanged(_patched_canonical_seams):
    """The executable-YES bin's buy_yes leg reads its scored hypothesis verbatim
    (p_value from the hypothesis, prefilter from passed_prefilter) — the FDR edge engine
    is untouched by the loss-class gate."""
    _q, _lcb, p_values, prefilter, _e = _run_builder()
    assert p_values[(_EXEC_COND, "buy_yes")] == pytest.approx(0.001)
    assert prefilter[(_EXEC_COND, "buy_yes")] is True
