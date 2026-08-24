# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/tier0_selection_lift_preregistration_2026-08-24.md
#   (FROZEN) — reversal_plan_tier0_2026-08-24.md item 7.
"""Tests for src/analysis/selection_lift.py (pure computation, no DB access)."""
from __future__ import annotations

import numpy as np
import pytest

from src.analysis.selection_lift import (
    Candidate,
    OpportunitySet,
    build_observations,
    collapse_duplicates_and_complements,
    city_date_bootstrap_ci,
    date_block_sensitivity,
    evaluation_is_locked,
    governing_ci,
    permutation_test,
    SelectionLiftObservation,
)


# ---------------------------------------------------------------------------
# (a) hand-computed 2-set fixture -> exact mean(L).
# ---------------------------------------------------------------------------


class TestBuildObservationsHandComputed:
    def test_two_set_fixture_matches_hand_computed_L(self):
        set1 = OpportunitySet(
            city="Denver",
            date="2026-08-01",
            candidates=(
                Candidate(id="s1", side="yes", p0=0.20, lead_bucket="day0", eligible=True, selected=True, y=1, market_key="m1"),
                Candidate(id="c1", side="yes", p0=0.18, lead_bucket="day0", eligible=True, selected=False, y=0, market_key="m2"),
                Candidate(id="c2", side="yes", p0=0.22, lead_bucket="day0", eligible=True, selected=False, y=1, market_key="m3"),
            ),
        )
        set2 = OpportunitySet(
            city="Miami",
            date="2026-08-02",
            candidates=(
                Candidate(id="s2", side="no", p0=0.50, lead_bucket="day1", eligible=True, selected=True, y=0, market_key="m4"),
                Candidate(id="c3", side="no", p0=0.48, lead_bucket="day1", eligible=True, selected=False, y=1, market_key="m5"),
            ),
        )
        result = build_observations([set1, set2])
        assert len(result.observations) == 2

        obs1 = next(o for o in result.observations if o.city_date_key == "Denver|2026-08-01")
        # treatment = 1 - 0.20 = 0.80; controls: (0-0.18)=-0.18, (1-0.22)=0.78 -> mean 0.30
        assert obs1.treatment == pytest.approx(0.80)
        assert obs1.control_mean == pytest.approx(0.30)
        assert obs1.L == pytest.approx(0.50)

        obs2 = next(o for o in result.observations if o.city_date_key == "Miami|2026-08-02")
        # treatment = 0 - 0.50 = -0.50; control: (1-0.48)=0.52
        assert obs2.treatment == pytest.approx(-0.50)
        assert obs2.control_mean == pytest.approx(0.52)
        assert obs2.L == pytest.approx(-1.02)

        mean_L = (obs1.L + obs2.L) / 2
        assert mean_L == pytest.approx(-0.26)
        assert result.coverage["included"] == 2


# ---------------------------------------------------------------------------
# (b) permutation null + (c) known-positive.
# ---------------------------------------------------------------------------


def _make_observation(city_date_key: str, pool: tuple[float, ...]) -> SelectionLiftObservation:
    treatment = pool[0]
    control_mean = float(np.mean(pool[1:])) if len(pool) > 1 else float("nan")
    city, date = city_date_key.split("|")
    return SelectionLiftObservation(
        city_date_key=city_date_key,
        city=city,
        date=date,
        treatment=treatment,
        control_mean=control_mean,
        L=treatment - control_mean,
        n_control=len(pool) - 1,
        pool_residuals=pool,
    )


class TestPermutationTestNull:
    def test_known_null_fixture_p_is_not_small(self):
        rng = np.random.default_rng(42)
        observations = []
        for i in range(60):
            pool = tuple(float(v) for v in rng.normal(0.0, 0.1, size=5))
            observations.append(_make_observation(f"City{i}|2026-08-{(i % 28) + 1:02d}", pool))

        result = permutation_test(observations, n_perm=2000, seed=7)
        assert result.p_value > 0.01


class TestPermutationTestKnownPositive:
    def test_known_positive_fixture_p_is_small(self):
        # Selected always beats controls by a wide, consistent margin.
        observations = [
            _make_observation(f"City{i}|2026-08-{(i % 28) + 1:02d}", (0.9, -0.1, -0.1, -0.1))
            for i in range(30)
        ]
        result = permutation_test(observations, n_perm=2000, seed=11)
        assert result.p_value < 0.01


class TestPermutationTestDeterminism:
    def test_same_seed_same_observations_identical_p(self):
        rng = np.random.default_rng(99)
        observations = []
        for i in range(20):
            pool = tuple(float(v) for v in rng.normal(0.05, 0.2, size=4))
            observations.append(_make_observation(f"City{i}|2026-08-{(i % 28) + 1:02d}", pool))

        r1 = permutation_test(observations, n_perm=1000, seed=123)
        r2 = permutation_test(observations, n_perm=1000, seed=123)
        assert r1.p_value == r2.p_value
        assert r1.observed_statistic == r2.observed_statistic

    def test_pool_with_fewer_than_two_members_raises(self):
        obs = _make_observation("City0|2026-08-01", (0.5,))
        with pytest.raises(ValueError):
            permutation_test([obs], n_perm=100, seed=1)


# ---------------------------------------------------------------------------
# (d) duplicate/complement collapse.
# ---------------------------------------------------------------------------


class TestCollapseDuplicatesAndComplements:
    def test_yes_no_complement_same_market_collapses_to_one(self):
        candidates = [
            Candidate(id="y1", side="yes", p0=0.10, lead_bucket="day0", eligible=True, selected=False, y=1, market_key="mkt"),
            Candidate(id="n1", side="no", p0=0.90, lead_bucket="day0", eligible=True, selected=True, y=0, market_key="mkt"),
        ]
        coverage: dict[str, int] = {}
        from collections import defaultdict

        coverage = defaultdict(int)
        survivors = collapse_duplicates_and_complements(candidates, coverage)
        assert len(survivors) == 1
        assert coverage["yes_no_complement_collapsed"] == 1
        # selected member kept when either side is selected.
        assert survivors[0].id == "n1"

    def test_same_side_duplicates_collapse_keeping_selected(self):
        candidates = [
            Candidate(id="a", side="yes", p0=0.10, lead_bucket="day0", eligible=True, selected=False, y=1, market_key="mkt"),
            Candidate(id="b", side="yes", p0=0.10, lead_bucket="day0", eligible=True, selected=True, y=1, market_key="mkt"),
        ]
        from collections import defaultdict

        coverage = defaultdict(int)
        survivors = collapse_duplicates_and_complements(candidates, coverage)
        assert len(survivors) == 1
        assert survivors[0].id == "b"
        assert coverage["same_side_duplicate_collapsed"] == 1

    def test_inconsistent_complement_prices_not_collapsed(self):
        candidates = [
            Candidate(id="y1", side="yes", p0=0.10, lead_bucket="day0", eligible=True, selected=False, y=1, market_key="mkt"),
            Candidate(id="n1", side="no", p0=0.80, lead_bucket="day0", eligible=True, selected=True, y=0, market_key="mkt"),
        ]
        from collections import defaultdict

        coverage = defaultdict(int)
        survivors = collapse_duplicates_and_complements(candidates, coverage)
        assert len(survivors) == 2
        assert coverage["uncollapsed_inconsistent_complement"] == 1


# ---------------------------------------------------------------------------
# (e) price-match window.
# ---------------------------------------------------------------------------


class TestPriceMatchWindow:
    def test_control_beyond_0_05_window_excluded(self):
        opp = OpportunitySet(
            city="Denver",
            date="2026-08-01",
            candidates=(
                Candidate(id="s1", side="yes", p0=0.20, lead_bucket="day0", eligible=True, selected=True, y=1, market_key="m1"),
                Candidate(id="near", side="yes", p0=0.24, lead_bucket="day0", eligible=True, selected=False, y=0, market_key="m2"),
                Candidate(id="far", side="yes", p0=0.26, lead_bucket="day0", eligible=True, selected=False, y=1, market_key="m3"),
            ),
        )
        result = build_observations([opp])
        assert len(result.observations) == 1
        obs = result.observations[0]
        assert obs.n_control == 1
        # only "near" (diff 0.04) included: residual = 0 - 0.24 = -0.24
        assert obs.control_mean == pytest.approx(-0.24)


# ---------------------------------------------------------------------------
# (f) empty control set excluded + counted.
# ---------------------------------------------------------------------------


class TestEmptyMatchedControl:
    def test_empty_control_excluded_and_counted(self):
        opp_empty = OpportunitySet(
            city="Denver",
            date="2026-08-01",
            candidates=(
                Candidate(id="s1", side="yes", p0=0.20, lead_bucket="day0", eligible=True, selected=True, y=1, market_key="m1"),
                # Only other candidate is out of the price window -> no matched control.
                Candidate(id="c1", side="yes", p0=0.90, lead_bucket="day0", eligible=True, selected=False, y=1, market_key="m2"),
            ),
        )
        opp_ok = OpportunitySet(
            city="Miami",
            date="2026-08-02",
            candidates=(
                Candidate(id="s2", side="yes", p0=0.20, lead_bucket="day0", eligible=True, selected=True, y=1, market_key="m3"),
                Candidate(id="c2", side="yes", p0=0.21, lead_bucket="day0", eligible=True, selected=False, y=0, market_key="m4"),
            ),
        )
        result = build_observations([opp_empty, opp_ok])
        assert len(result.observations) == 1
        assert result.observations[0].city_date_key == "Miami|2026-08-02"
        assert result.coverage["empty_matched_control"] == 1
        assert result.coverage["included"] == 1


# ---------------------------------------------------------------------------
# (h) evaluation lock guard.
# ---------------------------------------------------------------------------


class TestEvaluationLock:
    def test_below_stopping_count_is_locked(self):
        assert evaluation_is_locked(99) is True
        assert evaluation_is_locked(0) is True

    def test_at_or_above_stopping_count_is_unlocked(self):
        assert evaluation_is_locked(100) is False
        assert evaluation_is_locked(150) is False

    def test_custom_min_observations(self):
        assert evaluation_is_locked(5, min_observations=10) is True
        assert evaluation_is_locked(10, min_observations=10) is False


# ---------------------------------------------------------------------------
# Bootstrap CI sanity + governing_ci.
# ---------------------------------------------------------------------------


class TestBootstrapCI:
    def test_city_date_and_date_block_ci_are_seeded_deterministic(self):
        rng = np.random.default_rng(5)
        observations = []
        for i in range(20):
            pool = tuple(float(v) for v in rng.normal(0.02, 0.1, size=3))
            date = f"2026-08-{(i % 5) + 1:02d}"
            observations.append(_make_observation(f"City{i}|{date}", pool))

        cd1 = city_date_bootstrap_ci(observations, n_boot=500, seed=1)
        cd2 = city_date_bootstrap_ci(observations, n_boot=500, seed=1)
        assert cd1 == cd2

        db1 = date_block_sensitivity(observations, n_boot=500, seed=1)
        assert db1.n_blocks == 5  # 5 distinct calendar dates, multiple city-dates each

        governing = governing_ci(cd1, db1)
        assert governing in (cd1, db1)
