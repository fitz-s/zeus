"""P9 regression guard: model_agreement must NOT shift alpha via a hardcoded offset.

The former per-agreement penalty (SOFT_DISAGREE -0.10 / CONFLICT -0.20) was a fixed constant stapled
onto a continuous blending weight — the forbidden "fixed offset on a continuously-varying value"
pattern (a static constant cannot correct a varying quantity). alpha now serves the calibration-level
base unchanged; model_agreement must not move it. (alpha is also dormant end-to-end: its value is
discarded by the MODEL_ONLY posterior on every live path.)
"""
import pytest

from src.strategy.market_fusion import TemperatureDelta, compute_alpha

_COMMON = dict(
    calibration_level=2,
    ensemble_spread=TemperatureDelta(3.0, "C"),
    lead_days=3.0,
    hours_since_open=10.0,
    city_name="test",
    season="summer",
    authority_verified=True,
)


@pytest.mark.parametrize("agreement", ["AGREE", "NOT_CHECKED", "SOFT_DISAGREE", "CONFLICT"])
def test_model_agreement_does_not_offset_alpha(agreement):
    baseline = compute_alpha(**_COMMON, model_agreement="AGREE")
    result = compute_alpha(**_COMMON, model_agreement=agreement)
    assert result.value == baseline.value, (
        f"{agreement}: model_agreement must not shift alpha (no hardcoded offset), "
        f"got delta {result.value - baseline.value}"
    )
