# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md
#   §3 (make build_center RAW for the strategy: a case with nonzero historical EB shift still
#   returns zero shift + raw-member-envelope center) + §7 (provenance fail-closed: reject a
#   live-eligible distribution whose debias_shift != 0).
"""RED-on-revert tests for the RAW center + RAW provenance fail-closed gate.

  * ``test_raw_noop_authority_produces_zero_shift_center`` — with the spine's
    ``_NoOpDebiasAuthority`` (the live spine authority) the served center is the RAW
    weighted-Huber consensus of the raw members and the de-bias shift is EXACTLY 0, even
    when the members carry a clear historical location bias. Reverting the spine to a real
    forward de-bias authority makes the shift non-zero -> RED.

  * ``test_live_pd_with_nonzero_debias_shift_is_rejected`` — a live-eligible
    PredictiveDistribution whose ``debias.aggregate_shift_native != 0`` is REFUSED
    (``live_eligible=False`` + ``RAW_LAW_VIOLATION_DEBIAS_SHIFT_NONZERO``). This is the §7
    structural antibody: a forbidden forward de-bias is unconstructable on the live path.
    Removing the gate lets the de-biased PD serve -> RED.
"""
from __future__ import annotations

import numpy as np

from src.engine.qkernel_spine_bridge import _NoOpDebiasAuthority
from src.forecast.center import build_center
from src.forecast.debias_authority import AppliedDebias
from src.forecast.predictive_distribution_builder import PredictiveDistributionBuilder

# Reuse the Tokyo PD fixtures.
from tests.forecast.test_single_predictive_distribution_authority import (
    _case,
    _model_set,
    _no_emos,
    _no_obs,
    _pin_realized_floor,
)


class _NonZeroShiftAuthority:
    """A DebiasAuthority-shaped fake that applies a constant FORWARD shift (forbidden).

    Used ONLY to prove the §7 fail-closed gate rejects a de-biased live distribution. It
    shifts every member by ``+shift`` and reports a non-zero ``aggregate_shift_native``.
    """

    def __init__(self, shift: float) -> None:
        self._shift = float(shift)

    def apply(self, case, models):
        vals = np.asarray(models.member_values_native, dtype=float) + self._shift
        n = int(vals.size)
        applied = AppliedDebias(
            artifact_ids=("fake_forward_debias",),
            per_member_shift_native=tuple(self._shift for _ in range(n)),
            aggregate_shift_native=self._shift,
            trailing_residual_mean_native=self._shift,
            trailing_residual_std_native=0.4,
            activation_status="APPLIED",
            reason="fake forward de-bias (forbidden under RAW law)",
        )
        return vals, applied


def test_raw_noop_authority_produces_zero_shift_center():
    # Members with a clear historical location bias (all hot, ~30°C). Under the RAW NoOp
    # authority the center is the raw weighted-Huber consensus — zero shift — NOT pulled
    # toward any settlement-residual correction.
    models = _model_set([29.0, 30.0, 31.0, 30.5])
    center = build_center(_case(), models, _NoOpDebiasAuthority(), use_emos=False)

    # The de-bias shift is EXACTLY zero (RAW: raw == debiased).
    assert center.debiased_consensus_native == center.raw_consensus_native, (
        "debiased consensus != raw consensus — a non-zero de-bias shift leaked into the RAW center"
    )
    # The served center sits inside the raw member envelope (no forward correction moved it out).
    assert 29.0 <= center.mu_native <= 31.0
    assert center.center_method in ("WEIGHTED_HUBER_CONSENSUS", "SHRUNK_EMOS", "RAW_FALLBACK")
    # The debiased member min/max equal the raw member min/max (zero shift).
    assert center.debiased_member_min_native == 29.0
    assert center.debiased_member_max_native == 31.0


def test_live_pd_with_nonzero_debias_shift_is_rejected(monkeypatch):
    # A non-day0 case with a fitted realized floor so the σ authority is live-eligible.
    _no_emos(monkeypatch)
    _pin_realized_floor(monkeypatch, 2.2)
    case = _case(lead_hours=48.0)  # non-day0
    models = _model_set([20.0, 21.0, 22.0, 23.0], case=case)

    # RAW (NoOp) authority -> live-eligible, zero shift.
    raw_builder = PredictiveDistributionBuilder(_NoOpDebiasAuthority())
    raw_pd = raw_builder.build(case, models, _no_obs(), use_emos=False)
    assert raw_pd.live_eligible is True
    assert raw_pd.debias.aggregate_shift_native == 0.0
    assert raw_pd.ineligibility_reason is None

    # Forward de-bias authority (shift +2.0) -> the §7 gate must REFUSE the live distribution.
    biased_builder = PredictiveDistributionBuilder(_NonZeroShiftAuthority(2.0))
    biased_pd = biased_builder.build(case, models, _no_obs(), use_emos=False)
    assert biased_pd.live_eligible is False, (
        "a live-eligible PD carried a non-zero forward de-bias shift — the RAW §7 fail-closed "
        "gate was removed"
    )
    assert biased_pd.ineligibility_reason is not None
    assert "RAW_LAW_VIOLATION_DEBIAS_SHIFT_NONZERO" in biased_pd.ineligibility_reason
