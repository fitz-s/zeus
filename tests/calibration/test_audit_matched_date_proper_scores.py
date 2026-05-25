# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: Zeus #64 eval tool — antibody for §4.1 confound
#   (disjoint-domain scoring invalidated the raw-vs-ft comparison).
#   Tests prove the _intersect_distributions() layer is the antibody.
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Relationship tests for audit_matched_date_proper_scores._intersect_distributions()
#   Synthetic fixture: 4 dists per family, 2 overlapping keys, 2 disjoint →
#   assert len(matched) == 2, keys identical in both arms,
#   scoring only touches matched pairs.
# Reuse: synthetic data only; no external dependencies; safe to run in isolation
"""Unit tests for scripts/audit_matched_date_proper_scores.py — matched-intersection logic."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# Make the scripts directory importable
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from audit_matched_date_proper_scores import (  # noqa: E402
    _intersect_distributions,
    _aggregate_metrics,
    _brier_dist,
    _logloss_dist,
    _rps_dist,
    _pit_u,
    _cohort_filter,
    MIN_MATCHED_FLOOR,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_dist(
    city: str,
    target_date: str,
    lead_days: float,
    outcome_idx: int = 1,
    n_bins: int = 90,
    p_at_outcome: float = 0.6,
) -> dict:
    """Create a synthetic distribution dict for testing."""
    p_vec = np.ones(n_bins, dtype=float) * (1 - p_at_outcome) / (n_bins - 1)
    p_vec[outcome_idx] = p_at_outcome
    # Ensure sums to 1 exactly
    p_vec = p_vec / p_vec.sum()
    return {
        "decision_group_id": f"{city}:{target_date}:{lead_days}",
        "city": city,
        "cluster": city,
        "cycle": "00",
        "season": "summer",
        "lead_days": lead_days,
        "temperature_metric": "high",
        "target_date": target_date,
        "event_key": (city, target_date, lead_days),
        "p_raw_vec": p_vec,
        "outcome_idx": outcome_idx,
        "range_labels": [f"bin_{i}" for i in range(n_bins)],
        "unit": "°F",
        "n_bins": n_bins,
    }


# ---------------------------------------------------------------------------
# Core: intersection correctness
# ---------------------------------------------------------------------------

class TestIntersectDistributions:
    """Antibody for §4.1 confound: proves matched-intersection is correct."""

    def _build_fixture(self):
        """
        4 raw dists, 4 ft dists.
        Keys (London, 2025-01-01, 0.0) and (Tokyo, 2025-01-02, 1.0) overlap.
        Keys (Berlin, 2025-01-03, 0.0) is raw-only.
        Keys (Paris, 2025-01-04, 2.0) is ft-only.
        """
        raw_dists = [
            _make_dist("London", "2025-01-01", 0.0, outcome_idx=5, p_at_outcome=0.7),
            _make_dist("Tokyo",  "2025-01-02", 1.0, outcome_idx=3, p_at_outcome=0.5),
            _make_dist("Berlin", "2025-01-03", 0.0, outcome_idx=2, p_at_outcome=0.4),
            # Berlin appears only in raw
        ]
        ft_dists = [
            _make_dist("London", "2025-01-01", 0.0, outcome_idx=5, p_at_outcome=0.75),
            _make_dist("Tokyo",  "2025-01-02", 1.0, outcome_idx=3, p_at_outcome=0.55),
            _make_dist("Paris",  "2025-01-04", 2.0, outcome_idx=1, p_at_outcome=0.6),
            # Paris appears only in ft
        ]
        return raw_dists, ft_dists

    def test_intersection_count(self):
        """Exactly 2 overlapping keys → matched lists have length 2."""
        raw_dists, ft_dists = self._build_fixture()
        matched_raw, matched_ft = _intersect_distributions(raw_dists, ft_dists)
        assert len(matched_raw) == 2, (
            f"Expected 2 matched pairs, got {len(matched_raw)}. "
            "Disjoint-domain events must be excluded."
        )
        assert len(matched_ft) == 2

    def test_intersection_keys_identical(self):
        """Every matched pair must share the same event_key — head-to-head guarantee."""
        raw_dists, ft_dists = self._build_fixture()
        matched_raw, matched_ft = _intersect_distributions(raw_dists, ft_dists)
        for r, f in zip(matched_raw, matched_ft):
            assert r["event_key"] == f["event_key"], (
                f"Key mismatch: raw={r['event_key']} ft={f['event_key']}. "
                "Matched arms must correspond to the same forecast event."
            )

    def test_raw_only_key_excluded(self):
        """Berlin (raw-only) must NOT appear in matched output."""
        raw_dists, ft_dists = self._build_fixture()
        matched_raw, _ = _intersect_distributions(raw_dists, ft_dists)
        cities = {d["city"] for d in matched_raw}
        assert "Berlin" not in cities, (
            "Berlin (raw-only key) must be excluded from matched output."
        )

    def test_ft_only_key_excluded(self):
        """Paris (ft-only) must NOT appear in matched output."""
        raw_dists, ft_dists = self._build_fixture()
        _, matched_ft = _intersect_distributions(raw_dists, ft_dists)
        cities = {d["city"] for d in matched_ft}
        assert "Paris" not in cities, (
            "Paris (ft-only key) must be excluded from matched output."
        )

    def test_matched_cities_correct(self):
        """Only London and Tokyo appear in both arms."""
        raw_dists, ft_dists = self._build_fixture()
        matched_raw, matched_ft = _intersect_distributions(raw_dists, ft_dists)
        raw_cities = {d["city"] for d in matched_raw}
        ft_cities = {d["city"] for d in matched_ft}
        assert raw_cities == {"London", "Tokyo"}
        assert ft_cities == {"London", "Tokyo"}

    def test_empty_raw(self):
        """Empty raw → zero matched pairs."""
        _, ft_dists = self._build_fixture()
        matched_raw, matched_ft = _intersect_distributions([], ft_dists)
        assert len(matched_raw) == 0
        assert len(matched_ft) == 0

    def test_empty_ft(self):
        """Empty ft → zero matched pairs."""
        raw_dists, _ = self._build_fixture()
        matched_raw, matched_ft = _intersect_distributions(raw_dists, [])
        assert len(matched_raw) == 0
        assert len(matched_ft) == 0

    def test_fully_disjoint(self):
        """Completely disjoint domains → zero matched pairs (§4.1 confound scenario)."""
        raw_dists = [_make_dist("Berlin", "2024-01-01", 0.0)]
        ft_dists  = [_make_dist("Paris",  "2025-06-01", 3.0)]
        matched_raw, matched_ft = _intersect_distributions(raw_dists, ft_dists)
        assert len(matched_raw) == 0, (
            "Fully disjoint domains must produce zero matched pairs. "
            "This is the §4.1 confound scenario."
        )

    def test_fully_overlapping(self):
        """Fully overlapping domains → all pairs matched."""
        raw_dists = [
            _make_dist("London", "2025-01-01", 0.0),
            _make_dist("Tokyo",  "2025-01-02", 1.0),
        ]
        ft_dists = [
            _make_dist("London", "2025-01-01", 0.0),
            _make_dist("Tokyo",  "2025-01-02", 1.0),
        ]
        matched_raw, matched_ft = _intersect_distributions(raw_dists, ft_dists)
        assert len(matched_raw) == 2

    def test_lead_days_is_part_of_key(self):
        """Same (city, target_date) but different lead_days are NOT matched."""
        raw_dists = [_make_dist("London", "2025-01-01", 0.0)]
        ft_dists  = [_make_dist("London", "2025-01-01", 1.0)]  # different lead_days
        matched_raw, matched_ft = _intersect_distributions(raw_dists, ft_dists)
        assert len(matched_raw) == 0, (
            "lead_days must be part of the intersection key. "
            "Different lead forecasts for the same target_date are NOT the same event."
        )


# ---------------------------------------------------------------------------
# Scoring functions: numerical correctness
# ---------------------------------------------------------------------------

class TestScoringFunctions:

    def test_brier_perfect_prediction(self):
        """P(actual) = 1.0 → Brier = 0 for that bin, but other bins contribute."""
        n = 90
        p = np.zeros(n)
        p[5] = 1.0  # perfect
        score = _brier_dist(p, outcome_idx=5)
        assert score == pytest.approx(0.0, abs=1e-10)

    def test_brier_worst_case(self):
        """All probability on wrong bin → Brier = p_wrong^2 + (1-0)^2."""
        n = 90
        p = np.zeros(n)
        p[0] = 1.0  # all mass at bin 0
        score = _brier_dist(p, outcome_idx=5)  # outcome is bin 5
        # Contribution: (1-0)^2 at bin 0, (0-1)^2 at bin 5, 0 elsewhere
        assert score == pytest.approx(2.0, abs=1e-10)

    def test_logloss_perfect(self):
        """P(actual bin) = 1.0 → LogLoss ≈ 0."""
        n = 90
        p = np.zeros(n)
        p[3] = 1.0
        ll = _logloss_dist(p, outcome_idx=3)
        assert ll == pytest.approx(0.0, abs=1e-6)

    def test_logloss_near_zero(self):
        """P(actual bin) near 0 → large LogLoss."""
        n = 90
        p = np.ones(n) / n  # uniform
        ll = _logloss_dist(p, outcome_idx=0)
        assert ll == pytest.approx(-np.log(1.0 / n), rel=1e-6)

    def test_rps_perfect(self):
        """All mass at outcome bin → RPS = 0."""
        n = 90
        p = np.zeros(n)
        p[0] = 1.0
        rps = _rps_dist(p, outcome_idx=0)
        assert rps == pytest.approx(0.0, abs=1e-10)

    def test_pit_monotone_in_outcome(self):
        """PIT u_i = F(Y) = cumsum(p)[outcome_idx] is monotone in outcome_idx."""
        n = 20
        p = np.ones(n) / n  # uniform bins
        pits = [_pit_u(p, i) for i in range(n)]
        for i in range(1, n):
            assert pits[i] >= pits[i - 1]

    def test_aggregate_metrics_mean(self):
        """_aggregate_metrics returns mean Brier across distributions."""
        d1 = _make_dist("A", "2025-01-01", 0.0, outcome_idx=0, p_at_outcome=1.0)
        d2 = _make_dist("B", "2025-01-02", 0.0, outcome_idx=1, p_at_outcome=1.0)
        m = _aggregate_metrics([d1, d2])
        assert m["n"] == 2
        assert m["brier"] == pytest.approx(0.0, abs=1e-10)

    def test_aggregate_empty(self):
        """Empty distribution list → n=0, no crash."""
        m = _aggregate_metrics([])
        assert m["n"] == 0
        assert "brier" not in m


# ---------------------------------------------------------------------------
# Floor constant
# ---------------------------------------------------------------------------

class TestFloor:
    def test_min_floor_positive(self):
        assert MIN_MATCHED_FLOOR > 0

    def test_min_floor_value(self):
        """Floor is set to 30 per task spec."""
        assert MIN_MATCHED_FLOOR == 30


# ---------------------------------------------------------------------------
# Scoring only touches matched set (relationship test)
# ---------------------------------------------------------------------------

class TestMatchedScoringIsolation:
    """Proves scoring is computed only on matched events — not on the full domains."""

    def test_scores_differ_raw_vs_ft_on_matched(self):
        """
        Two matched events: raw has p_at_outcome=0.4, ft has p_at_outcome=0.8.
        Aggregate metrics on matched arms must reflect those values, not the
        disjoint extras.
        """
        raw_dists = [
            _make_dist("London", "2025-01-01", 0.0, outcome_idx=5, p_at_outcome=0.4),
            _make_dist("Berlin", "2025-01-03", 0.0, outcome_idx=2, p_at_outcome=0.9),  # raw-only
        ]
        ft_dists = [
            _make_dist("London", "2025-01-01", 0.0, outcome_idx=5, p_at_outcome=0.8),
            _make_dist("Paris",  "2025-01-04", 2.0, outcome_idx=1, p_at_outcome=0.9),  # ft-only
        ]
        matched_raw, matched_ft = _intersect_distributions(raw_dists, ft_dists)
        assert len(matched_raw) == 1  # only London

        m_raw = _aggregate_metrics(matched_raw)
        m_ft = _aggregate_metrics(matched_ft)

        # ft has higher p_actual → lower Brier, lower LogLoss
        assert m_ft["p_actual"] > m_raw["p_actual"], (
            "ft arm has higher p_actual on matched London event"
        )
        assert m_ft["brier"] < m_raw["brier"], (
            "ft arm should have lower Brier (higher p_actual) on matched event"
        )
        assert m_ft["logloss"] < m_raw["logloss"]

    def test_disjoint_domain_zero_matched_score_impossible(self):
        """
        Fully disjoint domains (§4.1 confound scenario):
        matched lists are empty → _aggregate_metrics returns n=0,
        no scores computed (no division-by-zero, no misleading delta).
        """
        raw_dists = [_make_dist("Berlin", "2024-01-01", 0.0)]
        ft_dists  = [_make_dist("Paris",  "2025-06-01", 3.0)]
        matched_raw, matched_ft = _intersect_distributions(raw_dists, ft_dists)
        m_raw = _aggregate_metrics(matched_raw)
        m_ft  = _aggregate_metrics(matched_ft)
        assert m_raw["n"] == 0
        assert m_ft["n"]  == 0
        assert "brier" not in m_raw, "No Brier should be computed on empty set"
