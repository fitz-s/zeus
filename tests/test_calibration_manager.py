"""Tests for calibration manager: bucket routing, maturity gate, fallback.

Covers:
1. NYC winter → US-Northeast_DJF bucket
2. Unknown city cluster → fallback works
3. Maturity levels correctly mapped
4. Store round-trip: save model → load model → predict
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.calibration.manager import (
    bucket_key,
    season_from_date,
    route_to_bucket,
    maturity_level,
    regularization_for_level,
    edge_threshold_multiplier,
    get_calibrator,
)
from src.calibration.store import (
    add_calibration_pair,
    get_pairs_for_bucket,
    save_platt_model,
    load_platt_model,
)
from src.calibration.effective_sample_size import (
    build_decision_groups,
    summarize_bucket_health,
    write_decision_groups,
)
from src.config import City
from src.state.db import get_connection, init_schema


NYC = City(
    name="NYC", lat=40.7772, lon=-73.8726,
    timezone="America/New_York", cluster="US-Northeast",
    settlement_unit="F", wu_station="KLGA",
)

LONDON = City(
    name="London", lat=51.4775, lon=-0.4614,
    timezone="Europe/London", cluster="Europe-Maritime",
    settlement_unit="C", wu_station="EGLL",
)


class TestBucketRouting:
    def test_nyc_winter(self):
        assert route_to_bucket(NYC, "2026-01-15") == "US-Northeast_DJF"

    def test_nyc_summer(self):
        assert route_to_bucket(NYC, "2026-07-15") == "US-Northeast_JJA"

    def test_london_spring(self):
        assert route_to_bucket(LONDON, "2026-04-10") == "Europe-Maritime_MAM"

    def test_december_is_winter(self):
        assert season_from_date("2026-12-01") == "DJF"

    def test_bucket_key_format(self):
        assert bucket_key("US-Northeast", "DJF") == "US-Northeast_DJF"


class TestMaturityLevel:
    def test_level_1(self):
        assert maturity_level(150) == 1
        assert maturity_level(500) == 1

    def test_level_2(self):
        assert maturity_level(50) == 2
        assert maturity_level(149) == 2

    def test_level_3(self):
        assert maturity_level(15) == 3
        assert maturity_level(49) == 3

    def test_level_4(self):
        assert maturity_level(14) == 4
        assert maturity_level(0) == 4

    def test_edge_threshold_multipliers(self):
        assert edge_threshold_multiplier(1) == 1.0
        assert edge_threshold_multiplier(2) == 1.5
        assert edge_threshold_multiplier(3) == 2.0
        assert edge_threshold_multiplier(4) == 3.0

    def test_regularization(self):
        assert regularization_for_level(1) == 1.0
        assert regularization_for_level(2) == 1.0
        assert regularization_for_level(3) == 0.1
        with pytest.raises(ValueError):
            regularization_for_level(4)


class TestStoreRoundTrip:
    def _get_test_conn(self, tmp_path):
        db_path = tmp_path / "test_cal.db"
        conn = get_connection(db_path)
        init_schema(conn)
        return conn

    def test_save_and_load_model(self, tmp_path):
        conn = self._get_test_conn(tmp_path)

        bootstrap = [(1.0, 0.1, -0.5), (0.9, 0.12, -0.48)]
        save_platt_model(
            conn, "US-Northeast_DJF",
            A=1.0, B=0.1, C=-0.5,
            bootstrap_params=bootstrap,
            n_samples=200,
            brier_insample=0.22,
        )
        conn.commit()

        loaded = load_platt_model(conn, "US-Northeast_DJF")
        assert loaded is not None
        assert loaded["A"] == 1.0
        assert loaded["B"] == 0.1
        assert loaded["C"] == -0.5
        assert loaded["n_samples"] == 200
        assert len(loaded["bootstrap_params"]) == 2

        conn.close()

    def test_load_nonexistent_returns_none(self, tmp_path):
        conn = self._get_test_conn(tmp_path)
        assert load_platt_model(conn, "NONEXISTENT_DJF") is None
        conn.close()

    def test_add_and_get_pairs(self, tmp_path):
        conn = self._get_test_conn(tmp_path)

        for i in range(20):
            add_calibration_pair(
                conn, "NYC", f"2026-01-{i+1:02d}", f"bin_{i%11}",
                p_raw=0.1 * (i % 11), outcome=1 if i == 5 else 0,
                lead_days=3.0, season="DJF", cluster="US-Northeast",
                forecast_available_at="2026-01-01T00:00:00Z",
            )
        conn.commit()

        pairs = get_pairs_for_bucket(conn, "US-Northeast", "DJF")
        assert len(pairs) == 20
        assert all("p_raw" in p for p in pairs)
        assert all("lead_days" in p for p in pairs)
        assert all("outcome" in p for p in pairs)

        conn.close()


class TestDecisionGroupAccounting:
    def test_builds_decision_groups_from_pair_rows(self, tmp_path):
        conn = get_connection(tmp_path / "test_groups.db")
        init_schema(conn)
        for target_date, forecast_available_at in [
            ("2026-01-01", "2025-12-30T00:00:00Z"),
            ("2026-01-02", "2025-12-31T00:00:00Z"),
        ]:
            for i in range(11):
                add_calibration_pair(
                    conn,
                    "NYC",
                    target_date,
                    f"bin_{i}",
                    p_raw=0.05 * i,
                    outcome=1 if i == 3 else 0,
                    lead_days=2.0,
                    season="DJF",
                    cluster="US-Northeast",
                    forecast_available_at=forecast_available_at,
                    settlement_value=41.0,
                )
        conn.commit()

        groups = build_decision_groups(conn)
        health = summarize_bucket_health(groups)
        written = write_decision_groups(conn, groups, recorded_at="2026-04-11T00:00:00Z")
        stored = conn.execute("SELECT COUNT(*) AS n FROM calibration_decision_group").fetchone()
        conn.close()

        assert len(groups) == 2
        assert all(group.n_pair_rows == 11 for group in groups)
        assert all(group.n_positive_rows == 1 for group in groups)
        assert groups[0].winning_range_label == "bin_3"
        assert health == [{
            "bucket_key": "US-Northeast_DJF",
            "cluster": "US-Northeast",
            "season": "DJF",
            "decision_groups": 2,
            "pair_rows": 22,
            "positive_rows": 2,
            "min_lead_days": 2.0,
            "max_lead_days": 2.0,
            "rows_per_group": 11.0,
        }]
        assert written == 2
        assert stored["n"] == 2

    def test_decision_group_write_is_idempotent(self, tmp_path):
        conn = get_connection(tmp_path / "test_groups.db")
        init_schema(conn)
        add_calibration_pair(
            conn,
            "NYC",
            "2026-01-01",
            "39-40°F",
            p_raw=0.4,
            outcome=1,
            lead_days=2.0,
            season="DJF",
            cluster="US-Northeast",
            forecast_available_at="2025-12-30T00:00:00Z",
            settlement_value=40.0,
        )
        groups = build_decision_groups(conn)

        assert write_decision_groups(conn, groups, recorded_at="t1") == 1
        assert write_decision_groups(conn, groups, recorded_at="t2") == 1
        row = conn.execute(
            "SELECT COUNT(*) AS n, MAX(recorded_at) AS recorded_at FROM calibration_decision_group"
        ).fetchone()
        conn.close()

        assert row["n"] == 1
        assert row["recorded_at"] == "t2"


class TestGetCalibrator:
    def test_returns_none_level4_when_empty(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)

        cal, level = get_calibrator(conn, NYC, "2026-01-15")
        assert cal is None
        assert level == 4

        conn.close()

    def test_returns_calibrator_from_stored_model(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)

        # Store a pre-fitted model
        bootstrap = [(1.0, 0.1, -0.5)] * 50
        save_platt_model(
            conn, "US-Northeast_DJF",
            A=1.0, B=0.1, C=-0.5,
            bootstrap_params=bootstrap,
            n_samples=200,
        )
        conn.commit()

        cal, level = get_calibrator(conn, NYC, "2026-01-15")
        assert cal is not None
        assert cal.fitted is True
        assert level == 1  # n=200 ≥ 150

        # Predict should work
        result = cal.predict(0.5, 3.0)
        assert 0.001 <= result <= 0.999

        conn.close()
