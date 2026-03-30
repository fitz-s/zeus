"""SQLite store for calibration pairs and Platt models.

Provides CRUD operations for calibration_pairs and platt_models tables.
All writes include proper timestamps. All reads enforce available_at constraint.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from src.state.db import get_connection


def add_calibration_pair(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    range_label: str,
    p_raw: float,
    outcome: int,
    lead_days: float,
    season: str,
    cluster: str,
    forecast_available_at: str,
    settlement_value: Optional[float] = None,
) -> None:
    """Insert a calibration pair (one per bin per settled market).

    Spec §8.1: Harvester generates 11 pairs per settlement (1 outcome=1, 10 outcome=0).
    """
    conn.execute("""
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days,
         season, cluster, forecast_available_at, settlement_value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (city, target_date, range_label, p_raw, outcome, lead_days,
          season, cluster, forecast_available_at, settlement_value))


def get_pairs_for_bucket(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
) -> list[dict]:
    """Get all calibration pairs for a bucket (cluster × season).

    Returns list of dicts with keys: p_raw, lead_days, outcome.
    """
    rows = conn.execute("""
        SELECT p_raw, lead_days, outcome
        FROM calibration_pairs
        WHERE cluster = ? AND season = ?
        ORDER BY target_date
    """, (cluster, season)).fetchall()

    return [dict(r) for r in rows]


def get_pairs_count(conn: sqlite3.Connection, cluster: str, season: str) -> int:
    """Count calibration pairs in a bucket."""
    return conn.execute("""
        SELECT COUNT(*) FROM calibration_pairs
        WHERE cluster = ? AND season = ?
    """, (cluster, season)).fetchone()[0]


def save_platt_model(
    conn: sqlite3.Connection,
    bucket_key: str,
    A: float,
    B: float,
    C: float,
    bootstrap_params: list[tuple[float, float, float]],
    n_samples: int,
    brier_insample: Optional[float] = None,
) -> None:
    """Save a fitted Platt model.

    Uses INSERT OR REPLACE to handle refits on the UNIQUE(bucket_key) constraint.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO platt_models
        (bucket_key, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, brier_insample, fitted_at, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (
        bucket_key, A, B, C,
        json.dumps(bootstrap_params),
        n_samples, brier_insample, now
    ))


def load_platt_model(
    conn: sqlite3.Connection,
    bucket_key: str,
) -> Optional[dict]:
    """Load a fitted Platt model. Returns None if not found or inactive."""
    row = conn.execute("""
        SELECT param_A, param_B, param_C, bootstrap_params_json,
               n_samples, brier_insample, fitted_at
        FROM platt_models
        WHERE bucket_key = ? AND is_active = 1
    """, (bucket_key,)).fetchone()

    if row is None:
        return None

    return {
        "A": row["param_A"],
        "B": row["param_B"],
        "C": row["param_C"],
        "bootstrap_params": json.loads(row["bootstrap_params_json"]),
        "n_samples": row["n_samples"],
        "brier_insample": row["brier_insample"],
        "fitted_at": row["fitted_at"],
    }


def deactivate_model(conn: sqlite3.Connection, bucket_key: str) -> None:
    """Mark a model as inactive (for refit/replacement)."""
    conn.execute("""
        UPDATE platt_models SET is_active = 0
        WHERE bucket_key = ?
    """, (bucket_key,))
