# Created: 2026-06-30
# Last reused/audited: 2026-07-09
# Authority basis: live-money qkernel submit authority and canonical selection-fact persistence.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.engine.event_reactor_adapter as era
from src.engine.event_reactor_adapter import (
    PreSubmitAuthorityWitness,
    _assert_live_entry_submit_authority,
    _candidate_bin_id_from_topology,
    _day0_live_submit_admission_rejection_reason,
    _day0_selected_route_fdr_proof,
    _event_bound_strategy_key,
    _fdr_rejection_reason,
    _final_intent_decision_source_context_payload,
    _pre_submit_revalidation_payload_from_final_intent,
    _qkernel_economics_with_near_day0_consistency,
    _qkernel_near_day0_cert_rejection_reason,
    _record_qkernel_selection_family_facts,
)
from src.events.candidate_binding import MarketTopologyCandidate
from src.contracts.execution_intent import DecisionSourceContext
from src.decision_kernel import claims
from src.decision_kernel.certificate import build_certificate
from src.types.market import Bin


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


def _current_qkernel_cert(*, side: str = "YES") -> dict:
    cert = _qkernel_cert()
    cert.update(
        decision_id="decision-current-1",
        receipt_hash="receipt-current-1",
        q_version="q-current-1",
        sample_hash="current-sample-hash",
        side=side,
        route_id=f"DIRECT_{side}:bin-1@proof",
        candidate_id=f"{side}:bin-1:DIRECT_{side}:bin-1@proof",
        q_lcb_guard_basis="CURRENT_POSTERIOR_BAND",
        q_lcb_guard_abstained=False,
        q_lcb_guard_cell_key="current-sample-hash",
        selection_guard_basis="CURRENT_POSTERIOR_BAND",
        selection_guard_abstained=False,
        selection_guard_cell_key="current-sample-hash",
        selection_guard_n=64,
    )
    _seal_current_qkernel_cert(cert)
    return cert


def _seal_current_qkernel_cert(cert: dict) -> None:
    cert["current_state_identity_hash"] = era.qkernel_current_state_identity_hash(cert)


def _day0_probability_fields(
    *,
    condition_id: str = "condition-1",
    q_live: float = 0.70,
    q_lcb: float = 0.60,
) -> dict[str, object]:
    lcb_transform = {
        "yes_lcb_by_condition": {condition_id: q_lcb},
        "no_lcb_by_condition": {condition_id: 0.20},
        "mask": [1.0],
    }
    return {
        "condition_id": condition_id,
        "q_live": q_live,
        "q_lcb_5pct": q_lcb,
        "day0_probability_authority": {
            "q_source": "day0_remaining_day",
            "q_mode": "remaining_day",
            "remaining_models": 3,
            "rounded_value": 32,
            "observation_time": "2026-07-02T02:00:00+00:00",
            "observation_available_at": "2026-07-02T02:06:24+00:00",
            "lcb_transform": lcb_transform,
        },
        "_edli_q_source": "day0_remaining_day",
        "_edli_day0_q_mode": "remaining_day",
        "_edli_day0_remaining_models": 3,
        "_edli_day0_lcb_transform": lcb_transform,
    }


def _day0_qkernel_cert(*, q_live: float = 0.70, q_lcb: float = 0.60) -> dict:
    cert = _qkernel_cert()
    cert.update(
        payoff_q_point=q_live,
        payoff_q_lcb=q_lcb,
        cost=0.40,
        edge_lcb=q_lcb - 0.40,
        q_lcb_guard_basis="DAY0_REMAINING_DAY_Q_LCB",
        q_lcb_guard_abstained=False,
        q_lcb_guard_cell_key="day0_remaining_day_q_lcb",
        selection_guard_basis="DAY0_REMAINING_DAY_Q_LCB",
        selection_guard_abstained=False,
        selection_guard_cell_key="day0_remaining_day_q_lcb",
        selection_guard_n=0,
        selection_guard_q_safe=q_lcb,
    )
    return cert


def _bound_day0_qkernel_route_proof(
    *,
    q_live: float,
    q_lcb: float,
    price: float,
    trade_score: float,
    false_edge_rate: float,
) -> SimpleNamespace:
    proof = SimpleNamespace(
        passed_prefilter=True,
        q_posterior=q_live,
        q_lcb_5pct=q_lcb,
        execution_price=SimpleNamespace(value=price),
        trade_score=trade_score,
        probability_authority="day0_absorbing_hard_fact",
        missing_reason=None,
        q_source="qkernel_spine",
        selection_authority_applied="qkernel_spine",
        direction="buy_yes",
        candidate=SimpleNamespace(
            condition_id="condition-1",
            bin=SimpleNamespace(low=10, high=10, unit="C", label="10C"),
        ),
    )
    bin_id = era._candidate_bin_id(proof)
    cert = _day0_qkernel_cert(q_live=q_live, q_lcb=q_lcb)
    cert.update(
        candidate_id=f"YES:{bin_id}:DIRECT_YES:{bin_id}@proof",
        bin_id=bin_id,
        route_id=f"DIRECT_YES:{bin_id}@proof",
        cost=price,
        edge_lcb=q_lcb - price,
        false_edge_rate=false_edge_rate,
        selection_guard_q_safe=q_lcb,
    )
    proof.qkernel_execution_economics = cert
    return proof


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


def _day0_submit_witness() -> PreSubmitAuthorityWitness:
    return PreSubmitAuthorityWitness(
        quote_seen_at="2026-07-02T02:18:08+00:00",
        book_hash="book-day0",
        current_best_bid=0.43,
        current_best_ask=0.44,
        tick_size=0.01,
        min_order_size=5.0,
        neg_risk=True,
        heartbeat_status="OK",
        user_ws_status="OK",
        venue_connectivity_status="OK",
        balance_allowance_status="OK",
        book_authority_id="clob_jit_book",
        book_captured_at="2026-07-02T02:18:08+00:00",
        heartbeat_authority_id="heartbeat",
        heartbeat_checked_at="2026-07-02T02:18:08+00:00",
        user_ws_authority_id="user_ws",
        user_ws_checked_at="2026-07-02T02:18:08+00:00",
        venue_connectivity_authority_id="venue",
        venue_connectivity_checked_at="2026-07-02T02:18:08+00:00",
        balance_allowance_authority_id="wallet",
        balance_allowance_checked_at="2026-07-02T02:18:08+00:00",
        checked_at="2026-07-02T02:18:08+00:00",
    )


def _day0_action_payload(*, bin_label: str) -> dict[str, object]:
    return {
        "event_type": "DAY0_EXTREME_UPDATED",
        "city": "Manila",
        "target_date": "2026-07-02",
        "metric": "high",
        "temperature_metric": "high",
        "direction": "buy_yes",
        "bin_label": bin_label,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }


def _day0_event_payload() -> SimpleNamespace:
    payload = {
        "city": "Manila",
        "target_date": "2026-07-02",
        "metric": "high",
        "station_id": "RPLL",
        "settlement_source": "aviationweather_metar",
        "observation_available_at": "2026-07-02T02:06:24+00:00",
        "rounded_value": 32,
    }
    return SimpleNamespace(
        event_id="event-day0-submit",
        event_type="DAY0_EXTREME_UPDATED",
        causal_snapshot_id="metar-fast",
        payload_json=json.dumps(payload),
        payload=payload,
    )


def test_day0_submit_gate_blocks_point_yes_one_bin_fragility() -> None:
    reason = _day0_live_submit_admission_rejection_reason(
        event=_day0_event_payload(),
        actionable_payload=_day0_action_payload(
            bin_label="Will the highest temperature in Manila be 32°C on July 2?"
        ),
        authority_witness=_day0_submit_witness(),
        order_mode="TAKER",
        decision_time=datetime(2026, 7, 2, 2, 17, tzinfo=timezone.utc),
    )
    assert reason == "DAY0_ONE_BIN_EDGE_FRAGILE"


def test_day0_submit_gate_blocks_taker_even_when_range_survives_stress() -> None:
    reason = _day0_live_submit_admission_rejection_reason(
        event=_day0_event_payload(),
        actionable_payload=_day0_action_payload(
            bin_label="Will the highest temperature in Manila be between 32-33°C on July 2?"
        ),
        authority_witness=_day0_submit_witness(),
        order_mode="TAKER",
        decision_time=datetime(2026, 7, 2, 2, 17, tzinfo=timezone.utc),
    )
    assert reason == "DAY0_TAKER_ENTRY_FORBIDDEN"


def test_day0_submit_gate_allows_maker_range_with_fresh_observation() -> None:
    reason = _day0_live_submit_admission_rejection_reason(
        event=_day0_event_payload(),
        actionable_payload=_day0_action_payload(
            bin_label="Will the highest temperature in Manila be between 32-33°C on July 2?"
        ),
        authority_witness=_day0_submit_witness(),
        order_mode="MAKER",
        decision_time=datetime(2026, 7, 2, 2, 17, tzinfo=timezone.utc),
    )
    assert reason is None


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
    family_row = conn.execute(
        "SELECT strategy_key FROM world.selection_family_fact"
    ).fetchone()
    assert family_row["strategy_key"] == "forecast_qkernel_entry"
    hypothesis_row = conn.execute(
        "SELECT meta_json FROM world.selection_hypothesis_fact"
    ).fetchone()
    assert json.loads(hypothesis_row["meta_json"])["strategy_key"] == "forecast_qkernel_entry"
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


def test_live_entry_qkernel_gate_accepts_low_cost_when_qkernel_cert_is_high_confidence():
    cert = _qkernel_cert()
    cert.update(cost=0.07, payoff_q_lcb=0.60, payoff_q_point=0.70, edge_lcb=0.53)

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


def test_live_entry_qkernel_gate_accepts_center_yes_below_binary_floor_when_quality_clear():
    cert = _qkernel_cert()
    cert.update(
        cost=0.12,
        payoff_q_lcb=0.30,
        payoff_q_point=0.36,
        edge_lcb=0.18,
        delta_u_at_min=0.01,
        optimal_stake_usd=10.0,
        optimal_delta_u=0.02,
        selection_guard_q_safe=0.30,
    )

    _assert_live_entry_submit_authority(
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "selection_authority_applied": "qkernel_spine",
            "direction": "buy_yes",
            "strategy_key": "center_buy",
            "candidate_bin_id": "bin-1",
            "q_live": 0.36,
            "q_lcb_5pct": 0.30,
            "min_entry_price": 0.02,
            "qkernel_execution_economics": cert,
        }
    )


def test_event_bound_strategy_key_treats_forecast_family_as_qkernel_entry():
    assert (
        _event_bound_strategy_key(
            event_type="FORECAST_SNAPSHOT_READY",
            direction="YES",
            metric="high",
        )
        == "forecast_qkernel_entry"
    )
    assert (
        _event_bound_strategy_key(
            event_type="FORECAST_SNAPSHOT_READY",
            direction="buy_no",
            metric="high",
        )
        == "forecast_qkernel_entry"
    )


def test_live_entry_qkernel_gate_rejects_buenos_aires_low_quality_yes():
    cert = _qkernel_cert()
    cert.update(
        cost=0.053828064525010946,
        payoff_q_lcb=0.0990451308919892,
        payoff_q_point=0.24833093804728934,
        edge_lcb=0.04521706636697825,
        selection_guard_q_safe=0.0990451308919892,
    )

    with pytest.raises(ValueError, match="ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "strategy_key": "forecast_qkernel_entry",
                "candidate_bin_id": "bin-1",
                "q_live": 0.24833093804728934,
                "q_lcb_5pct": 0.0990451308919892,
                "min_entry_price": 0.02,
                "qkernel_execution_economics": cert,
            }
        )


@pytest.mark.parametrize(("side", "direction"), (("YES", "buy_yes"), ("NO", "buy_no")))
def test_current_state_live_entry_uses_same_after_cost_rule_for_yes_and_no(side, direction):
    cert = _current_qkernel_cert(side=side)
    cert.update(
        cost=0.04,
        payoff_q_lcb=0.10,
        payoff_q_point=0.12,
        edge_lcb=0.06,
        selection_guard_q_safe=0.10,
    )
    _seal_current_qkernel_cert(cert)

    _assert_live_entry_submit_authority(
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "selection_authority_applied": "qkernel_spine",
            "direction": direction,
            "strategy_key": "forecast_qkernel_entry",
            "candidate_bin_id": "bin-1",
            "q_live": 0.12,
            "q_lcb_5pct": 0.10,
            "min_entry_price": 0.95,
            "qkernel_execution_economics": cert,
        }
    )


@pytest.mark.parametrize("missing_field", ("decision_id", "receipt_hash", "q_version", "sample_hash"))
def test_current_state_marker_requires_decision_and_posterior_identity(missing_field):
    cert = _current_qkernel_cert()
    cert.pop(missing_field)

    assert era._qkernel_current_state_solve_economics(cert) is False


def test_current_state_marker_rejects_unsealed_economics_mutation():
    cert = _current_qkernel_cert()

    cert["cost"] = 0.39

    assert era._qkernel_current_state_solve_economics(cert) is False


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_current_state_path_has_no_yes_only_near_day0_veto(side):
    cert = _current_qkernel_cert(side=side)
    cert["near_day0_raw_extrema_consistency"] = {
        "passed": False,
        "reason": "LEGACY_YES_ONLY_VETO",
    }

    assert era._qkernel_near_day0_cert_rejection_reason(cert) is None


@pytest.mark.parametrize(("side", "direction"), (("YES", "buy_yes"), ("NO", "buy_no")))
def test_current_state_actual_submit_has_no_side_or_fixed_profit_floor(side, direction):
    cert = _current_qkernel_cert(side=side)
    cert.update(
        cost=0.04,
        payoff_q_lcb=0.10,
        payoff_q_point=0.12,
        edge_lcb=0.06,
        optimal_stake_usd=0.01,
        selection_guard_q_safe=0.10,
    )
    _seal_current_qkernel_cert(cert)
    proof = SimpleNamespace(
        direction=direction,
        candidate=SimpleNamespace(metric="high"),
        qkernel_execution_economics=cert,
    )

    assert era._qkernel_actual_submit_quality_rejection_reason(
        proof=proof,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        actual_stake_usd=0.01,
        actual_cost=0.04,
    ) is None


@pytest.mark.parametrize(("side", "direction"), (("YES", "buy_yes"), ("NO", "buy_no")))
def test_current_state_actual_submit_must_remain_inside_certified_utility_envelope(
    side, direction
):
    cert = _current_qkernel_cert(side=side)
    cert.update(
        cost=0.40,
        payoff_q_lcb=0.60,
        payoff_q_point=0.70,
        edge_lcb=0.20,
        optimal_stake_usd=5.0,
        selection_guard_q_safe=0.60,
    )
    _seal_current_qkernel_cert(cert)
    proof = SimpleNamespace(
        direction=direction,
        candidate=SimpleNamespace(metric="high"),
        qkernel_execution_economics=cert,
    )

    assert era._qkernel_actual_submit_quality_rejection_reason(
        proof=proof,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        actual_stake_usd=2.5,
        actual_cost=0.39,
    ) is None
    assert "actual_cost_exceeds_certified_cost" in era._qkernel_actual_submit_quality_rejection_reason(
        proof=proof,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        actual_stake_usd=2.5,
        actual_cost=0.41,
    )
    assert "actual_stake_exceeds_certified_optimum" in era._qkernel_actual_submit_quality_rejection_reason(
        proof=proof,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        actual_stake_usd=5.01,
        actual_cost=0.39,
    )


def test_current_state_final_taker_spend_includes_fee_before_safe_prefix_check():
    cert = _current_qkernel_cert()
    cert.update(
        cost=0.50,
        payoff_q_lcb=0.60,
        payoff_q_point=0.70,
        edge_lcb=0.10,
        optimal_stake_usd=100.0,
        selection_guard_q_safe=0.60,
    )
    _seal_current_qkernel_cert(cert)
    intent = SimpleNamespace(
        payload={
            "limit_price": 0.48,
            "size": 100.0 / 0.48,
            "post_only": False,
        }
    )
    actual_cost = era._final_intent_worst_case_entry_cost(intent)
    actual_spend = era._final_intent_worst_case_entry_spend(intent)

    assert actual_cost < 0.50
    assert actual_spend > 100.0
    assert "actual_stake_exceeds_certified_optimum" in (
        era._qkernel_current_state_actual_submit_rejection_reason(
            cert=cert,
            actual_stake_usd=actual_spend,
            actual_cost=actual_cost,
        )
        or ""
    )


def test_qkernel_actual_submit_floor_uses_actual_stake_not_cert_optimal_size():
    cert = _qkernel_cert()
    cert.update(
        route_id="DIRECT_NO:bin-1@proof",
        candidate_id="NO:bin-1:DIRECT_NO:bin-1@proof",
        side="NO",
        payoff_q_point=0.8142,
        payoff_q_lcb=0.7043,
        cost=0.65733,
        edge_lcb=0.04697,
        optimal_stake_usd=154.0,
        optimal_delta_u=0.25,
        delta_u_at_min=0.01,
        selection_guard_q_safe=0.7043,
    )
    proof = SimpleNamespace(
        direction="buy_no",
        candidate=SimpleNamespace(metric="high"),
        qkernel_execution_economics=cert,
    )

    assert (
        era._qkernel_final_submit_floor_rejection_reason(
            proof=proof,
            cert=cert,
            strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        )
        is None
    )
    reason = era._qkernel_actual_submit_quality_rejection_reason(
        proof=proof,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        actual_stake_usd=6.23,
        actual_cost=0.65733,
    )

    assert reason is not None
    assert reason.startswith(
        "QKERNEL_ACTUAL_SUBMIT_QUALITY_FLOOR:actual_profit_below_strategy_floor:"
    )
    assert "strategy=forecast_qkernel_entry" in reason
    assert "floor=1.000000" in reason


def test_qkernel_actual_submit_floor_rejects_invalid_qkernel_evidence():
    cert = _qkernel_cert()
    cert.update(
        route_id="DIRECT_YES:bin-1@proof",
        candidate_id="YES:bin-1:DIRECT_YES:bin-1@proof",
        side="YES",
        payoff_q_point=0.24833093804728934,
        payoff_q_lcb=0.0990451308919892,
        cost=0.053828064525010946,
        edge_lcb=0.04521706636697825,
        optimal_stake_usd=23.69,
        optimal_delta_u=0.01,
        delta_u_at_min=0.0002,
        selection_guard_q_safe=0.0990451308919892,
    )
    proof = SimpleNamespace(
        direction="buy_yes",
        candidate=SimpleNamespace(metric="high"),
        qkernel_execution_economics=cert,
    )

    reason = era._qkernel_actual_submit_quality_rejection_reason(
        proof=proof,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        actual_stake_usd=5.46,
        actual_cost=0.053828064525010946,
    )

    assert reason is not None
    assert reason.startswith(
        "QKERNEL_ACTUAL_SUBMIT_QUALITY_FLOOR:"
        "QKERNEL_EXECUTION_ECONOMICS_ROI_FRONTIER_NOT_USEFUL:"
    )


def test_qkernel_selection_rejection_names_no_positive_edge_not_generic_invalid():
    cert = _qkernel_cert()
    cert.update(
        candidate_id="NO:bin-1:DIRECT_NO:bin-1@proof",
        route_id="DIRECT_NO:bin-1@proof",
        side="NO",
        payoff_q_point=0.88849,
        payoff_q_lcb=0.8053585,
        cost=0.98,
        edge_lcb=-0.1746415,
        delta_u_at_min=-0.0007298,
        optimal_delta_u=-0.0007298,
        optimal_stake_usd="0",
        selection_guard_q_safe=0.8053585,
    )

    reason = era._live_selection_rejection_reason(
        SimpleNamespace(
            direction="buy_no",
            q_lcb_5pct=0.8053585,
            qkernel_execution_economics=cert,
        ),
        strategy_policy_event_type="EDLI_REDECISION_PENDING",
        enforce_win_rate_floor=False,
    )

    assert reason is not None
    assert reason.startswith("QKERNEL_EDGE_LCB_NON_POSITIVE:")
    assert "payoff_q_lcb=0.805358" in reason
    assert "cost=0.980000" in reason
    assert "INVALID_FOR_SELECTION" not in reason


def test_near_day0_qkernel_consistency_rejects_raw_extrema_contradiction(monkeypatch):
    monkeypatch.setattr(
        era,
        "runtime_cities_by_name",
        lambda: {
            "Buenos Aires": SimpleNamespace(
                timezone="America/Argentina/Buenos_Aires",
                settlement_unit="C",
            )
        },
    )
    candidate = MarketTopologyCandidate(
        city="Buenos Aires",
        target_date="2026-07-02",
        metric="high",
        condition_id="ba-11c",
        yes_token_id="yes-ba-11c",
        no_token_id="no-ba-11c",
        bin=Bin(low=11, high=11, unit="C", label="11°C"),
    )
    bin_id = _candidate_bin_id_from_topology(candidate)
    cert = _qkernel_cert()
    cert.update(
        candidate_id=f"YES:{bin_id}:DIRECT_YES:{bin_id}@proof",
        route_id=f"DIRECT_YES:{bin_id}@proof",
        bin_id=bin_id,
        side="YES",
        cost=0.041,
        payoff_q_lcb=0.20,
        payoff_q_point=0.28,
        edge_lcb=0.159,
        selection_guard_q_safe=0.20,
    )

    annotated = _qkernel_economics_with_near_day0_consistency(
        {(bin_id, "YES"): cert},
        event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
        family=SimpleNamespace(
            city="Buenos Aires",
            target_date="2026-07-02",
            metric="high",
            candidates=(candidate,),
        ),
        payload={
            "_edli_spine_raw_members_native": [7.7, 7.8, 8.5],
            "_edli_spine_source_cycle_time_utc": "2026-07-01T12:00:00+00:00",
        },
        decision_time=datetime(2026, 7, 1, 22, 17, tzinfo=timezone.utc),
    )

    reason = _qkernel_near_day0_cert_rejection_reason(annotated[(bin_id, "YES")])
    assert reason is not None
    assert reason.startswith("ADMISSION_NEAR_DAY0_RAW_EXTREMA_CONTRADICTION")
    assert "raw_max=8.500" in reason
    assert "bin_low=11.000" in reason


def test_near_day0_qkernel_consistency_allows_supported_center_yes(monkeypatch):
    monkeypatch.setattr(
        era,
        "runtime_cities_by_name",
        lambda: {
            "Buenos Aires": SimpleNamespace(
                timezone="America/Argentina/Buenos_Aires",
                settlement_unit="C",
            )
        },
    )
    candidate = MarketTopologyCandidate(
        city="Buenos Aires",
        target_date="2026-07-02",
        metric="high",
        condition_id="ba-8c",
        yes_token_id="yes-ba-8c",
        no_token_id="no-ba-8c",
        bin=Bin(low=8, high=8, unit="C", label="8°C"),
    )
    bin_id = _candidate_bin_id_from_topology(candidate)
    cert = _qkernel_cert()
    cert.update(
        candidate_id=f"YES:{bin_id}:DIRECT_YES:{bin_id}@proof",
        route_id=f"DIRECT_YES:{bin_id}@proof",
        bin_id=bin_id,
        side="YES",
        cost=0.12,
        payoff_q_lcb=0.30,
        payoff_q_point=0.36,
        edge_lcb=0.18,
        selection_guard_q_safe=0.30,
    )

    annotated = _qkernel_economics_with_near_day0_consistency(
        {(bin_id, "YES"): cert},
        event=SimpleNamespace(event_type="FORECAST_SNAPSHOT_READY"),
        family=SimpleNamespace(
            city="Buenos Aires",
            target_date="2026-07-02",
            metric="high",
            candidates=(candidate,),
        ),
        payload={
            "_edli_spine_raw_members_native": [7.7, 7.8, 8.5],
            "_edli_spine_source_cycle_time_utc": "2026-07-01T12:00:00+00:00",
        },
        decision_time=datetime(2026, 7, 1, 22, 17, tzinfo=timezone.utc),
    )

    verdict = annotated[(bin_id, "YES")]["near_day0_raw_extrema_consistency"]
    assert verdict["passed"] is True
    assert _qkernel_near_day0_cert_rejection_reason(annotated[(bin_id, "YES")]) is None


def test_live_entry_qkernel_gate_rejects_failed_near_day0_consistency_verdict():
    cert = _qkernel_cert()
    cert["near_day0_raw_extrema_consistency"] = {
        "schema_version": 1,
        "passed": False,
        "reason": "ADMISSION_NEAR_DAY0_RAW_EXTREMA_CONTRADICTION:lead_hours=4.717",
    }

    with pytest.raises(ValueError, match="ADMISSION_NEAR_DAY0_RAW_EXTREMA_CONTRADICTION"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "candidate_bin_id": "bin-1",
                "q_live": 0.70,
                "q_lcb_5pct": 0.60,
                "strategy_key": "forecast_qkernel_entry",
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

    with pytest.raises(ValueError, match="ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "strategy_key": "forecast_qkernel_entry",
                "candidate_bin_id": "b34",
                "q_live": 0.12180248510788458,
                "q_lcb_5pct": 0.06052567908958011,
                "min_entry_price": 0.02,
                "qkernel_execution_economics": cert,
            }
        )


def test_live_entry_qkernel_gate_rejects_six_to_eight_cent_barely_positive_yes():
    cert = _qkernel_cert()
    cert.update(
        route_id="DIRECT_YES:b67@proof",
        candidate_id="YES:b67:DIRECT_YES:b67@proof",
        bin_id="b67",
        payoff_q_point=0.100000,
        payoff_q_lcb=0.078120,
        cost=0.067140,
        edge_lcb=0.010980,
        delta_u_at_min=0.000060,
        optimal_stake_usd=7.05,
        optimal_delta_u=0.000420,
        selection_guard_q_safe=0.078120,
    )

    with pytest.raises(ValueError, match="ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "strategy_key": "center_buy",
                "candidate_bin_id": "b67",
                "q_live": 0.100000,
                "q_lcb_5pct": 0.078120,
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


def test_live_entry_qkernel_gate_rejects_false_edge_rate_above_live_alpha():
    cert = _qkernel_cert()
    cert.update(false_edge_rate=0.50)

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
    cert.update(cost=0.07, payoff_q_lcb=0.60, payoff_q_point=0.70, edge_lcb=0.53)

    _assert_live_entry_submit_authority(
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "selection_authority_applied": "qkernel_spine",
            "direction": "buy_yes",
            "strategy_key": "center_buy",
            "candidate_bin_id": "bin-1",
            "q_live": 0.70,
            "q_lcb_5pct": 0.60,
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


def test_live_entry_day0_gate_accepts_live_observation_authority_with_qkernel():
    _assert_live_entry_submit_authority(
        _day0_payload(
            **_day0_probability_fields(),
            selection_authority_applied="qkernel_spine",
            direction="buy_yes",
            strategy_key="day0_nowcast_entry",
            candidate_bin_id="bin-1",
            min_entry_price=0.10,
            qkernel_execution_economics=_day0_qkernel_cert(),
        )
    )


def test_live_entry_day0_gate_accepts_degenerate_lcb_with_remaining_window_guard():
    q_live = 0.9541351747957598
    q_lcb = 0.9541351747957598
    cert = _day0_qkernel_cert(q_live=q_live, q_lcb=q_lcb)
    cert.update(selection_guard_q_safe=q_lcb)

    _assert_live_entry_submit_authority(
        _day0_payload(
            **_day0_probability_fields(q_live=q_live, q_lcb=q_lcb),
            selection_authority_applied="qkernel_spine",
            direction="buy_yes",
            strategy_key="day0_nowcast_entry",
            candidate_bin_id="bin-1",
            min_entry_price=0.10,
            qkernel_execution_economics=cert,
        )
    )


def test_live_entry_day0_gate_accepts_degenerate_lcb_with_oof_qkernel_guard():
    q_live = 0.9542497357620147
    q_lcb = 0.9542497290822666
    price = 0.8075023920658596
    cert = _day0_qkernel_cert(q_live=q_live, q_lcb=q_lcb)
    cert.update(
        cost=price,
        edge_lcb=q_lcb - price,
        false_edge_rate=0.05,
        optimal_stake_usd=383.9270934399719,
        optimal_delta_u=0.0536018706110991,
        delta_u_at_min=0.002361709922736971,
        q_lcb_guard_basis="OOF_WILSON_95_POOLED_TAIL",
        q_lcb_guard_cell_key="high|L1|YES|modal|qb19|coarse_global->tail_qb7+",
        selection_guard_basis="OOF_WILSON_95_POOLED_TAIL",
        selection_guard_cell_key="high|L1|YES|modal|qb19|coarse_global->tail_qb7+",
        selection_guard_q_safe=q_lcb,
    )

    _assert_live_entry_submit_authority(
        _day0_payload(
            **_day0_probability_fields(q_live=q_live, q_lcb=q_lcb),
            selection_authority_applied="qkernel_spine",
            direction="buy_yes",
            strategy_key="day0_nowcast_entry",
            candidate_bin_id="bin-1",
            min_entry_price=0.10,
            qkernel_execution_economics=cert,
        )
    )


def test_day0_fresh_submit_mode_remains_maker_even_when_policy_would_cross():
    mode = era._fresh_rest_then_cross_mode(
        actionable_payload=_day0_payload(
            direction="buy_yes",
            q_lcb_5pct=1.0,
            c_fee_adjusted=0.97,
            rest_then_cross_policy="TAKER_FLEETING_EDGE",
        ),
        executable_snapshot=SimpleNamespace(
            payload={"market_end_at": "2026-07-02T23:59:59+00:00"}
        ),
        fresh_best_bid=0.96,
        fresh_best_ask=0.97,
        tick_size=0.001,
        decision_time=datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc),
    )

    assert mode == "MAKER"


def test_day0_order_mode_remains_maker_even_with_taker_policy():
    mode = era._select_edli_order_mode(
        actionable_payload=_day0_payload(
            direction="buy_yes",
            rest_then_cross_policy="TAKER_FLEETING_EDGE",
            c_fee_adjusted=0.97,
        ),
        quote_payload={},
        best_bid=0.96,
        best_ask=0.97,
        executable_snapshot=SimpleNamespace(payload={}),
        fresh_best_bid=0.96,
        fresh_best_ask=0.97,
    )

    assert mode == "MAKER"


def test_live_entry_day0_gate_rejects_missing_qkernel_economics():
    with pytest.raises(ValueError, match="LIVE_ENTRY_QKERNEL_EXECUTION_ECONOMICS_REQUIRED"):
        _assert_live_entry_submit_authority(
            _day0_payload(
                **_day0_probability_fields(),
                selection_authority_applied="qkernel_spine",
                direction="buy_yes",
                strategy_key="day0_nowcast_entry",
                candidate_bin_id="bin-1",
                min_entry_price=0.10,
                qkernel_execution_economics=None,
            )
        )


def test_live_entry_day0_gate_rejects_missing_probability_authority():
    with pytest.raises(ValueError, match="LIVE_ENTRY_DAY0_PROBABILITY_AUTHORITY_REQUIRED"):
        _assert_live_entry_submit_authority(
            _day0_payload(
                selection_authority_applied="qkernel_spine",
                direction="buy_yes",
                strategy_key="day0_nowcast_entry",
                candidate_bin_id="bin-1",
                q_live=0.70,
                q_lcb_5pct=0.60,
                min_entry_price=0.10,
                qkernel_execution_economics=_day0_qkernel_cert(),
            )
        )


def test_live_entry_day0_gate_rejects_observed_boundary_qkernel_guard():
    cert = _day0_qkernel_cert()
    cert.update(
        q_lcb_guard_basis="DAY0_OBSERVED_BOUNDARY",
        q_lcb_guard_cell_key="day0_observed_boundary",
        selection_guard_basis="DAY0_OBSERVED_BOUNDARY",
        selection_guard_cell_key="day0_observed_boundary",
        selection_guard_n=1,
    )

    with pytest.raises(ValueError, match="LIVE_ENTRY_DAY0_QKERNEL_GUARD_AUTHORITY_REQUIRED"):
        _assert_live_entry_submit_authority(
            _day0_payload(
                **_day0_probability_fields(),
                selection_authority_applied="qkernel_spine",
                direction="buy_yes",
                strategy_key="day0_nowcast_entry",
                candidate_bin_id="bin-1",
                min_entry_price=0.10,
                qkernel_execution_economics=cert,
            )
        )


def test_live_entry_day0_gate_accepts_remaining_guard_without_oof_sample_count():
    cert = _day0_qkernel_cert()
    cert.update(selection_guard_n=0)

    _assert_live_entry_submit_authority(
        _day0_payload(
            **_day0_probability_fields(),
            selection_authority_applied="qkernel_spine",
            direction="buy_yes",
            strategy_key="day0_nowcast_entry",
            candidate_bin_id="bin-1",
            min_entry_price=0.10,
            qkernel_execution_economics=cert,
        )
    )


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


def test_day0_absorbing_hard_fact_route_fdr_passes_before_qkernel_false_edge():
    proof = SimpleNamespace(
        passed_prefilter=True,
        q_posterior=1.0,
        q_lcb_5pct=1.0,
        execution_price=SimpleNamespace(value=0.63),
        trade_score=0.348848,
        probability_authority="day0_absorbing_hard_fact",
        missing_reason=None,
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "false_edge_rate": 0.95,
        },
    )

    fdr = _day0_selected_route_fdr_proof(
        event_type="DAY0_EXTREME_UPDATED",
        family_id="Shanghai|2026-07-02|high",
        all_hypothesis_ids=tuple(f"h{i}" for i in range(22)),
        selected_hypothesis_id="h7",
        selected_proof=proof,
    )

    assert fdr is not None
    assert fdr.passed is True
    assert fdr.selected_post_fdr == ("h7",)


def test_day0_monotone_hard_fact_cert_can_dominate_served_proof_q():
    cert = _day0_qkernel_cert(q_live=1.0, q_lcb=1.0)
    cert.update(
        q_lcb_guard_cell_key="day0_monotone_hard_fact_q_lcb",
        selection_guard_cell_key="day0_monotone_hard_fact_q_lcb",
        q_dot_payoff=1.0,
    )

    assert (
        era._qkernel_cert_served_belief_rejection_reason(
            cert,
            proof_q_point=0.9090344934581372,
            proof_q_lcb=0.5,
        )
        is None
    )


def test_non_hard_fact_cert_still_rejects_served_proof_q_raise():
    cert = _day0_qkernel_cert(q_live=1.0, q_lcb=1.0)
    cert.update(q_dot_payoff=1.0)

    reason = era._qkernel_cert_served_belief_rejection_reason(
        cert,
        proof_q_point=0.9090344934581372,
        proof_q_lcb=0.5,
    )

    assert reason is not None
    assert reason.startswith("QKERNEL_SERVED_BELIEF_POINT_MISMATCH")


@pytest.mark.parametrize(
    "updates",
    [
        {
            "q_lcb_guard_cell_key": "day0_remaining_day_q_lcb",
            "selection_guard_basis": "OOF_WILSON_95",
            "selection_guard_cell_key": "day0_monotone_hard_fact_q_lcb",
        },
        {
            "q_lcb_guard_basis": "OOF_WILSON_95",
            "q_lcb_guard_cell_key": "day0_monotone_hard_fact_q_lcb",
            "selection_guard_cell_key": "day0_remaining_day_q_lcb",
        },
        {
            "q_lcb_guard_cell_key": "day0_monotone_hard_fact_q_lcb",
            "selection_guard_cell_key": "day0_monotone_hard_fact_q_lcb",
            "selection_guard_abstained": None,
        },
    ],
)
def test_day0_hard_fact_cert_requires_paired_explicit_guard_fields(updates):
    cert = _day0_qkernel_cert(q_live=1.0, q_lcb=1.0)
    cert.update(
        q_lcb_guard_cell_key="day0_monotone_hard_fact_q_lcb",
        selection_guard_cell_key="day0_monotone_hard_fact_q_lcb",
        q_dot_payoff=1.0,
    )
    cert.update(updates)

    reason = era._qkernel_cert_served_belief_rejection_reason(
        cert,
        proof_q_point=0.9090344934581372,
        proof_q_lcb=0.5,
    )

    assert reason is not None
    assert reason.startswith("QKERNEL_SERVED_BELIEF_POINT_MISMATCH")


def test_day0_route_fdr_uses_qkernel_empirical_false_edge_when_present():
    proof = _bound_day0_qkernel_route_proof(
        q_live=0.8732666666666666,
        q_lcb=0.6666666666666667,
        price=0.41,
        trade_score=0.23428719523121821,
        false_edge_rate=0.05,
    )

    fdr = _day0_selected_route_fdr_proof(
        event_type="DAY0_EXTREME_UPDATED",
        family_id="Milan|2026-07-04|high",
        all_hypothesis_ids=tuple(f"h{i}" for i in range(22)),
        selected_hypothesis_id="h7",
        selected_proof=proof,
    )
    reason = _fdr_rejection_reason(
        event_type="DAY0_EXTREME_UPDATED",
        fdr=SimpleNamespace(attempted_hypotheses=22, selected_post_fdr=()),
        selected_proof=proof,
    )

    assert fdr is not None
    assert fdr.passed is True
    assert fdr.selected_post_fdr == ("h7",)
    assert "day0_false_edge_rate=0.050000" in reason
    assert "day0_false_edge_source=qkernel_route_false_edge_rate" in reason


def test_day0_route_fdr_ignores_unbound_qkernel_false_edge_rate():
    proof = SimpleNamespace(
        passed_prefilter=True,
        q_posterior=0.8732666666666666,
        q_lcb_5pct=0.6666666666666667,
        execution_price=SimpleNamespace(value=0.41),
        trade_score=0.23428719523121821,
        probability_authority="day0_absorbing_hard_fact",
        missing_reason=None,
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "false_edge_rate": 0.01,
        },
    )

    fdr = _day0_selected_route_fdr_proof(
        event_type="DAY0_EXTREME_UPDATED",
        family_id="Milan|2026-07-04|high",
        all_hypothesis_ids=tuple(f"h{i}" for i in range(22)),
        selected_hypothesis_id="h7",
        selected_proof=proof,
    )
    reason = _fdr_rejection_reason(
        event_type="DAY0_EXTREME_UPDATED",
        fdr=SimpleNamespace(attempted_hypotheses=22, selected_post_fdr=()),
        selected_proof=proof,
    )

    assert fdr is not None
    assert fdr.passed is False
    assert fdr.selected_post_fdr == ()
    assert "day0_false_edge_rate=0.333333" in reason
    assert "day0_false_edge_source=q_lcb_complement" in reason


def test_day0_route_fdr_keeps_q_lcb_complement_when_qkernel_rate_is_weaker():
    proof = _bound_day0_qkernel_route_proof(
        q_live=1.0,
        q_lcb=1.0,
        price=0.63,
        trade_score=0.348848,
        false_edge_rate=0.95,
    )

    reason = _fdr_rejection_reason(
        event_type="DAY0_EXTREME_UPDATED",
        fdr=SimpleNamespace(attempted_hypotheses=22, selected_post_fdr=()),
        selected_proof=proof,
    )

    assert "day0_false_edge_rate=0.000000" in reason
    assert "day0_false_edge_source=q_lcb_complement" in reason


def test_day0_pre_submit_payload_preserves_observation_authority_and_qkernel():
    qkernel_cert = _qkernel_cert()
    final_intent = SimpleNamespace(
        certificate_hash="final-hash",
        payload={
            "event_id": "event-1",
            "event_type": "DAY0_EXTREME_UPDATED",
            "final_intent_id": "intent-1",
            "strategy_key": "day0_nowcast_entry",
            "condition_id": "condition-1",
            "token_id": "token-yes",
            "side": "BUY",
            "direction": "buy_yes",
            "city": "Chicago",
            "target_date": "2026-05-24",
            "metric": "high",
            "temperature_metric": "high",
            "bin_label": "80F",
            "outcome_label": "Yes",
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
            "selection_authority_applied": "qkernel_spine",
            "qkernel_execution_economics": qkernel_cert,
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
    assert payload["selection_authority_applied"] == "qkernel_spine"
    assert payload["qkernel_execution_economics"] == qkernel_cert
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


def test_day0_final_intent_source_context_binds_observation_and_base_forecast():
    decision_time = datetime(2026, 7, 1, 21, tzinfo=timezone.utc)
    forecast = build_certificate(
        certificate_type=claims.FORECAST_AUTHORITY,
        semantic_key="forecast:day0-base",
        claim_type=claims.FORECAST_AUTHORITY,
        mode="LIVE",
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload={
            "source_id": "replacement_raw_second_moment",
            "forecast_source_id": "replacement_raw_second_moment",
            "model_family": "replacement_raw_second_moment",
            "forecast_issue_time": "2026-07-01T06:00:00+00:00",
            "forecast_fetch_time": "2026-07-01T06:20:00+00:00",
            "forecast_available_at": "2026-07-01T06:20:00+00:00",
            "raw_payload_hash": "b" * 64,
            "posterior_identity_hash": "qv-day0-base-001",
            "degradation_level": "OK",
            "forecast_source_role": "day0_base_distribution",
            "authority_tier": "FORECAST",
            "decision_time": decision_time.isoformat(),
            "decision_time_status": "OK",
            "polymarket_end_anchor_source": "gamma_explicit",
            "zeus_submit_intent_time": "2026-07-01T21:00:01+00:00",
            "venue_ack_time": "2026-07-01T21:00:02+00:00",
        },
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    day0 = build_certificate(
        certificate_type=claims.DAY0_AUTHORITY,
        semantic_key="day0:obs",
        claim_type=claims.DAY0_AUTHORITY,
        mode="LIVE",
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload={
            "city": "Chicago",
            "target_date": "2026-07-01",
            "metric": "high",
            "station_id": "KORD",
            "observation_time": "2026-07-01T20:51:00+00:00",
            "observation_available_at": "2026-07-01T20:55:56+00:00",
        },
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )
    absorbing = build_certificate(
        certificate_type=claims.ABSORBING_BOUNDARY,
        semantic_key="day0:absorbing",
        claim_type=claims.ABSORBING_BOUNDARY,
        mode="LIVE",
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload={"boundary": "day0_absorbing_hard_fact"},
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )

    payload = _final_intent_decision_source_context_payload(
        event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
        forecast_authority=forecast,
        day0_source_certs=(day0, absorbing),
    )
    ctx = DecisionSourceContext.from_forecast_context(payload)

    assert payload["forecast_source_role"] == "day0_live_observation"
    assert payload["authority_tier"] == "OBSERVATION"
    assert payload["raw_payload_hash"] != forecast.payload["raw_payload_hash"]
    assert payload["posterior_identity_hash"] == payload["raw_payload_hash"]
    assert payload["base_posterior_identity_hash"] == "qv-day0-base-001"
    assert payload["day0_authority_certificate_hash"] == day0.certificate_hash
    assert ctx is not None
    assert ctx.posterior_identity_hash == payload["raw_payload_hash"]
    assert ctx.integrity_errors() == ()


def test_replacement_forecast_authority_binds_selected_proof_posterior_id(monkeypatch):
    """Replacement forecast authority must match the proof's served posterior row."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            product_id TEXT,
            source_id TEXT,
            data_version TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            posterior_identity_hash TEXT
        )
        """
    )
    # Same source cycle and q vector, two materializations. The unbound query would
    # pick the newer computed_at row; live final-intent authority must instead bind
    # to the posterior_id that produced the selected proof.
    conn.executemany(
        """
        INSERT INTO forecast_posteriors (
            posterior_id, product_id, source_id, data_version, city, target_date,
            temperature_metric, source_cycle_time, source_available_at, computed_at,
            posterior_identity_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                41,
                "openmeteo_ecmwf_ifs9_bayes_fusion_v1",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                "openmeteo_ecmwf_ifs9_bayes_fusion_high_v1",
                "Seoul",
                "2026-07-10",
                "high",
                "2026-07-08T06:00:00+00:00",
                "2026-07-08T12:31:30+00:00",
                "2026-07-08T12:49:13+00:00",
                "f" * 64,
            ),
            (
                42,
                "openmeteo_ecmwf_ifs9_bayes_fusion_v1",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                "openmeteo_ecmwf_ifs9_bayes_fusion_high_v1",
                "Seoul",
                "2026-07-10",
                "high",
                "2026-07-08T06:00:00+00:00",
                "2026-07-08T12:31:30+00:00",
                "2026-07-08T12:38:00+00:00",
                "a" * 64,
            ),
        ],
    )

    monkeypatch.setattr(era, "_replacement_authority_enabled", lambda: True)
    monkeypatch.setattr(
        era,
        "runtime_cities_by_name",
        lambda: {"Seoul": SimpleNamespace(timezone="Asia/Seoul", settlement_unit="C")},
    )
    monkeypatch.setattr(
        era,
        "_spine_multimodel_members_for_event",
        lambda *_args, **_kwargs: ((25.0, 26.0, 27.0), None, None),
    )
    monkeypatch.setattr(era, "_replacement_live_input_lag_reason", lambda *_args, **_kwargs: None)

    payload, _clock = era._forecast_authority_payload_and_clock(
        conn,
        event=SimpleNamespace(
            event_type="FORECAST_SNAPSHOT_READY",
            causal_snapshot_id="rmf-Seoul|2026-07-10|high|2026-07-08",
        ),
        family=SimpleNamespace(city="Seoul", target_date="2026-07-10", metric="high"),
        payload={},
        decision_time=datetime(2026, 7, 8, 17, 7, 14, tzinfo=timezone.utc),
        bound_posterior_id=42,
    )

    assert payload["posterior_identity_hash"] == "a" * 64
    assert payload["raw_payload_hash"] == "a" * 64
    assert payload["captured_at"] == "2026-07-08T12:38:00+00:00"
