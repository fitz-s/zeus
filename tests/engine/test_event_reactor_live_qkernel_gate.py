# Created: 2026-06-30
# Last reused/audited: 2026-06-30
# Authority basis: live-money qkernel submit authority and canonical selection-fact persistence.

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.event_reactor_adapter import (
    _assert_live_entry_submit_authority,
    _record_qkernel_selection_family_facts,
)


def _qkernel_cert() -> dict:
    return {
        "source": "qkernel_spine",
        "candidate_id": "YES:bin-1:DIRECT_YES:bin-1@proof",
        "bin_id": "bin-1",
        "route_id": "DIRECT_YES:bin-1@proof",
        "side": "YES",
        "payoff_q_point": 0.70,
        "payoff_q_lcb": 0.60,
        "edge_lcb": 0.20,
        "delta_u_at_min": 0.01,
        "optimal_stake_usd": 1.0,
        "optimal_delta_u": 0.02,
        "cost": 0.40,
        "false_edge_rate": 0.01,
        "direction_law_ok": True,
        "coherence_allows": True,
        "selection_guard_basis": "SELECTION_BETA_95",
        "selection_guard_abstained": False,
        "selection_guard_q_safe": 0.60,
    }


def _fake_qkernel_decision() -> SimpleNamespace:
    cost = SimpleNamespace(value=0.40)
    economics = SimpleNamespace(
        candidate_id="YES:bin-1:DIRECT_YES:bin-1@proof",
        route_id="DIRECT_YES:bin-1@proof",
        cost=cost,
        chosen_stake_cost=None,
        edge_lcb=0.20,
        point_ev=0.25,
        delta_u_at_min=0.01,
        optimal_delta_u=0.02,
        optimal_stake_usd=Decimal("1.00"),
        q_dot_payoff=0.70,
        payoff_q_lcb=0.60,
    )
    route = SimpleNamespace(side="YES", bin_id="bin-1")
    candidate_decision = SimpleNamespace(
        route=route,
        economics=economics,
        q_lcb_guard_basis="QLCB_IDENTITY",
        q_lcb_guard_abstained=False,
        q_lcb_guard_cell_key="",
        selection_guard_basis="SELECTION_BETA_95",
        selection_guard_abstained=False,
        selection_guard_cell_key="YES|L1|modal|pb6",
        selection_guard_n=100,
        selection_guard_q_safe=0.60,
        direction_law_ok=True,
        coherence_allows=True,
        robust_trade_score=0.20,
    )
    return SimpleNamespace(
        decision_id="qkernel-decision-1",
        receipt_hash="receipt-1",
        selected=economics,
        no_trade_reason=None,
        omega=SimpleNamespace(
            bins=(SimpleNamespace(bin_id="bin-1", label="30C"),)
        ),
        candidate_decisions=(candidate_decision,),
    )


def _fake_family() -> SimpleNamespace:
    return SimpleNamespace(
        family_id="weather-family-1",
        city="Shanghai",
        target_date="2026-06-30",
        metric="high",
    )


def _fake_event() -> SimpleNamespace:
    return SimpleNamespace(
        event_id="event-qkernel-selection",
        event_type="FORECAST_SNAPSHOT_READY",
        causal_snapshot_id="snapshot-qkernel-selection",
    )


def test_qkernel_selection_facts_write_to_attached_world_not_trade_local(tmp_path):
    from src.state.db import init_schema

    world_path = tmp_path / "world.db"
    world = sqlite3.connect(world_path)
    world.row_factory = sqlite3.Row
    init_schema(world)
    world.close()

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))

    result = _record_qkernel_selection_family_facts(
        conn,
        family=_fake_family(),
        decision=_fake_qkernel_decision(),
        event=_fake_event(),
        decision_time=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        decision_snapshot_id="snapshot-qkernel-selection",
    )

    assert result["status"] == "written"
    assert result["families"] == 1
    assert result["hypotheses"] == 1
    assert conn.execute("SELECT COUNT(*) FROM main.selection_family_fact").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM world.selection_family_fact").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM world.selection_hypothesis_fact").fetchone()[0] == 1
    conn.close()


def test_qkernel_selection_facts_fail_closed_without_attached_world(tmp_path):
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    result = _record_qkernel_selection_family_facts(
        conn,
        family=_fake_family(),
        decision=_fake_qkernel_decision(),
        event=_fake_event(),
        decision_time=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        decision_snapshot_id="snapshot-qkernel-selection",
    )

    assert result["status"] == "skipped_missing_canonical_world_table"
    assert conn.execute("SELECT COUNT(*) FROM main.selection_family_fact").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM main.selection_hypothesis_fact").fetchone()[0] == 0
    conn.close()


def test_live_entry_qkernel_gate_accepts_stamped_matching_cert():
    _assert_live_entry_submit_authority(
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "selection_authority_applied": "qkernel_spine",
            "direction": "buy_yes",
            "candidate_bin_id": "bin-1",
            "q_live": 0.70,
            "q_lcb_5pct": 0.60,
            "strategy_key": "center_buy",
            "min_entry_price": 0.10,
            "qkernel_execution_economics": _qkernel_cert(),
        }
    )


def test_live_entry_qkernel_gate_rejects_legacy_unstamped_payload():
    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_AUTHORITY_REQUIRED"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": None,
                "direction": "buy_yes",
                "candidate_bin_id": "bin-1",
                "qkernel_execution_economics": _qkernel_cert(),
            }
        )


def test_live_entry_qkernel_gate_rejects_bin_mismatch():
    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_CERT_BIN_MISMATCH"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "candidate_bin_id": "other-bin",
                "qkernel_execution_economics": _qkernel_cert(),
            }
        )


def test_live_entry_qkernel_gate_rejects_cost_below_strategy_entry_floor():
    cert = _qkernel_cert()
    cert.update(cost=0.07, payoff_q_lcb=0.18, payoff_q_point=0.24, edge_lcb=0.11)

    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_COST_BELOW_STRATEGY_FLOOR"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "strategy_key": "center_buy",
                "candidate_bin_id": "bin-1",
                "q_live": 0.24,
                "q_lcb_5pct": 0.18,
                "min_entry_price": 0.10,
                "qkernel_execution_economics": cert,
            }
        )


def test_live_entry_qkernel_gate_uses_current_registry_floor_over_legacy_payload():
    cert = _qkernel_cert()
    cert.update(cost=0.07, payoff_q_lcb=0.18, payoff_q_point=0.24, edge_lcb=0.11)

    with pytest.raises(ValueError, match="min_entry_price=0.100000000"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "strategy_key": "center_buy",
                "candidate_bin_id": "bin-1",
                "q_live": 0.24,
                "q_lcb_5pct": 0.18,
                # Durable receipts from older live code can carry the old 5c floor;
                # current live registry must still reject this 7c center-buy YES.
                "min_entry_price": 0.05,
                "qkernel_execution_economics": cert,
            }
        )


def _day0_payload(**overrides) -> dict:
    payload = {
        "event_type": "DAY0_EXTREME_UPDATED",
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    payload.update(overrides)
    return payload


def test_live_entry_day0_gate_accepts_live_observation_authority_without_qkernel():
    _assert_live_entry_submit_authority(_day0_payload(selection_authority_applied=None))


def test_live_entry_day0_gate_rejects_missing_live_observation_authority():
    with pytest.raises(
        ValueError,
        match="LIVE_ENTRY_DAY0_OBSERVATION_AUTHORITY_REQUIRED:live_authority_status=missing",
    ):
        _assert_live_entry_submit_authority(_day0_payload(live_authority_status=None))


def test_live_entry_gate_rejects_unknown_event_type_even_with_qkernel_cert():
    with pytest.raises(ValueError, match="LIVE_ENTRY_AUTHORITY_UNSUPPORTED_EVENT_TYPE"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "EXPERIMENTAL_EVENT",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "candidate_bin_id": "bin-1",
                "qkernel_execution_economics": _qkernel_cert(),
            }
        )
