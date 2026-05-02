# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: Codex P1 follow-up to PR #37 — evaluator.py:3156 area.
#                  ensemble_snapshots_v2 INSERT uses ON CONFLICT(city,
#                  target_date, temperature_metric, issue_time, data_version)
#                  DO UPDATE SET available_at, fetch_time, model_version,
#                  valid_time, ... so the same snapshot_id keeps getting
#                  served whenever a snapshot is refreshed in-cycle. The
#                  legacy ensemble_snapshots projection must mirror that
#                  upsert; otherwise refreshing a snapshot raises a spurious
#                  identity mismatch and aborts ENS storage.
"""Regression antibody: legacy snapshot projection mirrors v2 upsert.

Pre-fix behaviour: second call to ``_store_ens_snapshot`` with the same
conflict-key tuple but a different ``model_version`` / ``available_at`` /
``fetch_time`` raised
``ValueError: legacy ensemble snapshot projection refused``.

Post-fix behaviour: legacy row is UPDATEd to mirror v2's upsert. The
conflict-key fields stay immutable (genuine snapshot_id reuse for a
different (city, target_date, metric, issue_time, data_version) tuple
still raises). Mutable fields (model_version, available_at, fetch_time,
valid_time, lead_hours, members_json, spread, is_bimodal, authority)
move with the v2 row.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

import src.engine.evaluator as evaluator_module
from src.config import City
from src.state.db import get_connection, init_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX

NYC = City(
    name="NYC",
    lat=40.7772,
    lon=-73.8726,
    timezone="America/New_York",
    cluster="NYC",
    settlement_unit="F",
    wu_station="KLGA",
)


def _ens(value: float, spread: float = 1.25):
    return type(
        "DummyEns",
        (),
        {
            "member_extrema": np.array([value - 1.0, value, value + 1.0]),
            "spread_float": lambda self, _s=spread: _s,
            "is_bimodal": lambda self: False,
            "temperature_metric": HIGH_LOCALDAY_MAX,
        },
    )()


def _result(*, fetch_time: datetime, model: str = "ecmwf_ifs025"):
    return {
        "issue_time": None,
        "first_valid_time": datetime(2026, 1, 14, 5, 0, tzinfo=timezone.utc),
        "fetch_time": fetch_time,
        "model": model,
    }


def _legacy_row(conn, snapshot_id: str):
    return conn.execute(
        """
        SELECT model_version, available_at, fetch_time, valid_time, lead_hours,
               spread, is_bimodal, members_json, authority,
               city, target_date, temperature_metric, data_version, issue_time
        FROM ensemble_snapshots
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()


def _v2_row(conn, snapshot_id: str):
    return conn.execute(
        """
        SELECT model_version, available_at, fetch_time, valid_time
        FROM ensemble_snapshots_v2
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()


def _seed_legacy(conn, *, snapshot_id, model_version, available_at, fetch_time, valid_time):
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at,
         fetch_time, lead_hours, members_json, spread, is_bimodal,
         model_version, data_version, authority, temperature_metric)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            NYC.name,
            "2026-01-15",
            None,
            valid_time,
            available_at,
            fetch_time,
            12.0,
            json.dumps([39.0, 40.0, 41.0]),
            1.25,
            0,
            model_version,
            HIGH_LOCALDAY_MAX.data_version,
            "VERIFIED",
            HIGH_LOCALDAY_MAX.temperature_metric,
        ),
    )


def test_ensure_legacy_projection_updates_mutable_fields_on_existing_row(tmp_path):
    """Direct unit: same snapshot_id with same conflict-key tuple but new
    mutable fields → UPDATE the legacy row, do NOT raise.

    Pre-fix behaviour: raised ``legacy ensemble snapshot projection refused``
    on any model_version/available_at/fetch_time/valid_time mismatch, even
    when those changes were the legitimate result of v2's ON CONFLICT
    DO UPDATE in the same cycle.
    """
    conn = get_connection(tmp_path / "zeus.db")
    init_schema(conn)
    try:
        _seed_legacy(
            conn,
            snapshot_id=101,
            model_version="ecmwf_ifs025",
            available_at="2026-01-14T06:05:00+00:00",
            fetch_time="2026-01-14T06:05:00+00:00",
            valid_time="2026-01-14T05:00:00+00:00",
        )

        # New mutable fields, same conflict-key tuple. MUST NOT raise.
        evaluator_module._ensure_legacy_snapshot_projection(
            conn,
            legacy_table="ensemble_snapshots",
            snapshot_id=101,
            city=NYC,
            target_date="2026-01-15",
            issue_time=None,
            valid_time="2026-01-14T05:30:00+00:00",
            available_at="2026-01-14T06:35:00+00:00",
            fetch_time="2026-01-14T06:35:00+00:00",
            lead_hours=11.5,
            members_json=json.dumps([40.0, 41.0, 42.0]),
            spread=1.5,
            is_bimodal=0,
            model_version="ecmwf_ifs025_v2",
            data_version=HIGH_LOCALDAY_MAX.data_version,
            authority="VERIFIED",
            temperature_metric=HIGH_LOCALDAY_MAX.temperature_metric,
        )

        row = _legacy_row(conn, 101)
        assert row is not None
        # Mutable fields moved.
        assert row["model_version"] == "ecmwf_ifs025_v2"
        assert row["available_at"] == "2026-01-14T06:35:00+00:00"
        assert row["fetch_time"] == "2026-01-14T06:35:00+00:00"
        assert row["valid_time"] == "2026-01-14T05:30:00+00:00"
        assert row["spread"] == 1.5
        assert row["lead_hours"] == 11.5
        # Conflict-key fields unchanged.
        assert row["city"] == NYC.name
        assert row["target_date"] == "2026-01-15"
        assert row["temperature_metric"] == HIGH_LOCALDAY_MAX.temperature_metric
        assert row["data_version"] == HIGH_LOCALDAY_MAX.data_version
        assert row["issue_time"] is None
    finally:
        conn.close()


def test_ensure_legacy_projection_still_raises_on_genuine_conflict_key_mismatch(tmp_path):
    """If the same snapshot_id is reused for a genuinely different
    (city, target_date, temperature_metric, issue_time, data_version) tuple,
    that's data corruption, not a refresh — must still raise.
    """
    import pytest

    conn = get_connection(tmp_path / "zeus.db")
    init_schema(conn)
    try:
        _seed_legacy(
            conn,
            snapshot_id=999,
            model_version="ecmwf_ifs025",
            available_at="2026-01-14T06:05:00+00:00",
            fetch_time="2026-01-14T06:05:00+00:00",
            valid_time="2026-01-14T05:00:00+00:00",
        )

        with pytest.raises(ValueError, match="legacy ensemble snapshot projection refused"):
            evaluator_module._ensure_legacy_snapshot_projection(
                conn,
                legacy_table="ensemble_snapshots",
                snapshot_id=999,
                city=NYC,
                target_date="2026-01-16",  # DIFFERENT target_date
                issue_time=None,
                valid_time="2026-01-15T05:00:00+00:00",
                available_at="2026-01-15T06:05:00+00:00",
                fetch_time="2026-01-15T06:05:00+00:00",
                lead_hours=12.0,
                members_json=json.dumps([39.0, 40.0, 41.0]),
                spread=1.25,
                is_bimodal=0,
                model_version="ecmwf_ifs025",
                data_version=HIGH_LOCALDAY_MAX.data_version,
                authority="VERIFIED",
                temperature_metric=HIGH_LOCALDAY_MAX.temperature_metric,
            )
    finally:
        conn.close()


def test_snapshot_identity_matches_conflict_key_helper_only_checks_5_fields():
    """Antibody: the helper must only compare the 5 v2 conflict-key fields.

    If a future change adds available_at/fetch_time/model_version to the
    conflict-key check, the projection re-introduces the same fail-closed
    spurious raise on legitimate v2 upserts.
    """
    row = {
        "city": NYC.name,
        "target_date": "2026-01-15",
        "temperature_metric": HIGH_LOCALDAY_MAX.temperature_metric,
        "data_version": HIGH_LOCALDAY_MAX.data_version,
        "issue_time": None,
        # Mutable fields — intentionally divergent from kwargs below.
        "model_version": "old_model",
        "available_at": "2026-01-14T06:05:00+00:00",
        "fetch_time": "2026-01-14T06:05:00+00:00",
        "valid_time": "2026-01-14T05:00:00+00:00",
    }

    assert evaluator_module._snapshot_identity_matches_conflict_key(
        row,
        city=NYC,
        target_date="2026-01-15",
        temperature_metric=HIGH_LOCALDAY_MAX.temperature_metric,
        data_version=HIGH_LOCALDAY_MAX.data_version,
        issue_time=None,
    )

    # Genuine conflict-key mismatch (different metric) MUST still fail.
    assert not evaluator_module._snapshot_identity_matches_conflict_key(
        row,
        city=NYC,
        target_date="2026-01-15",
        temperature_metric="LOW_LOCALDAY_MIN",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        issue_time=None,
    )
