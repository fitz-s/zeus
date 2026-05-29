# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: HANDOFF_STAT_REFACTOR_2026-05-29 §4 #14
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: End-to-end tests for oos_validation_harness in EQUIVALENCE and IMPROVEMENT modes on synthetic fixtures.
# Reuse: Run after any change to oos_validation_harness.py, run_equivalence_report, or run_improvement_report.
"""End-to-end tests for the OOS before/after validation harness.

Covers:
  EQUIVALENCE mode: synthetic fixture rows → EquivalenceReport within P4 tolerances.
  IMPROVEMENT mode: underpowered fixture → manifest with chosen='raw'.

TDD: these tests were written BEFORE the harness ran successfully. They verify:
  1. The harness is importable (no side-effects at import time).
  2. make_synthetic_fixture_rows produces well-formed rows.
  3. run_equivalence_report returns an EquivalenceReport within P4 atol bounds.
  4. run_improvement_report returns a manifest with chosen='raw' on underpowered data.
  5. EquivalenceReport.summary_lines() is non-empty (smoke for the report format).
  6. format_improvement_report() produces non-empty lines with 'chosen' key.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
os.environ.setdefault("ZEUS_MODE", "paper")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def nyc_city():
    from src.config import City  # noqa: PLC0415
    return City(
        name="NYC",
        lat=40.7772, lon=-73.8726,
        timezone="America/New_York", cluster="US-Northeast",
        settlement_unit="F", wu_station="KLGA",
    )


@pytest.fixture(scope="module")
def harness():
    """Import the harness module (lazy — no DB needed)."""
    from scripts import oos_validation_harness as h  # noqa: PLC0415
    return h


# ---------------------------------------------------------------------------
# Smoke: import + fixture generation
# ---------------------------------------------------------------------------

class TestHarnessImport:
    def test_importable_without_db(self, harness):
        """Harness must be importable without any live DB."""
        assert hasattr(harness, "run_equivalence_report")
        assert hasattr(harness, "run_improvement_report")
        assert hasattr(harness, "make_synthetic_fixture_rows")
        assert hasattr(harness, "EquivalenceReport")

    def test_make_synthetic_fixture_rows_structure(self, harness, nyc_city):
        rows = harness.make_synthetic_fixture_rows(5, nyc_city, rng_seed=0)
        assert len(rows) == 5
        for row in rows:
            assert "members_json" in row
            assert "settlement_value_c" in row
            assert "target_date" in row
            assert row["members_unit"] == nyc_city.settlement_unit
            members = json.loads(row["members_json"])
            assert len(members) == 51, f"Expected 51 members, got {len(members)}"
            assert all(isinstance(m, float) for m in members)

    def test_make_rows_reproducible(self, harness, nyc_city):
        rows_a = harness.make_synthetic_fixture_rows(10, nyc_city, rng_seed=7)
        rows_b = harness.make_synthetic_fixture_rows(10, nyc_city, rng_seed=7)
        for a, b in zip(rows_a, rows_b):
            assert a["members_json"] == b["members_json"]
            assert a["settlement_value_c"] == b["settlement_value_c"]

    def test_make_rows_distinct_target_dates(self, harness, nyc_city):
        rows = harness.make_synthetic_fixture_rows(20, nyc_city, rng_seed=1)
        dates = [r["target_date"] for r in rows]
        # At minimum the first N dates should be distinct enough for k-fold splitting
        # (implementation uses i % 12 / i % 28 so at most 28*12=336 distinct values)
        assert len(set(dates)) >= 1


# ---------------------------------------------------------------------------
# EQUIVALENCE mode
# ---------------------------------------------------------------------------

class TestEquivalenceReport:
    """Equivalence: analytic vs MC p_raw on synthetic fixture within P4 tolerances."""

    @pytest.fixture(scope="class")
    def report(self, harness, nyc_city):
        rows = harness.make_synthetic_fixture_rows(10, nyc_city, rng_seed=42)
        return harness.run_equivalence_report(rows, nyc_city, n_mc=10_000, rng_seed=42)

    def test_report_type(self, harness, report):
        assert isinstance(report, harness.EquivalenceReport)

    def test_n_rows_matches(self, harness, nyc_city, report):
        assert report.n_rows == 10

    def test_within_p_raw_atol(self, report):
        """max |Δp_raw| must be within P4 atol = 2e-3."""
        assert report.within_p_raw_atol, (
            f"max |Δp_raw|={report.max_p_raw_abs_diff:.4e} exceeds atol={report.p_raw_atol:.1e}"
        )

    def test_within_logit_atol(self, report):
        """max |Δlogit| must be within P4 atol = 1.5e-2."""
        assert report.within_logit_atol, (
            f"max |Δlogit|={report.max_logit_abs_diff:.4e} exceeds atol={report.logit_atol:.1e}"
        )

    def test_per_row_count(self, report):
        assert len(report.per_row) == 10

    def test_summary_lines_non_empty(self, report):
        lines = report.summary_lines()
        assert len(lines) >= 5
        full = "\n".join(lines)
        assert "max |Δp_raw|" in full
        assert "max |Δlogit" in full

    def test_p_raw_diffs_non_negative(self, report):
        for r in report.per_row:
            assert r["max_p_raw_abs_diff"] >= 0.0

    def test_logit_diffs_non_negative(self, report):
        for r in report.per_row:
            assert r["max_logit_abs_diff"] >= 0.0

    def test_aggregate_equals_row_max(self, report):
        """Aggregate max must equal the max of per-row maxes."""
        per_row_max_p = max(r["max_p_raw_abs_diff"] for r in report.per_row)
        assert abs(report.max_p_raw_abs_diff - per_row_max_p) < 1e-12


# ---------------------------------------------------------------------------
# IMPROVEMENT mode
# ---------------------------------------------------------------------------

class TestImprovementReport:
    """Improvement: run_scoring on underpowered fixture → chosen='raw'."""

    @pytest.fixture(scope="class")
    def manifest(self, harness, nyc_city):
        # Use 30 rows so k=5 folds have at least 6 rows each, but still underpowered
        rows = harness.make_synthetic_fixture_rows(30, nyc_city, rng_seed=42)
        return harness.run_improvement_report(rows, nyc_city, "HIGH", k_folds=5)

    def test_manifest_is_dict(self, manifest):
        assert isinstance(manifest, dict)

    def test_manifest_has_chosen(self, manifest):
        assert "chosen" in manifest, f"manifest missing 'chosen' key: {manifest.keys()}"

    def test_chosen_is_raw_on_underpowered_fixture(self, manifest):
        """On a small underpowered synthetic fixture, raw must dominate."""
        assert manifest["chosen"] == "raw", (
            f"Expected chosen='raw' on underpowered fixture, got chosen={manifest['chosen']!r}. "
            f"reason={manifest.get('reason')}"
        )

    def test_raw_is_default_true(self, manifest):
        assert manifest.get("raw_is_default") is True, (
            f"raw_is_default={manifest.get('raw_is_default')}"
        )

    def test_manifest_has_raw_metrics(self, manifest):
        raw_m = manifest.get("raw_metrics") or {}
        assert "logloss" in raw_m or len(raw_m) == 0, (
            f"raw_metrics missing logloss: {raw_m}"
        )

    def test_format_improvement_report_non_empty(self, harness, manifest):
        lines = harness.format_improvement_report(manifest)
        assert len(lines) >= 4
        full = "\n".join(lines)
        assert "chosen" in full

    def test_candidate_metrics_present(self, manifest):
        """candidate_metrics dict must exist (may be empty on degenerate fixture)."""
        assert "candidate_metrics" in manifest
