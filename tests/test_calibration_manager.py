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
