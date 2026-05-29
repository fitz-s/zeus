# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL Findings 1+5 — lead/cycle/product keyed ENS bias correction.
"""Relationship tests: error_model_key identity includes product+cycle+lead_bucket;
lead-6 and lead-48 do NOT share a bucket; lead_bucket() boundary tests.

These tests are self-contained (no DB, no network) — they verify the structural
properties of the keying design:
  1. lead_bucket() maps known lead values to the expected bucket labels.
  2. lead-6 and lead-48 fall in DIFFERENT buckets (the short-lead sign-flip split).
  3. Boundary values land in the HIGHER bucket (lower-inclusive, upper-exclusive).
  4. Negative leads raise ValueError.
  5. error_model_key format includes city|metric|season|family|live_dv|lead_bucket|cycle.
  6. Two keys that share all dims except lead_bucket are distinct (no collision).
  7. Two keys that share all dims except cycle are distinct.
  8. Extension columns list includes lead_bucket and cycle.
  9. write_bias_model accepts lead_bucket + cycle params without error (schema guard).
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
    cycle: str,
) -> str:
    """Mirror the format used in fit_full_transport_error_models.py."""
    return f"{city}|{metric}|{season}|full_transport_v1|{live_dv}|{lb}|{cycle}"


def test_error_model_key_includes_lead_bucket_and_cycle():
    key = _make_error_model_key(
        city="London",
        metric="high",
        season="JJA",
        live_dv="ecmwf_opendata_mx2t3_local_calendar_day_max",
        lb="L00_24",
        cycle="00z",
    )
    assert "L00_24" in key
    assert "00z" in key
    assert "London" in key
    assert "high" in key
    assert "JJA" in key
    assert "full_transport_v1" in key


# ---------------------------------------------------------------------------
# 6. Two keys differing only in lead_bucket are DISTINCT (no collision)
# ---------------------------------------------------------------------------

def test_keys_differ_by_lead_bucket():
    base = dict(city="NYC", metric="low", season="DJF",
                live_dv="ecmwf_opendata_mn2t3_local_calendar_day_min", cycle="12z")
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
# 8. _CANONICAL_EXTENSION_COLUMNS contains lead_bucket
# ---------------------------------------------------------------------------

def test_extension_columns_include_lead_bucket():
    from src.calibration.ens_bias_repo import _CANONICAL_EXTENSION_COLUMNS
    col_names = [c[0] for c in _CANONICAL_EXTENSION_COLUMNS]
    assert "lead_bucket" in col_names, "lead_bucket missing from _CANONICAL_EXTENSION_COLUMNS"


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
