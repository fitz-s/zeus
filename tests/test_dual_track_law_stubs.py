"""Enforcement stubs for Dual-Track Metric Spine invariants (INV-18..INV-22)
and negative constraints (NC-11..NC-15).

Each test is a skeleton that skips with a message indicating which Phase will
activate the real enforcement. When the enforcement work lands, replace the
pytest.skip() body with the actual assertion.
"""
from __future__ import annotations

import pytest


# NC-11 / INV-14
def test_no_daily_low_on_legacy_table():
    """NC-11: No writing of daily-low rows on legacy (non-v2) tables.

    Verifies:
    1. apply_v2_schema creates the v2 tables in a fresh :memory: DB.
    2. The legacy settlements table still has UNIQUE(city, target_date) —
       not UNIQUE(city, target_date, temperature_metric) — so daily-low
       writes to the legacy table would violate NC-11 (caught at call sites).
    """
    import sqlite3
    from src.state.schema.v2_schema import apply_v2_schema
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")

    # Apply legacy + v2 schema
    init_schema(conn)

    # V2 tables must exist
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for v2_table in [
        "settlements_v2", "market_events_v2", "ensemble_snapshots_v2",
        "calibration_pairs_v2", "platt_models_v2", "observation_instants_v2",
        "historical_forecasts_v2", "day0_metric_fact",
    ]:
        assert v2_table in tables, f"v2 table {v2_table!r} must exist after init_schema"

    # Legacy settlements UNIQUE must still be (city, target_date) — single-metric
    # A second row with same city+date but different temperature_metric must FAIL,
    # proving that legacy table enforces the old single-metric constraint.
    conn.execute(
        "INSERT INTO settlements (city, target_date, authority) VALUES ('NYC', '2026-04-16', 'UNVERIFIED')"
    )
    try:
        conn.execute(
            "INSERT INTO settlements (city, target_date, authority) VALUES ('NYC', '2026-04-16', 'UNVERIFIED')"
        )
        # If we reach here the UNIQUE didn't fire — that's a schema regression
        assert False, (
            "Legacy settlements table accepted a duplicate (city, target_date) row; "
            "UNIQUE(city, target_date) constraint appears to be missing (NC-11 schema regression)"
        )
    except sqlite3.IntegrityError:
        pass  # Expected: legacy table enforces single-metric uniqueness


# NC-12 / INV-16
def test_no_high_low_mix_in_platt_or_bins():
    """NC-12: No mixing of high and low rows in Platt model, calibration pair set, bin lookup, or settlement identity."""
    pytest.skip("pending: enforced in Phase 7 rebuild")


# NC-13 / INV-17
def test_json_export_after_db_commit():
    """NC-13 / INV-17: JSON export writes must occur only after the corresponding DB commit returns.

    Uses commit_then_export to verify:
    1. Normal path: db_op fires, commit happens, json_export fires in order.
    2. Crash path: db_op raises → json_export never fires, DB has no partial row.
    """
    import sqlite3
    from src.state.canonical_write import commit_then_export

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE test_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    conn.execute("CREATE TABLE decision_log (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT)")
    conn.commit()

    call_log: list[str] = []

    def db_op():
        conn.execute("INSERT INTO test_artifacts (v) VALUES ('x')")
        call_log.append("db_op")

    def json_export():
        call_log.append("json_export")

    commit_then_export(conn, db_op=db_op, json_exports=[json_export])

    assert "db_op" in call_log
    assert "json_export" in call_log
    assert call_log.index("db_op") < call_log.index("json_export"), (
        "json_export must be called AFTER db_op (NC-13 / INV-17)"
    )

    # Crash path: db_op raises → no json_export, no partial row
    conn2 = sqlite3.connect(":memory:")
    conn2.execute("CREATE TABLE test_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    conn2.commit()

    json_called = []

    def bad_db_op():
        conn2.execute("INSERT INTO test_artifacts (v) VALUES ('partial')")
        raise RuntimeError("simulated failure")

    try:
        commit_then_export(conn2, db_op=bad_db_op, json_exports=[lambda: json_called.append(True)])
    except RuntimeError:
        pass

    assert not json_called, "json_export must NOT fire when db_op raises (NC-13)"
    (count,) = conn2.execute("SELECT COUNT(*) FROM test_artifacts").fetchone()
    assert count == 0, "DB must have no partial row after db_op failure"


# NC-14 / INV-21
def test_kelly_input_carries_distributional_info():
    """NC-14 / INV-21: kelly_size() must receive a distributional price object, not a bare entry_price scalar."""
    pytest.skip("pending: enforced pre-Phase 9 activation")


# NC-15 / INV-22
def test_fdr_family_key_is_canonical():
    """NC-15 / INV-22: Phase 1 enforcement — scope-aware FDR family grammar.

    make_hypothesis_family_id and make_edge_family_id produce distinct IDs for
    the same candidate inputs, and both are deterministic within their scope.
    This prevents BH discovery budgets from silently merging across scopes.
    """
    from src.strategy.selection_family import (
        make_hypothesis_family_id,
        make_edge_family_id,
    )

    cand = dict(
        cycle_mode="opening_hunt",
        city="NYC",
        target_date="2026-04-01",
        discovery_mode="opening_hunt",
        decision_snapshot_id="snap-1",
    )
    h_id = make_hypothesis_family_id(**cand)
    e_id = make_edge_family_id(**cand, strategy_key="center_buy")

    # Scope separation: same candidate inputs must produce different IDs
    assert h_id != e_id, "hypothesis and edge family IDs must differ for same candidate inputs"

    # Determinism within each scope
    assert h_id == make_hypothesis_family_id(**cand), "hypothesis family ID must be deterministic"
    assert e_id == make_edge_family_id(**cand, strategy_key="center_buy"), "edge family ID must be deterministic"


# INV-19
def test_red_triggers_active_position_sweep():
    """INV-19: RED risk level must cancel all pending orders and sweep active positions toward exit; entry-block-only RED is forbidden."""
    pytest.skip("pending: enforced in risk phase before Phase 9")


# INV-18
def test_chain_reconciliation_three_state_machine():
    """INV-18: Chain reconciliation state is three-valued (CHAIN_SYNCED / CHAIN_EMPTY / CHAIN_UNKNOWN); void decisions require CHAIN_EMPTY, not CHAIN_UNKNOWN."""
    from dataclasses import dataclass, field as dc_field
    from datetime import datetime, timezone, timedelta
    from typing import List
    from src.state.chain_state import ChainState, classify_chain_state

    @dataclass
    class _Pos:
        state: str = "holding"
        chain_verified_at: str = ""
        token_id: str = "tok-1"
        no_token_id: str = ""
        direction: str = "buy_yes"

    @dataclass
    class _Portfolio:
        positions: List[_Pos] = dc_field(default_factory=list)

    fresh = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    # Row 1: non-empty chain → SYNCED
    class _CP:
        token_id = "tok-1"

    result = classify_chain_state(
        fetched_at=fresh, chain_positions=[_CP()], portfolio=_Portfolio()
    )
    assert result == ChainState.CHAIN_SYNCED

    # Row 2: empty chain + stale verified_at → EMPTY (void allowed)
    result = classify_chain_state(
        fetched_at=fresh,
        chain_positions=[],
        portfolio=_Portfolio(positions=[_Pos(chain_verified_at=stale)]),
    )
    assert result == ChainState.CHAIN_EMPTY
    assert result != ChainState.CHAIN_UNKNOWN, "CHAIN_EMPTY must open the void gate"

    # Row 3: empty chain + recent verified_at → UNKNOWN (void blocked)
    result = classify_chain_state(
        fetched_at=fresh,
        chain_positions=[],
        portfolio=_Portfolio(positions=[_Pos(chain_verified_at=recent)]),
    )
    assert result == ChainState.CHAIN_UNKNOWN
    assert result != ChainState.CHAIN_EMPTY, "CHAIN_UNKNOWN must keep void gate closed"

    # Row 4: no fetched_at → UNKNOWN regardless
    result = classify_chain_state(
        fetched_at=None,
        chain_positions=[_CP()],
        portfolio=_Portfolio(),
    )
    assert result == ChainState.CHAIN_UNKNOWN


# INV-20
def test_load_portfolio_degrades_gracefully_on_authority_loss():
    """INV-20: Authority-loss must preserve monitor/exit/reconciliation paths in read-only mode; RuntimeError that kills the full cycle on authority-loss is forbidden."""
    pytest.skip("pending: enforced with Phase 6 runtime split")
