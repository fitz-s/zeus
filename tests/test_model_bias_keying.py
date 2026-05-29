# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL Findings 1+5 — lead/cycle/product keyed ENS bias correction.
"""Relationship tests: error_model_key identity includes product+lead_bucket;
lead-6 and lead-48 do NOT share a bucket; lead_bucket() boundary tests;
all 4 per-bucket rows survive (SEV-1 PK fix). cycle is NOT a key dimension (#363).

These tests are self-contained (no DB, no network) — they verify the structural
properties of the keying design:
  1. lead_bucket() maps known lead values to the expected bucket labels.
  2. lead-6 and lead-48 fall in DIFFERENT buckets (the short-lead sign-flip split).
  3. Boundary values land in the HIGHER bucket (lower-inclusive, upper-exclusive).
  4. Negative leads raise ValueError.
  5. error_model_key format = city|metric|season|family|live_dv|lead_bucket (no cycle).
  6. Two keys that share all dims except lead_bucket are distinct (no collision).
  7. cycle is NOT a key/column dimension (metric encodes cycle preference).
  8. lead_bucket is a BASE PK column (SEV-1 fix), not an extension column.
  9. write_bias_model + read_bias_model: all 4 bucket rows survive + correct bucket served.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.calibration.lead_bucket import LEAD_BUCKET_BOUNDS, lead_bucket


# ---------------------------------------------------------------------------
# 1. lead_bucket() maps expected values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lead_h,expected", [
    (0.0,   "L00_24"),
    (1.0,   "L00_24"),
    (6.0,   "L00_24"),
    (23.9,  "L00_24"),
    (24.0,  "L24_48"),   # boundary: 24 → HIGHER bucket
    (36.0,  "L24_48"),
    (47.9,  "L24_48"),
    (48.0,  "L48_96"),   # boundary: 48 → HIGHER bucket
    (72.0,  "L48_96"),
    (95.9,  "L48_96"),
    (96.0,  "L96_plus"), # boundary: 96 → HIGHER bucket
    (120.0, "L96_plus"),
    (240.0, "L96_plus"),
])
def test_lead_bucket_mapping(lead_h, expected):
    assert lead_bucket(lead_h) == expected


# ---------------------------------------------------------------------------
# 2. lead-6 and lead-48 do NOT share a bucket (the critical sign-flip split)
# ---------------------------------------------------------------------------

def test_lead_6_and_lead_48_in_different_buckets():
    b6  = lead_bucket(6.0)
    b48 = lead_bucket(48.0)
    assert b6 != b48, (
        f"lead-6 ({b6!r}) and lead-48 ({b48!r}) must be in different buckets — "
        "pooling short-lead (sign-flip) with medium-lead produces a wrong-sign correction."
    )
    assert b6 == "L00_24"
    assert b48 == "L48_96"


# ---------------------------------------------------------------------------
# 3. Both sides of each bucket boundary land in the correct bucket
# ---------------------------------------------------------------------------

def test_boundary_lower_inclusive():
    """Lower bound is INCLUSIVE: at exactly the boundary, go to the HIGHER bucket."""
    for lo, hi, label in LEAD_BUCKET_BOUNDS:
        if lo == 0.0:
            continue  # first bucket lower-bound is trivially inclusive
        # The value at exactly lo should be in THIS bucket, not the previous.
        assert lead_bucket(lo) == label, (
            f"At boundary lo={lo}, expected bucket {label!r}, "
            f"got {lead_bucket(lo)!r}"
        )


def test_boundary_upper_exclusive():
    """Upper bound is EXCLUSIVE: just below the boundary stays in the current bucket."""
    for lo, hi, label in LEAD_BUCKET_BOUNDS:
        if hi == float("inf"):
            continue
        just_below = hi - 0.001
        assert lead_bucket(just_below) == label, (
            f"Just below upper bound {hi} (lead={just_below}), "
            f"expected {label!r}, got {lead_bucket(just_below)!r}"
        )


# ---------------------------------------------------------------------------
# 4. Negative leads raise ValueError
# ---------------------------------------------------------------------------

def test_negative_lead_raises():
    with pytest.raises(ValueError, match="lead_hours must be >= 0"):
        lead_bucket(-1.0)

    with pytest.raises(ValueError):
        lead_bucket(-0.001)


# ---------------------------------------------------------------------------
# 5. error_model_key format includes all dimensions
# ---------------------------------------------------------------------------

def _make_error_model_key(
    city: str,
    metric: str,
    season: str,
    live_dv: str,
    lb: str,
) -> str:
    """Mirror the production format in fit_full_transport_error_models.py:
    city|metric|season|full_transport_v1|live_dv|lead_bucket  (cycle DROPPED, #363)."""
    return f"{city}|{metric}|{season}|full_transport_v1|{live_dv}|{lb}"


def test_error_model_key_includes_lead_bucket():
    key = _make_error_model_key(
        city="London",
        metric="high",
        season="JJA",
        live_dv="ecmwf_opendata_mx2t3_local_calendar_day_max",
        lb="L00_24",
    )
    assert "L00_24" in key
    assert "London" in key
    assert "high" in key
    assert "JJA" in key
    assert "full_transport_v1" in key
    assert "00z" not in key  # cycle is NOT a key dimension (#363)


# ---------------------------------------------------------------------------
# 6. Two keys differing only in lead_bucket are DISTINCT (no collision)
# ---------------------------------------------------------------------------

def test_keys_differ_by_lead_bucket():
    base = dict(city="NYC", metric="low", season="DJF",
                live_dv="ecmwf_opendata_mn2t3_local_calendar_day_min")
    key_short = _make_error_model_key(**base, lb="L00_24")
    key_medium = _make_error_model_key(**base, lb="L24_48")
    key_long = _make_error_model_key(**base, lb="L48_96")
    assert key_short != key_medium
    assert key_short != key_long
    assert key_medium != key_long


# ---------------------------------------------------------------------------
# 7. cycle is NOT a key/column dimension (#363 fix)
# ---------------------------------------------------------------------------

def test_cycle_is_not_a_key_dimension():
    """cycle was DROPPED from the identity (#363): the metric dimension already
    encodes cycle preference (HIGH->0z, LOW->12z) via load_bucket_residuals'
    metric-aware snapshot selection. A separate cycle key would require SQL-level
    cycle filtering to be honest; claiming it without enforcing it is forbidden."""
    from src.calibration.ens_bias_repo import _CANONICAL_EXTENSION_COLUMNS
    col_names = [c[0] for c in _CANONICAL_EXTENSION_COLUMNS]
    assert "cycle" not in col_names, "cycle must NOT be an extension column (not enforced as a filter)"


# ---------------------------------------------------------------------------
# 8. lead_bucket is a BASE PK column, NOT in _CANONICAL_EXTENSION_COLUMNS
#    (SEV-1 fix 2026-05-29: promoted from extension to PK so all 4 per-bucket
#    rows survive INSERT OR REPLACE instead of overwriting each other)
# ---------------------------------------------------------------------------

def test_lead_bucket_is_base_pk_column_not_extension():
    """lead_bucket must be a base PK column (in the CREATE TABLE schema) and must
    NOT appear in _CANONICAL_EXTENSION_COLUMNS.  Pre-fix it was an extension column
    with no PK presence — only the last-written bucket survived INSERT OR REPLACE."""
    from src.calibration.ens_bias_repo import (
        MODEL_BIAS_ENS_V2_SCHEMA,
        _CANONICAL_EXTENSION_COLUMNS,
    )
    col_names = [c[0] for c in _CANONICAL_EXTENSION_COLUMNS]
    # Must NOT be an extension column (it's a PK column now)
    assert "lead_bucket" not in col_names, (
        "lead_bucket must NOT be in _CANONICAL_EXTENSION_COLUMNS — it is a base PK column"
    )
    # Must be present in the CREATE TABLE schema
    assert "lead_bucket" in MODEL_BIAS_ENS_V2_SCHEMA.lower(), (
        "lead_bucket missing from MODEL_BIAS_ENS_V2_SCHEMA"
    )
    # Must appear in the PRIMARY KEY declaration
    schema_lower = MODEL_BIAS_ENS_V2_SCHEMA.lower()
    pk_start = schema_lower.find("primary key")
    assert pk_start != -1, "PRIMARY KEY not found in schema"
    pk_clause = schema_lower[pk_start:]
    assert "lead_bucket" in pk_clause, (
        "lead_bucket not in PRIMARY KEY clause of MODEL_BIAS_ENS_V2_SCHEMA"
    )


# ---------------------------------------------------------------------------
# 9. write_bias_model accepts lead_bucket without error (schema guard)
# ---------------------------------------------------------------------------

def test_write_bias_model_accepts_lead_bucket():
    """write_bias_model must accept the lead_bucket kwarg and write it when the column exists."""
    from src.calibration.ens_bias_repo import init_ens_bias_schema, write_bias_model

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)

    write_bias_model(
        conn,
        city="TestCity",
        season="JJA",
        metric="high",
        live_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        prior_data_version="tigge_mx2t6_local_calendar_day_max",
        posterior_bias_c=0.5,
        posterior_sd_c=1.0,
        n_live=10,
        n_prior=20,
        weight_live=0.5,
        estimator="test",
        error_model_family="full_transport_v1",
        error_model_key="TestCity|high|JJA|full_transport_v1|ecmwf_opendata_mx2t3_local_calendar_day_max|L24_48",
        authority="STAGING",
        lead_bucket="L24_48",
    )
    conn.commit()

    row = conn.execute(
        "SELECT lead_bucket FROM model_bias_ens WHERE city='TestCity'"
    ).fetchone()
    assert row is not None
    assert row["lead_bucket"] == "L24_48"
    conn.close()


def test_lead_bucket_values_are_exhaustive():
    """All LEAD_BUCKET_BOUNDS labels are reachable by lead_bucket()."""
    expected_labels = {label for _, _, label in LEAD_BUCKET_BOUNDS}
    test_leads = [0.0, 6.0, 36.0, 72.0, 120.0]
    found_labels = {lead_bucket(h) for h in test_leads}
    assert found_labels == expected_labels


# ---------------------------------------------------------------------------
# RELATIONSHIP TESTS (SEV-1 PK fix, 2026-05-29)
#
# These tests MUST have been RED before the PK fix:
#   - Pre-fix: PK = (city, season, month, metric, live_data_version)
#     → 4 write_bias_model calls with distinct lead_buckets ALL share the same PK
#     → only the last write (L96_plus) survives; 3 rows silently overwritten.
#   - Post-fix: PK = (..., lead_bucket)
#     → all 4 rows survive; each is independently addressable.
# ---------------------------------------------------------------------------

_COMMON_KWARGS = dict(
    city="TestCity",
    season="JJA",
    month=6,
    metric="high",
    live_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
    prior_data_version="tigge_mx2t6_local_calendar_day_max",
    posterior_sd_c=1.0,
    n_live=10,
    n_prior=20,
    weight_live=0.5,
    estimator="test",
    error_model_family="full_transport_v1",
    authority="VERIFIED",
)

_BUCKET_BIASES = [
    ("L00_24",  0.0),
    ("L24_48",  1.0),
    ("L48_96",  2.0),
    ("L96_plus", 3.0),
]


def _write_four_bucket_rows(conn: sqlite3.Connection) -> None:
    """Write 4 rows with distinct lead_buckets + distinct posterior_bias_c."""
    from src.calibration.ens_bias_repo import write_bias_model
    for lb, bias in _BUCKET_BIASES:
        write_bias_model(
            conn,
            posterior_bias_c=bias,
            lead_bucket=lb,
            **_COMMON_KWARGS,
        )
    conn.commit()


def test_all_four_bucket_rows_survive():
    """RELATIONSHIP TEST (RED pre-fix, GREEN post-fix).

    4 write_bias_model calls with distinct lead_buckets on the same
    city/season/month/metric/live_dv MUST all survive in the DB (not overwrite
    each other).  Pre-fix only 1 row survived; the SEV-1 PK promotion makes all 4
    independently addressable.
    """
    from src.calibration.ens_bias_repo import init_ens_bias_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)
    _write_four_bucket_rows(conn)

    row_count = conn.execute("SELECT COUNT(*) FROM model_bias_ens").fetchone()[0]
    assert row_count == 4, (
        f"Expected 4 rows (one per lead_bucket), got {row_count}. "
        "This is the SEV-1: INSERT OR REPLACE with old PK overwrites earlier buckets."
    )
    conn.close()


def test_read_bias_model_returns_correct_bucket():
    """RELATIONSHIP TEST: read_bias_model with lead_bucket='L00_24' returns
    the L00_24 row (posterior_bias_c=0), NOT the L96_plus row (3).

    Pre-fix: only L96_plus survived; read returned 3 for all leads (wrong).
    Post-fix: correct bucket served (0 for L00_24, 3 for L96_plus).
    """
    from src.calibration.ens_bias_repo import init_ens_bias_schema, read_bias_model
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)
    _write_four_bucket_rows(conn)

    row_short = read_bias_model(
        conn,
        city="TestCity",
        season="JJA",
        metric="high",
        live_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        month=6,
        error_model_family="full_transport_v1",
        lead_bucket="L00_24",
    )
    assert row_short is not None, "read_bias_model returned None for L00_24 (row missing?)"
    assert float(row_short["posterior_bias_c"]) == 0.0, (
        f"Expected L00_24 posterior_bias_c=0.0, got {row_short['posterior_bias_c']}. "
        "Correct bucket not served — likely serving L96_plus (last-written) instead."
    )

    row_long = read_bias_model(
        conn,
        city="TestCity",
        season="JJA",
        metric="high",
        live_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        month=6,
        error_model_family="full_transport_v1",
        lead_bucket="L96_plus",
    )
    assert row_long is not None, "read_bias_model returned None for L96_plus"
    assert float(row_long["posterior_bias_c"]) == 3.0, (
        f"Expected L96_plus posterior_bias_c=3.0, got {row_long['posterior_bias_c']}"
    )
    conn.close()


def test_read_bias_model_wrong_bucket_returns_none():
    """Fail-closed: requesting a bucket with no row returns None (does not fall
    back to a different bucket)."""
    from src.calibration.ens_bias_repo import init_ens_bias_schema, read_bias_model, write_bias_model
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_ens_bias_schema(conn)

    # Write only L96_plus
    write_bias_model(
        conn,
        posterior_bias_c=3.0,
        lead_bucket="L96_plus",
        **_COMMON_KWARGS,
    )
    conn.commit()

    result = read_bias_model(
        conn,
        city="TestCity",
        season="JJA",
        metric="high",
        live_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        month=6,
        error_model_family="full_transport_v1",
        lead_bucket="L00_24",  # bucket that was NOT written
    )
    assert result is None, (
        "Fail-closed violated: requested L00_24 but got a row (expected None). "
        "Serving the wrong bucket's bias is a correctness hazard."
    )
    conn.close()


def test_migration_preserves_rows_with_legacy_pooled():
    """MIGRATION TEST: old-PK table rows are preserved after migration, with
    lead_bucket='LEGACY_POOLED' for rows that had no lead_bucket column.

    Simulates: create old-schema table (PK without lead_bucket), insert rows,
    then call init_ens_bias_schema → migration rebuilds PK, rows survive.
    """
    from src.calibration.ens_bias_repo import init_ens_bias_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Simulate the OLD schema (PK without lead_bucket)
    conn.execute("""
        CREATE TABLE model_bias_ens(
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            month INTEGER NOT NULL DEFAULT 0,
            metric TEXT NOT NULL,
            live_source_id TEXT,
            live_data_version TEXT NOT NULL,
            prior_source_id TEXT,
            prior_data_version TEXT,
            contributor_policy TEXT,
            bias_unit TEXT NOT NULL DEFAULT 'C',
            posterior_bias_c REAL NOT NULL,
            posterior_sd_c REAL NOT NULL,
            n_live INTEGER NOT NULL,
            n_prior INTEGER NOT NULL,
            n_paired INTEGER,
            weight_live REAL NOT NULL,
            paired_delta_c REAL,
            v0_c2 REAL,
            vo_c2 REAL,
            estimator TEXT NOT NULL,
            training_cutoff TEXT,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (city, season, month, metric, live_data_version)
        )
    """)
    # Insert 2 legacy rows (no lead_bucket column)
    conn.execute(
        "INSERT INTO model_bias_ens "
        "(city, season, month, metric, live_data_version, bias_unit, "
        " posterior_bias_c, posterior_sd_c, n_live, n_prior, weight_live, "
        " estimator, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("CityA", "JJA", 6, "high", "dv1", "C", 1.5, 0.5, 10, 20, 0.5, "test", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO model_bias_ens "
        "(city, season, month, metric, live_data_version, bias_unit, "
        " posterior_bias_c, posterior_sd_c, n_live, n_prior, weight_live, "
        " estimator, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("CityB", "DJF", 12, "low", "dv2", "C", -0.5, 0.3, 5, 15, 0.4, "test", "2026-01-01T00:00:00"),
    )
    conn.commit()

    # Run migration (init_ens_bias_schema detects old PK and rebuilds)
    init_ens_bias_schema(conn)

    # Both rows must survive
    rows = conn.execute("SELECT city, lead_bucket FROM model_bias_ens ORDER BY city").fetchall()
    assert len(rows) == 2, f"Migration lost rows: expected 2, got {len(rows)}"
    assert rows[0]["city"] == "CityA"
    assert rows[0]["lead_bucket"] == "LEGACY_POOLED", (
        f"Legacy row should get lead_bucket='LEGACY_POOLED', got {rows[0]['lead_bucket']!r}"
    )
    assert rows[1]["city"] == "CityB"
    assert rows[1]["lead_bucket"] == "LEGACY_POOLED"
    conn.close()
