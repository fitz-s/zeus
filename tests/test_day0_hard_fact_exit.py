# Created: 2026-06-10
# Last reused or audited: 2026-06-17
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
  R17. Plausibility bound: an isolated implausible METAR print is held
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
    final_observed_bin_verdict,
    hard_fact_bin_verdict,
    hard_fact_monitor_belief,
    settlement_grade_effective_extreme,
)
from src.data.day0_oracle_anomaly import (
    _reset_registry_for_tests,
    flag_day0_oracle_anomaly,
    metar_held_counts,
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


def test_day0_hard_fact_eligible_for_quarantined_real_partial_exposure():
    from src.engine import cycle_runtime
    from src.state.portfolio import QUARANTINE_SENTINEL, Position

    day0 = Position(
        trade_id="day0-pos", market_id="m", city="Tokyo", cluster="asia",
        target_date="2026-06-10", bin_label="25C", direction="buy_yes",
        state="day0_window", shares=1.0,
    )
    active = Position(
        trade_id="active-pos", market_id="m", city="Tokyo", cluster="asia",
        target_date="2026-06-10", bin_label="25C", direction="buy_yes",
        state="active", shares=1.0,
    )
    quarantined_partial = Position(
        trade_id="q-pos", market_id="m", city="Lucknow", cluster="asia",
        target_date="2026-06-10", bin_label="35C or below", direction="buy_yes",
        state="quarantined", chain_state="entry_authority_quarantined",
        shares_filled=20.0, filled_cost_basis_usd=1.20,
    )
    quarantined_placeholder = Position(
        trade_id="q-placeholder", market_id="m", city=QUARANTINE_SENTINEL,
        cluster="unknown", target_date="2026-06-10", bin_label="UNKNOWN",
        direction="buy_yes", state="quarantined",
        chain_state="entry_authority_quarantined", shares_filled=20.0,
    )
    quarantined_no_exposure = Position(
        trade_id="q-empty", market_id="m", city="Tokyo", cluster="asia",
        target_date="2026-06-10", bin_label="25C", direction="buy_yes",
        state="quarantined", chain_state="entry_authority_quarantined",
    )

    # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): the legacy
    # state='quarantined'/chain_state='entry_authority_quarantined'
    # constructor inputs are remapped to their TRUE values (holding/synced)
    # by Position.__post_init__'s mixed-epoch bridge — no live Position can
    # carry state=='quarantined', so _quarantined_position_can_redecision's
    # own gate (state == 'quarantined') is now permanently unreachable (see
    # test_excision_t_consolidations_characterization.py, which
    # already pins this to always-False). All three legacy-"quarantined"
    # fixtures below resolve through the plain state-membership check
    # instead — they are all remapped to 'holding', which is eligible like
    # any other open position; the exposure/placeholder carve-outs this test
    # once exercised were quarantine-scar bookkeeping that no longer applies.
    assert cycle_runtime._day0_hard_fact_position_eligible(day0) is True
    assert cycle_runtime._day0_hard_fact_position_eligible(active) is True
    assert cycle_runtime._day0_hard_fact_position_eligible(quarantined_partial) is True
    assert cycle_runtime._day0_hard_fact_position_eligible(quarantined_placeholder) is True
    assert cycle_runtime._day0_hard_fact_position_eligible(quarantined_no_exposure) is True


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


def _paris():
    return SimpleNamespace(
        name="Paris", timezone="Europe/Paris", settlement_unit="C",
        wu_station="LFPB", settlement_source_type="wu_icao",
    )


def _manila():
    return SimpleNamespace(
        name="Manila", timezone="Asia/Manila", settlement_unit="C",
        wu_station="RPLL", settlement_source_type="wu_icao",
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
        lambda city_name, target_date, metric, **kwargs: value,
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

    def test_tokyo_low_buy_no_dead_bin_is_exact_monitor_structural_win(self):
        """Tokyo regression: LOW 21C buy_no with effective low 20 is exact q=1."""
        v = hard_fact_bin_verdict(
            metric="low", direction="buy_no", bin_low=21.0, bin_high=21.0,
            effective_extreme=20.0,
        )
        assert v is not None and v.action == "HOLD_STRUCTURAL_WIN"

        belief = hard_fact_monitor_belief(verdict=v, direction="buy_no")
        assert belief is not None
        assert belief.yes_verdict == "YES_DEAD"
        assert belief.yes_prob == pytest.approx(0.0)
        assert belief.held_verdict == "STRUCTURAL_WIN"
        assert belief.held_side_prob == pytest.approx(1.0)

    def test_low_finite_bin_containing_extreme_is_unresolved_intraday(self):
        assert hard_fact_bin_verdict(
            metric="low", direction="buy_yes", bin_low=21.0, bin_high=21.0,
            effective_extreme=21.0,
        ) is None
        assert hard_fact_bin_verdict(
            metric="low", direction="buy_no", bin_low=21.0, bin_high=21.0,
            effective_extreme=21.0,
        ) is None

    def test_direction_enum_is_normalized_before_verdict(self):
        from src.contracts.semantic_types import Direction

        v = hard_fact_bin_verdict(
            metric="low", direction=Direction.NO, bin_low=21.0, bin_high=21.0,
            effective_extreme=20.0,
        )
        assert v is not None and v.action == "HOLD_STRUCTURAL_WIN"

    def test_alive_bin_above_extreme_is_none(self):
        assert hard_fact_bin_verdict(
            metric="high", direction="buy_yes", bin_low=27.0, bin_high=27.0,
            effective_extreme=25.0,
        ) is None

    def test_final_observed_finite_bin_containment_is_settlement_verdict(self):
        yes = final_observed_bin_verdict(
            metric="high",
            direction="buy_yes",
            bin_low=32.0,
            bin_high=32.0,
            final_extreme=32.0,
        )
        no = final_observed_bin_verdict(
            metric="high",
            direction="buy_no",
            bin_low=32.0,
            bin_high=32.0,
            final_extreme=32.0,
        )
        assert yes is not None and yes.action == "HOLD_STRUCTURAL_WIN"
        assert no is not None and no.action == "EXIT_DEAD_BIN"


# ===========================================================================
# R15 — source discipline + margins (calibration-artifact-keyed)
# ===========================================================================

class TestSourceDiscipline:
    def _observation_instants_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE observation_instants (
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                source TEXT NOT NULL,
                timezone_name TEXT NOT NULL,
                local_timestamp TEXT NOT NULL,
                utc_timestamp TEXT NOT NULL,
                running_max REAL,
                running_min REAL,
                authority TEXT NOT NULL,
                causality_status TEXT,
                temperature_metric TEXT
            )"""
        )
        return conn

    def test_durable_observation_instants_low_structural_win_drives_hold(self, monkeypatch):
        """Paris regression: verified WU-hourly rows showed low 18C before the
        monitor tried to sell a 19C buy_no. The durable rows must be a hard fact
        source even when WU live API / METAR memo are cold."""
        monkeypatch.setattr(
            "src.execution.day0_hard_fact_exit._wu_rounded_extremes",
            lambda city, target_date, now: (_ for _ in ()).throw(AssertionError("WU API must not be called")),
        )
        _set_metar_memo(monkeypatch, None)
        conn = self._observation_instants_conn()
        for local_ts, utc_ts, low in [
            ("2026-06-20T00:00:00+02:00", "2026-06-19T22:00:00+00:00", 23.0),
            ("2026-06-20T05:00:00+02:00", "2026-06-20T03:00:00+00:00", 19.0),
            ("2026-06-20T06:00:00+02:00", "2026-06-20T04:00:00+00:00", 18.0),
            ("2026-06-20T07:00:00+02:00", "2026-06-20T05:00:00+00:00", 19.0),
        ]:
            conn.execute(
                "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "Paris",
                    "2026-06-20",
                    "wu_icao_history",
                    "Europe/Paris",
                    local_ts,
                    utc_ts,
                    24.0,
                    low,
                    "VERIFIED",
                    "OK",
                    None,
                ),
            )
        verdict = evaluate_hard_fact_exit(
            position=_position(
                city="Paris",
                target_date="2026-06-20",
                bin_label="Will the lowest temperature in Paris be 19°C on June 20?",
                direction="buy_no",
                temperature_metric="low",
            ),
            city=_paris(),
            now=datetime(2026, 6, 20, 4, 2, 40, tzinfo=UTC),
            world_conn=conn,
        )
        assert verdict is not None
        assert verdict.action == "HOLD_STRUCTURAL_WIN"
        assert verdict.rounded_extreme == pytest.approx(18.0)
        assert verdict.source == "durable_observation_instants"
        belief = hard_fact_monitor_belief(verdict=verdict, direction="buy_no")
        assert belief is not None
        assert belief.held_side_prob == pytest.approx(1.0)

    def test_durable_observation_instants_respects_local_date_and_now_floor(self, monkeypatch):
        """The durable lane must not repeat the UTC-date floor bug: target_date is
        the city-local date, while future UTC observations must still be ignored."""
        _set_metar_memo(monkeypatch, None)
        conn = self._observation_instants_conn()
        conn.execute(
            "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "Paris",
                "2026-06-20",
                "wu_icao_history",
                "Europe/Paris",
                "2026-06-20T00:00:00+02:00",
                "2026-06-19T22:00:00+00:00",
                23.0,
                23.0,
                "VERIFIED",
                "OK",
                None,
            ),
        )
        conn.execute(
            "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "Paris",
                "2026-06-20",
                "wu_icao_history",
                "Europe/Paris",
                "2026-06-20T06:00:00+02:00",
                "2026-06-20T04:00:00+00:00",
                24.0,
                18.0,
                "VERIFIED",
                "OK",
                None,
            ),
        )
        effective, source = settlement_grade_effective_extreme(
            city=_paris(),
            target_date="2026-06-20",
            metric="low",
            now=datetime(2026, 6, 20, 3, 30, tzinfo=UTC),
            world_conn=conn,
        )
        assert effective == pytest.approx(23.0)
        assert source == "durable_observation_instants"

    def test_final_day_durable_exact_bin_marks_buy_no_structural_loss(self, monkeypatch):
        """Manila regression: after local day completion, final high=32 means
        32C YES won and a held 32C NO is structurally dead. Intraday containment
        remains estimator territory; this only fires with durable end-of-day
        WU coverage."""
        _set_metar_memo(monkeypatch, None)
        conn = self._observation_instants_conn()
        for local_ts, utc_ts, high, low in [
            ("2026-06-29T00:00:00+08:00", "2026-06-28T16:00:00+00:00", 30.0, 28.0),
            ("2026-06-29T18:00:00+08:00", "2026-06-29T10:00:00+00:00", 32.0, 30.0),
            ("2026-06-29T23:00:00+08:00", "2026-06-29T15:00:00+00:00", 28.0, 28.0),
        ]:
            conn.execute(
                "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "Manila",
                    "2026-06-29",
                    "wu_icao_history",
                    "Asia/Manila",
                    local_ts,
                    utc_ts,
                    high,
                    low,
                    "VERIFIED",
                    "OK",
                    None,
                ),
            )

        verdict = evaluate_hard_fact_exit(
            position=_position(
                city="Manila",
                target_date="2026-06-29",
                bin_label="Will the highest temperature in Manila be 32°C on June 29?",
                direction="buy_no",
                temperature_metric="high",
            ),
            city=_manila(),
            now=datetime(2026, 6, 30, 3, 40, tzinfo=UTC),
            world_conn=conn,
        )
        assert verdict is not None
        assert verdict.action == "EXIT_DEAD_BIN"
        assert verdict.rounded_extreme == pytest.approx(32.0)
        assert verdict.source == "durable_observation_instants"
        belief = hard_fact_monitor_belief(verdict=verdict, direction="buy_no")
        assert belief is not None
        assert belief.yes_prob == pytest.approx(1.0)
        assert belief.held_side_prob == pytest.approx(0.0)

    def test_final_day_exact_bin_does_not_fire_before_local_day_complete(self, monkeypatch):
        _set_metar_memo(monkeypatch, None)
        conn = self._observation_instants_conn()
        for local_ts, utc_ts, high, low in [
            ("2026-06-29T18:00:00+08:00", "2026-06-29T10:00:00+00:00", 32.0, 30.0),
            ("2026-06-29T23:00:00+08:00", "2026-06-29T15:00:00+00:00", 32.0, 28.0),
        ]:
            conn.execute(
                "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "Manila",
                    "2026-06-29",
                    "wu_icao_history",
                    "Asia/Manila",
                    local_ts,
                    utc_ts,
                    high,
                    low,
                    "VERIFIED",
                    "OK",
                    None,
                ),
            )

        assert evaluate_hard_fact_exit(
            position=_position(
                city="Manila",
                target_date="2026-06-29",
                bin_label="Will the highest temperature in Manila be 32°C on June 29?",
                direction="buy_no",
                temperature_metric="high",
            ),
            city=_manila(),
            now=datetime(2026, 6, 29, 15, 30, tzinfo=UTC),
            world_conn=conn,
        ) is None

    def test_evaluate_hard_fact_exit_normalizes_direction_enum(self, monkeypatch):
        from src.contracts.semantic_types import Direction

        monkeypatch.setattr(
            "src.execution.day0_hard_fact_exit._wu_rounded_extremes",
            lambda city, target_date, now: (None, 20.0),
        )
        verdict = evaluate_hard_fact_exit(
            position=_position(
                direction=Direction.NO,
                temperature_metric="low",
                bin_label="21°C on June 18?",
                target_date="2026-06-18",
            ),
            city=_tokyo(),
            now=NOW,
        )
        assert verdict is not None
        assert verdict.action == "HOLD_STRUCTURAL_WIN"
        belief = hard_fact_monitor_belief(verdict=verdict, direction=Direction.NO)
        assert belief is not None
        assert belief.held_side_prob == pytest.approx(1.0)

    def test_metar_kill_at_faithful_city_uses_zero_margin(self, monkeypatch):
        _set_metar_memo(monkeypatch, 26)
        effective, source = settlement_grade_effective_extreme(
            city=_tokyo(), target_date="2026-06-10", metric="high", now=NOW,
        )
        assert effective == pytest.approx(26.0)
        assert source == "same_station_fast_tail"

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
        assert source == "wu_api+same_station_fast_tail"

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
        assert verdict.source == "same_station_fast_tail"

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
    """PRODUCTION-LIKE topology (PR#404 P1): market_events stores ONLY the YES
    token and NO temperature_metric column (the trades-DB shape);
    executable_market_snapshots carries yes_token_id/no_token_id;
    market_topology_state carries the TYPED temperature_metric."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE market_events (
            id INTEGER PRIMARY KEY, market_slug TEXT, city TEXT, target_date TEXT,
            condition_id TEXT, token_id TEXT, range_label TEXT,
            range_low REAL, range_high REAL, outcome TEXT, created_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT, condition_id TEXT, yes_token_id TEXT,
            no_token_id TEXT, captured_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE market_topology_state (
            condition_id TEXT, temperature_metric TEXT, city_id TEXT,
            target_local_date TEXT, recorded_at TEXT)"""
    )
    families = [
        # (cond, yes_token, no_token, city, date, lo, hi)
        ("c1", "tok-dead-yes", "tok-dead-no", "Tokyo", "2026-06-10", 25.0, 25.0),
        ("c2", "tok-alive-yes", "tok-alive-no", "Tokyo", "2026-06-10", 27.0, 27.0),
        ("c3", "tok-tomorrow", "tok-tomorrow-no", "Tokyo", "2026-06-11", 25.0, 25.0),
        # the buy_no death-ride family: open-high shoulder ('27 or higher')
        ("c4", "tok-shoulder-yes", "tok-shoulder-no", "Tokyo", "2026-06-10", 27.0, None),
    ]
    for cond, yes_t, no_t, city, date, lo, hi in families:
        conn.execute(
            "INSERT INTO market_events (market_slug, city, target_date, condition_id,"
            " token_id, range_label, range_low, range_high, outcome, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,'')",
            (f"highest-temperature-in-tokyo-on-{date}", city, date, cond, yes_t,
             "bin", lo, hi, ""),
        )
        conn.execute(
            "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?)",
            (f"ems-{cond}", cond, yes_t, no_t, "2026-06-10T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO market_topology_state VALUES (?,?,?,?,?)",
            (cond, "high", city, date, "2026-06-10T00:00:00+00:00"),
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

    def test_no_token_resolves_via_ems_and_shoulder_no_order_is_cancelled(self, monkeypatch):
        """PR#404 P1 production-topology case: the open order's asset is the
        NO token, which exists ONLY in executable_market_snapshots.no_token_id
        (market_events stores the YES token). The shoulder family (27-or-
        higher) with extreme 27 means buy_no is structurally DEAD -> the
        resting BUY-NO entry on the shoulder must be found AND cancelled,
        while the dead-bin NO order (structural WIN) is kept."""
        _set_metar_memo(monkeypatch, 27)
        clob = _FakeClob([
            {"orderID": "oN1", "asset_id": "tok-shoulder-no", "side": "BUY"},  # NO lost -> cancel
            {"orderID": "oN2", "asset_id": "tok-dead-no", "side": "BUY"},      # NO won -> keep
            {"orderID": "oN3", "asset_id": "tok-alive-no", "side": "BUY"},     # alive -> keep
        ])
        n = cancel_day0_dead_bin_resting_entries(
            clob=clob, conn=_orders_conn(),
            cities_by_name={"Tokyo": _tokyo()}, now=self.NOW_TOKYO_DAY,
        )
        assert n == 1
        assert clob.cancelled == ["oN1"]

    def test_metric_is_typed_never_slug_guessed(self):
        """The metric comes from market_topology_state.temperature_metric /
        market_events.temperature_metric — never from slug substrings. A token
        whose metric cannot be typed is SKIPPED (no wrong-direction cancel)."""
        from src.execution.day0_hard_fact_exit import _resolve_order_bin_identity

        conn = _orders_conn()
        identity = _resolve_order_bin_identity(conn, "tok-shoulder-no")
        assert identity is not None
        assert identity["metric"] == "high" and identity["direction"] == "buy_no"
        # drop the typed-metric authority -> resolution refuses (None), even
        # though the slug contains 'highest'
        conn.execute("DELETE FROM market_topology_state")
        assert _resolve_order_bin_identity(conn, "tok-shoulder-no") is None
        source = open("src/execution/day0_hard_fact_exit.py", encoding="utf-8").read()
        assert '"lowest" in market_slug' not in source


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

    def test_isolated_spike_is_held_no_ratchet(self):
        from src.data.day0_fast_obs import running_extremes_for_local_day

        reports = self._reports([(0, 22.0), (30, 23.0), (60, 45.0), (90, 23.5)])
        ex = running_extremes_for_local_day(reports, city=_tokyo(), target_date="2026-06-10")
        assert ex.held_implausible == 1
        assert ex.high_so_far == pytest.approx(23.5)  # the 45C print never ratchets
        assert ("Tokyo", "2026-06-10") in metar_held_counts()

    def test_corroborated_frontal_jump_is_accepted(self):
        from src.data.day0_fast_obs import running_extremes_for_local_day

        # +9C in 30 min, but the NEXT report confirms the new level
        reports = self._reports([(0, 22.0), (30, 31.0), (60, 31.5)])
        ex = running_extremes_for_local_day(reports, city=_tokyo(), target_date="2026-06-10")
        assert ex.held_implausible == 0
        assert ex.high_so_far == pytest.approx(31.5)

    def test_latest_print_implausible_step_waits_for_corroboration(self):
        from src.data.day0_fast_obs import running_extremes_for_local_day

        reports = self._reports([(0, 22.0), (30, 22.5), (60, 40.0)])
        ex = running_extremes_for_local_day(reports, city=_tokyo(), target_date="2026-06-10")
        assert ex.held_implausible == 1
        assert ex.high_so_far == pytest.approx(22.5)

    def test_climatology_band_rejects_absurd_values(self):
        from src.data.day0_fast_obs import filter_plausible_values

        base = datetime(2026, 6, 10, 0, 0, tzinfo=UTC)
        values = [(base, 22.0, None), (base + timedelta(minutes=30), 80.0, None)]
        accepted, held = filter_plausible_values(
            values, unit="C", city_name="Tokyo", month=6,
        )
        assert held >= 1
        assert all(v <= 60.0 for _, v, _ in accepted)


# ===========================================================================
# R18 — day0 exposure cap (fix 5)
# ===========================================================================

class TestDay0ExposureCap:
    """Wave-1 2026-06-12: the DAY0 family notional cap ($25) is DELETED ENTIRELY
    (operator no-caps law). These antibody tests verify the cap symbols and the
    sizing-kernel headroom bound are gone — day0 sizing is now q_lcb + fractional
    Kelly + free-cash + concentration ONLY, identical to every other lane."""

    def test_day0_notional_cap_symbols_are_deleted(self):
        import src.engine.event_reactor_adapter as era

        assert not hasattr(era, "_DAY0_FAMILY_NOTIONAL_CAP_DEFAULT_USD"), (
            "the $25 day0 family notional cap default must be deleted (no-caps law)"
        )
        assert not hasattr(era, "_day0_family_notional_cap_usd"), (
            "the day0 family notional cap reader must be deleted (no-caps law)"
        )

    def test_apply_day0_exposure_cap_is_removed(self):
        """The post-hoc clamp must not exist; importing it should fail."""
        import src.engine.event_reactor_adapter as era
        assert not hasattr(era, "_apply_day0_exposure_cap"), (
            "_apply_day0_exposure_cap must be removed (PR#404 P0-1 tombstone)"
        )

    def test_day0_cap_exception_classes_are_deleted(self):
        """The cap-abort exception classes must be gone with the cap."""
        import src.engine.event_reactor_adapter as era
        assert not hasattr(era, "_Day0CapExhausted")
        assert not hasattr(era, "_Day0CapBelowMinOrder")


# ===========================================================================
# R20 — hard-fact exit survives monitor canonical-write failure (PR#404 P0-4)
# ===========================================================================

class TestHardFactExitDespiteCanonicalWriteFailure:
    """Operator merge blocker 4: the hard-fact lane is settlement-authority
    evidence — a monitor telemetry/canonical-event write failure must not
    `continue` past it and hold a structurally dead leg another cycle."""

    def _run_phase(self, monkeypatch, *, hard_fact_verdict):
        import logging as _logging

        import numpy as np

        from src.contracts import EdgeContext, EntryMethod
        from src.engine import cycle_runtime
        from src.state.portfolio import Position, PortfolioState

        monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
        pos = Position(
            trade_id="hf_p04_001", market_id="mkt_hf", city="Tokyo",
            cluster="East Asia", target_date="2026-06-10",
            bin_label="25°C on June 10?", direction="buy_yes",
            size_usd=10.0, entry_price=0.40, p_posterior=0.55, edge=0.15,
            shares=25.0, cost_basis_usd=10.0, state="day0_window",
            token_id="tok_yes_hf", no_token_id="tok_no_hf", unit="C", env="live",
        )
        portfolio = PortfolioState(positions=[pos])

        class LiveClob:
            def get_best_bid_ask(self, token_id):
                return 0.10, 0.12, 100.0, 100.0

        class Tracker:
            def record_exit(self, position):
                pass

        def mock_refresh(conn, clob, position):
            return EdgeContext(
                p_raw=np.array([]), p_cal=np.array([]),
                p_market=np.array([position.entry_price]),
                p_posterior=position.p_posterior,
                forward_edge=0.0, alpha=0.0,
                confidence_band_upper=0.0, confidence_band_lower=0.0,
                entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
                decision_snapshot_id="snap1", n_edges_found=1, n_edges_after_fdr=1,
                market_velocity_1h=0.0, divergence_score=0.0,
            )

        monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
        # THE P0-4 condition: the canonical MONITOR_REFRESHED write FAILS
        monkeypatch.setattr(
            cycle_runtime, "_emit_monitor_refreshed_canonical_if_available",
            lambda conn, pos, *, deps, **kwargs: False,
        )
        monkeypatch.setattr(
            "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
            lambda *, position, city, now=None, world_conn=None, **kwargs: hard_fact_verdict,
        )

        results = []

        class Artifact:
            def add_monitor_result(self, result):
                results.append(result)

        deps = type(
            "Deps", (),
            {
                "MonitorResult": type(
                    "MonitorResult", (),
                    {"__init__": lambda self, **kw: self.__dict__.update(kw)},
                ),
                "logger": _logging.getLogger("test_hf_p04"),
                "cities_by_name": {
                    "Tokyo": type("City", (), {"timezone": "Asia/Tokyo"})()
                },
                "_utcnow": staticmethod(
                    lambda: datetime(2026, 6, 10, 6, 0, tzinfo=UTC)
                ),
            },
        )
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None, LiveClob(), portfolio, Artifact(), Tracker(), summary,
            deps=deps, exit_order_submit_enabled=False,
        )
        return results, summary

    def test_dead_bin_exits_even_when_canonical_write_fails(self, monkeypatch):
        verdict = HardFactVerdict(
            action="EXIT_DEAD_BIN",
            reason="running high extreme 26.0 beyond bin [25.0,25.0] — YES structurally dead",
            metric="high", rounded_extreme=26.0, source="same_station_fast_tail",
        )
        results, summary = self._run_phase(monkeypatch, hard_fact_verdict=verdict)
        assert summary.get("day0_hard_fact_exits") == 1
        assert summary.get("day0_hard_fact_exit_despite_canonical_write_failure") == 1
        assert summary.get("monitor_canonical_write_failed") == 1
        exits = [r for r in results if getattr(r, "should_exit", False)]
        assert exits, "the dead-bin exit decision must be recorded despite the write failure"
        assert any("DAY0_HARD_FACT_BIN_DEAD" in str(getattr(r, "exit_reason", "")) for r in exits)
        assert summary.get("exits_suppressed_no_submit", 0) >= 1  # submit-disabled fixture: decision made, no order

    def test_no_hard_fact_keeps_the_existing_failure_continue(self, monkeypatch):
        results, summary = self._run_phase(monkeypatch, hard_fact_verdict=None)
        assert summary.get("monitor_canonical_write_failed") == 1
        assert summary.get("day0_hard_fact_exits") is None
        reasons = [str(getattr(r, "exit_reason", "")) for r in results]
        assert any("MONITOR_CANONICAL_WRITE_FAILED" in reason for reason in reasons)
        assert not any(getattr(r, "should_exit", False) for r in results)


# ===========================================================================
# BLOCKER 2 — HOLD_STRUCTURAL_WIN is a terminal hold: evaluate_exit + ORANGE
#             must never sell a structurally-won position.
# ===========================================================================

class TestStructuralWinTerminalHold:
    """PR#404 BLOCKER 2: HOLD_STRUCTURAL_WIN must produce a hard should_exit=False
    decision that skips the estimator-evidence path AND the ORANGE favorable-exit
    layer.  An ORANGE context that would otherwise trigger a favorable exit must
    be held when a structural-win verdict is present.

    Cross-module invariant: day0_hard_fact.action=HOLD_STRUCTURAL_WIN at
    cycle_runtime.execute_monitoring_phase → should_exit=False regardless of
    what pos.evaluate_exit() or the ORANGE gate would return.
    """

    def _run_structural_win_phase(
        self, monkeypatch, *, evaluate_exit_says_exit: bool, summary_risk_level: str
    ):
        """Run one monitor cycle with:
        - hard_fact.action=HOLD_STRUCTURAL_WIN
        - evaluate_exit stubbed to return should_exit=evaluate_exit_says_exit
        - _summary_risk_level stubbed to return summary_risk_level
        """
        import logging as _logging
        import numpy as np

        from src.contracts import EdgeContext, EntryMethod
        from src.engine import cycle_runtime
        from src.state.portfolio import ExitDecision as _ExitDecision, Position, PortfolioState

        monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
        pos = Position(
            trade_id="sw_blocker2_001", market_id="mkt_sw", city="Tokyo",
            cluster="East Asia", target_date="2026-06-10",
            bin_label="25°C or higher", direction="buy_no",
            size_usd=10.0, entry_price=0.70, p_posterior=0.80, edge=0.10,
            shares=14.3, cost_basis_usd=10.0, state="day0_window",
            token_id="tok_no_sw", no_token_id="tok_no_sw", unit="C", env="live",
        )
        portfolio = PortfolioState(positions=[pos])

        class LiveClob:
            def get_best_bid_ask(self, token_id):
                return 0.60, 0.62, 100.0, 100.0

        class Tracker:
            def record_exit(self, position):
                pass

        def mock_refresh(conn, clob, position):
            return EdgeContext(
                p_raw=np.array([]), p_cal=np.array([]),
                p_market=np.array([position.entry_price]),
                p_posterior=position.p_posterior,
                forward_edge=0.0, alpha=0.0,
                confidence_band_upper=0.0, confidence_band_lower=0.0,
                entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
                decision_snapshot_id="snap_sw", n_edges_found=1, n_edges_after_fdr=1,
                market_velocity_1h=0.0, divergence_score=0.0,
            )

        monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
        monkeypatch.setattr(
            cycle_runtime, "_emit_monitor_refreshed_canonical_if_available",
            lambda conn, pos, *, deps, **kwargs: True,
        )
        # Stub hard-fact verdict: HOLD_STRUCTURAL_WIN (buy_no dead bin)
        hold_verdict = HardFactVerdict(
            action="HOLD_STRUCTURAL_WIN",
            reason="buy_no on dead bin [25.0,25.0] — structural win",
            metric="high", rounded_extreme=27.0, source="wu_icao",
        )
        monkeypatch.setattr(
            "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
            lambda *, position, city, now=None, world_conn=None, **kwargs: hold_verdict,
        )
        # Stub evaluate_exit to return the requested should_exit value
        monkeypatch.setattr(
            pos,
            "evaluate_exit",
            lambda ctx: _ExitDecision(
                evaluate_exit_says_exit,
                "STUB_EXIT_FROM_ESTIMATOR",
                trigger="STUB_ESTIMATOR_EXIT",
                selected_method=pos.selected_method or pos.entry_method,
            ),
        )
        # Stub _summary_risk_level to simulate ORANGE context
        monkeypatch.setattr(
            cycle_runtime, "_summary_risk_level",
            lambda summary: summary_risk_level,
        )

        results = []

        class Artifact:
            def add_monitor_result(self, result):
                results.append(result)

        deps = type(
            "Deps", (),
            {
                "MonitorResult": type(
                    "MonitorResult", (),
                    {"__init__": lambda self, **kw: self.__dict__.update(kw)},
                ),
                "logger": _logging.getLogger("test_sw_blocker2"),
                "cities_by_name": {
                    "Tokyo": type("City", (), {"timezone": "Asia/Tokyo"})()
                },
                "_utcnow": staticmethod(
                    lambda: datetime(2026, 6, 10, 6, 0, tzinfo=UTC)
                ),
            },
        )
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None, LiveClob(), portfolio, Artifact(), Tracker(), summary,
            deps=deps, exit_order_submit_enabled=False,
        )
        return results, summary

    def test_structural_win_held_even_when_evaluate_exit_says_exit(self, monkeypatch):
        """The key invariant: evaluate_exit stub returns should_exit=True, but
        HOLD_STRUCTURAL_WIN must produce should_exit=False — the structural hold
        is a terminal decision that skips the estimator-evidence path."""
        results, summary = self._run_structural_win_phase(
            monkeypatch, evaluate_exit_says_exit=True, summary_risk_level="GREEN",
        )
        assert summary.get("day0_hard_fact_structural_win_holds") == 1
        assert summary.get("day0_hard_fact_exits") is None
        assert not any(getattr(r, "should_exit", False) for r in results), (
            "structural-win hold must block the estimator exit (should_exit must be False)"
        )
        # Trigger should be the structural-win hold, not the estimator
        triggers = [str(getattr(r, "exit_trigger", getattr(r, "exit_reason", ""))) for r in results]
        assert any("STRUCTURAL_WIN_HOLD" in t for t in triggers), (
            f"expected DAY0_HARD_FACT_STRUCTURAL_WIN_HOLD trigger, got: {triggers}"
        )

    def test_orange_favorable_exit_cannot_override_structural_win_hold(self, monkeypatch):
        """ORANGE context + HOLD_STRUCTURAL_WIN → still held. The ORANGE gate must
        be skipped entirely when a structural-win verdict is present, so a
        favorable ORANGE exit cannot sell a structurally won buy_no position."""
        results, summary = self._run_structural_win_phase(
            monkeypatch, evaluate_exit_says_exit=False, summary_risk_level="ORANGE",
        )
        assert summary.get("day0_hard_fact_structural_win_holds") == 1
        assert not any(getattr(r, "should_exit", False) for r in results), (
            "ORANGE gate must not override a structural-win hold"
        )
        # ORANGE favorable-exit counter must NOT be incremented
        assert summary.get("risk_orange_favorable_exits", 0) == 0, (
            "ORANGE favorable_exits counter must be 0 for a structural-win hold"
        )

    def test_kill_switch_via_exit_dead_bin_overrides_structural_win(self, monkeypatch):
        """Separately named: EXIT_DEAD_BIN (kill-switch / manual reduce-only) CAN
        override the structural hold — it is a stronger verdict on the same axis."""
        import logging as _logging
        import numpy as np
        from src.contracts import EdgeContext, EntryMethod
        from src.engine import cycle_runtime
        from src.state.portfolio import Position, PortfolioState

        monkeypatch.setenv("ZEUS_MARKET_PHASE_DISPATCH", "0")
        pos = Position(
            trade_id="sw_ks_001", market_id="mkt_ks", city="Tokyo",
            cluster="East Asia", target_date="2026-06-10",
            bin_label="25°C or higher", direction="buy_no",
            size_usd=10.0, entry_price=0.70, p_posterior=0.80, edge=0.10,
            shares=14.3, cost_basis_usd=10.0, state="day0_window",
            token_id="tok_no_ks", no_token_id="tok_no_ks", unit="C", env="live",
        )
        portfolio = PortfolioState(positions=[pos])

        class LiveClob:
            def get_best_bid_ask(self, token_id):
                return 0.60, 0.62, 100.0, 100.0

        class Tracker:
            def record_exit(self, position):
                pass

        def mock_refresh(conn, clob, position):
            return EdgeContext(
                p_raw=np.array([]), p_cal=np.array([]),
                p_market=np.array([position.entry_price]),
                p_posterior=position.p_posterior,
                forward_edge=0.0, alpha=0.0,
                confidence_band_upper=0.0, confidence_band_lower=0.0,
                entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
                decision_snapshot_id="snap_ks", n_edges_found=1, n_edges_after_fdr=1,
                market_velocity_1h=0.0, divergence_score=0.0,
            )

        monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
        monkeypatch.setattr(
            cycle_runtime, "_emit_monitor_refreshed_canonical_if_available",
            lambda conn, pos, *, deps, **kwargs: True,
        )
        exit_verdict = HardFactVerdict(
            action="EXIT_DEAD_BIN",
            reason="manual reduce-only override — buy_no exited",
            metric="high", rounded_extreme=27.0, source="wu_icao",
        )
        monkeypatch.setattr(
            "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
            lambda *, position, city, now=None, world_conn=None, **kwargs: exit_verdict,
        )

        results = []

        class Artifact:
            def add_monitor_result(self, result):
                results.append(result)

        deps = type(
            "Deps", (),
            {
                "MonitorResult": type(
                    "MonitorResult", (),
                    {"__init__": lambda self, **kw: self.__dict__.update(kw)},
                ),
                "logger": _logging.getLogger("test_ks"),
                "cities_by_name": {
                    "Tokyo": type("City", (), {"timezone": "Asia/Tokyo"})()
                },
                "_utcnow": staticmethod(
                    lambda: datetime(2026, 6, 10, 6, 0, tzinfo=UTC)
                ),
            },
        )
        summary = {"monitors": 0, "exits": 0}
        cycle_runtime.execute_monitoring_phase(
            None, LiveClob(), portfolio, Artifact(), Tracker(), summary,
            deps=deps, exit_order_submit_enabled=False,
        )
        assert summary.get("day0_hard_fact_exits") == 1
        exits = [r for r in results if getattr(r, "should_exit", False)]
        assert exits, "EXIT_DEAD_BIN verdict must override and produce should_exit=True"


def test_pending_exit_position_is_still_re_evaluated_without_duplicate_submit(monkeypatch):
    import logging as _logging
    import numpy as np

    from src.contracts import EdgeContext, EntryMethod
    from src.engine import cycle_runtime
    from src.state.portfolio import ExitDecision as _ExitDecision, Position, PortfolioState

    pos = Position(
        trade_id="pending_exit_redecision_001",
        market_id="mkt_pending_exit",
        city="Tokyo",
        cluster="East Asia",
        target_date="2026-06-10",
        bin_label="25°C on June 10?",
        direction="buy_no",
        size_usd=10.0,
        entry_price=0.70,
        p_posterior=0.80,
        edge=0.10,
        shares=14.3,
        cost_basis_usd=10.0,
        state="pending_exit",
        token_id="tok_no_pending",
        no_token_id="tok_no_pending",
        unit="C",
        env="live",
        strategy_key="settlement_capture",
    )
    portfolio = PortfolioState(positions=[pos])
    calls = {"refresh": 0}

    def mock_refresh(conn, clob, position):
        calls["refresh"] += 1
        position.last_monitor_prob = 0.05
        position.last_monitor_prob_is_fresh = True
        position.last_monitor_market_price = 0.02
        position.last_monitor_market_price_is_fresh = True
        return EdgeContext(
            p_raw=np.array([]),
            p_cal=np.array([]),
            p_market=np.array([0.02]),
            p_posterior=0.05,
            forward_edge=-0.65,
            alpha=0.0,
            confidence_band_upper=0.0,
            confidence_band_lower=0.0,
            entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
            decision_snapshot_id="snap_pending_exit",
            n_edges_found=1,
            n_edges_after_fdr=1,
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", mock_refresh)
    monkeypatch.setattr(
        "src.execution.exit_lifecycle.handle_exit_pending_missing",
        lambda portfolio, pos, conn=None: {"action": "none"},
    )
    monkeypatch.setattr(
        "src.execution.day0_hard_fact_exit.evaluate_hard_fact_exit",
        lambda *, position, city, now=None, world_conn=None, **kwargs: None,
    )
    monkeypatch.setattr(
        cycle_runtime,
        "_emit_monitor_refreshed_canonical_if_available",
        lambda conn, pos, *, deps, **kwargs: True,
    )
    monkeypatch.setattr(
        pos,
        "evaluate_exit",
        lambda ctx: _ExitDecision(
            True,
            "STILL_ADVERSE_WHILE_EXIT_PENDING",
            trigger="ADVERSE_PENDING_EXIT",
            selected_method=pos.selected_method or pos.entry_method,
        ),
    )

    results = []

    class Artifact:
        def add_monitor_result(self, result):
            results.append(result)

    class Tracker:
        def record_exit(self, position):
            raise AssertionError("pending_exit monitor-only path must not record a duplicate exit")

    deps = type(
        "Deps",
        (),
        {
            "MonitorResult": type(
                "MonitorResult",
                (),
                {"__init__": lambda self, **kw: self.__dict__.update(kw)},
            ),
            "logger": _logging.getLogger("test_pending_exit_redecision"),
            "cities_by_name": {"Tokyo": type("City", (), {"timezone": "Asia/Tokyo"})()},
            "_utcnow": staticmethod(lambda: datetime(2026, 6, 10, 6, 0, tzinfo=UTC)),
        },
    )
    summary = {"monitors": 0, "exits": 0}

    cycle_runtime.execute_monitoring_phase(
        None,
        object(),
        portfolio,
        Artifact(),
        Tracker(),
        summary,
        deps=deps,
        exit_order_submit_enabled=True,
    )

    assert calls["refresh"] == 1
    assert summary.get("monitor_released_pending_exit_without_order") == 1
    assert summary.get("monitor_pending_exit_phase_evaluated") is None
    assert summary.get("pending_exit_exit_signal_already_in_flight") is None
    assert summary["exits"] == 1
    assert results and results[0].should_exit is True


# ===========================================================================
# R25 — PR#404 ROUND-2 P1-B: topology resolver is row-factory-agnostic
# ===========================================================================

class TestTupleConnectionTopology:
    """The resolver must not depend on sqlite3.Row being installed — a default
    tuple connection previously yielded an EMPTY identity dict and the
    dead-bin resting order silently escaped cancellation."""

    def _tuple_conn(self):
        # SAME schema/rows as _orders_conn but with NO row_factory (tuples).
        rows_conn = _orders_conn()
        rows_conn.commit()  # backup() busy-loops forever on a pending write txn
        raw = sqlite3.connect(":memory:")  # default: tuple rows
        rows_conn.backup(raw)
        rows_conn.close()
        assert raw.row_factory is None
        return raw

    def test_resolver_works_on_tuple_rows(self):
        from src.execution.day0_hard_fact_exit import _resolve_order_bin_identity

        conn = self._tuple_conn()
        identity = _resolve_order_bin_identity(conn, "tok-dead-yes")
        assert identity is not None
        assert identity["city"] == "Tokyo"
        assert identity["target_date"] == "2026-06-10"
        assert identity["metric"] == "high"
        assert identity["direction"] == "buy_yes"
        assert float(identity["range_high"]) == 25.0
        # NO token via EMS on tuples too
        identity_no = _resolve_order_bin_identity(conn, "tok-shoulder-no")
        assert identity_no is not None and identity_no["direction"] == "buy_no"

    def test_dead_bin_cancel_fires_on_tuple_connection(self, monkeypatch):
        """End-to-end: the risk-reduction sweep cancels the dead-bin order even
        when the monitor's connection lacks a Row factory."""
        _set_metar_memo(monkeypatch, 26)
        clob = _FakeClob([
            {"orderID": "o1", "asset_id": "tok-dead-yes", "side": "BUY"},
            {"orderID": "o2", "asset_id": "tok-alive-yes", "side": "BUY"},
        ])
        n = cancel_day0_dead_bin_resting_entries(
            clob=clob, conn=self._tuple_conn(),
            cities_by_name={"Tokyo": _tokyo()},
            now=datetime(2026, 6, 10, 6, 0, tzinfo=UTC),
        )
        assert n == 1
        assert clob.cancelled == ["o1"]

    def test_legacy_schema_without_metric_column_falls_through_to_typed_authority(self):
        """Two-query fallback: a market_events without temperature_metric (the
        trades-DB legacy shape, as in the fixture) resolves the metric from
        market_topology_state — and refuses when no typed authority exists."""
        from src.execution.day0_hard_fact_exit import _resolve_order_bin_identity

        conn = self._tuple_conn()
        identity = _resolve_order_bin_identity(conn, "tok-dead-yes")
        assert identity is not None and identity["metric"] == "high"
        conn.execute("DELETE FROM market_topology_state")
        assert _resolve_order_bin_identity(conn, "tok-dead-yes") is None
