# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: day0 first-principles review 2026-06-10
#   (/tmp/day0_first_principles_review.md); panic-sell incident evidence:
#   zeus_trades.db position_events b5d966a9-990 (Seoul 2026-06-07T15:08Z,
#   DAY0_OBSERVATION_REVERSAL ci_lower=point=-0.0758, sold 5 min after
#   DAY0_WINDOW_ENTERED); WU latency artifact config/wu_obs_latency.json.
"""Antibody tests killing the day0 panic-sell CATEGORY and pinning the
absorbing-boundary + obs-latency first principles.

Relationship contracts under test (cross-module, written before the fixes
they pin — repo law: relationship tests -> implementation -> function tests):

  R1. ABSORBING BOUNDARY: once the rounded running extreme passes a bin's
      survival edge, the day0-lane q for that bin is 0 forever (monotone:
      a rising max only shrinks the alive set; equality keeps the bin alive).

  R2. STALE-OBS BOUNDARY: a running extreme older than the city's measured
      WU staleness budget is a LOWER bound, not current truth. Killing bins
      remains safe (monotone), but boundary-adjacent ALIVE bins are UNKNOWN:
      their buy_yes submit license (q_lcb) must be 0. Missing/unparseable
      obs timestamps are MAXIMALLY stale (fail-closed).

  R3. TRANSITION MONOTONICITY (panic-sell category): the estimator switch at
      day0 arrival (forecast posterior -> day0 obs posterior) must never sell
      a position on a single-tick point-estimate move — including with the
      degenerate entry_ci_width=0.0 of the 2026-06-07 Seoul incident. A
      position whose bin contains the running extreme survives day0 arrival;
      a CI-separated dead position still exits.

  R4. PRE-MATURITY AUTHORITY: a HIGH running max before the diurnal peak is
      an early-day bound, not exit authority; a DETERMINISTIC bound (obs
      already past every remaining member) is authority at any daypart.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.calibration.qlcb_provenance import _qlcb_float
from src.engine.event_reactor_adapter import (
    _apply_day0_mask_to_generated_probabilities,
    _apply_day0_mask_to_probability_vector,
    _day0_absorbing_mask,
    _day0_observation_age_minutes,
)
from src.signal.day0_obs_latency import (
    DEFAULT_STALENESS_BUDGET_MIN,
    stale_extreme_uncertainty_margin,
    staleness_budget_minutes,
)
from src.state.portfolio import ExitContext, Position

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 6, 10, 18, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Stubs for the event-reactor family/candidate shape (era reads .candidates[i]
# .bin.low/.high/.unit and .condition_id only).
# ---------------------------------------------------------------------------

def _bin(low, high, unit="C"):
    return SimpleNamespace(low=low, high=high, unit=unit)


def _family(city, bins, metric="high"):
    candidates = [
        SimpleNamespace(bin=b, condition_id=f"cond{i}") for i, b in enumerate(bins)
    ]
    return SimpleNamespace(city=city, metric=metric, candidates=candidates)


def _payload(metric, rounded, obs_age_minutes=30.0, city="Seoul"):
    obs_time = (NOW - timedelta(minutes=obs_age_minutes)).isoformat() if obs_age_minutes is not None else None
    return {
        "metric": metric,
        "rounded_value": rounded,
        "observation_time": obs_time,
        "observation_available_at": obs_time,
        "city": city,
        "settlement_unit": "C",
    }


def _seoul_high_family():
    """5-bin Seoul HIGH family: <=23 | 24 | 25 | 26 | >=27 (deg C)."""
    return _family(
        "Seoul",
        [
            _bin(None, 23.0),
            _bin(24.0, 24.0),
            _bin(25.0, 25.0),
            _bin(26.0, 26.0),
            _bin(27.0, None),
        ],
    )


def _uniform_q(family):
    n = len(family.candidates)
    return {f"cond{i}": 1.0 / n for i in range(n)}


def _full_lcb(family, value=0.2):
    out = {}
    for i in range(len(family.candidates)):
        out[(f"cond{i}", "buy_yes")] = value
        out[(f"cond{i}", "buy_no")] = value
    return out


# ===========================================================================
# R1 — absorbing boundary correctness + monotonicity
# ===========================================================================

class TestAbsorbingBoundary:
    def test_high_kills_bins_strictly_below_running_max(self):
        fam = _seoul_high_family()
        mask = _day0_absorbing_mask(payload=_payload("high", 25.0), family=fam)
        # <=23 dead, 24 dead, 25 alive (contains extreme), 26 alive, >=27 alive
        assert list(mask) == [0.0, 0.0, 1.0, 1.0, 1.0]

    def test_high_equality_keeps_boundary_bin_alive(self):
        fam = _seoul_high_family()
        mask = _day0_absorbing_mask(payload=_payload("high", 24.0), family=fam)
        assert mask[1] == 1.0  # bin 24-24: rounded == high -> contains the max

    def test_low_kills_bins_strictly_above_running_min(self):
        fam = _family(
            "Seoul",
            [_bin(None, 23.0), _bin(24.0, 24.0), _bin(25.0, 25.0), _bin(26.0, None)],
            metric="low",
        )
        mask = _day0_absorbing_mask(payload=_payload("low", 24.0), family=fam)
        # low running min 24: bins with low > 24 dead; shoulder <=23 alive
        assert list(mask) == [1.0, 1.0, 0.0, 0.0]

    def test_rising_max_only_shrinks_alive_set(self):
        """Monotone-truth: the alive set under rounded=r2 is a subset of r1<r2."""
        fam = _seoul_high_family()
        previous_alive = None
        for rounded in (23.0, 24.0, 25.0, 26.0, 27.0, 28.0):
            mask = _day0_absorbing_mask(payload=_payload("high", rounded), family=fam)
            alive = {i for i, m in enumerate(mask) if m > 0.0}
            if previous_alive is not None:
                assert alive <= previous_alive, f"alive set grew at rounded={rounded}"
            previous_alive = alive
        # The open-high shoulder can never die.
        assert 4 in previous_alive

    def test_masked_probability_vector_is_zero_on_dead_bins_and_normalized(self):
        fam = _seoul_high_family()
        vec = [0.3, 0.3, 0.2, 0.15, 0.05]
        masked = _apply_day0_mask_to_probability_vector(
            payload=_payload("high", 25.0), family=fam, vector=vec
        )
        assert masked[0] == 0.0 and masked[1] == 0.0
        assert abs(float(masked.sum()) - 1.0) < 1e-9
        # Renormalization preserves relative forecast mass among alive bins.
        assert masked[2] > masked[3] > masked[4]

    def test_masked_generated_q_zero_and_no_buy_yes_license_on_dead_bins(self):
        fam = _seoul_high_family()
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=_payload("high", 25.0, obs_age_minutes=10.0),
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )
        assert q["cond0"] < 1e-6 and q["cond1"] < 1e-6
        assert _qlcb_float(lcb[("cond0", "buy_yes")]) == 0.0
        assert _qlcb_float(lcb[("cond1", "buy_yes")]) == 0.0
        # buy_no on the day0 lane carries no submit license at all (pinned invariant).
        for i in range(5):
            assert _qlcb_float(lcb[(f"cond{i}", "buy_no")]) == 0.0


# ===========================================================================
# R2 — stale-obs boundary guard (latency-aware dead/alive decisions)
# ===========================================================================

class TestStaleObsBoundaryGuard:
    def test_fresh_obs_within_budget_does_not_suppress(self):
        fam = _seoul_high_family()
        # Seoul budget is 70 min (30-min METAR cadence + delay); 10 min is fresh.
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=_payload("high", 25.0, obs_age_minutes=10.0),
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )
        for i in (2, 3, 4):  # alive bins keep their buy_yes license
            assert _qlcb_float(lcb[(f"cond{i}", "buy_yes")]) > 0.0

    def test_stale_obs_suppresses_boundary_adjacent_buy_yes_license(self):
        fam = _seoul_high_family()
        # 190 min old: 120 min past Seoul's 70-min budget -> margin = 2.5C/h * 2h = 5C.
        # Bins with high <= 25+5=30 are unknowable: 25, 26 suppressed; shoulder >=27
        # (open high) cannot die and keeps its license.
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=_payload("high", 25.0, obs_age_minutes=190.0),
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )
        assert _qlcb_float(lcb[("cond2", "buy_yes")]) == 0.0
        assert _qlcb_float(lcb[("cond3", "buy_yes")]) == 0.0
        assert _qlcb_float(lcb[("cond4", "buy_yes")]) > 0.0  # open-high shoulder
        # q itself stays the honest masked posterior (only the license is pulled).
        assert q["cond2"] > 0.0

    def test_missing_observation_time_is_maximally_stale_fail_closed(self):
        fam = _seoul_high_family()
        payload = _payload("high", 25.0, obs_age_minutes=None)
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=payload,
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )
        # max margin 2.5*6=15C: every finite-upper alive bin suppressed.
        assert _qlcb_float(lcb[("cond2", "buy_yes")]) == 0.0
        assert _qlcb_float(lcb[("cond3", "buy_yes")]) == 0.0
        assert _qlcb_float(lcb[("cond4", "buy_yes")]) > 0.0

    def test_kill_direction_unaffected_by_staleness(self):
        """Stale obs may suppress ALIVE bins but never resurrects DEAD ones."""
        fam = _seoul_high_family()
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=_payload("high", 25.0, obs_age_minutes=500.0),
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )
        assert q["cond0"] < 1e-6 and q["cond1"] < 1e-6

    def test_guard_flag_off_restores_pre_guard_behavior(self, monkeypatch):
        import src.engine.event_reactor_adapter as era

        monkeypatch.setattr(era, "_day0_stale_obs_boundary_guard_enabled", lambda: False)
        fam = _seoul_high_family()
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=_payload("high", 25.0, obs_age_minutes=500.0),
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )
        assert _qlcb_float(lcb[("cond2", "buy_yes")]) > 0.0

    def test_low_metric_suppression_is_symmetric(self):
        fam = _family(
            "Seoul",
            [_bin(None, 21.0), _bin(22.0, 22.0), _bin(23.0, 23.0), _bin(24.0, None)],
            metric="low",
        )
        # running min 23, very stale: margin 5C -> bins with low >= 23-5=18 that are
        # alive and have a finite low edge are unknowable: 22-22 and 23-23 suppressed;
        # open-low shoulder <=21 cannot die.
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=_payload("low", 23.0, obs_age_minutes=190.0),
            family=fam,
            q_by_condition={f"cond{i}": 0.25 for i in range(4)},
            lcb_by_condition={(f"cond{i}", d): 0.2 for i in range(4) for d in ("buy_yes", "buy_no")},
            decision_time=NOW,
        )
        assert _qlcb_float(lcb[("cond0", "buy_yes")]) > 0.0  # open-low shoulder
        assert _qlcb_float(lcb[("cond1", "buy_yes")]) == 0.0
        assert _qlcb_float(lcb[("cond2", "buy_yes")]) == 0.0

    def test_age_helper_fail_closed_on_garbage(self):
        assert _day0_observation_age_minutes({"observation_time": "not-a-time"}, NOW) is None
        assert _day0_observation_age_minutes({}, NOW) is None
        assert _day0_observation_age_minutes({"observation_time": "2026-06-10T17:00:00"}, NOW) is None  # naive
        ok = _day0_observation_age_minutes({"observation_time": "2026-06-10T17:00:00+00:00"}, NOW)
        assert ok is not None and abs(ok - 60.0) < 1e-6

    def test_margin_math_and_budget_defaults(self):
        # within budget -> zero margin (normal cadence is not staleness)
        assert stale_extreme_uncertainty_margin(unit="C", obs_age_minutes=40.0, budget_minutes=70.0) == 0.0
        # 130 min past a 70-min budget -> 1h excess -> 2.5C
        assert abs(stale_extreme_uncertainty_margin(unit="C", obs_age_minutes=130.0, budget_minutes=70.0) - 2.5) < 1e-9
        # None / NaN age -> saturated margin (fail-closed)
        assert stale_extreme_uncertainty_margin(unit="C", obs_age_minutes=None, budget_minutes=70.0) == 2.5 * 6.0
        assert stale_extreme_uncertainty_margin(unit="C", obs_age_minutes=float("nan"), budget_minutes=70.0) == 2.5 * 6.0
        # unknown city -> conservative default budget
        assert staleness_budget_minutes("NoSuchCity") == pytest.approx(DEFAULT_STALENESS_BUDGET_MIN)
        # measured city budgets are positive and at most the conservative default
        assert 0.0 < staleness_budget_minutes("Seoul") <= DEFAULT_STALENESS_BUDGET_MIN


# ===========================================================================
# R3 — transition monotonicity: the panic-sell category is dead
# ===========================================================================

def _make_position(**overrides) -> Position:
    defaults = dict(
        trade_id="day0_fp_001",
        market_id="mkt_day0_fp",
        city="Seoul",
        cluster="East Asia",
        target_date="2026-06-08",
        bin_label="25",
        direction="buy_no",
        size_usd=4.41,
        entry_price=0.63,
        p_posterior=0.7947650958698815,
        edge=0.08,
        shares=7.0,
        cost_basis_usd=4.41,
        state="day0_window",
        token_id="tok_yes_d0",
        no_token_id="tok_no_d0",
        unit="C",
        env="live",
    )
    defaults.update(overrides)
    return Position(**defaults)


class TestDay0TransitionMonotonicity:
    def test_seoul_incident_replay_single_tick_reversal_holds(self):
        """Replay of position b5d966a9-990 (2026-06-07T15:08Z): buy_no Seoul 25C,
        day0 arrival at local midnight, posterior step 0.795->0.644 from the
        estimator switch, forward edge -0.0758 with DEGENERATE entry_ci_width=0.
        Pre-fix code sold within 5 minutes (DAY0_OBSERVATION_REVERSAL). The
        category contract: a single-tick day0 point-estimate reversal NEVER sells.
        """
        pos = _make_position(entry_ci_width=0.0)
        decision = pos.evaluate_exit(
            ExitContext(
                fresh_prob=0.6442104133956563,
                fresh_prob_is_fresh=True,
                current_market_price=0.72,
                current_market_price_is_fresh=True,
                best_bid=0.72,
                hours_to_settlement=20.0,
                position_state="day0_window",
                day0_active=True,
            )
        )
        assert decision.should_exit is False
        assert decision.trigger != "DAY0_OBSERVATION_REVERSAL"
        assert "day0_observation_reversal_requires_ci_separation" in decision.applied_validations

    def test_bin_contains_running_extreme_survives_day0_arrival(self):
        """Operator first principle: a position whose bin contains the running
        extreme (posterior favorable) must NOT be sold on day0 arrival."""
        pos = _make_position(direction="buy_yes", entry_price=0.40, p_posterior=0.55, entry_ci_width=0.04)
        decision = pos.evaluate_exit(
            ExitContext(
                fresh_prob=0.85,  # day0 obs CONFIRMS the bin (contains running max)
                fresh_prob_is_fresh=True,
                current_market_price=0.60,
                current_market_price_is_fresh=True,
                best_bid=0.59,
                hours_to_settlement=8.0,
                position_state="day0_window",
                day0_active=True,
            )
        )
        assert decision.should_exit is False

    def test_day0_arrival_without_fresh_probability_holds_fail_closed(self):
        """At day0 arrival the obs estimator is typically NOT yet mature
        (maturity gate) -> fresh_prob stays stale. The exit path must HOLD with
        an explicit incomplete verdict, never dump."""
        pos = _make_position(entry_ci_width=0.0)
        decision = pos.evaluate_exit(
            ExitContext(
                fresh_prob=None,
                fresh_prob_is_fresh=False,
                current_market_price=0.72,
                current_market_price_is_fresh=True,
                best_bid=0.72,
                hours_to_settlement=20.0,
                position_state="day0_window",
                day0_active=True,
            )
        )
        assert decision.should_exit is False
        assert decision.reason.startswith("INCOMPLETE_EXIT_CONTEXT")
        assert "day0_probability_authority_blocked" in decision.applied_validations

    def test_degraded_day0_belief_is_evidence_unavailable_not_exit(self):
        pos = _make_position(direction="buy_yes", entry_ci_width=0.04)
        decision = pos.evaluate_exit(
            ExitContext(
                fresh_prob=float("nan"),
                fresh_prob_is_fresh=False,
                current_market_price=0.72,
                current_market_price_is_fresh=True,
                best_bid=0.72,
                hours_to_settlement=20.0,
                position_state="day0_window",
                day0_active=True,
                entry_posterior=0.79,
                belief_available=False,
            )
        )
        assert decision.should_exit is False
        assert decision.trigger == "EVIDENCE_UNAVAILABLE"

    def test_ci_separated_dead_position_still_exits(self):
        """The other half of the principle: an absorbing-boundary-DEAD position
        (belief collapsed WITH CI-separated evidence) must still exit."""
        pos = _make_position(direction="buy_yes", entry_price=0.40, p_posterior=0.55, entry_ci_width=0.04)
        decision = pos.evaluate_exit(
            ExitContext(
                fresh_prob=0.02,  # bin structurally dead under the mature obs floor
                fresh_prob_is_fresh=True,
                current_market_price=0.10,
                current_market_price_is_fresh=True,
                best_bid=0.08,
                hours_to_settlement=6.0,
                position_state="day0_window",
                day0_active=True,
                entry_posterior=0.55,
                entry_ci=(0.50, 0.60),
                current_ci=(0.0, 0.05),
                belief_available=True,
            )
        )
        assert decision.should_exit is True
        assert decision.trigger == "CI_SEPARATED_REVERSAL"

    def test_no_single_cycle_day0_reversal_sell_producer_in_source(self):
        """Static antibody: portfolio.py must not reconstruct the pre-2026-06-07
        single-cycle DAY0_OBSERVATION_REVERSAL sell. The bare trigger may only
        appear as the HELD_FOR_EVIDENCE hold or in the re-entry blocklist."""
        source = (ROOT / "src" / "state" / "portfolio.py").read_text(encoding="utf-8")
        assert 'trigger="DAY0_OBSERVATION_REVERSAL"' not in source
        assert 'trigger="DAY0_OBSERVATION_REVERSAL_HELD_FOR_EVIDENCE"' in source

    def test_buy_no_day0_monitor_probability_stays_independent(self):
        """Static antibody: the monitor day0 lane must keep refusing to feed a
        buy_no position a fresh day0 'model probability' derived from YES-side
        math (the estimator-switch artifact source for buy_no)."""
        source = (ROOT / "src" / "engine" / "monitor_refresh.py").read_text(encoding="utf-8")
        assert "buy_no_independent_monitor_probability_missing" in source


# ===========================================================================
# R8 — day0 q_lcb is a REAL lower bound (static-sampler fix, review item D)
# ===========================================================================

class _StubAnalysis:
    """Minimal stand-in for MarketAnalysis exposing the sampler contract
    surface (_rng / _settle / _bin_probability / bins / p_cal)."""

    def __init__(self, bins, p_cal, seed=7):
        import numpy as np

        self._rng = np.random.default_rng(seed)
        self.bins = bins
        self.p_cal = p_cal

    def _settle(self, values):
        import numpy as np

        return np.floor(np.asarray(values, dtype=float) + 0.5)

    def _bin_probability(self, measured, b):
        import numpy as np

        if getattr(b, "is_open_low", False):
            return float(np.mean(measured <= b.high))
        if getattr(b, "is_open_high", False):
            return float(np.mean(measured >= b.low))
        return float(np.mean((measured >= b.low) & (measured <= b.high)))


def _rich_bin(low, high, unit="C"):
    return SimpleNamespace(
        low=low, high=high, unit=unit,
        is_open_low=low is None, is_open_high=high is None,
    )


class TestDay0BootstrapLcb:
    def _family(self):
        bins = [
            _rich_bin(None, 23.0), _rich_bin(24.0, 24.0), _rich_bin(25.0, 25.0),
            _rich_bin(26.0, 26.0), _rich_bin(27.0, None),
        ]
        candidates = [SimpleNamespace(bin=b, condition_id=f"cond{i}") for i, b in enumerate(bins)]
        return SimpleNamespace(city="Seoul", metric="high", candidates=candidates), bins

    def _sampler(self, *, obs_age_minutes, members=(24.5, 25.2, 25.8, 26.4, 27.1)):
        import numpy as np
        from src.engine.event_reactor_adapter import _make_day0_bootstrap_sampler

        family, bins = self._family()
        payload = _payload("high", 25.0, obs_age_minutes=obs_age_minutes)
        sampler = _make_day0_bootstrap_sampler(
            members_native=np.asarray(members, dtype=float),
            payload=payload, family=family, unit="C", decision_time=NOW,
        )
        return sampler, bins

    def test_sampler_draws_are_valid_masked_distributions(self):
        import numpy as np

        sampler, bins = self._sampler(obs_age_minutes=10.0)
        assert sampler is not None
        analysis = _StubAnalysis(bins, p_cal=np.full(5, 0.2))
        for _ in range(50):
            vec = sampler(analysis, 32)
            assert np.all(np.isfinite(vec))
            assert abs(float(vec.sum()) - 1.0) < 1e-9
            # absorbing boundary in EVERY draw: bins below the running max dead
            assert vec[0] == 0.0 and vec[1] == 0.0

    def test_qlcb_percentile_sits_strictly_below_point_q(self):
        """The category defect was q_lcb == q (zero-variance static sampler).
        A real bootstrap must produce strictly positive dispersion across
        draws for an uncertain bin."""
        import numpy as np

        sampler, bins = self._sampler(obs_age_minutes=10.0)
        analysis = _StubAnalysis(bins, p_cal=np.full(5, 0.2))
        draws = np.array([sampler(analysis, 32) for _ in range(300)])
        floor_bin = draws[:, 2]  # the bin containing the running max
        assert float(floor_bin.std()) > 0.0
        q_point = float(floor_bin.mean())
        q_lcb_5pct = float(np.percentile(floor_bin, 5))
        assert q_lcb_5pct < q_point

    def test_stale_obs_widens_the_bootstrap(self):
        """Staleness (per config/wu_obs_latency.json budgets) must add draw
        dispersion — the latency artifact is wired into the LCB, not just the
        boundary guard."""
        import numpy as np

        fresh_sampler, bins = self._sampler(obs_age_minutes=10.0)
        stale_sampler, _ = self._sampler(obs_age_minutes=None)  # maximally stale
        fresh_analysis = _StubAnalysis(bins, np.full(5, 0.2), seed=11)
        stale_analysis = _StubAnalysis(bins, np.full(5, 0.2), seed=11)
        fresh = np.array([fresh_sampler(fresh_analysis, 32) for _ in range(200)])
        stale = np.array([stale_sampler(stale_analysis, 32) for _ in range(200)])
        # open-high shoulder probability dispersion grows with the staleness sigma
        assert float(stale[:, 4].std()) > float(fresh[:, 4].std())

    def test_empty_members_degrades_to_none_static_fallback(self):
        import numpy as np
        from src.engine.event_reactor_adapter import _make_day0_bootstrap_sampler

        family, _ = self._family()
        sampler = _make_day0_bootstrap_sampler(
            members_native=np.array([]), payload=_payload("high", 25.0),
            family=family, unit="C", decision_time=NOW,
        )
        assert sampler is None

    def test_obs_floor_clamp_respects_low_metric_direction(self):
        import numpy as np
        from src.engine.event_reactor_adapter import _make_day0_bootstrap_sampler

        bins = [_rich_bin(None, 21.0), _rich_bin(22.0, 22.0), _rich_bin(23.0, 23.0), _rich_bin(24.0, None)]
        candidates = [SimpleNamespace(bin=b, condition_id=f"cond{i}") for i, b in enumerate(bins)]
        family = SimpleNamespace(city="Seoul", metric="low", candidates=candidates)
        payload = _payload("low", 23.0, obs_age_minutes=10.0)
        sampler = _make_day0_bootstrap_sampler(
            members_native=np.asarray([21.5, 22.5, 23.5]), payload=payload,
            family=family, unit="C", decision_time=NOW,
        )
        analysis = _StubAnalysis(bins, p_cal=np.full(4, 0.25))
        for _ in range(30):
            vec = sampler(analysis, 32)
            # bins ABOVE the running min are dead for LOW
            assert vec[3] == 0.0
            assert abs(float(vec.sum()) - 1.0) < 1e-9


# ===========================================================================
# R4 — pre-maturity authority gate (the estimator-switch root fix)
# ===========================================================================

class TestDay0MaturityAuthority:
    @staticmethod
    def _gate(**kw):
        from src.engine.monitor_refresh import _day0_extreme_authority_rejection_reason
        from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN

        defaults = dict(
            temperature_metric=HIGH_LOCALDAY_MAX,
            temporal_context=SimpleNamespace(daypart="morning", post_peak_confidence=0.0),
            hours_remaining=20.0,
            observed_extreme_so_far=24.0,
            member_extrema_remaining=[25.0, 26.0, 27.0],
        )
        defaults.update(kw)
        if kw.get("metric") == "low":
            defaults["temperature_metric"] = LOW_LOCALDAY_MIN
            defaults.pop("metric")
        defaults.pop("metric", None)
        return _day0_extreme_authority_rejection_reason(**defaults)

    def test_high_running_max_at_local_midnight_is_not_authority(self):
        """The Seoul incident's root: a midnight running max replaced the
        forecast posterior. Pre-peak HIGH bounds must be rejected."""
        reason = self._gate(
            temporal_context=SimpleNamespace(daypart="pre_sunrise", post_peak_confidence=0.0)
        )
        assert reason is not None and "not_mature" in reason

    def test_high_pre_peak_morning_is_not_authority(self):
        assert self._gate() is not None

    def test_high_post_peak_with_confidence_is_authority(self):
        reason = self._gate(
            temporal_context=SimpleNamespace(daypart="post_peak", post_peak_confidence=0.8)
        )
        assert reason is None

    def test_deterministic_bound_is_authority_at_any_daypart(self):
        """Hard fact: obs already exceeds every remaining member -> authority
        even pre-peak (the absorbing boundary is calendar-independent)."""
        reason = self._gate(
            observed_extreme_so_far=30.0,
            member_extrema_remaining=[25.0, 26.0, 27.0],
            temporal_context=SimpleNamespace(daypart="morning", post_peak_confidence=0.0),
        )
        assert reason is None

    def test_no_observation_yet_is_never_authority(self):
        reason = self._gate(observed_extreme_so_far=None)
        assert reason is not None and "no_intraday_extreme" in reason

    def test_low_not_terminal_until_final_hours(self):
        # BOUNDED_LIVE low (members below the running min remain possible):
        # early-day running min is not terminal authority.
        reason = self._gate(
            metric="low", hours_remaining=20.0,
            observed_extreme_so_far=24.0, member_extrema_remaining=[22.0, 23.0],
        )
        assert reason is not None and "not_terminal" in reason
        # Same bounded state inside the terminal window -> authority.
        assert self._gate(
            metric="low", hours_remaining=3.0,
            observed_extreme_so_far=24.0, member_extrema_remaining=[22.0, 23.0],
        ) is None
        # DETERMINISTIC low (obs already undercuts every remaining member) is
        # authority at any hour — hard fact, calendar-independent.
        assert self._gate(
            metric="low", hours_remaining=20.0,
            observed_extreme_so_far=18.0, member_extrema_remaining=[19.0, 20.0],
        ) is None


# ===========================================================================
# R23 — LCB transform audit identity (PR#404 P1): submit-license changes are
# hash-visible even when q is unchanged
# ===========================================================================

class TestLcbTransformAuditIdentity:
    def _masked(self, obs_age_minutes):
        from src.calibration.qlcb_provenance import _qlcb_float
        from src.engine.event_reactor_adapter import (
            _apply_day0_mask_to_generated_probabilities,
            _probability_vector_hash,
        )

        fam = _seoul_high_family()
        payload = _payload("high", 25.0, obs_age_minutes=obs_age_minutes)
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=payload, family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )
        conditions = [f"cond{i}" for i in range(5)]
        q_hash = _probability_vector_hash(q[c] for c in conditions)
        lcb_hash = _probability_vector_hash(
            _qlcb_float(lcb.get((c, "buy_yes"), 0.0)) for c in conditions
        )
        return q_hash, lcb_hash, payload["_edli_day0_lcb_transform"]

    def test_staleness_changes_lcb_hash_but_not_q_hash(self):
        """The exact audit gap from the review: q identical, submit license
        revoked by the staleness guard — the q hash CANNOT see it; the lcb
        vector hash and the transform identity MUST."""
        import json as _json

        q_fresh, lcb_fresh, t_fresh = self._masked(10.0)
        q_stale, lcb_stale, t_stale = self._masked(None)  # maximally stale
        assert q_fresh == q_stale, "the staleness guard must not move q"
        assert lcb_fresh != lcb_stale, "license revocation must change the lcb vector hash"
        c_fresh = _json.dumps(t_fresh, sort_keys=True, default=str)
        c_stale = _json.dumps(t_stale, sort_keys=True, default=str)
        assert c_fresh != c_stale

    def test_transform_carries_explanation_fields(self):
        _q, _lcb, transform = self._masked(None)
        assert transform["staleness_suppressed_conditions"]  # bins suppressed when stale
        assert transform["metric"] == "high"
        assert transform["rounded_extreme"] == 25.0
        assert "staleness_margin" in transform and "staleness_budget_minutes" in transform
        assert set(transform["yes_lcb_by_condition"]) == {f"cond{i}" for i in range(5)}

    def test_evidence_hash_wiring_present_in_source(self):
        source = (ROOT / "src" / "engine" / "event_reactor_adapter.py").read_text(encoding="utf-8")
        assert '"q_lcb_vector_hash"' in source
        assert '"day0_lcb_transform_hash"' in source
