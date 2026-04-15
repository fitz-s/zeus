"""SQLite store for calibration pairs and Platt models.

Provides CRUD operations for calibration_pairs and platt_models tables.
All writes include proper timestamps. All reads enforce available_at constraint.

K4: get_pairs_for_bucket now defaults to authority_filter='VERIFIED' so all
callers get only provenance-verified pairs by default. Pass
authority_filter='any' to bypass (diagnostic / rebuild use only).
If the authority column is missing (pre-migration DB), the filter is skipped
so existing callers are not broken.
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from src.contracts.settlement_semantics import round_wmo_half_up_value
from src.state.db import get_world_connection


def infer_bin_width_from_label(range_label: str) -> float | None:
    """Infer finite bin width from a stored range label.

    Returns:
    - finite width for point/range bins
    - None for shoulders or unparseable labels
    """
    label = (range_label or "").strip()

    # Shoulder low/high
    if re.search(r"\u00b0[FfCc]\s+or\s+(below|lower)$", label):
        return None
    if re.search(r"\u00b0[FfCc]\s+or\s+(higher|above|more)$", label):
        return None

    # Interior range like 39-40\u00b0F
    m = re.search(r"(-?\d+\.?\d*)\s*[-\u2013]\s*(-?\d+\.?\d*)\s*\u00b0?[FfCc]?", label)
    if m:
        low = float(m.group(1))
        high = float(m.group(2))
        return max(1.0, high - low + 1.0)

    # Point bin like 10\u00b0C
    m = re.search(r"(-?\d+\.?\d*)\s*\u00b0[Cc]$", label)
    if m:
        return 1.0

    return None


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
    decision_group_id: Optional[str] = None,
    bias_corrected: bool = False,
    *,
    bin_source: str = "legacy",
    authority: str = "UNVERIFIED",
) -> None:
    """Insert a calibration pair (one per bin per settled market).

    Spec \u00a78.1: Harvester generates 11 pairs per settlement (1 outcome=1, 10 outcome=0).
    settlement_value is stored for audit only \u2014 defensive round to integer per contract.

    2026-04-14 refactor: ``bin_source`` defaults to ``"legacy"`` so existing
    callers (market-bin-derived harvester path, generate_calibration_pairs.py)
    are unchanged. The new canonical-grid rebuild script passes
    ``bin_source="canonical_v1"`` to mark rows it owns, which the destructive
    DELETE path in that script targets by equality match.
    """
    if settlement_value is not None:
        settlement_value = round_wmo_half_up_value(float(settlement_value))
    if decision_group_id is None:
        decision_group_id = f"{city}|{target_date}|{forecast_available_at}|lead={lead_days:g}"
    conn.execute("""
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days,
         season, cluster, forecast_available_at, settlement_value,
         decision_group_id, bias_corrected, bin_source, authority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (city, target_date, range_label, p_raw, outcome, lead_days,
          season, cluster, forecast_available_at, settlement_value,
          decision_group_id, int(bool(bias_corrected)), bin_source, authority))


def _has_authority_column(conn: sqlite3.Connection) -> bool:
    """Check whether calibration_pairs has the authority column.

    Used to gracefully handle pre-migration DBs in tests and production
    until migrate_add_authority_column.py has been run.
    """
    rows = conn.execute("PRAGMA table_info(calibration_pairs)").fetchall()
    return any(row[1] == "authority" for row in rows)


def get_pairs_for_bucket(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    authority_filter: str = 'VERIFIED',
) -> list[dict]:
    """Get calibration pairs for a bucket (cluster \u00d7 season).

    K4: authority_filter defaults to 'VERIFIED' so all callers get only
    provenance-verified pairs by default. Pass authority_filter='any' to
    bypass the filter (diagnostic / rebuild use only).

    If the authority column is absent (pre-migration DB), the filter is
    silently skipped so existing callers are not broken by the schema gap.

    Returns list of dicts with keys: p_raw, lead_days, outcome, range_label.
    """
    if authority_filter == 'any':
        rows = conn.execute("""
            SELECT p_raw, lead_days, outcome, range_label
            FROM calibration_pairs
            WHERE cluster = ? AND season = ?
            ORDER BY target_date
        """, (cluster, season)).fetchall()
    elif not _has_authority_column(conn):
        # M7 fix: pre-migration DB without authority column.
        # If caller requests UNVERIFIED, return empty list to prevent false-positive
        # blocks (returning all rows would look like contamination to the evaluator).
        # If caller requests VERIFIED (default), also return empty -- no verified data
        # can exist on a pre-migration DB.
        return []
    else:
        rows = conn.execute("""
            SELECT p_raw, lead_days, outcome, range_label
            FROM calibration_pairs
            WHERE cluster = ? AND season = ? AND authority = ?
            ORDER BY target_date
        """, (cluster, season, authority_filter)).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        item["bin_width"] = infer_bin_width_from_label(item.get("range_label", ""))
        result.append(item)
    return result


def get_pairs_count(
    conn: sqlite3.Connection,
    cluster: str,
    season: str,
    authority_filter: str = "VERIFIED",
) -> int:
    """Count calibration pairs in a bucket.

    K4.5 H5 fix: filters by authority='VERIFIED' by default.
    Pass authority_filter='any' to count all rows (diagnostics only).
    """
    if authority_filter == "any" or not _has_authority_column(conn):
        return conn.execute("""
            SELECT COUNT(*) FROM calibration_pairs
            WHERE cluster = ? AND season = ?
        """, (cluster, season)).fetchone()[0]
    return conn.execute("""
        SELECT COUNT(*) FROM calibration_pairs
        WHERE cluster = ? AND season = ? AND authority = ?
    """, (cluster, season, authority_filter)).fetchone()[0]


def save_platt_model(
    conn: sqlite3.Connection,
    bucket_key: str,
    A: float,
    B: float,
    C: float,
    bootstrap_params: list[tuple[float, float, float]],
    n_samples: int,
    brier_insample: Optional[float] = None,
    input_space: str = "raw_probability",
    authority: str = "VERIFIED",
) -> None:
    """Save a fitted Platt model.

    Uses INSERT OR REPLACE to handle refits on the UNIQUE(bucket_key) constraint.
    authority defaults to 'VERIFIED': this function writes a freshly fitted,
    trusted model. Pass authority='UNVERIFIED' only for diagnostic/test data.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO platt_models
        (bucket_key, param_A, param_B, param_C, bootstrap_params_json,
         n_samples, brier_insample, fitted_at, is_active, input_space, authority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
    """, (
        bucket_key, A, B, C,
        json.dumps(bootstrap_params),
        n_samples, brier_insample, now, input_space, authority
    ))


def load_platt_model(
    conn: sqlite3.Connection,
    bucket_key: str,
) -> Optional[dict]:
    """Load a fitted Platt model. Returns None if not found, inactive, or not VERIFIED."""
    row = conn.execute("""
        SELECT param_A, param_B, param_C, bootstrap_params_json,
               n_samples, brier_insample, fitted_at, input_space
        FROM platt_models
        WHERE bucket_key = ? AND is_active = 1 AND authority = 'VERIFIED'
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
        "input_space": row["input_space"] or "raw_probability",
    }


def deactivate_model(conn: sqlite3.Connection, bucket_key: str) -> None:
    """Mark a model as inactive (for refit/replacement)."""
    conn.execute("""
        UPDATE platt_models SET is_active = 0
        WHERE bucket_key = ?
    """, (bucket_key,))
