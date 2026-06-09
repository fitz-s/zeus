# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06
# Purpose: Protect optional ecCodes AIFS GRIB point-sample extraction for replacement forecast materialization.
# Reuse: Run before changing AIFS GRIB extraction or materialization inputs.
# Authority basis: Operator-directed AIFS sampled-2t replacement shadow integration.
"""AIFS GRIB point sample extraction tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.ecmwf_aifs_grib_samples import extract_aifs_2t_point_samples_from_grib


UTC = timezone.utc


class _FakeEcCodes:
    def __init__(self, rows):
        self.rows = list(rows)
        self.index = 0

    def codes_grib_new_from_file(self, _fh):
        if self.index >= len(self.rows):
            return None
        gid = self.index
        self.index += 1
        return gid

    def codes_get(self, gid, key):
        row = self.rows[gid]
        if key not in row:
            raise KeyError(key)
        return row[key]

    def codes_grib_find_nearest(self, gid, lat, lon):
        return [{"lat": lat + 0.01, "lon": lon - 0.01, "distance": 1.2, "value": 290.0 + gid / 10.0}]

    def codes_release(self, _gid):
        return None


def _rows():
    base = {
        "marsClass": "ai",
        "stream": "enfo",
        "model": "aifs-ens",
        "shortName": "2t",
        "paramId": 167,
        "levtype": "sfc",
        "step": 6,
    }
    rows = [{**base, "marsType": "cf"}]
    for member in range(1, 51):
        rows.append({**base, "marsType": "pf", "number": member})
    return rows


def test_extract_aifs_grib_point_samples_with_injected_eccodes(tmp_path) -> None:
    grib = tmp_path / "aifs.grib2"
    grib.write_bytes(b"fake-grib")

    result = extract_aifs_2t_point_samples_from_grib(
        grib,
        latitude=31.2304,
        longitude=121.4737,
        source_cycle_time=datetime(2026, 6, 6, 0, tzinfo=UTC),
        eccodes_module=_FakeEcCodes(_rows()),
    )

    assert result.message_count == 51
    assert result.step_hours == (6,)
    assert len(result.samples) == 51
    assert result.samples[0].member_id == "control"
    assert result.samples[0].valid_time_utc.isoformat() == "2026-06-06T06:00:00+00:00"
    assert result.samples[0].temperature == pytest.approx(16.85)
    assert result.nearest_points[0]["grid_latitude"] == pytest.approx(31.2404)


def test_extract_aifs_grib_point_samples_requires_eccodes_when_not_injected(tmp_path) -> None:
    grib = tmp_path / "aifs.grib2"
    grib.write_bytes(b"fake-grib")

    with pytest.raises(RuntimeError, match="ecCodes Python bindings are required"):
        extract_aifs_2t_point_samples_from_grib(
            grib,
            latitude=31.2304,
            longitude=121.4737,
            source_cycle_time=datetime(2026, 6, 6, 0, tzinfo=UTC),
        )


def test_extract_aifs_grib_point_samples_blocks_invalid_identity(tmp_path) -> None:
    grib = tmp_path / "aifs.grib2"
    grib.write_bytes(b"fake-grib")
    rows = _rows()
    rows[0]["model"] = "ifs"

    with pytest.raises(ValueError, match="AIFS_GRIB_IDENTITY_INVALID"):
        extract_aifs_2t_point_samples_from_grib(
            grib,
            latitude=31.2304,
            longitude=121.4737,
            source_cycle_time=datetime(2026, 6, 6, 0, tzinfo=UTC),
            eccodes_module=_FakeEcCodes(rows),
        )
