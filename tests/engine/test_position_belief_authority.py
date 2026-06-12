# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: settlement-losses incident 2026-06-12 (Karachi position:
#   719/719 monitor refreshes with last_monitor_prob_is_fresh=False while the
#   entry authority forecast_posteriors was live and had re-ranked the held bin
#   to family top 18h before settlement) + consult REQ-20260612-052802 K1.
"""ANTIBODY: held-position belief comes from the SAME authority entry used.

The disease: entry decisions read ``forecast_posteriors`` (replacement chain)
while the exit monitor's probability came from a legacy day0/ens chain that
has been dead since inception — so every held position was monitored with
permanently-stale belief and the exit gate could never fire. These tests pin:

1. ``load_replacement_belief`` reads the freshest posterior row, indexes the
   held bin by its venue range-label, converts to held-side space exactly once,
   and brands freshness from an explicit age budget — stale is returned as
   information, absence and unparseable timestamps fail closed.
2. ``monitor_probability_refresh`` treats the replacement belief as PRIMARY:
   a fresh row attests freshness without consulting the legacy chain; a stale
   or missing row falls through to legacy with an honest annotation and can
   never borrow freshness.
3. The belief-dead watchdog escalates after N consecutive stale-belief cycles
   while the market price stays fresh (719 silent cycles can never recur).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.engine.position_belief import (
    DEFAULT_MAX_AGE_HOURS,
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
            temperature_metric TEXT, computed_at TEXT, q_json TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return str(path)


def _insert(db_path, *, posterior_id, computed_at, q, city="Karachi",
            target_date="2026-06-12", metric="high"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO forecast_posteriors VALUES (?,?,?,?,?,?)",
        (posterior_id, city, target_date, metric, computed_at, json.dumps(q)),
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

    def test_stale_row_returned_with_fresh_false(self, forecasts_db):
        """Staleness is information, absence is not — the caller annotates and
        falls through to legacy telemetry, but never brands this fresh."""
        _insert(forecasts_db, posterior_id="p1",
                computed_at=(NOW - timedelta(hours=DEFAULT_MAX_AGE_HOURS + 5)).isoformat(),
                q={BIN: 0.242})
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
