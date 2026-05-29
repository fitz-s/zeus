# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P2 step-4 (P2_LEDGER_SEAM_FINDINGS_2026-05-29.md §wiring-plan).
#   End-to-end relationship test of build_evidence on the REAL canonical forecasts schema
#   (init_schema_forecasts) — the only layer that catches (a) the dataset_id column rename
#   and (b) the SQL column wiring, which dict-fixture unit tests cannot.
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Integration test of build_evidence against canonical schema — catches dataset_id column rename and pair_residual D-J1 wrong-station drop.
# Reuse: Run after any change to build_evidence SQL, pair_residual gate, or init_schema_forecasts DDL.
"""build_evidence against the canonical ensemble_snapshots / settlement_outcomes schema.

Two structural guarantees the legacy loose JOIN lacked:
  - reads the canonical `dataset_id` lineage column (the removed `data_version` errors here);
  - DROPS a wrong-station settlement (same city/date/metric) via the pair_residual gate (D-J1),
    instead of emitting a residual with a collapsed lineage.
"""

from __future__ import annotations

import json
import sqlite3

from scripts.build_ens_residual_evidence import build_evidence
from src.state.db import init_schema_forecasts


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    init_schema_forecasts(c)
    return c


def _insert(conn: sqlite3.Connection, table: str, **vals) -> None:
    cols = ",".join(vals)
    ph = ",".join("?" * len(vals))
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", list(vals.values()))


def _snap(conn: sqlite3.Connection, **over) -> None:
    base = dict(
        city="Chicago", target_date="2026-05-20", temperature_metric="high",
        physical_quantity="temperature", observation_field="high_temp",
        issue_time="2026-05-18T00:00:00+00:00",
        source_cycle_time="2026-05-18T00:00:00+00:00",
        available_at="2026-05-18T01:00:00+00:00", fetch_time="2026-05-18T01:00:00+00:00",
        lead_hours=48.0, members_json=json.dumps([72.0, 74.0, 76.0]), members_unit="degF",
        model_version="ecmwf_ens",
        dataset_id="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
        settlement_station_id="KORD", settlement_unit="F", settlement_source_type="wu_icao",
        forecast_window_start_utc="2026-05-20T05:00:00+00:00",
        forecast_window_end_utc="2026-05-21T05:00:00+00:00",
        source_run_id="run-1", contributes_to_target_extrema=1, authority="VERIFIED",
    )
    base.update(over)
    _insert(conn, "ensemble_snapshots", **base)


def _settle(conn: sqlite3.Connection, **over) -> None:
    base = dict(
        city="Chicago", target_date="2026-05-20", temperature_metric="high",
        settlement_value=77.0,
        settlement_source="https://www.wunderground.com/history/daily/us/il/chicago/KORD",
        provenance_json=json.dumps({"data_version": "wu_icao_history_v1"}),
        authority="VERIFIED",
    )
    base.update(over)
    _insert(conn, "settlement_outcomes", **base)


def test_select_uses_dataset_id_not_data_version():
    """Regression for the rename: build_evidence must read the canonical `dataset_id`, not the
    removed `data_version` column — a stale SELECT raises OperationalError on this schema."""
    conn = _conn()
    _snap(conn)
    _settle(conn)
    out = build_evidence(conn, metric="high", lead_max=48.0, cities=None, accept_cycle=None)
    assert len(out) == 1


def test_true_pair_emitted_with_product_dim():
    conn = _conn()
    _snap(conn)
    _settle(conn)
    out = build_evidence(conn, metric="high", lead_max=48.0, cities=None, accept_cycle=None)
    assert len(out) == 1
    row = out[0]
    assert row["city"] == "Chicago"
    assert row["data_version"] == "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
    assert row["product"] == "mx2t3"  # ResidualKey dim emitted (step 4)


def test_low_equivalent_constructions_dedup_to_one_no_double_count():
    """Pre-LOW guard. The two live LOW constructions tigge_mn2t6_local_calendar_day_min_v1 and
    ..._contract_window_v2 are bit-identical (verified 2026-05-29: 334,215 paired live rows,
    max |Δ| = 0.0000°C — contract_window 'serves the same local-day settlement object', per
    ensemble_snapshot_provenance). They share (city, target_date, issue_time) and differ only in
    dataset_id, so the ledger must emit ONE residual per (city, date) — never double-count the
    duplicate construction (which would inflate n_paired / effective sample size). Also the first
    LOW-metric (12z cycle, mn2t6 product) path exercise of build_evidence."""
    conn = _conn()
    low = dict(
        temperature_metric="low", observation_field="low_temp",
        issue_time="2026-05-18T12:00:00+00:00",          # 12z = the LOW-strict cycle
        source_cycle_time="2026-05-18T12:00:00+00:00",
        members_json=json.dumps([50.0, 51.0, 52.0]),
    )
    _snap(conn, dataset_id="tigge_mn2t6_local_calendar_day_min_v1", **low)
    _snap(conn, dataset_id="tigge_mn2t6_local_calendar_day_min_contract_window_v2", **low)
    _settle(conn, temperature_metric="low", settlement_value=52.0)

    out = build_evidence(conn, metric="low", lead_max=48.0, cities=None, accept_cycle=None)
    assert len(out) == 1, f"expected ONE deduped LOW residual, got {len(out)} (double-count)"
    assert out[0]["product"] == "mn2t6"
    assert out[0]["data_version"].startswith("tigge_mn2t6_local_calendar_day_min")


def test_wrong_station_dropped_end_to_end():
    """D-J1 on the REAL schema: forecast claims KORD, the city's settlement is KMDW (same
    city/date/metric — the legacy loose JOIN paired them). The gated build_evidence DROPS it."""
    conn = _conn()
    _snap(conn, settlement_station_id="KORD")
    _settle(
        conn,
        settlement_source="https://www.wunderground.com/history/daily/us/il/chicago/KMDW",
    )
    out = build_evidence(conn, metric="high", lead_max=48.0, cities=None, accept_cycle=None)
    assert out == []  # dropped — never emitted with a collapsed lineage


def test_hong_kong_unparseable_url_with_station_column_emitted():
    """D-S1 end-to-end: HKO settles via a climat.htm URL that carries NO station code. Pre-D-S1
    the URL parse raised SettlementIncompleteError → the residual was dropped, starving HK's
    ledger. With the first-class settlement_station column populated on the settlement row,
    build_evidence reads it (SELECT carries s.settlement_station) and emits the residual."""
    conn = _conn()
    _snap(
        conn, city="Hong Kong", settlement_station_id="VHHH",
        settlement_source_type="hko", settlement_unit="C",
        dataset_id="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
    )
    _settle(
        conn, city="Hong Kong", settlement_station="VHHH", settlement_unit="C",
        settlement_source="https://www.hko.gov.hk/en/cis/climat.htm",  # no station in URL
        provenance_json=json.dumps({"data_version": "hko_daily_api_v1"}),
    )
    out = build_evidence(conn, metric="high", lead_max=48.0, cities=None, accept_cycle=None)
    assert len(out) == 1
    assert out[0]["city"] == "Hong Kong"


def test_settlement_unit_mismatch_dropped_end_to_end():
    """D-S1 de-tautologization end-to-end: the forecast claims its settlement is in F
    (ensemble_snapshots.settlement_unit='F'); the settlement row's VERIFIED settlement_unit
    column says 'C'. That is a degC/degF mis-scale — the gate must DROP it, not silently coerce
    the settlement to the forecast's claim (the pre-D-S1 tautology emitted a mis-scaled residual)."""
    conn = _conn()
    _snap(conn, settlement_unit="F")
    _settle(conn, settlement_unit="C")  # verified column disagrees with the forecast's F claim
    out = build_evidence(conn, metric="high", lead_max=48.0, cities=None, accept_cycle=None)
    assert out == []


def test_settlement_unit_column_agreeing_emitted_end_to_end():
    """Control for the mismatch test: a VERIFIED settlement_unit column that AGREES with the
    forecast's claim emits normally (the de-tautologization only drops genuine disagreements)."""
    conn = _conn()
    _snap(conn, settlement_unit="F")
    _settle(conn, settlement_unit="F")
    out = build_evidence(conn, metric="high", lead_max=48.0, cities=None, accept_cycle=None)
    assert len(out) == 1


# Pre-D-S1 settlement_outcomes (no settlement_station / settlement_unit columns) — what an
# un-migrated live forecasts DB carries. build_evidence must detect the columns' ABSENCE (PRAGMA)
# and OMIT them from the SELECT rather than OperationalError.
_LEGACY_SETTLEMENT_DDL = """
    CREATE TABLE settlement_outcomes (
        settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT NOT NULL, target_date TEXT NOT NULL,
        temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
        market_slug TEXT, winning_bin TEXT, settlement_value REAL, settlement_source TEXT,
        settled_at TEXT,
        authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
            CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
        provenance_json TEXT NOT NULL DEFAULT '{}',
        recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, outcome_type INTEGER,
        UNIQUE(city, target_date, temperature_metric)
    )
"""


def test_build_evidence_graceful_on_pre_ds1_db_missing_columns():
    """Graceful degradation (the has_ds1_settlement_cols PRAGMA branch): a pre-D-S1 forecasts DB
    has NO settlement_station/settlement_unit columns. build_evidence must detect their absence
    and OMIT them from the SELECT — returning rows via the URL/claim heuristic, NOT raising
    OperationalError (settlement_unit's CHECK blocks a DROP COLUMN, so rebuild the legacy table)."""
    conn = _conn()
    conn.execute("DROP TABLE settlement_outcomes")
    conn.execute(_LEGACY_SETTLEMENT_DDL)
    _snap(conn)
    _settle(conn)  # legacy columns only — no station/unit
    out = build_evidence(conn, metric="high", lead_max=48.0, cities=None, accept_cycle=None)
    assert len(out) == 1  # heuristic fallback path, no OperationalError
