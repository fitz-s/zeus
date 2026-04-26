# Lifecycle: created=2026-04-26; last_reviewed=2026-04-26; last_reused=never
# Purpose: B5 antibody — pin DST gap detection (`_is_missing_local_hour`) +
#          ObsV2Row round-trip of `is_missing_local_hour` flag end-to-end.
#          Closes the regression-coverage gap left over from the 2026-04-22
#          DST-fill packet (writer + caller paths + backfill script all
#          shipped in earlier packets, but no antibody pinned the contract).
# Reuse: Covers src/signal/diurnal._is_missing_local_hour + ObsV2Row round-trip
#        + insert_rows persistence. If a future refactor changes the helper
#        return type or drops the writer field, this test fires.
# Authority basis: docs/operations/task_2026-04-26_b5_dst_antibody/plan.md +
#   parent docs/operations/task_2026-04-26_live_readiness_completion/plan.md
#   §5 K3.B5-backfill row.
"""B5 antibody — DST is_missing_local_hour flag end-to-end pin.

Pre-this-slice: writer field accepts the flag (src/data/observation_instants_v2_writer.py:138);
all callers compute it via `_is_missing_local_hour` (wu_hourly_client:331,
ogimet_hourly_client:402, hourly_instants_append:153, daily_obs_append:668);
historical backfill is handled by `scripts/fill_obs_v2_dst_gaps.py`. None
of those paths had a regression test pinning the contract.

This file is the regression antibody. If a future refactor:
- changes `_is_missing_local_hour` return semantics → tests 1-3 fire
- drops the field from `ObsV2Row` → test 4 fires (constructor signature)
- changes the field's default → test 5 fires
- breaks the writer's round-trip of the field → test 6 fires
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.observation_instants_v2_writer import ObsV2Row, insert_rows
from src.signal.diurnal import _is_missing_local_hour
from src.state.schema.v2_schema import apply_v2_schema


# ---------------------------------------------------------------------------
# Helper unit tests (1-3)
# ---------------------------------------------------------------------------


def test_is_missing_local_hour_london_spring_forward():
    """London 2025-03-30 01:30 falls in DST gap (clocks jumped 01:00→02:00)."""
    london = ZoneInfo("Europe/London")
    assert _is_missing_local_hour(datetime(2025, 3, 30, 1, 30), london) is True


def test_is_missing_local_hour_atlanta_spring_forward():
    """Atlanta 2025-03-09 02:30 falls in DST gap (clocks jumped 02:00→03:00).

    America/New_York is the IANA zone for Atlanta — both share the US-East DST
    boundary. This test pins the cross-zone applicability of the helper.
    """
    new_york = ZoneInfo("America/New_York")
    assert _is_missing_local_hour(datetime(2025, 3, 9, 2, 30), new_york) is True


def test_is_missing_local_hour_returns_false_outside_gap():
    """Control: timestamps outside the DST gap return False.

    Three control samples covering both timezones and pre-/post-gap windows.
    Without the False side, a degenerate `return True` impl would still pass
    the positive cases.
    """
    london = ZoneInfo("Europe/London")
    new_york = ZoneInfo("America/New_York")
    # Post-gap on the same date
    assert _is_missing_local_hour(datetime(2025, 3, 30, 3, 0), london) is False
    assert _is_missing_local_hour(datetime(2025, 3, 9, 4, 0), new_york) is False
    # Pre-gap on the same date
    assert _is_missing_local_hour(datetime(2025, 3, 9, 1, 0), new_york) is False


# ---------------------------------------------------------------------------
# ObsV2Row constructor + writer round-trip (4-6)
# ---------------------------------------------------------------------------


def _valid_provenance() -> str:
    """Minimal provenance dict that passes A1 validation."""
    return json.dumps(
        {
            "tier": "WU_ICAO",
            "station_id": "EGLC",
            "payload_hash": "sha256:" + "b" * 64,
            "source_url": "https://api.weather.com/v1/REDACTED",
            "parser_version": "test_obs_v2_dst_missing_hour_flag_v1",
        },
        sort_keys=True,
    )


def _london_dst_gap_row(*, is_missing_local_hour: int) -> ObsV2Row:
    """London 2025-03-30 01:30 — a UTC instant whose local mapping is in the gap."""
    return ObsV2Row(
        city="London",
        target_date="2025-03-30",
        source="wu_icao_history",
        timezone_name="Europe/London",
        local_timestamp="2025-03-30T01:30:00+00:00",  # the gap value the source reported
        utc_timestamp="2025-03-30T01:30:00+00:00",
        utc_offset_minutes=0,
        time_basis="utc_hour_aligned",
        temp_unit="C",
        imported_at="2026-04-26T00:00:00+00:00",
        authority="VERIFIED",
        data_version="v1.wu-native.pilot",
        provenance_json=_valid_provenance(),
        is_missing_local_hour=is_missing_local_hour,
        temp_current=8.0,
        station_id="EGLC",
    )


def test_obs_v2_row_accepts_is_missing_local_hour_flag():
    """ObsV2Row constructor accepts is_missing_local_hour=1 without raising."""
    row = _london_dst_gap_row(is_missing_local_hour=1)
    assert row.is_missing_local_hour == 1


def test_obs_v2_row_default_is_missing_local_hour_is_zero():
    """Default value is 0 — pin contract.

    A future field-rename or default-flip would silently change every caller
    that omits the kwarg. This test pins the default.
    """
    # Construct WITHOUT specifying the flag — must default to 0.
    row = ObsV2Row(
        city="London",
        target_date="2025-03-30",
        source="wu_icao_history",
        timezone_name="Europe/London",
        local_timestamp="2025-03-30T03:00:00+01:00",  # post-gap, normal value
        utc_timestamp="2025-03-30T02:00:00+00:00",
        utc_offset_minutes=60,
        time_basis="utc_hour_aligned",
        temp_unit="C",
        imported_at="2026-04-26T00:00:00+00:00",
        authority="VERIFIED",
        data_version="v1.wu-native.pilot",
        provenance_json=_valid_provenance(),
        temp_current=9.0,
        station_id="EGLC",
    )
    assert row.is_missing_local_hour == 0


def test_obs_v2_writer_persists_dst_gap_flag():
    """Round-trip: write a flagged row, read it back, confirm flag survived.

    Pins the writer→DB→reader integrity for the flag specifically. A column
    rename or INSERT-statement drift would break this test before any
    production data was corrupted.
    """
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)

    row = _london_dst_gap_row(is_missing_local_hour=1)
    inserted = insert_rows(conn, [row])
    assert inserted == 1, f"Expected 1 row inserted, got {inserted}"

    cursor = conn.execute(
        "SELECT is_missing_local_hour FROM observation_instants_v2 "
        "WHERE city = ? AND utc_timestamp = ?",
        ("London", "2025-03-30T01:30:00+00:00"),
    )
    rows = cursor.fetchall()
    assert len(rows) == 1, f"Expected 1 row queried back, got {len(rows)}"
    assert rows[0][0] == 1, (
        f"is_missing_local_hour did not round-trip: expected 1, got {rows[0][0]!r}"
    )
