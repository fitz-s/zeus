# Created: 2026-04-23
# Last reused/audited: 2026-04-30
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
    _lookup_settlement_obs,
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
            "conditionId": "condition-17c",
            "clobTokenIds": json.dumps(["yes-17c", "no-17c"]),
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


def test_T3_find_winning_bin_rejects_unexpected_outcome_labels():
    """Unexpected outcome labels fail closed rather than guessing token mapping."""
    ev = _event_with_market("resolved", ["Maybe", "No"], ["1", "0"])
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
             "conditionId": "condition-15c", "clobTokenIds": json.dumps(["yes-15c", "no-15c"]),
             "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["0", "1"])},
            {"question": "Will the highest temperature in London be 16°C on April 15?",
             "umaResolutionStatus": "resolved",
             "conditionId": "condition-16c", "clobTokenIds": json.dumps(["yes-16c", "no-16c"]),
             "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["0", "1"])},
            {"question": "Will the highest temperature in London be 18°C on April 15?",
             "umaResolutionStatus": "resolved",
             "conditionId": "condition-18c", "clobTokenIds": json.dumps(["yes-18c", "no-18c"]),
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
    """Isolated SQLite DB with the settlements schema (post-REOPEN-2) + trigger.

    Per test-engineer Phase 2 P0 finding: the real DB carries the
    settlements_authority_monotonic trigger; tests MUST include it so
    trigger-blocked writes surface as IntegrityError in test rather than
    silently pass on an incomplete schema simulation.
    """
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
        UNIQUE(city, target_date, temperature_metric)
    );

    CREATE TABLE observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT NOT NULL,
        target_date TEXT NOT NULL,
        source TEXT NOT NULL,
        high_temp REAL,
        low_temp REAL,
        unit TEXT,
        fetched_at TEXT,
        UNIQUE(city, target_date, source)
    );

    -- Matches production trigger from src/state/db.py (P-B migration)
    CREATE TRIGGER IF NOT EXISTS settlements_authority_monotonic
    BEFORE UPDATE OF authority ON settlements
    WHEN (OLD.authority = 'VERIFIED' AND NEW.authority = 'UNVERIFIED')
      OR (OLD.authority = 'QUARANTINED' AND NEW.authority = 'VERIFIED'
          AND (NEW.provenance_json IS NULL
               OR json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL))
    BEGIN
        SELECT RAISE(ABORT, 'settlements.authority transition forbidden: VERIFIED->UNVERIFIED blocked, or QUARANTINED->VERIFIED missing provenance_json.reactivated_by');
    END;
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
    assert r["physical_quantity"] == "mx2t6_local_calendar_day_max"
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


# ---- Phase 2 verification gap-fills ----

def test_T10_lookup_obs_wu_branch(scratch_db):
    """_lookup_settlement_obs: wu_icao city finds wu_icao_history row."""
    scratch_db.execute(
        "INSERT INTO observations (city, target_date, source, high_temp, unit, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("London", "2026-04-15", "wu_icao_history", 17.3, "C", "2026-04-15T12:00:00Z"),
    )
    row = _lookup_settlement_obs(scratch_db, _mock_city("London", unit="C", st="wu_icao"), "2026-04-15")
    assert row is not None
    assert row["source"] == "wu_icao_history"
    assert row["high_temp"] == 17.3


def test_T10b_lookup_obs_noaa_branch(scratch_db):
    """_lookup_settlement_obs: noaa city finds ogimet_metar_* row."""
    scratch_db.execute(
        "INSERT INTO observations (city, target_date, source, high_temp, unit, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Moscow", "2026-04-15", "ogimet_metar_uuww", -5.0, "C", "2026-04-15T12:00:00Z"),
    )
    row = _lookup_settlement_obs(scratch_db, _mock_city("Moscow", unit="C", st="noaa"), "2026-04-15")
    assert row is not None
    assert row["source"] == "ogimet_metar_uuww"


def test_T10c_lookup_obs_hko_branch(scratch_db):
    """_lookup_settlement_obs: hko city finds hko_daily_api row."""
    scratch_db.execute(
        "INSERT INTO observations (city, target_date, source, high_temp, unit, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Hong Kong", "2026-04-15", "hko_daily_api", 28.5, "C", "2026-04-15T12:00:00Z"),
    )
    row = _lookup_settlement_obs(scratch_db, _mock_city("Hong Kong", unit="C", st="hko"), "2026-04-15")
    assert row is not None
    assert row["source"] == "hko_daily_api"


def test_T10d_lookup_obs_cwa_returns_none(scratch_db):
    """_lookup_settlement_obs: cwa_station has no accepted proxy; always returns None."""
    # Even with an ogimet row present, cwa_station routing refuses cross-family obs.
    scratch_db.execute(
        "INSERT INTO observations (city, target_date, source, high_temp, unit, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Taipei", "2026-04-15", "ogimet_metar_rctp", 25.0, "C", "2026-04-15T12:00:00Z"),
    )
    row = _lookup_settlement_obs(scratch_db, _mock_city("Taipei", unit="C", st="cwa_station"), "2026-04-15")
    assert row is None


def test_T10e_lookup_obs_cross_family_rejected(scratch_db):
    """_lookup_settlement_obs: wu-labeled city with only ogimet obs → None (no cross-family fallback)."""
    scratch_db.execute(
        "INSERT INTO observations (city, target_date, source, high_temp, unit, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("Tel Aviv", "2026-03-15", "ogimet_metar_llbg", 20.0, "C", "2026-03-15T12:00:00Z"),
    )
    row = _lookup_settlement_obs(scratch_db, _mock_city("Tel Aviv", unit="C", st="wu_icao"), "2026-03-15")
    assert row is None  # no wu_icao_history row present; ogimet is wrong family


def test_T11_upsert_idempotency(scratch_db):
    """Calling _write_settlement_truth twice on same (city, target_date) → second call wins (INSERT OR REPLACE)."""
    city = _mock_city()
    _write_settlement_truth(
        scratch_db, city, "2026-04-15",
        pm_bin_lo=17.0, pm_bin_hi=17.0,
        obs_row={"id": 1, "source": "wu_icao_history", "high_temp": 17.3,
                 "unit": "C", "fetched_at": "2026-04-15T12:00:00Z"},
    )
    scratch_db.commit()
    count_1 = scratch_db.execute("SELECT COUNT(*) FROM settlements WHERE city=? AND target_date=?",
                                  ("London", "2026-04-15")).fetchone()[0]
    # Second call with different obs (fresher fetched_at) must overwrite, not duplicate.
    _write_settlement_truth(
        scratch_db, city, "2026-04-15",
        pm_bin_lo=17.0, pm_bin_hi=17.0,
        obs_row={"id": 2, "source": "wu_icao_history", "high_temp": 17.3,
                 "unit": "C", "fetched_at": "2026-04-15T18:00:00Z"},
    )
    scratch_db.commit()
    count_2 = scratch_db.execute("SELECT COUNT(*) FROM settlements WHERE city=? AND target_date=?",
                                  ("London", "2026-04-15")).fetchone()[0]
    assert count_1 == 1 and count_2 == 1  # upsert, not duplicate
    # Second write should have the later fetched_at
    prov_str = scratch_db.execute(
        "SELECT provenance_json FROM settlements WHERE city=? AND target_date=?",
        ("London", "2026-04-15"),
    ).fetchone()[0]
    prov = json.loads(prov_str)
    assert prov["decision_time_snapshot_id"] == "2026-04-15T18:00:00Z"


def test_T12_integration_flag_on_processes_event(monkeypatch, tmp_path):
    """Flag-ON integration: mocked Gamma event + obs_row → `_write_settlement_truth` actually called.

    This is the integration test test-engineer flagged as a P0 gap: would have
    caught the winning_label NameError that code-reviewer found in Phase 2
    (since the NameError fires INSIDE the per-event try/except and is swallowed
    by `except Exception as e`, the observable signal is that _write_settlement_truth
    IS reached but downstream harvest_settlement/_settle_positions receive the
    undefined symbol — a call-count assertion surfaces the regression).
    """
    from src.execution import harvester as hv

    # Mock a Gamma event with a resolved YES-won market on a point bin
    event = _event_with_market(
        "resolved", ["Yes", "No"], ["1", "0"],
        question="Will the highest temperature in London be 17°C on April 15?",
    )
    event["title"] = "Highest temperature in London on April 15?"
    event["slug"] = "highest-temperature-in-london-on-april-15-2026"

    london = _mock_city("London", unit="C", st="wu_icao")

    # Track call counts on the functions the P0 NameError would prevent reaching
    write_calls = []
    harvest_calls = []
    settle_calls = []

    def _fake_write(*a, **kw):
        write_calls.append((a, kw))
        return {"authority": "VERIFIED", "settlement_value": 17.0, "winning_bin": "17°C", "reason": None}

    def _fake_harvest(*a, **kw):
        harvest_calls.append((a, kw))
        return 0

    def _fake_settle(*a, **kw):
        settle_calls.append((a, kw))
        return 0

    monkeypatch.setattr(hv, "_match_city", lambda title, slug: london)
    monkeypatch.setattr(hv, "_extract_target_date", lambda ev: "2026-04-15")
    monkeypatch.setattr(hv, "_fetch_settled_events", lambda: [event])
    # Give run_harvester real connections so obs lookup can be mocked out
    dummy_db_path = tmp_path / "dummy.db"
    dummy_conn = sqlite3.connect(dummy_db_path, isolation_level=None)
    monkeypatch.setattr(hv, "get_trade_connection", lambda: dummy_conn)
    monkeypatch.setattr(hv, "get_world_connection", lambda: dummy_conn)
    # Return a valid obs_row so _write_settlement_truth is reached
    monkeypatch.setattr(hv, "_lookup_settlement_obs",
        lambda conn, city, td, **kw: {"id": 1, "source": "wu_icao_history",
                                      "high_temp": 17.3, "unit": "C",
                                      "fetched_at": "2026-04-15T12:00:00Z"})
    monkeypatch.setattr(hv, "_preflight_harvester_stage2_db_shape",
                        lambda trade, shared: {"stage2_status": "not_run_no_settled_events"})
    monkeypatch.setattr(hv, "load_portfolio", lambda: None)
    monkeypatch.setattr(hv, "_write_settlement_truth", _fake_write)
    monkeypatch.setattr(hv, "harvest_settlement", _fake_harvest)
    monkeypatch.setattr(hv, "_snapshot_contexts_for_market",
                        lambda *a, **kw: ([], []))
    monkeypatch.setattr(hv, "_settle_positions", _fake_settle)
    monkeypatch.setattr(hv, "query_legacy_settlement_records", lambda *a, **kw: [])
    monkeypatch.setattr(hv, "store_settlement_records", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "get_tracker", lambda: None)
    monkeypatch.setattr(hv, "save_tracker", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "save_portfolio", lambda *a, **kw: None)
    monkeypatch.setenv("ZEUS_HARVESTER_LIVE_ENABLED", "1")

    try:
        result = hv.run_harvester()
    finally:
        dummy_conn.close()

    # The P0 NameError would cause _write_settlement_truth to be reached but
    # harvest_settlement + _settle_positions to fail inside except Exception.
    # All three must be reached for the full pipeline to work end-to-end.
    assert len(write_calls) == 1, f"_write_settlement_truth not called (got {len(write_calls)})"
    # _settle_positions is called OUTSIDE the stage2_ready branch at harvester.py:443
    # so it MUST be reached even with stage2_ready=False. If winning_label
    # NameError fires in the per-event try, the except-handler swallows it
    # and _settle_positions is skipped — this assertion catches that P0.
    assert len(settle_calls) == 1, (
        f"_settle_positions not reached ({len(settle_calls)}); "
        "likely winning_label NameError regression"
    )
    # harvest_settlement is inside `if stage2_ready:` branch; stage2 mocked False
    # in this test, so it should NOT be called. Separate test can exercise stage2=True.
    assert len(harvest_calls) == 0, (
        f"harvest_settlement unexpectedly called ({len(harvest_calls)}) with stage2_ready=False"
    )
    assert result.get("disabled_by_flag") is not True


# ---- S2.5 format-unification structural antibody ----


def test_S25_format_range_function_never_reappears():
    """AST-level antibody: src/execution/harvester.py must NOT define a
    function named `_format_range` (or any variant) and must NOT emit
    unicode ≥/≤ shoulders in source code.

    DR-33-A (commit 9026192) removed the pre-P-D `_format_range` function
    and replaced it with text-form `_canonical_bin_label`. Data-readiness
    closure rule 15 (NH-E2 in the closure banner) requires that future
    refactors not silently re-introduce the unicode shoulder format OR
    the legacy range formatter. This test AST-walks the harvester source
    and asserts both the function-name absence and the unicode-emission
    absence. Category immunity via CI — wrong reintroduction fails the
    gate before merge.
    """
    import ast
    from pathlib import Path

    harvester_path = Path(__file__).resolve().parents[1] / "src" / "execution" / "harvester.py"
    source = harvester_path.read_text()

    # 1. AST gate: no FunctionDef with name `_format_range` (or common variants)
    tree = ast.parse(source, filename=str(harvester_path))
    forbidden_names = {"_format_range", "format_range", "_format_bin_range"}
    defined_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    collisions = forbidden_names & defined_names
    assert not collisions, (
        f"harvester.py re-introduced forbidden format-range function(s): {collisions}. "
        "DR-33-A retired _format_range; canonical label emission goes via "
        "_canonical_bin_label only. See S2.5 / closure-banner NH-E2."
    )

    # 2. Source gate: no unicode ≥ / ≤ at the START of any string literal.
    #    Checking startswith (not `in`) cleanly distinguishes actual label
    #    emissions (e.g. literal "≥21°C" — bad) from explanatory prose / docstrings
    #    that reference the unicode form as a negative example (e.g. "…because
    #    '≥21°C' would silently misparse…" — fine).
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            stripped = node.value.lstrip()
            assert not stripped.startswith("≥"), (
                f"harvester.py string literal at line {node.lineno} starts "
                f"with unicode ≥ — text form 'or higher' required (S2.5 / NH-E2)"
            )
            assert not stripped.startswith("≤"), (
                f"harvester.py string literal at line {node.lineno} starts "
                f"with unicode ≤ — text form 'or below' required (S2.5 / NH-E2)"
            )


def test_S25_every_canonical_label_passes_strict_parser():
    """Every _canonical_bin_label output must round-trip through the S2.4
    strict parser `_parse_canonical_bin_label` (non-None return).

    Distinct from T4b which round-trips through the tolerant
    `_parse_temp_range`. This gate specifically pins the STRICT parser
    vs. STRICT emitter: wrong output from either side flips the
    test red at CI time."""
    from src.data.market_scanner import _parse_canonical_bin_label

    cases = [
        # point bins
        (17.0, 17.0, "C"), (75.0, 75.0, "F"),
        (-5.0, -5.0, "C"), (0.0, 0.0, "F"),
        # finite ranges
        (15.0, 16.0, "C"), (74.0, 76.0, "F"), (-10.0, -5.0, "C"),
        # shoulders
        (None, 15.0, "C"), (None, 50.0, "F"),
        (21.0, None, "C"), (90.0, None, "F"),
    ]
    for lo, hi, unit in cases:
        label = _canonical_bin_label(lo, hi, unit)
        parsed = _parse_canonical_bin_label(label)
        assert parsed is not None, (
            f"S2.5 gate: canonical label {label!r} from ({lo}, {hi}, {unit}) "
            f"was REJECTED by strict parser — emitter/parser contract broken"
        )
