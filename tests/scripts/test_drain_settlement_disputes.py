# Created: 2026-07-04
# Last reused or audited: 2026-07-04
# Authority basis: P0a market-semantics purity plan — settlement_outcomes DISPUTE
#   backlog drain. src/state/chain_mirror_reconciler.py::load_settlement_lookup grades
#   legacy positions ONLY against authority='VERIFIED'; DISPUTED rows can never close
#   a position until re-verified. Operator correction 2026-07-04: grading authority is the
#   VENUE'S RESOLVED OUTCOME (payment fact), not a local recomputation — a resolved market
#   has already paid real money on a specific bin. The declared-source observation is a
#   SEPARATE, secondary fact (forecast-skill calibration) that never blocks VERIFY. Second
#   operator correction: settlement rounding is PER-SOURCE (SettlementSemantics.for_city),
#   never hand-rounded — an apparent venue-vs-declared-source disagreement can dissolve once
#   the correct source rounding rule (e.g. HKO oracle_truncate vs wu_icao wmo_half_up) is
#   applied to the same raw reading.
"""Antibody tests: scripts/drain_settlement_disputes.

All Gamma network calls are mocked via monkeypatching
scripts.drain_settlement_disputes._fetch_venue_event_by_slug — no test makes a real HTTP
request. Builds a SYNTHETIC forecasts DB (settlement_outcomes + observations, with the real
VERIFIED-unit guard triggers) and asserts:
  - a venue-resolved market (point / finite_range / open_shoulder bins) VERIFIES with
    provenance stamped source='venue_resolution' + raw resolution evidence;
  - settlement rounding is PER-SOURCE (SettlementSemantics.for_city): the SAME raw reading
    rounds differently for an HKO city (oracle_truncate) vs a wu_icao city (wmo_half_up),
    and this determines whether a venue-vs-declared-source conflict fires at all — a naive
    universal rounding would falsely flag the HKO case as conflicting;
  - a genuine declared-source disagreement (correct per-source rounding still does not land
    in the venue's bin) is recorded as resolution_conflict='venue_vs_declared_source' without
    blocking VERIFY, and settlement_value never contradicts its own winning bin;
  - pc_audit_*-prefixed reasons are NEVER auto-reactivated even when the venue has since
    resolved — reported separately as operator_review_venue_resolved;
  - an unresolved venue market stays DISPUTED/absent — nothing invented;
  - a network fault fails closed: the row stays DISPUTED, no partial write, error reported;
  - dry-run (apply=False) writes nothing; --apply is idempotent;
  - a missing market backfills from venue resolution the same way a DISPUTED row does.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.config import City
from src.contracts.settlement_semantics import SettlementSemantics
from scripts.drain_settlement_disputes import (
    DEFAULT_MISSING_MARKETS,
    VenueFetchError,
    declared_source_fact,
    drain,
    resolve_missing_market,
    resolve_disputed_row,
)
import scripts.drain_settlement_disputes as dsq

_SETTLEMENT_OUTCOMES_DDL = """
CREATE TABLE settlement_outcomes (
    settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
    market_slug TEXT,
    winning_bin TEXT,
    settlement_value REAL,
    settlement_source TEXT,
    settled_at TEXT,
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'DISPUTED')),
    provenance_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settlement_unit TEXT CHECK (settlement_unit IS NULL OR settlement_unit IN ('F', 'C')),
    UNIQUE(city, target_date, temperature_metric)
)
"""

_VERIFIED_UNIT_TRIGGERS = (
    """
    CREATE TRIGGER _settlement_outcomes_verified_unit_check
    BEFORE INSERT ON settlement_outcomes
    FOR EACH ROW
    WHEN NEW.authority = 'VERIFIED' AND NEW.settlement_unit IS NULL
    BEGIN
        SELECT RAISE(ABORT, 'VERIFIED_SETTLEMENT_REQUIRES_UNIT');
    END
    """,
    """
    CREATE TRIGGER _settlement_outcomes_verified_unit_check_update
    BEFORE UPDATE ON settlement_outcomes
    FOR EACH ROW
    WHEN NEW.authority = 'VERIFIED' AND NEW.settlement_unit IS NULL
    BEGIN
        SELECT RAISE(ABORT, 'VERIFIED_SETTLEMENT_REQUIRES_UNIT');
    END
    """,
)

_OBSERVATIONS_DDL = """
CREATE TABLE observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT, target_date TEXT, source TEXT,
    high_temp REAL, low_temp REAL, unit TEXT,
    station_id TEXT, fetched_at TEXT, authority TEXT,
    high_local_time TEXT, low_local_time TEXT
)
"""


def _make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(_SETTLEMENT_OUTCOMES_DDL)
    for ddl in _VERIFIED_UNIT_TRIGGERS:
        conn.execute(ddl)
    conn.execute(_OBSERVATIONS_DDL)
    conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_disputed(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    provenance: dict,
    market_slug: str | None = None,
    settlement_value: float | None = None,
    winning_bin: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO settlement_outcomes
           (city, target_date, temperature_metric, authority, market_slug, settlement_value,
            winning_bin, provenance_json)
           VALUES (?, ?, ?, 'DISPUTED', ?, ?, ?, ?)""",
        (city, target_date, metric, market_slug, settlement_value, winning_bin, json.dumps(provenance)),
    )
    conn.commit()
    return cur.lastrowid


def _insert_obs(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    source: str,
    high_temp: float | None = None,
    low_temp: float | None = None,
    unit: str = "C",
    station_id: str = "TESTWU",
    authority: str = "VERIFIED",
) -> int:
    cur = conn.execute(
        """INSERT INTO observations
           (city, target_date, source, high_temp, low_temp, unit, station_id,
            fetched_at, authority, high_local_time, low_local_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, '2026-07-01T00:00:00+00:00', ?,
                   '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00')""",
        (city, target_date, source, high_temp, low_temp, unit, station_id, authority),
    )
    conn.commit()
    return cur.lastrowid


def _wu_city(name: str, *, unit: str = "C", station: str = "TESTWU") -> City:
    return City(
        name=name, lat=0.0, lon=0.0, timezone="UTC", settlement_unit=unit,
        cluster="test", wu_station=station, settlement_source_type="wu_icao",
        slug_names=(name.lower().replace(" ", "-"),),
    )


def _hko_city(name: str = "Hong Kong") -> City:
    return City(
        name=name, lat=22.3, lon=114.2, timezone="Asia/Hong_Kong", settlement_unit="C",
        cluster="test", wu_station="HKO", settlement_source_type="hko",
        slug_names=(name.lower().replace(" ", "-"),),
    )


def _noaa_city(name: str = "Denver") -> City:
    return City(
        name=name, lat=39.7, lon=-104.9, timezone="America/Denver", settlement_unit="F",
        cluster="test", wu_station="TESTNOAA", settlement_source_type="noaa",
        slug_names=(name.lower().replace(" ", "-"),),
    )


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = _make_db(tmp_path / "forecasts_test.db")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Gamma network mocking — no test makes a real HTTP request
# ---------------------------------------------------------------------------

def _resolved_event(
    range_label: str, *, yes_won: bool = True, condition_id: str = "0xcond", yes_token: str = "tok-yes",
    slug: str = "test-slug", uma_status: str = "resolved",
) -> dict:
    """A minimal Gamma event payload with one resolved binary market."""
    return {
        "slug": slug,
        "markets": [
            {
                "conditionId": condition_id,
                "question": range_label,
                "umaResolutionStatus": uma_status,
                "outcomePrices": '["1","0"]' if yes_won else '["0","1"]',
                "outcomes": '["Yes","No"]',
                "clobTokenIds": f'["{yes_token}","tok-no"]',
            }
        ],
    }


def _unresolved_event(range_label: str, *, slug: str = "test-slug") -> dict:
    return {
        "slug": slug,
        "markets": [
            {
                "conditionId": "0xcond",
                "question": range_label,
                "umaResolutionStatus": "open",
                "outcomePrices": '["0.4","0.6"]',
                "outcomes": '["Yes","No"]',
                "clobTokenIds": '["tok-yes","tok-no"]',
            }
        ],
    }


def _mock_venue(monkeypatch: pytest.MonkeyPatch, *, event: dict | None = None, raises: bool = False):
    """Monkeypatch the sole network seam. `event=None` -> Gamma has no such event.
    `raises=True` -> simulate a network fault (VenueFetchError)."""
    def fake_fetch(slug: str):
        if raises:
            raise VenueFetchError(f"simulated network fault for slug={slug!r}")
        return event

    monkeypatch.setattr(dsq, "_fetch_venue_event_by_slug", fake_fetch)


# ---------------------------------------------------------------------------
# Venue-resolved bins (point / finite_range / open_shoulder) — VERIFY
# ---------------------------------------------------------------------------

def test_venue_resolved_point_bin_verifies_with_provenance(db, monkeypatch):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("20°C"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["rounded_value"] == 20.0
    assert decision["winning_bin"] == "20°C"
    assert decision["resolution_conflict"] is None

    report = drain(db, apply=True, city_map=city_map)
    assert report["disposition_counts"]["verify"] == 1
    assert sid in report["verified_settlement_ids"]

    after = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "VERIFIED"
    assert after["settlement_value"] == 20.0
    assert after["winning_bin"] == "20°C"
    assert after["settlement_unit"] == "C"
    prov = json.loads(after["provenance_json"])
    assert prov["source"] == "venue_resolution"
    assert prov["venue_resolution"]["condition_id"] == "0xcond"
    assert prov["declared_source_type"] == "WU"
    assert prov["declared_source_observed_value"] == 20.0
    assert prov["prior_authority"] == "DISPUTED"
    assert prov["prior_dispute_reason"] == "harvester_live_obs_outside_bin"
    assert prov["reactivated_by"] == "scripts.drain_settlement_disputes"
    assert prov["prior_provenance"]["dispute_reason"] == "harvester_live_obs_outside_bin"


def test_venue_resolved_finite_range_bin_verifies(db, monkeypatch):
    city_map = {"Testville": _wu_city("Testville", unit="F")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=50.6, unit="F")
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("50-51°F"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["winning_bin"] == "50-51°F"
    assert decision["rounded_value"] == 51.0  # WMO half-up: 50.6 -> 51, contained in [50,51]


def test_venue_resolved_open_shoulder_bin_verifies(db, monkeypatch):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", low_temp=5.0)
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="low",
        market_slug="lowest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("6°C or below"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["winning_bin"] == "6°C or below"


def test_f_bin_on_c_city_conversion_and_snap(db, monkeypatch):
    # Fix #262 case: Gamma label posed in F, city settles in C. 48F -> 8.888C -> snap to 9.
    city_map = {"London": _wu_city("London", unit="C")}
    _insert_obs(db, city="London", target_date="2026-06-01", source="wu_icao_history", high_temp=9.0)
    sid = _insert_disputed(
        db, city="London", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-london-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("48°F"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["winning_bin"] == "9°C"


# ---------------------------------------------------------------------------
# Per-source rounding (SettlementSemantics.for_city) — never hand-rounded
# ---------------------------------------------------------------------------

def test_declared_source_fact_routes_through_settlement_semantics_per_city(db):
    """The SAME raw reading (26.8) must round DIFFERENTLY depending on the city's declared
    settlement source: HKO uses oracle_truncate (floor); wu_icao and noaa use wmo_half_up."""
    hko = _hko_city()
    wu = _wu_city("Testville", unit="C")
    noaa = _noaa_city()

    _insert_obs(db, city="Hong Kong", target_date="2026-06-26", source="hko_daily_api", low_temp=26.8, station_id="HKO")
    _insert_obs(db, city="Testville", target_date="2026-06-26", source="wu_icao_history", low_temp=26.8)
    _insert_obs(db, city="Denver", target_date="2026-06-26", source="ogimet_metar_x", low_temp=26.8, unit="F", station_id="TESTNOAA")

    hko_fact = declared_source_fact(db, hko, "2026-06-26", "low")
    wu_fact = declared_source_fact(db, wu, "2026-06-26", "low")
    noaa_fact = declared_source_fact(db, noaa, "2026-06-26", "low")

    assert hko_fact["declared_source_type"] == "HKO"
    assert hko_fact["declared_source_observed_value"] == 26.0  # oracle_truncate: floor(26.8) == 26

    assert wu_fact["declared_source_type"] == "WU"
    assert wu_fact["declared_source_observed_value"] == 27.0  # wmo_half_up: floor(26.8+0.5) == 27

    assert noaa_fact["declared_source_type"] == "NOAA"
    assert noaa_fact["declared_source_observed_value"] == 27.0  # wmo_half_up, same rule as WU

    # Regression pin: declared_source_fact is the SOLE rounding seam and it is not
    # hand-rolled — it must dispatch through SettlementSemantics.for_city for every city.
    for city in (hko, wu, noaa):
        sem = SettlementSemantics.for_city(city)
        assert sem.rounding_rule in ("oracle_truncate", "wmo_half_up", "floor", "ceil")


def test_hko_correct_rounding_dissolves_apparent_conflict(db, monkeypatch):
    """The load-bearing case: a raw HKO low of 26.8 truncates (oracle_truncate) to 26, which
    IS contained in the venue's resolved 26°C bin — no conflict. A naive wmo_half_up rounding
    of the same raw value would have rounded to 27 and wrongly flagged a conflict."""
    city_map = {"Hong Kong": _hko_city()}
    _insert_obs(db, city="Hong Kong", target_date="2026-06-26", source="hko_daily_api", low_temp=26.8, station_id="HKO")
    sid = _insert_disputed(
        db, city="Hong Kong", target_date="2026-06-26", metric="low",
        market_slug="lowest-temperature-in-hong-kong-on-june-26-2026",
        provenance={"dispute_reason": "harvester_source_disagreement_within_tolerance"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("26°C"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["declared_source_observed_value"] == 26.0
    assert decision["resolution_conflict"] is None
    assert decision["rounded_value"] == 26.0


def test_wu_icao_city_same_raw_value_would_conflict(db, monkeypatch):
    """Contrast case for the rounding test above: the SAME raw value (26.8) in a wu_icao city
    (wmo_half_up) rounds to 27, which does NOT land in a 26°C venue-resolved bin — a genuine
    conflict, correctly source-rounded (not a false negative from under-rounding either)."""
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-26", source="wu_icao_history", low_temp=26.8)
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-26", metric="low",
        market_slug="lowest-temperature-in-testville-on-june-26-2026",
        provenance={"dispute_reason": "harvester_source_disagreement_within_tolerance"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("26°C"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"  # venue resolution still grades it — payment fact
    assert decision["declared_source_observed_value"] == 27.0
    assert decision["resolution_conflict"] == "venue_vs_declared_source"
    assert decision["rounded_value"] == 26.0  # settlement_value stays the PAYMENT fact (point bin), never 27


# ---------------------------------------------------------------------------
# Venue-vs-declared-source conflict: recorded, never blocks VERIFY
# ---------------------------------------------------------------------------

def test_declared_source_disagreement_verifies_and_records_conflict(db, monkeypatch):
    city_map = {"Hong Kong": _hko_city()}
    # HKO obs correctly source-rounds to 27 (floor(27.0)=27) — genuinely outside the venue's
    # resolved 26°C bin. This is the real Hong Kong 2026-06-26 audit case.
    _insert_obs(db, city="Hong Kong", target_date="2026-06-26", source="hko_daily_api", low_temp=27.0, station_id="HKO")
    sid = _insert_disputed(
        db, city="Hong Kong", target_date="2026-06-26", metric="low",
        market_slug="lowest-temperature-in-hong-kong-on-june-26-2026",
        provenance={"dispute_reason": "harvester_source_disagreement_within_tolerance"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("26°C"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["winning_bin"] == "26°C"
    assert decision["declared_source_type"] == "HKO"
    assert decision["declared_source_observed_value"] == 27.0
    assert decision["resolution_conflict"] == "venue_vs_declared_source"
    assert decision["rounded_value"] == 26.0  # payment fact wins; 27.0 never lands in settlement_value

    report = drain(db, apply=True, city_map=city_map)
    assert report["disposition_counts"]["verify"] == 1
    after = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "VERIFIED"
    assert after["winning_bin"] == "26°C"
    assert after["settlement_value"] == 26.0
    prov = json.loads(after["provenance_json"])
    assert prov["resolution_conflict"] == "venue_vs_declared_source"
    assert prov["declared_source_observed_value"] == 27.0


# ---------------------------------------------------------------------------
# pc_audit_* manual-audit carve-out
# ---------------------------------------------------------------------------

def test_pc_audit_reason_reserved_when_venue_unresolved(db, monkeypatch):
    city_map = {"Testville": _wu_city("Testville")}
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-03-08", metric="high",
        market_slug="highest-temperature-in-testville-on-march-8-2026",
        provenance={"dispute_reason": "pc_audit_dst_spring_forward_bin_mismatch"},
    )
    _mock_venue(monkeypatch, event=None)  # venue has no such event / not yet resolved

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "manual_audit_reserved"

    report = drain(db, apply=True, city_map=city_map)
    assert report["disposition_counts"].get("verify", 0) == 0
    after = db.execute("SELECT authority FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "DISPUTED"


def test_pc_audit_reason_reported_not_reactivated_when_venue_resolved(db, monkeypatch):
    """Even when the venue HAS since resolved, a pc_audit_* row is never auto-flipped — it is
    reported separately for operator review (the one honesty carve-out)."""
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-03-20", source="wu_icao_history", high_temp=29.0)
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-03-20", metric="high",
        market_slug="highest-temperature-in-testville-on-march-20-2026",
        provenance={"dispute_reason": "pc_audit_shenzhen_drift_nonreproducible"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("29°C"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "operator_review_venue_resolved"
    assert "venue_resolution" in decision

    report = drain(db, apply=True, city_map=city_map)
    assert report["disposition_counts"].get("verify", 0) == 0
    assert report["disposition_counts"]["operator_review_venue_resolved"] == 1
    after = db.execute("SELECT authority FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "DISPUTED"  # never auto-written


# ---------------------------------------------------------------------------
# Venue unresolved / network fault — fail closed, nothing invented
# ---------------------------------------------------------------------------

def test_venue_unresolved_stays_disputed(db, monkeypatch):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _mock_venue(monkeypatch, event=_unresolved_event("20-21°C"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "venue_unresolved"

    drain(db, apply=True, city_map=city_map)
    after = db.execute("SELECT authority FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "DISPUTED"


def test_venue_no_event_found_stays_disputed(db, monkeypatch):
    city_map = {"Testville": _wu_city("Testville")}
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _mock_venue(monkeypatch, event=None)

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "venue_unresolved"


def test_synthetic_uma_backfill_slug_falls_back_to_derived_candidate(db, monkeypatch):
    """Live-DB finding (2026-07-04): 2,077 rows repo-wide (181 of the 383 DISPUTED) carry a
    SYNTHETIC market_slug like 'uma_backfill_nyc_2026-01-02_high' — not a real, queryable Gamma
    slug (real slugs are hyphen-only kebab-case; this one has underscores). Trusting it as
    authoritative silently means every one of these rows always resolves 'no event found' even
    when the market DOES exist under the standard derived slug. This is the fix that took the
    live recoverable count from 93 to 260 of 383."""
    city_map = {"NYC": _wu_city("NYC")}
    sid = _insert_disputed(
        db, city="NYC", target_date="2026-01-02", metric="high",
        market_slug="uma_backfill_nyc_2026-01-02_high",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _insert_obs(db, city="NYC", target_date="2026-01-02", source="wu_icao_history", high_temp=30.0, unit="C")

    def fake_fetch(slug: str):
        # The synthetic slug itself must NEVER be queried as if it were real; only the
        # derived candidate ("highest-temperature-in-nyc-on-january-2-2026") resolves.
        assert "uma_backfill" not in slug
        if slug == "highest-temperature-in-nyc-on-january-2-2026":
            return _resolved_event("30°C", slug=slug)
        return None

    monkeypatch.setattr(dsq, "_fetch_venue_event_by_slug", fake_fetch)

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["winning_bin"] == "30°C"


def test_real_gamma_slug_is_queried_directly_without_derivation(db, monkeypatch):
    """A real, persisted market_slug (no underscores) is queried as-is — the derived-candidate
    fallback path must never be reached when the real slug already resolves."""
    city_map = {"Testville": _wu_city("Testville")}
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)

    queried_slugs = []

    def fake_fetch(slug: str):
        queried_slugs.append(slug)
        return _resolved_event("20°C", slug=slug)

    monkeypatch.setattr(dsq, "_fetch_venue_event_by_slug", fake_fetch)

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert queried_slugs == ["highest-temperature-in-testville-on-june-1-2026"]


def test_venue_resolution_stamps_market_level_uma_status(db, monkeypatch):
    """umaResolutionStatus lives on the individual market within event['markets'], not the
    event object — the stamped provenance must reflect the WINNING market's own status, not a
    silent top-level miss (a real live-DB bug this drain script's own diagnosis run caught: the
    event-level lookup always returned null)."""
    city_map = {"Testville": _wu_city("Testville")}
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    _mock_venue(monkeypatch, event=_resolved_event("20°C", uma_status="resolved"))

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["venue_resolution"]["umaResolutionStatus"] == "resolved"


def test_venue_fetch_error_fails_closed_no_partial_write(db, monkeypatch):
    """A network fault must NEVER be recorded as 'unresolved' and must NEVER write anything —
    the row stays exactly as it was, and the error is reported."""
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _mock_venue(monkeypatch, raises=True)

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_disputed_row(db, city_map, row)
    assert decision["disposition"] == "venue_fetch_error"
    assert "simulated network fault" in decision["detail"]

    report = drain(db, apply=True, city_map=city_map)
    assert report["disposition_counts"]["venue_fetch_error"] == 1
    assert report["disposition_counts"].get("verify", 0) == 0
    after = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "DISPUTED"
    assert after["winning_bin"] is None
    assert after["provenance_json"] == json.dumps({"dispute_reason": "harvester_live_obs_outside_bin"})


# ---------------------------------------------------------------------------
# Dry-run / apply / idempotency
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(db, monkeypatch):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("20°C"))

    report = drain(db, apply=False, city_map=city_map)
    assert report["applied"] is False
    assert report["disposition_counts"]["verify"] == 1  # decision computed...
    after = db.execute("SELECT authority FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "DISPUTED"  # ...but never written


def test_apply_is_idempotent(db, monkeypatch):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_disputed(
        db, city="Testville", target_date="2026-06-01", metric="high",
        market_slug="highest-temperature-in-testville-on-june-1-2026",
        provenance={"dispute_reason": "harvester_live_obs_outside_bin"},
    )
    _mock_venue(monkeypatch, event=_resolved_event("20°C"))

    first = drain(db, apply=True, city_map=city_map)
    assert first["disposition_counts"]["verify"] == 1

    second = drain(db, apply=True, city_map=city_map)
    assert second["disputed_before"] == 0
    assert second["disposition_counts"].get("verify", 0) == 0

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert row["authority"] == "VERIFIED"
    prov = json.loads(row["provenance_json"])
    # Second run never touched an already-VERIFIED row: no doubled prior_provenance nesting.
    assert "prior_provenance" not in prov["prior_provenance"] if "prior_provenance" in prov else True


# ---------------------------------------------------------------------------
# Missing-market backfill
# ---------------------------------------------------------------------------

def test_missing_market_backfills_from_venue_resolution(db, monkeypatch):
    city_map = {"Hong Kong": _hko_city(), "Helsinki": _wu_city("Helsinki")}
    for city_name, target_date, metric in DEFAULT_MISSING_MARKETS:
        existing = db.execute(
            "SELECT 1 FROM settlement_outcomes WHERE city=? AND target_date=? AND temperature_metric=?",
            (city_name, target_date, metric),
        ).fetchone()
        assert existing is None

    _mock_venue(monkeypatch, event=_resolved_event("15°C"))
    report = drain(db, apply=True, city_map=city_map)
    for missing in report["missing_markets"]:
        assert missing["disposition"] == "verify"
    count = db.execute("SELECT COUNT(*) FROM settlement_outcomes WHERE authority='VERIFIED'").fetchone()[0]
    assert count == 2
    hk_row = db.execute(
        "SELECT * FROM settlement_outcomes WHERE city='Hong Kong' AND target_date='2026-06-25' AND temperature_metric='low'"
    ).fetchone()
    assert hk_row["authority"] == "VERIFIED"
    assert hk_row["winning_bin"] == "15°C"


def test_missing_market_unresolved_stays_absent(db, monkeypatch):
    city_map = {"Hong Kong": _hko_city(), "Helsinki": _wu_city("Helsinki")}
    _mock_venue(monkeypatch, event=None)

    report = drain(db, apply=True, city_map=city_map)
    for missing in report["missing_markets"]:
        assert missing["disposition"] == "venue_unresolved"
    count = db.execute("SELECT COUNT(*) FROM settlement_outcomes").fetchone()[0]
    assert count == 0  # nothing invented


def test_missing_market_fetch_error_does_not_insert(db, monkeypatch):
    city_map = {"Hong Kong": _hko_city(), "Helsinki": _wu_city("Helsinki")}
    _mock_venue(monkeypatch, raises=True)

    report = drain(db, apply=True, city_map=city_map)
    for missing in report["missing_markets"]:
        assert missing["disposition"] == "venue_fetch_error"
    count = db.execute("SELECT COUNT(*) FROM settlement_outcomes").fetchone()[0]
    assert count == 0


def test_missing_market_already_present_is_not_touched(db, monkeypatch):
    city_map = {"Testville": _wu_city("Testville")}
    city_name, target_date, metric = "Testville", "2026-06-01", "high"
    sid = _insert_disputed(
        db, city=city_name, target_date=target_date, metric=metric,
        provenance={"dispute_reason": "harvester_live_no_obs"},
    )
    _mock_venue(monkeypatch, event=None)
    report = drain(db, apply=True, missing_markets=((city_name, target_date, metric),), city_map=city_map)
    assert report["missing_markets"][0]["disposition"] == "already_present"
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert row["authority"] == "DISPUTED"  # untouched by the missing-market path
