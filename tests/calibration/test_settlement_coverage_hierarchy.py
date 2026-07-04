# Lifecycle: created=2026-07-04; last_reviewed=2026-07-04; last_reused=never
# Purpose: F1 hierarchical settlement-coverage calibrator — estimator, hierarchy
#   selection, prefix/walk-forward replay, and dedup unit tests for
#   src/calibration/settlement_coverage_hierarchy.py. Pure-module tests only (no DB,
#   no event_reactor_adapter wiring) — the wiring + fail-closed + byte-identical
#   flag-OFF tests live in tests/engine/test_event_reactor_settlement_coverage_hierarchy.py.
"""Unit tests for the F1 hierarchical settlement-coverage calibrator.

Authority: settled-chain audit z~=-4.5 (decision-time q=0.84 bucket realizes 0.44,
n=36, opening_inertia buy_no). Sanity anchors from the audit are encoded verbatim
as tests (16/36 at q_bar=0.844 -> posterior mean ~=0.446, must shrink hard; 12/17
at q_bar~=0.76 -> upper bound ~=0.85, must NOT actuate).
"""
from __future__ import annotations

import pytest

from src.calibration.settlement_coverage_hierarchy import (
    ExecutablePair,
    HierarchyObservation,
    UNKNOWN_STRATEGY_KEY,
    canonicalize_strategy_key,
    coverage_status_for_cohort,
    dedupe_observations,
    filter_observations_prefix,
    hierarchical_coverage_check,
    is_high_confidence_bucket,
    is_monitored_only_bucket,
    jeffreys_is_unlicensed,
    jeffreys_posterior_mean,
    jeffreys_upper95,
    q_bucket_bounds,
    q_bucket_key,
)


CANONICAL_STRATEGY = "opening_inertia"  # a real CANONICAL_STRATEGY_KEYS member
OTHER_STRATEGY = "settlement_capture"
THIRD_STRATEGY = "center_buy"
FOURTH_STRATEGY = "shoulder_sell"


def _make_obs(
    *,
    n: int,
    wins: int,
    q_raw: float,
    city: str = "Singapore",
    metric: str = "high",
    band_template: str = "T>=31C",
    direction: str = "buy_no",
    strategy_key: str = CANONICAL_STRATEGY,
    date_prefix: str = "2026-06",
    settlement_time_prefix: str = "2026-06",
) -> list[HierarchyObservation]:
    """Build ``n`` synthetic observations with exactly ``wins`` wins, each on a
    DISTINCT target_date/settlement_time so date/city-metric diversity checks can
    be satisfied trivially when the caller wants them satisfied."""
    obs = []
    for i in range(n):
        day = i + 1
        obs.append(
            HierarchyObservation(
                condition_or_market_id=f"cond-{city}-{i}",
                target_date=f"{date_prefix}-{day:02d}",
                city=city,
                metric=metric,
                band_template=band_template,
                direction=direction,
                strategy_key=strategy_key,
                q_raw=q_raw,
                won=(i < wins),
                settlement_time=f"{settlement_time_prefix}-{day:02d}T12:00:00Z",
            )
        )
    return obs


# ---------------------------------------------------------------------------
# 1. Estimator unit tests
# ---------------------------------------------------------------------------


class TestJeffreysEstimator:
    def test_audit_anchor_16_of_36_shrinks_hard(self):
        # 16/36 at q_bar=0.844 -> posterior mean ~=0.446, must be UNLICENSED.
        wins, n = 16, 36
        q_bar = 0.844
        mean = jeffreys_posterior_mean(wins, n)
        assert mean == pytest.approx(0.4459, abs=1e-3)
        assert jeffreys_is_unlicensed(wins, n, q_bar) is True

    def test_audit_anchor_12_of_17_does_not_actuate(self):
        # 12/17 at q_bar~=0.76 -> upper bound ~=0.85, must NOT actuate (not unlicensed).
        wins, n, q_bar = 12, 17, 0.76
        upper = jeffreys_upper95(wins, n)
        assert upper == pytest.approx(0.855, abs=0.01)
        assert jeffreys_is_unlicensed(wins, n, q_bar) is False

    def test_zero_wins_avoids_degeneracy(self):
        # Jeffreys posterior mean must NOT collapse to a literal 0 with 0 wins.
        mean = jeffreys_posterior_mean(0, 30)
        assert 0.0 < mean < 0.05

    def test_all_wins_avoids_degeneracy(self):
        # Jeffreys posterior mean must NOT collapse to a literal 1 with all wins.
        mean = jeffreys_posterior_mean(30, 30)
        assert 0.95 < mean < 1.0

    def test_n29_vs_n30_boundary(self):
        # Same overconfident ratio at n=29 (below min_n) -> INSUFFICIENT_DATA;
        # at n=30 (meets min_n) -> the statistical test actually runs.
        status_29 = coverage_status_for_cohort(wins=13, n=29, claimed_mean_q=0.844, min_n=30)
        status_30 = coverage_status_for_cohort(wins=13, n=30, claimed_mean_q=0.844, min_n=30)
        assert status_29 == "INSUFFICIENT_DATA"
        assert status_30 != "INSUFFICIENT_DATA"

    def test_q_bucket_edge_0_80_exactly(self):
        # q == 0.80 exactly must land in [0.80, 0.85), never [0.75, 0.80).
        assert q_bucket_bounds(0.80) == (0.80, 0.85)
        assert q_bucket_key(0.80) == "0.80-0.85"

    def test_q_bucket_edge_0_95_folds_into_top_bucket(self):
        assert q_bucket_bounds(0.95) == (0.90, 0.95)

    def test_q_bucket_just_below_edge(self):
        lo, hi = q_bucket_bounds(0.7999)
        assert (lo, hi) == (0.75, 0.80)

    def test_high_confidence_bucket_classification(self):
        assert is_high_confidence_bucket(0.75, 0.80) is True
        assert is_high_confidence_bucket(0.90, 0.95) is True
        assert is_high_confidence_bucket(0.70, 0.75) is False

    def test_monitored_bucket_0_70_0_75_never_pooled(self):
        assert is_monitored_only_bucket(0.70, 0.75) is True
        assert is_monitored_only_bucket(0.75, 0.80) is False


# ---------------------------------------------------------------------------
# 2. Hierarchy selection tests
# ---------------------------------------------------------------------------


class TestHierarchySelection:
    def test_exact_cell_licensed_shields_from_broken_parent(self):
        """The climatology lesson test: seed a SHARP, LICENSED exact cell (city,
        metric, band, direction) under a strategy-level cohort that IS overconfident.
        Assert the exact-cell shield applies -- NO shrink, despite the broken parent.
        """
        # Exact cell: 30 obs, ALL won, claimed q_raw ~ 0.84 -> realized 1.0 >= claimed
        # - tol -> LICENSED (calibrated/conservative).
        exact_obs = _make_obs(n=30, wins=30, q_raw=0.84, city="Singapore", metric="high")
        # Broken strategy-level cohort at the SAME bucket/direction/strategy:
        # heavily overconfident (16/36 anchor), but from a DIFFERENT city so it
        # would only ever be reached as a Level-1 STRATEGY_BUCKET parent.
        broken_strategy_obs = _make_obs(
            n=36, wins=16, q_raw=0.844, city="Tokyo", metric="high",
            date_prefix="2026-05", settlement_time_prefix="2026-05",
        )
        observations = exact_obs + broken_strategy_obs
        result = hierarchical_coverage_check(
            city="Singapore", metric="high", band_template="T>=31C", direction="buy_no",
            strategy_key=CANONICAL_STRATEGY, q_raw=0.84, q_lcb_raw=0.80,
            observations=observations,
        )
        assert result.level == "LOCAL_SHIELD"
        assert result.status == "LICENSED"
        assert result.q_exec == pytest.approx(0.84)
        assert result.q_lcb_exec == pytest.approx(0.80)

    def test_exact_cell_insufficient_inherits_parent_shrink(self):
        """Exact cell is thin (n<30) -> falls through to the Level-1 strategy
        cohort, which IS overconfident -> shrink applies."""
        exact_obs = _make_obs(n=5, wins=5, q_raw=0.84, city="Singapore", metric="high")
        # Level-1 strategy cohort: same strategy/direction/bucket, DIFFERENT
        # cities so the exact-cell scope never absorbs them, spanning >=8 dates
        # and >=4 distinct (city, metric) pairs.
        strategy_obs = []
        cities = ["Tokyo", "Manila", "Bangkok", "Hanoi", "Jakarta"]
        for idx in range(36):
            city = cities[idx % len(cities)]
            strategy_obs.append(
                HierarchyObservation(
                    condition_or_market_id=f"cond-strat-{idx}",
                    target_date=f"2026-05-{(idx % 28) + 1:02d}",
                    city=city,
                    metric="high",
                    band_template="T>=31C",
                    direction="buy_no",
                    strategy_key=CANONICAL_STRATEGY,
                    q_raw=0.844,
                    won=(idx < 16),
                    settlement_time=f"2026-05-{(idx % 28) + 1:02d}T12:00:00Z",
                )
            )
        observations = exact_obs + strategy_obs
        result = hierarchical_coverage_check(
            city="Singapore", metric="high", band_template="T>=31C", direction="buy_no",
            strategy_key=CANONICAL_STRATEGY, q_raw=0.84, q_lcb_raw=0.80,
            observations=observations,
        )
        assert result.level == "STRATEGY_BUCKET"
        assert result.status == "UNLICENSED"
        # The strategy-bucket pool absorbs the thin exact-cell observations too
        # (same strategy/direction/bucket) -- 41 obs, 21 wins -> posterior mean
        # (21+0.5)/(41+1) ~= 0.512, still a hard shrink from the claimed 0.84.
        assert result.q_exec == pytest.approx(0.512, abs=0.02)

    def test_level2_single_strategy_dominance_not_eligible(self):
        """Cross-strategy cohort where ONE strategy is 80% of n must NOT be
        eligible for Level 2 (max-share > 70% control)."""
        dominant = _make_obs(n=80, wins=35, q_raw=0.84, strategy_key=CANONICAL_STRATEGY, city="A")
        minor = _make_obs(
            n=20, wins=9, q_raw=0.84, strategy_key=OTHER_STRATEGY, city="B",
            date_prefix="2026-04", settlement_time_prefix="2026-04",
        )
        observations = dominant + minor
        # Query from a THIRD city/strategy combo so only Level-2/3 pooling can fire
        # (exact cell + level-1 strategy cohort are both empty/thin for this query).
        result = hierarchical_coverage_check(
            city="Z", metric="high", band_template="T>=31C", direction="buy_no",
            strategy_key=THIRD_STRATEGY, q_raw=0.84, q_lcb_raw=0.80,
            observations=observations,
        )
        # Level 2 must be rejected (dominance); with only 2 strategies present,
        # Level 3 requires >=3 canonical strategies, so it also fails -> no-op.
        assert result.level is None
        assert result.status == "INSUFFICIENT_DATA"
        assert result.q_exec == pytest.approx(0.84)

    def test_level2_leave_one_strategy_out_failure_not_eligible(self):
        """Two qualifying strategies at n>=20 each, combined n>=80, no dominance
        -- but overconfidence is driven ENTIRELY by one strategy: removing it
        makes the remainder NOT overconfident. Level 2 must reject this cohort."""
        # Strategy A: 40 obs, heavily overconfident on its own (10/40 wins, q=0.84).
        strat_a = _make_obs(
            n=40, wins=10, q_raw=0.84, strategy_key=CANONICAL_STRATEGY, city="A",
            date_prefix="2026-03", settlement_time_prefix="2026-03",
        )
        # Strategy B: 40 obs, PERFECTLY calibrated (34/40 ~ 0.85 >= claimed 0.84 - tol).
        strat_b = _make_obs(
            n=40, wins=34, q_raw=0.84, strategy_key=OTHER_STRATEGY, city="B",
            date_prefix="2026-04", settlement_time_prefix="2026-04",
        )
        observations = strat_a + strat_b
        result = hierarchical_coverage_check(
            city="Z", metric="high", band_template="T>=31C", direction="buy_no",
            strategy_key=THIRD_STRATEGY, q_raw=0.84, q_lcb_raw=0.80,
            observations=observations,
        )
        # Removing strategy A leaves strategy B alone, which is NOT unlicensed ->
        # leave-one-strategy-out fails -> Level 2 not eligible. Only 2 canonical
        # strategies present -> Level 3 (needs >=3) also not eligible -> no-op.
        assert result.level is None
        assert result.status == "INSUFFICIENT_DATA"

    def test_level2_eligible_cross_strategy_shrinks(self):
        """Two balanced, jointly-overconfident canonical strategies -> Level 2
        cross-strategy cohort licenses a shrink for a THIRD strategy's query at
        the same (direction, bucket)."""
        strat_a = _make_obs(
            n=40, wins=17, q_raw=0.84, strategy_key=CANONICAL_STRATEGY, city="A",
            date_prefix="2026-03", settlement_time_prefix="2026-03",
        )
        strat_b = _make_obs(
            n=40, wins=18, q_raw=0.84, strategy_key=OTHER_STRATEGY, city="B",
            date_prefix="2026-04", settlement_time_prefix="2026-04",
        )
        observations = strat_a + strat_b
        result = hierarchical_coverage_check(
            city="Z", metric="high", band_template="T>=31C", direction="buy_no",
            strategy_key=THIRD_STRATEGY, q_raw=0.84, q_lcb_raw=0.80,
            observations=observations,
        )
        assert result.level == "CROSS_STRATEGY"
        assert result.status == "UNLICENSED"
        assert result.q_exec < 0.84

    def test_level3_global_pools_across_direction_and_strategy(self):
        """Three canonical strategies, each n>=20, jointly overconfident, total
        n>=120, pooled ACROSS direction (buy_yes + buy_no) -> Level 3 GLOBAL."""
        strat_a = _make_obs(
            n=40, wins=17, q_raw=0.84, strategy_key=CANONICAL_STRATEGY, city="A",
            direction="buy_no", date_prefix="2026-01", settlement_time_prefix="2026-01",
        )
        strat_b = _make_obs(
            n=40, wins=17, q_raw=0.84, strategy_key=OTHER_STRATEGY, city="B",
            direction="buy_yes", date_prefix="2026-02", settlement_time_prefix="2026-02",
        )
        strat_c = _make_obs(
            n=40, wins=17, q_raw=0.84, strategy_key=THIRD_STRATEGY, city="C",
            direction="buy_no", date_prefix="2026-03", settlement_time_prefix="2026-03",
        )
        observations = strat_a + strat_b + strat_c
        result = hierarchical_coverage_check(
            city="Z", metric="high", band_template="T>=31C", direction="buy_yes",
            strategy_key=FOURTH_STRATEGY, q_raw=0.84, q_lcb_raw=0.80,
            observations=observations,
        )
        assert result.level == "GLOBAL"
        assert result.status == "UNLICENSED"

    def test_unknown_strategy_never_forms_own_cohort(self):
        """An unrecognized strategy_key must canonicalize to UNKNOWN and never
        license a Level-1/1b cohort of its own."""
        assert canonicalize_strategy_key("not_a_real_strategy") == UNKNOWN_STRATEGY_KEY
        unknown_obs = _make_obs(n=40, wins=10, q_raw=0.84, strategy_key="not_a_real_strategy", city="Q")
        result = hierarchical_coverage_check(
            city="Z", metric="high", band_template="T>=31C", direction="buy_no",
            strategy_key="not_a_real_strategy", q_raw=0.84, q_lcb_raw=0.80,
            observations=unknown_obs,
        )
        # No Level-1 cohort can form for an unknown strategy; with < Level-2/3
        # minimums the result is a no-op.
        assert result.level is None
        assert result.status == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# 3. Prefix / walk-forward replay
# ---------------------------------------------------------------------------


class TestPrefixReplay:
    def test_prefix_filter_excludes_future_and_concurrent_settlements(self):
        obs = [
            HierarchyObservation(
                condition_or_market_id="a", target_date="2026-06-01", city="X", metric="high",
                band_template="T", direction="buy_no", strategy_key=CANONICAL_STRATEGY, q_raw=0.8,
                won=True, settlement_time="2026-06-01T12:00:00Z",
            ),
            HierarchyObservation(
                condition_or_market_id="b", target_date="2026-06-02", city="X", metric="high",
                band_template="T", direction="buy_no", strategy_key=CANONICAL_STRATEGY, q_raw=0.8,
                won=True, settlement_time="2026-06-05T12:00:00Z",  # AT/after decision time
            ),
            HierarchyObservation(
                condition_or_market_id="c", target_date="2026-06-03", city="X", metric="high",
                band_template="T", direction="buy_no", strategy_key=CANONICAL_STRATEGY, q_raw=0.8,
                won=True, settlement_time="",  # no settlement time stamp at all
            ),
        ]
        decision_time = "2026-06-05T12:00:00Z"
        admitted = filter_observations_prefix(obs, decision_time)
        assert [o.condition_or_market_id for o in admitted] == ["a"]
        assert all(o.settlement_time < decision_time for o in admitted)

    def test_bucket_shrinks_only_after_qualifying_prior_evidence_exists(self):
        """Simulate a chronological stream: EARLY in the stream there is not yet
        enough prior evidence (decision at T1 sees a thin prefix -> no shrink);
        LATER, once >=30 qualifying prior settlements exist (decision at T2 sees
        the full prefix), the same q=0.84 claim shrinks."""
        # Build 36 settlement events (16 wins) chronologically dated 2026-05-01..
        full_stream = _make_obs(
            n=36, wins=16, q_raw=0.844, city="Singapore", metric="high",
            date_prefix="2026-05", settlement_time_prefix="2026-05",
        )
        # Decision T1: right after only the first 10 settlements exist (thin).
        t1 = "2026-05-11T00:00:00Z"
        prefix_t1 = filter_observations_prefix(full_stream, t1)
        assert len(prefix_t1) == 10
        result_t1 = hierarchical_coverage_check(
            city="Singapore", metric="high", band_template="T>=31C", direction="buy_no",
            strategy_key=CANONICAL_STRATEGY, q_raw=0.844, q_lcb_raw=0.80,
            observations=prefix_t1,
        )
        assert result_t1.status == "INSUFFICIENT_DATA"
        assert result_t1.q_exec == pytest.approx(0.844)

        # Decision T2: after all 36 settlements exist -> exact cell now qualifies
        # (n=36 >= 30) and is overconfident -> shrink applies.
        t2 = "2026-06-10T00:00:00Z"
        prefix_t2 = filter_observations_prefix(full_stream, t2)
        assert len(prefix_t2) == 36
        result_t2 = hierarchical_coverage_check(
            city="Singapore", metric="high", band_template="T>=31C", direction="buy_no",
            strategy_key=CANONICAL_STRATEGY, q_raw=0.844, q_lcb_raw=0.80,
            observations=prefix_t2,
        )
        assert result_t2.status == "UNLICENSED"
        assert result_t2.q_exec < 0.844

    def test_no_observation_at_or_after_decision_time_ever_enters_a_cohort(self):
        full_stream = _make_obs(
            n=40, wins=16, q_raw=0.844, city="Singapore", metric="high",
            date_prefix="2026-05", settlement_time_prefix="2026-05",
        )
        decision_time = "2026-05-20T00:00:00Z"
        admitted = filter_observations_prefix(full_stream, decision_time)
        assert all(o.settlement_time < decision_time for o in admitted)
        assert len(admitted) < len(full_stream)


# ---------------------------------------------------------------------------
# 6. Dedup
# ---------------------------------------------------------------------------


class TestDedup:
    def test_two_fills_same_market_side_claim_one_day_count_once(self):
        first = HierarchyObservation(
            condition_or_market_id="cond-1", target_date="2026-06-01", city="X", metric="high",
            band_template="T", direction="buy_no", strategy_key=CANONICAL_STRATEGY, q_raw=0.80,
            won=False, settlement_time="2026-06-01T12:00:00Z",
        )
        second_fill_same_claim = HierarchyObservation(
            condition_or_market_id="cond-1", target_date="2026-06-01", city="X", metric="high",
            band_template="T", direction="buy_no", strategy_key=CANONICAL_STRATEGY, q_raw=0.83,
            won=True, settlement_time="2026-06-01T12:00:00Z",
        )
        deduped = dedupe_observations([first, second_fill_same_claim])
        assert len(deduped) == 1
        # Last-write-wins (mirrors K3's "last-written receipt's claim stood").
        assert deduped[0].q_raw == pytest.approx(0.83)
        assert deduped[0].won is True

    def test_distinct_days_are_not_deduped(self):
        day1 = HierarchyObservation(
            condition_or_market_id="cond-1", target_date="2026-06-01", city="X", metric="high",
            band_template="T", direction="buy_no", strategy_key=CANONICAL_STRATEGY, q_raw=0.80,
            won=False, settlement_time="2026-06-01T12:00:00Z",
        )
        day2 = HierarchyObservation(
            condition_or_market_id="cond-1", target_date="2026-06-02", city="X", metric="high",
            band_template="T", direction="buy_no", strategy_key=CANONICAL_STRATEGY, q_raw=0.80,
            won=True, settlement_time="2026-06-02T12:00:00Z",
        )
        deduped = dedupe_observations([day1, day2])
        assert len(deduped) == 2
