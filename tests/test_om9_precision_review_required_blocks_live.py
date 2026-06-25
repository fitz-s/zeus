from tests.test_replacement_forecast_materializer import _conn, _precision_guard, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live


def test_om9_precision_review_required_blocks_live_materialization() -> None:
    guard = _precision_guard(city_class="coastal", land_sea_mask="sea")

    result = materialize_replacement_forecast_live(_conn(), _request(openmeteo_precision_guard=guard))

    assert guard.status == "REVIEW_REQUIRED"
    assert result.status == "BLOCKED"
    assert "OM9_PRECISION_GUARD_NOT_LIVE_PASS" in result.reason_codes
