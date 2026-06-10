# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: adversarial review /tmp/day0_adversarial_review.md MUST-FIX
#   #1 (hard-fact bin-death exit lane, buy_yes kill + buy_no symmetric lane),
#   #3-wiring (resting-order cancel), #4 (METAR plausibility bound), #5 (day0
#   exposure cap). Reviewer scenarios replayed: boundary-death exits in <=1
#   monitor cycle; estimator-flip still held (panic-sell hardening intact).
"""Antibody tests for the day0 HARD-FACT exit lane and its siblings.

Relationship contracts:
  R13. HARD FACT vs ESTIMATOR split: an absorbing-boundary bin death (measured
       running extreme beyond the bin's survival edge, margin per the
       calibration artifact) exits in ONE evaluation — no maturity gate, no CI
       separation, no fresh_prob. A finite bin merely CONTAINING the extreme
       is NOT a hard fact for either side (estimator lane unchanged: the
       Seoul-incident replay antibody must keep passing).
  R14. buy_no symmetric lane: NO on the absorbing shoulder the extreme entered
       = structural loss -> exit; NO on a killed bin = structural WIN -> hold.
  R15. Source discipline: WU kills at face value; METAR kills only at
       settlement-faithful cities with the empirical-divergence margin;
       an active oracle-anomaly pause suspends the lane entirely.
  R16. Resting-order cancel: open BUY entry orders on hard-fact-dead day0
       bins (or anomaly-paused families) are cancelled; alive bins, SELL
       orders, and non-day0 dates are untouched.
  R17. Plausibility bound: an isolated implausible METAR print is quarantined
       (no ratchet, no bin-kill); a corroborated frontal jump is accepted; the
       latest uncorroborated print waits one report.
  R18. Day0 exposure cap: reduce-only clamp; exhausted headroom is a
       deterministic ValueError (-> no-submit receipt), never a bigger stake.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.execution.day0_hard_fact_exit import (
    HardFactVerdict,
    _reset_wu_memo_for_tests,
    cancel_day0_dead_bin_resting_entries,
    evaluate_hard_fact_exit,
    hard_fact_bin_verdict,
    settlement_grade_effective_extreme,
)
from src.data.day0_oracle_anomaly import (
    _reset_registry_for_tests,
    flag_day0_oracle_anomaly,
    metar_quarantine_counts,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 10, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    _reset_registry_for_tests()
    _reset_wu_memo_for_tests()
    # Default: no WU source (tests opt in); no METAR memo (tests opt in).
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit._wu_rounded_extremes",
        lambda city, target_date, now: (None, None),
    )
    yield
    _reset_registry_for_tests()
    _reset_wu_memo_for_tests()


def _tokyo():
    # settlement-faithful, empirical threshold 1.0 (config/wu_metar_divergence.json)
    return SimpleNamespace(
        name="Tokyo", timezone="Asia/Tokyo", settlement_unit="C",
        wu_station="RJTT", settlement_source_type="wu_icao",
    )


def _wellington():
    # UNMEASURED city -> default_guess threshold (C: 1.0)... use an F default city
    return SimpleNamespace(
        name="Wellington", timezone="Pacific/Auckland", settlement_unit="C",
        wu_station="NZWN", settlement_source_type="wu_icao",
    )


def _position(**over):
    base = dict(
        trade_id="hf-test-1", city="Tokyo", target_date="2026-06-10",
        bin_label="25°C on June 10?", direction="buy_yes",
        temperature_metric="high", state="day0_window",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _set_metar_memo(monkeypatch, value):
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit._metar_rounded_extreme",
        lambda city_name, target_date, metric: value,
    )


# ===========================================================================
# R13/R14 — the verdict matrix (pure)
# ===========================================================================

class TestVerdictMatrix:
    def test_buy_yes_dead_bin_exits(self):
        v = hard_fact_bin_verdict(
            metric="high", direction="buy_yes", bin_low=25.0, bin_high=25.0,
            effective_extreme=26.0,
        )
        assert v is not None and v.action == "EXIT_DEAD_BIN"

    def test_buy_no_dead_bin_is_structural_win_hold(self):
        v = hard_fact_bin_verdict(
            metric="high", direction="buy_no", bin_low=25.0, bin_high=25.0,
            effective_extreme=26.0,
        )
        assert v is not None and v.action == "HOLD_STRUCTURAL_WIN"

    def test_buy_no_shoulder_entered_exits(self):
        """The reviewer's 'buy_no death-ride' scenario: the extreme ENTERED the
        open-high shoulder — a max can never leave it; NO has structurally lost."""
        v = hard_fact_bin_verdict(
            metric="high", direction="buy_no", bin_low=27.0, bin_high=None,
            effective_extreme=27.0,
        )
        assert v is not None and v.action == "EXIT_DEAD_BIN"

    def test_buy_yes_shoulder_entered_is_structural_win(self):
        v = hard_fact_bin_verdict(
            metric="high", direction="buy_yes", bin_low=27.0, bin_high=None,
            effective_extreme=28.0,
        )
        assert v is not None and v.action == "HOLD_STRUCTURAL_WIN"

    def test_finite_bin_containing_extreme_is_not_a_hard_fact(self):
        """A max can still leave a finite bin upward — estimator territory for
        BOTH sides (the maturity gate + CI separation stay in charge)."""
        for direction in ("buy_yes", "buy_no"):
            assert hard_fact_bin_verdict(
                metric="high", direction=direction, bin_low=25.0, bin_high=25.0,
                effective_extreme=25.0,
            ) is None

    def test_low_metric_symmetry(self):
        # LOW: bin dead when extreme drops below bin_low
        v = hard_fact_bin_verdict(
            metric="low", direction="buy_yes", bin_low=18.0, bin_high=18.0,
            effective_extreme=17.0,
        )
        assert v is not None and v.action == "EXIT_DEAD_BIN"
        # LOW shoulder ("18 or below"): min entered it -> NO lost
        v = hard_fact_bin_verdict(
            metric="low", direction="buy_no", bin_low=None, bin_high=18.0,
            effective_extreme=18.0,
        )
        assert v is not None and v.action == "EXIT_DEAD_BIN"

    def test_alive_bin_above_extreme_is_none(self):
        assert hard_fact_bin_verdict(
            metric="high", direction="buy_yes", bin_low=27.0, bin_high=27.0,
            effective_extreme=25.0,
        ) is None


# ===========================================================================
# R15 — source discipline + margins (calibration-artifact-keyed)
# ===========================================================================

class TestSourceDiscipline:
    def test_metar_kill_at_faithful_city_uses_zero_margin(self, monkeypatch):
        _set_metar_memo(monkeypatch, 26)
        effective, source = settlement_grade_effective_extreme(
            city=_tokyo(), target_date="2026-06-10", metric="high", now=NOW,
        )
        assert effective == pytest.approx(26.0)
        assert source == "metar_fast_lane"

    def test_metar_kill_at_unmeasured_city_carries_default_margin(self, monkeypatch):
        """default_guess city: the METAR extreme is shifted by the conservative
        threshold before it can kill — 26 at a C-default (1.0) city is
        effectively 25 (cannot kill the 25 bin); 27 effectively 26 (kills)."""
        _set_metar_memo(monkeypatch, 26)
        effective, _ = settlement_grade_effective_extreme(
            city=_wellington(), target_date="2026-06-10", metric="high", now=NOW,
        )
        assert effective == pytest.approx(25.0)
        v = hard_fact_bin_verdict(
            metric="high", direction="buy_yes", bin_low=25.0, bin_high=25.0,
            effective_extreme=effective,
        )
        assert v is None  # not beyond the edge after the margin
        _set_metar_memo(monkeypatch, 27)
        effective, _ = settlement_grade_effective_extreme(
            city=_wellington(), target_date="2026-06-10", metric="high", now=NOW,
        )
        assert effective == pytest.approx(26.0)

    def test_wu_kills_at_face_value_and_composes_with_metar(self, monkeypatch):
        monkeypatch.setattr(
            "src.execution.day0_hard_fact_exit._wu_rounded_extremes",
            lambda city, target_date, now: (26.0, 18.0),
        )
        _set_metar_memo(monkeypatch, None)
        effective, source = settlement_grade_effective_extreme(
            city=_tokyo(), target_date="2026-06-10", metric="high", now=NOW,
        )
        assert effective == pytest.approx(26.0) and source == "wu_api"
        _set_metar_memo(monkeypatch, 27)
        effective, source = settlement_grade_effective_extreme(
            city=_tokyo(), target_date="2026-06-10", metric="high", now=NOW,
        )
        assert effective == pytest.approx(27.0)  # absorbing compose: max
        assert source == "wu_api+metar_fast_lane"

    def test_no_source_yields_none(self, monkeypatch):
        _set_metar_memo(monkeypatch, None)
        effective, source = settlement_grade_effective_extreme(
            city=_tokyo(), target_date="2026-06-10", metric="high", now=NOW,
        )
        assert effective is None and source == ""


# ===========================================================================
# R13 — the lane end-to-end (reviewer scenario: exit in <=1 evaluation)
# ===========================================================================

class TestLaneEndToEnd:
    def test_boundary_death_exits_in_one_evaluation(self, monkeypatch):
        """Reviewer Q1 scenario: hold YES 25C, METAR prints 26.x — ONE call to
        the lane (== one monitor cycle) produces the exit verdict. No maturity
        gate, no CI separation, no fresh_prob involved."""
        _set_metar_memo(monkeypatch, 26)
        verdict = evaluate_hard_fact_exit(position=_position(), city=_tokyo(), now=NOW)
        assert verdict is not None and verdict.action == "EXIT_DEAD_BIN"
        assert verdict.source == "metar_fast_lane"

    def test_buy_no_death_ride_is_closed(self, monkeypatch):
        """Reviewer 'worst way to lose money #1': buy_no on the shoulder the
        extreme entered now exits via the hard-fact lane despite buy_no having
        no estimator-lane authority."""
        _set_metar_memo(monkeypatch, 27)
        verdict = evaluate_hard_fact_exit(
            position=_position(direction="buy_no", bin_label="27°C or higher"),
            city=_tokyo(), now=NOW,
        )
        assert verdict is not None and verdict.action == "EXIT_DEAD_BIN"

    def test_estimator_flip_is_not_a_hard_fact(self, monkeypatch):
        """The panic-sell category stays dead: extreme INSIDE the held finite
        bin -> lane returns None -> evaluate_exit (with all its hardening)
        remains the only exit authority."""
        _set_metar_memo(monkeypatch, 25)
        assert evaluate_hard_fact_exit(position=_position(), city=_tokyo(), now=NOW) is None

    def test_oracle_anomaly_pause_suspends_the_lane(self, monkeypatch):
        _set_metar_memo(monkeypatch, 30)
        flag_day0_oracle_anomaly("Tokyo", "2026-06-10", detail="test")
        assert evaluate_hard_fact_exit(position=_position(), city=_tokyo(), now=NOW) is None

    def test_unparseable_bin_label_holds(self, monkeypatch):
        _set_metar_memo(monkeypatch, 30)
        assert evaluate_hard_fact_exit(
            position=_position(bin_label="60-65"), city=_tokyo(), now=NOW,
        ) is None

    def test_cycle_runtime_wiring_and_trigger_not_evidence_gated(self):
        source = open("src/engine/cycle_runtime.py", encoding="utf-8").read()
        assert "evaluate_hard_fact_exit" in source
        assert "DAY0_HARD_FACT_BIN_DEAD" in source
        from src.engine.cycle_runtime import _D4_ASYMMETRIC_EXIT_TRIGGERS

        # the hard-fact trigger must NOT be gated by the statistical-evidence gate
        assert "DAY0_HARD_FACT_BIN_DEAD" not in _D4_ASYMMETRIC_EXIT_TRIGGERS


# ===========================================================================
# R16 — resting-order cancel sweep
# ===========================================================================

class _FakeClob:
    def __init__(self, orders):
        self.orders = orders
        self.cancelled: list[str] = []

    def get_open_orders(self):
        return list(self.orders)

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"orderID": order_id, "status": "CANCELED"}


def _orders_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE market_events (
            id INTEGER PRIMARY KEY, market_slug TEXT, city TEXT, target_date TEXT,
            condition_id TEXT, token_id TEXT, range_label TEXT,
            range_low REAL, range_high REAL, outcome TEXT, created_at TEXT)"""
    )
    rows = [
        # dead 25-bin YES token (extreme 26): MUST cancel
        ("highest-temperature-in-tokyo-on-june-10", "Tokyo", "2026-06-10",
         "c1", "tok-dead-yes", "25°C", 25.0, 25.0, "Yes"),
        # alive 27-bin YES token: keep
        ("highest-temperature-in-tokyo-on-june-10", "Tokyo", "2026-06-10",
         "c2", "tok-alive-yes", "27°C", 27.0, 27.0, "Yes"),
        # dead 25-bin NO token: structural WIN — keep resting
        ("highest-temperature-in-tokyo-on-june-10", "Tokyo", "2026-06-10",
         "c1", "tok-dead-no", "25°C", 25.0, 25.0, "No"),
        # non-day0 date: keep
        ("highest-temperature-in-tokyo-on-june-11", "Tokyo", "2026-06-11",
         "c3", "tok-tomorrow", "25°C", 25.0, 25.0, "Yes"),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO market_events (market_slug, city, target_date, condition_id,"
            " token_id, range_label, range_low, range_high, outcome, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,'')",
            r,
        )
    return conn


class TestRestingOrderCancel:
    NOW_TOKYO_DAY = datetime(2026, 6, 10, 6, 0, tzinfo=UTC)  # Jun 10 15:00 JST

    def test_dead_bin_buy_entry_cancelled_alive_and_winners_kept(self, monkeypatch):
        _set_metar_memo(monkeypatch, 26)
        clob = _FakeClob([
            {"orderID": "o1", "asset_id": "tok-dead-yes", "side": "BUY"},
            {"orderID": "o2", "asset_id": "tok-alive-yes", "side": "BUY"},
            {"orderID": "o3", "asset_id": "tok-dead-no", "side": "BUY"},
            {"orderID": "o4", "asset_id": "tok-tomorrow", "side": "BUY"},
            {"orderID": "o5", "asset_id": "tok-dead-yes", "side": "SELL"},  # exit lifecycle's
        ])
        n = cancel_day0_dead_bin_resting_entries(
            clob=clob, conn=_orders_conn(),
            cities_by_name={"Tokyo": _tokyo()}, now=self.NOW_TOKYO_DAY,
        )
        assert n == 1
        assert clob.cancelled == ["o1"]

    def test_anomaly_paused_family_cancels_all_its_day0_entries(self, monkeypatch):
        _set_metar_memo(monkeypatch, None)
        flag_day0_oracle_anomaly("Tokyo", "2026-06-10", detail="paris-class")
        clob = _FakeClob([
            {"orderID": "o1", "asset_id": "tok-dead-yes", "side": "BUY"},
            {"orderID": "o2", "asset_id": "tok-alive-yes", "side": "BUY"},
            {"orderID": "o4", "asset_id": "tok-tomorrow", "side": "BUY"},
        ])
        n = cancel_day0_dead_bin_resting_entries(
            clob=clob, conn=_orders_conn(),
            cities_by_name={"Tokyo": _tokyo()}, now=self.NOW_TOKYO_DAY,
        )
        assert n == 2
        assert set(clob.cancelled) == {"o1", "o2"}  # tomorrow's order untouched

    def test_cancel_failure_is_loud_but_not_fatal(self, monkeypatch):
        _set_metar_memo(monkeypatch, 26)

        class _FailingClob(_FakeClob):
            def cancel_order(self, order_id):
                raise RuntimeError("venue down")

        clob = _FailingClob([{"orderID": "o1", "asset_id": "tok-dead-yes", "side": "BUY"}])
        n = cancel_day0_dead_bin_resting_entries(
            clob=clob, conn=_orders_conn(),
            cities_by_name={"Tokyo": _tokyo()}, now=self.NOW_TOKYO_DAY,
        )
        assert n == 0  # no successful cancel, no exception


# ===========================================================================
# R17 — METAR plausibility bound (fix 4)
# ===========================================================================

class TestPlausibilityBound:
    def _reports(self, temps_with_minutes, station="RJTT"):
        from src.data.day0_fast_obs import MetarReport

        base = datetime(2026, 6, 10, 0, 0, tzinfo=UTC)  # Jun 10 09:00 JST
        return [
            MetarReport(
                station_id=station, obs_time=base + timedelta(minutes=m),
                receipt_time=base + timedelta(minutes=m + 4),
                temp_c=t, metar_type="METAR", raw=f"METAR {station} 21/15",
            )
            for m, t in temps_with_minutes
        ]

    def test_isolated_spike_is_quarantined_no_ratchet(self):
        from src.data.day0_fast_obs import running_extremes_for_local_day

        reports = self._reports([(0, 22.0), (30, 23.0), (60, 45.0), (90, 23.5)])
        ex = running_extremes_for_local_day(reports, city=_tokyo(), target_date="2026-06-10")
        assert ex.quarantined_implausible == 1
        assert ex.high_so_far == pytest.approx(23.5)  # the 45C print never ratchets
        assert ("Tokyo", "2026-06-10") in metar_quarantine_counts()

    def test_corroborated_frontal_jump_is_accepted(self):
        from src.data.day0_fast_obs import running_extremes_for_local_day

        # +9C in 30 min, but the NEXT report confirms the new level
        reports = self._reports([(0, 22.0), (30, 31.0), (60, 31.5)])
        ex = running_extremes_for_local_day(reports, city=_tokyo(), target_date="2026-06-10")
        assert ex.quarantined_implausible == 0
        assert ex.high_so_far == pytest.approx(31.5)

    def test_latest_print_implausible_step_waits_for_corroboration(self):
        from src.data.day0_fast_obs import running_extremes_for_local_day

        reports = self._reports([(0, 22.0), (30, 22.5), (60, 40.0)])
        ex = running_extremes_for_local_day(reports, city=_tokyo(), target_date="2026-06-10")
        assert ex.quarantined_implausible == 1
        assert ex.high_so_far == pytest.approx(22.5)

    def test_climatology_band_rejects_absurd_values(self):
        from src.data.day0_fast_obs import filter_plausible_values

        base = datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
        values = [(base, 22.0, None), (base + timedelta(minutes=30), 80.0, None)]
        accepted, quarantined = filter_plausible_values(
            values, unit="C", city_name="Tokyo", month=6,
        )
        assert quarantined >= 1
        assert all(v <= 60.0 for _, v, _ in accepted)


# ===========================================================================
# R18 — day0 exposure cap (PR#404 P0-1: a SIZING-KERNEL bound, not a post-clamp)
# ===========================================================================

class TestDay0ExposureCap:
    """Kernel-integration cases live in tests/test_day0_exposure_cap_kernel.py
    (real proof -> curve -> kernel path, incl. the operator's headroom-$20 /
    min-order-$30 boundary). Here: the config surface + the removal antibody."""

    def test_post_hoc_clamp_is_deleted(self):
        """PR#404 P0-1: the post-hoc clamp bypassed min-order semantics and could
        emit a stake below the real venue floor. It must stay deleted — the cap
        is a kernel bound (day0_headroom_usd) with first-class aborts."""
        import src.engine.event_reactor_adapter as era

        assert not hasattr(era, "_apply_day0_exposure_cap")
        source = open("src/engine/event_reactor_adapter.py", encoding="utf-8").read()
        assert "day0_headroom_usd" in source
        assert "SUBMIT_ABORTED_DAY0_CAP_EXHAUSTED" in source
        assert "SUBMIT_ABORTED_DAY0_CAP_BELOW_MIN_ORDER" in source

    def test_default_cap_is_modest_and_configurable(self):
        from src.engine.event_reactor_adapter import (
            _DAY0_FAMILY_NOTIONAL_CAP_DEFAULT_USD,
            _day0_family_notional_cap_usd,
        )

        assert 0.0 < _DAY0_FAMILY_NOTIONAL_CAP_DEFAULT_USD <= 100.0
        cap = _day0_family_notional_cap_usd()
        assert cap is None or cap > 0.0
