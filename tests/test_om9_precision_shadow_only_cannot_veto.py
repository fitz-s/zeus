from tests.test_replacement_forecast_materializer import _conn, _precision_guard, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_shadow


def test_om9_precision_shadow_only_blocks_materialized_veto_path() -> None:
    guard = _precision_guard(city_class="coastal", land_sea_mask="sea")

    result = materialize_replacement_forecast_shadow(_conn(), _request(openmeteo_precision_guard=guard))

    assert guard.status == "SHADOW_ONLY"
    assert result.status == "BLOCKED"
    assert "OM9_PRECISION_GUARD_BLOCKED_MATERIALIZATION" in result.reason_codes
