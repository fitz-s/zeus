from dataclasses import replace

from tests.test_replacement_forecast_materializer import _conn, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_shadow


def test_partial_aifs_coverage_is_report_only_and_not_materialized_for_veto() -> None:
    request = _request()
    extraction = replace(request.aifs_extraction, members=request.aifs_extraction.members[:1])

    result = materialize_replacement_forecast_shadow(_conn(), replace(request, aifs_extraction=extraction))

    assert result.status == "BLOCKED"
    assert result.posterior_id is None
