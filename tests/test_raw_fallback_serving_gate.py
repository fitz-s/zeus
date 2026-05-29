# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL Findings 1+5 — lead/cycle/product keyed ENS bias correction.
"""Relationship tests: raw fallback dominance gate.

The accept rule (operator verbatim):
  "Accept candidate only if: candidate beats raw on at least 2 of 3 proper scores AND
   bootstrap LCB(improvement) > 0 AND no catastrophic cohort regression.
   If none pass: use raw identity."

These tests verify the gate semantics exhaustively:
  1. Raw wins when all candidates fail the product gate (cross-product evidence).
  2. Raw wins when all candidates have LCB <= 0 (no OOS improvement guarantee).
  3. Raw wins when all candidates have < 2/3 proper-score wins.
  4. Raw wins when all candidates are catastrophic.
  5. Raw wins when candidate pool is empty.
  6. Correct candidate wins when exactly one passes all gates.
  7. load_bucket_residuals returns empty list when no rows match the requested bucket.
  8. load_bucket_residuals: lead-6 and lead-48 rows never appear in the same bucket query.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.score_error_model_candidates import choose_candidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw() -> dict[str, float]:
    return {"logloss": 2.0, "rps": 1.5, "brier": 0.8}


def _better() -> dict[str, float]:
    return {"logloss": 1.5, "rps": 1.2, "brier": 0.6}


def _worse() -> dict[str, float]:
    return {"logloss": 2.5, "rps": 1.8, "brier": 1.0}


# ---------------------------------------------------------------------------
# 1. Raw wins when all candidates fail product gate
# ---------------------------------------------------------------------------

def test_raw_wins_all_cross_product():
    decision = choose_candidate(
        candidate_metrics={"bias_tigge": _better()},
        raw_metrics=_raw(),
        improvement_lcb={"bias_tigge": 0.1},
        catastrophic={"bias_tigge": False},
        target_product="mx2t3",
        candidate_products={"bias_tigge": "mx2t6"},  # wrong product
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True
    assert "bias_tigge" in decision.refused_cross_product


# ---------------------------------------------------------------------------
# 2. Raw wins when LCB <= 0 for all candidates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lcb", [-0.5, 0.0, -1e-9])
def test_raw_wins_non_positive_lcb(lcb):
    decision = choose_candidate(
        candidate_metrics={"bias_v1": _better()},
        raw_metrics=_raw(),
        improvement_lcb={"bias_v1": lcb},
        catastrophic={"bias_v1": False},
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t3"},
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True


# ---------------------------------------------------------------------------
# 3. Raw wins when candidate does not achieve >= 2/3 proper-score wins
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cand_scores", [
    # 0 wins
    {"logloss": 2.5, "rps": 1.6, "brier": 0.9},
    # 1 win (logloss only)
    {"logloss": 1.5, "rps": 1.6, "brier": 0.9},
    # 1 win (rps only)
    {"logloss": 2.1, "rps": 1.2, "brier": 0.9},
    # 1 win (brier only)
    {"logloss": 2.1, "rps": 1.6, "brier": 0.6},
])
def test_raw_wins_insufficient_proper_score_wins(cand_scores):
    decision = choose_candidate(
        candidate_metrics={"bias_v1": cand_scores},
        raw_metrics=_raw(),
        improvement_lcb={"bias_v1": 0.1},
        catastrophic={"bias_v1": False},
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t3"},
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True


# ---------------------------------------------------------------------------
# 4. Raw wins when candidate is catastrophic
# ---------------------------------------------------------------------------

def test_raw_wins_catastrophic_candidate():
    decision = choose_candidate(
        candidate_metrics={"bias_v1": _better()},
        raw_metrics=_raw(),
        improvement_lcb={"bias_v1": 0.15},
        catastrophic={"bias_v1": True},
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t3"},
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True


# ---------------------------------------------------------------------------
# 5. Raw wins when candidate pool is empty
# ---------------------------------------------------------------------------

def test_raw_wins_empty_candidate_pool():
    decision = choose_candidate(
        candidate_metrics={},
        raw_metrics=_raw(),
        improvement_lcb={},
        catastrophic={},
        target_product="mx2t3",
        candidate_products={},
    )
    assert decision.chosen == "raw"
    assert decision.raw_is_default is True


# ---------------------------------------------------------------------------
# 6. Correct candidate wins when exactly one passes all gates
# ---------------------------------------------------------------------------

def test_candidate_wins_when_all_gates_pass():
    decision = choose_candidate(
        candidate_metrics={"bias_v1": _better()},
        raw_metrics=_raw(),
        improvement_lcb={"bias_v1": 0.05},
        catastrophic={"bias_v1": False},
        target_product="mx2t3",
        candidate_products={"bias_v1": "mx2t3"},
    )
    assert decision.chosen == "bias_v1"
    assert decision.raw_is_default is False
    assert not decision.refused_cross_product


# ---------------------------------------------------------------------------
# 7. load_bucket_residuals returns empty list when no rows match the bucket
# ---------------------------------------------------------------------------

def _make_test_db_with_snapshots(conn: sqlite3.Connection, lead_hours_list: list[float]) -> None:
    """Create minimal ensemble_snapshots + settlement_outcomes in an in-memory DB."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ensemble_snapshots (
            city TEXT, dataset_id TEXT, temperature_metric TEXT, lead_hours REAL,
            target_date TEXT, members_json TEXT, members_unit TEXT,
            available_at TEXT, issue_time TEXT, authority TEXT,
            contributes_to_target_extrema INTEGER,
            boundary_ambiguous INTEGER DEFAULT 0,
            training_allowed INTEGER DEFAULT 1,
            causality_status TEXT DEFAULT 'OK'
        );
        CREATE TABLE IF NOT EXISTS settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, authority TEXT
        );
    """)
    for i, lh in enumerate(lead_hours_list):
        td = f"2025-07-{(i % 28) + 1:02d}"
        conn.execute(
            "INSERT INTO ensemble_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("TestCity", "ecmwf_opendata_mx2t3_local_calendar_day_max", "high",
             lh, td, json.dumps([20.0, 21.0, 22.0]), "C",
             "2025-07-01T06:00:00", "2025-07-01T00:00:00", "VERIFIED",
             1, 0, 1, "OK"),
        )
        conn.execute(
            "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?)",
            ("TestCity", td, "high", 21.5, "VERIFIED"),
        )
    conn.commit()


def test_load_bucket_residuals_empty_for_nonmatching_bucket():
    """Requesting L96_plus on a DB with only lead-6 rows returns empty list."""
    from src.calibration.ens_bias_repo import load_bucket_residuals

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_test_db_with_snapshots(conn, [6.0, 12.0, 18.0])  # all L00_24

    result = load_bucket_residuals(
        conn,
        city="TestCity",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        metric="high",
        lead_bucket_filter="L96_plus",
        require_verified=True,
    )
    assert result == [], f"Expected empty list for L96_plus, got {result}"
    conn.close()


# ---------------------------------------------------------------------------
# 8. Lead-6 and lead-48 rows never appear in the same bucket query result
# ---------------------------------------------------------------------------

def test_lead_6_and_lead_48_never_in_same_bucket_query():
    """A DB with both lead-6 (L00_24) and lead-48 (L48_96) rows:
    querying L00_24 should only return lead-6 residuals;
    querying L48_96 should only return lead-48 residuals;
    the sets must be disjoint in terms of which rows contribute.
    """
    from src.calibration.ens_bias_repo import load_bucket_residuals

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Mix of lead-6 (L00_24) and lead-48 (L48_96) rows with DIFFERENT settlement values
    # so we can distinguish which residuals come from which lead.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ensemble_snapshots (
            city TEXT, dataset_id TEXT, temperature_metric TEXT, lead_hours REAL,
            target_date TEXT, members_json TEXT, members_unit TEXT,
            available_at TEXT, issue_time TEXT, authority TEXT,
            contributes_to_target_extrema INTEGER,
            boundary_ambiguous INTEGER DEFAULT 0,
            training_allowed INTEGER DEFAULT 1,
            causality_status TEXT DEFAULT 'OK'
        );
        CREATE TABLE IF NOT EXISTS settlement_outcomes (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            settlement_value REAL, authority TEXT
        );
    """)
    # lead-6 rows → members mean = 20, settlement = 15 → residual = +5
    # lead-48 rows → members mean = 30, settlement = 15 → residual = +15
    # (different dates to avoid deduplication by target_date)
    for td, lh, members_mean, settlement in [
        ("2025-06-01", 6.0,  20.0, 15.0),
        ("2025-06-02", 6.0,  20.0, 15.0),
        ("2025-07-01", 48.0, 30.0, 15.0),
        ("2025-07-02", 48.0, 30.0, 15.0),
    ]:
        members = [members_mean - 0.5, members_mean, members_mean + 0.5]
        conn.execute(
            "INSERT INTO ensemble_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("TestCity", "ecmwf_opendata_mx2t3_local_calendar_day_max", "high",
             lh, td, json.dumps(members), "C",
             f"{td}T06:00:00", f"{td}T00:00:00", "VERIFIED",
             1, 0, 1, "OK"),
        )
        conn.execute(
            "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?)",
            ("TestCity", td, "high", settlement, "VERIFIED"),
        )
    conn.commit()

    short_lead_residuals = load_bucket_residuals(
        conn,
        city="TestCity",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        metric="high",
        lead_bucket_filter="L00_24",
        require_verified=True,
    )
    long_lead_residuals = load_bucket_residuals(
        conn,
        city="TestCity",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        metric="high",
        lead_bucket_filter="L48_96",
        require_verified=True,
    )

    # Verify we got rows for each bucket
    assert len(short_lead_residuals) > 0, "Expected L00_24 residuals for lead-6 rows"
    assert len(long_lead_residuals) > 0, "Expected L48_96 residuals for lead-48 rows"

    # Residuals must be from different populations (approx. +5 vs +15)
    import statistics
    short_mean = statistics.fmean(short_lead_residuals)
    long_mean = statistics.fmean(long_lead_residuals)

    # Short-lead rows should NOT contain the long-lead residual magnitude and vice-versa
    # (approx 5.0 vs 15.0 — no mixing means means are far apart)
    assert abs(short_mean - long_mean) > 5.0, (
        f"Short-lead mean={short_mean:.2f} and long-lead mean={long_mean:.2f} are too close "
        f"— bucket filter may be mixing leads (pooling bug not fixed)."
    )

    conn.close()
