# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b market-topology readiness contract.

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

from src.state.db import init_schema
from src.state.market_topology_repo import (
    get_current_market_topology,
    get_market_topology_state,
    write_market_topology_state,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_market_topology_state_schema_tracks_source_contract_and_authority() -> None:
    conn = _conn()

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(market_topology_state)")}

    assert {
        "topology_id",
        "market_family",
        "condition_id",
        "token_ids_json",
        "source_contract_status",
        "authority_status",
        "status",
    } <= columns


def test_market_topology_repo_round_trips_current_state() -> None:
    conn = _conn()

    write_market_topology_state(
        conn,
        topology_id="topo-1",
        market_family="polymarket_weather_daily_high",
        condition_id="0xabc",
        status="CURRENT",
        source_contract_status="MATCH",
        authority_status="VERIFIED",
        event_id="event-1",
        question_id="question-1",
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
        token_ids_json=["yes", "no"],
        bin_topology_hash="sha256:topology",
        gamma_captured_at=datetime(2026, 5, 2, 8, tzinfo=timezone.utc),
        provenance_json={"scanner": "test"},
    )

    row = get_market_topology_state(conn, "topo-1")
    assert row is not None
    assert row["authority_status"] == "VERIFIED"
    assert json.loads(row["token_ids_json"]) == ["yes", "no"]


def test_current_market_topology_can_be_selected_by_entry_scope() -> None:
    conn = _conn()
    write_market_topology_state(
        conn,
        topology_id="topo-1",
        market_family="polymarket_weather_daily_high",
        condition_id="0xabc",
        status="CURRENT",
        source_contract_status="MATCH",
        authority_status="VERIFIED",
        city_id="LONDON",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
    )

    row = get_current_market_topology(
        conn,
        market_family="polymarket_weather_daily_high",
        condition_id="0xabc",
        city_id="LONDON",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
    )

    assert row is not None
    assert row["topology_id"] == "topo-1"


def test_market_topology_logical_scope_upsert_overwrites_current_state() -> None:
    conn = _conn()
    base_kwargs = {
        "market_family": "polymarket_weather_daily_high",
        "condition_id": "0xabc",
        "source_contract_status": "MATCH",
        "authority_status": "VERIFIED",
        "city_id": "LONDON",
        "target_local_date": date(2026, 5, 3),
        "temperature_metric": "high",
    }

    write_market_topology_state(conn, topology_id="topo-green", status="CURRENT", **base_kwargs)
    write_market_topology_state(
        conn,
        topology_id="topo-red",
        status="MISMATCH",
        source_contract_status="MISMATCH",
        authority_status="UNKNOWN",
        city_id="LONDON",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
        market_family="polymarket_weather_daily_high",
        condition_id="0xabc",
    )

    rows = conn.execute("SELECT * FROM market_topology_state WHERE condition_id = '0xabc'").fetchall()
    assert len(rows) == 1
    assert rows[0]["topology_id"] == "topo-red"
    assert rows[0]["status"] == "MISMATCH"


def test_market_topology_repo_rejects_unknown_status() -> None:
    conn = _conn()

    with pytest.raises(ValueError, match="invalid topology status"):
        write_market_topology_state(
            conn,
            topology_id="topo-bad",
            market_family="polymarket_weather_daily_high",
            condition_id="0xabc",
            status="READY",
            source_contract_status="MATCH",
            authority_status="VERIFIED",
        )
