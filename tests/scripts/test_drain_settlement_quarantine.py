# Created: 2026-07-04
# Last reused or audited: 2026-07-04
# Authority basis: P0a market-semantics purity plan — settlement_outcomes QUARANTINE
#   backlog drain. src/state/chain_mirror_reconciler.py::load_settlement_lookup grades
#   legacy positions ONLY against authority='VERIFIED'; QUARANTINED rows can never close
#   a position until re-verified from persisted evidence.
"""Antibody tests: scripts/drain_settlement_quarantine.

Builds a SYNTHETIC forecasts DB (settlement_outcomes + observations, with the real
VERIFIED-unit guard triggers) and asserts:
  - point-bin containment re-check verifies a genuinely-resolvable row and preserves
    prior quarantine history + reactivated_by in provenance;
  - finite_range and open_shoulder bins are evaluated with the same containment rule;
  - HKO oracle_truncate rounding is applied (not WMO half-up) before containment;
  - F-bin-on-C-city conversion + WMO edge-snap matches harvester_truth_writer.py;
  - genuinely conflicting evidence (recomputed value outside the bin) stays QUARANTINED;
  - no persisted observation / no persisted bin info both stay QUARANTINED, reported as
    unfillable, never invented;
  - pc_audit_*-prefixed reasons are NEVER auto-reactivated even when mechanically
    "contained" (prior human audit judgment is not silently overridden);
  - dry-run (apply=False) writes nothing to the DB;
  - --apply is idempotent (a second run reprocesses zero rows, no duplicate history);
  - a missing market backfills only when a persisted observation exists, and never
    invents bin containment from nothing.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.config import City
from scripts.drain_settlement_quarantine import (
    DEFAULT_MISSING_MARKETS,
    drain,
    resolve_missing_market,
    resolve_quarantined_row,
)

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
        CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
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


def _insert_quarantined(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    provenance: dict,
    settlement_value: float | None = None,
    winning_bin: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO settlement_outcomes
           (city, target_date, temperature_metric, authority, settlement_value,
            winning_bin, provenance_json)
           VALUES (?, ?, ?, 'QUARANTINED', ?, ?, ?)""",
        (city, target_date, metric, settlement_value, winning_bin, json.dumps(provenance)),
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
    )


def _hko_city(name: str = "Hong Kong") -> City:
    return City(
        name=name, lat=22.3, lon=114.2, timezone="Asia/Hong_Kong", settlement_unit="C",
        cluster="test", wu_station="HKO", settlement_source_type="hko",
    )


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = _make_db(tmp_path / "forecasts_test.db")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Point-bin containment + provenance history preservation
# ---------------------------------------------------------------------------

def test_point_bin_contained_verifies_and_preserves_history(db):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_quarantined(
        db, city="Testville", target_date="2026-06-01", metric="high",
        provenance={"quarantine_reason": "harvester_live_obs_outside_bin", "pm_bin_lo": 20.0, "pm_bin_hi": 20.0},
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["rounded_value"] == 20.0
    assert decision["winning_bin"] == "20°C"

    report = drain(db, apply=True, city_map=city_map)
    assert report["disposition_counts"]["verify"] == 1
    assert sid in report["verified_settlement_ids"]

    after = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "VERIFIED"
    assert after["settlement_value"] == 20.0
    assert after["winning_bin"] == "20°C"
    assert after["settlement_unit"] == "C"
    prov = json.loads(after["provenance_json"])
    assert prov["prior_authority"] == "QUARANTINED"
    assert prov["prior_quarantine_reason"] == "harvester_live_obs_outside_bin"
    assert prov["reactivated_by"] == "scripts.drain_settlement_quarantine"
    assert prov["prior_provenance"]["quarantine_reason"] == "harvester_live_obs_outside_bin"


def test_finite_range_bin_contained_verifies(db):
    city_map = {"Testville": _wu_city("Testville", unit="F")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=50.6, unit="F")
    sid = _insert_quarantined(
        db, city="Testville", target_date="2026-06-01", metric="high",
        provenance={"quarantine_reason": "harvester_live_obs_outside_bin", "pm_bin_lo": 50.0, "pm_bin_hi": 51.0, "pm_bin_unit": "F"},
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["rounded_value"] == 51.0  # WMO half-up: 50.6 -> 51
    assert decision["winning_bin"] == "50-51°F"


def test_open_shoulder_bin_contained_verifies(db):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", low_temp=5.0)
    sid = _insert_quarantined(
        db, city="Testville", target_date="2026-06-01", metric="low",
        provenance={"quarantine_reason": "harvester_live_obs_outside_bin", "pm_bin_lo": None, "pm_bin_hi": 6.0},
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["winning_bin"] == "6°C or below"


def test_hko_oracle_truncate_rounding_applied(db):
    city_map = {"Hong Kong": _hko_city()}
    # HKO rounding_rule is oracle_truncate (floor), NOT wmo_half_up: 27.9 must floor to 27,
    # not round to 28. Bin is the point bin "27°C".
    _insert_obs(db, city="Hong Kong", target_date="2026-06-26", source="hko_daily_api", low_temp=27.9, station_id="HKO")
    sid = _insert_quarantined(
        db, city="Hong Kong", target_date="2026-06-26", metric="low",
        provenance={"quarantine_reason": "harvester_source_disagreement_within_tolerance", "pm_bin_lo": 27.0, "pm_bin_hi": 27.0},
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["rounded_value"] == 27.0  # truncate(27.9) == 27, not 28


def test_f_bin_on_c_city_conversion_and_snap(db):
    # Fix #262 case: bin posed in F, city settles in C. 48F -> 8.888C -> snap to 9.
    city_map = {"London": _wu_city("London", unit="C")}
    _insert_obs(db, city="London", target_date="2026-06-01", source="wu_icao_history", high_temp=9.0)
    sid = _insert_quarantined(
        db, city="London", target_date="2026-06-01", metric="high",
        provenance={"quarantine_reason": "harvester_live_obs_outside_bin", "pm_bin_lo": 48.0, "pm_bin_hi": 48.0, "pm_bin_unit": "F"},
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "verify"
    assert decision["winning_bin"] == "9°C"


def test_v1_extra_nested_bin_is_recovered(db):
    """Rows migrated by backfill_settlement_outcomes_canonical_2026_06_02.py nest
    pm_bin_lo/pm_bin_hi under provenance_json['v1_extra'] instead of the top level."""
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_quarantined(
        db, city="Testville", target_date="2026-06-01", metric="high",
        provenance={
            "quarantine_reason": "harvester_live_obs_outside_bin",
            "pm_bin_lo": None, "pm_bin_hi": None,
            "v1_extra": {"pm_bin_lo": 20.0, "pm_bin_hi": 20.0, "unit": "C"},
        },
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "verify"


# ---------------------------------------------------------------------------
# Conflicting evidence and unfillable cases stay QUARANTINED
# ---------------------------------------------------------------------------

def test_conflicting_evidence_stays_quarantined(db):
    city_map = {"Hong Kong": _hko_city()}
    # Recomputed HKO obs (27) disagrees with the market's actual resolved point bin (26).
    _insert_obs(db, city="Hong Kong", target_date="2026-06-26", source="hko_daily_api", low_temp=27.0, station_id="HKO")
    sid = _insert_quarantined(
        db, city="Hong Kong", target_date="2026-06-26", metric="low",
        provenance={"quarantine_reason": "harvester_source_disagreement_within_tolerance", "pm_bin_lo": 26.0, "pm_bin_hi": 26.0},
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "conflicting_evidence_not_contained"

    drain(db, apply=True, city_map=city_map)
    after = db.execute("SELECT authority FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "QUARANTINED"


def test_no_persisted_observation_is_unfillable(db):
    city_map = {"Testville": _wu_city("Testville")}
    sid = _insert_quarantined(
        db, city="Testville", target_date="2026-06-01", metric="high",
        provenance={"quarantine_reason": "harvester_live_no_obs", "pm_bin_lo": 20.0, "pm_bin_hi": 20.0},
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "unfillable_no_persisted_observation"


def test_no_bin_info_is_unfillable_never_invented(db):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_quarantined(
        db, city="Testville", target_date="2026-06-01", metric="high",
        provenance={"quarantine_reason": "harvester_live_obs_outside_bin", "pm_bin_lo": None, "pm_bin_hi": None},
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "unfillable_no_bin_info"


def test_pc_audit_reason_never_auto_reactivated_even_if_contained(db):
    """A pc_audit_* reason carries prior HUMAN audit judgment. Even when the mechanical
    obs+bin re-check would say 'contained', this script must not silently override it."""
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_quarantined(
        db, city="Testville", target_date="2026-06-01", metric="high",
        provenance={"quarantine_reason": "pc_audit_shenzhen_drift_nonreproducible", "pm_bin_lo": 20.0, "pm_bin_hi": 20.0},
    )
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    decision = resolve_quarantined_row(db, city_map, row)
    assert decision["disposition"] == "manual_audit_reserved"

    report = drain(db, apply=True, city_map=city_map)
    assert report["disposition_counts"].get("verify", 0) == 0
    after = db.execute("SELECT authority FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "QUARANTINED"


# ---------------------------------------------------------------------------
# Dry-run / apply / idempotency
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(db):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_quarantined(
        db, city="Testville", target_date="2026-06-01", metric="high",
        provenance={"quarantine_reason": "harvester_live_obs_outside_bin", "pm_bin_lo": 20.0, "pm_bin_hi": 20.0},
    )
    report = drain(db, apply=False, city_map=city_map)
    assert report["applied"] is False
    assert report["disposition_counts"]["verify"] == 1  # decision computed...
    after = db.execute("SELECT authority FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert after["authority"] == "QUARANTINED"  # ...but never written


def test_apply_is_idempotent(db):
    city_map = {"Testville": _wu_city("Testville")}
    _insert_obs(db, city="Testville", target_date="2026-06-01", source="wu_icao_history", high_temp=20.0)
    sid = _insert_quarantined(
        db, city="Testville", target_date="2026-06-01", metric="high",
        provenance={"quarantine_reason": "harvester_live_obs_outside_bin", "pm_bin_lo": 20.0, "pm_bin_hi": 20.0},
    )
    first = drain(db, apply=True, city_map=city_map)
    assert first["disposition_counts"]["verify"] == 1

    second = drain(db, apply=True, city_map=city_map)
    assert second["quarantined_before"] == 0
    assert second["disposition_counts"].get("verify", 0) == 0

    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert row["authority"] == "VERIFIED"
    prov = json.loads(row["provenance_json"])
    # Second run never touched an already-VERIFIED row: no doubled prior_provenance nesting.
    assert "prior_provenance" not in prov["prior_provenance"] if "prior_provenance" in prov else True


# ---------------------------------------------------------------------------
# Missing-market backfill
# ---------------------------------------------------------------------------

def test_missing_market_unfillable_when_no_observation(db):
    city_map = {"Hong Kong": _hko_city(), "Helsinki": _wu_city("Helsinki")}
    for city_name, target_date, metric in DEFAULT_MISSING_MARKETS:
        existing = db.execute(
            "SELECT 1 FROM settlement_outcomes WHERE city=? AND target_date=? AND temperature_metric=?",
            (city_name, target_date, metric),
        ).fetchone()
        assert existing is None
        decision = resolve_missing_market(db, city_map, city_name, target_date, metric)
        assert decision["disposition"] == "unfillable_no_persisted_observation"

    report = drain(db, apply=True, city_map=city_map)
    for missing in report["missing_markets"]:
        assert missing["disposition"] == "unfillable_no_persisted_observation"
    # Nothing was inserted — do not invent settlement rows.
    count = db.execute("SELECT COUNT(*) FROM settlement_outcomes").fetchone()[0]
    assert count == 0


def test_missing_market_backfills_when_observation_persisted():
    """Even with a persisted observation, a market with NO settlement_outcomes row has no
    provenance to recover a bin from — this script must report unfillable, not invent a bin."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        conn = _make_db(Path(d) / "forecasts_test.db")
        try:
            city_map = {"Testville": _wu_city("Testville")}
            _insert_obs(conn, city="Testville", target_date="2026-06-05", source="wu_icao_history", high_temp=20.0)
            decision = resolve_missing_market(conn, city_map, "Testville", "2026-06-05", "high")
            assert decision["disposition"] == "unfillable_no_bin_info"
            count = conn.execute("SELECT COUNT(*) FROM settlement_outcomes").fetchone()[0]
            assert count == 0
        finally:
            conn.close()


def test_missing_market_already_present_is_not_touched(db):
    city_map = {"Testville": _wu_city("Testville")}
    city_name, target_date, metric = "Testville", "2026-06-01", "high"
    sid = _insert_quarantined(
        db, city=city_name, target_date=target_date, metric=metric,
        provenance={"quarantine_reason": "harvester_live_no_obs"},
    )
    report = drain(db, apply=True, missing_markets=((city_name, target_date, metric),), city_map=city_map)
    assert report["missing_markets"][0]["disposition"] == "already_present"
    row = db.execute("SELECT * FROM settlement_outcomes WHERE settlement_id=?", (sid,)).fetchone()
    assert row["authority"] == "QUARANTINED"  # untouched by the missing-market path
