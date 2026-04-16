"""P9 regression test: model_agreement affects alpha correctly."""
import pytest
from src.strategy.market_fusion import compute_alpha, TemperatureDelta

_COMMON = dict(
    calibration_level=2,
    ensemble_spread=TemperatureDelta(3.0, "C"),
    lead_days=3.0,
    hours_since_open=10.0,
    city_name="test",
    season="summer",
    authority_verified=True,
)

@pytest.mark.parametrize("agreement,expected_penalty", [
    ("AGREE", 0.0),
    ("NOT_CHECKED", 0.0),
    ("SOFT_DISAGREE", -0.10),
    ("CONFLICT", -0.20),
])
def test_model_agreement_alpha_penalty(agreement, expected_penalty):
    baseline = compute_alpha(**_COMMON, model_agreement="AGREE")
    result = compute_alpha(**_COMMON, model_agreement=agreement)
    assert abs(result.value - (baseline.value + expected_penalty)) < 1e-9, \
        f"{agreement}: expected penalty {expected_penalty}, got {result.value - baseline.value}"
