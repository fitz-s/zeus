# Created: (pre-existing)
# Last reused or audited: 2026-06-09
# Authority basis: STALE_LAW re-pin. The dict-input `_settlement_step_c` helper
#   in replacement_forecast_materialization_request_builder was removed; the
#   request builder now reads settlement_step_c directly from its payload
#   (request_builder.py:212). The Fahrenheit->5/9 °C settlement-step INVARIANT
#   survives in src/data/replacement_forecast_materialization_seed_builder.py
#   as `_settlement_step_c(unit: str)` (seed_builder.py:75). Re-pinned to the
#   current unit-string signature; the dead `temperature_unit: "fahrenheit"`
#   dict key is gone (unit now flows as uppercase "F"/"C" from city_config).
from src.data.replacement_forecast_materialization_seed_builder import _settlement_step_c


def test_fahrenheit_replacement_seed_uses_five_ninths_celsius_settlement_step() -> None:
    assert _settlement_step_c("F") == 5.0 / 9.0
    assert _settlement_step_c("f") == 5.0 / 9.0


def test_celsius_replacement_seed_uses_unit_settlement_step() -> None:
    assert _settlement_step_c("C") == 1.0
    assert _settlement_step_c("c") == 1.0
