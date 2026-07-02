# Created: 2026-06-10
# Last reused or audited: 2026-07-01
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
from src.contracts.execution_price import ExecutionPrice
from src.engine.event_reactor_adapter import (
    _apply_day0_mask_to_generated_probabilities,
    _apply_day0_mask_to_probability_vector,
    _day0_absorbing_mask,
    _day0_hard_fact_fdr_maps,
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


def _native_cost(price: float):
    return (
        None,
        ExecutionPrice(
            value=price,
            price_type="ask",
            fee_deducted=True,
            currency="probability_units",
        ),
        1.0,
        None,
        None,
    )


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

    def test_masked_generated_q_zero_and_structural_buy_no_license_on_dead_bins(self):
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
        # Dead YES bins are deterministic buy-NO wins; unresolved/alive bins are not.
        assert _qlcb_float(lcb[("cond0", "buy_no")]) == pytest.approx(1.0)
        assert _qlcb_float(lcb[("cond1", "buy_no")]) == pytest.approx(1.0)
        for i in range(2, 5):
            assert _qlcb_float(lcb[(f"cond{i}", "buy_no")]) == 0.0

    def test_crossed_high_open_shoulder_gets_structural_buy_yes_license(self):
        fam = _seoul_high_family()
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=_payload("high", 27.0, obs_age_minutes=500.0),
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )

        assert q["cond4"] == pytest.approx(1.0)
        assert _qlcb_float(lcb[("cond4", "buy_yes")]) == pytest.approx(1.0)
        assert _qlcb_float(lcb[("cond4", "buy_no")]) == 0.0

    def test_crossed_low_open_shoulder_gets_structural_buy_yes_license(self):
        fam = _family(
            "Seoul",
            [_bin(None, 21.0), _bin(22.0, 22.0), _bin(23.0, 23.0), _bin(24.0, None)],
            metric="low",
        )
        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=_payload("low", 21.0, obs_age_minutes=500.0),
            family=fam,
            q_by_condition={f"cond{i}": 0.25 for i in range(4)},
            lcb_by_condition={(f"cond{i}", d): 0.2 for i in range(4) for d in ("buy_yes", "buy_no")},
            decision_time=NOW,
        )

        assert q["cond0"] == pytest.approx(1.0)
        assert _qlcb_float(lcb[("cond0", "buy_yes")]) == pytest.approx(1.0)
        assert _qlcb_float(lcb[("cond0", "buy_no")]) == 0.0

    def test_day0_fdr_prefilter_uses_masked_hard_fact_lcb_not_forecast_prefilter(self):
        fam = _seoul_high_family()
        _q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=_payload("high", 27.0, obs_age_minutes=10.0),
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )
        p_values, prefilter = _day0_hard_fact_fdr_maps(
            family=fam,
            native_costs={
                ("cond0", "buy_no"): _native_cost(0.75),
                ("cond4", "buy_yes"): _native_cost(0.60),
            },
            masked_lcb_by_condition=lcb,
        )

        assert p_values[("cond0", "buy_no")] == 0.0
        assert prefilter[("cond0", "buy_no")] is True
        assert p_values[("cond4", "buy_yes")] == 0.0
        assert prefilter[("cond4", "buy_yes")] is True
        assert p_values[("cond2", "buy_yes")] == 1.0
        assert prefilter[("cond2", "buy_yes")] is False


# ===========================================================================
# R1B — pre-Day0 LOW carryover is a soft entry signal, not a hard fact
# ===========================================================================

class TestPreDay0LowCarryover:
    @staticmethod
    def _london_city():
        return SimpleNamespace(
            name="London",
            timezone="Europe/London",
            settlement_unit="C",
            settlement_source_type="wu_icao",
            wu_station="EGLL",
            instrument_noise_override=0.28,
            lat=51.47,
        )

    @staticmethod
    def _metar(ts: str, temp_c: float, station: str = "EGLL"):
        from src.data.day0_fast_obs import MetarReport

        return MetarReport(
            station_id=station,
            obs_time=datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc),
            receipt_time=datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc),
            temp_c=temp_c,
            metar_type="METAR",
            raw=f"{station} 172230Z AUTO T01200080",
        )

    def test_pre_day0_low_window_ignores_prior_day_morning_low(self):
        from src.data.day0_fast_obs import pre_day0_low_window_for_target

        city = self._london_city()
        window = pre_day0_low_window_for_target(
            [
                self._metar("2026-06-17T05:00:00Z", 5.0),    # previous-day low, outside late window
                self._metar("2026-06-17T20:30:00Z", 13.0),   # 21:30 BST
                self._metar("2026-06-17T22:30:00Z", 12.0),   # 23:30 BST
                self._metar("2026-06-17T22:35:00Z", 3.0, "EGSS"),  # wrong station
            ],
            city=city,
            target_date="2026-06-18",
            as_of=datetime(2026, 6, 17, 22, 45, tzinfo=timezone.utc),
            lookback_hours=3.0,
            max_lead_hours=4.0,
        )
        assert window is not None
        assert window.window_low == pytest.approx(12.0)
        assert window.current_temp == pytest.approx(12.0)
        assert window.sample_count == 2

    def test_pre_day0_low_carryover_moves_probability_without_certainty(self):
        import numpy as np

        from src.contracts import SettlementSemantics
        from src.signal.day0_low_distribution import (
            PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION,
            build_pre_day0_low_empirical_conditioning,
        )
        from src.signal.ensemble_signal import analytic_p_raw_vector_from_maxes
        from src.types.market import Bin

        city = self._london_city()
        sem = SettlementSemantics.for_city(city)
        bins = [
            Bin(None, 11.0, "C", "11°C or below"),
            Bin(12.0, 12.0, "C", "12°C"),
            Bin(13.0, 13.0, "C", "13°C"),
            Bin(14.0, None, "C", "14°C or higher"),
        ]
        member_mins = np.full(51, 15.0)
        base = analytic_p_raw_vector_from_maxes(member_mins, city, sem, bins)
        model = {
            "model_version": PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION,
            "source_table": "test_observation_instants",
            "live_policy": {
                "max_lead_hours": 4.0,
                "trailing_lookback_hours": 1.0,
                "basis": "test",
            },
            "by_city": {
                "London": {
                    "lead_buckets": {
                        "1": {
                            "n": 240,
                            "residual_quantiles": [-0.2, 0.0, 0.4, 0.8, 1.0],
                        }
                    }
                }
            },
            "by_unit": {},
        }
        conditioning = build_pre_day0_low_empirical_conditioning(
            member_mins=member_mins,
            window_low=12.0,
            lead_hours_to_target_start=0.25,
            unit="C",
            city_name="London",
            model=model,
        )
        assert conditioning is not None
        empirical = analytic_p_raw_vector_from_maxes(
            conditioning.conditioned_member_mins, city, sem, bins
        )
        assert base[3] > 0.95
        assert empirical[1] + empirical[2] > 0.50
        assert empirical[1] + empirical[2] < 0.999
        assert conditioning.residual_scope == "city:London"
        assert conditioning.residual_sample_count == 240

    def test_pre_day0_low_carryover_requires_empirical_model_for_live_q(self):
        import numpy as np
        from src.signal.day0_low_distribution import build_pre_day0_low_empirical_conditioning

        assert build_pre_day0_low_empirical_conditioning(
            member_mins=np.full(51, 15.0),
            window_low=12.0,
            lead_hours_to_target_start=1.0,
            unit="C",
            city_name="London",
            model=None,
        ) is None
        source = (ROOT / "src" / "engine" / "evaluator.py").read_text(encoding="utf-8")
        assert "EMPIRICAL_RESIDUAL_MODEL_VERIFIED" in source
        assert "UNCALIBRATED_HEURISTIC_SHADOW_ONLY" not in source

    def test_pre_day0_low_carryover_not_active_after_start_or_too_early(self):
        import numpy as np

        from src.signal.day0_low_distribution import (
            PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION,
            build_pre_day0_low_empirical_conditioning,
        )
        model = {
            "model_version": PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION,
            "source_table": "test_observation_instants",
            "live_policy": {
                "max_lead_hours": 4.0,
                "trailing_lookback_hours": 1.0,
                "basis": "test",
            },
            "by_city": {
                "London": {
                    "lead_buckets": {
                        "3": {"n": 240, "residual_quantiles": [-1.0, 0.0, 1.0]},
                        "4": {"n": 240, "residual_quantiles": [-1.5, -0.5, 0.5]},
                    }
                }
            },
            "by_unit": {},
        }

        kwargs = dict(
            member_mins=np.full(51, 15.0),
            window_low=12.0,
            unit="C",
            city_name="London",
            model=model,
        )
        assert build_pre_day0_low_empirical_conditioning(
            **kwargs,
            lead_hours_to_target_start=-0.10,
        ) is None
        assert build_pre_day0_low_empirical_conditioning(
            **kwargs,
            lead_hours_to_target_start=4.50,
        ) is None
        assert build_pre_day0_low_empirical_conditioning(
            **kwargs,
            lead_hours_to_target_start=3.00,
        ) is not None
        assert build_pre_day0_low_empirical_conditioning(
            **kwargs,
            lead_hours_to_target_start=4.00,
        ) is not None

    def test_edli_qkernel_spine_uses_pre_day0_low_carryover_members(self):
        from src.data.day0_fast_obs import PreDay0LowWindow
        from src.engine import event_reactor_adapter as era
        from src.signal.day0_low_distribution import PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION

        city = self._london_city()
        family = SimpleNamespace(city="London", metric="low", target_date="2026-06-18")
        model = {
            "model_version": PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION,
            "source_table": "test_observation_instants",
            "live_policy": {
                "max_lead_hours": 4.0,
                "trailing_lookback_hours": 1.0,
                "basis": "test",
            },
            "by_city": {
                "London": {
                    "lead_buckets": {
                        "1": {
                            "n": 240,
                            "residual_quantiles": [-0.2, 0.0, 0.4, 0.8],
                        }
                    }
                }
            },
            "by_unit": {},
        }
        window = PreDay0LowWindow(
            city="London",
            station_id="EGLL",
            target_date="2026-06-18",
            unit="C",
            window_start_time=datetime(2026, 6, 17, 21, 45, tzinfo=timezone.utc),
            target_start_time=datetime(2026, 6, 17, 23, 0, tzinfo=timezone.utc),
            window_low=12.0,
            current_temp=12.3,
            low_obs_time=datetime(2026, 6, 17, 22, 30, tzinfo=timezone.utc),
            first_obs_time=datetime(2026, 6, 17, 21, 50, tzinfo=timezone.utc),
            last_obs_time=datetime(2026, 6, 17, 22, 40, tzinfo=timezone.utc),
            last_receipt_time=datetime(2026, 6, 17, 22, 41, tzinfo=timezone.utc),
            sample_count=3,
            skipped_unit_law=0,
            quarantined_implausible=0,
        )
        monkey = pytest.MonkeyPatch()
        monkey.setattr(era, "runtime_cities_by_name", lambda: {"London": city})
        try:
            members, meta, reason = era._apply_pre_day0_low_carryover_to_spine_members(
                family=family,
                decision_time=datetime(2026, 6, 17, 22, 45, tzinfo=timezone.utc),
                members_native=[15.0, 15.0, 15.0],
                empirical_model=model,
                low_window=window,
            )
        finally:
            monkey.undo()

        assert reason is None
        assert meta is not None
        assert meta["live_probability_applied"] is True
        assert meta["original_member_count"] == 3
        assert meta["conditioned_member_count"] == 12
        assert len(members) == 12
        assert min(members) == pytest.approx(11.8)
        assert max(members) == pytest.approx(12.8)

    def test_edli_qkernel_spine_blocks_when_pre_day0_low_evidence_missing(self):
        from src.engine import event_reactor_adapter as era
        from src.signal.day0_low_distribution import PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION

        city = self._london_city()
        family = SimpleNamespace(city="London", metric="low", target_date="2026-06-18")
        model = {
            "model_version": PRE_DAY0_LOW_EMPIRICAL_MODEL_VERSION,
            "live_policy": {
                "max_lead_hours": 4.0,
                "trailing_lookback_hours": 1.0,
                "basis": "test",
            },
            "by_city": {},
            "by_unit": {},
        }
        monkey = pytest.MonkeyPatch()
        monkey.setattr(era, "runtime_cities_by_name", lambda: {"London": city})
        try:
            members, meta, reason = era._apply_pre_day0_low_carryover_to_spine_members(
                family=family,
                decision_time=datetime(2026, 6, 17, 22, 45, tzinfo=timezone.utc),
                members_native=[15.0, 15.0, 15.0],
                empirical_model=model,
                low_window=None,
            )
        finally:
            monkey.undo()

        assert members == [15.0, 15.0, 15.0]
        assert meta is None
        assert reason == "PRE_DAY0_LOW_CARRYOVER_UNAVAILABLE:fast_obs_window_missing:lead_hours=0.250"

    def test_edli_qkernel_spine_carryover_is_inactive_outside_low_window(self):
        from src.engine import event_reactor_adapter as era

        family = SimpleNamespace(city="London", metric="high", target_date="2026-06-18")
        members, meta, reason = era._apply_pre_day0_low_carryover_to_spine_members(
            family=family,
            decision_time=datetime(2026, 6, 17, 22, 45, tzinfo=timezone.utc),
            members_native=[15.0, 15.0, 15.0],
            empirical_model=None,
            low_window=None,
        )

        assert members == [15.0, 15.0, 15.0]
        assert meta is None
        assert reason is None


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


class TestDay0RemainingDayMaturityEntryGuard:
    def test_immature_high_boundary_point_yes_has_no_submit_lcb(self):
        fam = _seoul_high_family()
        payload = _payload("high", 25.0, obs_age_minutes=10.0)
        payload["_edli_q_source"] = "day0_remaining_day"
        payload["_edli_day0_exit_authority_status"] = "immature"
        payload["_edli_day0_exit_authority_reason"] = (
            "day0_high_extreme_not_mature:daypart=morning,post_peak_confidence=0.000"
        )

        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=payload,
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )

        assert q["cond2"] > 0.0
        assert _qlcb_float(lcb[("cond2", "buy_yes")]) == 0.0
        assert (
            payload["_edli_day0_lcb_transform"][
                "immature_boundary_yes_suppressed_conditions"
            ]
            == ["cond2"]
        )

    def test_mature_high_boundary_point_yes_keeps_submit_lcb(self):
        fam = _seoul_high_family()
        payload = _payload("high", 25.0, obs_age_minutes=10.0)
        payload["_edli_q_source"] = "day0_remaining_day"
        payload["_edli_day0_exit_authority_status"] = "mature"
        payload["_edli_day0_exit_authority_reason"] = "day0_high_extreme_post_peak"

        _q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=payload,
            family=fam,
            q_by_condition=_uniform_q(fam),
            lcb_by_condition=_full_lcb(fam),
            decision_time=NOW,
        )

        assert _qlcb_float(lcb[("cond2", "buy_yes")]) > 0.0
        assert (
            payload["_edli_day0_lcb_transform"][
                "immature_boundary_yes_suppressed_conditions"
            ]
            == []
        )

    def test_immature_low_boundary_point_yes_has_no_submit_lcb(self):
        fam = _family(
            "Seoul",
            [_bin(None, 21.0), _bin(22.0, 22.0), _bin(23.0, 23.0), _bin(24.0, None)],
            metric="low",
        )
        payload = _payload("low", 23.0, obs_age_minutes=10.0)
        payload["_edli_q_source"] = "day0_remaining_day"
        payload["_edli_day0_exit_authority_status"] = "immature"
        payload["_edli_day0_exit_authority_reason"] = (
            "day0_low_extreme_not_terminal:hours_remaining=12.0"
        )

        q, lcb = _apply_day0_mask_to_generated_probabilities(
            payload=payload,
            family=fam,
            q_by_condition={f"cond{i}": 0.25 for i in range(4)},
            lcb_by_condition={
                (f"cond{i}", d): 0.2
                for i in range(4)
                for d in ("buy_yes", "buy_no")
            },
            decision_time=NOW,
        )

        assert q["cond2"] > 0.0
        assert _qlcb_float(lcb[("cond2", "buy_yes")]) == 0.0
        assert (
            payload["_edli_day0_lcb_transform"][
                "immature_boundary_yes_suppressed_conditions"
            ]
            == ["cond2"]
        )


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
        assert "day0_observation_reversal_nonterminal" in decision.applied_validations
        assert "consecutive_cycle_check" in decision.applied_validations

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

    def test_day0_zero_probability_with_sell_value_exits_even_inside_ci_noise_floor(self):
        """Kuala Lumpur 34C regression: Day0 remaining-window q=0 plus a
        real executable bid is an economic exit, not a panic sale. The CI noise
        floor may protect light negative edge, but it must not hold a zero-value
        claim when direct sell value dominates hold value."""
        pos = _make_position(
            direction="buy_yes",
            entry_price=0.031,
            p_posterior=0.121802485107885,
            entry_ci_width=0.0,
            shares=10.01029,
            cost_basis_usd=0.3103,
            size_usd=0.3103,
        )
        decision = pos.evaluate_exit(
            ExitContext(
                fresh_prob=0.0,
                fresh_prob_is_fresh=True,
                current_market_price=0.011,
                current_market_price_is_fresh=True,
                best_bid=0.011,
                best_ask=0.03,
                hours_to_settlement=18.0,
                position_state="day0_window",
                day0_active=True,
                entry_posterior=0.121802485107885,
                entry_ci=(0.121802485107885, 0.121802485107885),
                current_ci=(0.0, 0.0),
                belief_available=True,
                day0_zero_probability_exit_authority=True,
            )
        )
        assert decision.should_exit is True
        assert decision.trigger == "DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES"
        assert "day0_zero_probability_sell_value_dominates" in decision.applied_validations

    def test_no_single_cycle_day0_reversal_sell_producer_in_source(self):
        """Static antibody: portfolio.py must not reconstruct the pre-2026-06-07
        single-cycle DAY0_OBSERVATION_REVERSAL sell."""
        source = (ROOT / "src" / "state" / "portfolio.py").read_text(encoding="utf-8")
        assert 'trigger="DAY0_OBSERVATION_REVERSAL"' not in source
        assert "DAY0_OBSERVATION_REVERSAL_HELD_FOR_EVIDENCE" not in source

    def test_buy_no_day0_monitor_probability_uses_explicit_held_side_conversion(self):
        """Static antibody: buy_no monitor probability must be explicitly held-side.

        The Day0 signal estimates the YES outcome for the bin. A buy_no held
        position needs the complementary outcome probability, but not a NO-token
        executable price or confidence-bound shortcut.
        """
        source = (ROOT / "src" / "engine" / "monitor_refresh.py").read_text(encoding="utf-8")
        assert "_held_side_probability_from_yes_bin_probability" in source
        assert "buy_no_independent_monitor_probability_missing" not in source

    def test_buy_no_day0_monitor_probability_converts_direction_enum(self):
        """Position normalizes direction to Direction; enum inputs still need NO space."""
        from src.contracts.semantic_types import Direction
        from src.engine.monitor_refresh import _held_side_probability_from_yes_bin_probability

        actual = _held_side_probability_from_yes_bin_probability(
            0.004831941161402871,
            Direction.NO,
        )
        assert actual == pytest.approx(0.9951680588385971)

    def test_day0_monitor_does_not_reintroduce_legacy_platt(self):
        """Day0 monitor evidence must not resurrect the retired ENS+Platt era."""
        source = (ROOT / "src" / "engine" / "monitor_refresh.py").read_text(encoding="utf-8")
        start = source.index("def _refresh_day0_observation(")
        end = source.index("def _day0_extreme_authority_rejection_reason(")
        body = source[start:end]
        assert "_monitor_calibrator_for_ens_result" not in body
        assert "platt_recalibration" not in body


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
