# Created: 2026-06-30
# Last reused/audited: 2026-06-30
# Authority basis: live-money qkernel submit authority and canonical selection-fact persistence.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.event_reactor_adapter import (
    PreSubmitAuthorityWitness,
    _assert_live_entry_submit_authority,
    _fdr_rejection_reason,
    _pre_submit_revalidation_payload_from_final_intent,
    _record_day0_selection_family_facts,
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


def _fake_qkernel_decision_with_prefilter_reject() -> SimpleNamespace:
    selected_cost = SimpleNamespace(value=0.40)
    selected = SimpleNamespace(
        candidate_id="YES:bin-1:DIRECT_YES:bin-1@proof",
        route_id="DIRECT_YES:bin-1@proof",
        cost=selected_cost,
        chosen_stake_cost=None,
        edge_lcb=0.20,
        point_ev=0.25,
        delta_u_at_min=0.01,
        optimal_delta_u=0.02,
        optimal_stake_usd=Decimal("1.00"),
        q_dot_payoff=0.70,
        payoff_q_lcb=0.60,
    )
    rejected_cost = SimpleNamespace(value=0.40)
    rejected = SimpleNamespace(
        candidate_id="NO:bin-2:DIRECT_NO:bin-2@proof",
        route_id="DIRECT_NO:bin-2@proof",
        cost=rejected_cost,
        chosen_stake_cost=None,
        edge_lcb=-0.01,
        point_ev=0.01,
        delta_u_at_min=0.01,
        optimal_delta_u=0.02,
        optimal_stake_usd=Decimal("1.00"),
        q_dot_payoff=0.70,
        payoff_q_lcb=0.39,
    )
    selected_decision = SimpleNamespace(
        route=SimpleNamespace(side="YES", bin_id="bin-1"),
        economics=selected,
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
    rejected_decision = SimpleNamespace(
        route=SimpleNamespace(side="NO", bin_id="bin-2"),
        economics=rejected,
        q_lcb_guard_basis="QLCB_IDENTITY",
        q_lcb_guard_abstained=False,
        q_lcb_guard_cell_key="",
        selection_guard_basis="SELECTION_BETA_95",
        selection_guard_abstained=False,
        selection_guard_cell_key="NO|L1|modal|pb6",
        selection_guard_n=100,
        selection_guard_q_safe=0.39,
        direction_law_ok=True,
        coherence_allows=True,
        robust_trade_score=-0.01,
    )
    return SimpleNamespace(
        decision_id="qkernel-decision-1",
        receipt_hash="receipt-1",
        selected=selected,
        no_trade_reason=None,
        omega=SimpleNamespace(
            bins=(
                SimpleNamespace(bin_id="bin-1", label="30C"),
                SimpleNamespace(bin_id="bin-2", label="31C"),
            )
        ),
        candidate_decisions=(rejected_decision, selected_decision),
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


def _fake_day0_event() -> SimpleNamespace:
    return SimpleNamespace(
        event_id="event-day0-selection",
        event_type="DAY0_EXTREME_UPDATED",
        causal_snapshot_id="snapshot-day0-selection",
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


def test_day0_legacy_selection_facts_write_to_attached_world_not_trade_local(tmp_path):
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

    selected = SimpleNamespace(
        candidate_id="day0-candidate-selected",
        condition_id="condition-1",
        token_id="token-yes",
        direction="buy_yes",
        bin_label="30C",
        execution_price=0.38,
        q_posterior=1.0,
        q_lcb_5pct=0.96,
        c_cost_95pct=0.38,
        p_fill_lcb=0.90,
        trade_score=0.58,
        p_value=0.04,
        passed_prefilter=True,
        native_quote_available=True,
        missing_reason=None,
        support_index=0,
        bin_id="bin-30c",
    )
    rejected = SimpleNamespace(
        candidate_id="day0-candidate-rejected",
        condition_id="condition-2",
        token_id="token-no",
        direction="buy_no",
        bin_label="31C",
        execution_price=0.72,
        q_posterior=0.05,
        q_lcb_5pct=0.02,
        c_cost_95pct=0.72,
        p_fill_lcb=0.90,
        trade_score=0.0,
        p_value=1.0,
        passed_prefilter=False,
        native_quote_available=True,
        missing_reason="ADMISSION_CAPITAL_EFFICIENCY_LCB_EV",
        support_index=1,
        bin_id="bin-31c",
    )
    book = SimpleNamespace(
        book_id="opportunity-book-day0",
        book_version=1,
        evaluations=(selected, rejected),
        selected_candidate_id=selected.candidate_id,
        cache_summary={
            "selection_authority": "robust_marginal_utility",
            "actual_receipt_selected_candidate_id": selected.candidate_id,
        },
    )

    result = _record_day0_selection_family_facts(
        conn,
        family=_fake_family(),
        opportunity_book=book,
        event=_fake_day0_event(),
        decision_time=datetime(2026, 7, 1, 9, tzinfo=timezone.utc),
        decision_snapshot_id="snapshot-day0-selection",
    )

    assert result["status"] == "written"
    assert result["families"] == 1
    assert result["hypotheses"] == 2
    assert conn.execute("SELECT COUNT(*) FROM main.selection_family_fact").fetchone()[0] == 0
    family_row = conn.execute(
        "SELECT strategy_key, discovery_mode, meta_json FROM world.selection_family_fact"
    ).fetchone()
    assert family_row["strategy_key"] == "settlement_capture"
    assert family_row["discovery_mode"] == "DAY0_EXTREME_UPDATED"
    family_meta = json.loads(family_row["meta_json"])
    assert family_meta["source"] == "event_bound_legacy_selector"
    assert family_meta["selected_post_fdr"] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM world.selection_hypothesis_fact WHERE selected_post_fdr=1"
    ).fetchone()[0] == 1
    rejected_row = conn.execute(
        """
        SELECT rejection_stage, meta_json
        FROM world.selection_hypothesis_fact
        WHERE selected_post_fdr=0
        """
    ).fetchone()
    assert rejected_row["rejection_stage"] == "EVENT_BOUND_GATE_REJECTED"
    assert json.loads(rejected_row["meta_json"])["missing_reason"] == (
        "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV"
    )
    conn.close()


def test_qkernel_prefilter_rejection_uses_stable_stage_and_meta_detail(tmp_path):
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
        decision=_fake_qkernel_decision_with_prefilter_reject(),
        event=_fake_event(),
        decision_time=datetime(2026, 6, 30, 12, tzinfo=timezone.utc),
        decision_snapshot_id="snapshot-qkernel-selection",
    )

    assert result["status"] == "written"
    assert result["hypotheses"] == 2
    row = conn.execute(
        """
        SELECT rejection_stage, meta_json
        FROM world.selection_hypothesis_fact
        WHERE candidate_id = ?
        """,
        ("NO:bin-2:DIRECT_NO:bin-2@proof",),
    ).fetchone()
    assert row is not None
    assert row["rejection_stage"] == "QKERNEL_PREFILTER_REJECTED"
    assert json.loads(row["meta_json"])["rejection_detail"] == "edge_lcb_nonpositive"
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


def test_live_entry_qkernel_gate_accepts_low_cost_when_qkernel_cert_is_authoritative():
    cert = _qkernel_cert()
    cert.update(cost=0.07, payoff_q_lcb=0.18, payoff_q_point=0.24, edge_lcb=0.11)

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


def test_live_entry_qkernel_gate_rejects_low_price_yes_tail_below_roi_frontier_floor():
    cert = _qkernel_cert()
    cert.update(
        route_id="DIRECT_YES:b34@proof",
        candidate_id="YES:b34:DIRECT_YES:b34@proof",
        bin_id="b34",
        payoff_q_point=0.12180248510788458,
        payoff_q_lcb=0.06052567908958011,
        cost=0.04001526925923045,
        edge_lcb=0.020510409830349664,
        delta_u_at_min=0.00009152233738979263,
        optimal_stake_usd=1.4412832709285736,
        optimal_delta_u=0.0006333828915951036,
        selection_guard_q_safe=0.06052567908958011,
    )

    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_EXECUTION_ECONOMICS_INVALID"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "strategy_key": "center_buy",
                "candidate_bin_id": "b34",
                "q_live": 0.12180248510788458,
                "q_lcb_5pct": 0.06052567908958011,
                "min_entry_price": 0.02,
                "qkernel_execution_economics": cert,
            }
        )


def test_live_entry_qkernel_gate_rejects_nonpositive_delta_u_at_min():
    cert = _qkernel_cert()
    cert.update(delta_u_at_min=-0.01)

    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_EXECUTION_ECONOMICS_INVALID"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "strategy_key": "center_buy",
                "candidate_bin_id": "bin-1",
                "q_live": 0.70,
                "q_lcb_5pct": 0.60,
                "min_entry_price": 0.10,
                "qkernel_execution_economics": cert,
            }
        )


def test_live_entry_qkernel_gate_does_not_reapply_legacy_price_floor():
    cert = _qkernel_cert()
    cert.update(cost=0.07, payoff_q_lcb=0.18, payoff_q_point=0.24, edge_lcb=0.11)

    _assert_live_entry_submit_authority(
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "selection_authority_applied": "qkernel_spine",
            "direction": "buy_yes",
            "strategy_key": "center_buy",
            "candidate_bin_id": "bin-1",
            "q_live": 0.24,
            "q_lcb_5pct": 0.18,
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


def test_day0_fdr_rejection_reason_carries_route_evidence():
    reason = _fdr_rejection_reason(
        event_type="DAY0_EXTREME_UPDATED",
        fdr=SimpleNamespace(
            attempted_hypotheses=22,
            selected_post_fdr=(),
        ),
        selected_proof=SimpleNamespace(
            passed_prefilter=True,
            q_posterior=0.94,
            q_lcb_5pct=0.91,
            execution_price=SimpleNamespace(value=0.62),
            trade_score=0.29,
            probability_authority="day0_absorbing_hard_fact",
            missing_reason=None,
        ),
    )

    assert reason.startswith("FDR_REJECTED:")
    assert "event_type=DAY0_EXTREME_UPDATED" in reason
    assert "q_lcb=0.910000" in reason
    assert "price=0.620000" in reason
    assert "day0_false_edge_rate=0.090000" in reason
    assert "probability_authority=day0_absorbing_hard_fact" in reason


def test_day0_pre_submit_payload_preserves_observation_authority():
    final_intent = SimpleNamespace(
        certificate_hash="final-hash",
        payload={
            "event_id": "event-1",
            "event_type": "DAY0_EXTREME_UPDATED",
            "final_intent_id": "intent-1",
            "strategy_key": "settlement_capture",
            "condition_id": "condition-1",
            "token_id": "token-no",
            "side": "BUY",
            "direction": "buy_no",
            "city": "Chicago",
            "target_date": "2026-05-24",
            "metric": "high",
            "temperature_metric": "high",
            "bin_label": "79F or below",
            "outcome_label": "No",
            "unit": "F",
            "order_type": "LIMIT",
            "time_in_force": "GTC",
            "post_only": True,
            "limit_price": 0.40,
            "q_live": 0.70,
            "q_lcb_5pct": 0.60,
            "trade_score": 0.20,
            "action_score": 0.20,
            "size": 10.0,
            "min_entry_price": 0.10,
            "min_expected_profit_usd": 1.0,
            "min_submit_edge_density": 0.05,
            "c_fee_adjusted": 0.40,
            "c_cost_95pct": 0.45,
            "selection_authority_applied": None,
            "qkernel_execution_economics": None,
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "cost_basis_hash": "cost-hash",
        },
    )
    witness = PreSubmitAuthorityWitness(
        quote_seen_at="2026-05-24T18:59:59+00:00",
        book_hash="book-hash",
        current_best_bid=0.39,
        current_best_ask=0.41,
        tick_size=0.01,
        min_order_size=5.0,
        neg_risk=False,
        heartbeat_status="OK",
        user_ws_status="OK",
        venue_connectivity_status="OK",
        balance_allowance_status="OK",
        book_authority_id="execution_feasibility_evidence",
        book_captured_at="2026-05-24T18:59:59+00:00",
        heartbeat_authority_id="heartbeat_supervisor",
        heartbeat_checked_at="2026-05-24T19:00:00+00:00",
        user_ws_authority_id="ws_gap_guard",
        user_ws_checked_at="2026-05-24T19:00:00+00:00",
        venue_connectivity_authority_id="polymarket_public_orderbook",
        venue_connectivity_checked_at="2026-05-24T19:00:00+00:00",
        balance_allowance_authority_id="polymarket_wallet_readonly",
        balance_allowance_checked_at="2026-05-24T19:00:00+00:00",
        checked_at="2026-05-24T19:00:00+00:00",
    )

    payload = _pre_submit_revalidation_payload_from_final_intent(
        final_intent=final_intent,
        executable_snapshot=SimpleNamespace(payload={}),
        decision_time=datetime(2026, 5, 24, 19, tzinfo=timezone.utc),
        authority_witness=witness,
    )

    assert payload["event_type"] == "DAY0_EXTREME_UPDATED"
    assert payload["qkernel_execution_economics"] is None
    assert payload["source_match_status"] == "MATCH"
    assert payload["local_date_status"] == "MATCH"
    assert payload["station_match_status"] == "MATCH"
    assert payload["dst_status"] == "UNAMBIGUOUS"
    assert payload["metric_match_status"] == "MATCH"
    assert payload["rounding_status"] == "MATCH"
    assert payload["source_authorized_status"] == "AUTHORIZED"
    assert payload["live_authority_status"] == "live"


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
