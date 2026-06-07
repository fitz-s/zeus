# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect AIFS ENS OpenData request contract for sampled-2t replacement shadow extraction.
# Reuse: Run before changing AIFS ENS OpenData request parameters or artifact capture metadata.
# Authority basis: ECMWF OpenData AIFS ENS sampled-2t shadow integration; not B0 calibration authority.
"""AIFS ENS OpenData request contract tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.ecmwf_aifs_ens_request import (
    DEFAULT_STEPS,
    ECMWF_CLASS,
    MODEL,
    PARAMS,
    SOURCE,
    STREAM,
    TYPES,
    build_aifs_ens_open_data_request,
    retrieve_aifs_ens_open_data_request,
)
from src.data.ecmwf_aifs_sampled_2t_localday import HIGH_DATA_VERSION, LOW_DATA_VERSION, PRODUCT_ID, SOURCE_ID


def test_aifs_ens_request_uses_aifs_ens_model_and_sampled_2t_steps(tmp_path) -> None:
    target = tmp_path / "aifs.grib2"
    request = build_aifs_ens_open_data_request(
        forecast_date="2026-06-06",
        cycle_hour="6",
        target_path=target,
    )

    assert request.client_kwargs() == {"source": SOURCE, "model": MODEL}
    retrieve = request.retrieve_kwargs()
    assert "class" not in retrieve
    assert retrieve["model"] == MODEL
    assert retrieve["date"] == "20260606"
    assert retrieve["time"] == 6
    assert retrieve["stream"] == STREAM
    assert retrieve["type"] == list(TYPES)
    assert retrieve["param"] == list(PARAMS)
    assert retrieve["step"] == list(DEFAULT_STEPS)
    assert retrieve["target"] == str(target)
    assert request.source_cycle_time == datetime(2026, 6, 6, 6, tzinfo=timezone.utc)


def test_aifs_ens_request_metadata_is_shadow_only_and_product_isolated(tmp_path) -> None:
    request = build_aifs_ens_open_data_request(
        forecast_date=date(2026, 6, 6),
        cycle_hour=0,
        target_path=tmp_path / "aifs.grib2",
        steps=(0, 6, 12),
    )

    metadata = request.manifest_metadata()
    assert metadata["source_id"] == SOURCE_ID
    assert metadata["product_id"] == PRODUCT_ID
    assert metadata["class"] == "ai"
    assert metadata["model"] == "aifs-ens"
    assert metadata["measurement_policy"] == "sampled_2t_6h_local_calendar_day"
    assert metadata["trade_authority_status"] == "SHADOW_ONLY"
    assert metadata["training_allowed"] is False
    assert request.high_data_version == HIGH_DATA_VERSION
    assert request.low_data_version == LOW_DATA_VERSION
    for identifier in (request.source_id, request.product_id, request.high_data_version, request.low_data_version):
        assert ("h" + "3") not in identifier.lower()


def test_aifs_ens_request_rejects_wrong_cycle_steps_or_authority(tmp_path) -> None:
    with pytest.raises(ValueError, match="00/06/12/18"):
        build_aifs_ens_open_data_request(forecast_date="2026-06-06", cycle_hour=3, target_path=tmp_path / "bad.grib2")

    with pytest.raises(ValueError, match="6-hourly"):
        build_aifs_ens_open_data_request(
            forecast_date="2026-06-06",
            cycle_hour=0,
            target_path=tmp_path / "bad.grib2",
            steps=(0, 3, 6),
        )

    request = build_aifs_ens_open_data_request(forecast_date="2026-06-06", cycle_hour=0, target_path=tmp_path / "aifs.grib2")
    with pytest.raises(ValueError, match="shadow-only"):
        type(request)(
            forecast_date=request.forecast_date,
            cycle_hour=request.cycle_hour,
            target_path=request.target_path,
            trade_authority_status="ENTRY_PRIMARY",
        )

    with pytest.raises(ValueError, match="period-extrema"):
        type(request)(
            forecast_date=request.forecast_date,
            cycle_hour=request.cycle_hour,
            target_path=request.target_path,
            high_data_version="ecmwf_aifs_mx2t3_bad",
        )


def test_aifs_ens_retrieve_uses_injected_client_factory(tmp_path) -> None:
    target = tmp_path / "aifs.grib2"
    request = build_aifs_ens_open_data_request(forecast_date="2026-06-06", cycle_hour=12, target_path=target, steps=(0, 6))
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        def retrieve(self, **kwargs):
            calls.append(("retrieve", kwargs))

    returned = retrieve_aifs_ens_open_data_request(request, client_factory=FakeClient)

    assert returned == target
    assert calls[0] == ("client", {"source": "aws", "model": "aifs-ens"})
    assert calls[1][0] == "retrieve"
    assert "class" not in calls[1][1]
    assert calls[1][1]["model"] == "aifs-ens"
    assert calls[1][1]["time"] == 12
    assert calls[1][1]["step"] == [0, 6]
    assert calls[1][1]["target"] == str(target)
