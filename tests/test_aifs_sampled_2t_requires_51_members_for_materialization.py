from dataclasses import replace

from tests.test_replacement_forecast_materializer import _conn, _request

from src.data.replacement_forecast_materializer import materialize_replacement_forecast_shadow


def test_materialization_blocks_aifs_extraction_with_fewer_than_51_members() -> None:
    request = _request()
    partial = replace(request.aifs_extraction, members=request.aifs_extraction.members[:50])

    result = materialize_replacement_forecast_shadow(_conn(), replace(request, aifs_extraction=partial))

    assert result.ok is False
    assert "REPLACEMENT_MATERIALIZATION_AIFS_MEMBER_COVERAGE_INCOMPLETE" in result.reason_codes
