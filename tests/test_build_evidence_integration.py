# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P2 step-4 (P2_LEDGER_SEAM_FINDINGS_2026-05-29.md §wiring-plan).
#   End-to-end relationship test of build_evidence on the REAL canonical forecasts schema
#   (init_schema_forecasts) — the only layer that catches (a) the dataset_id column rename
#   and (b) the SQL column wiring, which dict-fixture unit tests cannot.
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
