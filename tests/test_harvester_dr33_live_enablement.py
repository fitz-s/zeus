# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: docs/operations/task_2026-04-23_live_harvester_enablement_dr33/plan.md
#                  + P-D §6.1 (_find_winning_bin fix)
#                  + P-E canonical authority pattern (INV-14 + provenance_json + SettlementSemantics gate)
#                  + Fitz Constraint: relationship tests BEFORE implementation
#
# 9 tests covering:
#   T1-T3: _find_winning_bin UMA-vote gate + fail-closed branches
#   T4:    _canonical_bin_label all 4 shapes + round-trip through _parse_temp_range
#   T5-T6: _write_settlement_truth VERIFIED/QUARANTINED authority gating
#   T7:    _write_settlement_truth does NOT call conn.commit()
#   T8:    run_harvester early-returns when feature flag OFF
#   T9:    canonical labels round-trip cleanly (sanity guard against re-introduction of ≥/≤)

from __future__ import annotations

import json
import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.config import City
from src.data.market_scanner import _parse_temp_range
from src.execution.harvester import (
    _canonical_bin_label,
    _find_winning_bin,
    _write_settlement_truth,
)


def _mock_city(name: str = "London", unit: str = "C", st: str = "wu_icao") -> City:
    """Minimal City for harvester tests."""
    return City(
        name=name, lat=51.47, lon=-0.45, timezone="Europe/London",
        settlement_unit=unit, cluster=name, wu_station="EGLC",
        settlement_source="https://example.com/london",
        settlement_source_type=st,
    )


def _event_with_market(uma_status: str, outcomes: list[str], outcome_prices: list[str],
                      question: str = "Will the highest temperature in London be 17°C on April 15?"):
    """Helper to build a Gamma-shaped event with one market."""
    return {
        "title": "Highest temperature in London on April 15?",
        "slug": "highest-temperature-in-london-on-april-15-2026",
        "markets": [{
            "question": question,
            "umaResolutionStatus": uma_status,
            "outcomes": json.dumps(outcomes),
            "outcomePrices": json.dumps(outcome_prices),
        }],
    }


# ---- T1-T3 _find_winning_bin ----

def test_T1_find_winning_bin_pending_returns_none():
    """umaResolutionStatus='pending' must NOT be read (P-D §5.3 preserves R3-09)."""
    ev = _event_with_market("pending", ["Yes", "No"], ["1", "0"])
    assert _find_winning_bin(ev) == (None, None)


def test_T2_find_winning_bin_resolved_yes_won_returns_bin():
    """umaResolutionStatus='resolved' + outcomes=['Yes','No'] + prices=['1','0'] → YES won."""
    ev = _event_with_market("resolved", ["Yes", "No"], ["1", "0"])
    lo, hi = _find_winning_bin(ev)
    assert lo == 17.0 and hi == 17.0


def test_T3_find_winning_bin_rejects_unexpected_outcomes_order():
    """outcomes=['No','Yes'] is unexpected; P-D §6.1 fails closed (no silent swap)."""
    ev = _event_with_market("resolved", ["No", "Yes"], ["0", "1"])
    # outcomes order differs from ['Yes','No']; treat as unexpected and skip
    assert _find_winning_bin(ev) == (None, None)


def test_T3b_find_winning_bin_resolved_no_won_returns_none():
    """outcomes=['Yes','No'] + prices=['0','1'] → NO won → not this market's bin."""
    # Event has 3 markets, none of them YES-won
    ev = {
        "title": "Highest temperature in London on April 15?",
        "slug": "test-event",
        "markets": [
            {"question": "Will the highest temperature in London be 15°C on April 15?",
             "umaResolutionStatus": "resolved",
             "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["0", "1"])},
            {"question": "Will the highest temperature in London be 16°C on April 15?",
             "umaResolutionStatus": "resolved",
             "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["0", "1"])},
            {"question": "Will the highest temperature in London be 18°C on April 15?",
             "umaResolutionStatus": "resolved",
             "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["0", "1"])},
        ],
    }
    assert _find_winning_bin(ev) == (None, None)


# ---- T4 _canonical_bin_label ----

@pytest.mark.parametrize("lo, hi, unit, expected", [
    (17.0, 17.0, "C", "17°C"),                      # point
    (86.0, 87.0, "F", "86-87°F"),                   # range
    (None, 15.0, "C", "15°C or below"),             # low shoulder
    (21.0, None, "C", "21°C or higher"),            # high shoulder
    (75.0, None, "F", "75°F or higher"),
    (None, 50.0, "F", "50°F or below"),
    (None, None, "C", None),                        # both NULL → None
])
def test_T4_canonical_bin_label_all_shapes(lo, hi, unit, expected):
    assert _canonical_bin_label(lo, hi, unit) == expected


def test_T4b_canonical_label_roundtrip_via_parse_temp_range():
    """Every label _canonical_bin_label produces round-trips cleanly through _parse_temp_range
    (P-E C1 invariant: no silent misparse via ≥/≤ unicode)."""
    cases = [
        (17.0, 17.0, "C"),
        (86.0, 87.0, "F"),
        (None, 15.0, "C"),
        (21.0, None, "C"),
        (75.0, None, "F"),
        (None, 50.0, "F"),
    ]
    for lo, hi, unit in cases:
        label = _canonical_bin_label(lo, hi, unit)
        assert label is not None
        parsed_lo, parsed_hi = _parse_temp_range(label)
        assert parsed_lo == lo, f"{label}: parsed_lo {parsed_lo} != {lo}"
        assert parsed_hi == hi, f"{label}: parsed_hi {parsed_hi} != {hi}"


# ---- T5-T7 _write_settlement_truth ----

@pytest.fixture
def scratch_db(tmp_path):
    """Isolated SQLite DB with the full settlements schema (post-P-B)."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.executescript("""
    CREATE TABLE settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT NOT NULL,
        target_date TEXT NOT NULL,
        market_slug TEXT,
        winning_bin TEXT,
        settlement_value REAL,
        settlement_source TEXT,
        settled_at TEXT,
        authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
          CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
        pm_bin_lo REAL, pm_bin_hi REAL, unit TEXT, settlement_source_type TEXT,
        temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low')),
        physical_quantity TEXT,
        observation_field TEXT CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp')),
        data_version TEXT,
        provenance_json TEXT,
        UNIQUE(city, target_date)
    );
    """)
    yield conn
    conn.close()


def test_T5_write_settlement_verified_path(scratch_db):
    """obs.high_temp rounds into [pm_bin_lo, pm_bin_hi] → VERIFIED with all INV-14 + provenance fields."""
    city = _mock_city()
    result = _write_settlement_truth(
        scratch_db, city, "2026-04-15",
        pm_bin_lo=17.0, pm_bin_hi=17.0,
        event_slug="test-event",
        obs_row={"id": 42, "source": "wu_icao_history", "high_temp": 17.3,
                 "unit": "C", "fetched_at": "2026-04-15T12:00:00+00:00"},
    )
    scratch_db.commit()  # caller owns commit (T7)

    row = scratch_db.execute("SELECT * FROM settlements").fetchone()
    cols = [d[0] for d in scratch_db.execute("SELECT * FROM settlements LIMIT 0").description]
    r = dict(zip(cols, row))

    assert r["authority"] == "VERIFIED"
    assert r["settlement_value"] == 17.0  # wmo_half_up(17.3) = 17
    assert r["winning_bin"] == "17°C"
    assert r["temperature_metric"] == "high"
    assert r["physical_quantity"] == "daily_maximum_air_temperature"
    assert r["observation_field"] == "high_temp"
    assert r["data_version"] == "wu_icao_history_v1"
    prov = json.loads(r["provenance_json"])
    assert prov["writer"] == "harvester_live_dr33"
    assert prov["decision_time_snapshot_id"] == "2026-04-15T12:00:00+00:00"
    assert prov["source_family"] == "WU"
    assert prov["obs_id"] == 42
    assert prov["rounding_rule"] == "wmo_half_up"
    assert prov["reconstruction_method"] == "harvester_live_uma_vote"
    assert result["authority"] == "VERIFIED"


def test_T6_write_settlement_quarantined_outside_bin(scratch_db):
    """obs.high_temp outside [pm_bin_lo, pm_bin_hi] → QUARANTINED with enumerable reason."""
    city = _mock_city()
    _write_settlement_truth(
        scratch_db, city, "2026-04-15",
        pm_bin_lo=17.0, pm_bin_hi=17.0,
        event_slug="test-event",
        obs_row={"id": 42, "source": "wu_icao_history", "high_temp": 22.0,
                 "unit": "C", "fetched_at": "2026-04-15T12:00:00+00:00"},
    )
    scratch_db.commit()
    row = scratch_db.execute(
        "SELECT authority, settlement_value, winning_bin, provenance_json FROM settlements"
    ).fetchone()
    authority, sv, wb, pjs = row
    assert authority == "QUARANTINED"
    assert sv == 22.0  # evidence preserved
    assert wb is None  # winning_bin NULL on QUARANTINED
    prov = json.loads(pjs)
    assert prov["quarantine_reason"] == "harvester_live_obs_outside_bin"


def test_T6b_write_settlement_quarantined_no_obs(scratch_db):
    """obs_row is None → QUARANTINED with harvester_live_no_obs reason; no crash."""
    city = _mock_city()
    _write_settlement_truth(
        scratch_db, city, "2026-04-15",
        pm_bin_lo=17.0, pm_bin_hi=17.0,
        event_slug="test-event",
        obs_row=None,
    )
    scratch_db.commit()
    row = scratch_db.execute(
        "SELECT authority, settlement_value, provenance_json FROM settlements"
    ).fetchone()
    authority, sv, pjs = row
    assert authority == "QUARANTINED"
    assert sv is None
    prov = json.loads(pjs)
    assert prov["quarantine_reason"] == "harvester_live_no_obs"
    assert prov["decision_time_snapshot_id"] is None


def test_T7_write_settlement_does_not_commit(scratch_db):
    """_write_settlement_truth must NOT call conn.commit() — caller owns txn.

    sqlite3.Connection.commit is read-only; wrap the connection in a proxy
    that intercepts .commit() but delegates everything else.
    """
    city = _mock_city()

    class _CommitCounterConn:
        def __init__(self, real):
            self._real = real
            self.commit_count = 0

        def __getattr__(self, name):
            return getattr(self._real, name)

        def commit(self):
            self.commit_count += 1
            return self._real.commit()

    proxy = _CommitCounterConn(scratch_db)
    _write_settlement_truth(
        proxy, city, "2026-04-15",
        pm_bin_lo=17.0, pm_bin_hi=17.0,
        obs_row={"id": 1, "source": "wu_icao_history", "high_temp": 17.0,
                 "unit": "C", "fetched_at": "2026-04-15T12:00:00Z"},
    )
    assert proxy.commit_count == 0, (
        "T7 violation: _write_settlement_truth must not call conn.commit(); caller owns txn boundary"
    )


# ---- T8 feature flag ----

def test_T8_run_harvester_skips_when_flag_off(monkeypatch):
    """ZEUS_HARVESTER_LIVE_ENABLED unset or != '1' → early return with empty counts."""
    from src.execution.harvester import run_harvester

    # Simulate default-OFF
    monkeypatch.delenv("ZEUS_HARVESTER_LIVE_ENABLED", raising=False)
    # Even if we mock everything downstream, the flag gate should short-circuit BEFORE
    # any Gamma fetch happens. We just verify the return signature + that no exception fires.
    with patch("src.execution.harvester._fetch_settled_events") as fetch_mock, \
         patch("src.execution.harvester.get_trade_connection") as trade_mock, \
         patch("src.execution.harvester.get_world_connection") as shared_mock:
        result = run_harvester()
        # Feature flag OFF → gate fires before any data-plane calls
        fetch_mock.assert_not_called()
        trade_mock.assert_not_called()
        shared_mock.assert_not_called()
    # Must return a dict (caller may log counts)
    assert isinstance(result, dict)
    assert result.get("status") == "disabled_by_feature_flag" or result.get("disabled_by_flag") is True


# ---- T9 regression guard ----

def test_T9_canonical_labels_never_contain_unicode_shoulders():
    """Regression guard against re-introduction of ≥/≤ unicode shoulders (P-E C1)."""
    # Every shoulder label _canonical_bin_label produces must be text-form
    for lo, hi, unit in [(None, 15.0, "C"), (21.0, None, "C"), (None, 50.0, "F"), (75.0, None, "F")]:
        label = _canonical_bin_label(lo, hi, unit)
        assert "≥" not in label and "≤" not in label, (
            f"T9 regression: unicode shoulder in {label}"
        )
        assert "or below" in label or "or higher" in label
