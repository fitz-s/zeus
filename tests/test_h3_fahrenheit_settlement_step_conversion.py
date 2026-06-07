from src.data.replacement_forecast_materialization_request_builder import _settlement_step_c


def test_fahrenheit_replacement_seed_uses_five_ninths_celsius_settlement_step() -> None:
    assert _settlement_step_c({"settlement_unit": "F"}) == 5.0 / 9.0
    assert _settlement_step_c({"temperature_unit": "fahrenheit"}) == 5.0 / 9.0
