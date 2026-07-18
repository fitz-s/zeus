# Created: 2026-06-30
# Last reused/audited: 2026-07-16
# Authority basis: live-money qkernel submit authority and canonical selection-fact persistence.

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
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
from src.events.reactor import EventSubmissionReceipt
from src.contracts.execution_intent import DecisionSourceContext
from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
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


def _global_decision(
    *,
    shares: str,
    cost: str,
    q: str,
    candidate=None,
    wealth: str = "1000",
):
    shares_decimal = Decimal(shares)
    cost_decimal = Decimal(cost)
    q_decimal = Decimal(q)
    robust_ev = q_decimal * shares_decimal - cost_decimal
    wealth_decimal = Decimal(wealth)
    win_payoff = shares_decimal - cost_decimal
    loss_payoff = -cost_decimal
    terminal = SimpleNamespace(
        win_probability_lcb=float(q_decimal),
        loss_probability_ucb=float(Decimal("1") - q_decimal),
        loss_payoff_usd=loss_payoff,
        win_payoff_usd=win_payoff,
        median_payoff_usd=(
            win_payoff if q_decimal > Decimal("0.5") else loss_payoff
        ),
        wealth_after_loss_usd=wealth_decimal - cost_decimal,
        wealth_after_win_usd=wealth_decimal + shares_decimal - cost_decimal,
        expected_value_diagnostic_usd=float(robust_ev),
    )
    return SimpleNamespace(
        candidate=candidate,
        shares=shares_decimal,
        cost_usd=cost_decimal,
        robust_ev_usd=robust_ev,
        terminal_wealth=terminal,
    )


def _seal_current_qkernel_cert(cert: dict) -> None:
    cert["current_state_identity_hash"] = era.qkernel_current_state_identity_hash(cert)


def _global_current_qkernel_cert(*, side: str = "YES") -> dict:
    cert = _current_qkernel_cert(side=side)
    for field in (
        "candidate_id",
        "bin_id",
        "route_id",
        "delta_u_at_min",
        "optimal_stake_usd",
        "optimal_delta_u",
        "direction_law_ok",
        "coherence_allows",
    ):
        cert.pop(field)
    cert.update(
        payoff_q_point=0.70,
        payoff_q_lcb=0.60,
        cost=0.05,
        edge_lcb=0.55,
        global_actuation_identity="global-actuation-1",
        global_optimum_semantics="CUT_TIME_GLOBAL_OPTIMUM",
        global_candidate_id="global-candidate-1",
        global_bin_id="bin-1",
        global_universe_witness_identity="global-universe-1",
        global_wealth_witness_identity="global-wealth-1",
        global_selection_epoch_identity="global-epoch-1",
        global_selection_cut_at="2026-07-11T23:00:00+00:00",
        global_selection_decision_at="2026-07-11T23:00:01+00:00",
        global_jit_book_hash="jit-book-1",
        global_jit_venue_book_hash="jit-venue-book-1",
        global_jit_book_snapshot_id="jit-snapshot-1",
        global_jit_execution_curve_identity="jit-curve-1",
        global_target_shares="20",
        global_expected_cost_usd="1",
        global_max_spend_usd="1",
        global_robust_delta_log_wealth=0.01,
        global_robust_ev_usd=11.0,
        global_cut_time_win_probability_lcb=0.60,
        global_cut_time_loss_probability_ucb=0.40,
        global_terminal_win_probability_lcb=0.60,
        global_terminal_loss_probability_ucb=0.40,
        global_terminal_loss_payoff_usd="-1",
        global_terminal_win_payoff_usd="19",
        global_terminal_median_payoff_usd="19",
        global_terminal_wealth_after_loss_usd="99",
        global_terminal_wealth_after_win_usd="119",
        global_cut_time_expected_value_diagnostic_usd=11.0,
        global_expected_value_diagnostic_usd=11.0,
        global_expected_value_semantics="DIAGNOSTIC_EXPECTATION_NOT_REALIZED_GAIN",
        global_terminal_payoff_semantics="BINARY_0_1",
    )
    _seal_current_qkernel_cert(cert)
    return cert


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


def _day0_action_payload(*, bin_label: str, direction: str = "buy_yes") -> dict[str, object]:
    return {
        "event_type": "DAY0_EXTREME_UPDATED",
        "city": "Manila",
        "target_date": "2026-07-02",
        "metric": "high",
        "temperature_metric": "high",
        "direction": direction,
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


def test_day0_submit_gate_allows_sealed_global_current_point_taker() -> None:
    payload = _day0_action_payload(
        bin_label="Will the highest temperature in Manila be 32°C on July 2?"
    )
    payload["qkernel_execution_economics"] = _global_current_qkernel_cert()

    reason = _day0_live_submit_admission_rejection_reason(
        event=_day0_event_payload(),
        actionable_payload=payload,
        authority_witness=_day0_submit_witness(),
        order_mode="TAKER",
        decision_time=datetime(2026, 7, 2, 2, 17, tzinfo=timezone.utc),
    )

    assert reason is None


def test_day0_submit_gate_malformed_global_cert_cannot_bypass_fragility() -> None:
    cert = _global_current_qkernel_cert()
    cert["global_bin_id"] = "mutated-bin"
    payload = _day0_action_payload(
        bin_label="Will the highest temperature in Manila be 32°C on July 2?"
    )
    payload["qkernel_execution_economics"] = cert

    reason = _day0_live_submit_admission_rejection_reason(
        event=_day0_event_payload(),
        actionable_payload=payload,
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


@pytest.mark.parametrize(
    ("metric", "bin_label", "observed", "yes_survives"),
    (
        ("high", "32°C", 31, True),
        ("high", "32-33°C", 33, False),
        ("high", "32°C or below", 32, False),
        ("high", "32°C or higher", 31, True),
        ("low", "32°C", 33, True),
        ("low", "32-33°C", 32, False),
        ("low", "32°C or below", 33, True),
        ("low", "32°C or higher", 32, False),
    ),
)
def test_day0_one_bin_stress_is_payoff_complement_symmetric(
    metric: str,
    bin_label: str,
    observed: float,
    yes_survives: bool,
) -> None:
    common = {
        "metric": metric,
        "temperature_metric": metric,
        "bin_label": bin_label,
        "rounded_value": observed,
    }
    _, yes_result = era._day0_bin_stress_verdict(
        actionable_payload={**common, "direction": "buy_yes"},
        event_payload={},
    )
    _, no_result = era._day0_bin_stress_verdict(
        actionable_payload={**common, "direction": "buy_no"},
        event_payload={},
    )

    assert yes_result is yes_survives
    assert no_result is (not yes_survives)


def test_day0_submit_gate_blocks_no_when_one_bin_stress_enters_point_bin() -> None:
    event = _day0_event_payload()
    event.payload["rounded_value"] = 31
    event.payload_json = json.dumps(event.payload)

    reason = _day0_live_submit_admission_rejection_reason(
        event=event,
        actionable_payload=_day0_action_payload(
            bin_label="Will the highest temperature in Manila be 32°C on July 2?",
            direction="buy_no",
        ),
        authority_witness=_day0_submit_witness(),
        order_mode="MAKER",
        decision_time=datetime(2026, 7, 2, 2, 17, tzinfo=timezone.utc),
    )

    assert reason == "DAY0_ONE_BIN_EDGE_FRAGILE"


@pytest.mark.parametrize("direction", ("buy_yes", "buy_no"))
def test_day0_one_bin_stress_fails_closed_when_bin_is_unparseable(direction: str) -> None:
    distance, survives = era._day0_bin_stress_verdict(
        actionable_payload={
            "direction": direction,
            "metric": "high",
            "bin_label": "not a settlement bin",
            "rounded_value": 31,
        },
        event_payload={},
    )

    assert distance == 0.0
    assert survives is False


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


def test_live_entry_qkernel_gate_accepts_center_yes_when_symmetric_quality_floor_clear():
    cert = _qkernel_cert()
    cert.update(
        cost=0.12,
        payoff_q_lcb=0.52,
        payoff_q_point=0.60,
        edge_lcb=0.40,
        delta_u_at_min=0.01,
        optimal_stake_usd=10.0,
        optimal_delta_u=0.02,
        selection_guard_q_safe=0.52,
    )

    _assert_live_entry_submit_authority(
        {
            "event_type": "FORECAST_SNAPSHOT_READY",
            "selection_authority_applied": "qkernel_spine",
            "direction": "buy_yes",
            "strategy_key": "center_buy",
            "candidate_bin_id": "bin-1",
            "q_live": 0.60,
            "q_lcb_5pct": 0.52,
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


def test_live_entry_qkernel_gate_accepts_underpriced_buenos_aires_yes():
    cert = _qkernel_cert()
    cert.update(
        cost=0.053828064525010946,
        payoff_q_lcb=0.0990451308919892,
        payoff_q_point=0.24833093804728934,
        edge_lcb=0.04521706636697825,
        selection_guard_q_safe=0.0990451308919892,
    )

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
def test_current_state_live_entry_uses_robust_utility_not_legacy_strategy_floor(
    side, direction
):
    cert = _current_qkernel_cert(side=side)
    cert.update(
        cost=0.05,
        payoff_q_lcb=0.11,
        payoff_q_point=0.12,
        edge_lcb=0.06,
        selection_guard_q_safe=0.11,
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
                "q_lcb_5pct": 0.11,
            "min_entry_price": 0.95,
            "qkernel_execution_economics": cert,
        }
    )


@pytest.mark.parametrize("missing_field", ("decision_id", "receipt_hash", "q_version", "sample_hash"))
def test_current_state_marker_requires_decision_and_posterior_identity(missing_field):
    cert = _current_qkernel_cert()
    cert.pop(missing_field)

    assert era._qkernel_current_state_solve_economics(cert) is False
    assert (
        era._qkernel_current_state_solve_economics_rejection_reason(cert)
        == missing_field
    )


def test_current_state_marker_rejects_unsealed_economics_mutation():
    cert = _current_qkernel_cert()

    cert["cost"] = 0.39
    cert["edge_lcb"] = 0.21

    assert era._qkernel_current_state_solve_economics(cert) is False
    assert era._valid_qkernel_execution_economics_payload(cert, direction="buy_yes") is None


@pytest.mark.parametrize(("side", "direction"), (("YES", "buy_yes"), ("NO", "buy_no")))
def test_global_actuation_rebinds_submit_gate_to_exact_current_band(
    monkeypatch,
    side,
    direction,
):
    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=0.7801526877016629,
        payoff_q_lcb=0.7271700502061007,
        pre_qkernel_q_lcb_5pct=0.7271700502061007,
        cost=0.6087637988435255,
        edge_lcb=0.11840625136257521,
        route_cost=0.55,
        route_edge_lcb=0.17717005020610066,
    )
    decision = _global_decision(
        shares="158.25",
        cost="91.3482",
        q="0.7271700502061007",
    )
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-sample",
        yes_q_samples=SimpleNamespace(shape=(400, 11)),
        band_alpha=0.05,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )
    current_band_q = decision.terminal_wealth.win_probability_lcb

    assert current["global_current_band_payoff_q_lcb"] == pytest.approx(current_band_q)
    assert current["payoff_q_lcb"] == pytest.approx(0.7271700502061007)
    assert current["global_current_effective_payoff_q_lcb"] == pytest.approx(
        0.7271700502061007
    )
    assert current["q_lcb_guard_basis"] == "CURRENT_POSTERIOR_BAND"
    assert current["selection_guard_basis"] == "CURRENT_POSTERIOR_BAND"
    assert current["sample_hash"] == witness.sample_matrix_identity
    assert era._qkernel_current_state_solve_economics(current) is True

    def legacy_selection_curse_must_not_run(**_kwargs):
        raise AssertionError("global current-band certificate was downgraded")

    monkeypatch.setattr(
        era,
        "_event_bound_q_exec_lcb",
        legacy_selection_curse_must_not_run,
    )
    proof = era._build_event_bound_taker_quality_proof(
        actionable_payload={
            "direction": direction,
            "selection_authority_applied": "qkernel_spine",
            "candidate_bin_id": "bin-1",
            "q_live": current["payoff_q_point"],
            "q_lcb_5pct": current["payoff_q_lcb"],
            "live_cap_reserved_notional_usd": "107.61",
            "qkernel_execution_economics": current,
        },
        order_mode="TAKER",
        fresh_best_bid=0.54,
        fresh_best_ask=0.55,
    )
    assert proof is not None and proof["passed"] is True
    assert proof["q_exec_lcb_basis"] == "CURRENT_POSTERIOR_BAND"


@pytest.mark.parametrize(("side", "direction"), (("YES", "buy_yes"), ("NO", "buy_no")))
def test_low_probability_current_band_taker_is_symmetric_positive_growth(
    side,
    direction,
):
    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=0.999,
        payoff_q_lcb=0.13,
        pre_qkernel_q_lcb_5pct=0.13,
        cost=0.10,
        edge_lcb=0.03,
        route_cost=0.10,
        route_edge_lcb=0.03,
        selection_guard_q_safe=0.13,
    )
    _seal_current_qkernel_cert(cert)
    decision = _global_decision(shares="100", cost="10", q="0.13")
    witness = SimpleNamespace(
        sample_matrix_identity=f"current-sample-{side.lower()}",
        yes_q_samples=SimpleNamespace(shape=(400, 11)),
        band_alpha=0.05,
    )
    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )
    assert current["payoff_q_lcb"] == pytest.approx(0.13)
    assert current["edge_lcb"] == pytest.approx(0.03)
    assert era._qkernel_current_state_solve_economics(current) is True


def test_deterministic_day0_witness_rejects_certificate_probability_drift():
    from src.solve.solver import (
        DeterministicBinPayoffWitness,
        OutcomeTokenBinding,
        deterministic_bin_payoff_witness_identity,
    )

    captured_at = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    fields = {
        "family_key": "day0-family",
        "bindings": (
            OutcomeTokenBinding(
                bin_id="dead-bin",
                condition_id="condition",
                yes_token_id="yes",
                no_token_id="no",
            ),
            OutcomeTokenBinding(
                bin_id="unknown-bin",
                condition_id="other-condition",
                yes_token_id="other-yes",
                no_token_id="other-no",
            ),
        ),
        "exact_yes_payoffs": (("dead-bin", 0),),
        "q_version": "day0-q",
        "resolution_identity": "resolution",
        "topology_identity": "topology",
        "posterior_identity_hash": "day0-state",
        "source_truth_identity": "observation",
        "authority_certificate_hash": "certificate",
        "band_alpha": 0.05,
        "band_basis": "day0_deterministic_bin_payoff_v1",
        "captured_at_utc": captured_at,
    }
    witness = DeterministicBinPayoffWitness(
        **fields,
        max_age=timedelta(seconds=1),
        witness_identity=deterministic_bin_payoff_witness_identity(**fields),
    )
    candidate = SimpleNamespace(side="NO", bin_id="dead-bin")
    decision = _global_decision(
        shares="10",
        cost="2",
        q="1",
        candidate=candidate,
    )
    cert = _current_qkernel_cert(side="NO")
    cert.update(
        payoff_q_point=1.0,
        payoff_q_lcb=1.0,
        pre_qkernel_q_lcb_5pct=1.0,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )
    assert current["payoff_q_point"] == pytest.approx(1.0)
    assert current["false_edge_rate"] == pytest.approx(0.0)

    cert["payoff_q_point"] = 0.99
    with pytest.raises(ValueError, match="GLOBAL_CURRENT_STATE_POINT_Q_INVALID"):
        era._global_current_state_execution_economics(
            cert,
            decision=decision,
            witness=witness,
        )


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_current_entry_price_policy_is_native_side_symmetric(side):
    def candidate(*, action="BUY", price="0.10"):
        return SimpleNamespace(
            action=action,
            side=side,
            executable_cost_curve=SimpleNamespace(
                levels=(SimpleNamespace(price=Decimal(price)),)
            ),
        )

    reason = era._global_current_entry_price_policy_rejection_reason(
        candidate(price="0.004"),
        strategy_key="forecast_qkernel_entry",
    )

    assert reason == (
        "GLOBAL_ENTRY_PRICE_BELOW_STRATEGY_FLOOR:"
        f"strategy=forecast_qkernel_entry:side={side}:best_ask=0.004:floor=0.1"
    )
    assert (
        era._global_current_entry_price_policy_rejection_reason(
            candidate(price="0.10"),
            strategy_key="forecast_qkernel_entry",
        )
        is None
    )
    assert (
        era._global_current_entry_price_policy_rejection_reason(
            candidate(action="SELL", price="0.004"),
            strategy_key="forecast_qkernel_entry",
        )
        is None
    )


def test_global_current_band_rejects_terminal_certificate_incoherent_with_its_branch():
    """A sub-0.5 certificate must put its median on the loss branch."""

    cert = _current_qkernel_cert(side="YES")
    cert.update(
        payoff_q_point=0.999,
        payoff_q_lcb=0.13,
        pre_qkernel_q_lcb_5pct=0.13,
        cost=0.10,
        edge_lcb=0.03,
        selection_guard_q_safe=0.13,
    )
    _seal_current_qkernel_cert(cert)
    shares = Decimal("100")
    cost = Decimal("10")
    win_payoff = shares - cost
    terminal = SimpleNamespace(
        win_probability_lcb=0.13,
        loss_probability_ucb=0.87,
        loss_payoff_usd=-cost,
        win_payoff_usd=win_payoff,
        median_payoff_usd=win_payoff,
        wealth_after_loss_usd=Decimal("1000") - cost,
        wealth_after_win_usd=Decimal("1000") + win_payoff,
        expected_value_diagnostic_usd=float(Decimal("0.13") * shares - cost),
    )
    decision = SimpleNamespace(
        candidate=None,
        shares=shares,
        cost_usd=cost,
        robust_ev_usd=Decimal("0.13") * shares - cost,
        terminal_wealth=terminal,
    )
    witness = SimpleNamespace(
        sample_matrix_identity="current-sample-incoherent",
        yes_q_samples=SimpleNamespace(shape=(400, 11)),
        band_alpha=0.05,
    )

    with pytest.raises(
        ValueError,
        match="GLOBAL_CURRENT_STATE_TERMINAL_CERTIFICATE_INCOHERENT",
    ):
        era._global_current_state_execution_economics(
            cert,
            decision=decision,
            witness=witness,
        )


@pytest.mark.parametrize(("side", "direction"), (("YES", "buy_yes"), ("NO", "buy_no")))
def test_global_current_submit_does_not_require_legacy_route_optimizer_fields(
    side,
    direction,
):
    cert = _global_current_qkernel_cert(side=side)
    proof = SimpleNamespace(
        direction=direction,
        candidate=SimpleNamespace(metric="high"),
        qkernel_execution_economics=cert,
    )

    assert era._qkernel_actual_submit_quality_rejection_reason(
        proof=proof,
        strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
        actual_stake_usd=1.0,
        actual_cost=0.05,
    ) is None
    assert (
        era._qkernel_actual_submit_quality_rejection_reason(
            proof=proof,
            strategy_policy_event_type="FORECAST_SNAPSHOT_READY",
            actual_stake_usd=1.0,
            actual_cost=0.06,
        )
        == "GLOBAL_ACTUATION_EXPECTED_COST_EXCEEDED"
    )


@pytest.mark.parametrize(("side", "direction"), (("YES", "buy_yes"), ("NO", "buy_no")))
def test_global_current_certificate_is_selectable_without_legacy_route_fields(
    side,
    direction,
):
    cert = _global_current_qkernel_cert(side=side)
    proof = SimpleNamespace(
        direction=direction,
        q_lcb_5pct=cert["payoff_q_lcb"],
        q_source="qkernel_spine",
        selection_authority_applied="qkernel_spine",
        qkernel_execution_economics=cert,
    )

    assert (
        era._valid_selected_qkernel_execution_economics_payload(
            cert,
            direction=direction,
        )
        is cert
    )
    assert (
        era._live_selection_rejection_reason(
            proof,
            enforce_win_rate_floor=False,
        )
        is None
    )


def test_global_current_certificate_fails_closed_on_side_or_envelope_mismatch():
    side_mismatch = _global_current_qkernel_cert(side="NO")
    assert (
        era._global_current_state_execution_economics_rejection_reason(
            side_mismatch,
            direction="buy_yes",
        )
        == "side_direction_mismatch"
    )

    broken_envelope = _global_current_qkernel_cert()
    broken_envelope["global_robust_ev_usd"] = 0.0
    _seal_current_qkernel_cert(broken_envelope)
    assert (
        era._valid_selected_qkernel_execution_economics_payload(
            broken_envelope,
            direction="buy_yes",
        )
        is None
    )


@pytest.mark.parametrize(("side", "direction"), (("YES", "buy_yes"), ("NO", "buy_no")))
def test_global_current_certificate_accepts_live_complement_rounding(
    side,
    direction,
):
    cert = _global_current_qkernel_cert(side=side)
    q_lcb = 0.8344915302118994
    cost = 0.63
    shares = 13.0
    expected_cost = shares * cost
    win_payoff = shares - expected_cost
    cert.update(
        payoff_q_point=0.979320785,
        q_dot_payoff=0.979320785,
        payoff_q_lcb=q_lcb,
        cost=cost,
        edge_lcb=q_lcb - cost,
        global_target_shares=shares,
        global_expected_cost_usd=expected_cost,
        global_max_spend_usd=expected_cost,
        global_robust_ev_usd=q_lcb * shares - expected_cost,
        global_cut_time_win_probability_lcb=q_lcb,
        # The solver and certificate complement paths can land on adjacent
        # binary64 values while representing the same exact probability.
        global_cut_time_loss_probability_ucb=0.16550846978810063,
        global_terminal_win_probability_lcb=q_lcb,
        global_terminal_loss_probability_ucb=0.1655084697881006,
        global_terminal_loss_payoff_usd=-expected_cost,
        global_terminal_win_payoff_usd=win_payoff,
        global_terminal_median_payoff_usd=win_payoff,
        global_terminal_wealth_after_loss_usd=100.0 - expected_cost,
        global_terminal_wealth_after_win_usd=100.0 + win_payoff,
        global_cut_time_expected_value_diagnostic_usd=q_lcb * shares - expected_cost,
        global_expected_value_diagnostic_usd=q_lcb * shares - expected_cost,
    )
    _seal_current_qkernel_cert(cert)

    assert (
        era._global_current_state_execution_economics_rejection_reason(
            cert,
            direction=direction,
        )
        is None
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("global_bin_id", None),
        ("global_terminal_win_probability_lcb", None),
        ("global_terminal_loss_probability_ucb", 0.60),
        ("global_terminal_loss_payoff_usd", "-0.99"),
        ("global_terminal_median_payoff_usd", "23"),
        ("global_expected_value_semantics", "REALIZED_GAIN"),
    ),
)
def test_global_current_certificate_rejects_missing_or_forged_terminal_branch(
    field,
    replacement,
):
    cert = _global_current_qkernel_cert()
    if replacement is None:
        cert.pop(field)
    else:
        cert[field] = replacement
    _seal_current_qkernel_cert(cert)

    assert (
        era._valid_selected_qkernel_execution_economics_payload(
            cert,
            direction="buy_yes",
        )
        is None
    )


def test_broken_global_certificate_cannot_fall_back_to_legacy_route_fields():
    cert = _global_current_qkernel_cert()
    cert.update(
        candidate_id="YES:bin-1:DIRECT_YES:bin-1@proof",
        route_id="DIRECT_YES:bin-1@proof",
        delta_u_at_min=0.01,
        optimal_stake_usd=1.0,
        optimal_delta_u=0.02,
        direction_law_ok=True,
        coherence_allows=True,
    )
    cert.pop("global_actuation_identity")
    _seal_current_qkernel_cert(cert)
    proof = SimpleNamespace(
        direction="buy_yes",
        candidate=SimpleNamespace(metric="high"),
        qkernel_execution_economics=cert,
    )

    assert era._declares_global_current_state_execution_economics(cert) is True
    assert (
        era._valid_selected_qkernel_execution_economics_payload(
            cert,
            direction="buy_yes",
        )
        is None
    )
    assert era._qkernel_actual_submit_quality_rejection_reason(
        proof=proof,
        actual_stake_usd=1.0,
        actual_cost=0.05,
    ).startswith(
        "QKERNEL_ACTUAL_SUBMIT_QUALITY_FLOOR:"
        "GLOBAL_CURRENT_STATE_EXECUTION_ECONOMICS_INVALID:"
    )


@pytest.mark.parametrize(
    ("side", "direction"),
    (("YES", "buy_yes"), ("NO", "buy_no")),
)
def test_actionable_payload_preserves_sealed_global_execution_economics(
    side,
    direction,
):
    cert = _global_current_qkernel_cert(side=side)
    receipt = EventSubmissionReceipt(
        False,
        "global-event-1",
        "global-snapshot-1",
        proof_accepted=True,
        strategy_key="forecast_qkernel_entry",
        family_id="family-1",
        candidate_id="global-candidate-1",
        condition_id="condition-1",
        token_id=f"{side.lower()}-1",
        direction=direction,
        candidate_bin_id="bin-1",
        q_source="replacement_0_1",
        selection_authority_applied="qkernel_spine",
        q_live=0.70,
        q_lcb_5pct=0.60,
        qkernel_execution_economics=cert,
    )
    live_cap = SimpleNamespace(
        payload={
            "usage_id": "usage-1",
            "reserved_notional_usd": 1.0,
        }
    )

    payload = era._actionable_payload_from_receipt(receipt, live_cap)
    payload["event_type"] = "FORECAST_SNAPSHOT_READY"

    assert payload["qkernel_execution_economics"] == cert
    _assert_live_entry_submit_authority(payload)
    taker = era._build_event_bound_taker_quality_proof(
        actionable_payload=payload,
        order_mode="TAKER",
        fresh_best_bid=0.04,
        fresh_best_ask=0.05,
    )
    assert taker is not None and taker["passed"] is True


def test_global_bin_identity_mutation_breaks_current_state_seal():
    cert = _global_current_qkernel_cert()
    sealed = cert["current_state_identity_hash"]

    cert["global_bin_id"] = "other-bin"

    assert era.qkernel_current_state_identity_hash(cert) != sealed
    assert (
        era._valid_selected_qkernel_execution_economics_payload(
            cert,
            direction="buy_yes",
        )
        is None
    )


def test_global_actuation_submit_revalidates_current_wealth_economics(monkeypatch):
    from src.engine import global_auction_universe

    monkeypatch.setattr(
        global_auction_universe,
        "current_portfolio_wealth_witness",
        lambda *_args, **_kwargs: SimpleNamespace(
            economic_identity="wealth-economics-1"
        ),
    )
    actuation = SimpleNamespace(wealth_economic_identity="wealth-economics-1")

    assert era._global_actuation_current_wealth_block_reason(
        object(),
        global_actuation=actuation,
        decision_time=datetime.now(timezone.utc),
    ) is None

    actuation.wealth_economic_identity = "wealth-economics-old"
    assert era._global_actuation_current_wealth_block_reason(
        object(),
        global_actuation=actuation,
        decision_time=datetime.now(timezone.utc),
    ) == "GLOBAL_PREFLIGHT_WEALTH_SUPERSEDED"


def test_global_actuation_submit_blocks_ambiguous_current_wealth(monkeypatch):
    from src.engine import global_auction_universe

    def ambiguous(*_args, **_kwargs):
        raise ValueError("CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS")

    monkeypatch.setattr(
        global_auction_universe,
        "current_portfolio_wealth_witness",
        ambiguous,
    )

    reason = era._global_actuation_current_wealth_block_reason(
        object(),
        global_actuation=SimpleNamespace(
            wealth_economic_identity="wealth-economics-1"
        ),
        decision_time=datetime.now(timezone.utc),
    )

    assert reason == (
        "GLOBAL_PREFLIGHT_WEALTH_UNAVAILABLE:ValueError:"
        "CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS"
    )


def test_global_actuation_current_band_refuses_non_positive_bound():
    cert = _current_qkernel_cert(side="NO")
    cert.update(
        payoff_q_point=0.70,
        pre_qkernel_q_lcb_5pct=0.65,
        cost=0.60,
        edge_lcb=0.10,
    )
    decision = _global_decision(shares="10", cost="6", q="0.59")
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-sample",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    with pytest.raises(ValueError, match="GLOBAL_CURRENT_STATE_ECONOMICS_NON_POSITIVE"):
        era._global_current_state_execution_economics(
            cert,
            decision=decision,
            witness=witness,
        )


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_current_band_binds_candidate_side_when_cert_omits_it(side):
    cert = _current_qkernel_cert(side=side)
    cert.pop("side")
    cert.update(
        payoff_q_point=0.70,
        pre_qkernel_q_lcb_5pct=0.65,
        payoff_q_lcb=0.60,
        cost=0.40,
        edge_lcb=0.20,
    )
    decision = _global_decision(
        shares="10",
        cost="4",
        q="0.60",
        candidate=SimpleNamespace(side=side),
    )
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-sample",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )

    assert current["side"] == side


def test_global_actuation_current_band_refuses_candidate_cert_side_mismatch():
    cert = _current_qkernel_cert(side="NO")
    decision = _global_decision(
        shares="10",
        cost="4",
        q="0.60",
        candidate=SimpleNamespace(side="YES"),
    )
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-sample",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    with pytest.raises(ValueError, match="GLOBAL_CURRENT_STATE_SIDE_INVALID"):
        era._global_current_state_execution_economics(
            cert,
            decision=decision,
            witness=witness,
        )


def test_global_actuation_current_band_missing_prior_still_accepts_low_probability_order():
    cert = _current_qkernel_cert(side="YES")
    for field in (
        "source",
        "decision_id",
        "receipt_hash",
        "q_version",
        "payoff_q_lcb",
        "cost",
    ):
        cert.pop(field)
    cert.update(
        global_actuation_identity="global-actuation-1",
        global_economic_identity="global-economic-1",
        pre_qkernel_q_lcb_5pct=0.12,
    )
    decision = _global_decision(shares="100", cost="5", q="0.10")
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-sample",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
        q_version="global-q-version-1",
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )

    assert current["payoff_q_lcb"] == pytest.approx(0.10)
    assert current["edge_lcb"] == pytest.approx(0.05)


def test_global_actuation_current_band_rejects_malformed_present_prior_lcb():
    cert = _current_qkernel_cert(side="YES")
    cert["payoff_q_lcb"] = "not-a-probability"
    decision = _global_decision(shares="100", cost="1", q="0.60")
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-sample",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    with pytest.raises(ValueError, match="GLOBAL_CURRENT_STATE_PRIOR_LCB_INVALID"):
        era._global_current_state_execution_economics(
            cert,
            decision=decision,
            witness=witness,
        )


def test_global_actuation_missing_point_still_accepts_low_probability_order():
    cert = _current_qkernel_cert(side="NO")
    cert.pop("payoff_q_point")
    cert.update(
        payoff_q_lcb=0.15,
        pre_qkernel_q_lcb_5pct=0.15,
        cost=0.10,
        edge_lcb=0.05,
    )
    decision = _global_decision(
        shares="10",
        cost="1",
        q="0.15",
        candidate=SimpleNamespace(bin_id="bin-1", side="NO"),
    )
    witness = SimpleNamespace(
        bin_ids=("bin-1", "bin-2"),
        sample_matrix_identity="global-current-missing-point",
        yes_q_samples=np.tile(np.array([[0.8, 0.2]]), (400, 1)),
        band_alpha=0.05,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )

    assert current["payoff_q_lcb"] == pytest.approx(0.15)
    assert current["edge_lcb"] == pytest.approx(0.05)


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_current_band_can_tighten_served_bound(side):
    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=0.80,
        pre_qkernel_q_lcb_5pct=0.70,
        cost=0.40,
        edge_lcb=0.30,
    )
    decision = _global_decision(shares="10", cost="4", q="0.60")
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-tighter-sample",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )

    assert current["global_current_band_payoff_q_lcb"] == pytest.approx(0.60)
    assert current["global_current_served_payoff_q_lcb"] == pytest.approx(0.70)
    assert current["payoff_q_lcb"] == pytest.approx(0.60)
    assert era._qkernel_current_state_solve_economics(current) is True


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_legacy_served_bound_is_diagnostic_only(side):
    """A historical served shrink cannot veto the current source-clock band."""

    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=0.80,
        payoff_q_lcb=0.40,
        pre_qkernel_q_lcb_5pct=0.45,
        cost=0.40,
        edge_lcb=0.0,
    )
    decision = _global_decision(shares="10", cost="4", q="0.70")
    witness = SimpleNamespace(
        sample_matrix_identity=f"global-current-no-legacy-veto-{side.lower()}",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )

    assert current["global_current_served_payoff_q_lcb"] == pytest.approx(0.45)
    assert current["global_current_prior_payoff_q_lcb"] == pytest.approx(0.40)
    assert current["payoff_q_lcb"] == pytest.approx(0.70)
    assert current["global_current_effective_payoff_q_lcb"] == pytest.approx(0.70)
    assert era._qkernel_current_state_solve_economics(current) is True


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_reauctions_sample_band_above_served_point(side):
    """A coherent sample tail cannot loosen the separately served point bound."""

    served = 0.9187643552930886
    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=served,
        payoff_q_lcb=served,
        pre_qkernel_q_lcb_5pct=served,
        cost=0.001,
        edge_lcb=served - 0.001,
    )
    decision = _global_decision(shares="1000", cost="1", q="0.9375885546392851")
    witness = SimpleNamespace(
        sample_matrix_identity=f"global-current-point-cap-{side.lower()}",
        yes_q_samples=SimpleNamespace(shape=(400, 11)),
        band_alpha=0.05,
    )

    with pytest.raises(era._GlobalProbabilityTightened) as raised:
        era._global_current_state_execution_economics(
            cert,
            decision=decision,
            witness=witness,
        )

    assert raised.value.payoff_q_lcb == pytest.approx(served)


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_reauctions_boundary_lcb_above_immutable_point(side):
    """A rounded boundary LCB is projected onto its point before re-auction."""

    point = 1.0 - 1e-12
    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=point,
        payoff_q_lcb=1.0,
        pre_qkernel_q_lcb_5pct=1.0,
        cost=0.40,
        edge_lcb=0.60,
    )
    decision = _global_decision(shares="10", cost="4", q="1")
    witness = SimpleNamespace(
        sample_matrix_identity=f"global-current-boundary-{side.lower()}",
        yes_q_samples=SimpleNamespace(shape=(500, 11)),
        band_alpha=0.05,
    )

    with pytest.raises(era._GlobalProbabilityTightened) as raised:
        era._global_current_state_execution_economics(
            cert,
            decision=decision,
            witness=witness,
        )

    assert raised.value.payoff_q_lcb == pytest.approx(point)


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_reauctions_prior_band_above_served_point(side):
    """An improved qkernel band is capped by the frozen served certificate."""

    served = 0.9187643552930886
    current = 0.9375885546392851
    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=served,
        payoff_q_lcb=current,
        pre_qkernel_q_lcb_5pct=served,
        cost=0.001,
        edge_lcb=current - 0.001,
    )
    decision = _global_decision(shares="1000", cost="1", q=str(current))
    witness = SimpleNamespace(
        sample_matrix_identity=f"global-current-prior-cap-{side.lower()}",
        yes_q_samples=SimpleNamespace(shape=(400, 11)),
        band_alpha=0.05,
    )

    with pytest.raises(era._GlobalProbabilityTightened) as raised:
        era._global_current_state_execution_economics(
            cert,
            decision=decision,
            witness=witness,
        )

    assert raised.value.payoff_q_lcb == pytest.approx(served)


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_legacy_prior_below_majority_is_diagnostic_only(side):
    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=0.80,
        payoff_q_lcb=0.49,
        pre_qkernel_q_lcb_5pct=0.49,
        cost=0.10,
        edge_lcb=0.39,
    )
    decision = _global_decision(shares="10", cost="1", q="0.60")
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-majority-drop",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )

    assert current["global_current_prior_payoff_q_lcb"] == pytest.approx(0.49)
    assert current["payoff_q_lcb"] == pytest.approx(0.60)


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_legacy_bound_absence_still_accepts_low_probability_order(side):
    cert = _current_qkernel_cert(side=side)
    cert.pop("pre_qkernel_q_lcb_5pct", None)
    cert.update(
        payoff_q_point=0.30,
        payoff_q_lcb=0.20,
        cost=0.10,
        edge_lcb=0.10,
    )
    decision = _global_decision(shares="10", cost="1", q="0.15")
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-no-legacy-bound",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )

    assert current["payoff_q_lcb"] == pytest.approx(0.15)
    assert current["edge_lcb"] == pytest.approx(0.05)


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_legacy_prior_cannot_tighten_frozen_witness(side):
    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=0.80,
        payoff_q_lcb=0.55,
        pre_qkernel_q_lcb_5pct=0.70,
        cost=0.40,
        edge_lcb=0.15,
    )
    decision = _global_decision(shares="10", cost="4", q="0.60")
    witness = SimpleNamespace(
        sample_matrix_identity="global-current-prior-bound-sample",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )

    assert current["global_current_prior_payoff_q_lcb"] == pytest.approx(0.55)
    assert current["payoff_q_lcb"] == pytest.approx(0.60)


@pytest.mark.parametrize("side", ("YES", "NO"))
def test_global_actuation_legacy_prior_cannot_reprice_current_selected_size(side):
    wealth = 100.0
    shares = 80.0
    cost = 40.0
    tightened_q = 0.55
    tightened_delta_log = (
        tightened_q * math.log((wealth + shares - cost) / wealth)
        + (1.0 - tightened_q) * math.log((wealth - cost) / wealth)
    )
    assert tightened_delta_log < 0.0

    cert = _current_qkernel_cert(side=side)
    cert.update(
        payoff_q_point=0.80,
        payoff_q_lcb=tightened_q,
        pre_qkernel_q_lcb_5pct=0.70,
        cost=0.50,
        edge_lcb=0.05,
    )
    decision = _global_decision(
        shares=str(shares),
        cost=str(cost),
        q="0.70",
        wealth=str(wealth),
    )
    witness = SimpleNamespace(
        sample_matrix_identity=f"global-negative-log-tightening-{side.lower()}",
        yes_q_samples=SimpleNamespace(shape=(400, 2)),
        band_alpha=0.05,
    )

    current = era._global_current_state_execution_economics(
        cert,
        decision=decision,
        witness=witness,
    )

    assert current["global_current_prior_payoff_q_lcb"] == pytest.approx(tightened_q)
    assert current["payoff_q_lcb"] == pytest.approx(0.70)


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
        cost=0.05,
        payoff_q_lcb=0.10,
        payoff_q_point=0.12,
        edge_lcb=0.05,
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
        actual_cost=0.05,
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


def test_qkernel_actual_submit_floor_accepts_price_relative_positive_economics():
    # forecast_qkernel_entry declares min_entry_price: 0.10 in the strategy
    # registry (architecture/strategy_profile_registry.yaml) — a cost below
    # that strategy floor is rejected by entry_price_floor_decision regardless
    # of edge, so the price-relative-acceptance fixture must clear 0.10.
    cert = _qkernel_cert()
    cert.update(
        route_id="DIRECT_YES:bin-1@proof",
        candidate_id="YES:bin-1:DIRECT_YES:bin-1@proof",
        side="YES",
        payoff_q_point=0.30,
        payoff_q_lcb=0.20,
        cost=0.15,
        edge_lcb=0.05,
        optimal_stake_usd=23.69,
        optimal_delta_u=0.01,
        delta_u_at_min=0.0002,
        selection_guard_q_safe=0.20,
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
        actual_cost=0.15,
    )

    assert reason is None


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


def test_live_entry_qkernel_authority_enforces_absolute_price_floor():
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

    with pytest.raises(ValueError, match="LIVE_ENTRY_UNIT_PRICE_OUT_OF_BOUNDS"):
        _assert_live_entry_submit_authority(
            {
                "event_type": "FORECAST_SNAPSHOT_READY",
                "selection_authority_applied": "qkernel_spine",
                "direction": "buy_yes",
                "strategy_key": "forecast_qkernel_entry",
                "candidate_bin_id": "b34",
                "q_live": 0.12180248510788458,
                "q_lcb_5pct": 0.06052567908958011,
                "min_entry_price": 0.05,
                "qkernel_execution_economics": cert,
            }
        )


def test_live_entry_qkernel_gate_accepts_six_to_eight_cent_positive_yes():
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


def test_day0_replacement_route_delegates_fdr_to_bound_qkernel_certificate():
    proof = _bound_day0_qkernel_route_proof(
        q_live=1.0,
        q_lcb=1.0,
        price=0.00315,
        trade_score=0.99685,
        false_edge_rate=0.05,
    )
    proof.probability_authority = "replacement_0_1"
    family_id = "Hong Kong|2026-07-13|high"
    hypothesis_ids = tuple(f"h{i}" for i in range(22))

    day0_fdr = _day0_selected_route_fdr_proof(
        event_type="DAY0_EXTREME_UPDATED",
        family_id=family_id,
        all_hypothesis_ids=hypothesis_ids,
        selected_hypothesis_id="h7",
        selected_proof=proof,
    )
    qkernel_fdr = era._qkernel_selected_route_fdr_proof(
        family_id=family_id,
        all_hypothesis_ids=hypothesis_ids,
        selected_hypothesis_id="h7",
        selected_proof=proof,
    )

    assert day0_fdr is None
    assert qkernel_fdr is not None
    assert qkernel_fdr.passed is True
    assert qkernel_fdr.selected_post_fdr == ("h7",)


def test_day0_replacement_route_without_qkernel_certificate_uses_legacy_fdr():
    proof = SimpleNamespace(
        passed_prefilter=True,
        probability_authority="replacement_0_1",
        selection_authority_applied=None,
        qkernel_execution_economics=None,
    )

    day0_fdr = _day0_selected_route_fdr_proof(
        event_type="DAY0_EXTREME_UPDATED",
        family_id="Hong Kong|2026-07-13|high",
        all_hypothesis_ids=("h7",),
        selected_hypothesis_id="h7",
        selected_proof=proof,
    )
    qkernel_fdr = era._qkernel_selected_route_fdr_proof(
        family_id="Hong Kong|2026-07-13|high",
        all_hypothesis_ids=("h7",),
        selected_hypothesis_id="h7",
        selected_proof=proof,
    )

    assert day0_fdr is None
    assert qkernel_fdr is None


def test_day0_replacement_route_stops_on_failed_bound_qkernel_certificate():
    proof = _bound_day0_qkernel_route_proof(
        q_live=0.80,
        q_lcb=0.75,
        price=0.40,
        trade_score=0.35,
        false_edge_rate=0.50,
    )
    proof.probability_authority = "replacement_0_1"
    family_id = "Hong Kong|2026-07-13|high"

    day0_fdr = _day0_selected_route_fdr_proof(
        event_type="DAY0_EXTREME_UPDATED",
        family_id=family_id,
        all_hypothesis_ids=("h7",),
        selected_hypothesis_id="h7",
        selected_proof=proof,
    )
    qkernel_fdr = era._qkernel_selected_route_fdr_proof(
        family_id=family_id,
        all_hypothesis_ids=("h7",),
        selected_hypothesis_id="h7",
        selected_proof=proof,
    )

    assert day0_fdr is None
    assert qkernel_fdr is not None
    assert qkernel_fdr.passed is False
    assert qkernel_fdr.selected_post_fdr == ()


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


def test_pre_submit_payload_uses_fee_aware_global_worst_cost_edge():
    qkernel_cert = _qkernel_cert()
    qkernel_cert.update(
        {
            "global_actuation_identity": "global-actuation-1",
            "global_target_shares": "10",
            "global_max_spend_usd": "4.5",
        }
    )
    final_intent = SimpleNamespace(
        certificate_hash="final-hash",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "condition_id": "condition-1",
            "token_id": "token-yes",
            "side": "BUY",
            "direction": "buy_yes",
            "order_type": "FOK",
            "time_in_force": "FOK",
            "post_only": False,
            "limit_price": 0.44,
            "q_live": 0.70,
            "q_lcb_5pct": 0.60,
            "trade_score": 0.25,
            "size": 10.0,
            "qkernel_execution_economics": qkernel_cert,
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
        book_authority_id="clob_jit_book",
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

    assert payload["expected_edge"] == pytest.approx(0.15)


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


@pytest.mark.parametrize(
    "event_type",
    ("FORECAST_SNAPSHOT_READY", "DAY0_EXTREME_UPDATED"),
)
def test_replacement_forecast_authority_binds_selected_proof_posterior_id(
    monkeypatch,
    event_type,
):
    """Forecast and Day0 certificates share the selected proof's posterior parent."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    topology = [{"bin_id": "25C", "lower_c": 25.0, "upper_c": 25.0}]
    topology_hash = stable_hash(topology)
    provenance_json = json.dumps(
        {
            "bin_topology": topology,
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "q_lcb_basis": "fused_center_bootstrap_p05",
            "q_ucb_json_role": "fused_center_bootstrap_ucb",
            "q_lcb_bootstrap_draws": 200,
            "q_bootstrap_samples_hash": "b" * 64,
            "bayes_precision_fusion": {
                "used_models": ["a", "b", "c"],
                "current_value_serving": {
                    model: {
                        "raw_model_forecast_id": index,
                        "served_via": "single_runs",
                        "served_cycle": "2026-07-08T06:00:00+00:00",
                    }
                    for index, model in enumerate(("a", "b", "c"), start=1)
                },
            },
        },
        sort_keys=True,
    )
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
                posterior_identity_hash TEXT,
                family_id TEXT,
                bin_topology_hash TEXT,
                q_json TEXT,
                q_lcb_json TEXT,
                q_ucb_json TEXT,
                provenance_json TEXT
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
                posterior_identity_hash, family_id, bin_topology_hash,
                q_json, q_lcb_json, q_ucb_json, provenance_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    "Seoul|2026-07-10|high",
                    topology_hash,
                    '{"25C": 1.0}',
                    '{"25C": 0.8}',
                    '{"25C": 1.0}',
                    provenance_json,
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
                    "Seoul|2026-07-10|high",
                    topology_hash,
                    '{"25C": 1.0}',
                    '{"25C": 0.8}',
                    '{"25C": 1.0}',
                    provenance_json,
                ),
        ],
    )

    monkeypatch.setattr(era, "_replacement_authority_enabled", lambda: True)
    monkeypatch.setattr(
        era,
        "runtime_cities_by_name",
        lambda: {"Seoul": SimpleNamespace(timezone="Asia/Seoul", settlement_unit="C")},
    )
    member_provenance = []

    def members_for_bound_posterior(*_args, **kwargs):
        member_provenance.append(kwargs.get("provenance"))
        return (25.0, 26.0, 27.0)

    monkeypatch.setattr(
        era,
        "_posterior_bound_multimodel_members",
        members_for_bound_posterior,
    )
    monkeypatch.setattr(era, "_replacement_live_input_lag_reason", lambda *_args, **_kwargs: None)

    payload, _clock = era._forecast_authority_payload_and_clock(
        conn,
        event=SimpleNamespace(
            event_type=event_type,
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
    assert payload["replacement_bin_topology"] == topology
    assert member_provenance == [json.loads(provenance_json)]

    monkeypatch.setattr(
        era,
        "_posterior_bound_multimodel_members",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        era,
        "_spine_multimodel_members_for_event",
        lambda *_args, **_kwargs: pytest.fail(
            "declared posterior member binding must not fall back to carrier inference"
        ),
    )
    with pytest.raises(
        ValueError,
        match="FORECAST_AUTHORITY_EVIDENCE_MISSING:replacement_posterior",
    ):
        era._forecast_authority_payload_and_clock(
            conn,
            event=SimpleNamespace(
                event_type=event_type,
                causal_snapshot_id="rmf-Seoul|2026-07-10|high|2026-07-08",
            ),
            family=SimpleNamespace(
                city="Seoul",
                target_date="2026-07-10",
                metric="high",
            ),
            payload={},
            decision_time=datetime(
                2026,
                7,
                8,
                17,
                7,
                14,
                tzinfo=timezone.utc,
            ),
            bound_posterior_id=42,
        )


def test_posterior_cycle_members_do_not_depend_on_forecast_carrier(monkeypatch):
    """Posterior members come from its recorded current inputs, not carrier shape."""

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE raw_model_forecasts (
            raw_model_forecast_id INTEGER PRIMARY KEY,
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            captured_at TEXT,
            lead_days INTEGER,
            endpoint TEXT,
            forecast_value_c REAL
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO raw_model_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                index,
                model,
                "Hong Kong",
                "2026-07-13",
                "high",
                cycle,
                available,
                captured,
                0,
                "single_runs",
                value,
            )
            for index, model, cycle, available, captured, value in (
                (
                    1,
                    "a",
                    "2026-07-12T18:00:00+00:00",
                    "2026-07-13T00:04:00+00:00",
                    "2026-07-13T00:49:00+00:00",
                    33.0,
                ),
                (
                    2,
                    "b",
                    "2026-07-12T18:00:00+00:00",
                    "2026-07-12T21:35:00+00:00",
                    "2026-07-13T00:49:00+00:00",
                    34.0,
                ),
                (
                    3,
                    "c",
                    "2026-07-13T01:50:00+00:00",
                    "2026-07-13T01:50:00+00:00",
                    "2026-07-13T02:23:00+00:00",
                    35.0,
                ),
            )
        ],
    )
    monkeypatch.setattr(
        era,
        "runtime_cities_by_name",
        lambda: {
            "Hong Kong": SimpleNamespace(
                timezone="Asia/Hong_Kong",
                settlement_unit="C",
            )
        },
    )
    family = SimpleNamespace(
        city="Hong Kong",
        target_date="2026-07-13",
        metric="high",
    )
    provenance = {
        "bayes_precision_fusion": {
            "used_models": ["a", "b", "c"],
            "current_value_serving": {
                "a": {
                    "raw_model_forecast_id": 1,
                    "served_via": "single_runs",
                    "served_cycle": "2026-07-12T18:00:00+00:00",
                },
                "b": {
                    "raw_model_forecast_id": 2,
                    "served_via": "single_runs",
                    "served_cycle": "2026-07-12T18:00:00+00:00",
                },
                "c": {
                    "raw_model_forecast_id": 3,
                    "served_via": "single_runs",
                    "served_cycle": "2026-07-13T01:50:00+00:00",
                },
            },
        }
    }
    members = era._posterior_bound_multimodel_members(
        conn,
        family=family,
        source_cycle_time="2026-07-13T06:00:00+00:00",
        provenance=provenance,
    )

    assert members == (33.0, 34.0, 35.0)

    source_clock = json.loads(json.dumps(provenance))
    fusion = source_clock["bayes_precision_fusion"]
    fusion["used_models"] = ["a", "b"]
    fusion["current_value_serving"].pop("c")
    fusion.update(
        {
            "decorrelated_providers_complete": True,
            "decorrelated_providers_expected": 2,
            "decorrelated_providers_served": 2,
            "source_clock_one_scheme": {
                "configured_sources": ["a", "b"],
                "used_weights": {"a": 0.5, "b": 0.5},
                "missing_sources": [],
                "one_scheme_status": "GRID_CAP10_LIVE_READY",
                "walkforward_pass": True,
            },
        }
    )
    assert era._posterior_bound_multimodel_members(
        conn,
        family=family,
        source_cycle_time="2026-07-13T06:00:00+00:00",
        provenance=source_clock,
    ) == (33.0, 34.0)
    assert era._posterior_bound_spine_inputs(
        conn,
        family=family,
        source_cycle_time="2026-07-13T06:00:00+00:00",
        provenance=source_clock,
    ) == (
        (33.0, 34.0),
        "2026-07-13T06:00:00+00:00",
        (0.5, 0.5),
    )
    present, certificate = era._source_clock_model_count_certificate(source_clock)
    assert present is True
    assert certificate == {
        "posterior_model_count_basis": "source_clock_configured_sources",
        "posterior_completeness_status": "GRID_CAP10_LIVE_READY",
        "posterior_configured_sources": ("a", "b"),
        "posterior_served_sources": ("a", "b"),
        "posterior_missing_sources": (),
        "posterior_walkforward_pass": True,
        "posterior_configured_model_count": 2,
        "posterior_served_model_count": 2,
    }

    legacy_two = json.loads(json.dumps(source_clock))
    legacy_two["bayes_precision_fusion"].pop("source_clock_one_scheme")
    assert era._posterior_bound_multimodel_members(
        conn,
        family=family,
        source_cycle_time="2026-07-13T06:00:00+00:00",
        provenance=legacy_two,
    ) is None

    incomplete = json.loads(json.dumps(source_clock))
    incomplete["bayes_precision_fusion"]["source_clock_one_scheme"][
        "missing_sources"
    ] = ["b"]
    assert era._posterior_bound_multimodel_members(
        conn,
        family=family,
        source_cycle_time="2026-07-13T06:00:00+00:00",
        provenance=incomplete,
    ) is None
    assert era._posterior_bound_spine_inputs(
        conn,
        family=family,
        source_cycle_time="2026-07-13T06:00:00+00:00",
        provenance=incomplete,
    ) is None

    provenance["bayes_precision_fusion"]["current_value_serving"]["c"][
        "raw_model_forecast_id"
    ] = 99
    assert (
        era._posterior_bound_multimodel_members(
            conn,
            family=family,
            source_cycle_time="2026-07-13T06:00:00+00:00",
            provenance=provenance,
        )
        is None
    )
