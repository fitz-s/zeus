# Created: 2026-06-12
# Last reused or audited: 2026-06-19
# Authority basis: settlement-losses incident 2026-06-12 (Karachi position:
#   719/719 monitor refreshes with last_monitor_prob_is_fresh=False while the
#   entry authority forecast_posteriors was live and had re-ranked the held bin
#   to family top 18h before settlement) + consult REQ-20260612-052802 K1.
#   2026-06-12 update: regime law U1/U2 + Denver incident (stale 0.79 masked as
#   fresh while market 0.22) — replacement-authority positions must FAULT
#   (BELIEF_AUTHORITY_FAULT) + reseed, never substitute the legacy ENS belief.
"""ANTIBODY: held-position belief comes from the SAME authority entry used.

The disease: entry decisions read ``forecast_posteriors`` (replacement chain)
while the exit monitor's probability came from a legacy day0/ens chain that
has been dead since inception — so every held position was monitored with
permanently-stale belief and the exit gate could never fire. These tests pin:

1. ``load_replacement_belief`` reads the freshest posterior row, indexes the
   held bin by its venue range-label, converts to held-side space exactly once,
   and brands freshness from the live source-cycle clock when available —
   stale is returned as information, absence and unparseable timestamps fail closed.
2. ``monitor_probability_refresh`` treats the replacement belief as PRIMARY:
   a fresh row attests freshness without consulting the legacy chain; a stale
   or missing row cannot borrow freshness from the legacy ENS chain. Non-day0
   positions fault/reseed; day0 observation remains a separate authority.
3. The belief-dead watchdog escalates after N consecutive stale-belief cycles
   while the market price stays fresh (719 silent cycles can never recur).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.contracts import EntryMethod
from src.engine.position_belief import (
    DEFAULT_MAX_AGE_HOURS,
    LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID,
    SELECTED_METHOD_REPLACEMENT_POSTERIOR,
    ReplacementBelief,
    load_replacement_belief,
)

NOW = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)
BIN = "Will the highest temperature in Karachi be 37°C on June 12?"
OTHER_BIN = "Will the highest temperature in Karachi be 38°C on June 12?"


@pytest.fixture
def forecasts_db(tmp_path):
    path = tmp_path / "zeus-forecasts.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id TEXT, city TEXT, target_date TEXT,
            temperature_metric TEXT, computed_at TEXT, q_json TEXT,
            source_cycle_time TEXT,
            runtime_layer TEXT,
            source_id TEXT,
            posterior_method TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            endpoint TEXT,
            coverage_status TEXT,
            captured_at TEXT,
            source_available_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return str(path)


def _insert(db_path, *, posterior_id, computed_at, q, city="Karachi",
            target_date="2026-06-12", metric="high", source_cycle_time=None,
            runtime_layer="live", source_id=LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID,
            posterior_method="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO forecast_posteriors VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            posterior_id,
            city,
            target_date,
            metric,
            computed_at,
            json.dumps(q),
            source_cycle_time,
            runtime_layer,
            source_id,
            posterior_method,
        ),
    )
    conn.commit()
    conn.close()


def _insert_raw(db_path, *, source_cycle_time, city="Karachi",
                target_date="2026-06-12", metric="high",
                endpoint="single_runs", coverage_status="COVERED",
                captured_at=None, source_available_at=None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO raw_model_forecasts VALUES (?,?,?,?,?,?,?,?)",
        (
            city,
            target_date,
            metric,
            source_cycle_time,
            endpoint,
            coverage_status,
            captured_at,
            source_available_at,
        ),
    )
    conn.commit()
    conn.close()


def _load(db_path, *, direction="buy_no", bin_label=BIN, now=NOW, **kw):
    return load_replacement_belief(
        city="Karachi",
        target_date="2026-06-12",
        temperature_metric="high",
        bin_label=bin_label,
        direction=direction,
        now=now,
        db_path=db_path,
        **kw,
    )


class TestLoadReplacementBelief:
    def test_fresh_row_buy_no_is_held_side_converted(self, forecasts_db):
        _insert(forecasts_db, posterior_id="p1",
                computed_at=(NOW - timedelta(hours=2)).isoformat(),
                q={BIN: 0.242, OTHER_BIN: 0.29})
        belief = _load(forecasts_db, direction="buy_no")
        assert belief is not None
        assert belief.fresh is True
        assert belief.q_yes_bin == pytest.approx(0.242)
        assert belief.held_side_prob == pytest.approx(1.0 - 0.242)
        assert belief.posterior_id == "p1"

    def test_buy_yes_is_q_directly(self, forecasts_db):
        _insert(forecasts_db, posterior_id="p1",
                computed_at=(NOW - timedelta(hours=1)).isoformat(),
                q={BIN: 0.242})
        belief = _load(forecasts_db, direction="buy_yes")
        assert belief.held_side_prob == pytest.approx(0.242)

    def test_freshest_row_wins(self, forecasts_db):
        _insert(forecasts_db, posterior_id="old",
                computed_at=(NOW - timedelta(hours=8)).isoformat(), q={BIN: 0.10})
        _insert(forecasts_db, posterior_id="new",
                computed_at=(NOW - timedelta(hours=1)).isoformat(), q={BIN: 0.30})
        belief = _load(forecasts_db)
        assert belief.posterior_id == "new"
        assert belief.q_yes_bin == pytest.approx(0.30)
        assert belief.runtime_layer == "live"

    def test_newer_non_live_row_cannot_override_live_runtime_layer(self, forecasts_db):
        _insert(
            forecasts_db,
            posterior_id="live",
            computed_at=(NOW - timedelta(hours=2)).isoformat(),
            q={BIN: 0.20},
            runtime_layer="live",
        )
        _insert(
            forecasts_db,
            posterior_id="non-live",
            computed_at=(NOW - timedelta(minutes=5)).isoformat(),
            q={BIN: 0.80},
            runtime_layer=None,
        )

        belief = _load(forecasts_db)

        assert belief is not None
        assert belief.posterior_id == "live"
        assert belief.q_yes_bin == pytest.approx(0.20)

    def test_newer_deprecated_aifs_row_cannot_override_live_bpf_authority(self, forecasts_db):
        _insert(
            forecasts_db,
            posterior_id="bpf",
            computed_at=(NOW - timedelta(hours=2)).isoformat(),
            q={BIN: 0.20},
        )
        _insert(
            forecasts_db,
            posterior_id="aifs-residue",
            computed_at=(NOW - timedelta(minutes=5)).isoformat(),
            q={BIN: 0.80},
            source_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            posterior_method="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        )

        belief = _load(forecasts_db)

        assert belief is not None
        assert belief.posterior_id == "bpf"
        assert belief.q_yes_bin == pytest.approx(0.20)
        assert belief.source_id == LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID
        assert belief.posterior_method == "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"

    def test_live_bpf_source_accepts_non_source_posterior_method(self, forecasts_db):
        """posterior_method is provenance, not live-authority identity."""
        _insert(
            forecasts_db,
            posterior_id="bpf-method",
            computed_at=(NOW - timedelta(hours=1)).isoformat(),
            q={BIN: 0.34},
            source_id=LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID,
            posterior_method="the_path_bayes_precision_fusion",
        )

        belief = _load(forecasts_db)

        assert belief is not None
        assert belief.posterior_id == "bpf-method"
        assert belief.q_yes_bin == pytest.approx(0.34)
        assert belief.source_id == LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID
        assert belief.posterior_method == "the_path_bayes_precision_fusion"

    def test_only_deprecated_aifs_rows_fail_closed(self, forecasts_db):
        _insert(
            forecasts_db,
            posterior_id="aifs-residue",
            computed_at=(NOW - timedelta(minutes=5)).isoformat(),
            q={BIN: 0.80},
            source_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
            posterior_method="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        )

        assert _load(forecasts_db) is None

    def test_only_non_live_rows_fail_closed(self, forecasts_db):
        _insert(
            forecasts_db,
            posterior_id="non-live",
            computed_at=(NOW - timedelta(minutes=5)).isoformat(),
            q={BIN: 0.80},
            runtime_layer=None,
        )

        assert _load(forecasts_db) is None

    def test_stale_row_returned_with_fresh_false(self, forecasts_db):
        """Staleness is information, absence is not — the caller annotates and
        must never brand this fresh."""
        _insert(forecasts_db, posterior_id="p1",
                computed_at=(NOW - timedelta(hours=DEFAULT_MAX_AGE_HOURS + 5)).isoformat(),
                q={BIN: 0.242})
        belief = _load(forecasts_db)
        assert belief is not None
        assert belief.fresh is False

    def test_source_cycle_clock_controls_live_schema_freshness(self, forecasts_db):
        """Live posteriors stay lawful by the shared source-cycle horizon, not
        the old 9h computed_at monitor clock."""
        _insert(
            forecasts_db,
            posterior_id="p1",
            computed_at=(NOW - timedelta(hours=14)).isoformat(),
            source_cycle_time=(NOW - timedelta(hours=24)).isoformat(),
            q={BIN: 0.242},
        )
        belief = _load(forecasts_db)
        assert belief is not None
        assert belief.fresh is True
        assert belief.freshness_basis == "source_cycle_time"
        assert belief.source_cycle_age_hours == pytest.approx(24.0)

    def test_newer_raw_cycle_marks_posterior_stale_until_materialized(self, forecasts_db):
        """Monitor authority must not treat an older posterior as fresh when
        newer live-input raw cycles already exist for the same family."""
        _insert(
            forecasts_db,
            posterior_id="p1",
            computed_at=(NOW - timedelta(hours=1)).isoformat(),
            source_cycle_time=(NOW - timedelta(hours=12)).isoformat(),
            q={BIN: 0.242},
        )
        _insert_raw(
            forecasts_db,
            source_cycle_time=(NOW - timedelta(hours=6)).isoformat(),
            captured_at=(NOW - timedelta(hours=5, minutes=30)).isoformat(),
            source_available_at=(NOW - timedelta(hours=5, minutes=45)).isoformat(),
        )

        belief = _load(forecasts_db)

        assert belief is not None
        assert belief.fresh is False
        assert belief.freshness_basis == "source_cycle_time_raw_model_forecasts_lag"
        assert belief.latest_raw_cycle_time == (NOW - timedelta(hours=6)).isoformat()
        assert belief.raw_cycle_lag_hours == pytest.approx(6.0)
        validation = belief.freshness_validation()
        assert "latest_raw_cycle_time=" in validation
        assert "raw_cycle_lag_h=6.00" in validation
        assert validation.endswith(";stale")

    def test_source_cycle_clock_still_fails_closed_after_bound(self, forecasts_db):
        _insert(
            forecasts_db,
            posterior_id="p1",
            computed_at=(NOW - timedelta(hours=14)).isoformat(),
            source_cycle_time=(NOW - timedelta(hours=36)).isoformat(),
            q={BIN: 0.242},
        )
        belief = _load(forecasts_db)
        assert belief is not None
        assert belief.fresh is False

    def test_missing_family_fails_closed(self, forecasts_db):
        assert _load(forecasts_db) is None

    def test_unmatched_bin_label_fails_closed(self, forecasts_db):
        _insert(forecasts_db, posterior_id="p1",
                computed_at=(NOW - timedelta(hours=1)).isoformat(),
                q={OTHER_BIN: 0.29})
        assert _load(forecasts_db) is None

    def test_whitespace_normalized_bin_match(self, forecasts_db):
        _insert(forecasts_db, posterior_id="p1",
                computed_at=(NOW - timedelta(hours=1)).isoformat(),
                q={BIN.replace(" be ", "  be "): 0.242})
        belief = _load(forecasts_db)
        assert belief is not None
        assert belief.q_yes_bin == pytest.approx(0.242)

    def test_unparseable_computed_at_fails_closed(self, forecasts_db):
        """The 2026-06-11 serving-freshness incident class: a row with no
        usable capture time must never be branded fresh."""
        _insert(forecasts_db, posterior_id="p1", computed_at="not-a-time",
                q={BIN: 0.242})
        assert _load(forecasts_db) is None

    def test_out_of_range_q_fails_closed(self, forecasts_db):
        _insert(forecasts_db, posterior_id="p1",
                computed_at=(NOW - timedelta(hours=1)).isoformat(),
                q={BIN: 1.7})
        assert _load(forecasts_db) is None

    def test_future_computed_at_is_not_fresh(self, forecasts_db):
        """A clock-skewed future row must not be branded fresh (negative age)."""
        _insert(forecasts_db, posterior_id="p1",
                computed_at=(NOW + timedelta(hours=2)).isoformat(),
                q={BIN: 0.242})
        belief = _load(forecasts_db)
        assert belief is not None
        assert belief.fresh is False


class TestMonitorPrimaryAuthority:
    """monitor_probability_refresh: replacement belief is PRIMARY."""

    def _pos(self):
        from src.state.portfolio import Position

        return Position(
            trade_id="t-belief-1",
            market_id="m1",
            city="Karachi",
            cluster="Karachi",
            target_date="2026-06-12",
            bin_label=BIN,
            direction="buy_no",
            unit="C",
            temperature_metric="high",
            entry_method="ens_member_counting",
            entry_price=0.66,
            p_posterior=0.855,
        )

    def test_day0_yes_bin_probability_converts_to_held_side_for_buy_no(self):
        import src.engine.monitor_refresh as mr

        assert mr._held_side_probability_from_yes_bin_probability(
            0.23,
            "buy_yes",
        ) == pytest.approx(0.23)
        assert mr._held_side_probability_from_yes_bin_probability(
            0.23,
            "buy_no",
        ) == pytest.approx(0.77)

    def test_fresh_belief_attests_without_legacy_chain(self, monkeypatch):
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        belief = ReplacementBelief(
            held_side_prob=0.758, q_yes_bin=0.242, posterior_id="p9",
            computed_at="2026-06-12T10:00:00+00:00", age_hours=2.0,
            fresh=True, bin_key=BIN, direction="buy_no",
        )
        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: belief)
        legacy_called = []
        monkeypatch.setattr(
            mr, "_refresh_ens_member_counting",
            lambda **kw: legacy_called.append("ens") or (0.5, []),
        )
        monkeypatch.setattr(
            mr, "_refresh_day0_observation",
            lambda **kw: legacy_called.append("day0") or (0.5, []),
        )
        pos = self._pos()
        prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
            pos, conn=None, city=object(), target_d=None,
        )
        assert is_fresh is True
        assert prob == pytest.approx(0.758)
        assert legacy_called == []
        assert refresh_pos.selected_method == SELECTED_METHOD_REPLACEMENT_POSTERIOR
        assert any(
            v.startswith("belief_source=forecast_posteriors")
            for v in refresh_pos.applied_validations
        )

    def test_stale_belief_falls_through_and_never_borrows_freshness(self, monkeypatch):
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        belief = ReplacementBelief(
            held_side_prob=0.758, q_yes_bin=0.242, posterior_id="p9",
            computed_at="2026-06-12T00:00:00+00:00", age_hours=99.0,
            fresh=False, bin_key=BIN, direction="buy_no",
        )
        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: belief)
        monkeypatch.setattr(
            mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []),
        )
        pos = self._pos()
        prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
            pos, conn=None, city=object(), target_d=None,
        )
        # The legacy refresher did not attest freshness; stale replacement
        # belief must not be promoted into authority.
        assert is_fresh is not True
        assert any(
            v.startswith("replacement_posterior_stale")
            for v in pos.applied_validations
        )

    def test_stale_belief_on_target_local_day_uses_day0_observation_lane(self, monkeypatch):
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        belief = ReplacementBelief(
            held_side_prob=0.758, q_yes_bin=0.242, posterior_id="p9",
            computed_at="2026-06-12T00:00:00+00:00", age_hours=99.0,
            fresh=False, bin_key=BIN, direction="buy_no",
        )
        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: belief)
        observed = []

        def fake_day0_refresh(**kw):
            observed.append(kw["position"].entry_method)
            mr._set_monitor_probability_fresh(kw["position"], True)
            return 0.64, ["day0_observation"]

        monkeypatch.setattr(
            mr,
            "_refresh_day0_observation",
            fake_day0_refresh,
        )
        monkeypatch.setattr(
            mr,
            "_refresh_ens_member_counting",
            lambda **kw: (_ for _ in ()).throw(AssertionError("ENS fallback must not run")),
        )
        pos = self._pos()
        pos.state = "active"
        pos.target_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        city = type(
            "City",
            (),
            {"timezone": "Asia/Shanghai", "settlement_source_type": "wu_icao"},
        )()

        prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
            pos,
            conn=None,
            city=city,
            target_d=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
        )

        assert observed == [EntryMethod.DAY0_OBSERVATION.value]
        assert prob == pytest.approx(0.64)
        assert (
            refresh_pos.selected_method
            == mr.SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
        )
        assert "day0_observation_remaining_window" in refresh_pos.applied_validations
        assert is_fresh is True

    def test_day0_observation_dominates_even_fresh_replacement_belief(self, monkeypatch):
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        monkeypatch.setattr(
            pb,
            "load_replacement_belief",
            lambda **kw: (_ for _ in ()).throw(
                AssertionError("Day0 monitor must not read forecast posterior first")
            ),
        )
        observed = []

        def fake_day0_refresh(**kw):
            observed.append(kw["position"].entry_method)
            mr._set_monitor_probability_fresh(kw["position"], True)
            return 0.64, ["day0_observation"]

        monkeypatch.setattr(mr, "_refresh_day0_observation", fake_day0_refresh)
        monkeypatch.setattr(
            mr,
            "_refresh_ens_member_counting",
            lambda **kw: (_ for _ in ()).throw(AssertionError("ENS fallback must not run")),
        )
        pos = self._pos()
        pos.state = "active"
        pos.target_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        city = type(
            "City",
            (),
            {"timezone": "Asia/Shanghai", "settlement_source_type": "wu_icao"},
        )()

        prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
            pos,
            conn=None,
            city=city,
            target_d=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
        )

        assert observed == [EntryMethod.DAY0_OBSERVATION.value]
        assert prob == pytest.approx(0.64)
        assert (
            refresh_pos.selected_method
            == mr.SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
        )
        assert "day0_observation_remaining_window" in refresh_pos.applied_validations
        assert is_fresh is True

    def test_hko_day0_window_uses_day0_observation_lane(self, monkeypatch):
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        belief = ReplacementBelief(
            held_side_prob=0.758, q_yes_bin=0.242, posterior_id="p9",
            computed_at="2026-06-12T00:00:00+00:00", age_hours=99.0,
            fresh=False, bin_key=BIN, direction="buy_no",
        )
        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: belief)
        observed = []

        def fake_day0_refresh(**kw):
            observed.append(kw["position"].entry_method)
            mr._set_monitor_probability_fresh(kw["position"], True)
            return 0.71, ["day0_observation"]

        monkeypatch.setattr(mr, "_refresh_day0_observation", fake_day0_refresh)
        monkeypatch.setattr(
            mr,
            "_refresh_ens_member_counting",
            lambda **kw: (_ for _ in ()).throw(AssertionError("ENS fallback must not run")),
        )
        pos = self._pos()
        pos.city = "Hong Kong"
        pos.state = "day0_window"
        pos.target_date = datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat()
        city = type(
            "City",
            (),
            {"timezone": "Asia/Hong_Kong", "settlement_source_type": "hko"},
        )()

        prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
            pos,
            conn=None,
            city=city,
            target_d=datetime.now(ZoneInfo("Asia/Hong_Kong")).date(),
        )

        assert observed == [EntryMethod.DAY0_OBSERVATION.value]
        assert prob == pytest.approx(0.71)
        assert (
            refresh_pos.selected_method
            == mr.SELECTED_METHOD_DAY0_OBSERVATION_REMAINING_WINDOW
        )
        assert "day0_observation_remaining_window" in refresh_pos.applied_validations
        assert is_fresh is True

    def test_day0_monitor_accepts_incomplete_window_only_as_bound(self, monkeypatch):
        import src.engine.monitor_refresh as mr

        pos = self._pos()
        obs = {
            "observation_time": NOW.isoformat(),
            "coverage_status": "WINDOW_INCOMPLETE",
        }
        monkeypatch.setattr(mr, "_fetch_day0_observation", lambda city, target_d: obs)
        monkeypatch.setattr(
            mr,
            "_day0_observation_source_rejection_reason",
            lambda *args, **kwargs: None,
        )
        allow_flags = []

        def fake_quality(*args, allow_incomplete_window_bound=False, **kwargs):
            allow_flags.append(allow_incomplete_window_bound)
            return "stop_after_quality_assertion"

        monkeypatch.setattr(
            mr,
            "_day0_observation_quality_rejection_reason",
            fake_quality,
        )

        prob, validations = mr._refresh_day0_observation(
            position=pos,
            current_p_market=0.12,
            conn=None,
            city=type("City", (), {"name": "Chengdu"})(),
            target_d=NOW.date(),
        )

        assert allow_flags == [True]
        assert prob == pytest.approx(pos.p_posterior)
        assert "day0_observation_bound_only:coverage_window_incomplete" in validations
        assert "observation_quality_gate" in validations

    def test_missing_belief_annotates_and_falls_through(self, monkeypatch):
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: None)
        monkeypatch.setattr(
            mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []),
        )
        pos = self._pos()
        _, _, is_fresh = mr.monitor_probability_refresh(
            pos, conn=None, city=object(), target_d=None,
        )
        assert is_fresh is not True
        assert "replacement_posterior_missing" in pos.applied_validations


class TestReplacementAuthorityFaultSuppressesLegacy:
    """Regime law U1/U2 (2026-06-12), Denver incident, and 2026-06-16 source
    parity widening: a non-day0 held position whose replacement belief is
    stale/missing must NOT be papered over by legacy ENS forecast belief.
    Instead: not-fresh + BELIEF_AUTHORITY_FAULT + fail-soft single-family reseed.
    The day0 observation lane remains separately authorized."""

    def _edli_pos(self, trade_id="edli-belief-1", entry_method="ens_member_counting"):
        from src.state.portfolio import Position

        return Position(
            trade_id=trade_id, market_id="m1", city="Karachi",
            cluster="Karachi", target_date="2026-06-12", bin_label=BIN,
            direction="buy_no", unit="C", temperature_metric="high",
            entry_method=entry_method, entry_price=0.66, p_posterior=0.855,
        )

    def _stale_belief(self):
        return ReplacementBelief(
            held_side_prob=0.758, q_yes_bin=0.242, posterior_id="p9",
            computed_at="2026-06-12T00:00:00+00:00", age_hours=99.0,
            fresh=False, bin_key=BIN, direction="buy_no",
        )

    def test_edli_stale_belief_faults_and_suppresses_legacy_and_reseeds(self, monkeypatch):
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: self._stale_belief())
        legacy_called = []
        monkeypatch.setattr(
            mr, "_refresh_ens_member_counting",
            lambda **kw: legacy_called.append("ens") or (0.5, []),
        )
        reseeds = []
        monkeypatch.setattr(
            mr, "_enqueue_single_family_belief_reseed_failsoft",
            lambda **kw: reseeds.append(kw) or {"status": "ok", "enqueued": True},
        )

        pos = self._edli_pos()
        prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
            pos, conn=None, city=object(), target_d=None,
        )

        # Legacy ENS forecast belief was NOT substituted.
        assert legacy_called == [], "legacy ENS path must not run for edli fault"
        assert is_fresh is False
        assert "BELIEF_AUTHORITY_FAULT" in pos.applied_validations
        assert "legacy_belief_substitution_suppressed" in pos.applied_validations
        # A targeted single-family reseed was enqueued for THIS family.
        assert len(reseeds) == 1
        assert reseeds[0] == {
            "city": "Karachi", "target_date": "2026-06-12", "metric": "high",
        }

    def test_edli_missing_belief_faults_and_reseeds(self, monkeypatch):
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: None)
        legacy_called = []
        monkeypatch.setattr(
            mr, "_refresh_ens_member_counting",
            lambda **kw: legacy_called.append("ens") or (0.5, []),
        )
        reseeds = []
        monkeypatch.setattr(
            mr, "_enqueue_single_family_belief_reseed_failsoft",
            lambda **kw: reseeds.append(kw) or None,
        )

        pos = self._edli_pos(trade_id="edli-belief-2")
        _, _, is_fresh = mr.monitor_probability_refresh(
            pos, conn=None, city=object(), target_d=None,
        )
        assert legacy_called == []
        assert is_fresh is False
        assert "BELIEF_AUTHORITY_FAULT" in pos.applied_validations
        assert len(reseeds) == 1

    def test_reseed_failure_does_not_crash_monitor(self, monkeypatch):
        """The reseed enqueue is fail-soft: an exception inside it must not
        propagate out of monitor_probability_refresh."""
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: self._stale_belief())

        def _boom(**kw):
            raise RuntimeError("reseed lane exploded")

        # Patch the REAL helper (not the wrapper) to ensure the wrapper's own
        # try/except absorbs it. Here we patch the inner trigger via the wrapper's
        # fail-soft contract by making the config lookup raise.
        monkeypatch.setattr(
            "src.data.replacement_forecast_production._replacement_forecast_live_materialization_queue_config",
            _boom,
        )
        monkeypatch.setattr(mr, "_refresh_ens_member_counting", lambda **kw: (0.5, []))

        pos = self._edli_pos(trade_id="edli-belief-3")
        # Must not raise.
        _, _, is_fresh = mr.monitor_probability_refresh(
            pos, conn=None, city=object(), target_d=None,
        )
        assert is_fresh is False
        assert "BELIEF_AUTHORITY_FAULT" in pos.applied_validations

    def test_legacy_entered_position_suppressed_under_source_parity_widening(self, monkeypatch):
        """SOURCE-PARITY WIDENING (2026-06-16, spine source-divergence fix, plan
        Option A): a LEGACY (non-edli) non-day0 position with a stale/missing
        replacement belief is now ALSO suppressed (fail-closed) rather than
        substituting the cold single-model ``ensemble_snapshots`` EMOS center —
        the same cold-center divergence the entry spine fix removed, formerly
        re-introduced on the held side for legacy positions. The legacy ENS path
        MUST NOT run; belief is marked not-fresh + BELIEF_AUTHORITY_FAULT + a
        same-family reseed re-materializes the SAME authority next cycle.

        RED-on-revert: restoring an edli-only guard re-enables ensemble
        substitution for legacy positions -> ``legacy_called`` becomes
        ``["ens"]`` -> this test fails."""
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: self._stale_belief())
        legacy_called = []
        monkeypatch.setattr(
            mr, "_refresh_ens_member_counting",
            lambda **kw: legacy_called.append("ens") or (0.5, []),
        )
        reseeds = []
        monkeypatch.setattr(
            mr, "_enqueue_single_family_belief_reseed_failsoft",
            lambda **kw: reseeds.append(kw) or {"status": "ok", "enqueued": True},
        )

        pos = self._edli_pos(trade_id="legacy-trade-77")  # NON-edli
        _, _, is_fresh = mr.monitor_probability_refresh(
            pos, conn=None, city=object(), target_d=None,
        )

        # Legacy ENS forecast belief was NOT substituted (the cold-center seam is
        # closed for legacy positions too); belief is fail-closed-unavailable and a
        # same-family reseed was enqueued on the SAME authority.
        assert legacy_called == [], "legacy ENS path must not run for a non-day0 legacy fault"
        assert is_fresh is False
        assert "BELIEF_AUTHORITY_FAULT" in pos.applied_validations
        assert "legacy_belief_substitution_suppressed" in pos.applied_validations
        assert len(reseeds) == 1
        assert reseeds[0] == {
            "city": "Karachi", "target_date": "2026-06-12", "metric": "high",
        }

    def test_legacy_day0_window_position_reseeds_when_day0_lane_not_fresh(self, monkeypatch):
        """The day0 nowcast lane remains EXEMPT from the widened guard: a legacy
        day0_window position over a wu_icao settlement city still falls through to
        its refresher (day0 settlement-day observation is a distinct authority, not
        a forecast-belief substitution). This pins that the widening did NOT
        swallow the day0 lane. If that day0 authority is unavailable/not fresh,
        the same-family BPF reseed still fires so the held position does not stay
        blind until settlement."""
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: self._stale_belief())
        legacy_called = []
        monkeypatch.setattr(
            mr, "_refresh_ens_member_counting",
            lambda **kw: legacy_called.append("ens") or (0.5, []),
        )
        monkeypatch.setattr(
            mr, "_refresh_day0_observation",
            lambda **kw: legacy_called.append("day0") or (0.5, []),
        )
        reseeds = []
        monkeypatch.setattr(
            mr, "_enqueue_single_family_belief_reseed_failsoft",
            lambda **kw: reseeds.append(kw),
        )

        pos = self._edli_pos(trade_id="legacy-trade-78")  # NON-edli
        pos.entry_method = "day0_observation"  # routes _would_use_day0_lane True
        mr.monitor_probability_refresh(pos, conn=None, city=object(), target_d=None)

        # The day0-exempt branch was taken: NOT suppressed and no legacy fault,
        # but the unavailable day0 authority triggers the BPF repair lane.
        assert "legacy_belief_substitution_suppressed" not in pos.applied_validations
        assert "BELIEF_AUTHORITY_FAULT" not in pos.applied_validations
        assert "day0_observation_unavailable:replacement_belief_reseed" in pos.applied_validations
        assert reseeds == [
            {"city": "Karachi", "target_date": "2026-06-12", "metric": "high"}
        ]

    def test_fresh_day0_window_position_does_not_reseed(self, monkeypatch):
        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb

        monkeypatch.setattr(pb, "load_replacement_belief", lambda **kw: self._stale_belief())

        def fake_day0_refresh(**kw):
            setattr(kw["position"], mr._MONITOR_PROBABILITY_FRESH_ATTR, True)
            return 0.5, []

        monkeypatch.setattr(mr, "_refresh_day0_observation", fake_day0_refresh)
        reseeds = []
        monkeypatch.setattr(
            mr, "_enqueue_single_family_belief_reseed_failsoft",
            lambda **kw: reseeds.append(kw),
        )

        pos = self._edli_pos(trade_id="legacy-trade-79")
        pos.entry_method = "day0_observation"
        _, _, is_fresh = mr.monitor_probability_refresh(pos, conn=None, city=object(), target_d=None)

        assert is_fresh is True
        assert reseeds == []


class TestBeliefDeadWatchdog:
    def _pos(self, trade_id="t-watchdog-1"):
        from src.state.portfolio import Position

        pos = Position(
            trade_id=trade_id, market_id="m1", city="Karachi",
            cluster="Karachi", target_date="2026-06-12", bin_label=BIN,
            direction="buy_no", unit="C", temperature_metric="high",
            entry_method="ens_member_counting", entry_price=0.66,
            p_posterior=0.855,
        )
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_prob_is_fresh = False
        return pos

    def test_three_stale_cycles_with_fresh_price_raise_fault(self):
        import src.engine.monitor_refresh as mr

        mr._belief_stale_cycles.clear()
        pos = self._pos()
        for _ in range(2):
            mr._track_belief_staleness(pos)
        assert "BELIEF_AUTHORITY_FAULT" not in pos.applied_validations
        mr._track_belief_staleness(pos)
        assert "BELIEF_AUTHORITY_FAULT" in pos.applied_validations
        assert "belief_stale_cycles=3" in pos.applied_validations

    def test_fresh_belief_resets_counter(self):
        import src.engine.monitor_refresh as mr

        mr._belief_stale_cycles.clear()
        pos = self._pos(trade_id="t-watchdog-2")
        mr._track_belief_staleness(pos)
        mr._track_belief_staleness(pos)
        pos.last_monitor_prob_is_fresh = True
        mr._track_belief_staleness(pos)
        assert mr._belief_stale_cycles.get("t-watchdog-2") is None

    def test_stale_market_price_does_not_count(self):
        import src.engine.monitor_refresh as mr

        mr._belief_stale_cycles.clear()
        pos = self._pos(trade_id="t-watchdog-3")
        pos.last_monitor_market_price_is_fresh = False
        for _ in range(5):
            mr._track_belief_staleness(pos)
        assert "BELIEF_AUTHORITY_FAULT" not in pos.applied_validations


class TestLiveEnumDirectionIntegration:
    """UNMOCKED path: a real Position (whose direction is the coerced
    Direction enum, str() == 'Direction.NO') through the real loader against
    a real fixture DB. The mocked wiring tests above swallowed exactly this
    bug on 2026-06-12: every live monitor cycle passed str(Direction.NO) and
    the loader fail-closed to 'replacement_posterior_missing'."""

    def test_enum_direction_position_gets_fresh_belief(self, forecasts_db, monkeypatch):
        from datetime import datetime, timezone

        import src.engine.monitor_refresh as mr
        import src.engine.position_belief as pb
        from src.state.portfolio import Position

        _insert(forecasts_db, posterior_id="p-live",
                computed_at=datetime.now(timezone.utc).isoformat(),
                q={BIN: 0.242})
        real_loader = pb.load_replacement_belief
        monkeypatch.setattr(
            pb, "load_replacement_belief",
            lambda **kw: real_loader(**{**kw, "db_path": forecasts_db}),
        )
        pos = Position(
            trade_id="t-enum-1", market_id="m1", city="Karachi",
            cluster="Karachi", target_date="2026-06-12", bin_label=BIN,
            direction="buy_no",  # __post_init__ coerces to Direction.NO
            unit="C", temperature_metric="high",
            entry_method="ens_member_counting", entry_price=0.66,
            p_posterior=0.855,
        )
        prob, refresh_pos, is_fresh = mr.monitor_probability_refresh(
            pos, conn=None, city=object(), target_d=None,
        )
        assert is_fresh is True, refresh_pos.applied_validations
        assert prob == pytest.approx(1.0 - 0.242)
