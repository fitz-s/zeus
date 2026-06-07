from dataclasses import replace

from tests.test_replacement_forecast_materializer import _conn, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_shadow


def test_materialization_blocks_om9_anchor_without_full_local_day_hourly_coverage() -> None:
    request = _request()
    anchor = replace(
        request.openmeteo_anchor,
        sample_count=23,
        contributing_local_times=request.openmeteo_anchor.contributing_local_times[:-1],
        contributing_valid_times_utc=request.openmeteo_anchor.contributing_valid_times_utc[:-1],
    )

    result = materialize_replacement_forecast_shadow(_conn(), replace(request, openmeteo_anchor=anchor))

    assert result.ok is False
    assert "REPLACEMENT_MATERIALIZATION_OM9_LOCALDAY_HOURLY_COVERAGE_INCOMPLETE" in result.reason_codes
