from __future__ import annotations

import json

from scripts.backfill_tigge_snapshot_p_raw import (
    materialize_snapshot_row,
    p_raw_from_member_values,
)
from src.config import cities_by_name
from src.state.db import get_connection, init_schema
from src.types import Bin


def test_p_raw_from_member_values_uses_typed_bins_and_units():
    city = cities_by_name["NYC"]
    bins = [
        Bin(low=39, high=40, unit="F", label="39-40°F"),
        Bin(low=41, high=42, unit="F", label="41-42°F"),
    ]

    p_raw = p_raw_from_member_values([39.0, 40.0, 41.0, 42.0], bins, city)

    assert p_raw == [0.5, 0.5]


def test_materialize_snapshot_row_writes_replay_compatible_vector(tmp_path):
    db_path = tmp_path / "world.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (1, 'NYC', '2026-04-03', '2026-04-02T00:00:00Z', '2026-04-03T00:00:00Z',
                '2026-04-02T08:00:00Z', '2026-04-02T08:05:00Z', 24.0,
                '[39.0, 40.0, 41.0, 42.0]', NULL, 2.0, 0, 'ecmwf_tigge', 'test')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES
        ('NYC', '2026-04-03', '39-40°F', 0.0, 1, 1.0, 'MAM', 'US-Northeast', '2026-04-02T08:00:00Z', 40.0),
        ('NYC', '2026-04-03', '41-42°F', 0.0, 0, 1.0, 'MAM', 'US-Northeast', '2026-04-02T08:00:00Z', 40.0)
        """
    )
    conn.commit()

    row = conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id = 1").fetchone()
    status = materialize_snapshot_row(conn, row)
    stored = conn.execute("SELECT p_raw_json FROM ensemble_snapshots WHERE snapshot_id = 1").fetchone()[0]
    conn.close()

    assert status == "updated"
    assert json.loads(stored) == [0.5, 0.5]
