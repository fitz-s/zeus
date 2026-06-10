# Created: 2026-06-10
# Lifecycle: created=2026-06-10; last_reviewed=2026-06-10; last_reused=2026-06-10
# Purpose: Antibody: diurnal_peak_prob.p_high_set must be survival-shaped (monotone, ~1 by evening) — the day0 maturity gate's substrate.
# Reuse: Run after any etl_diurnal_curves.py change or diurnal table regeneration; live-table checks skip when state DB absent.
# Last reused or audited: 2026-06-10
# Authority basis: adversarial review /tmp/day0_adversarial_review.md finding 3
#   (diurnal_peak_prob.p_high_set was PMF-shaped — per-hour bucket max vs daily
#   max — non-monotone and ~0 by evening; impossible for the documented
#   "P(daily high already set by hour h)"). ETL repaired 2026-06-10
#   (scripts/etl_diurnal_curves.py: cumulative running max + full-day coverage
#   gate + isotonic aggregate); table regenerated same day.
"""Antibody: diurnal_peak_prob must be survival-shaped (monotone non-decreasing).

The day0 maturity gate (monitor_refresh._day0_extreme_authority_rejection_reason
-> post_peak_confidence) load-bears on this table. The broken shape produced
BOTH failure directions: sunset-locked US exits (Chicago June h17=0.10, h20=0.0)
and inflated early-hour confidence. The repaired semantics:

  p_high_set(city, month, h) = P(cumulative running max through local hour h
                                 == the day's final max), full-coverage days only

— monotone non-decreasing in h by definition, ~1.0 by late evening.

DATA-VERIFIED NOTE on the review's 'Seoul 13:00' case: the review flagged
Seoul h13 >= 0.5 as a pre-peak-authority hazard, reasoning from the broken
table. The REPAIRED truth says Seoul/RKSI (coastal Incheon) genuinely reaches
its June daily max by 13:00 KST on 56/69 fully-covered days (raw
observation_instants query 2026-06-10) — an early coastal peak, so >=0.5 at
13:00 is CORRECT data, not the hazard. The hazard test here is therefore the
SHAPE (monotone, ~1 by evening, low pre-sunrise), not a hardcoded city hour.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORLD_DB = ROOT / "state" / "zeus-world.db"


def _etl_module():
    spec = importlib.util.spec_from_file_location(
        "etl_diurnal_curves", ROOT / "scripts" / "etl_diurnal_curves.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# Pure semantics (the finding-3 fix itself)
# ===========================================================================

class TestCumulativeHighSetSemantics:
    def test_indicator_is_survival_shaped_not_pmf(self):
        """The defective version scored the PEAK HOUR ~1 and the evening ~0.
        The repaired version: 0 before the peak hour, 1 from the peak ONWARD."""
        etl = _etl_module()
        samples = [
            {"hour": 8, "running_max": 18.0},
            {"hour": 11, "running_max": 24.0},
            {"hour": 14, "running_max": 30.0},   # the day's peak hour
            {"hour": 17, "running_max": 27.0},   # bucket max FALLS after peak
            {"hour": 20, "running_max": 22.0},
            {"hour": 23, "running_max": 20.0},
        ]
        indicators = dict(etl._cumulative_high_set_indicators(samples))
        assert indicators[8] == 0.0 and indicators[11] == 0.0
        assert indicators[14] == 1.0
        # THE defect: these were 0.0 in the broken table (bucket 27 != max 30)
        assert indicators[17] == 1.0 and indicators[20] == 1.0 and indicators[23] == 1.0

    def test_indicator_monotone_for_any_input_order(self):
        etl = _etl_module()
        samples = [
            {"hour": h, "running_max": v}
            for h, v in [(20, 22.0), (8, 18.0), (14, 30.0), (11, 24.0), (17, 27.0)]
        ]
        indicators = etl._cumulative_high_set_indicators(samples)
        values = [v for _, v in sorted(indicators)]
        assert values == sorted(values)

    def test_isotonic_pass_removes_sampling_noise_decreases(self):
        etl = _etl_module()
        noisy = {8: 0.1, 11: 0.3, 14: 0.62, 15: 0.58, 17: 0.9, 20: 0.97, 23: 1.0}
        iso = etl._isotonic_by_hour(noisy)
        values = [iso[h] for h in sorted(iso)]
        assert values == sorted(values)
        assert iso[15] == pytest.approx(0.62)  # lifted to the running max
        assert iso[23] == pytest.approx(1.0)


# ===========================================================================
# Regenerated-table shape (live DB; skipped where the state DB is absent)
# ===========================================================================

@pytest.mark.skipif(not WORLD_DB.exists(), reason="state/zeus-world.db not present")
class TestRegeneratedTableShape:
    @pytest.fixture(scope="class")
    def conn(self):
        conn = sqlite3.connect(f"file:{WORLD_DB}?mode=ro", uri=True, timeout=15)
        # DATA-PRESENCE GUARD: scratch worktrees / CI sandboxes can carry an
        # EMPTY state DB created as a side effect of other test runs
        # (init_schema on the relative state path). The shape antibody is only
        # meaningful against a POPULATED regenerated table — an empty table is
        # 'not present', not 'non-monotone'.
        try:
            populated = conn.execute(
                "SELECT COUNT(*) FROM diurnal_peak_prob"
            ).fetchone()[0] >= 1000
        except sqlite3.Error:
            populated = False
        if not populated:
            conn.close()
            pytest.skip("diurnal_peak_prob not populated in this state DB")
        yield conn
        conn.close()

    def test_globally_monotone_in_hour(self, conn):
        bad = conn.execute(
            """
            SELECT COUNT(*) FROM diurnal_peak_prob a
            JOIN diurnal_peak_prob b
              ON a.city = b.city AND a.month = b.month
             AND b.hour = a.hour + 1 AND b.p_high_set < a.p_high_set - 1e-9
            """
        ).fetchone()[0]
        assert bad == 0, f"{bad} non-monotone adjacent (city,month,hour) pairs"

    def test_evening_confidence_is_high_not_zero(self, conn):
        """The broken table had h20 ~= 0 (Chicago June 0.0). P(high set by
        late evening) must be ~1 — the daily high cannot still be pending at
        23:00 local on more than a sliver of days."""
        row = conn.execute(
            """
            SELECT MIN(p_high_set) FROM diurnal_peak_prob
            WHERE hour = 23 AND n_obs >= 20
            """
        ).fetchone()
        assert row[0] is not None and row[0] >= 0.9

    def test_us_cities_reach_authority_before_sunset_in_june(self, conn):
        """Reviewer's sunset-lock case: Chicago/NYC June must cross the gate's
        0.5 confidence DURING the afternoon (the broken table never did)."""
        for city in ("Chicago", "NYC"):
            row = conn.execute(
                """
                SELECT MIN(hour) FROM diurnal_peak_prob
                WHERE city = ? AND month = 6 AND p_high_set >= 0.5
                """,
                (city,),
            ).fetchone()
            assert row[0] is not None, f"{city}: no June rows"
            assert row[0] <= 16, f"{city}: 0.5 confidence only at hour {row[0]} (sunset-locked)"

    def test_pre_sunrise_confidence_is_low(self, conn):
        """Counter-direction: the repaired curve must not hand out post-peak
        confidence at dawn (would re-open the panic-sell hazard)."""
        row = conn.execute(
            """
            SELECT MAX(p_high_set) FROM diurnal_peak_prob
            WHERE hour = 7 AND month = 6 AND n_obs >= 20
              AND city IN ('Chicago','NYC','Seoul','London','Tokyo','Denver')
            """
        ).fetchone()
        assert row[0] is not None and row[0] < 0.5
