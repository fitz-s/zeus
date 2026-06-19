from dataclasses import replace

from tests.test_replacement_forecast_materializer import _conn, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live


def test_materialization_blocks_aifs_member_with_missing_expected_step() -> None:
    request = _request()
    first = request.aifs_extraction.members[0]
    bad_first = replace(
        first,
        sample_count=first.sample_count - 1,
        contributing_valid_times_utc=first.contributing_valid_times_utc[:-1],
    )
    extraction = replace(request.aifs_extraction, members=(bad_first, *request.aifs_extraction.members[1:]))

    result = materialize_replacement_forecast_live(_conn(), replace(request, aifs_extraction=extraction))

    assert result.ok is False
    assert "REPLACEMENT_MATERIALIZATION_AIFS_STEP_COVERAGE_INCOMPLETE" in result.reason_codes
