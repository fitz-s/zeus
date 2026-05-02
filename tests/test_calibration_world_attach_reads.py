# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: live-unblock PR37; runtime calibration truth lives in attached world DB
"""Regression tests for calibration reads through trade DB + attached world DB.

Live cycles use a trade DB connection with the world DB attached as ``world``.
Legacy bootstrap left empty calibration tables in the trade DB, so unqualified
``FROM platt_models_v2`` reads can silently miss authoritative world rows.
"""
from __future__ import annotations

import json
import sqlite3

from src.calibration.manager import get_calibrator
from src.calibration.store import load_platt_model_v2
from src.config import City


PLATT_V2_SCHEMA = """
CREATE TABLE {schema}platt_models_v2 (
    model_key TEXT PRIMARY KEY,
    temperature_metric TEXT NOT NULL,
    cluster TEXT NOT NULL,
    season TEXT NOT NULL,
    data_version TEXT NOT NULL,
    input_space TEXT NOT NULL,
    param_A REAL NOT NULL,
    param_B REAL NOT NULL,
    param_C REAL NOT NULL,
    bootstrap_params_json TEXT NOT NULL,
    n_samples INTEGER NOT NULL,
    brier_insample REAL,
    fitted_at TEXT NOT NULL,
    is_active INTEGER NOT NULL,
    authority TEXT NOT NULL
)
"""


LOW_VERSION = "tigge_mn2t6_local_calendar_day_min_v1"


def _attached_trade_conn(tmp_path) -> sqlite3.Connection:
    trade_path = tmp_path / "trade.db"
    world_path = tmp_path / "world.db"
    conn = sqlite3.connect(trade_path)
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    conn.execute(PLATT_V2_SCHEMA.format(schema=""))
    conn.execute(PLATT_V2_SCHEMA.format(schema="world."))
    conn.execute(
        """
        INSERT INTO world.platt_models_v2 (
            model_key, temperature_metric, cluster, season, data_version,
            input_space, param_A, param_B, param_C, bootstrap_params_json,
            n_samples, brier_insample, fitted_at, is_active, authority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"low:London:MAM:{LOW_VERSION}:width_normalized_density",
            "low",
            "London",
            "MAM",
            LOW_VERSION,
            "width_normalized_density",
            0.25,
            -0.10,
            0.0,
            json.dumps([(0.25, -0.10, 0.0)]),
            957,
            0.18,
            "2026-05-01T18:22:35+00:00",
            1,
            "VERIFIED",
        ),
    )
    return conn


def test_load_platt_model_v2_prefers_attached_world_over_empty_trade_shadow(tmp_path):
    conn = _attached_trade_conn(tmp_path)

    loaded = load_platt_model_v2(
        conn,
        temperature_metric="low",
        cluster="London",
        season="MAM",
        data_version=LOW_VERSION,
    )

    assert loaded is not None
    assert loaded["A"] == 0.25
    assert loaded["n_samples"] == 957


def test_get_calibrator_uses_attached_world_platt_model(tmp_path):
    conn = _attached_trade_conn(tmp_path)
    city = City(
        name="London",
        lat=51.4775,
        lon=-0.4614,
        timezone="Europe/London",
        settlement_unit="C",
        cluster="London",
        wu_station="EGLL",
    )

    cal, level = get_calibrator(
        conn,
        city,
        "2026-05-04",
        temperature_metric="low",
    )

    assert cal is not None
    assert cal.A == 0.25
    assert level == 1
