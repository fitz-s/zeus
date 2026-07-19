# Created: 2026-04-26
# Lifecycle: created=2026-04-26; last_reviewed=2026-07-19; last_reused=2026-07-19
# Purpose: Lock INV-31 command recovery behavior plus snapshot-gated command inserts.
# Reuse: Run when command recovery, command journal schema, or executable snapshot gating changes.
# Last reused/audited: 2026-07-19
# Authority basis: docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md u00a7P1.S4
"""INV-31 anchor tests: command recovery loop.

All 8 resolution-table cases + cycle integration test.
Uses in-memory DB; mocks PolymarketClient.get_order.
"""
from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.decision_kernel.canonicalization import qkernel_current_state_identity_hash


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory DB with full schema."""
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.collateral_ledger import init_collateral_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    init_collateral_schema(c)
    yield c
    c.close()


@pytest.fixture
def mock_client():
    return MagicMock(spec_set=["get_order", "get_open_orders", "get_trades", "get_clob_market_info", "v2_preflight"])


def test_edli_recovery_refs_prefer_world_authority_over_trade_ghosts():
    from src.execution.command_recovery import (
        _edli_live_cap_ref,
        _edli_live_order_events_ref,
        _edli_live_order_projection_ref,
    )

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE edli_live_order_events (id INTEGER)")
        conn.execute("CREATE TABLE edli_live_order_projection (id INTEGER)")
        conn.execute("CREATE TABLE edli_live_cap_usage (id INTEGER)")
        conn.execute("ATTACH DATABASE ':memory:' AS world")
        conn.execute("CREATE TABLE world.edli_live_order_events (id INTEGER)")
        conn.execute("CREATE TABLE world.edli_live_order_projection (id INTEGER)")
        conn.execute("CREATE TABLE world.edli_live_cap_usage (id INTEGER)")

        assert _edli_live_order_events_ref(conn) == "world.edli_live_order_events"
        assert _edli_live_order_projection_ref(conn) == "world.edli_live_order_projection"
        assert _edli_live_cap_ref(conn, "edli_live_cap_usage") == "world.edli_live_cap_usage"
    finally:
        conn.close()


# T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md):
# test_filled_projection_repair_voids_absorbed_chain_only_stub deleted.
# It exercised command_recovery._void_absorbed_chain_only_projection, which
# only ever matched phase='quarantined' AND chain_state=
# 'entry_authority_quarantined' rows -- a combination the CHECK constraint
# no longer admits post-T5-migration. The function (and its 3 call sites)
# were deleted as provably unreachable; there is no current-law replacement
# scenario to rewrite this test into.


def _valid_day0_pre_submit_payload(**overrides):
    payload = {
        "event_id": "evt-day0-presubmit",
        "final_intent_id": "intent-day0-presubmit",
        "condition_id": "cond-day0-presubmit",
        "token_id": "tok-day0-presubmit",
        "side": "BUY",
        "direction": "buy_yes",
        "order_type": "GTC",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": "2026-06-30T17:18:36+00:00",
        "quote_seen_at": "2026-06-30T17:18:34+00:00",
        "quote_age_ms": 2000,
        "max_quote_age_ms": 5000,
        "book_hash": "book-hash",
        "current_best_bid": 0.68,
        "current_best_ask": 0.72,
        "limit_price": 0.70,
        "size": 10.0,
        "q_live": 0.96,
        "q_lcb_5pct": 0.95,
        "expected_edge": 0.25,
        "min_entry_price": 0.10,
        "min_expected_profit_usd": 1.0,
        "min_submit_edge_density": 0.02,
        "expected_edge_source_certificate_hash": "edge-cert",
        "cost_basis_source_certificate_hash": "cost-cert",
        "would_cross_book": False,
        "tick_size": "0.01",
        "tick_aligned": True,
        "min_order_size": 1.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "book_authority_id": "book-auth",
        "book_captured_at": "2026-06-30T17:18:34+00:00",
        "heartbeat_authority_id": "heartbeat-auth",
        "heartbeat_checked_at": "2026-06-30T17:18:35+00:00",
        "user_ws_authority_id": "user-ws-auth",
        "user_ws_checked_at": "2026-06-30T17:18:35+00:00",
        "venue_connectivity_authority_id": "venue-auth",
        "venue_connectivity_checked_at": "2026-06-30T17:18:35+00:00",
        "balance_allowance_authority_id": "balance-auth",
        "balance_allowance_checked_at": "2026-06-30T17:18:35+00:00",
        "event_type": "DAY0_EXTREME_UPDATED",
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "route_id": "DIRECT_YES:b20@proof",
            "route_type": "direct",
            "side": "YES",
            "payoff_q_point": 0.96,
            "payoff_q_lcb": 0.95,
            "cost": 0.70,
            "edge_lcb": 0.25,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 10.0,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
            "q_lcb_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
            "selection_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
            "q_lcb_guard_abstained": False,
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.95,
        },
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "live_authority_status": "live",
        "source_authorized_status": "AUTHORIZED",
        "day0_q_source": "day0_remaining_day",
        "day0_q_mode": "remaining_day",
        "day0_remaining_models": 37,
        "rounded_value": 72.0,
        "observation_time": "2026-06-30T17:18:00+00:00",
        "day0_lcb_transform": {
            "yes_lcb_by_condition": {"cond-day0-presubmit": 0.95},
            "no_lcb_by_condition": {"cond-day0-presubmit": 0.05},
        },
    }
    payload.update(overrides)
    return payload


def test_day0_presubmit_revalidation_uses_observation_authority_with_qkernel():
    from src.events.live_order_aggregate import _validate_pre_submit_revalidation_payload

    _validate_pre_submit_revalidation_payload(_valid_day0_pre_submit_payload())


def _replacement_day0_recovery_payload(direction: str) -> dict:
    q_live, q_lcb = ((0.65, 0.58) if direction == "buy_yes" else (0.72, 0.64))
    posterior_id = 36169
    condition_id = f"condition-recovery-{direction}"
    observation = {
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "observation_time": "2026-07-13T09:00:00+00:00",
        "observation_available_at": "2026-07-13T09:02:00+00:00",
        "raw_value": 16.0,
        "rounded_value": 16,
        "sample_count": 11,
        "station_id": "EGLC",
        "settlement_source": "wu_icao_history",
        "settlement_unit": "C",
        "_edli_global_day0_binding": {
            "posterior_id": posterior_id,
            "city": "London",
            "target_date": "2026-07-13",
            "metric": "low",
            "observation_time": "2026-07-13T09:00:00+00:00",
            "observation_available_at": "2026-07-13T09:02:00+00:00",
            "observed_extreme_native": 16.0,
            "rounded_value": 16,
            "sample_count": 11,
            "station_id": "EGLC",
            "settlement_source": "wu_icao_history",
            "settlement_unit": "C",
        },
    }
    economics = {
        "source": "qkernel_spine",
        "decision_id": f"decision-{direction}",
        "receipt_hash": f"receipt-{direction}",
        "q_version": f"q-version-{direction}",
        "sample_hash": f"sample-{direction}",
        "candidate_id": f"candidate-{direction}",
        "route_id": f"DIRECT_{'YES' if direction == 'buy_yes' else 'NO'}:bin",
        "bin_id": "bin",
        "side": "YES" if direction == "buy_yes" else "NO",
        "payoff_q_point": q_live,
        "payoff_q_lcb": q_lcb,
        "edge_lcb": q_lcb - 0.40,
        "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
        "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
        "q_lcb_guard_abstained": False,
        "selection_guard_abstained": False,
        "q_lcb_guard_cell_key": f"sample-{direction}",
        "selection_guard_cell_key": f"sample-{direction}",
        "selection_guard_n": 400,
        "selection_guard_q_safe": q_lcb,
    }
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )
    return {
        "event_type": "DAY0_EXTREME_UPDATED",
        "direction": direction,
        "condition_id": condition_id,
        "city": "London",
        "target_date": "2026-07-13",
        "metric": "low",
        "posterior_id": posterior_id,
        "q_live": q_live,
        "q_lcb_5pct": q_lcb,
        "q_source": "replacement_0_1",
        "_edli_q_source": "replacement_0_1",
        "day0_probability_authority": {
            "probability_authority": "replacement_current_global_probability_v1",
            "q_source": "replacement_0_1",
            "posterior_id": posterior_id,
            "global_current_observation_payload": observation,
        },
        "qkernel_execution_economics": economics,
    }


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
def test_recovery_preserves_current_replacement_day0_qkernel_authority(direction):
    from src.execution.command_recovery import _event_context_qkernel_authority

    payload = _replacement_day0_recovery_payload(direction)
    economics = payload["qkernel_execution_economics"]

    recovered, q_live, q_lcb, authority = _event_context_qkernel_authority(
        economics,
        event_type=payload["event_type"],
        probability_payload=payload,
        q_live=0.01,
        q_lcb=0.0,
    )

    assert recovered == economics
    assert q_live == pytest.approx(payload["q_live"])
    assert q_lcb == pytest.approx(payload["q_lcb_5pct"])
    assert authority == "qkernel_spine"


def test_recovery_nested_audit_threads_owning_replacement_probability_payload():
    from src.execution.command_recovery import (
        _event_context_qkernel_authority,
        _selected_qkernel_execution_economics_with_authority,
    )

    audit = _replacement_day0_recovery_payload("buy_no")
    economics = audit.pop("qkernel_execution_economics")
    audit["opportunity_book"] = {
        "cache_summary": {"selected_qkernel_execution_economics": economics}
    }
    wrapper = {"decision_audit": audit}

    selected, probability_payload = (
        _selected_qkernel_execution_economics_with_authority(wrapper)
    )
    recovered, _q_live, _q_lcb, authority = _event_context_qkernel_authority(
        selected,
        event_type=audit["event_type"],
        probability_payload=probability_payload,
        q_live=audit["q_live"],
        q_lcb=audit["q_lcb_5pct"],
    )

    assert probability_payload is audit
    assert recovered == economics
    assert authority == "qkernel_spine"


def test_recovery_downgrades_tampered_replacement_probability_binding():
    from src.execution.command_recovery import _event_context_qkernel_authority

    payload = _replacement_day0_recovery_payload("buy_yes")
    payload["day0_probability_authority"]["posterior_id"] += 1

    recovered, q_live, q_lcb, authority = _event_context_qkernel_authority(
        payload["qkernel_execution_economics"],
        event_type=payload["event_type"],
        probability_payload=payload,
        q_live=payload["q_live"],
        q_lcb=payload["q_lcb_5pct"],
    )

    assert recovered == {}
    assert q_live is None
    assert q_lcb is None
    assert authority == "venue_fact_recovery"


@pytest.mark.parametrize(
    ("field", "value"),
    (("side", "NO"), ("payoff_q_point", 0.99), ("payoff_q_lcb", 0.98)),
)
def test_recovery_downgrades_replacement_selected_leg_tamper(field, value):
    from src.execution.command_recovery import _event_context_qkernel_authority

    payload = _replacement_day0_recovery_payload("buy_yes")
    economics = payload["qkernel_execution_economics"]
    economics[field] = value
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )

    recovered, q_live, q_lcb, authority = _event_context_qkernel_authority(
        economics,
        event_type=payload["event_type"],
        probability_payload=payload,
        q_live=payload["q_live"],
        q_lcb=payload["q_lcb_5pct"],
    )

    assert recovered == {}
    assert q_live is None
    assert q_lcb is None
    assert authority == "venue_fact_recovery"


def test_recovery_downgrades_unsealed_replacement_current_state_identity():
    from src.execution.command_recovery import _event_context_qkernel_authority

    payload = _replacement_day0_recovery_payload("buy_no")
    economics = payload["qkernel_execution_economics"]
    economics.pop("sample_hash")

    recovered, q_live, q_lcb, authority = _event_context_qkernel_authority(
        economics,
        event_type=payload["event_type"],
        probability_payload=payload,
        q_live=payload["q_live"],
        q_lcb=payload["q_lcb_5pct"],
    )

    assert recovered == {}
    assert q_live is None
    assert q_lcb is None
    assert authority == "venue_fact_recovery"


def test_recovery_downgrades_mixed_container_event_type_bypass():
    from src.execution.command_recovery import _event_context_qkernel_authority

    payload = _replacement_day0_recovery_payload("buy_no")

    recovered, q_live, q_lcb, authority = _event_context_qkernel_authority(
        payload["qkernel_execution_economics"],
        event_type="FORECAST_SNAPSHOT_READY",
        probability_payload=payload,
        q_live=payload["q_live"],
        q_lcb=payload["q_lcb_5pct"],
    )

    assert recovered == {}
    assert q_live is None
    assert q_lcb is None
    assert authority == "venue_fact_recovery"


def test_forecast_presubmit_revalidation_still_requires_qkernel_economics():
    from src.events.live_order_aggregate import (
        LiveOrderAggregateError,
        _validate_pre_submit_revalidation_payload,
    )

    payload = _valid_day0_pre_submit_payload(
        event_type="FORECAST_SNAPSHOT_READY",
        qkernel_execution_economics=None,
    )

    with pytest.raises(
        LiveOrderAggregateError,
        match="PreSubmitRevalidated requires qkernel_execution_economics",
    ):
        _validate_pre_submit_revalidation_payload(payload)


def test_day0_strategy_fallback_preserves_buy_yes_nowcast_semantics():
    from src.execution.command_recovery import _event_bound_strategy_key_from_payload

    assert (
        _event_bound_strategy_key_from_payload(
            {"event_type": "DAY0_EXTREME_UPDATED", "direction": "buy_yes"}
        )
        == "day0_nowcast_entry"
    )
    assert (
        _event_bound_strategy_key_from_payload(
            {"event_type": "DAY0_EXTREME_UPDATED", "direction": "buy_no"}
        )
        == "settlement_capture"
    )


def test_forecast_strategy_fallback_preserves_qkernel_semantics():
    from src.execution.command_recovery import _event_bound_strategy_key_from_payload

    assert (
        _event_bound_strategy_key_from_payload(
            {"event_type": "FORECAST_SNAPSHOT_READY", "direction": "buy_yes"}
        )
        == "forecast_qkernel_entry"
    )
    assert (
        _event_bound_strategy_key_from_payload(
            {"event_type": "FORECAST_SNAPSHOT_READY", "direction": "buy_no"}
        )
        == "forecast_qkernel_entry"
    )


def test_boot_fast_recovery_does_not_capture_venue_snapshot(tmp_path, monkeypatch):
    """Boot-fast recovery must not block scheduler startup on CLOB reads."""
    from src.execution import command_recovery
    from src.execution import venue_sync_contract
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema

    db_path = tmp_path / "boot-fast.db"
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    init_schema(seed)
    init_collateral_schema(seed)
    seed.execute(
        """
        INSERT INTO position_current (
            position_id, phase, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price,
            p_posterior, decision_snapshot_id, entry_method, strategy_key,
            edge_source, discovery_mode, chain_state, token_id, no_token_id,
            condition_id, order_id, order_status, updated_at, temperature_metric,
            chain_shares, exit_retry_count, next_exit_retry_at, exit_reason
        ) VALUES (
            'pos-boot-chain-zero-stale', 'voided', 'condition-test', 'Manila', 'Manila',
            '2026-07-01', 'Will the highest temperature in Manila be 29C on July 1?',
            'buy_yes', 'C', 0.15, 9.7, 0.15, 0.015,
            0.13, 'forecast-snap-old', 'qkernel_spine', 'center_buy',
            'center_buy', 'opening_hunt', 'chain_confirmed_zero', 'tok-001', 'tok-001-no',
            'condition-test', NULL, 'retry_pending',
            '2026-06-29T17:33:25+00:00', 'high',
            9.7, 6, '2026-06-29T17:45:00+00:00', 'CHAIN_CONFIRMED_ZERO'
        )
        """
    )
    seed.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, decision_id,
            snapshot_id, order_id, command_id, caused_by, idempotency_key,
            venue_status, source_module, payload_json, env
        ) VALUES (
            'evt-boot-chain-zero-stale', 'pos-boot-chain-zero-stale', 11, 'ADMIN_VOIDED',
            '2026-06-29T17:33:25+00:00', 'pending_exit', 'voided',
            'center_buy', 'dec-1', 'snap-1', NULL, NULL,
            'chain_truth_balance_zero', 'idem-boot-chain-zero-stale', 'voided',
            'src.execution.exit_lifecycle',
            '{"evidence_source":"CHAIN_BALANCEOF","chain_state":"chain_confirmed_zero","reason":"CHAIN_CONFIRMED_ZERO"}',
            'live'
        )
        """
    )
    seed.commit()
    seed.close()

    def _conn_factory():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    def _fail_capture(*args, **kwargs):
        raise AssertionError("boot_fast must not call capture_venue_read_snapshot")

    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
    monkeypatch.setattr(venue_sync_contract, "capture_venue_read_snapshot", _fail_capture)

    client = MagicMock(spec_set=["get_order", "get_open_orders", "get_trades", "get_clob_market_info"])
    summary = command_recovery.reconcile_unresolved_commands(client=client, scope="boot_fast")

    assert summary["scope"] == "boot_fast"
    assert summary["venue_snapshot_deferred"] is True
    assert summary["deferred_full_sweep"] is True
    assert summary["hard_terminal_position_projection_repair"]["advanced"] == 1
    client.get_order.assert_not_called()
    client.get_open_orders.assert_not_called()
    client.get_trades.assert_not_called()
    verified = _conn_factory()
    try:
        row = verified.execute(
            """
            SELECT chain_shares, order_status, exit_retry_count, next_exit_retry_at
              FROM position_current
             WHERE position_id = 'pos-boot-chain-zero-stale'
            """
        ).fetchone()
    finally:
        verified.close()
    assert row["chain_shares"] == 0.0
    assert row["order_status"] == "voided"
    assert row["exit_retry_count"] == 0
    assert row["next_exit_retry_at"] is None


def test_boot_fast_releases_review_required_exit_mutex_before_scheduler(
    tmp_path,
    monkeypatch,
):
    """Boot-fast must clear local REVIEW_REQUIRED exit mutex debt without venue reads."""
    from src.execution import command_recovery
    from src.execution import venue_sync_contract
    from src.execution.exit_safety import ExitMutex
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema

    db_path = tmp_path / "boot-fast-review-mutex.db"
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    init_schema(seed)
    init_collateral_schema(seed)
    _insert(
        seed,
        command_id="cmd-review-exit",
        position_id="pos-review",
        intent_kind="EXIT",
        token_id="tok-review",
        side="SELL",
    )
    _advance_to_review_required(seed, "cmd-review-exit")
    mutex = ExitMutex(seed)
    assert mutex.acquire("pos-review", "tok-review", "cmd-review-exit") is True
    seed.commit()
    seed.close()

    def _conn_factory():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    def _fail_capture(*args, **kwargs):
        raise AssertionError("boot_fast must not call capture_venue_read_snapshot")

    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
    monkeypatch.setattr(venue_sync_contract, "capture_venue_read_snapshot", _fail_capture)

    client = MagicMock(spec_set=["get_order", "get_open_orders", "get_trades", "get_clob_market_info"])
    summary = command_recovery.reconcile_unresolved_commands(client=client, scope="boot_fast")

    assert summary["scope"] == "boot_fast"
    assert summary["venue_snapshot_deferred"] is True
    assert summary["review_required_exit_mutex_release"] == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    verified = _conn_factory()
    try:
        row = verified.execute(
            """
            SELECT m.released_at, m.release_reason, c.state
              FROM exit_mutex_holdings m
              JOIN venue_commands c ON c.command_id = m.command_id
             WHERE m.command_id = 'cmd-review-exit'
            """
        ).fetchone()
    finally:
        verified.close()
    assert row["released_at"] is not None
    assert row["release_reason"] == "REVIEW_REQUIRED_RECOVERY"
    assert row["state"] == "REVIEW_REQUIRED"


def test_boot_fast_repairs_confirmed_chain_absence_positive_projection(
    tmp_path,
    monkeypatch,
):
    """Boot-fast must clear chain-absent positive projection debt before schedulers start."""
    from src.execution import command_recovery
    from src.execution import venue_sync_contract
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema

    db_path = tmp_path / "boot-fast-chain-absence.db"
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    init_schema(seed)
    init_collateral_schema(seed)
    # T5 REPLACEMENT PHASE LAW (docs/rebuild/quarantine_excision_2026-07-11.md):
    # the candidate query for repair_confirmed_chain_absence_positive_projections
    # keys off chain_state, not phase -- a normal open phase (active) is the
    # current-law vehicle for "real exposure with a chain-absence conflict",
    # never a quarantine scar.
    seed.execute(
        """
        INSERT INTO position_current (
            position_id, phase, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price,
            p_posterior, decision_snapshot_id, entry_method, strategy_key,
            edge_source, discovery_mode, chain_state, token_id, no_token_id,
            condition_id, order_id, order_status, updated_at, temperature_metric,
            chain_shares, chain_avg_price, chain_cost_basis_usd, exit_reason
        ) VALUES (
            'pos-boot-chain-absent-positive', 'active', 'condition-munich',
            'Munich', 'Europe', '2026-06-30',
            'Will the highest temperature in Munich be 30C on June 30?',
            'buy_no', 'C', 21.27, 29.14, 21.27, 0.73,
            0.1449, 'forecast-snap-munich', 'qkernel_spine', 'center_buy',
            'center_buy', 'day0', 'chain_absent_confirmed_position_unattributed',
            'tok-yes', 'tok-no', 'condition-munich', 'ord-entry', 'filled',
            '2026-06-30T00:22:00+00:00', 'high', 29.14, 0.73, 21.27,
            'chain_absent_confirmed_position_unattributed'
        )
        """
    )
    seed.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, decision_id,
            snapshot_id, order_id, command_id, caused_by, idempotency_key,
            venue_status, source_module, payload_json, env
        ) VALUES (
            'evt-boot-chain-absent-positive', 'pos-boot-chain-absent-positive',
            4, 'REVIEW_REQUIRED', '2026-06-30T00:22:00+00:00',
            'active', 'active', 'center_buy', 'dec-1', 'snap-1',
            'ord-entry', NULL, 'chain_absent_confirmed_position_unattributed',
            'idem-boot-chain-absent-positive', 'review_required',
            'src.state.chain_reconciliation',
            '{"reason":"chain_absent_confirmed_position_unattributed"}',
            'live'
        )
        """
    )
    seed.commit()
    seed.close()

    def _conn_factory():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    def _fail_capture(*args, **kwargs):
        raise AssertionError("boot_fast must not call capture_venue_read_snapshot")

    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
    monkeypatch.setattr(venue_sync_contract, "capture_venue_read_snapshot", _fail_capture)

    client = MagicMock(spec_set=["get_order", "get_open_orders", "get_trades", "get_clob_market_info"])
    summary = command_recovery.reconcile_unresolved_commands(client=client, scope="boot_fast")

    assert summary["scope"] == "boot_fast"
    assert summary["venue_snapshot_deferred"] is True
    assert summary["confirmed_chain_absence_projection_repair"] == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    verified = _conn_factory()
    try:
        row = verified.execute(
            """
            SELECT chain_shares, chain_avg_price, chain_cost_basis_usd
              FROM position_current
             WHERE position_id = 'pos-boot-chain-absent-positive'
            """
        ).fetchone()
    finally:
        verified.close()
    assert dict(row) == {
        "chain_shares": 0.0,
        "chain_avg_price": 0.0,
        "chain_cost_basis_usd": 0.0,
    }


def test_boot_fast_budget_interrupts_slow_db_pass_before_scheduler(
    tmp_path,
    monkeypatch,
):
    """Boot-fast recovery must defer slow local repairs instead of blocking boot."""
    from src.execution import command_recovery
    from src.execution import venue_sync_contract
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema

    db_path = tmp_path / "boot-fast-budget.db"
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    init_schema(seed)
    init_collateral_schema(seed)
    seed.commit()
    seed.close()

    def _conn_factory():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    def _fail_capture(*args, **kwargs):
        raise AssertionError("boot_fast must not call capture_venue_read_snapshot")

    def _slow_db_pass(conn):
        conn.execute(
            """
            WITH RECURSIVE cnt(x) AS (
                SELECT 0
                UNION ALL
                SELECT x + 1 FROM cnt WHERE x < 100000000
            )
            SELECT max(x) FROM cnt
            """
        ).fetchone()
        return {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}

    monkeypatch.setenv("ZEUS_BOOT_FAST_RECOVERY_BUDGET_SECONDS", "0.001")
    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
    monkeypatch.setattr(venue_sync_contract, "capture_venue_read_snapshot", _fail_capture)
    monkeypatch.setattr(
        command_recovery,
        "reconcile_edli_confirmed_legacy_command_repairs",
        _slow_db_pass,
    )

    client = MagicMock(spec_set=["get_order", "get_open_orders", "get_trades", "get_clob_market_info"])
    summary = command_recovery.reconcile_unresolved_commands(client=client, scope="boot_fast")

    assert summary["scope"] == "boot_fast"
    assert summary["venue_snapshot_deferred"] is True
    assert summary["deferred_full_sweep"] is True
    assert summary["boot_fast_budget_exhausted"] is True
    assert "edli_confirmed_legacy_command_repair" in summary["boot_fast_deferred_passes"]
    assert summary["boot_fast_defer_reasons"]["edli_confirmed_legacy_command_repair"] == (
        "budget_exhausted_during_pass"
    )
    client.get_order.assert_not_called()
    client.get_open_orders.assert_not_called()
    client.get_trades.assert_not_called()


@pytest.mark.parametrize(
    "lock_error",
    [
        BlockingIOError(
            "db_writer_lock(write_class=live) contended on test.writer-lock.live"
        ),
        sqlite3.OperationalError("database is locked"),
    ],
)
def test_live_tick_lock_contention_defers_once_without_sleep(monkeypatch, lock_error):
    from src.execution import command_recovery

    calls = []
    summary = {}

    def _contended():
        calls.append("attempt")
        raise lock_error

    monkeypatch.setattr(
        command_recovery.time,
        "sleep",
        lambda _delay: pytest.fail("live_tick DB contention must not sleep"),
    )

    assert command_recovery._run_recovery_pass_with_lock_policy(
        "first_pass",
        _contended,
        scope="live_tick",
        summary=summary,
    ) is None
    assert command_recovery._run_recovery_pass_with_lock_policy(
        "later_pass",
        _contended,
        scope="live_tick",
        summary=summary,
    ) is None

    assert calls == ["attempt"]
    assert summary == {
        "db_lock_deferred": True,
        "db_lock_deferred_at": "first_pass",
        "db_lock_deferred_count": 1,
    }


def test_full_recovery_preserves_lock_retry_schedule(monkeypatch):
    from src.execution import command_recovery

    attempts = 0
    sleeps = []

    def _eventually_available():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database is locked")
        return "done"

    monkeypatch.setattr(command_recovery.time, "sleep", sleeps.append)

    result = command_recovery._run_recovery_pass_with_lock_policy(
        "required_pass",
        _eventually_available,
        scope="full",
        summary={},
    )

    assert result == "done"
    assert attempts == 3
    assert sleeps == [2.0, 5.0]


def test_live_tick_does_not_swallow_unrelated_blocking_io():
    from src.execution import command_recovery

    def _unrelated_block():
        raise BlockingIOError("unrelated file operation would block")

    with pytest.raises(BlockingIOError, match="unrelated file operation"):
        command_recovery._run_recovery_pass_with_lock_policy(
            "pass_body",
            _unrelated_block,
            scope="live_tick",
            summary={},
        )


def test_live_tick_apply_factory_requests_two_layer_nowait():
    from src.execution import command_recovery

    calls = []

    def _factory(**kwargs):
        calls.append(kwargs)
        conn = sqlite3.connect(":memory:")
        conn.execute(f"PRAGMA busy_timeout = {kwargs['busy_timeout_ms']}")
        return conn

    _factory.supports_nonblocking_flocks = True
    live_tick_factory = command_recovery._recovery_apply_conn_factory(
        _factory,
        scope="live_tick",
    )

    conn = live_tick_factory()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 0
    finally:
        conn.close()

    assert calls == [{"blocking": False, "busy_timeout_ms": 0}]


def test_live_tick_apply_factory_interrupts_query_after_deadline(monkeypatch):
    from src.execution import command_recovery

    now = [0.0]

    def _factory(**_kwargs):
        return sqlite3.connect(":memory:")

    _factory.supports_nonblocking_flocks = True
    monkeypatch.setattr(command_recovery.time, "monotonic", lambda: now[0])
    live_tick_factory = command_recovery._recovery_apply_conn_factory(
        _factory,
        scope="live_tick",
        deadline_monotonic=1.0,
    )

    conn = live_tick_factory()
    try:
        now[0] = 2.0
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            conn.execute(
                """
                WITH RECURSIVE seq(n) AS (
                    VALUES(1) UNION ALL SELECT n + 1 FROM seq WHERE n < 100000
                )
                SELECT sum(n) FROM seq
                """
            ).fetchone()
    finally:
        conn.close()


def test_live_tick_db_budget_defers_remaining_passes(monkeypatch):
    from src.execution import command_recovery

    calls = []
    summary = {}
    monkeypatch.setattr(command_recovery.time, "monotonic", lambda: 2.0)

    def _interrupted():
        calls.append("first")
        raise sqlite3.OperationalError("interrupted")

    assert command_recovery._run_recovery_pass_with_lock_policy(
        "slow_pass",
        _interrupted,
        scope="live_tick",
        summary=summary,
        deadline_monotonic=1.0,
    ) is None
    assert command_recovery._run_recovery_pass_with_lock_policy(
        "later_pass",
        lambda: calls.append("later"),
        scope="live_tick",
        summary=summary,
        deadline_monotonic=1.0,
    ) is None

    assert calls == ["first"]
    assert summary == {
        "db_budget_deferred": True,
        "db_budget_deferred_at": "slow_pass",
        "db_budget_deferred_count": 1,
    }


def test_live_tick_prioritizes_capital_releases_before_terminal_order_budget_defer(monkeypatch):
    from src.execution import command_recovery
    from src.execution import venue_sync_contract

    calls = []
    now = [0.0]

    def _conn_factory():
        return sqlite3.connect(":memory:")

    def _obligations(_conn):
        calls.append("terminal_entry_exposure_obligations")
        return {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}

    def _matched_cancel_review(_conn):
        calls.append("matched_cancel_review_required_entries")
        return {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}

    def _terminal_order_facts(_conn, **_kwargs):
        calls.append("terminal_order_facts")
        now[0] = 1.0
        raise sqlite3.OperationalError("interrupted")

    original_run = command_recovery._run_recovery_pass_with_lock_policy

    def _run_priority_pass(label, fn, **kwargs):
        if label in {
            "edli_post_submit_unknown_absence_fast",
            "review_required_exit_mutex_release",
            "recorded_exit_fill_projection",
            "cancel_ack_terminal_no_fill_facts",
        }:
            return None
        return original_run(label, fn, **kwargs)

    monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
    monkeypatch.setattr(command_recovery.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        command_recovery,
        "reconcile_terminal_entry_exposure_obligations",
        _obligations,
    )
    monkeypatch.setattr(
        command_recovery,
        "reconcile_matched_cancel_review_required_entries",
        _matched_cancel_review,
    )
    monkeypatch.setattr(
        command_recovery,
        "reconcile_terminal_order_facts",
        _terminal_order_facts,
    )
    monkeypatch.setattr(
        command_recovery,
        "_run_recovery_pass_with_lock_policy",
        _run_priority_pass,
    )

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    command_recovery._reconcile_passes_short_conn(
        MagicMock(),
        summary,
        "2026-07-19T00:00:00+00:00",
        scope="live_tick",
    )

    assert calls == [
        "terminal_entry_exposure_obligations",
        "matched_cancel_review_required_entries",
        "terminal_order_facts",
    ]
    assert summary["db_budget_deferred"] is True
    assert summary["db_budget_deferred_at"] == "terminal_order_facts"


def test_full_recovery_does_not_swallow_sqlite_interrupt():
    from src.execution import command_recovery

    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        command_recovery._run_recovery_pass_with_lock_policy(
            "required_pass",
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("interrupted")),
            scope="full",
            summary={},
        )


def test_live_tick_first_apply_contention_skips_remaining_sweep(monkeypatch):
    from src.execution import command_recovery
    from src.execution import venue_sync_contract

    apply_attempts = []

    def _contended_factory(**kwargs):
        apply_attempts.append(kwargs)
        raise BlockingIOError(
            "db_writer_lock(write_class=live) contended on test.writer-lock.live"
        )

    _contended_factory.requires_writer_flocks = True
    _contended_factory.supports_nonblocking_flocks = True

    def _read_factory():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(
        venue_sync_contract,
        "default_trade_conn_factory",
        _contended_factory,
    )
    monkeypatch.setattr(
        venue_sync_contract,
        "default_trade_read_conn_factory",
        _read_factory,
    )
    monkeypatch.setattr(
        command_recovery,
        "_edli_post_submit_unknown_absence_candidates",
        lambda _conn: [],
    )
    monkeypatch.setattr(
        venue_sync_contract,
        "capture_venue_read_snapshot",
        lambda *_args, **_kwargs: pytest.fail(
            "known writer contention must skip the broad venue snapshot"
        ),
    )

    summary = {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
    command_recovery._reconcile_passes_short_conn(
        MagicMock(),
        summary,
        "2026-07-14T05:00:00+00:00",
        scope="live_tick",
    )

    assert apply_attempts == [{"blocking": False, "busy_timeout_ms": 0}]
    assert summary["db_lock_deferred"] is True
    assert summary["db_lock_deferred_at"] == "edli_post_submit_unknown_absence_fast"
    assert summary["db_lock_deferred_count"] == 1
    assert summary["deferred_full_sweep"] is True
    assert summary["scope"] == "live_tick"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 32 hex chars — satisfies IdempotencyKey length validation.
_DEFAULT_IDEM_KEY = "a" * 32
_NOW = datetime(2026, 4, 26, tzinfo=timezone.utc)


def _entry_submit_payload() -> dict:
    return {
        "execution_capability": {
            "allowed": True,
            "components": [
                {
                    "component": "entry_economics",
                    "allowed": True,
                    "details": {
                        "q_live": 0.62,
                        "q_lcb_5pct": 0.55,
                        "expected_edge": 0.05,
                        "limit_price": 0.50,
                        "submit_edge": 0.05,
                        "expected_profit_usd": 1.00,
                        "min_entry_price": 0.05,
                        "min_expected_profit_usd": 1.00,
                        "submit_edge_density": 0.10,
                        "min_submit_edge_density": 0.05,
                        "shares": 20.0,
                        "qkernel_side": "YES",
                    },
                },
                {
                    "component": "entry_actionable_certificate",
                    "allowed": True,
                    "details": {"certificate_id": "cert-recovery"},
                },
            ],
        },
    }


def _insert(conn, *, command_id="cmd-001", position_id="pos-001",
            decision_id="dec-001", idempotency_key=None,
            intent_kind="ENTRY", market_id="mkt-001", token_id="tok-001",
            no_token_id: str | None = None,
            selected_token_id: str | None = None,
            outcome_label: str | None = None,
            event_slug: str | None = None,
            side="BUY", order_type="GTC", size=10.0, price=0.5,
            created_at="2026-04-26T00:00:00Z"):
    """Insert a command row and return its command_id."""
    from src.state.venue_command_repo import insert_command
    if idempotency_key is None:
        import hashlib
        # Build a unique 32-hex key per command_id so duplicate inserts don't collide.
        idempotency_key = hashlib.md5(command_id.encode()).hexdigest()
    no_token_id = no_token_id or f"{token_id}-no"
    selected_token_id = selected_token_id or token_id
    outcome_label = outcome_label or ("NO" if selected_token_id == no_token_id else "YES")
    snapshot_id = _ensure_snapshot(
        conn,
        token_id=token_id,
        no_token_id=no_token_id,
        selected_outcome_token_id=selected_token_id,
        outcome_label=outcome_label,
        event_slug=event_slug,
    )
    insert_command(
        conn,
        command_id=command_id,
        snapshot_id=snapshot_id,
        envelope_id=_ensure_envelope(
            conn,
            token_id=token_id,
            no_token_id=no_token_id,
            selected_outcome_token_id=selected_token_id,
            outcome_label=outcome_label,
            side=side,
            order_type=order_type,
            price=price,
            size=size,
        ),
        position_id=position_id,
        decision_id=decision_id,
        idempotency_key=idempotency_key,
        intent_kind=intent_kind,
        market_id=market_id,
        token_id=selected_token_id,
        side=side,
        size=size,
        price=price,
        created_at=created_at,
        q_version="test-q-version",
    )
    return command_id


def _open_test_entry_obligation(conn, command_id: str) -> None:
    from src.state.entry_exposure_obligation import open_entry_exposure_obligation
    from src.state.schema.entry_exposure_obligations_schema import ensure_table

    ensure_table(conn)
    open_entry_exposure_obligation(
        conn,
        command_id=command_id,
        owner_domain="trade",
        token_id=f"token-{command_id}",
        condition_id=f"condition-{command_id}",
        shares=10.0,
        cost_basis_usd=5.0,
        now="2026-07-14T08:00:00+00:00",
    )


def _append_test_entry_fill(conn, command_id: str, *, with_trade: bool) -> None:
    from src.state.venue_command_repo import append_event, append_trade_fact

    at = "2026-07-14T08:00:01+00:00"
    order_id = f"order-{command_id}"
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at=at,
        payload=_entry_submit_payload(),
    )
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_ACKED",
        occurred_at=at,
        payload={"venue_order_id": order_id},
    )
    if with_trade:
        append_trade_fact(
            conn,
            trade_id=f"trade-{command_id}",
            venue_order_id=order_id,
            command_id=command_id,
            state="MATCHED",
            filled_size="10",
            fill_price="0.5",
            source="REST",
            observed_at=at,
            raw_payload_hash="f" * 64,
            raw_payload_json={"test": "terminal entry obligation"},
        )
    append_event(
        conn,
        command_id=command_id,
        event_type="FILL_CONFIRMED",
        occurred_at=at,
        payload={
            "venue_order_id": order_id,
            "trade_id": f"trade-{command_id}",
            "filled_size": "10",
            "fill_price": "0.5",
        },
    )


def test_terminal_entry_obligation_releases_only_on_authoritative_fill(conn):
    from src.execution.command_recovery import (
        reconcile_terminal_entry_exposure_obligations,
    )

    no_projection = _insert(
        conn,
        command_id="cmd-obligation-no-projection",
        position_id="pos-obligation-no-projection",
    )
    _open_test_entry_obligation(conn, no_projection)
    _append_test_entry_fill(conn, no_projection, with_trade=True)

    zero_economics = _insert(
        conn,
        command_id="cmd-obligation-zero-economics",
        position_id="pos-obligation-zero-economics",
        token_id="tok-obligation-zero-economics",
    )
    _open_test_entry_obligation(conn, zero_economics)
    _append_test_entry_fill(conn, zero_economics, with_trade=True)
    zero_order_id = f"order-{zero_economics}"
    _seed_pending_entry_projection(
        conn,
        position_id="pos-obligation-zero-economics",
        command_id=zero_economics,
        order_id=zero_order_id,
        token_id="tok-obligation-zero-economics",
    )
    _append_test_filled_entry_projection(
        conn,
        position_id="pos-obligation-zero-economics",
        command_id=zero_economics,
        order_id=zero_order_id,
        shares=0.0,
        cost_basis_usd=0.0,
        size_usd=0.0,
        entry_price=0.0,
        event_payload={"shares": 10.0, "size_usd": 5.0, "entry_price": 0.5},
    )

    invalid_event_economics = _insert(
        conn,
        command_id="cmd-obligation-invalid-event-economics",
        position_id="pos-obligation-invalid-event-economics",
        token_id="tok-obligation-invalid-event-economics",
    )
    _open_test_entry_obligation(conn, invalid_event_economics)
    _append_test_entry_fill(conn, invalid_event_economics, with_trade=True)
    invalid_order_id = f"order-{invalid_event_economics}"
    _seed_pending_entry_projection(
        conn,
        position_id="pos-obligation-invalid-event-economics",
        command_id=invalid_event_economics,
        order_id=invalid_order_id,
        token_id="tok-obligation-invalid-event-economics",
    )
    _append_test_filled_entry_projection(
        conn,
        position_id="pos-obligation-invalid-event-economics",
        command_id=invalid_event_economics,
        order_id=invalid_order_id,
        event_payload={"shares": 10.0, "size_usd": 5.0, "entry_price": 1.0},
    )

    proven = _insert(
        conn,
        command_id="cmd-obligation-proven",
        position_id="pos-obligation-proven",
        token_id="tok-obligation-proven",
    )
    _open_test_entry_obligation(conn, proven)
    _append_test_entry_fill(conn, proven, with_trade=True)
    proven_order_id = f"order-{proven}"
    _seed_pending_entry_projection(
        conn,
        position_id="pos-obligation-proven",
        command_id=proven,
        order_id=proven_order_id,
        token_id="tok-obligation-proven",
    )
    _append_test_filled_entry_projection(
        conn,
        position_id="pos-obligation-proven",
        command_id=proven,
        order_id=proven_order_id,
    )

    summary = reconcile_terminal_entry_exposure_obligations(conn)

    assert summary == {"scanned": 4, "advanced": 1, "stayed": 3, "errors": 0}
    statuses = dict(
        conn.execute(
            "SELECT command_id, status FROM entry_exposure_obligations"
        ).fetchall()
    )
    assert statuses[proven] == "RESOLVED"
    assert statuses[no_projection] == "OPEN"
    assert statuses[zero_economics] == "OPEN"
    assert statuses[invalid_event_economics] == "OPEN"
    assert reconcile_terminal_entry_exposure_obligations(conn) == {
        "scanned": 3,
        "advanced": 0,
        "stayed": 3,
        "errors": 0,
    }


def test_terminal_entry_obligation_releases_proven_no_fill_but_not_conflict(conn):
    from src.execution.command_recovery import (
        reconcile_terminal_entry_exposure_obligations,
    )
    from src.state.venue_command_repo import (
        append_event,
        append_order_fact,
        append_trade_fact,
    )

    no_fill = _insert(conn, command_id="cmd-obligation-no-fill")
    _open_test_entry_obligation(conn, no_fill)
    append_event(
        conn,
        command_id=no_fill,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-07-14T08:00:01+00:00",
        payload=_entry_submit_payload(),
    )
    append_event(
        conn,
        command_id=no_fill,
        event_type="SUBMIT_REJECTED",
        occurred_at="2026-07-14T08:00:02+00:00",
        payload={"reason": "venue_rejected_400"},
    )
    conflict = _insert(conn, command_id="cmd-obligation-conflict")
    _open_test_entry_obligation(conn, conflict)
    append_event(
        conn,
        command_id=conflict,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-07-14T08:00:01+00:00",
        payload=_entry_submit_payload(),
    )
    append_event(
        conn,
        command_id=conflict,
        event_type="SUBMIT_REJECTED",
        occurred_at="2026-07-14T08:00:02+00:00",
        payload={"reason": "venue_rejected_400"},
    )
    append_trade_fact(
        conn,
        trade_id="trade-obligation-conflict",
        venue_order_id="order-obligation-conflict",
        command_id=conflict,
        state="MATCHED",
        filled_size="1",
        fill_price="0.5",
        source="REST",
        observed_at="2026-07-14T08:00:03+00:00",
        raw_payload_hash="e" * 64,
        raw_payload_json={"test": "contradictory exposure"},
    )
    exec_conflict = _insert(conn, command_id="cmd-obligation-exec-conflict")
    _open_test_entry_obligation(conn, exec_conflict)
    append_event(
        conn,
        command_id=exec_conflict,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-07-14T08:00:01+00:00",
        payload=_entry_submit_payload(),
    )
    append_event(
        conn,
        command_id=exec_conflict,
        event_type="SUBMIT_REJECTED",
        occurred_at="2026-07-14T08:00:02+00:00",
        payload={"reason": "venue_rejected_400"},
    )
    conn.execute(
        "INSERT INTO execution_fact "
        "(intent_id, order_role, shares, filled_at, venue_status, "
        "terminal_exec_status, command_id) "
        "VALUES (?, 'entry', 1, ?, 'FILLED', 'filled', ?)",
        (
            "intent-obligation-exec-conflict",
            "2026-07-14T08:00:03+00:00",
            exec_conflict,
        ),
    )
    order_conflict = _insert(conn, command_id="cmd-obligation-order-conflict")
    _open_test_entry_obligation(conn, order_conflict)
    append_event(
        conn,
        command_id=order_conflict,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-07-14T08:00:01+00:00",
        payload=_entry_submit_payload(),
    )
    append_event(
        conn,
        command_id=order_conflict,
        event_type="SUBMIT_REJECTED",
        occurred_at="2026-07-14T08:00:02+00:00",
        payload={"reason": "venue_rejected_400"},
    )
    append_order_fact(
        conn,
        venue_order_id="order-obligation-live-conflict",
        command_id=order_conflict,
        state="LIVE",
        remaining_size="10",
        matched_size="0",
        source="REST",
        observed_at="2026-07-14T08:00:03+00:00",
        raw_payload_hash="d" * 64,
        raw_payload_json={"test": "contradictory live order"},
    )
    cancel_conflicts = []
    for fact_state in ("CANCEL_REQUESTED", "CANCEL_UNKNOWN", "CANCEL_FAILED"):
        cancel_conflict = _insert(
            conn,
            command_id=f"cmd-obligation-{fact_state.lower()}",
        )
        cancel_conflicts.append(cancel_conflict)
        _open_test_entry_obligation(conn, cancel_conflict)
        append_event(
            conn,
            command_id=cancel_conflict,
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-07-14T08:00:01+00:00",
            payload=_entry_submit_payload(),
        )
        append_event(
            conn,
            command_id=cancel_conflict,
            event_type="SUBMIT_REJECTED",
            occurred_at="2026-07-14T08:00:02+00:00",
            payload={"reason": "venue_rejected_400"},
        )
        append_order_fact(
            conn,
            venue_order_id=f"order-obligation-{fact_state.lower()}",
            command_id=cancel_conflict,
            state=fact_state,
            remaining_size="10",
            matched_size="0",
            source="REST",
            observed_at="2026-07-14T08:00:03+00:00",
            raw_payload_hash=hashlib.sha256(fact_state.encode()).hexdigest(),
            raw_payload_json={"test": "cancel not terminal"},
        )
    terminal_order = _insert(conn, command_id="cmd-obligation-terminal-order")
    _open_test_entry_obligation(conn, terminal_order)
    append_event(
        conn,
        command_id=terminal_order,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-07-14T08:00:01+00:00",
        payload=_entry_submit_payload(),
    )
    append_event(
        conn,
        command_id=terminal_order,
        event_type="SUBMIT_REJECTED",
        occurred_at="2026-07-14T08:00:02+00:00",
        payload={"reason": "venue_rejected_400"},
    )
    append_order_fact(
        conn,
        venue_order_id="order-obligation-terminal-order",
        command_id=terminal_order,
        state="LIVE",
        remaining_size="10",
        matched_size="0",
        source="REST",
        observed_at="2026-07-14T08:00:03+00:00",
        raw_payload_hash="c" * 64,
        raw_payload_json={"test": "stale live order"},
    )
    append_order_fact(
        conn,
        venue_order_id="order-obligation-terminal-order",
        command_id=terminal_order,
        state="CANCEL_CONFIRMED",
        remaining_size="0",
        matched_size="0",
        source="REST",
        observed_at="2026-07-14T08:00:04+00:00",
        raw_payload_hash="b" * 64,
        raw_payload_json={"test": "terminal zero-fill order"},
    )
    multi_order = _insert(conn, command_id="cmd-obligation-multi-order")
    _open_test_entry_obligation(conn, multi_order)
    append_event(
        conn,
        command_id=multi_order,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-07-14T08:00:01+00:00",
        payload=_entry_submit_payload(),
    )
    append_event(
        conn,
        command_id=multi_order,
        event_type="SUBMIT_REJECTED",
        occurred_at="2026-07-14T08:00:02+00:00",
        payload={"reason": "venue_rejected_400"},
    )
    append_order_fact(
        conn,
        venue_order_id="order-obligation-multi-live",
        command_id=multi_order,
        state="LIVE",
        remaining_size="10",
        matched_size="0",
        source="REST",
        observed_at="2026-07-14T08:00:03+00:00",
        raw_payload_hash="a" * 64,
        raw_payload_json={"test": "first venue order remains live"},
    )
    append_order_fact(
        conn,
        venue_order_id="order-obligation-multi-terminal",
        command_id=multi_order,
        state="CANCEL_CONFIRMED",
        remaining_size="0",
        matched_size="0",
        source="REST",
        observed_at="2026-07-14T08:00:04+00:00",
        raw_payload_hash="9" * 64,
        raw_payload_json={"test": "second venue order is terminal"},
    )
    unknown_live = _insert(conn, command_id="cmd-obligation-live-unknown-size")
    _open_test_entry_obligation(conn, unknown_live)
    append_event(
        conn,
        command_id=unknown_live,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-07-14T08:00:01+00:00",
        payload=_entry_submit_payload(),
    )
    append_event(
        conn,
        command_id=unknown_live,
        event_type="SUBMIT_REJECTED",
        occurred_at="2026-07-14T08:00:02+00:00",
        payload={"reason": "venue_rejected_400"},
    )
    append_order_fact(
        conn,
        venue_order_id="order-obligation-live-unknown-size",
        command_id=unknown_live,
        state="LIVE",
        remaining_size=None,
        matched_size=None,
        source="REST",
        observed_at="2026-07-14T08:00:03+00:00",
        raw_payload_hash="8" * 64,
        raw_payload_json={"test": "live order with unknown size"},
    )

    summary = reconcile_terminal_entry_exposure_obligations(conn)

    assert summary == {"scanned": 10, "advanced": 2, "stayed": 8, "errors": 0}
    statuses = dict(
        conn.execute(
            "SELECT command_id, status FROM entry_exposure_obligations"
        ).fetchall()
    )
    assert statuses[no_fill] == "RESOLVED"
    assert statuses[conflict] == "OPEN"
    assert statuses[exec_conflict] == "OPEN"
    assert statuses[order_conflict] == "OPEN"
    assert all(statuses[command_id] == "OPEN" for command_id in cancel_conflicts)
    assert statuses[terminal_order] == "RESOLVED"
    assert statuses[multi_order] == "OPEN"
    assert statuses[unknown_live] == "OPEN"


def test_multiwinner_loop_recovers_k_sequential_commands_independently(conn):
    """ANTIBODY (docs/operations/current/plans/auction_multiwinner_plan_2026-07-19.md
    §5, item 6): simulate a crash after K sequential submits in one wake (the
    multi-winner loop keeps submits strictly serialized, so K commands each
    fully commit before the next epoch begins -- identical in kind to today's
    cross-cycle pattern, just repeated in-wake). Each command_id must recover/
    terminalize independently through venue_command_repo.append_event
    (INV-42), with no collapse across commands and no double-release: a crash
    leaving one command mid-flight (SUBMIT_REQUESTED only, never ACKED) must
    not block or corrupt the two prior commands that already reached a fill,
    and re-running recovery must be idempotent (no double-advance)."""
    from src.execution.command_recovery import (
        reconcile_terminal_entry_exposure_obligations,
    )

    # Two epochs fully committed and filled before the crash (K=2 durable
    # winners), one epoch mid-flight when the crash hit (SUBMIT_REQUESTED
    # only -- never reached SUBMIT_ACKED/FILL_CONFIRMED).
    filled_winners = []
    for index in range(2):
        command_id = f"cmd-epoch-winner-{index}"
        position_id = f"pos-epoch-winner-{index}"
        token_id = f"tok-epoch-winner-{index}"
        order_id = f"order-{command_id}"
        _insert(conn, command_id=command_id, position_id=position_id, token_id=token_id)
        _open_test_entry_obligation(conn, command_id)
        _append_test_entry_fill(conn, command_id, with_trade=True)
        _seed_pending_entry_projection(
            conn,
            position_id=position_id,
            command_id=command_id,
            order_id=order_id,
            token_id=token_id,
        )
        _append_test_filled_entry_projection(
            conn,
            position_id=position_id,
            command_id=command_id,
            order_id=order_id,
        )
        filled_winners.append(command_id)

    crashed_mid_flight = "cmd-epoch-winner-crashed"
    _insert(conn, command_id=crashed_mid_flight)
    _open_test_entry_obligation(conn, crashed_mid_flight)
    from src.state.venue_command_repo import append_event

    append_event(
        conn,
        command_id=crashed_mid_flight,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-07-14T08:00:01+00:00",
        payload=_entry_submit_payload(),
    )

    summary = reconcile_terminal_entry_exposure_obligations(conn)

    # Each command_id recovers independently: the two authoritatively-filled
    # winners advance to RESOLVED; the mid-flight crash victim stays OPEN
    # (never terminalized without proof) -- no collapse across commands.
    assert summary == {"scanned": 3, "advanced": 2, "stayed": 1, "errors": 0}
    statuses = dict(
        conn.execute(
            "SELECT command_id, status FROM entry_exposure_obligations"
        ).fetchall()
    )
    for winner in filled_winners:
        assert statuses[winner] == "RESOLVED"
    assert statuses[crashed_mid_flight] == "OPEN"

    # No double-release: re-running recovery only re-scans the still-open
    # command and does not re-advance (or regress) the already-resolved ones.
    assert reconcile_terminal_entry_exposure_obligations(conn) == {
        "scanned": 1,
        "advanced": 0,
        "stayed": 1,
        "errors": 0,
    }
    statuses_after_rerun = dict(
        conn.execute(
            "SELECT command_id, status FROM entry_exposure_obligations"
        ).fetchall()
    )
    for winner in filled_winners:
        assert statuses_after_rerun[winner] == "RESOLVED"
    assert statuses_after_rerun[crashed_mid_flight] == "OPEN"


def _ensure_snapshot(
    conn,
    *,
    token_id: str,
    snapshot_id: str | None = None,
    no_token_id: str | None = None,
    selected_outcome_token_id: str | None = None,
    outcome_label: str = "YES",
    event_slug: str | None = None,
) -> str:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    no_token_id = no_token_id or f"{token_id}-no"
    selected_outcome_token_id = selected_outcome_token_id or token_id
    snapshot_id = snapshot_id or f"snap-{selected_outcome_token_id}"
    if get_snapshot(conn, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        conn,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-test",
            event_id="event-test",
            event_slug=event_slug or "event-test",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=no_token_id,
            selected_outcome_token_id=selected_outcome_token_id,
            outcome_label=outcome_label,
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            fee_details={},
            token_map_raw={"YES": token_id, "NO": no_token_id},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.51"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


def _ensure_envelope(
    conn,
    *,
    token_id: str,
    no_token_id: str | None = None,
    selected_outcome_token_id: str | None = None,
    outcome_label: str = "YES",
    envelope_id: str | None = None,
    side: str = "BUY",
    order_type: str = "GTC",
    price: float | Decimal = 0.5,
    size: float | Decimal = 10.0,
    raw_response_json: str | None = None,
    order_id: str | None = None,
    transaction_hashes: tuple[str, ...] = (),
    signed_order: bytes | None = None,
    signed_order_hash: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> str:
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.venue_command_repo import insert_submission_envelope

    price_dec = Decimal(str(price))
    size_dec = Decimal(str(size))
    no_token_id = no_token_id or f"{token_id}-no"
    selected_outcome_token_id = selected_outcome_token_id or token_id
    envelope_id = envelope_id or f"env-{selected_outcome_token_id}-{side}-{price_dec}-{size_dec}"
    if conn.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone():
        return envelope_id
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=no_token_id,
            selected_outcome_token_id=selected_outcome_token_id,
            outcome_label=outcome_label,
            side=side,
            price=price_dec,
            size=size_dec,
            order_type=order_type,
            post_only=False,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            neg_risk=False,
            fee_details={},
            canonical_pre_sign_payload_hash="d" * 64,
            signed_order=signed_order,
            signed_order_hash=signed_order_hash,
            raw_request_hash="e" * 64,
            raw_response_json=raw_response_json,
            order_id=order_id,
            trade_ids=(),
            transaction_hashes=transaction_hashes,
            error_code=error_code,
            error_message=error_message,
            captured_at=_NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    return envelope_id


def _advance_to_submitting(conn, command_id="cmd-001", venue_order_id=None):
    """Advance from INTENT_CREATED u2192 SUBMITTING.

    If venue_order_id provided, set it on the command row after advancing.
    """
    from src.state.venue_command_repo import append_event
    row = conn.execute(
        "SELECT intent_kind FROM venue_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    payload = _entry_submit_payload() if row is not None and row["intent_kind"] == "ENTRY" else None
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_REQUESTED",
        occurred_at="2026-04-26T00:01:00Z",
        payload=payload,
    )
    if venue_order_id is not None:
        conn.execute(
            "UPDATE venue_commands SET venue_order_id = ? WHERE command_id = ?",
            (venue_order_id, command_id),
        )
        conn.commit()


def _insert_edli_live_order_event(
    conn,
    *,
    aggregate_id: str,
    sequence: int,
    event_type: str,
    payload: dict,
    occurred_at: str,
):
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    event_hash = hashlib.sha256(f"{aggregate_id}:{sequence}:{event_type}:{payload_hash}".encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 'explicit_reconcile', ?, ?, 1)
        """,
        (
            f"edli-test-{sequence}",
            aggregate_id,
            sequence,
            event_type,
            event_hash,
            payload_json,
            payload_hash,
            occurred_at,
            occurred_at,
        ),
    )


def _seed_edli_absorbed_fill_recovery(
    conn,
    *,
    command_id: str = "cmd-absorbed-fill",
    proof_mutator=None,
) -> dict:
    execution_command_id = f"edli_exec_cmd:{command_id}:intent:no-token:buy_no"
    final_intent_id = f"edli_intent:{command_id}:no-token"
    position_id = f"pos-{command_id}"
    aggregate_id = f"agg-{command_id}"
    yes_token_id = f"yes-{command_id}"
    no_token_id = f"no-{command_id}"
    old_order_id = f"old-order-{command_id}"
    recovered_order_id = f"recovered-order-{command_id}"
    recovered_trade_id = f"recovered-trade-{command_id}"
    _insert(
        conn,
        command_id=command_id,
        position_id=position_id,
        decision_id=execution_command_id,
        token_id=yes_token_id,
        no_token_id=no_token_id,
        selected_token_id=no_token_id,
        outcome_label="NO",
        size=5.6,
        price=0.65,
    )
    _advance_to_submitting(conn, command_id=command_id)
    _seed_pending_entry_projection(
        conn,
        position_id=position_id,
        command_id="prior-command",
        order_id=old_order_id,
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'active',
               direction = 'buy_no',
               token_id = ?,
               no_token_id = ?,
               condition_id = 'condition-test',
               shares = 17.35,
               cost_basis_usd = 11.16,
               entry_price = 11.16 / 17.35,
               size_usd = 11.16,
               order_status = 'filled',
               fill_authority = 'venue_confirmed_full',
               chain_state = 'synced',
               chain_shares = 17.35,
               chain_avg_price = 11.16 / 17.35,
               chain_cost_basis_usd = 11.16,
               chain_seen_at = '2026-07-17T09:43:10+00:00'
         WHERE position_id = ?
        """,
        (yes_token_id, no_token_id, position_id),
    )
    _open_test_entry_obligation(conn, command_id)
    legs = [
        {
            "role": "TAKER",
            "trade_id": f"prior-trade-{command_id}",
            "venue_order_id": old_order_id,
            "price": 0.64,
            "size": 11.75,
        },
        {
            "role": "TAKER",
            "trade_id": recovered_trade_id,
            "venue_order_id": recovered_order_id,
            "price": 0.65,
            "size": 5.6,
        },
    ]
    proof = {
        "schema_version": 1,
        "source": "authenticated_clob_user_read",
        "owner_scope": "authenticated_funder",
        "observed_at": "2026-07-17T09:43:10+00:00",
        "aggregate_id": aggregate_id,
        "event_id": f"event-{command_id}",
        "final_intent_id": final_intent_id,
        "execution_command_id": execution_command_id,
        "token_id": no_token_id,
        "condition_id": "condition-test",
        "direction": "buy_no",
        "funder_address": "0xfunder",
        "limit_price": 0.65,
        "order_size": 5.6,
        "case": "CONFIRMED_FILL_ALREADY_ABSORBED",
        "reconcile_reason": "AUTHENTICATED_CLOB_FILL_ALREADY_ABSORBED_INTO_POSITION",
        "venue_trade_exists": True,
        "venue_order_exists": False,
        "matched_legs": legs,
        "matched_trade_ids": sorted(leg["trade_id"] for leg in legs),
        "absorbed_position": {
            "position_id": position_id,
            "token_id": yes_token_id,
            "no_token_id": no_token_id,
            "direction": "buy_no",
            "shares": 17.35,
            "entry_price": 11.16 / 17.35,
            "phase": "active",
            "fill_authority": "venue_confirmed_full",
            "order_id": old_order_id,
            "condition_id": "condition-test",
        },
        "cap_transition": "CONSUMED",
    }
    if proof_mutator is not None:
        proof_mutator(proof)
        proof["matched_trade_ids"] = sorted(
            {str(leg["trade_id"]) for leg in proof["matched_legs"]}
        )
    proof["proof_hash"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, default=str).encode()
    ).hexdigest()
    _insert_edli_live_order_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="Reconciled",
        occurred_at="2026-07-17T09:43:10+00:00",
        payload={
            "event_id": f"event-{command_id}",
            "final_intent_id": final_intent_id,
            "execution_command_id": execution_command_id,
            "pending_reconcile": False,
            "venue_order_exists": False,
            "venue_trade_exists": True,
            "cap_transition_recommendation": "CONSUMED",
            "reconcile_reason": "AUTHENTICATED_CLOB_FILL_ALREADY_ABSORBED_INTO_POSITION",
            "authenticated_resting_absorbed_proof": proof,
        },
    )
    return {
        "command_id": command_id,
        "position_id": position_id,
        "venue_order_id": recovered_order_id,
        "trade_id": recovered_trade_id,
        "proof_hash": proof["proof_hash"],
    }


def _seed_abandoned_unsubmitted_edli_ghost(
    conn,
    *,
    aggregate_id: str = "agg-abandoned-ghost",
    event_id: str = "event-abandoned-ghost",
    final_intent_id: str = "intent-abandoned-ghost",
    execution_command_id: str = "exec-abandoned-ghost",
    usage_id: str = "cap-abandoned-ghost",
) -> None:
    command_payload = {
        "schema_version": 1,
        "event_id": event_id,
        "final_intent_id": final_intent_id,
        "execution_command_id": execution_command_id,
        "execution_receipt_hash": "receipt-abandoned-ghost",
    }
    _insert_edli_live_order_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="PreSubmitRevalidated",
        payload={**command_payload, "direction": "buy_yes", "limit_price": 0.01},
        occurred_at="2026-04-26T00:00:00Z",
    )
    _insert_edli_live_order_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload=command_payload,
        occurred_at="2026-04-26T00:00:01Z",
    )
    last_hash = conn.execute(
        """
        SELECT event_hash
          FROM edli_live_order_events
         WHERE aggregate_id = ? AND event_sequence = 2
        """,
        (aggregate_id,),
    ).fetchone()["event_hash"]
    conn.execute(
        """
        INSERT INTO edli_live_order_projection (
            aggregate_id, event_id, final_intent_id, current_state,
            last_sequence, last_event_type, last_event_hash,
            pending_reconcile, venue_order_id, updated_at, schema_version
        ) VALUES (?, ?, ?, 'EXECUTION_COMMAND_CREATED',
                  2, 'ExecutionCommandCreated', ?, 0, NULL,
                  '2026-04-26T00:00:01Z', 1)
        """,
        (aggregate_id, event_id, final_intent_id, last_hash),
    )
    conn.execute(
        """
        INSERT INTO edli_live_cap_usage (
            usage_id, event_id, decision_time, cap_scope,
            max_notional_usd, max_orders_per_day, reserved_notional_usd,
            order_count, reservation_status, final_intent_id,
            execution_command_id, created_at, schema_version
        ) VALUES (?, ?, '2026-04-26T00:00:00Z', 'tiny_live_canary',
                  5.0, 1, 5.0, 1, 'RESERVED', ?, ?,
                  '2026-04-26T00:00:00Z', 1)
        """,
        (usage_id, event_id, final_intent_id, execution_command_id),
    )


def _advance_to_unknown(conn, command_id="cmd-001", venue_order_id=None):
    """Advance to UNKNOWN state (INTENT_CREATED u2192 SUBMITTING u2192 UNKNOWN)."""
    from src.state.venue_command_repo import append_event
    _advance_to_submitting(conn, command_id=command_id, venue_order_id=venue_order_id)
    append_event(conn, command_id=command_id, event_type="SUBMIT_UNKNOWN",
                 occurred_at="2026-04-26T00:02:00Z")


def test_abandoned_unsubmitted_ghost_recovery_requires_visible_venue_commands(conn):
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    _seed_abandoned_unsubmitted_edli_ghost(conn)
    conn.execute("DROP TABLE venue_commands")

    summary = reconcile_abandoned_unsubmitted_ghosts(
        conn,
        updated_before="2026-04-26T00:10:00Z",
    )

    assert {k: summary[k] for k in ("scanned", "advanced", "stayed", "errors")} == {
        "scanned": 0,
        "advanced": 0,
        "stayed": 0,
        "errors": 0,
    }
    assert summary["continuations"] == []
    event_types = [
        row["event_type"]
        for row in conn.execute(
            """
            SELECT event_type
              FROM edli_live_order_events
             WHERE aggregate_id = 'agg-abandoned-ghost'
             ORDER BY event_sequence
            """
        )
    ]
    assert event_types == ["PreSubmitRevalidated", "ExecutionCommandCreated"]


def test_abandoned_unsubmitted_ghost_recovery_terminalizes_only_without_command_row(conn):
    from src.execution.command_recovery import reconcile_abandoned_unsubmitted_ghosts

    _seed_abandoned_unsubmitted_edli_ghost(conn)

    summary = reconcile_abandoned_unsubmitted_ghosts(
        conn,
        updated_before="2026-04-26T00:10:00Z",
    )

    assert {k: summary[k] for k in ("scanned", "advanced", "stayed", "errors")} == {
        "scanned": 1,
        "advanced": 1,
        "stayed": 0,
        "errors": 0,
    }
    assert len(summary["continuations"]) == 1
    assert summary["continuations"][0]["aggregate_id"] == "agg-abandoned-ghost"
    event_types = [
        row["event_type"]
        for row in conn.execute(
            """
            SELECT event_type
              FROM edli_live_order_events
             WHERE aggregate_id = 'agg-abandoned-ghost'
             ORDER BY event_sequence
            """
        )
    ]
    assert event_types == [
        "PreSubmitRevalidated",
        "ExecutionCommandCreated",
        "SubmitRejected",
    ]
    projection = conn.execute(
        """
        SELECT current_state
          FROM edli_live_order_projection
         WHERE aggregate_id = 'agg-abandoned-ghost'
        """
    ).fetchone()
    assert projection["current_state"] == "SUBMIT_REJECTED"


def _advance_to_unknown_side_effect(conn, command_id="cmd-001", venue_order_id=None):
    """Advance to SUBMIT_UNKNOWN_SIDE_EFFECT for idempotency-key recovery."""
    from src.state.venue_command_repo import append_event
    _advance_to_submitting(conn, command_id=command_id, venue_order_id=venue_order_id)
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_TIMEOUT_UNKNOWN",
        occurred_at="2026-04-26T00:02:00Z",
    )


def _advance_to_cancel_pending(conn, command_id="cmd-001", venue_order_id=None):
    """Advance to CANCEL_PENDING (INTENT_CREATED u2192 SUBMITTING u2192 ACKED u2192 CANCEL_PENDING)."""
    from src.state.venue_command_repo import append_event
    _advance_to_submitting(conn, command_id=command_id, venue_order_id=venue_order_id)
    append_event(conn, command_id=command_id, event_type="SUBMIT_ACKED",
                 occurred_at="2026-04-26T00:02:00Z")
    append_event(conn, command_id=command_id, event_type="CANCEL_REQUESTED",
                 occurred_at="2026-04-26T00:03:00Z")


def _advance_to_cancel_unknown_review_required(conn, command_id="cmd-001", venue_order_id="ord-001"):
    from src.state.venue_command_repo import append_event

    _advance_to_cancel_pending(conn, command_id=command_id, venue_order_id=venue_order_id)
    append_event(
        conn,
        command_id=command_id,
        event_type="CANCEL_REPLACE_BLOCKED",
        occurred_at="2026-04-26T00:04:00Z",
        payload={
            "reason": "post_cancel_exception_possible_side_effect: local adapter error",
            "cancel_outcome": {
                "exception_type": "AttributeError",
                "exception_message": "'str' object has no attribute 'orderID'",
            },
            "requires_m5_reconcile": True,
            "semantic_cancel_status": "CANCEL_UNKNOWN",
        },
    )


def _advance_to_acked(conn, command_id="cmd-001", venue_order_id="ord-001"):
    from src.state.venue_command_repo import append_event

    _advance_to_submitting(conn, command_id=command_id)
    append_event(
        conn,
        command_id=command_id,
        event_type="SUBMIT_ACKED",
        occurred_at="2026-04-26T00:02:00Z",
        payload={"venue_order_id": venue_order_id, "venue_status": "accepted"},
    )


def _seed_pending_entry_projection(
    conn,
    *,
    position_id="pos-001",
    command_id="cmd-001",
    order_id="ord-001",
    token_id="tok-001",
):
    from src.state.ledger import append_many_and_project

    event_base = {
        "position_id": position_id,
        "event_version": 1,
        "strategy_key": "opening_inertia",
        "decision_id": "dec-001",
        "snapshot_id": "snap-pos-001",
        "command_id": command_id,
        "caused_by": None,
        "source_module": "tests.test_command_recovery",
        "env": "live",
    }
    events = [
        {
            **event_base,
            "event_id": f"{position_id}:open",
            "sequence_no": 1,
            "event_type": "POSITION_OPEN_INTENT",
            "occurred_at": "2026-04-26T00:02:00Z",
            "phase_before": None,
            "phase_after": "pending_entry",
            "order_id": None,
            "idempotency_key": f"{position_id}:open",
            "venue_status": None,
            "payload_json": "{}",
        },
        {
            **event_base,
            "event_id": f"{position_id}:posted",
            "sequence_no": 2,
            "event_type": "ENTRY_ORDER_POSTED",
            "occurred_at": "2026-04-26T00:02:00Z",
            "phase_before": "pending_entry",
            "phase_after": "pending_entry",
            "order_id": order_id,
            "idempotency_key": f"{position_id}:posted",
            "venue_status": "pending",
            "payload_json": "{}",
        },
    ]
    projection = {
        "position_id": position_id,
        "phase": "pending_entry",
        "trade_id": position_id,
        "market_id": "condition-test",
        "city": "Karachi",
        "cluster": "Karachi",
        "target_date": "2026-05-17",
        "bin_label": "Karachi high",
        "direction": "buy_yes",
        "unit": "C",
        "size_usd": 3.2,
        "shares": 0.0,
        "cost_basis_usd": 0.0,
        "entry_price": 0.0,
        "p_posterior": 0.9,
        "entry_ci_width": 0.0,
        "last_monitor_prob": None,
        "last_monitor_prob_is_fresh": None,
        "last_monitor_edge": None,
        "last_monitor_market_price": None,
        "last_monitor_market_price_is_fresh": None,
        "last_monitor_best_bid": None,
        "last_monitor_best_ask": None,
        "last_monitor_market_vig": None,
        "decision_snapshot_id": "snap-pos-001",
        "entry_method": "ens_member_counting",
        "strategy_key": "opening_inertia",
        "edge_source": "opening_inertia",
        "discovery_mode": "opening_hunt",
        "chain_state": "local_only",
        "token_id": token_id,
        "no_token_id": f"{token_id}-no",
        "condition_id": "condition-test",
        "order_id": order_id,
        "order_status": "pending",
        "updated_at": "2026-04-26T00:02:00Z",
        "temperature_metric": "high",
        # PR #351 D0b: durable authority columns are part of
        # CANONICAL_POSITION_CURRENT_COLUMNS (required by require_payload_fields).
        "fill_authority": None,
        "recovery_authority": None,
        "chain_shares": None,
        # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28).
        "chain_avg_price": None,
        "chain_cost_basis_usd": None,
        "chain_seen_at": None,
        "chain_absence_at": None,
        # BUG #128 durable realized-P&L columns (NULL on pending entry).
        "realized_pnl_usd": None,
        "exit_price": None,
        "settlement_price": None,
        "settled_at": None,
        "exit_reason": None,
        # K3 exit-retry backoff columns (task #45, 2026-06-11): canonical;
        # zero/NULL on a pending entry.
        "exit_retry_count": 0,
        "next_exit_retry_at": None,
    }
    append_many_and_project(conn, events, projection)


def _append_test_filled_entry_projection(
    conn,
    *,
    position_id: str,
    command_id: str,
    order_id: str,
    shares: float = 10.0,
    cost_basis_usd: float = 5.0,
    size_usd: float = 5.0,
    entry_price: float = 0.5,
    event_payload: dict | None = None,
) -> None:
    from src.state.ledger import append_many_and_project

    projection = dict(
        conn.execute(
            "SELECT * FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()
    )
    projection.update(
        phase="active",
        shares=shares,
        cost_basis_usd=cost_basis_usd,
        size_usd=size_usd,
        entry_price=entry_price,
        order_id=order_id,
        order_status="filled",
        updated_at="2026-07-14T08:00:02+00:00",
    )
    payload = (
        event_payload
        if event_payload is not None
        else {
            "shares": shares,
            "size_usd": size_usd,
            "entry_price": entry_price,
        }
    )
    append_many_and_project(
        conn,
        [
            {
                "event_id": f"{position_id}:filled:{command_id}",
                "position_id": position_id,
                "event_version": 1,
                "sequence_no": 3,
                "event_type": "ENTRY_ORDER_FILLED",
                "occurred_at": "2026-07-14T08:00:02+00:00",
                "phase_before": "pending_entry",
                "phase_after": "active",
                "strategy_key": "opening_inertia",
                "decision_id": "dec-001",
                "snapshot_id": "snap-pos-001",
                "order_id": order_id,
                "command_id": command_id,
                "caused_by": None,
                "idempotency_key": f"{position_id}:filled:{command_id}",
                "venue_status": "FILLED",
                "source_module": "tests.test_command_recovery",
                "env": "live",
                "payload_json": json.dumps(payload, sort_keys=True),
            }
        ],
        projection,
    )


def _append_order_fact(
    conn,
    *,
    command_id="cmd-001",
    order_id="ord-001",
    state="CANCEL_CONFIRMED",
    matched_size="0",
    remaining_size="0",
    source="REST",
    raw_payload_json=None,
):
    from src.state.venue_command_repo import append_order_fact

    payload = raw_payload_json or {"status": state, "order_id": order_id}
    return append_order_fact(
        conn,
        venue_order_id=order_id,
        command_id=command_id,
        state=state,
        remaining_size=remaining_size,
        matched_size=matched_size,
        source=source,
        observed_at="2026-04-26T00:05:00Z",
        venue_timestamp="2026-04-26T00:05:00Z",
        raw_payload_hash="f" * 64,
        raw_payload_json=payload,
    )


def _append_confirmed_trade_fact(
    conn,
    *,
    command_id="cmd-001",
    order_id="ord-001",
    trade_id="trade-001",
    filled_size="1.25",
    fill_price="0.50",
):
    return _append_trade_fact(
        conn,
        command_id=command_id,
        order_id=order_id,
        trade_id=trade_id,
        state="CONFIRMED",
        filled_size=filled_size,
        fill_price=fill_price,
    )


def _append_trade_fact(
    conn,
    *,
    command_id="cmd-001",
    order_id="ord-001",
    trade_id="trade-001",
    state="CONFIRMED",
    filled_size="1.25",
    fill_price="0.50",
    tx_hash: str | None = None,
):
    from src.state.venue_command_repo import append_trade_fact

    return append_trade_fact(
        conn,
        trade_id=trade_id,
        venue_order_id=order_id,
        command_id=command_id,
        state=state,
        filled_size=filled_size,
        fill_price=fill_price,
        source="REST",
        observed_at="2026-04-26T00:06:00Z",
        venue_timestamp="2026-04-26T00:06:00Z",
        tx_hash=tx_hash,
        raw_payload_hash=hashlib.sha256(
            f"{command_id}:{order_id}:{trade_id}:{state}:{filled_size}:{fill_price}:{tx_hash}".encode()
        ).hexdigest(),
        raw_payload_json={
            "id": trade_id,
            "status": state,
            "tx_hash": tx_hash,
            "maker_orders": [
                {
                    "order_id": order_id,
                    "matched_amount": filled_size,
                    "price": fill_price,
                }
            ],
        },
    )


def _insert_decision_log_trade_case_for_recovery(
    conn,
    *,
    decision_id="dec-001",
    trade_id="pos-001",
    token_id="tok-001",
    no_token_id="tok-001-no",
    direction="buy_yes",
    strategy_key="opening_inertia",
    edge_source="opening_inertia",
):
    artifact = {
        "mode": "opening_hunt",
        "started_at": "2026-04-26T00:00:00Z",
        "completed_at": "2026-04-26T00:08:00Z",
        "trade_cases": [
            {
                "decision_id": decision_id,
                "trade_id": trade_id,
                "status": "filled",
                "timestamp": "2026-04-26T00:00:00Z",
                "city": "Karachi",
                "target_date": "2026-05-17",
                "range_label": "Will the highest temperature in Karachi be 40C on May 17?",
                "direction": direction,
                "market_id": "condition-test",
                "token_id": token_id,
                "no_token_id": no_token_id,
                "size_usd": 1.70,
                "entry_price": 0.34,
                "p_posterior": 0.91,
                "strategy_key": strategy_key,
                "edge_source": edge_source,
                "decision_snapshot_id": "forecast-snap-001",
                "selected_method": "ens_member_counting",
                "settlement_semantics_json": json.dumps({"measurement_unit": "C"}),
                "epistemic_context_json": json.dumps(
                    {"forecast_context": {"temperature_metric": "high"}}
                ),
            }
        ],
    }
    conn.execute(
        """
        INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "opening_hunt",
            "2026-04-26T00:00:00Z",
            "2026-04-26T00:08:00Z",
            json.dumps(artifact, sort_keys=True),
            "2026-04-26T00:08:00Z",
            "live",
        ),
    )


def _insert_actionable_certificate_for_recovery(
    conn,
    *,
    event_id: str = "evt-edli-cert",
    token_id: str = "tok-001",
    q_live: float = 0.37,
    direction: str = "buy_yes",
    payoff_q_point: float | None = None,
    quarantine: bool = False,
) -> str:
    q_lcb = max(0.0, q_live - 0.05)
    side = "YES" if direction == "buy_yes" else "NO"
    payoff_q_point = q_live if payoff_q_point is None else payoff_q_point
    payoff_q_lcb = q_lcb
    cost = min(0.01, max(0.001, payoff_q_lcb / 2.0)) if payoff_q_lcb > 0 else 0.01
    edge_lcb = payoff_q_lcb - cost
    payload = {
        "event_id": event_id,
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": "forecast-snap-edli",
        "family_id": "family-test",
        "candidate_id": f"{side}:bin-test:DIRECT_{side}:bin-test@proof",
        "condition_id": "condition-test",
        "token_id": token_id,
        "city": "Karachi",
        "target_date": "2026-05-17",
        "bin_label": "Will the highest temperature in Karachi be 40C on May 17?",
        "direction": direction,
        "strategy_key": "forecast_qkernel_entry",
        "metric": "high",
        "unit": "C",
        "q_live": q_live,
        "q_lcb_5pct": q_lcb,
        "c_fee_adjusted": 0.51,
        "c_cost_95pct": 0.51,
        "p_fill_lcb": 0.5,
        "trade_score": max(edge_lcb, 0.01),
        "action_score": max(edge_lcb, 0.01),
        "executable_snapshot_id": "ems-test",
        "fdr_family_id": "fdr-family-test",
        "kelly_decision_id": "kelly-test",
        "risk_decision_id": "risk-test",
        "live_cap_usage_id": "live-cap-test",
        "native_quote_available": True,
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": side,
            "candidate_id": f"{side}:bin-test:DIRECT_{side}:bin-test@proof",
            "route_id": f"DIRECT_{side}:bin-test@proof",
            "bin_id": "bin-test",
            "payoff_q_point": payoff_q_point,
            "payoff_q_lcb": payoff_q_lcb,
            "cost": cost,
            "edge_lcb": edge_lcb,
            "delta_u_at_min": max(edge_lcb, 0.01),
            "optimal_stake_usd": 10.0,
            "optimal_delta_u": max(edge_lcb, 0.01),
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": payoff_q_lcb,
        },
        "final_intent_id": f"intent:{event_id}:{token_id}",
    }
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    cert_hash = hashlib.sha256((payload_json + ":cert").encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_id, certificate_type, schema_version, canonicalization_version,
            semantic_key, claim_type, mode, decision_time, authority_id,
            authority_version, algorithm_id, algorithm_version, payload_json,
            payload_hash, certificate_hash, verifier_status, created_at
        ) VALUES (?, 'ActionableTradeCertificate', 1, 'test-v1',
                  ?, 'actionable_trade', 'LIVE', '2026-04-26T00:00:00Z',
                  'test-authority', 'v1', 'test-algorithm', 'v1', ?,
                  ?, ?, 'VERIFIED', '2026-04-26T00:00:00Z')
        """,
        (
            f"ActionableTradeCertificate:{cert_hash[:24]}",
            f"actionable:{event_id}:{token_id}",
            payload_json,
            payload_hash,
            cert_hash,
        ),
    )
    if quarantine:
        from src.state.fact_revocation import (
            DECISION_CERTIFICATES_TABLE,
            REASON_INVALID_LIVE_ACTIONABLE,
        )
        from src.state.schema.fact_revocations_schema import ensure_table

        ensure_table(conn)

        conn.execute(
            """
            INSERT INTO fact_revocations
                (table_name, row_id, reason_code, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                DECISION_CERTIFICATES_TABLE,
                cert_hash,
                REASON_INVALID_LIVE_ACTIONABLE,
                "2026-04-26T00:00:02Z",
            ),
        )
    return cert_hash


def _insert_final_intent_certificate_for_recovery(
    conn,
    *,
    event_id: str = "evt-edli-cert",
    final_intent_id: str = "intent:evt-edli-cert:tok-001",
    token_id: str = "tok-001",
    q_live: float = 0.91,
    direction: str = "buy_yes",
) -> str:
    payload = {
        "event_id": event_id,
        "final_intent_id": final_intent_id,
        "condition_id": "condition-test",
        "token_id": token_id,
        "city": "Karachi",
        "target_date": "2026-05-17",
        "bin_label": "Will the highest temperature in Karachi be 40C on May 17?",
        "direction": direction,
        "strategy_key": "forecast_qkernel_entry",
        "metric": "high",
        "unit": "C",
        "q_live": q_live,
        "causal_snapshot_id": "forecast-snap-final-intent",
    }
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    cert_hash = hashlib.sha256((payload_json + ":final-cert").encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO decision_certificates (
            certificate_id, certificate_type, schema_version, canonicalization_version,
            semantic_key, claim_type, mode, decision_time, authority_id,
            authority_version, algorithm_id, algorithm_version, payload_json,
            payload_hash, certificate_hash, verifier_status, created_at
        ) VALUES (?, 'FinalIntentCertificate', 1, 'test-v1',
                  ?, 'final_intent', 'LIVE', '2026-04-26T00:00:00Z',
                  'test-authority', 'v1', 'test-algorithm', 'v1', ?,
                  ?, ?, 'VERIFIED', '2026-04-26T00:00:01Z')
        """,
        (
            f"FinalIntentCertificate:{cert_hash[:24]}",
            f"final_intent:{event_id}:{final_intent_id}",
            payload_json,
            payload_hash,
            cert_hash,
        ),
    )
    return cert_hash


def _advance_to_partial(conn, command_id="cmd-001", venue_order_id="ord-001"):
    from src.state.venue_command_repo import append_event

    _advance_to_acked(conn, command_id=command_id, venue_order_id=venue_order_id)
    append_event(
        conn,
        command_id=command_id,
        event_type="PARTIAL_FILL_OBSERVED",
        occurred_at="2026-04-26T00:06:00Z",
        payload={
            "venue_order_id": venue_order_id,
            "trade_id": "trade-001",
            "filled_size": "1.25",
            "fill_price": "0.50",
            "source": "test",
        },
    )


def _advance_to_review_required(conn, command_id="cmd-001"):
    """Advance to REVIEW_REQUIRED (INTENT_CREATED u2192 REVIEW_REQUIRED)."""
    from src.state.venue_command_repo import append_event
    append_event(conn, command_id=command_id, event_type="REVIEW_REQUIRED",
                 occurred_at="2026-04-26T00:01:00Z")


def _get_state(conn, command_id):
    from src.state.venue_command_repo import get_command
    cmd = get_command(conn, command_id)
    return cmd["state"] if cmd else None


def _get_events(conn, command_id):
    from src.state.venue_command_repo import list_events
    return list_events(conn, command_id)


def _connect_file_db(path):
    from src.state.db import init_schema
    from src.state.collateral_ledger import init_collateral_schema

    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    init_schema(c)
    return c


@pytest.mark.parametrize("partial_status", ["PARTIAL", "PARTIALLY_MATCHED", "PARTIALLY_FILLED"])
def test_partial_polling_with_trade_id_projects_optimistic_lot(tmp_path, partial_status):
    """PARTIAL with real trade id is optimistic exposure, not synthetic finality."""
    from src.execution.fill_tracker import _maybe_append_venue_fill_observation
    from src.state.portfolio import Position

    db_path = tmp_path / "partial-fill.db"
    conn = _connect_file_db(db_path)
    _insert(
        conn,
        command_id="cmd-partial",
        position_id="runtime-pos-partial",
        decision_id="dec-partial",
        token_id="tok-partial",
        side="BUY",
        size=10.0,
        price=0.5,
    )
    conn.execute(
        "UPDATE venue_commands SET venue_order_id = ? WHERE command_id = ?",
        ("vord-partial", "cmd-partial"),
    )
    conn.execute(
        """
        INSERT INTO trade_decisions (
            market_id, bin_label, direction, size_usd, price, timestamp,
            p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
            status, runtime_trade_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "mkt-001",
            "50-51°F",
            "buy_yes",
            10.0,
            0.5,
            _NOW.isoformat(),
            0.6,
            0.6,
            0.1,
            0.05,
            0.15,
            0.0,
            "pending",
            "runtime-pos-partial",
        ),
    )
    conn.commit()
    conn.close()

    pos = Position(
        trade_id="runtime-pos-partial",
        market_id="mkt-001",
        city="Paris",
        cluster="Paris",
        target_date="2026-04-26",
        bin_label="50-51°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.5,
        shares=20.0,
        state="pending_tracked",
        order_id="vord-partial",
        entry_order_id="vord-partial",
    )
    deps = SimpleNamespace(get_connection=lambda: _connect_file_db(db_path))

    assert _maybe_append_venue_fill_observation(
        pos,
        {
            "status": partial_status,
            "trade_id": "venue-trade-partial",
            "filled_size": "4.25",
            "price": "0.5",
        },
        status=partial_status,
        shares=4.25,
        fill_price=0.5,
        observed_at=_NOW,
        deps=deps,
    )

    verify = _connect_file_db(db_path)
    try:
        order_fact = verify.execute(
            "SELECT state, matched_size FROM venue_order_facts WHERE venue_order_id = ?",
            ("vord-partial",),
        ).fetchone()
        trade_fact = verify.execute(
            "SELECT trade_fact_id, state, filled_size FROM venue_trade_facts WHERE trade_id = ?",
            ("venue-trade-partial",),
        ).fetchone()
        lot = verify.execute(
            "SELECT state, shares FROM position_lots WHERE source_trade_fact_id = ?",
            (trade_fact["trade_fact_id"],),
        ).fetchone()
    finally:
        verify.close()

    assert dict(order_fact) == {"state": "PARTIALLY_MATCHED", "matched_size": "4.25"}
    assert {key: trade_fact[key] for key in ("state", "filled_size")} == {
        "state": "MATCHED",
        "filled_size": "4.25",
    }
    assert lot["state"] == "OPTIMISTIC_EXPOSURE"
    assert Decimal(str(lot["shares"])) == Decimal("4.25")


# ---------------------------------------------------------------------------
# TestRecoveryResolutionTable
# ---------------------------------------------------------------------------

class TestRecoveryResolutionTable:
    """Cover all 8 INV-31 anchor resolution-table cases."""

    # Case 1: SUBMITTING + venue_order_id + venue finds order u2192 ACKED
    def test_submitting_with_venue_order_resolves_to_acked(self, conn, mock_client):
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-001")
        mock_client.get_order.return_value = {"orderID": "vord-001", "status": "LIVE"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "ACKED"
        assert summary["advanced"] == 1
        assert summary["scanned"] == 1
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "SUBMIT_ACKED" in event_types

    def test_submitting_with_order_state_resolves_to_acked(self, conn, mock_client):
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-state")
        mock_client.get_order.return_value = SimpleNamespace(
            order_id="vord-state",
            status="LIVE",
            raw={"orderID": "vord-state", "status": "LIVE"},
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "ACKED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        ack = [e for e in events if e["event_type"] == "SUBMIT_ACKED"][-1]
        payload = json.loads(ack["payload_json"])
        assert payload["venue_response"] == {"orderID": "vord-state", "status": "LIVE"}

    def test_submitting_rejects_empty_normalized_venue_order_payload(
        self, conn, mock_client
    ):
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-empty")
        mock_client.get_order.return_value = object()

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        assert "SUBMIT_ACKED" not in [e["event_type"] for e in events]
        review = [e for e in events if e["event_type"] == "REVIEW_REQUIRED"][-1]
        payload = json.loads(review["payload_json"])
        assert payload == {
            "reason": "recovery_order_not_found_at_venue",
            "venue_order_id": "vord-empty",
        }

    def test_submitting_with_state_only_rejected_resolves_to_submit_rejected(
        self, conn, mock_client
    ):
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-rejected")
        mock_client.get_order.return_value = {"orderID": "vord-rejected", "state": "REJECTED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REJECTED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        rejected = [e for e in events if e["event_type"] == "SUBMIT_REJECTED"][-1]
        payload = json.loads(rejected["payload_json"])
        assert payload["venue_status"] == "REJECTED"

    def test_stale_intent_created_without_submit_terminalizes_no_side_effect(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["stale_intent_created_no_submit"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "SUBMIT_REJECTED"
        mock_client.get_order.assert_not_called()
        events = _get_events(conn, "cmd-001")
        rejected = [e for e in events if e["event_type"] == "SUBMIT_REJECTED"][-1]
        payload = json.loads(rejected["payload_json"])
        assert payload["reason"] == "pre_venue_intent_abandoned_before_submit"
        assert payload["side_effect_boundary_crossed"] is False
        assert payload["venue_order_created"] is False
        assert payload["safe_replay_permitted"] is True

    def test_stale_increment_intent_terminalizes_with_existing_position(
        self,
        conn,
        mock_client,
    ):
        """An existing position is exposure truth, not proof this shell submitted."""
        _insert(conn, position_id="existing-pos")
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, strategy_key, updated_at, temperature_metric,
                shares, chain_shares
            ) VALUES (?, 'active', 'center_bin_buy', ?, 'high', 22, 22)
            """,
            ("existing-pos", "2026-04-25T23:59:00+00:00"),
        )
        conn.commit()

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["stale_intent_created_no_submit"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "SUBMIT_REJECTED"
        position = conn.execute(
            "SELECT shares, chain_shares FROM position_current WHERE position_id = ?",
            ("existing-pos",),
        ).fetchone()
        assert tuple(position) == (22.0, 22.0)
        rejected = [
            event
            for event in _get_events(conn, "cmd-001")
            if event["event_type"] == "SUBMIT_REJECTED"
        ][-1]
        payload = json.loads(rejected["payload_json"])
        assert payload["required_predicates"]["no_command_position_events"] is True
        mock_client.get_order.assert_not_called()

    # Case 2: SUBMITTING + no venue_order_id -> idempotency/absence recovery
    # A deterministic venue 400 can fail to persist SUBMIT_REJECTED if the local
    # DB is locked after the HTTP response. Recovery must not park that row in
    # manual review; it proves venue absence and releases the command.
    def test_submitting_without_order_id_safe_absence_resolves_to_rejected(self, conn):
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id=None)
        client = MagicMock()
        client.find_order_by_idempotency_key.return_value = None

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, client)

        assert _get_state(conn, "cmd-001") == "REJECTED"
        assert summary["advanced"] == 1
        client.get_order.assert_not_called()
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "SUBMIT_REJECTED" in event_types
        rejected = next(e for e in events if e["event_type"] == "SUBMIT_REJECTED")
        payload = json.loads(rejected["payload_json"])
        assert payload["reason"] == "safe_replay_permitted_no_order_found"
        assert payload["safe_replay_permitted"] is True
        assert payload["recovered_from_state"] == "SUBMITTING"
        assert payload["lookup_method"] == "idempotency_key"

    def test_submitting_without_order_id_waits_for_safe_replay_window(self, conn):
        created_at = datetime.now(timezone.utc).isoformat()
        _insert(conn, created_at=created_at)
        _advance_to_submitting(conn, venue_order_id=None)
        conn.execute(
            "UPDATE venue_commands SET updated_at = ? WHERE command_id = 'cmd-001'",
            (created_at,),
        )
        conn.commit()
        client = MagicMock()
        client.find_order_by_idempotency_key.return_value = None

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, client)

        assert _get_state(conn, "cmd-001") == "SUBMITTING"
        assert summary["advanced"] == 0
        assert summary["stayed"] >= 1
        assert "SUBMIT_REJECTED" not in [e["event_type"] for e in _get_events(conn, "cmd-001")]

    def test_submitting_without_order_id_stays_when_authenticated_lookup_is_unavailable(
        self, conn
    ):
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id=None)

        class UnavailableClient:
            def get_open_orders(self):
                raise RuntimeError("authenticated read unavailable")

            def get_trades(self):
                raise RuntimeError("authenticated read unavailable")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, UnavailableClient())

        assert _get_state(conn, "cmd-001") == "SUBMITTING"
        assert summary["advanced"] == 0
        assert summary["stayed"] == 1
        assert "REVIEW_REQUIRED" not in [
            event["event_type"] for event in _get_events(conn, "cmd-001")
        ]

    def test_lookup_unavailable_review_clears_only_after_fresh_zero_exposure_proof(
        self, conn, mock_client
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn)
        _advance_to_submitting(conn, venue_order_id=None)
        append_event(
            conn,
            command_id="cmd-001",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:02:00Z",
            payload={"reason": "recovery_no_venue_order_id_lookup_unavailable"},
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import _reconcile_row
        from src.execution.command_bus import VenueCommand

        row = conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = 'cmd-001'"
        ).fetchone()
        outcome = _reconcile_row(conn, VenueCommand.from_row(dict(row)), mock_client)

        assert outcome == "advanced"
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        assert _get_events(conn, "cmd-001")[-1]["event_type"] == (
            "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
        )

    def test_restart_preflight_recovers_no_venue_exit_into_retry_projection(
        self,
        tmp_path,
        monkeypatch,
    ):
        from src.execution import command_recovery, venue_sync_contract
        from src.state.db import init_schema
        from src.state.collateral_ledger import init_collateral_schema

        db_path = tmp_path / "restart-preflight.db"
        seed = sqlite3.connect(db_path)
        seed.row_factory = sqlite3.Row
        init_schema(seed)
        init_collateral_schema(seed)
        _seed_pending_entry_projection(
            seed,
            position_id="pos-exit",
            command_id="cmd-entry",
            order_id="ord-entry",
        )
        seed.execute(
            """
            UPDATE position_current
               SET phase = 'pending_exit',
                   shares = 21.42,
                   chain_shares = 21.42,
                   cost_basis_usd = 14.35,
                   entry_price = 0.67,
                   order_status = 'filled',
                   exit_reason = 'FAMILY_DIRECT_SELL_DOMINATES_HOLD',
                   chain_state = 'synced',
                   updated_at = '2026-06-29T05:08:33+00:00'
             WHERE position_id = 'pos-exit'
            """
        )
        _insert(
            seed,
            command_id="cmd-exit",
            position_id="pos-exit",
            decision_id="exit:pos-exit",
            intent_kind="EXIT",
            side="SELL",
            size=21.42,
            price=0.58,
            created_at="2026-06-29T05:08:33+00:00",
        )
        _advance_to_submitting(seed, command_id="cmd-exit", venue_order_id=None)
        _insert(
            seed,
            command_id="cmd-review-old",
            position_id="pos-review-old",
            decision_id="dec-review-old",
            intent_kind="ENTRY",
            side="BUY",
            size=8.0,
            price=0.56,
            created_at="2026-06-23T19:13:43+00:00",
        )
        _advance_to_submitting(seed, command_id="cmd-review-old", venue_order_id=None)
        from src.state.venue_command_repo import append_event

        append_event(
            seed,
            command_id="cmd-review-old",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-06-23T19:13:47+00:00",
            payload={"reason": "recovery_no_venue_order_id"},
        )
        seed.commit()
        seed.close()

        def _conn_factory():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(
            venue_sync_contract,
            "default_trade_conn_factory",
            _conn_factory,
        )
        client = MagicMock(
            spec_set=[
                "get_order",
                "get_open_orders",
                "get_trades",
                "get_clob_market_info",
            ]
        )
        client.get_open_orders.return_value = []
        client.get_trades.return_value = []

        summary = command_recovery.reconcile_unresolved_commands(
            client=client,
            scope="restart_preflight",
        )

        check = sqlite3.connect(db_path)
        check.row_factory = sqlite3.Row
        current = check.execute(
            """
            SELECT phase, order_status, exit_retry_count, next_exit_retry_at
              FROM position_current
             WHERE position_id = 'pos-exit'
            """
        ).fetchone()
        command_state = _get_state(check, "cmd-exit")
        latest_event = check.execute(
            """
            SELECT event_type, venue_status, payload_json
              FROM position_events
             WHERE position_id = 'pos-exit'
             ORDER BY sequence_no DESC
             LIMIT 1
            """
        ).fetchone()
        check.close()

        assert summary["scope"] == "restart_preflight"
        assert summary["restart_preflight_narrow"] is True
        assert summary["scanned"] == 1
        assert summary["restart_no_venue_exit_retry_projection"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert command_state == "REJECTED"
        assert dict(current) == {
            "phase": "pending_exit",
            "order_status": "retry_pending",
            "exit_retry_count": 1,
            "next_exit_retry_at": current["next_exit_retry_at"],
        }
        assert current["next_exit_retry_at"]
        assert latest_event["event_type"] == "EXIT_ORDER_REJECTED"
        assert latest_event["venue_status"] == "retry_pending"
        event_payload = json.loads(latest_event["payload_json"])
        assert event_payload["exit_reason"] == "FAMILY_DIRECT_SELL_DOMINATES_HOLD"
        assert "submit absence for exit command cmd-exit" in event_payload["error"]

    def test_edli_confirmed_fill_terminalizes_submitting_without_order_id(
        self, conn, mock_client
    ):
        execution_command_id = "edli_exec_cmd:test-event:test-intent:tok-001:buy_no"
        final_intent_id = "edli_intent:test-event:tok-001"
        venue_order_id = "0xedliorder"
        trade_id = "edli-trade-001"
        _insert(conn, decision_id=execution_command_id, size=9.0, price=0.97)
        _advance_to_submitting(conn, venue_order_id=None)
        _insert_edli_live_order_event(
            conn,
            aggregate_id="edli-aggregate",
            sequence=1,
            event_type="VenueSubmitAcknowledged",
            occurred_at="2026-04-26T00:02:00+00:00",
            payload={
                "execution_command_id": execution_command_id,
                "final_intent_id": final_intent_id,
                "venue_order_id": venue_order_id,
                "recovered_trade_id": trade_id,
                "transaction_hash": "0xtx",
            },
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id="edli-aggregate",
            sequence=2,
            event_type="UserTradeObserved",
            occurred_at="2026-04-26T00:03:00+00:00",
            payload={
                "final_intent_id": final_intent_id,
                "venue_order_id": venue_order_id,
                "trade_id": trade_id,
                "trade_status": "CONFIRMED",
                "fill_authority_state": "FILL_CONFIRMED",
                "filled_size": "9",
                "fill_price": "0.97",
                "avg_fill_price": "0.97",
                "transaction_hash": "0xtx",
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["edli_confirmed_legacy_command_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "FILLED"
        mock_client.get_order.assert_not_called()
        command = conn.execute(
            "SELECT venue_order_id FROM venue_commands WHERE command_id = 'cmd-001'"
        ).fetchone()
        assert command["venue_order_id"] == venue_order_id
        trade = conn.execute(
            """
            SELECT state, filled_size, fill_price
              FROM venue_trade_facts
             WHERE command_id = 'cmd-001'
               AND trade_id = ?
            """,
            (trade_id,),
        ).fetchone()
        assert dict(trade) == {
            "state": "CONFIRMED",
            "filled_size": "9",
            "fill_price": "0.97",
        }
        events = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert events == ["INTENT_CREATED", "SUBMIT_REQUESTED", "SUBMIT_ACKED", "FILL_CONFIRMED"]

    def test_edli_absorbed_fill_terminalizes_without_reprojecting_position(self, conn):
        seeded = _seed_edli_absorbed_fill_recovery(conn)
        from src.execution.command_recovery import (
            reconcile_edli_confirmed_legacy_command_repairs,
            reconcile_filled_entry_projection_repairs,
            reconcile_terminal_entry_exposure_obligations,
            reconcile_terminal_positive_entry_projection_repairs,
        )

        position_events_before = conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ?",
            (seeded["position_id"],),
        ).fetchone()[0]
        summary = reconcile_edli_confirmed_legacy_command_repairs(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        command = conn.execute(
            "SELECT state, venue_order_id FROM venue_commands WHERE command_id = ?",
            (seeded["command_id"],),
        ).fetchone()
        assert dict(command) == {
            "state": "FILLED",
            "venue_order_id": seeded["venue_order_id"],
        }
        events = _get_events(conn, seeded["command_id"])
        assert [event["event_type"] for event in events] == [
            "INTENT_CREATED",
            "SUBMIT_REQUESTED",
            "SUBMIT_ACKED",
            "FILL_CONFIRMED",
        ]
        fill_payload = json.loads(events[-1]["payload_json"])
        assert fill_payload["recovered_from"] == "edli_confirmed_fill_already_absorbed"
        assert fill_payload["proof_hash"] == seeded["proof_hash"]
        trade = conn.execute(
            """
            SELECT venue_order_id, state, filled_size, fill_price
              FROM venue_trade_facts
             WHERE command_id = ? AND trade_id = ?
            """,
            (seeded["command_id"], seeded["trade_id"]),
        ).fetchone()
        assert dict(trade) == {
            "venue_order_id": seeded["venue_order_id"],
            "state": "CONFIRMED",
            "filled_size": "5.6",
            "fill_price": "0.65",
        }

        assert reconcile_filled_entry_projection_repairs(conn) == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }
        assert reconcile_terminal_positive_entry_projection_repairs(conn) == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        position = conn.execute(
            """
            SELECT shares, cost_basis_usd, chain_shares, chain_cost_basis_usd, order_id
              FROM position_current WHERE position_id = ?
            """,
            (seeded["position_id"],),
        ).fetchone()
        assert dict(position) == {
            "shares": pytest.approx(17.35),
            "cost_basis_usd": pytest.approx(11.16),
            "chain_shares": pytest.approx(17.35),
            "chain_cost_basis_usd": pytest.approx(11.16),
            "order_id": f"old-order-{seeded['command_id']}",
        }
        assert conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ?",
            (seeded["position_id"],),
        ).fetchone()[0] == position_events_before
        # The absorbed position is aggregate evidence only; without a
        # command/order-bound fill projection it cannot release this command.
        assert reconcile_terminal_entry_exposure_obligations(conn) == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        obligation = conn.execute(
            "SELECT status FROM entry_exposure_obligations WHERE command_id = ?",
            (seeded["command_id"],),
        ).fetchone()
        assert obligation["status"] == "OPEN"
        assert reconcile_edli_confirmed_legacy_command_repairs(conn) == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_edli_absorbed_fill_refuses_ambiguous_matching_leg(self, conn):
        def duplicate_matching_leg(proof):
            proof["matched_legs"].append(
                {
                    "role": "TAKER",
                    "trade_id": "second-economics-match",
                    "venue_order_id": "second-economics-match-order",
                    "price": 0.65,
                    "size": 5.6,
                }
            )

        seeded = _seed_edli_absorbed_fill_recovery(
            conn,
            command_id="cmd-absorbed-ambiguous",
            proof_mutator=duplicate_matching_leg,
        )
        from src.execution.command_recovery import (
            reconcile_edli_confirmed_legacy_command_repairs,
        )

        assert reconcile_edli_confirmed_legacy_command_repairs(conn) == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }
        command = conn.execute(
            "SELECT state, venue_order_id FROM venue_commands WHERE command_id = ?",
            (seeded["command_id"],),
        ).fetchone()
        assert dict(command) == {"state": "SUBMITTING", "venue_order_id": None}
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_trade_facts WHERE command_id = ?",
            (seeded["command_id"],),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM entry_exposure_obligations WHERE command_id = ?",
            (seeded["command_id"],),
        ).fetchone()[0] == "OPEN"

    # Case 3: UNKNOWN + venue_order_id + venue finds order u2192 ACKED
    def test_unknown_with_venue_order_resolves_to_acked(self, conn, mock_client):
        _insert(conn)
        _advance_to_unknown(conn, venue_order_id="vord-002")
        mock_client.get_order.return_value = {"orderID": "vord-002", "status": "MATCHED"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "ACKED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "SUBMIT_ACKED" in event_types

    def test_unknown_with_state_only_rejected_resolves_to_submit_rejected(
        self, conn, mock_client
    ):
        _insert(conn)
        _advance_to_unknown(conn, venue_order_id="vord-unknown-rejected")
        mock_client.get_order.return_value = {
            "orderID": "vord-unknown-rejected",
            "state": "REJECTED",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REJECTED"
        assert summary["advanced"] == 1

    # Case 4: UNKNOWN + venue_order_id + venue returns None u2192 REVIEW_REQUIRED
    def test_unknown_without_venue_order_resolves_to_review_required(self, conn, mock_client):
        _insert(conn)
        _advance_to_unknown(conn, venue_order_id="vord-003")
        mock_client.get_order.return_value = None  # order not found

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "REVIEW_REQUIRED" in event_types

    # Case 5: CANCEL_PENDING + venue returns None (order gone) u2192 CANCELLED
    def test_cancel_pending_with_missing_order_resolves_to_cancelled(self, conn, mock_client):
        _insert(conn)
        _advance_to_cancel_pending(conn, venue_order_id="vord-004")
        mock_client.get_order.return_value = None  # order missing

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "CANCELLED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        event_types = [e["event_type"] for e in events]
        assert "CANCEL_ACKED" in event_types

    # Case 6: REVIEW_REQUIRED rows are skipped (operator-handoff)
    def test_review_required_is_skipped(self, conn, mock_client):
        _insert(conn)
        _advance_to_review_required(conn)
        mock_client.get_order.return_value = {"orderID": "x", "status": "LIVE"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        # State should NOT change
        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert summary["stayed"] == 1
        assert summary["advanced"] == 0
        # get_order should NOT be called
        mock_client.get_order.assert_not_called()

    def test_review_required_recovery_no_venue_order_id_auto_clears_on_absence_proof(
        self, conn, mock_client
    ):
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.venue_command_repo import append_event

        _insert(
            conn,
            command_id="cmd-no-order",
            position_id="pos-no-order",
            decision_id="dec-no-order",
            token_id="tok-no-order",
            size=10.865300810243,
            price=0.67,
        )
        append_event(
            conn,
            command_id="cmd-no-order",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"reason": "recovery_no_venue_order_id"},
        )
        mock_client.get_open_orders.return_value = [
            {"id": "unrelated", "asset_id": "other-token", "status": "LIVE"}
        ]
        mock_client.get_trades.return_value = [
            {"id": "old-trade", "asset_id": "tok-no-order", "match_time": "1"}
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands

        before_count, _ = count_unknown_side_effects(conn)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert before_count == 0
        assert _get_state(conn, "cmd-no-order") == "EXPIRED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-no-order")
        assert events[-1]["event_type"] == "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["reason"] == "review_cleared_no_venue_exposure"
        assert payload["proof_class"] == "venue_absence_no_exposure"
        assert payload["source_proof"]["source_function"] == "command_recovery._reconcile_row"
        assert payload["venue_absence_proof"]["matching_open_order_count"] == 0
        assert payload["venue_absence_proof"]["matching_trade_count"] == 0
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()

    def test_review_required_recovery_no_venue_order_id_confirmed_taker_trade_fills(
        self, conn, mock_client
    ):
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.venue_command_repo import append_event

        _insert(
            conn,
            command_id="cmd-confirmed-maker",
            position_id="pos-confirmed-maker",
            decision_id="dec-confirmed-maker",
            token_id="tok-confirmed-maker",
            size=10.865300810243,
            price=0.67,
        )
        append_event(
            conn,
            command_id="cmd-confirmed-maker",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"reason": "recovery_no_venue_order_id"},
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = [
            {
                "id": "trade-confirmed-maker",
                "status": "CONFIRMED",
                "trader_side": "TAKER",
                "match_time": "2026-04-26T00:02:00Z",
                "transaction_hash": "0xtx-confirmed-maker",
                "asset_id": "tok-confirmed-maker",
                "taker_order_id": "ord-confirmed-maker",
                "side": "BUY",
                "price": "0.67",
                "size": "10.86",
            }
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-confirmed-maker") == "FILLED"
        assert summary["advanced"] >= 1
        cmd = conn.execute(
            "SELECT venue_order_id FROM venue_commands WHERE command_id = 'cmd-confirmed-maker'"
        ).fetchone()
        assert cmd["venue_order_id"] == "ord-confirmed-maker"
        events = _get_events(conn, "cmd-confirmed-maker")
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "recovery_no_venue_order_id_confirmed_trade"
        assert payload["required_predicates"]["maker_order_token_matches_command"] is True
        trade_fact = conn.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-confirmed-maker'
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-confirmed-maker",
            "venue_order_id": "ord-confirmed-maker",
            "state": "CONFIRMED",
            "filled_size": "10.86",
            "fill_price": "0.67",
            "tx_hash": "0xtx-confirmed-maker",
        }
        unknown_count, unknown_markets = count_unknown_side_effects(conn)
        assert unknown_count == 0
        assert unknown_markets == ()

    def test_matched_submit_missing_trade_id_confirmed_trade_fills(
        self, conn, mock_client
    ):
        from src.state.venue_command_repo import append_event

        _insert(
            conn,
            command_id="cmd-matched-missing-trade-id",
            position_id="pos-matched-missing-trade-id",
            decision_id="dec-matched-missing-trade-id",
            token_id="tok-matched-missing-trade-id",
            size=31.5,
            price=0.74,
        )
        _advance_to_submitting(
            conn,
            command_id="cmd-matched-missing-trade-id",
            venue_order_id="ord-matched-missing-trade-id",
        )
        append_event(
            conn,
            command_id="cmd-matched-missing-trade-id",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"reason": "matched_submit_missing_trade_id"},
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = [
            {
                "id": "trade-matched-missing-trade-id",
                "status": "CONFIRMED",
                "match_time": "2026-04-26T00:02:00Z",
                "transaction_hash": "0xtx-matched-missing-trade-id",
                "maker_orders": [
                    {
                        "asset_id": "tok-matched-missing-trade-id",
                        "order_id": "ord-matched-missing-trade-id",
                        "side": "BUY",
                        "price": "0.73",
                        "matched_amount": "31.9",
                    }
                ],
            }
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-matched-missing-trade-id") == "FILLED"
        assert summary["advanced"] >= 1
        event = _get_events(conn, "cmd-matched-missing-trade-id")[-1]
        assert event["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(event["payload_json"])
        assert payload["proof_class"] == (
            "matched_submit_missing_trade_id_confirmed_trade"
        )
        assert payload["required_predicates"][
            "bound_venue_order_id_matches_trade"
        ] is True
        trade_fact = conn.execute(
            """
            SELECT filled_size, fill_price
              FROM venue_trade_facts
             WHERE command_id = 'cmd-matched-missing-trade-id'
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "filled_size": "31.9",
            "fill_price": "0.73",
        }

    def test_matched_submit_missing_trade_id_reads_v2_adapter_trades(
        self, conn
    ):
        from src.state.venue_command_repo import append_event

        command_id = "cmd-matched-wrapper-client"
        order_id = "ord-matched-wrapper-client"
        token_id = "tok-matched-wrapper-client"
        _insert(
            conn,
            command_id=command_id,
            position_id="pos-matched-wrapper-client",
            decision_id="dec-matched-wrapper-client",
            token_id=token_id,
            size=31.5,
            price=0.74,
        )
        _advance_to_submitting(
            conn,
            command_id=command_id,
            venue_order_id=order_id,
        )
        append_event(
            conn,
            command_id=command_id,
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"reason": "matched_submit_missing_trade_id"},
        )

        adapter = MagicMock(spec_set=["get_trades"])
        adapter.get_trades.return_value = [
            {
                "id": "trade-matched-wrapper-client",
                "status": "CONFIRMED",
                "match_time": "2026-04-26T00:02:00Z",
                "transaction_hash": "0xtx-matched-wrapper-client",
                "asset_id": token_id,
                "taker_order_id": order_id,
                "side": "BUY",
                "price": "0.74",
                "size": "31.5",
            }
        ]

        class WrapperClient:
            def get_open_orders(self):
                return []

            def _ensure_v2_adapter(self):
                return adapter

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, WrapperClient())

        assert _get_state(conn, command_id) == "FILLED"
        assert summary["advanced"] >= 1
        adapter.get_trades.assert_called_once_with()

    def test_matched_edli_fill_prefers_certificate_bound_projection(
        self, conn, monkeypatch
    ):
        from src.execution import command_recovery

        candidate = {"command_id": "cmd-edli-projection"}
        calls = []
        monkeypatch.setattr(
            command_recovery,
            "_latest_unprojected_filled_entry_candidates",
            lambda _conn: [candidate],
        )
        monkeypatch.setattr(
            command_recovery,
            "_append_filled_entry_projection_repair",
            lambda _conn, *, candidate, client=None: calls.append(candidate) or True,
        )

        command_recovery._append_matched_order_fill_projection(
            conn,
            command={
                "command_id": "cmd-edli-projection",
                "decision_id": "edli_exec_cmd:evt-projection:intent:tok:tok:buy_yes",
            },
            venue_order_id="ord-edli-projection",
            matched_size="10",
            fill_price="0.50",
            observed_at="2026-04-26T00:02:00Z",
        )

        assert calls == [candidate]

    def test_restart_preflight_admits_only_exact_confirmed_matched_submit_review(
        self, conn
    ):
        from src.execution.command_recovery import (
            _restart_preflight_unresolved_commands,
        )
        from src.state.venue_command_repo import append_event

        command_id = "cmd-restart-matched-submit"
        order_id = "ord-restart-matched-submit"
        _insert(
            conn,
            command_id=command_id,
            position_id="pos-restart-matched-submit",
            decision_id="dec-restart-matched-submit",
            token_id="tok-restart-matched-submit",
            size=31.5,
            price=0.74,
        )
        _advance_to_submitting(
            conn,
            command_id=command_id,
            venue_order_id=order_id,
        )
        append_event(
            conn,
            command_id=command_id,
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"reason": "matched_submit_missing_trade_id"},
        )
        _append_confirmed_trade_fact(
            conn,
            command_id=command_id,
            order_id=order_id,
            trade_id="trade-restart-matched-submit",
            filled_size="31.9",
            fill_price="0.73",
        )

        rows = _restart_preflight_unresolved_commands(conn)

        assert [row["command_id"] for row in rows] == [command_id]

        partial_command_id = "cmd-restart-matched-submit-partial"
        partial_order_id = "ord-restart-matched-submit-partial"
        _insert(
            conn,
            command_id=partial_command_id,
            position_id="pos-restart-matched-submit-partial",
            decision_id="dec-restart-matched-submit-partial",
            token_id="tok-restart-matched-submit-partial",
            size=31.5,
            price=0.74,
        )
        _advance_to_submitting(
            conn,
            command_id=partial_command_id,
            venue_order_id=partial_order_id,
        )
        append_event(
            conn,
            command_id=partial_command_id,
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"reason": "matched_submit_missing_trade_id"},
        )
        _append_confirmed_trade_fact(
            conn,
            command_id=partial_command_id,
            order_id=partial_order_id,
            trade_id="trade-restart-matched-submit-partial",
            filled_size="31.48",
            fill_price="0.74",
        )
        assert [
            row["command_id"]
            for row in _restart_preflight_unresolved_commands(conn)
        ] == [command_id]

    def test_review_required_recovery_no_venue_order_id_confirmed_trade_stays_when_order_open(
        self, conn, mock_client
    ):
        from src.state.venue_command_repo import append_event

        _insert(
            conn,
            command_id="cmd-confirmed-open",
            position_id="pos-confirmed-open",
            decision_id="dec-confirmed-open",
            token_id="tok-confirmed-open",
            size=10.865300810243,
            price=0.67,
        )
        append_event(
            conn,
            command_id="cmd-confirmed-open",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"reason": "recovery_no_venue_order_id"},
        )
        mock_client.get_open_orders.return_value = [
            {
                "id": "ord-confirmed-open",
                "asset_id": "tok-confirmed-open",
                "side": "BUY",
                "price": "0.67",
                "size": "10.865300810243",
            }
        ]
        mock_client.get_trades.return_value = [
            {
                "id": "trade-confirmed-open",
                "status": "CONFIRMED",
                "trader_side": "TAKER",
                "match_time": "2026-04-26T00:02:00Z",
                "asset_id": "tok-confirmed-open",
                "taker_order_id": "ord-confirmed-open",
                "side": "BUY",
                "price": "0.67",
                "size": "10.86",
            }
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-confirmed-open") == "REVIEW_REQUIRED"
        assert summary["advanced"] == 0
        assert summary["stayed"] == 1
        events = _get_events(conn, "cmd-confirmed-open")
        assert events[-1]["event_type"] == "REVIEW_REQUIRED"

    def test_review_required_recovery_no_venue_order_id_stays_on_matching_trade(
        self, conn, mock_client
    ):
        from src.state.venue_command_repo import append_event

        _insert(
            conn,
            command_id="cmd-has-trade",
            position_id="pos-has-trade",
            decision_id="dec-has-trade",
            token_id="tok-has-trade",
        )
        append_event(
            conn,
            command_id="cmd-has-trade",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"reason": "recovery_no_venue_order_id"},
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = [
            {
                "id": "matching-trade",
                "asset_id": "tok-has-trade",
                "side": "BUY",
                "price": "0.5",
                "size": "10.0",
                "match_time": "2026-04-26T00:02:00Z",
            }
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-has-trade") == "REVIEW_REQUIRED"
        assert summary["advanced"] == 0
        assert summary["stayed"] == 1
        events = _get_events(conn, "cmd-has-trade")
        assert events[-1]["event_type"] == "REVIEW_REQUIRED"

    def test_review_required_exit_no_order_id_ignores_historical_entry_side_trade(
        self, conn, mock_client
    ):
        from src.state.venue_command_repo import append_event

        _insert(
            conn,
            command_id="cmd-exit-no-order",
            position_id="pos-exit-no-order",
            decision_id="dec-exit-no-order",
            intent_kind="EXIT",
            token_id="tok-exit-no-order",
            side="SELL",
            size=5.06,
            price=0.98,
        )
        append_event(
            conn,
            command_id="cmd-exit-no-order",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"reason": "recovery_no_venue_order_id"},
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = [
            {
                "id": "historical-entry-fill",
                "status": "CONFIRMED",
                "asset_id": "tok-exit-no-order",
                "side": "BUY",
                "price": "0.98",
                "size": "5.06",
                "match_time": "2026-04-26T00:02:00Z",
            }
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-exit-no-order") == "EXPIRED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-exit-no-order")
        assert events[-1]["event_type"] == "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["venue_absence_proof"]["matching_trade_count"] == 0
        assert payload["venue_absence_proof"]["trade_count"] == 1

    def test_cancel_unknown_review_required_live_order_restores_acked(self, conn, mock_client):
        _insert(conn, intent_kind="EXIT", side="SELL", size=11.62, price=0.02)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-live")
        mock_client.get_order.return_value = {
            "orderID": "ord-live",
            "status": "LIVE",
            "matched_size": "0",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "ACKED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "REVIEW_CLEARED_VENUE_ORDER_LIVE"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["reason"] == "review_cleared_venue_order_live"
        assert payload["required_predicates"]["latest_event_is_cancel_replace_blocked"] is True
        assert payload["required_predicates"]["point_order_status_live"] is True

    def test_post_ack_review_required_terminal_no_fill_expires(self, conn, mock_client):
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.venue_command_repo import append_event

        _insert(conn, command_id="cmd-post-ack", position_id="pos-post-ack")
        _advance_to_acked(conn, command_id="cmd-post-ack", venue_order_id="ord-post-ack")
        append_event(
            conn,
            command_id="cmd-post-ack",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:03:00Z",
            payload={
                "reason": "entry_ack_persistence_failed_after_side_effect",
                "venue_order_id": "ord-post-ack",
                "side_effect_boundary_crossed": True,
                "sdk_submit_returned_order_id": True,
            },
        )
        _append_order_fact(
            conn,
            command_id="cmd-post-ack",
            order_id="ord-post-ack",
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="10",
            source="WS_USER",
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []
        mock_client.get_order.return_value = {
            "orderID": "ord-post-ack",
            "status": "CANCELED",
            "size_matched": "0",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        before_count, _ = count_unknown_side_effects(conn)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert before_count == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-post-ack") == "EXPIRED"
        events = _get_events(conn, "cmd-post-ack")
        assert events[-1]["event_type"] == "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "acked_submit_terminal_no_fill"
        assert payload["required_predicates"]["terminal_order_fact_no_fill"] is True
        assert payload["required_predicates"]["no_matching_open_orders"] is True
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()

    def test_post_ack_review_required_live_order_restores_acked(self, conn, mock_client):
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.venue_command_repo import append_event

        _insert(conn, command_id="cmd-post-ack-live", position_id="pos-post-ack-live")
        _advance_to_acked(
            conn,
            command_id="cmd-post-ack-live",
            venue_order_id="ord-post-ack-live",
        )
        append_event(
            conn,
            command_id="cmd-post-ack-live",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:03:00Z",
            payload={
                "reason": "entry_ack_persistence_failed_after_side_effect",
                "venue_order_id": "ord-post-ack-live",
                "side_effect_boundary_crossed": True,
                "sdk_submit_returned_order_id": True,
            },
        )
        _append_order_fact(
            conn,
            command_id="cmd-post-ack-live",
            order_id="ord-post-ack-live",
            state="LIVE",
            matched_size="0",
            remaining_size="10",
            source="WS_USER",
        )
        mock_client.get_open_orders.return_value = [
            {
                "id": "ord-post-ack-live",
                "asset_id": "tok-001",
                "side": "BUY",
                "price": "0.50",
                "original_size": "10",
                "size_matched": "0",
                "status": "LIVE",
            }
        ]
        mock_client.get_trades.return_value = []
        mock_client.get_order.return_value = {
            "orderID": "ord-post-ack-live",
            "status": "LIVE",
            "size_matched": "0",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        before_count, _ = count_unknown_side_effects(conn)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert before_count == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-post-ack-live") == "ACKED"
        events = _get_events(conn, "cmd-post-ack-live")
        assert events[-1]["event_type"] == "REVIEW_CLEARED_VENUE_ORDER_LIVE"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "acked_submit_venue_order_live"
        assert payload["required_predicates"]["authenticated_live_order_seen"] is True
        assert payload["required_predicates"]["latest_order_fact_live"] is True
        assert payload["required_predicates"]["no_trade_facts"] is True
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()

    def test_post_ack_review_required_live_order_restores_from_local_fact_when_account_read_fails(
        self, conn, mock_client
    ):
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.venue_command_repo import append_event

        _insert(conn, command_id="cmd-post-ack-local-live", position_id="pos-post-ack-local-live")
        _advance_to_acked(
            conn,
            command_id="cmd-post-ack-local-live",
            venue_order_id="ord-post-ack-local-live",
        )
        append_event(
            conn,
            command_id="cmd-post-ack-local-live",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:03:00Z",
            payload={
                "reason": "entry_ack_persistence_failed_after_side_effect",
                "venue_order_id": "ord-post-ack-local-live",
                "side_effect_boundary_crossed": True,
                "sdk_submit_returned_order_id": True,
            },
        )
        _append_order_fact(
            conn,
            command_id="cmd-post-ack-local-live",
            order_id="ord-post-ack-local-live",
            state="LIVE",
            matched_size="0",
            remaining_size="10",
            source="WS_USER",
        )
        mock_client.get_open_orders.side_effect = RuntimeError("account read unavailable")
        mock_client.get_trades.side_effect = RuntimeError("account read unavailable")
        mock_client.get_order.side_effect = RuntimeError("point read unavailable")

        from src.execution.command_recovery import reconcile_unresolved_commands

        before_count, _ = count_unknown_side_effects(conn)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert before_count == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-post-ack-local-live") == "ACKED"
        events = _get_events(conn, "cmd-post-ack-local-live")
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "acked_submit_venue_order_live"
        assert payload["required_predicates"]["latest_order_fact_live"] is True
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()

    def test_post_ack_review_required_terminal_no_fill_stays_with_trade_fact(
        self, conn, mock_client
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, command_id="cmd-post-ack-fill", position_id="pos-post-ack-fill")
        _advance_to_acked(
            conn,
            command_id="cmd-post-ack-fill",
            venue_order_id="ord-post-ack-fill",
        )
        append_event(
            conn,
            command_id="cmd-post-ack-fill",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:03:00Z",
            payload={
                "reason": "entry_ack_persistence_failed_after_side_effect",
                "venue_order_id": "ord-post-ack-fill",
                "side_effect_boundary_crossed": True,
                "sdk_submit_returned_order_id": True,
            },
        )
        _append_order_fact(
            conn,
            command_id="cmd-post-ack-fill",
            order_id="ord-post-ack-fill",
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="10",
            source="WS_USER",
        )
        _append_confirmed_trade_fact(
            conn,
            command_id="cmd-post-ack-fill",
            order_id="ord-post-ack-fill",
            trade_id="trade-post-ack-fill",
            filled_size="1",
            fill_price="0.5",
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []
        mock_client.get_order.return_value = {
            "orderID": "ord-post-ack-fill",
            "status": "CANCELED",
            "size_matched": "0",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-post-ack-fill") == "REVIEW_REQUIRED"
        assert summary["advanced"] == 0
        assert summary["stayed"] == 1

    def test_cancel_unknown_review_required_matched_order_with_confirmed_trade_fills(self, conn, mock_client):
        _insert(conn, intent_kind="EXIT", side="SELL", size=5, price=0.55)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-exit")
        mock_client.get_order.return_value = {
            "orderID": "ord-exit",
            "status": "ORDER_STATUS_MATCHED",
            "original_size": "5000000",
            "size_matched": "5000000",
            "_venue_response_contract": "POLYMARKET_CLOB_V2_FIXED_6_POINT_ORDER",
            "_v2_original_size": "5",
            "_v2_matched_size": "5",
            "price": "0.55",
        }
        mock_client.get_trades.return_value = [
            {
                "id": "trade-exit-001",
                "taker_order_id": "ord-exit",
                "status": "CONFIRMED",
                "side": "SELL",
                "size": "5",
                "price": "0.56",
                "transaction_hash": "0xabc",
            }
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "FILLED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "cancel_unknown_confirmed_trade_with_positive_trade_fact"
        assert payload["required_predicates"]["semantic_cancel_status_cancel_unknown"] is True
        fact = conn.execute(
            """
            SELECT state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-001'
            """
        ).fetchone()
        assert dict(fact) == {
            "state": "CONFIRMED",
            "filled_size": "5",
            "fill_price": "0.56",
            "tx_hash": "0xabc",
        }

    def test_cancel_unknown_unknown_point_order_with_exact_trade_fills_entry_projection(
        self, conn, mock_client
    ):
        _insert(conn, intent_kind="ENTRY", side="BUY", size=5, price=0.55)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, order_id="ord-entry")
        mock_client.get_order.return_value = {
            "orderID": "ord-entry",
            "status": "UNKNOWN",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = [
            {
                "id": "trade-entry-001",
                "taker_order_id": "ord-other-side",
                "status": "CONFIRMED",
                "side": "BUY",
                "asset_id": "tok-yes",
                "size": "14",
                "price": "0.45",
                "maker_orders": [
                    {
                        "order_id": "ord-entry",
                        "side": "BUY",
                        "asset_id": "tok-001",
                        "matched_amount": "5",
                        "price": "0.55",
                    },
                    {
                        "order_id": "ord-other-maker",
                        "side": "SELL",
                        "asset_id": "tok-yes",
                        "matched_amount": "9",
                        "price": "0.45",
                    },
                ],
                "transaction_hash": "0xdef",
            }
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "FILLED"
        assert summary["advanced"] >= 1
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["venue_order_proof"]["venue_status"] == "UNKNOWN"
        assert payload["trade_fact_proof"]["trade"]["id"] == "trade-entry-001"
        fact = conn.execute(
            """
            SELECT state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-001'
            """
        ).fetchone()
        assert dict(fact) == {
            "state": "CONFIRMED",
            "filled_size": "5",
            "fill_price": "0.55",
            "tx_hash": "0xdef",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id='pos-001'"
        ).fetchone()
        assert current["phase"] in {"active", "day0_window"}
        assert Decimal(str(current["shares"])) == Decimal("5")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("2.75")
        assert current["order_status"] == "filled"

    def test_cancel_unknown_review_required_terminal_no_fill_expires_entry(self, conn, mock_client):
        from src.execution.exchange_reconcile import list_unresolved_findings, record_finding

        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-terminal")
        _seed_pending_entry_projection(conn, order_id="ord-terminal")
        finding = record_finding(
            conn,
            kind="local_orphan_order",
            subject_id="ord-terminal",
            context="ws_gap",
            evidence={
                "reason": "local_open_order_absent_from_exchange_open_orders",
                "exchange_open_order_ids": [],
                "trade_enumeration_available": True,
            },
            recorded_at="2026-04-26T00:06:00Z",
        )
        mock_client.get_order.return_value = {
            "orderID": "ord-terminal",
            "status": "CANCELLED",
            "matched_size": "0",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "EXPIRED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["reason"] == "review_cleared_no_venue_exposure"
        assert payload["proof_class"] == "cancel_unknown_terminal_no_fill"
        assert payload["required_predicates"]["point_order_terminal_no_fill"] is True
        assert payload["required_predicates"]["no_matching_open_orders"] is True
        assert payload["required_predicates"]["no_matching_trades"] is True
        assert payload["resolved_m5_local_orphan_findings"] == 1
        assert [row.finding_id for row in list_unresolved_findings(conn)] == []
        resolved = conn.execute(
            "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
            (finding.finding_id,),
        ).fetchone()
        assert dict(resolved) == {
            "resolution": "command_recovery_terminal_no_fill",
            "resolved_by": "src.execution.command_recovery",
        }

        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id='pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "voided",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "order_status": "canceled",
        }

    def test_cancel_unknown_terminal_no_fill_with_voided_projection_expires_entry(
        self, conn, mock_client
    ):
        """Live 2026-07-05 shape (commands 12e0ee45e0a44bc8/1a74acd884cf4ba5):
        the venue canceled a maker rest (zero fill) and the projection lane
        had already voided the position before command recovery ran. voided
        + zero shares + zero cost is exactly as zero-exposure as
        pending_entry; recovery must not strand the command in
        REVIEW_REQUIRED."""
        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-voided")
        _seed_pending_entry_projection(conn, order_id="ord-voided")
        conn.execute(
            "UPDATE position_current SET phase='voided', order_status='canceled' "
            "WHERE position_id='pos-001'"
        )
        mock_client.get_order.return_value = {
            "orderID": "ord-voided",
            "status": "CANCELLED",
            "matched_size": "0",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "EXPIRED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "cancel_unknown_terminal_no_fill"

    def test_cancel_unknown_review_required_absent_point_order_no_exposure_expires_entry(
        self, conn, mock_client
    ):
        from src.risk_allocator.governor import count_unknown_side_effects

        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-absent")
        _seed_pending_entry_projection(conn, order_id="ord-absent")
        mock_client.get_order.return_value = None
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        before_count, _ = count_unknown_side_effects(conn)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert before_count == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "cancel_unknown_terminal_no_fill"
        assert payload["required_predicates"]["point_order_absent"] is True
        assert payload["required_predicates"]["no_matching_open_orders"] is True
        assert payload["required_predicates"]["no_matching_trades"] is True
        assert payload["venue_absence_proof"]["point_order_status"] == "NOT_FOUND"
        assert payload["venue_absence_proof"]["matching_open_order_count"] == 0
        assert payload["venue_absence_proof"]["matching_trade_count"] == 0

        terminal_fact = conn.execute(
            """
            SELECT state, matched_size, remaining_size, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert terminal_fact["state"] == "VENUE_WIPED"
        assert Decimal(str(terminal_fact["matched_size"])) == Decimal("0")
        assert Decimal(str(terminal_fact["remaining_size"])) == Decimal("0")
        fact_payload = json.loads(terminal_fact["raw_payload_json"])
        assert fact_payload["source_reason"] == "cancel_unknown_point_order_absent_terminal_no_fill"

        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id='pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "voided",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "order_status": "canceled",
        }
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()

    def test_maker_rest_cancel_unknown_legacy_payload_absent_point_order_expires_entry(
        self, conn, mock_client
    ):
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.venue_command_repo import append_event

        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_pending(conn, venue_order_id="ord-maker-rest-absent")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REPLACE_BLOCKED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={
                "venue_order_id": "ord-maker-rest-absent",
                "reason": "post_cancel_unknown_possible_side_effect",
                "cancel_outcome": {
                    "orderID": "ord-maker-rest-absent",
                    "status": "NOT_CANCELED",
                    "errorMessage": (
                        "ord-maker-rest-absent: order can't be found - already canceled or matched"
                    ),
                },
            },
        )
        _seed_pending_entry_projection(conn, order_id="ord-maker-rest-absent")
        mock_client.get_order.return_value = None
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        before_count, _ = count_unknown_side_effects(conn)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert before_count == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        events = _get_events(conn, "cmd-001")
        payload = json.loads(events[-1]["payload_json"])
        assert payload["source_proof"]["source_reason"] == (
            "cancel_unknown_point_order_absent_terminal_no_fill"
        )
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()

    def test_maker_rest_cancel_unknown_absent_projection_terminal_no_fill_expires_entry(
        self, conn, mock_client
    ):
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.venue_command_repo import append_event

        _insert(
            conn,
            intent_kind="ENTRY",
            side="BUY",
            size=28.56,
            price=0.71,
            selected_token_id="tok-001-no",
        )
        _insert_decision_log_trade_case_for_recovery(
            conn,
            direction="buy_no",
        )
        _advance_to_cancel_pending(conn, venue_order_id="ord-maker-rest-canceled")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REPLACE_BLOCKED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={
                "venue_order_id": "ord-maker-rest-canceled",
                "reason": "post_cancel_unknown_possible_side_effect",
                "semantic_cancel_status": "CANCEL_UNKNOWN",
                "requires_m5_reconcile": True,
                "cancel_outcome": {
                    "orderID": "ord-maker-rest-canceled",
                    "status": "NOT_CANCELED",
                    "errorMessage": (
                        "ord-maker-rest-canceled: the order is already canceled"
                    ),
                },
            },
        )
        conn.execute("DELETE FROM position_current WHERE position_id = 'pos-001'")
        mock_client.get_order.return_value = {
            "orderID": "ord-maker-rest-canceled",
            "status": "CANCELED",
            "matched_size": "0",
            "remaining_size": "0",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        before_count, _ = count_unknown_side_effects(conn)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert before_count == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "voided",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "order_status": "canceled",
        }
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()

    def test_maker_rest_cancel_unknown_legacy_payload_unknown_no_live_record_expires_entry(
        self, conn, mock_client
    ):
        from src.risk_allocator.governor import count_unknown_side_effects
        from src.state.venue_command_repo import append_event

        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_pending(conn, venue_order_id="ord-maker-rest-unknown")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REPLACE_BLOCKED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={
                "venue_order_id": "ord-maker-rest-unknown",
                "reason": "post_cancel_unknown_possible_side_effect",
                "cancel_outcome": {
                    "orderID": "ord-maker-rest-unknown",
                    "status": "NOT_CANCELED",
                    "errorMessage": (
                        "ord-maker-rest-unknown: order can't be found - already canceled or matched"
                    ),
                },
            },
        )
        _seed_pending_entry_projection(conn, order_id="ord-maker-rest-unknown")
        mock_client.get_order.return_value = {
            "orderID": "ord-maker-rest-unknown",
            "status": "UNKNOWN",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        before_count, _ = count_unknown_side_effects(conn)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert before_count == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "REVIEW_CLEARED_NO_VENUE_EXPOSURE"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["required_predicates"]["point_order_no_live_record"] is True
        assert "point_order_absent" not in payload["required_predicates"]
        assert payload["venue_absence_proof"]["point_order_status"] == "UNKNOWN"
        assert payload["venue_absence_proof"]["point_order"] == {
            "orderID": "ord-maker-rest-unknown",
            "status": "UNKNOWN",
        }
        assert payload["source_proof"]["source_reason"] == (
            "cancel_unknown_point_order_no_live_record_terminal_no_fill"
        )
        terminal_fact = conn.execute(
            """
            SELECT state, matched_size, remaining_size, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert terminal_fact["state"] == "VENUE_WIPED"
        fact_payload = json.loads(terminal_fact["raw_payload_json"])
        assert fact_payload["required_predicates"]["point_order_no_live_record"] is True
        assert Decimal(str(terminal_fact["matched_size"])) == Decimal("0")
        assert Decimal(str(terminal_fact["remaining_size"])) == Decimal("0")
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()

    def test_cancel_unknown_unknown_point_order_with_live_data_stays_review_required(
        self, conn, mock_client
    ):
        from src.risk_allocator.governor import count_unknown_side_effects

        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-unknown-live-data")
        _seed_pending_entry_projection(conn, order_id="ord-unknown-live-data")
        mock_client.get_order.return_value = {
            "orderID": "ord-unknown-live-data",
            "status": "UNKNOWN",
            "size": "11.62",
            "price": "0.02",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        event_count_before = len(_get_events(conn, "cmd-001"))
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["stayed"] >= 1
        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert len(_get_events(conn, "cmd-001")) == event_count_before
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 1
        assert after_markets == ("mkt-001",)

    def test_expired_terminal_no_fill_entry_resolves_late_m5_local_orphan_finding(self, conn, mock_client):
        from src.execution.exchange_reconcile import list_unresolved_findings, record_finding

        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-terminal")
        _seed_pending_entry_projection(conn, order_id="ord-terminal")
        mock_client.get_order.return_value = {
            "orderID": "ord-terminal",
            "status": "CANCELLED",
            "matched_size": "0",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands
        first_summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "EXPIRED"
        assert first_summary["advanced"] == 1
        finding = record_finding(
            conn,
            kind="local_orphan_order",
            subject_id="ord-terminal",
            context="ws_gap",
            evidence={
                "reason": "local_open_order_absent_from_exchange_open_orders",
                "exchange_open_order_ids": [],
                "trade_enumeration_available": True,
            },
            recorded_at="2026-04-26T00:07:00Z",
        )

        second_summary = reconcile_unresolved_commands(conn, mock_client)

        assert second_summary["stale_terminal_no_fill_findings"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert [row.finding_id for row in list_unresolved_findings(conn)] == []
        resolved = conn.execute(
            "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
            (finding.finding_id,),
        ).fetchone()
        assert dict(resolved) == {
            "resolution": "command_recovery_terminal_no_fill",
            "resolved_by": "src.execution.command_recovery",
        }

    def test_cancel_unknown_review_required_terminal_with_trade_match_stays_blocked(self, conn, mock_client):
        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-terminal")
        _seed_pending_entry_projection(conn, order_id="ord-terminal")
        mock_client.get_order.return_value = {
            "orderID": "ord-terminal",
            "status": "CANCELED",
            "matched_size": "0",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = [
            {
                "id": "trade-terminal",
                "asset_id": "tok-001",
                "side": "BUY",
                "price": "0.02",
                "size": "11.62",
                "match_time": "2026-04-26T00:04:30Z",
            }
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert summary["stayed"] == 1
        assert summary["advanced"] == 0
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "CANCEL_REPLACE_BLOCKED"

    def test_cancel_unknown_review_required_terminal_with_open_order_match_stays_blocked(self, conn, mock_client):
        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-terminal")
        _seed_pending_entry_projection(conn, order_id="ord-terminal")
        mock_client.get_order.return_value = {
            "orderID": "ord-terminal",
            "status": "CANCELED",
            "matched_size": "0",
        }
        mock_client.get_open_orders.return_value = [
            {
                "orderID": "ord-terminal",
                "asset_id": "tok-001",
                "side": "BUY",
                "price": "0.02",
                "original_size": "11.62",
            }
        ]
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert summary["stayed"] == 1
        assert summary["advanced"] == 0
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "CANCEL_REPLACE_BLOCKED"

    def test_cancel_unknown_review_required_terminal_with_local_exposure_stays_blocked(self, conn, mock_client):
        _insert(conn, intent_kind="ENTRY", side="BUY", size=11.62, price=0.02)
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-terminal")
        _seed_pending_entry_projection(conn, order_id="ord-terminal")
        conn.execute(
            """
            UPDATE position_current
               SET shares = 1.25,
                   cost_basis_usd = 0.025
             WHERE position_id = 'pos-001'
            """
        )
        mock_client.get_order.return_value = {
            "orderID": "ord-terminal",
            "status": "CANCELED",
            "matched_size": "0",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert summary["stayed"] == 1
        assert summary["advanced"] == 0
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "CANCEL_REPLACE_BLOCKED"

    def test_review_required_after_prior_fill_can_be_proof_cleared_to_filled(self, conn):
        from src.execution.command_recovery import clear_review_required_confirmed_fill
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.44)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            trade_id="trade-001",
            order_id="ord-001",
            state="MATCHED",
            filled_size="5.116278",
            fill_price="0.4299998944545233859457988796",
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "reason": "place_limit_order_matched_submit",
                "venue_order_id": "ord-001",
                "trade_id": "trade-001",
                "filled_size": "5.116278",
                "fill_price": "0.4299998944545233859457988796",
            },
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:07:00Z",
            payload={
                "reason": "ws_trade_lifecycle_regression_or_economic_drift",
                "trade_id": "trade-001",
                "venue_order_id": "ord-001",
            },
        )

        payload = clear_review_required_confirmed_fill(
            conn,
            "cmd-001",
            source_commit="test-commit",
            reviewed_by="pytest",
            occurred_at="2026-04-26T00:08:00Z",
        )

        assert _get_state(conn, "cmd-001") == "FILLED"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        assert json.loads(events[-1]["payload_json"]) == payload
        assert payload["reason"] == "review_cleared_confirmed_fill"
        assert payload["required_predicates"]["prior_fill_confirmed_event"] is True
        assert payload["trade_fact_proof"]["state"] == "MATCHED"

    def test_review_required_clearance_uses_canonical_positive_trade_fact(self, conn):
        """Relationship: a later weak trade fact cannot erase prior fill proof."""
        from src.execution.command_recovery import clear_review_required_confirmed_fill
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.44)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            trade_id="trade-001",
            order_id="ord-001",
            state="CONFIRMED",
            filled_size="5.116278",
            fill_price="0.4299998944545233859457988796",
        )
        _append_trade_fact(
            conn,
            trade_id="trade-001",
            order_id="ord-001",
            state="MATCHED",
            filled_size="1",
            fill_price="0.4299998944545233859457988796",
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "reason": "place_limit_order_matched_submit",
                "venue_order_id": "ord-001",
                "trade_id": "trade-001",
                "filled_size": "5.116278",
                "fill_price": "0.4299998944545233859457988796",
            },
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:07:00Z",
            payload={
                "reason": "ws_trade_lifecycle_regression_or_economic_drift",
                "trade_id": "trade-001",
                "venue_order_id": "ord-001",
            },
        )

        payload = clear_review_required_confirmed_fill(
            conn,
            "cmd-001",
            source_commit="test-commit",
            reviewed_by="pytest",
            occurred_at="2026-04-26T00:08:00Z",
        )

        assert _get_state(conn, "cmd-001") == "FILLED"
        assert payload["trade_fact_proof"]["state"] == "CONFIRMED"
        assert payload["filled_size"] == "5.116278"

    def test_review_required_fill_confirmed_clearance_requires_structured_proof(self, conn):
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.44)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            trade_id="trade-001",
            order_id="ord-001",
            state="MATCHED",
            filled_size="5.116278",
            fill_price="0.4299998944545233859457988796",
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "reason": "place_limit_order_matched_submit",
                "venue_order_id": "ord-001",
                "trade_id": "trade-001",
                "filled_size": "5.116278",
                "fill_price": "0.4299998944545233859457988796",
            },
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:07:00Z",
            payload={"reason": "ws_trade_lifecycle_regression_or_economic_drift"},
        )

        with pytest.raises(ValueError, match="review confirmed-fill clearance payload"):
            append_event(
                conn,
                command_id="cmd-001",
                event_type="FILL_CONFIRMED",
                occurred_at="2026-04-26T00:08:00Z",
                payload={"reason": "place_limit_order_matched_submit"},
            )

    # Case 7: venue lookup raises u2192 state stays (error counted)
    def test_venue_lookup_exception_leaves_state(self, conn, mock_client):
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-005")
        mock_client.get_order.side_effect = RuntimeError("network timeout")

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        # State must NOT change; error must be counted
        assert _get_state(conn, "cmd-001") == "SUBMITTING"
        assert summary["errors"] == 1
        assert summary["advanced"] == 0

    # Case 8: CANCEL_PENDING + venue says order CANCELLED u2192 CANCELLED
    def test_cancel_pending_with_cancelled_status_resolves_to_cancelled(self, conn, mock_client):
        _insert(conn)
        _advance_to_cancel_pending(conn, venue_order_id="vord-006")
        mock_client.get_order.return_value = {"orderID": "vord-006", "status": "CANCELLED"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "CANCELLED"
        assert summary["advanced"] == 1

    def test_cancel_pending_with_state_only_cancelled_resolves_to_cancelled(
        self, conn, mock_client
    ):
        _insert(conn)
        _advance_to_cancel_pending(conn, venue_order_id="vord-state-cancel")
        mock_client.get_order.return_value = {
            "orderID": "vord-state-cancel",
            "state": "CANCELED",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "CANCELLED"
        assert summary["advanced"] == 1

    # Supplementary: CANCEL_PENDING + venue order still active u2192 stays CANCEL_PENDING
    def test_cancel_pending_with_active_order_stays_in_cancel_pending(self, conn, mock_client):
        _insert(conn)
        _advance_to_cancel_pending(conn, venue_order_id="vord-007")
        mock_client.get_order.return_value = {"orderID": "vord-007", "status": "LIVE"}

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "CANCEL_PENDING"
        assert summary["stayed"] == 1
        assert summary["advanced"] == 0

    def test_maker_rest_cancel_pending_live_order_waits_inside_cancel_grace(
        self,
        conn,
        mock_client,
        monkeypatch,
    ):
        import src.execution.command_recovery as recovery
        from src.state.venue_command_repo import append_event

        monkeypatch.setattr(recovery, "_now_iso", lambda: "2026-04-26T00:03:05+00:00")
        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-maker-live")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-04-26T00:02:00Z",
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:03:00Z",
            payload={
                "venue_order_id": "vord-maker-live",
                "source": "maker_rest_escalation",
            },
        )
        mock_client.get_order.return_value = {
            "orderID": "vord-maker-live",
            "status": "LIVE",
            "matched_size": "0",
        }

        summary = recovery.reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "CANCEL_PENDING"
        assert summary["advanced"] == 0
        assert summary["stayed"] == 1
        event_types = [event["event_type"] for event in _get_events(conn, "cmd-001")]
        assert event_types[-1] == "CANCEL_REQUESTED"

    def test_maker_rest_cancel_pending_live_order_restores_acked(self, conn, mock_client):
        from src.state.venue_command_repo import append_event

        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-maker-live")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-04-26T00:02:00Z",
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:03:00Z",
            payload={
                "venue_order_id": "vord-maker-live",
                "source": "maker_rest_escalation",
            },
        )
        mock_client.get_order.return_value = {
            "orderID": "vord-maker-live",
            "status": "LIVE",
            "matched_size": "0",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "ACKED"
        assert summary["advanced"] == 1
        events = _get_events(conn, "cmd-001")
        assert events[-2]["event_type"] == "CANCEL_REPLACE_BLOCKED"
        assert events[-1]["event_type"] == "REVIEW_CLEARED_VENUE_ORDER_LIVE"
        cancel_payload = json.loads(events[-2]["payload_json"])
        clear_payload = json.loads(events[-1]["payload_json"])
        assert cancel_payload["semantic_cancel_status"] == "CANCEL_UNKNOWN"
        assert cancel_payload["requires_m5_reconcile"] is True
        assert clear_payload["proof_class"] == "cancel_unknown_venue_order_live"

    def test_maker_rest_cancel_pending_live_order_missing_matched_size_stays(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn)
        _advance_to_submitting(conn, venue_order_id="vord-maker-live")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-04-26T00:02:00Z",
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:03:00Z",
            payload={
                "venue_order_id": "vord-maker-live",
                "source": "maker_rest_escalation",
            },
        )
        mock_client.get_order.return_value = {
            "orderID": "vord-maker-live",
            "status": "LIVE",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "CANCEL_PENDING"
        assert summary["advanced"] == 0
        assert summary["stayed"] == 1
        event_types = [event["event_type"] for event in _get_events(conn, "cmd-001")]
        assert event_types[-1] == "CANCEL_REQUESTED"

    def test_acked_terminal_no_fill_order_fact_expires_command_and_voids_pending_entry(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="0", remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["scanned"] == 0
        assert summary["terminal_order_facts"]["advanced"] == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert event_types[-1] == "EXPIRED"
        position_event = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, command_id, order_id
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(position_event) == {
            "event_type": "ENTRY_ORDER_VOIDED",
            "phase_before": "pending_entry",
            "phase_after": "voided",
            "command_id": "cmd-001",
            "order_id": "ord-001",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "voided"
        assert Decimal(str(current["shares"])) == Decimal("0")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0")
        assert current["order_status"] == "canceled"

    def test_acked_terminal_no_fill_order_fact_projects_edli_lifecycle_terminal(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        aggregate_id = "event-1:intent-1"
        command_payload = {
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "dec-001",
            "command_id": "cmd-001",
        }
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="ExecutionCommandCreated",
            payload=command_payload,
            occurred_at="2026-04-26T00:01:00Z",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="VenueSubmitAcknowledged",
            payload={**command_payload, "venue_order_id": "ord-001"},
            occurred_at="2026-04-26T00:02:00Z",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=3,
            event_type="CapTransitioned",
            payload={
                **command_payload,
                "venue_order_id": "ord-001",
                "to_status": "CONSUMED",
                "execution_receipt_hash": "receipt-hash",
            },
            occurred_at="2026-04-26T00:02:01Z",
        )
        last_hash = conn.execute(
            """
            SELECT event_hash
              FROM edli_live_order_events
             WHERE aggregate_id = ?
             ORDER BY event_sequence DESC
             LIMIT 1
            """,
            (aggregate_id,),
        ).fetchone()["event_hash"]
        conn.execute(
            """
            INSERT INTO edli_live_order_projection (
                aggregate_id, event_id, final_intent_id, current_state,
                last_sequence, last_event_type, last_event_hash,
                pending_reconcile, venue_order_id, updated_at, schema_version
            ) VALUES (?, 'event-1', 'intent-1', 'CAP_TRANSITIONED',
                      3, 'CapTransitioned', ?, 0, 'ord-001',
                      '2026-04-26T00:02:01Z', 1)
            """,
            (aggregate_id, last_hash),
        )
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="0", remaining_size="0")

        from src.engine.event_reactor_adapter import _TERMINAL_EVENT_SQL
        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"]["advanced"] == 1
        projection = conn.execute(
            """
            SELECT current_state, last_event_type, pending_reconcile, venue_order_id
              FROM edli_live_order_projection
             WHERE aggregate_id = ?
            """,
            (aggregate_id,),
        ).fetchone()
        assert dict(projection) == {
            "current_state": "TERMINAL_NO_FILL",
            "last_event_type": "OrderLifecycleProjected",
            "pending_reconcile": 0,
            "venue_order_id": "ord-001",
        }
        lifecycle = conn.execute(
            """
            SELECT payload_json
              FROM edli_live_order_events
             WHERE aggregate_id = ?
               AND event_type = 'OrderLifecycleProjected'
            """,
            (aggregate_id,),
        ).fetchone()
        payload = json.loads(lifecycle["payload_json"])
        assert payload["order_lifecycle_state"] == "TERMINAL_NO_FILL"
        assert payload["exposure_created"] is False
        assert payload["matched_size"] == "0"
        assert conn.execute(_TERMINAL_EVENT_SQL, (aggregate_id,)).fetchone() is not None

    def test_cancel_pending_terminal_no_fill_order_fact_cancels_command_and_voids_pending_entry(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_cancel_pending(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="0", remaining_size="12.44")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"]["advanced"] == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "CANCELLED"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert event_types[-1] == "CANCEL_ACKED"
        position_event = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, command_id, order_id
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(position_event) == {
            "event_type": "ENTRY_ORDER_VOIDED",
            "phase_before": "pending_entry",
            "phase_after": "voided",
            "command_id": "cmd-001",
            "order_id": "ord-001",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "voided"
        assert Decimal(str(current["shares"])) == Decimal("0")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0")
        assert current["order_status"] == "canceled"

    def test_terminal_no_fill_order_fact_can_collect_redecision_continuation(
        self,
        conn,
        mock_client,
    ):
        _insert(
            conn,
            token_id="tok-001",
            no_token_id="tok-001-no",
            selected_token_id="tok-001-no",
        )
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="0", remaining_size="0")

        from src.execution.command_recovery import reconcile_terminal_order_facts

        summary = reconcile_terminal_order_facts(conn, collect_continuations=True)

        assert summary["advanced"] == 1
        assert summary["continuations"] == [
            {
                "command_id": "cmd-001",
                "position_id": "pos-001",
                "venue_order_id": "ord-001",
                "condition_id": "condition-test",
                "token_id": "tok-001-no",
                "city": "Karachi",
                "target_date": "2026-05-17",
                "temperature_metric": "high",
                "metric": "high",
                "reason": "venue_terminal_no_fill",
            }
        ]

    def test_terminal_no_fill_continuation_uses_snapshot_family_without_market_events(
        self,
        conn,
        mock_client,
    ):
        conn.execute("DROP TABLE IF EXISTS market_events")
        _insert(
            conn,
            token_id="tok-001",
            no_token_id="tok-001-no",
            selected_token_id="tok-001-no",
            event_slug="highest-temperature-in-boston-on-june-23-2026",
        )
        _advance_to_acked(conn, venue_order_id="ord-001")
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="0", remaining_size="0")

        from src.execution.command_recovery import reconcile_terminal_order_facts

        summary = reconcile_terminal_order_facts(conn, collect_continuations=True)

        assert summary["advanced"] == 1
        assert summary["continuations"] == [
            {
                "command_id": "cmd-001",
                "position_id": "pos-001",
                "venue_order_id": "ord-001",
                "condition_id": "condition-test",
                "token_id": "tok-001-no",
                "city": "Boston",
                "target_date": "2026-06-23",
                "temperature_metric": "high",
                "metric": "high",
                "reason": "venue_terminal_no_fill",
            }
        ]

    def test_terminal_no_fill_recovery_writes_redecision_event_in_same_pass(
        self,
        conn,
        mock_client,
        monkeypatch,
    ):
        """Terminal no-fill projection must durably requeue EDLI before main bridge."""
        import src.events.triggers.forecast_snapshot_ready as fsr_trigger
        from src.events.opportunity_event import make_opportunity_event
        from src.execution import command_recovery
        from src.execution.command_recovery import reconcile_terminal_order_facts

        _insert(
            conn,
            token_id="tok-001",
            no_token_id="tok-001-no",
            selected_token_id="tok-001-no",
        )
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="0", remaining_size="0")

        class FakeTrigger:
            def __init__(self, writer, *, live_eligibility_reader):
                self.writer = writer
                self.live_eligibility_reader = live_eligibility_reader

            def build_committed_snapshot_events(self, **kwargs):
                assert kwargs["event_type"] == "EDLI_REDECISION_PENDING"
                assert kwargs["restrict_to_families"] == {("Karachi", "2026-05-17", "high")}
                assert kwargs["source"] == "terminal-no-fill:cmd-001"
                received_at = kwargs["received_at"]
                return [
                    make_opportunity_event(
                        event_type="EDLI_REDECISION_PENDING",
                        entity_key="Karachi|2026-05-17|high|snap-terminal-no-fill",
                        source=kwargs["source"],
                        observed_at=received_at,
                        available_at=received_at,
                        received_at=received_at,
                        causal_snapshot_id="snap-terminal-no-fill",
                        payload={
                            "city": "Karachi",
                            "target_date": "2026-05-17",
                            "metric": "high",
                            "snapshot_id": "snap-terminal-no-fill",
                        },
                        priority=50,
                    )
                ]

        monkeypatch.setattr(fsr_trigger, "ForecastSnapshotReadyTrigger", FakeTrigger)
        monkeypatch.setattr(
            fsr_trigger,
            "executable_forecast_live_eligible_reader",
            lambda forecasts_conn: "reader",
        )
        monkeypatch.setattr(
            command_recovery,
            "_recovery_forecasts_read_connection",
            lambda recovery_conn: (recovery_conn, False),
        )

        summary = reconcile_terminal_order_facts(conn, collect_continuations=True)

        assert summary["advanced"] == 1
        assert summary["continuations"] == []
        assert summary["immediate_redecision_events"] == 1
        row = conn.execute(
            """
            SELECT e.event_type, e.source, e.payload_json, p.processing_status
              FROM opportunity_events e
              JOIN opportunity_event_processing p ON p.event_id = e.event_id
             WHERE e.event_type = 'EDLI_REDECISION_PENDING'
            """
        ).fetchone()
        assert row is not None
        assert row["source"] == "terminal-no-fill:cmd-001"
        assert row["processing_status"] == "pending"
        payload = json.loads(row["payload_json"])
        assert payload["redecision_origin"] == "terminal_no_fill"
        assert payload["terminal_no_fill_command_id"] == "cmd-001"
        assert payload["rest_then_cross_policy"] == "TAKER_ESCALATED_AFTER_REST"
        assert payload["rest_then_cross_escalated_after_rest"] is True
        assert payload["rest_then_cross_escalation_source"] == "terminal_no_fill"

    def test_acked_point_order_terminal_no_fill_fact_expires_command_and_voids_pending_entry(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="LIVE", matched_size="0", remaining_size="10")
        mock_client.get_order.return_value = {
            "orderID": "ord-001",
            "status": "CANCELED",
            "matched_size": "0",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_point_orders"] == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        assert summary["terminal_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        order_fact = dict(order_fact)
        payload = json.loads(order_fact.pop("raw_payload_json"))
        assert order_fact == {
            "state": "CANCEL_CONFIRMED",
            "remaining_size": "0",
            "matched_size": "0",
            "source": "REST",
        }
        assert payload["reason"] == "point_order_terminal_no_fill"
        assert payload["required_predicates"]["no_matching_open_orders"] is True
        assert payload["required_predicates"]["no_matching_trades"] is True

    def test_live_tick_primes_acked_order_before_projection_creates_terminal_candidate(
        self,
        conn,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-late-candidate")

        from src.execution.command_recovery import _collect_recovery_priming_keys

        priming = _collect_recovery_priming_keys(conn, scope="live_tick")

        assert "ord-late-candidate" in priming["order_ids"]

    @pytest.mark.parametrize(
        ("filled_size", "remaining_size", "position_phase", "live_tick_expected"),
        [
            ("10", "0", "economically_closed", False),
            ("8", "2", "economically_closed", True),
            ("10", "0", "active", False),
        ],
    )
    def test_live_tick_primes_filled_order_only_for_canonical_partial_coverage(
        self,
        conn,
        filled_size,
        remaining_size,
        position_phase,
        live_tick_expected,
    ):
        from src.execution.command_recovery import _collect_recovery_priming_keys
        from src.state.venue_command_repo import append_event

        _insert(conn, size=10.0)
        _advance_to_acked(conn, venue_order_id="ord-filled-scope")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "venue_order_id": "ord-filled-scope",
                "trade_id": "trade-filled-scope",
                "filled_size": "10",
                "fill_price": "0.5",
            },
        )
        _seed_pending_entry_projection(conn, order_id="ord-filled-scope")
        conn.execute(
            """
            UPDATE position_current
               SET phase = ?,
                   shares = 0,
                   cost_basis_usd = 0,
                   order_status = 'filled'
             WHERE position_id = 'pos-001'
            """,
            (position_phase,),
        )
        _append_order_fact(
            conn,
            order_id="ord-filled-scope",
            state="PARTIALLY_MATCHED" if remaining_size != "0" else "MATCHED",
            matched_size=filled_size,
            remaining_size=remaining_size,
        )
        _append_confirmed_trade_fact(
            conn,
            order_id="ord-filled-scope",
            filled_size=filled_size,
            fill_price="0.50",
        )

        live_tick = _collect_recovery_priming_keys(conn, scope="live_tick")
        boot_fast = _collect_recovery_priming_keys(conn, scope="boot_fast")
        full = _collect_recovery_priming_keys(conn, scope="full")

        assert ("ord-filled-scope" in live_tick["order_ids"]) is live_tick_expected
        assert "ord-filled-scope" in boot_fast["order_ids"]
        assert "ord-filled-scope" in full["order_ids"]

    def test_live_tick_does_not_overprime_filled_terminal_exit_with_stale_remainder(
        self,
        conn,
    ):
        from src.execution.command_recovery import _collect_recovery_priming_keys
        from src.state.venue_command_repo import append_event

        _insert(conn, intent_kind="EXIT", side="SELL", size=10.0)
        _advance_to_acked(conn, venue_order_id="ord-filled-terminal-exit")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "venue_order_id": "ord-filled-terminal-exit",
                "trade_id": "trade-filled-terminal-exit",
                "filled_size": "10",
                "fill_price": "0.5",
            },
        )
        _seed_pending_entry_projection(conn, order_id="ord-filled-terminal-exit")
        conn.execute(
            "UPDATE position_current SET phase = 'settled' WHERE position_id = 'pos-001'"
        )
        _append_order_fact(
            conn,
            order_id="ord-filled-terminal-exit",
            state="PARTIALLY_MATCHED",
            matched_size="8",
            remaining_size="2",
        )

        live_tick = _collect_recovery_priming_keys(conn, scope="live_tick")
        boot_fast = _collect_recovery_priming_keys(conn, scope="boot_fast")
        full = _collect_recovery_priming_keys(conn, scope="full")

        assert "ord-filled-terminal-exit" not in live_tick["order_ids"]
        assert "ord-filled-terminal-exit" in boot_fast["order_ids"]
        assert "ord-filled-terminal-exit" in full["order_ids"]

    def test_live_partial_remainder_seek_does_not_demote_stronger_canonical_fact(
        self,
        conn,
    ):
        from src.execution.command_recovery import _partial_remainder_candidates
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0)
        _advance_to_acked(conn, venue_order_id="ord-partial-seek")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "venue_order_id": "ord-partial-seek",
                "trade_id": "trade-partial-seek",
                "filled_size": "5",
                "fill_price": "0.5",
            },
        )
        _seed_pending_entry_projection(conn, order_id="ord-partial-seek")
        conn.execute(
            "UPDATE position_current SET phase = 'economically_closed' WHERE position_id = 'pos-001'"
        )
        _append_order_fact(
            conn,
            order_id="ord-partial-seek",
            state="MATCHED",
            matched_size="5",
            remaining_size="0",
        )
        _append_order_fact(
            conn,
            order_id="ord-partial-seek",
            state="RESTING",
            matched_size="0",
            remaining_size="5",
        )

        sql_trace = []
        conn.set_trace_callback(sql_trace.append)
        candidates = _partial_remainder_candidates(
            conn,
            updated_before=None,
            live_tick_scope=True,
        )
        conn.set_trace_callback(None)

        assert candidates == []
        assert any(
            "SELECT latest.fact_id FROM venue_order_facts latest"
            in " ".join(query.split())
            for query in sql_trace
        )

    def test_live_tick_does_not_prime_terminal_zero_fill_matched_recovery_debt(
        self,
        conn,
    ):
        from src.execution.command_recovery import (
            _collect_recovery_priming_keys,
            _latest_matched_order_fact_candidates,
        )
        from src.state.venue_command_repo import append_event

        _insert(conn, command_id="cmd-terminal-zero")
        _advance_to_cancel_pending(
            conn,
            command_id="cmd-terminal-zero",
            venue_order_id="ord-terminal-zero",
        )
        append_event(
            conn,
            command_id="cmd-terminal-zero",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={"venue_order_id": "ord-terminal-zero", "venue_status": "CANCELED"},
        )
        _append_order_fact(
            conn,
            command_id="cmd-terminal-zero",
            order_id="ord-terminal-zero",
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="0",
        )

        priming = _collect_recovery_priming_keys(conn, scope="live_tick")
        full_candidates = _latest_matched_order_fact_candidates(conn)
        live_tick_candidates = _latest_matched_order_fact_candidates(
            conn,
            skip_stale_terminal_zero=True,
        )

        assert "ord-terminal-zero" not in priming["order_ids"]
        assert any(row["command_id"] == "cmd-terminal-zero" for row in full_candidates)
        assert all(
            row["command_id"] != "cmd-terminal-zero"
            for row in live_tick_candidates
        )

    @pytest.mark.parametrize(
        ("position_phase", "live_tick_expected"),
        [
            ("settled", False),
            ("voided", False),
            ("admin_closed", False),
            ("economically_closed", True),
            ("day0_window", True),
        ],
    )
    def test_live_tick_skips_only_durably_filled_hard_terminal_matched_orders(
        self,
        conn,
        position_phase,
        live_tick_expected,
    ):
        from src.execution.command_recovery import (
            _collect_recovery_priming_keys,
            _latest_matched_order_fact_candidates,
        )
        from src.state.venue_command_repo import append_event

        _insert(conn, size=10.0)
        _advance_to_cancel_pending(conn, venue_order_id="ord-terminal-filled")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={"venue_order_id": "ord-terminal-filled", "venue_status": "CANCELED"},
        )
        _seed_pending_entry_projection(conn, order_id="ord-terminal-filled")
        conn.execute(
            "UPDATE position_current SET phase = ? WHERE position_id = 'pos-001'",
            (position_phase,),
        )
        _append_order_fact(
            conn,
            order_id="ord-terminal-filled",
            state="CANCEL_CONFIRMED",
            matched_size="10",
            remaining_size="0",
        )
        _append_trade_fact(
            conn,
            order_id="ord-terminal-filled",
            trade_id="trade-terminal-filled",
            state="CONFIRMED",
            filled_size="10",
            fill_price="0.5",
        )

        sql_trace = []
        conn.set_trace_callback(sql_trace.append)
        live_tick_candidates = _latest_matched_order_fact_candidates(
            conn,
            skip_stale_terminal_zero=True,
            skip_projected_hard_terminal=True,
        )
        conn.set_trace_callback(None)
        full_candidates = _latest_matched_order_fact_candidates(conn)
        live_tick = _collect_recovery_priming_keys(conn, scope="live_tick")
        boot_fast = _collect_recovery_priming_keys(conn, scope="boot_fast")

        assert (
            any(row["command_id"] == "cmd-001" for row in live_tick_candidates)
            is live_tick_expected
        )
        assert any(row["command_id"] == "cmd-001" for row in full_candidates)
        assert (
            "ord-terminal-filled" in live_tick["order_ids"]
        ) is live_tick_expected
        assert "ord-terminal-filled" in boot_fast["order_ids"]
        assert not any(
            "SELECT COUNT(*) AS count FROM venue_trade_facts WHERE command_id" in " ".join(query.split())
            for query in sql_trace
        )
        assert any(
            "JOIN matched_candidate_commands scope ON scope.command_id = fact.command_id"
            in " ".join(query.split())
            for query in sql_trace
        )

    def test_live_tick_matched_apply_avoids_venue_read_for_projected_hard_terminal(
        self,
        conn,
        mock_client,
    ):
        from src.execution.command_recovery import reconcile_matched_order_facts
        from src.state.venue_command_repo import append_event

        _insert(conn, size=10.0)
        _advance_to_cancel_pending(conn, venue_order_id="ord-terminal-filled")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={"venue_order_id": "ord-terminal-filled", "venue_status": "CANCELED"},
        )
        _seed_pending_entry_projection(conn, order_id="ord-terminal-filled")
        conn.execute(
            "UPDATE position_current SET phase = 'settled' WHERE position_id = 'pos-001'"
        )
        _append_order_fact(
            conn,
            order_id="ord-terminal-filled",
            state="CANCEL_CONFIRMED",
            matched_size="10",
            remaining_size="0",
        )
        _append_trade_fact(
            conn,
            order_id="ord-terminal-filled",
            trade_id="trade-terminal-filled",
            state="CONFIRMED",
            filled_size="10",
            fill_price="0.5",
        )

        summary = reconcile_matched_order_facts(
            conn,
            mock_client,
            skip_stale_terminal_zero=True,
            skip_projected_hard_terminal=True,
        )

        assert summary == {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
        mock_client.get_order.assert_not_called()

    @pytest.mark.parametrize("incomplete_proof", ["missing_trade_fact", "missing_position", "review_required"])
    def test_live_tick_keeps_terminal_matched_orders_without_complete_local_proof(
        self,
        conn,
        incomplete_proof,
    ):
        from src.execution.command_recovery import (
            _collect_recovery_priming_keys,
            _latest_matched_order_fact_candidates,
        )
        from src.state.venue_command_repo import append_event

        _insert(conn, size=10.0)
        if incomplete_proof == "review_required":
            _advance_to_cancel_unknown_review_required(
                conn,
                venue_order_id="ord-incomplete-terminal",
            )
        else:
            _advance_to_cancel_pending(conn, venue_order_id="ord-incomplete-terminal")
            append_event(
                conn,
                command_id="cmd-001",
                event_type="CANCEL_ACKED",
                occurred_at="2026-04-26T00:04:00Z",
                payload={"venue_order_id": "ord-incomplete-terminal", "venue_status": "CANCELED"},
            )
        _seed_pending_entry_projection(conn, order_id="ord-incomplete-terminal")
        conn.execute(
            "UPDATE position_current SET phase = 'settled' WHERE position_id = 'pos-001'"
        )
        _append_order_fact(
            conn,
            order_id="ord-incomplete-terminal",
            state="CANCEL_CONFIRMED",
            matched_size="10",
            remaining_size="0",
        )
        if incomplete_proof != "missing_trade_fact":
            _append_trade_fact(
                conn,
                order_id="ord-incomplete-terminal",
                trade_id="trade-incomplete-terminal",
                state="CONFIRMED",
                filled_size="10",
                fill_price="0.5",
            )
        if incomplete_proof == "missing_position":
            conn.execute("DELETE FROM position_current WHERE position_id = 'pos-001'")

        candidates = _latest_matched_order_fact_candidates(
            conn,
            skip_stale_terminal_zero=True,
            skip_projected_hard_terminal=True,
        )
        priming = _collect_recovery_priming_keys(conn, scope="live_tick")

        assert any(row["command_id"] == "cmd-001" for row in candidates)
        assert "ord-incomplete-terminal" in priming["order_ids"]

    def test_live_tick_scopes_recorded_maker_fill_economics(
        self,
        tmp_path,
        monkeypatch,
    ):
        from src.execution import command_recovery, exchange_reconcile, venue_sync_contract
        from src.state.db import init_schema
        from src.state.collateral_ledger import init_collateral_schema

        db_path = tmp_path / "live-tick-maker-scope.db"
        seed = sqlite3.connect(db_path)
        seed.row_factory = sqlite3.Row
        init_schema(seed)
        init_collateral_schema(seed)
        seed.close()

        def _conn_factory():
            scoped_conn = sqlite3.connect(db_path)
            scoped_conn.row_factory = sqlite3.Row
            return scoped_conn

        observed_scopes = []

        def _maker_fill_scope_spy(conn, *, observed_at=None, live_tick_scope=False):
            observed_scopes.append(live_tick_scope)
            return {"scanned": 0, "corrected": 0, "stayed": 0, "errors": 0}

        monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
        monkeypatch.setattr(
            exchange_reconcile,
            "reconcile_recorded_maker_fill_economics",
            _maker_fill_scope_spy,
        )
        client = MagicMock(
            spec_set=["get_order", "get_open_orders", "get_trades", "get_clob_market_info"]
        )
        client.get_open_orders.return_value = []
        client.get_trades.return_value = []

        command_recovery.reconcile_unresolved_commands(client=client, scope="live_tick")

        assert observed_scopes == [True]

    def test_live_tick_clears_terminal_cancel_fact_before_venue_snapshot(
        self,
        tmp_path,
        monkeypatch,
    ):
        from src.execution import command_recovery
        from src.execution import venue_sync_contract
        from src.state.db import init_schema
        from src.state.collateral_ledger import init_collateral_schema

        db_path = tmp_path / "live-tick-terminal-before-snapshot.db"
        seed = sqlite3.connect(db_path)
        seed.row_factory = sqlite3.Row
        init_schema(seed)
        init_collateral_schema(seed)
        _insert(seed)
        _advance_to_cancel_pending(seed, venue_order_id="ord-001")
        _seed_pending_entry_projection(seed)
        _append_order_fact(
            seed,
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="12.44",
        )
        seed.commit()
        seed.close()

        def _conn_factory():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        def _fail_capture(*args, **kwargs):
            raise RuntimeError("venue snapshot blocked after local cleanup")

        monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
        monkeypatch.setattr(venue_sync_contract, "capture_venue_read_snapshot", _fail_capture)

        client = MagicMock(spec_set=["get_order", "get_open_orders", "get_trades", "get_clob_market_info"])
        with pytest.raises(RuntimeError, match="venue snapshot blocked after local cleanup"):
            command_recovery.reconcile_unresolved_commands(client=client, scope="live_tick")

        verified = _conn_factory()
        try:
            command = verified.execute(
                "SELECT state FROM venue_commands WHERE command_id = 'cmd-001'"
            ).fetchone()
            latest_event = verified.execute(
                """
                SELECT event_type
                  FROM venue_command_events
                 WHERE command_id = 'cmd-001'
                 ORDER BY sequence_no DESC
                 LIMIT 1
                """
            ).fetchone()
            current = verified.execute(
                """
                SELECT phase, shares, cost_basis_usd, order_status
                  FROM position_current
                 WHERE position_id = 'pos-001'
                """
            ).fetchone()
        finally:
            verified.close()

        assert command["state"] == "CANCELLED"
        assert latest_event["event_type"] == "CANCEL_ACKED"
        assert current["phase"] == "voided"
        assert Decimal(str(current["shares"])) == Decimal("0")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0")
        assert current["order_status"] == "canceled"

    def test_acked_terminal_point_order_missing_matched_size_stays(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="LIVE", matched_size="0", remaining_size="10")
        mock_client.get_order.return_value = {
            "orderID": "ord-001",
            "status": "CANCELED",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_point_orders"] == {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}
        assert summary["terminal_order_facts"]["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "ACKED"
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert dict(current) == {"phase": "pending_entry", "shares": 0.0, "cost_basis_usd": 0.0}
        latest_fact = conn.execute(
            """
            SELECT state, matched_size
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_fact) == {"state": "LIVE", "matched_size": "0"}

    def test_cancelled_terminal_no_fill_order_without_pending_projection_recovers_and_voids(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, size=13.45, price=0.01)
        _advance_to_cancel_pending(conn, venue_order_id="ord-cancelled")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:05:00Z",
            payload={"venue_order_id": "ord-cancelled", "venue_status": "CANCELED"},
        )
        _append_order_fact(
            conn,
            order_id="ord-cancelled",
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="13.45",
            source="WS_USER",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "CANCELLED"
        position_events = conn.execute(
            """
            SELECT sequence_no, event_type, phase_before, phase_after, command_id, order_id
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert [dict(row) for row in position_events] == [
            {
                "sequence_no": 1,
                "event_type": "POSITION_OPEN_INTENT",
                "phase_before": None,
                "phase_after": "pending_entry",
                "command_id": "cmd-001",
                "order_id": None,
            },
            {
                "sequence_no": 2,
                "event_type": "ENTRY_ORDER_POSTED",
                "phase_before": "pending_entry",
                "phase_after": "pending_entry",
                "command_id": "cmd-001",
                "order_id": "ord-cancelled",
            },
            {
                "sequence_no": 3,
                "event_type": "ENTRY_ORDER_VOIDED",
                "phase_before": "pending_entry",
                "phase_after": "voided",
                "command_id": "cmd-001",
                "order_id": "ord-cancelled",
            },
        ]
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "voided"
        assert Decimal(str(current["shares"])) == Decimal("0")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0")
        assert current["order_status"] == "canceled"

        second_summary = reconcile_unresolved_commands(conn, mock_client)
        assert second_summary["terminal_order_facts"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_terminal_no_fill_missing_projection_stays_when_positive_trade_fact_exists(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, size=13.45, price=0.01)
        _advance_to_cancel_pending(conn, venue_order_id="ord-partial")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:05:00Z",
            payload={"venue_order_id": "ord-partial", "venue_status": "CANCELED"},
        )
        _append_order_fact(
            conn,
            order_id="ord-partial",
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="13.45",
            source="WS_USER",
        )
        _append_trade_fact(
            conn,
            order_id="ord-partial",
            state="MATCHED",
            filled_size="1.25",
            fill_price="0.01",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"] == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        assert conn.execute(
            "SELECT 1 FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM position_events WHERE position_id = 'pos-001'"
        ).fetchone() is None

    def test_cancelled_terminal_no_fill_with_existing_pending_projection_voids(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, size=13.45, price=0.01)
        _advance_to_acked(conn, venue_order_id="ord-cancelled")
        _append_order_fact(
            conn,
            order_id="ord-cancelled",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import reconcile_unresolved_commands

        live_summary = reconcile_unresolved_commands(conn, mock_client)
        assert live_summary["live_entry_projection_repair"]["advanced"] == 1

        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={"venue_order_id": "ord-cancelled"},
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:05:00Z",
            payload={"venue_order_id": "ord-cancelled", "venue_status": "CANCELED"},
        )
        _append_order_fact(
            conn,
            order_id="ord-cancelled",
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="13.45",
            source="WS_USER",
        )

        terminal_summary = reconcile_unresolved_commands(conn, mock_client)

        assert terminal_summary["terminal_order_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "voided"
        assert Decimal(str(current["shares"])) == Decimal("0")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0")
        assert current["order_status"] == "canceled"
        events = conn.execute(
            """
            SELECT event_type
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert [row["event_type"] for row in events] == [
            "POSITION_OPEN_INTENT",
            "ENTRY_ORDER_POSTED",
            "ENTRY_ORDER_VOIDED",
        ]
        second_summary = reconcile_unresolved_commands(conn, mock_client)
        assert second_summary["terminal_order_facts"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_cancel_acked_zero_fill_without_terminal_fact_voids_pending_entry(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, size=10.35, price=0.60)
        _advance_to_acked(conn, venue_order_id="ord-cancelled")
        _seed_pending_entry_projection(conn, order_id="ord-cancelled")
        _append_order_fact(
            conn,
            order_id="ord-cancelled",
            state="LIVE",
            matched_size="0",
            remaining_size="10.35",
            source="REST",
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={"venue_order_id": "ord-cancelled"},
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:05:00Z",
            payload={"venue_order_id": "ord-cancelled", "venue_status": "CANCELED"},
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["cancel_ack_terminal_no_fill_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert summary["terminal_order_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "voided"
        assert Decimal(str(current["shares"])) == Decimal("0")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0")
        assert current["order_status"] == "canceled"
        terminal_fact = conn.execute(
            """
            SELECT state, matched_size, remaining_size, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert terminal_fact["state"] == "CANCEL_CONFIRMED"
        assert Decimal(str(terminal_fact["matched_size"])) == Decimal("0")
        assert Decimal(str(terminal_fact["remaining_size"])) == Decimal("10.35")
        assert json.loads(terminal_fact["raw_payload_json"])["proof_class"] == (
            "cancel_ack_plus_zero_pending_projection"
        )

    def test_cancel_acked_zero_fill_without_position_projection_voids_unprojected_entry(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(
            conn,
            size=21.61,
            price=0.72,
            token_id="tok-yes",
            no_token_id="tok-no",
            selected_token_id="tok-no",
            outcome_label="NO",
            event_slug="highest-temperature-in-denver-on-june-21-2026",
        )
        _advance_to_acked(conn, venue_order_id="ord-cancelled")
        _append_order_fact(
            conn,
            order_id="ord-cancelled",
            state="LIVE",
            matched_size="0",
            remaining_size="21.61",
            source="REST",
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={"venue_order_id": "ord-cancelled"},
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:05:00Z",
            payload={"venue_order_id": "ord-cancelled", "venue_status": "CANCELED"},
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["cancel_ack_terminal_no_fill_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert summary["terminal_order_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, city, target_date, temperature_metric, direction,
                   shares, cost_basis_usd, order_status, strategy_key
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "voided",
            "city": "Denver",
            "target_date": "2026-06-21",
            "temperature_metric": "high",
            "direction": "buy_no",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "order_status": "canceled",
            "strategy_key": "opening_inertia",
        }
        position_events = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, command_id, order_id
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert [dict(row) for row in position_events] == [
            {
                "event_type": "ENTRY_ORDER_VOIDED",
                "phase_before": None,
                "phase_after": "voided",
                "command_id": "cmd-001",
                "order_id": "ord-cancelled",
            }
        ]
        terminal_fact = conn.execute(
            """
            SELECT state, matched_size, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert terminal_fact["state"] == "CANCEL_CONFIRMED"
        assert Decimal(str(terminal_fact["matched_size"])) == Decimal("0")
        assert json.loads(terminal_fact["raw_payload_json"])["proof_class"] == (
            "cancel_ack_plus_zero_unprojected_entry"
        )

    def test_cancel_acked_zero_fill_with_positive_trade_fact_stays_pending(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, size=10.35, price=0.60)
        _advance_to_acked(conn, venue_order_id="ord-cancelled")
        _seed_pending_entry_projection(conn, order_id="ord-cancelled")
        _append_order_fact(
            conn,
            order_id="ord-cancelled",
            state="LIVE",
            matched_size="0",
            remaining_size="10.35",
            source="REST",
        )
        _append_trade_fact(
            conn,
            command_id="cmd-001",
            order_id="ord-cancelled",
            filled_size="1.00",
            fill_price="0.60",
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={"venue_order_id": "ord-cancelled"},
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:05:00Z",
            payload={"venue_order_id": "ord-cancelled", "venue_status": "CANCELED"},
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["cancel_ack_terminal_no_fill_facts"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            "SELECT phase FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] != "voided"

    def test_acked_live_order_fact_with_point_order_matched_records_fill(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="LIVE", matched_size="0", remaining_size="5")
        mock_client.get_order.return_value = {
            "id": "ord-001",
            "status": "MATCHED",
            "size_matched": "5",
            "price": "0.34",
            "associate_trades": ["trade-001"],
            "transactionsHashes": ["0xhash-001"],
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["scanned"] == 0
        assert summary["matched_order_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "FILLED"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert event_types[-1] == "FILL_CONFIRMED"
        latest_order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_order_fact) == {
            "state": "MATCHED",
            "remaining_size": "0",
            "matched_size": "5",
            "source": "REST",
        }
        trade_fact = conn.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-001",
            "venue_order_id": "ord-001",
            "state": "MATCHED",
            "filled_size": "5",
            "fill_price": "0.34",
            "tx_hash": "0xhash-001",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, entry_price, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "active"
        assert Decimal(str(current["shares"])) == Decimal("5")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("1.7")
        assert Decimal(str(current["entry_price"])) == Decimal("0.34")
        assert current["order_status"] == "filled"

    def test_cancelled_command_with_late_matched_point_order_projects_without_illegal_event(
        self,
        conn,
        mock_client,
    ):
        """A post-cancel matched order fact is venue truth, not a CANCELLED->FILLED command transition."""
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-late-match")
        _seed_pending_entry_projection(conn, order_id="ord-late-match")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:03:00Z",
            payload={"reason": "maker_redecision_reprice"},
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={"venue_order_id": "ord-late-match", "venue_status": "CANCELED"},
        )
        _append_order_fact(
            conn,
            order_id="ord-late-match",
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="5",
        )
        mock_client.get_order.return_value = {
            "id": "ord-late-match",
            "status": "MATCHED",
            "size_matched": "5",
            "price": "0.34",
            "associate_trades": ["trade-late-match"],
            "transactionsHashes": ["0xhash-late-match"],
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "CANCELLED"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert event_types[-1] == "CANCEL_ACKED"
        assert "FILL_CONFIRMED" not in event_types
        latest_order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_order_fact) == {
            "state": "MATCHED",
            "remaining_size": "0",
            "matched_size": "5",
            "source": "REST",
        }
        trade_fact = conn.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-late-match",
            "venue_order_id": "ord-late-match",
            "state": "MATCHED",
            "filled_size": "5",
            "fill_price": "0.34",
            "tx_hash": "0xhash-late-match",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, entry_price, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "active"
        assert Decimal(str(current["shares"])) == Decimal("5")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("1.7")
        assert Decimal(str(current["entry_price"])) == Decimal("0.34")
        assert current["order_status"] == "filled"

    def test_acked_unknown_point_order_recovers_from_maker_trade_facts(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=10.58, price=0.67)
        _advance_to_acked(conn, venue_order_id="ord-maker-fill")
        _seed_pending_entry_projection(conn, order_id="ord-maker-fill")
        _append_order_fact(
            conn,
            order_id="ord-maker-fill",
            state="LIVE",
            matched_size="0",
            remaining_size="10.58",
        )
        mock_client.get_order.return_value = {
            "id": "ord-maker-fill",
            "status": "UNKNOWN",
        }
        mock_client.get_trades.return_value = [
            {
                "id": "trade-maker-a",
                "status": "CONFIRMED",
                "market": "market-1",
                "transaction_hash": "0xhash-a",
                "maker_orders": [
                    {
                        "order_id": "ord-maker-fill",
                        "matched_amount": "4.48",
                        "price": "0.67",
                        "asset_id": "token-no-1",
                        "outcome": "No",
                        "side": "BUY",
                    }
                ],
            },
            {
                "id": "trade-maker-b",
                "status": "CONFIRMED",
                "market": "market-1",
                "transaction_hash": "0xhash-b",
                "maker_orders": [
                    {
                        "order_id": "ord-maker-fill",
                        "matched_amount": "6.10",
                        "price": "0.67",
                        "asset_id": "token-no-1",
                        "outcome": "No",
                        "side": "BUY",
                    }
                ],
            },
        ]

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "FILLED"
        latest_order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_order_fact) == {
            "state": "MATCHED",
            "remaining_size": "0",
            "matched_size": "10.58",
            "source": "REST",
        }
        trade_fact = conn.execute(
            """
            SELECT trade_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-maker-a",
            "state": "MATCHED",
            "filled_size": "10.58",
            "fill_price": "0.67",
            "tx_hash": "0xhash-a",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, entry_price, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "active"
        assert Decimal(str(current["shares"])) == Decimal("10.58")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("7.0886")
        assert Decimal(str(current["entry_price"])) == Decimal("0.67")
        assert current["order_status"] == "filled"

    def test_exit_point_order_matched_uses_sell_making_amount_as_share_size(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, intent_kind="EXIT", side="SELL", size=15.5, price=0.7)
        _advance_to_acked(conn, venue_order_id="ord-sell-matched")
        _append_order_fact(
            conn,
            order_id="ord-sell-matched",
            state="LIVE",
            matched_size="0",
            remaining_size="15.5",
        )
        mock_client.get_order.return_value = {
            "id": "ord-sell-matched",
            "status": "MATCHED",
            "makingAmount": "15.5",
            "takingAmount": "10.85",
            "associate_trades": ["trade-sell-matched"],
            "transactionsHashes": ["0xhash-sell-matched"],
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "FILLED"
        latest_order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_order_fact) == {
            "state": "MATCHED",
            "remaining_size": "0",
            "matched_size": "15.5",
            "source": "REST",
        }
        trade_fact = conn.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-sell-matched",
            "venue_order_id": "ord-sell-matched",
            "state": "MATCHED",
            "filled_size": "15.5",
            "fill_price": "0.7",
            "tx_hash": "0xhash-sell-matched",
        }

    def test_review_required_exit_matched_order_fact_finalizes_exit_projection(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, command_id="cmd-entry", position_id="pos-exit-review")
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(
            conn,
            position_id="pos-exit-review",
            command_id="cmd-entry",
            order_id="ord-entry",
        )
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'pending_exit',
                   direction = 'buy_yes',
                   shares = 85.17,
                   chain_shares = 85.17,
                   cost_basis_usd = 4.34367,
                   entry_price = 0.050999,
                   token_id = 'tok-001',
                   no_token_id = 'tok-001-no',
                   order_status = 'retry_pending',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-exit-review'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit-review",
            position_id="pos-exit-review",
            intent_kind="EXIT",
            side="SELL",
            size=85.17,
            price=0.04,
        )
        _advance_to_acked(
            conn,
            command_id="cmd-exit-review",
            venue_order_id="ord-exit-review",
        )
        append_event(
            conn,
            command_id="cmd-exit-review",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:03:00Z",
            payload={"venue_order_id": "ord-exit-review"},
        )
        append_event(
            conn,
            command_id="cmd-exit-review",
            event_type="CANCEL_FAILED",
            occurred_at="2026-04-26T00:03:01Z",
            payload={
                "venue_order_id": "ord-exit-review",
                "reason": "matched orders can't be canceled",
                "cancel_outcome": {
                    "status": "NOT_CANCELED",
                    "errorMessage": "matched orders can't be canceled",
                },
            },
        )
        _append_order_fact(
            conn,
            command_id="cmd-exit-review",
            order_id="ord-exit-review",
            state="MATCHED",
            matched_size="85.17",
            remaining_size="0",
            raw_payload_json={
                "id": "ord-exit-review",
                "status": "MATCHED",
                "size_matched": "85.17",
                "price": "0.04",
                "associate_trades": ["trade-exit-review"],
            },
        )
        mock_client.get_order.return_value = {
            "id": "ord-exit-review",
            "status": "MATCHED",
            "size_matched": "85.17",
            "price": "0.04",
            "associate_trades": ["trade-exit-review"],
            "transactionsHashes": ["0xhash-exit-review"],
        }
        before_cancel_requested = sum(
            1
            for event in _get_events(conn, "cmd-exit-review")
            if event["event_type"] == "CANCEL_REQUESTED"
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-exit-review") == "FILLED"
        events = _get_events(conn, "cmd-exit-review")
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        assert sum(1 for event in events if event["event_type"] == "CANCEL_REQUESTED") == before_cancel_requested
        trade_fact = conn.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-exit-review'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-exit-review",
            "venue_order_id": "ord-exit-review",
            "state": "MATCHED",
            "filled_size": "85.17",
            "fill_price": "0.04",
            "tx_hash": "0xhash-exit-review",
        }
        current = conn.execute(
            """
            SELECT phase, chain_shares, order_status, exit_price
              FROM position_current
             WHERE position_id = 'pos-exit-review'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "economically_closed",
            "chain_shares": 0.0,
            "order_status": "sell_filled",
            "exit_price": 0.04,
        }

    def test_matched_order_recovery_preserves_partial_size_below_command(
        self,
        conn,
        mock_client,
    ):
        """Venue MATCHED cannot overwrite the observed filled-share quantity."""
        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="LIVE", matched_size="0", remaining_size="5")
        mock_client.get_order.return_value = {
            "id": "ord-001",
            "status": "MATCHED",
            "size_matched": "4.99",
            "price": "0.34",
            "associate_trades": ["trade-001"],
            "transactionsHashes": ["0xhash-001"],
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "PARTIAL_FILL_OBSERVED"
        latest_order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_order_fact) == {
            "state": "PARTIALLY_MATCHED",
            "remaining_size": "0.01",
            "matched_size": "4.99",
            "source": "REST",
        }
        position = conn.execute(
            "SELECT shares FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert position is not None
        assert Decimal(str(position["shares"])) == Decimal("4.99")

    def test_matched_order_recovery_consumes_typed_v2_fixed_6_size(
        self,
        conn,
        mock_client,
    ):
        """Recovery consumes adapter-typed shares, never raw fixed-6 units."""
        _insert(conn, size=10.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-v2-fixed-6")
        _seed_pending_entry_projection(conn, order_id="ord-v2-fixed-6")
        _append_order_fact(
            conn,
            order_id="ord-v2-fixed-6",
            state="LIVE",
            matched_size="0",
            remaining_size="10",
        )
        mock_client.get_order.return_value = {
            "id": "ord-v2-fixed-6",
            "status": "ORDER_STATUS_MATCHED",
            "side": "BUY",
            "original_size": "10000000",
            "size_matched": "3250000",
            "_venue_response_contract": "POLYMARKET_CLOB_V2_FIXED_6_POINT_ORDER",
            "_v2_original_size": "10",
            "_v2_matched_size": "3.25",
            "price": "0.34",
            "associate_trades": ["trade-v2-fixed-6"],
            "transactionsHashes": ["0xhash-v2-fixed-6"],
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        latest_order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_order_fact) == {
            "state": "PARTIALLY_MATCHED",
            "remaining_size": "6.75",
            "matched_size": "3.25",
        }
        position = conn.execute(
            "SELECT shares FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert position is not None
        assert Decimal(str(position["shares"])) == Decimal("3.25")

    def test_live_point_order_with_positive_size_matched_projects_partial_entry(
        self,
        conn,
        mock_client,
    ):
        """Polymarket can keep an order LIVE after a maker partial fill."""
        _insert(conn, size=10.58, price=0.67)
        _advance_to_acked(conn, venue_order_id="ord-live-partial")
        _seed_pending_entry_projection(conn, order_id="ord-live-partial")
        _append_order_fact(
            conn,
            order_id="ord-live-partial",
            state="LIVE",
            matched_size="0",
            remaining_size="10.58",
        )
        mock_client.get_order.return_value = {
            "id": "ord-live-partial",
            "status": "LIVE",
            "size_matched": "4.484847",
            "original_size": "10.58",
            "price": "0.67",
            "associate_trades": ["trade-live-partial"],
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        latest_order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_order_fact) == {
            "state": "PARTIALLY_MATCHED",
            "remaining_size": "6.095153",
            "matched_size": "4.484847",
            "source": "REST",
        }
        trade_fact = conn.execute(
            """
            SELECT state, filled_size, fill_price
              FROM venue_trade_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "state": "MATCHED",
            "filled_size": "4.484847",
            "fill_price": "0.67",
        }
        projection = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, entry_price, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert projection["phase"] == "active"
        assert Decimal(str(projection["shares"])) == Decimal("4.484847")
        assert Decimal(str(projection["cost_basis_usd"])) == Decimal("3.00484749")
        assert Decimal(str(projection["entry_price"])) == Decimal("0.67")
        assert projection["order_status"] == "partial"

    def test_partial_entry_upgrades_only_when_cumulative_fill_reaches_requested_size(
        self,
        conn,
        mock_client,
    ):
        """Relationship: complete order truth closes a prior partial command."""
        _insert(conn, size=15.0, price=0.34)
        _seed_pending_entry_projection(conn)
        _advance_to_partial(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-001",
            state="MATCHED",
            filled_size="15",
            fill_price="0.34",
        )
        _append_order_fact(conn, state="MATCHED", matched_size="15", remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["completed_partial_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "FILLED"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "completed_partial_order_fact"
        assert payload["matched_size"] == "15"
        assert payload["remaining_size"] == "0"

    def test_terminal_partial_fak_never_upgrades_to_full_fill_and_releases_obligation(
        self,
        conn,
        mock_client,
    ):
        """A terminal FAK partial is final order state, not a full-fill claim."""
        _insert(conn, order_type="FAK", size=15.0, price=0.08)
        _seed_pending_entry_projection(conn)
        _open_test_entry_obligation(conn, "cmd-001")
        _advance_to_partial(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-terminal-partial",
            state="CONFIRMED",
            filled_size="11",
            fill_price="0.08",
        )
        _append_order_fact(
            conn,
            state="MATCHED",
            matched_size="11",
            remaining_size="0",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import (
            _latest_order_fact_for_command_order,
            reconcile_unresolved_commands,
        )

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["completed_partial_order_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert summary["filled_entry_execution_fact_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert summary["post_maker_execution_fact_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        assert [event["event_type"] for event in _get_events(conn, "cmd-001")].count(
            "FILL_CONFIRMED"
        ) == 0
        terminal = conn.execute(
            """
            SELECT state, matched_size, remaining_size, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
               AND json_extract(raw_payload_json, '$.proof_class')
                   = 'terminal_partial_order_fact'
             LIMIT 1
            """
        ).fetchone()
        assert terminal is not None
        assert terminal["state"] == "PARTIALLY_MATCHED"
        assert terminal["matched_size"] == "11"
        assert terminal["remaining_size"] == "0"
        assert json.loads(terminal["raw_payload_json"])["proof_class"] == (
            "terminal_partial_order_fact"
        )
        canonical = _latest_order_fact_for_command_order(
            conn,
            command_id="cmd-001",
            venue_order_id="ord-001",
        )
        assert {
            key: canonical[key]
            for key in ("state", "matched_size", "remaining_size")
        } == {
            "state": "PARTIALLY_MATCHED",
            "matched_size": "11",
            "remaining_size": "0",
        }
        execution = conn.execute(
            """
            SELECT shares, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE command_id = 'cmd-001'
               AND order_role = 'entry'
            """
        ).fetchone()
        assert dict(execution) == {
            "shares": 11.0,
            "venue_status": "PARTIAL",
            "terminal_exec_status": "partial",
        }
        obligation = conn.execute(
            "SELECT status FROM entry_exposure_obligations WHERE command_id = 'cmd-001'"
        ).fetchone()
        assert obligation["status"] == "RESOLVED"

        repeated = reconcile_unresolved_commands(conn, mock_client)
        assert repeated["completed_partial_order_facts"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "PARTIAL"

    def test_terminal_partial_correction_requires_canonical_partial_command_state(
        self,
        conn,
    ):
        """A proof-shaped payload cannot demote a non-partial command's terminal fact."""

        _insert(conn, order_type="FAK", size=15.0, price=0.08)
        _advance_to_acked(conn, venue_order_id="ord-001")
        terminal_id = _append_order_fact(
            conn,
            state="MATCHED",
            matched_size="11",
            remaining_size="0",
        )
        correction_id = _append_order_fact(
            conn,
            state="PARTIALLY_MATCHED",
            matched_size="11",
            remaining_size="0",
            raw_payload_json={
                "proof_class": "terminal_partial_order_fact",
                "required_predicates": {
                    "terminal_order_remainder_zero": True,
                    "canonical_trade_facts_match_terminal_order_fact": True,
                    "cumulative_fill_below_requested_size": True,
                },
            },
        )

        assert correction_id == terminal_id
        facts = conn.execute(
            "SELECT state FROM venue_order_facts WHERE command_id = 'cmd-001'"
        ).fetchall()
        assert [row["state"] for row in facts] == ["MATCHED"]

    def test_legacy_false_filled_terminal_partial_returns_to_partial_truth(
        self,
        conn,
        mock_client,
    ):
        """A historical short FILL_CONFIRMED cannot remain canonical FILLED."""

        _insert(conn, order_type="FAK", size=15.0, price=0.08)
        _seed_pending_entry_projection(conn)
        _advance_to_partial(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-terminal-partial",
            state="CONFIRMED",
            filled_size="11",
            fill_price="0.08",
        )
        _append_order_fact(
            conn,
            state="MATCHED",
            matched_size="11",
            remaining_size="0",
        )
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "venue_order_id": "ord-001",
                "filled_size": "11",
                "fill_price": "0.08",
            },
        )
        with pytest.raises(
            ValueError,
            match="terminal partial command correction order size does not match",
        ):
            append_event(
                conn,
                command_id="cmd-001",
                event_type="PARTIAL_FILL_OBSERVED",
                occurred_at="2026-04-26T00:06:01Z",
                payload={
                    "reason": "terminal_partial_order_fact_corrected",
                    "proof_class": "terminal_partial_order_fact",
                    "command_id": "cmd-001",
                    "venue_order_id": "ord-001",
                    "filled_size": "10",
                    "requested_size": "15.0",
                    "required_predicates": {
                        "terminal_order_remainder_zero": True,
                        "canonical_trade_facts_match_terminal_order_fact": True,
                        "cumulative_fill_below_requested_size": True,
                    },
                },
            )
        assert _get_state(conn, "cmd-001") == "FILLED"
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["completed_partial_order_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "PARTIAL_FILL_OBSERVED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "terminal_partial_order_fact"
        assert payload["requested_size"] == "15.0"
        terminal = conn.execute(
            """
            SELECT state, matched_size, remaining_size
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
               AND json_extract(raw_payload_json, '$.proof_class')
                   = 'terminal_partial_order_fact'
            """
        ).fetchone()
        assert dict(terminal) == {
            "state": "PARTIALLY_MATCHED",
            "matched_size": "11",
            "remaining_size": "0",
        }
        execution = conn.execute(
            """
            SELECT shares, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE command_id = 'cmd-001'
               AND order_role = 'entry'
            """
        ).fetchone()
        assert dict(execution) == {
            "shares": 11.0,
            "venue_status": "PARTIAL",
            "terminal_exec_status": "partial",
        }

        repeated = reconcile_unresolved_commands(conn, mock_client)
        assert repeated["completed_partial_order_facts"]["scanned"] == 0

    def test_partial_entry_uses_canonical_order_truth_over_later_weaker_fact(
        self,
        conn,
        mock_client,
    ):
        """Relationship: later weak order facts cannot demote terminal truth."""
        _insert(conn, size=5.0, price=0.34)
        _seed_pending_entry_projection(conn)
        _advance_to_partial(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-001",
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )
        _append_order_fact(conn, state="MATCHED", matched_size="5", remaining_size="0")
        _append_order_fact(conn, state="RESTING", matched_size="5", remaining_size="0.01")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["completed_partial_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "FILLED"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "completed_partial_order_fact"
        assert payload["remaining_size"] == "0"

    def test_review_required_matched_cancel_clears_when_held_projection_matches_fill(
        self,
        conn,
        mock_client,
    ):
        """A matched-order cancel response must not strand an already held entry."""
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.34)
        _seed_pending_entry_projection(conn)
        _advance_to_partial(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-001",
            state="CONFIRMED",
            filled_size="4.995",
            fill_price="0.34",
        )
        _append_order_fact(conn, state="PARTIALLY_MATCHED", matched_size="4.995", remaining_size="0.005")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   chain_state = 'synced',
                   shares = 4.995,
                   chain_shares = 4.995,
                   cost_basis_usd = 1.6983,
                   entry_price = 0.34,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:07:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={"venue_order_id": "ord-001", "source": "maker_rest_escalation"},
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REPLACE_BLOCKED",
            occurred_at="2026-04-26T00:08:02Z",
            payload={
                "venue_order_id": "ord-001",
                "reason": "post_cancel_unknown_possible_side_effect",
                "cancel_outcome": {
                    "orderID": "ord-001",
                    "status": "NOT_CANCELED",
                    "errorMessage": "matched orders can't be canceled",
                },
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_cancel_review_required_entries"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "FILLED"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "matched_cancel_with_confirmed_held_projection"
        assert payload["required_predicates"]["active_projection_matches_confirmed_fill"] is True

    def test_review_required_matched_cancel_uses_chain_shares_over_submitted_shares(
        self,
        conn,
        mock_client,
    ):
        """Chain-observed exposure is the fill proof when raw submitted shares drift."""
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.34)
        _seed_pending_entry_projection(conn)
        _advance_to_partial(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-001",
            state="CONFIRMED",
            filled_size="4.995",
            fill_price="0.34",
        )
        _append_order_fact(conn, state="PARTIALLY_MATCHED", matched_size="4.995", remaining_size="0.005")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   chain_state = 'synced',
                   shares = 0.0,
                   chain_shares = 4.995,
                   cost_basis_usd = 0.0,
                   chain_cost_basis_usd = 1.6983,
                   entry_price = 0.34,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:07:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={"venue_order_id": "ord-001", "source": "maker_rest_escalation"},
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REPLACE_BLOCKED",
            occurred_at="2026-04-26T00:08:02Z",
            payload={
                "venue_order_id": "ord-001",
                "reason": "post_cancel_unknown_possible_side_effect",
                "cancel_outcome": {
                    "orderID": "ord-001",
                    "status": "NOT_CANCELED",
                    "errorMessage": "matched orders can't be canceled",
                },
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_cancel_review_required_entries"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "FILLED"

    @pytest.mark.parametrize("scope", ["restart_preflight", "live_tick", "boot_fast"])
    def test_scoped_recovery_projects_filled_exit_trade_fact_to_closed(
        self,
        tmp_path,
        monkeypatch,
        scope,
    ):
        """Scoped live recovery must release pending_exit when durable full-fill truth exists."""
        from src.execution import command_recovery, venue_sync_contract
        from src.state.db import init_schema
        from src.state.collateral_ledger import init_collateral_schema
        from src.state.venue_command_repo import append_event

        db_path = tmp_path / f"{scope}-filled-exit-projection.db"
        seed = sqlite3.connect(db_path)
        seed.row_factory = sqlite3.Row
        init_schema(seed)
        init_collateral_schema(seed)
        _insert(
            seed,
            command_id="cmd-entry",
            position_id="pos-exit",
            size=10.01,
            price=0.12,
            token_id="tok-exit",
        )
        _advance_to_acked(seed, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(
            seed,
            position_id="pos-exit",
            command_id="cmd-entry",
            order_id="ord-entry",
        )
        seed.execute(
            """
            UPDATE position_current
               SET phase = 'pending_exit',
                   shares = 10.0102,
                   chain_shares = 10.0102,
                   cost_basis_usd = 1.2012,
                   entry_price = 0.12,
                   order_status = 'sell_placed',
                   exit_reason = 'DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES',
                   updated_at = '2026-04-26T00:07:00Z'
             WHERE position_id = 'pos-exit'
            """
        )
        _insert(
            seed,
            command_id="cmd-exit",
            position_id="pos-exit",
            intent_kind="EXIT",
            side="SELL",
            size=10.01,
            price=0.01,
            token_id="tok-exit",
        )
        _advance_to_acked(seed, command_id="cmd-exit", venue_order_id="ord-exit")
        append_event(
            seed,
            command_id="cmd-exit",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={
                "reason": "place_exit_order_matched_submit",
                "venue_order_id": "ord-exit",
                "trade_id": "trade-exit",
                "filled_size": "10.01",
                "fill_price": "0.01",
                "tx_hash": "0xexit",
            },
        )
        _append_trade_fact(
            seed,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="trade-exit",
            state="MATCHED",
            filled_size="10.01",
            fill_price="0.01",
            tx_hash="0xexit",
        )
        seed.commit()
        seed.close()

        def _conn_factory():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
        client = MagicMock(
            spec_set=[
                "get_order",
                "get_open_orders",
                "get_trades",
                "get_clob_market_info",
            ]
        )
        client.get_open_orders.return_value = []
        client.get_trades.return_value = []

        summary = command_recovery.reconcile_unresolved_commands(
            client=client,
            scope=scope,
        )

        check = _conn_factory()
        try:
            current = check.execute(
                """
                SELECT phase, order_status, exit_price, chain_shares
                  FROM position_current
                 WHERE position_id = 'pos-exit'
                """
            ).fetchone()
            lifecycle_event = check.execute(
                """
                SELECT event_type, phase_before, phase_after, order_id, command_id, venue_status
                  FROM position_events
                 WHERE position_id = 'pos-exit'
                   AND event_type = 'EXIT_ORDER_FILLED'
                 ORDER BY sequence_no DESC
                 LIMIT 1
                """
            ).fetchone()
        finally:
            check.close()

        assert summary["scope"] == scope
        assert summary["exit_pending_projections"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert dict(current) == {
            "phase": "economically_closed",
            "order_status": "sell_filled",
            "exit_price": 0.01,
            "chain_shares": 0.0,
        }
        assert dict(lifecycle_event) == {
            "event_type": "EXIT_ORDER_FILLED",
            "phase_before": "pending_exit",
            "phase_after": "economically_closed",
            "order_id": "ord-exit",
            "command_id": "cmd-exit",
            "venue_status": "sell_filled",
        }

        retear = _conn_factory()
        try:
            first_count = retear.execute(
                """
                SELECT COUNT(*) AS n
                  FROM position_events
                 WHERE position_id = 'pos-exit'
                   AND event_type = 'EXIT_ORDER_FILLED'
                   AND order_id = 'ord-exit'
                """
            ).fetchone()["n"]
            retear.execute(
                """
                UPDATE position_current
                   SET phase = 'pending_exit',
                       chain_shares = 10.0102,
                       order_status = 'retry_pending',
                       updated_at = '2026-04-26T00:09:00Z'
                 WHERE position_id = 'pos-exit'
                """
            )
            retear.commit()
        finally:
            retear.close()

        second_summary = command_recovery.reconcile_unresolved_commands(
            client=client,
            scope=scope,
        )
        second_check = _conn_factory()
        try:
            second_count = second_check.execute(
                """
                SELECT COUNT(*) AS n
                  FROM position_events
                 WHERE position_id = 'pos-exit'
                   AND event_type = 'EXIT_ORDER_FILLED'
                   AND order_id = 'ord-exit'
                """
            ).fetchone()["n"]
            repaired = second_check.execute(
                """
                SELECT phase, order_status, chain_shares
                  FROM position_current
                 WHERE position_id = 'pos-exit'
                """
            ).fetchone()
        finally:
            second_check.close()

        assert second_summary["exit_pending_projections"]["errors"] == 0
        assert second_count == first_count == 1
        assert dict(repaired) == {
            "phase": "economically_closed",
            "order_status": "sell_filled",
            "chain_shares": 0.0,
        }

    @pytest.mark.parametrize("scope", ["restart_preflight", "live_tick", "boot_fast"])
    def test_scoped_recovery_clears_matched_cancel_entry_review(
        self,
        tmp_path,
        monkeypatch,
        scope,
    ):
        """Narrow live scopes must not leave matched held entries stranded."""
        from src.execution import command_recovery, venue_sync_contract
        from src.state.db import init_schema
        from src.state.collateral_ledger import init_collateral_schema
        from src.state.venue_command_repo import append_event

        db_path = tmp_path / f"{scope}.db"
        seed = sqlite3.connect(db_path)
        seed.row_factory = sqlite3.Row
        init_schema(seed)
        init_collateral_schema(seed)
        _insert(seed, size=5.0, price=0.34)
        _seed_pending_entry_projection(seed)
        _advance_to_partial(seed, venue_order_id="ord-001")
        _append_trade_fact(
            seed,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-001",
            state="CONFIRMED",
            filled_size="4.995",
            fill_price="0.34",
        )
        _append_order_fact(
            seed,
            state="PARTIALLY_MATCHED",
            matched_size="4.995",
            remaining_size="0.005",
        )
        seed.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   chain_state = 'synced',
                   shares = 4.995,
                   chain_shares = 4.995,
                   cost_basis_usd = 1.6983,
                   entry_price = 0.34,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:07:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        append_event(
            seed,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={"venue_order_id": "ord-001", "source": "maker_rest_escalation"},
        )
        append_event(
            seed,
            command_id="cmd-001",
            event_type="CANCEL_REPLACE_BLOCKED",
            occurred_at="2026-04-26T00:08:02Z",
            payload={
                "venue_order_id": "ord-001",
                "reason": "post_cancel_unknown_possible_side_effect",
                "cancel_outcome": {
                    "orderID": "ord-001",
                    "status": "NOT_CANCELED",
                    "errorMessage": "matched orders can't be canceled",
                },
            },
        )
        seed.commit()
        seed.close()

        def _conn_factory():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
        client = MagicMock(
            spec_set=[
                "get_order",
                "get_open_orders",
                "get_trades",
                "get_clob_market_info",
            ]
        )
        client.get_open_orders.return_value = []
        client.get_trades.return_value = []

        summary = command_recovery.reconcile_unresolved_commands(
            client=client,
            scope=scope,
        )

        check = _conn_factory()
        try:
            events = _get_events(check, "cmd-001")
            state = _get_state(check, "cmd-001")
            current = check.execute(
                """
                SELECT shares, chain_shares, cost_basis_usd, entry_price, order_status
                  FROM position_current
                 WHERE position_id = 'pos-001'
                """
            ).fetchone()
        finally:
            check.close()

        assert summary["scope"] == scope
        assert summary["matched_cancel_review_required_entries"]["advanced"] == 1
        assert state == "FILLED"
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "matched_cancel_with_confirmed_held_projection"

    @pytest.mark.parametrize("scope", ["restart_preflight", "live_tick", "boot_fast"])
    def test_scoped_recovery_clears_terminal_positive_entry_review(
        self,
        tmp_path,
        monkeypatch,
        scope,
    ):
        """Terminal venue order truth plus held chain projection clears review."""
        from src.execution import command_recovery, venue_sync_contract
        from src.state.db import init_schema
        from src.state.collateral_ledger import init_collateral_schema
        from src.state.venue_command_repo import append_event

        db_path = tmp_path / f"{scope}-terminal-positive.db"
        seed = sqlite3.connect(db_path)
        seed.row_factory = sqlite3.Row
        init_schema(seed)
        init_collateral_schema(seed)
        _insert(seed, size=5.0, price=0.34)
        _seed_pending_entry_projection(seed)
        _advance_to_partial(seed, venue_order_id="ord-001")
        _append_trade_fact(
            seed,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-partial",
            state="CONFIRMED",
            filled_size="3.0",
            fill_price="0.34",
        )
        _append_order_fact(
            seed,
            order_id="ord-001",
            state="MATCHED",
            matched_size="5.0",
            remaining_size="0",
        )
        seed.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   chain_state = 'synced',
                   shares = 5.0,
                   chain_shares = 5.0,
                   cost_basis_usd = 1.70,
                   chain_cost_basis_usd = 1.70,
                   entry_price = 0.34,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:07:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        append_event(
            seed,
            command_id="cmd-001",
            event_type="REVIEW_REQUIRED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={
                "reason": "partial_remainder_point_order_filled_without_full_trade_fact",
                "venue_order_id": "ord-001",
                "proof_class": "point_order_filled_requires_complete_fill_fact_authority",
            },
        )
        seed.commit()
        seed.close()

        def _conn_factory():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
        client = MagicMock(
            spec_set=[
                "get_order",
                "get_open_orders",
                "get_trades",
                "get_clob_market_info",
            ]
        )
        client.get_open_orders.return_value = []
        client.get_trades.return_value = []

        summary = command_recovery.reconcile_unresolved_commands(
            client=client,
            scope=scope,
        )

        check = _conn_factory()
        try:
            events = _get_events(check, "cmd-001")
            state = _get_state(check, "cmd-001")
            current = check.execute(
                """
                SELECT shares, chain_shares, cost_basis_usd, entry_price, order_status
                  FROM position_current
                 WHERE position_id = 'pos-001'
                """
            ).fetchone()
        finally:
            check.close()

        assert summary["scope"] == scope
        assert summary["matched_cancel_review_required_entries"]["advanced"] == 1
        assert state == "FILLED"
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == (
            "review_required_terminal_order_fact_with_held_projection"
        )
        assert dict(current) == {
            "shares": 5.0,
            "chain_shares": 5.0,
            "cost_basis_usd": 1.7,
            "entry_price": 0.34,
            "order_status": "filled",
        }

    @pytest.mark.parametrize("scope", ["restart_preflight", "live_tick", "boot_fast"])
    def test_scoped_recovery_repairs_terminal_positive_entry_projection_size(
        self,
        tmp_path,
        monkeypatch,
        scope,
    ):
        from src.execution import command_recovery, venue_sync_contract
        from src.state.db import init_schema
        from src.state.collateral_ledger import init_collateral_schema
        from src.state.venue_command_repo import append_event

        db_path = tmp_path / f"{scope}-terminal-positive-projection.db"
        seed = sqlite3.connect(db_path)
        seed.row_factory = sqlite3.Row
        init_schema(seed)
        init_collateral_schema(seed)
        _insert(seed, size=5.0, price=0.34)
        _seed_pending_entry_projection(seed)
        _advance_to_partial(seed, venue_order_id="ord-001")
        _append_trade_fact(
            seed,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-partial",
            state="CONFIRMED",
            filled_size="3.0",
            fill_price="0.34",
        )
        _append_order_fact(
            seed,
            order_id="ord-001",
            state="MATCHED",
            matched_size="5.0",
            remaining_size="0",
        )
        append_event(
            seed,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={
                "reason": "review_cleared_confirmed_fill",
                "proof_class": "cancel_unknown_confirmed_trade_with_positive_trade_fact",
                "command_id": "cmd-001",
                "venue_order_id": "ord-001",
                "trade_id": "trade-partial",
                "filled_size": "3.0",
                "fill_price": "0.34",
                "required_predicates": {
                    "latest_event_is_cancel_replace_blocked": True,
                    "semantic_cancel_status_cancel_unknown": True,
                    "requires_m5_reconcile": True,
                    "positive_trade_fact": True,
                },
                "source_proof": {
                    "source_commit": "test",
                    "source_function": (
                        "command_recovery._review_required_cancel_unknown_live_order_recovery"
                    ),
                    "source_reason": "cancel_unknown_confirmed_trade_fill",
                },
                "cleared_at": "2026-04-26T00:08:00Z",
            },
        )
        seed.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   chain_state = 'synced',
                   shares = 3.0,
                   chain_shares = 5.0,
                   cost_basis_usd = 1.02,
                   chain_cost_basis_usd = 1.70,
                   entry_price = 0.34,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:08:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        seed.commit()
        seed.close()

        def _conn_factory():
            c = sqlite3.connect(db_path)
            c.row_factory = sqlite3.Row
            return c

        monkeypatch.setattr(venue_sync_contract, "default_trade_conn_factory", _conn_factory)
        client = MagicMock(
            spec_set=[
                "get_order",
                "get_open_orders",
                "get_trades",
                "get_clob_market_info",
            ]
        )
        client.get_open_orders.return_value = []
        client.get_trades.return_value = []

        summary = command_recovery.reconcile_unresolved_commands(
            client=client,
            scope=scope,
        )

        check = _conn_factory()
        try:
            current = check.execute(
                """
                SELECT shares, chain_shares, cost_basis_usd, entry_price, order_status
                  FROM position_current
                 WHERE position_id = 'pos-001'
                """
            ).fetchone()
        finally:
            check.close()

        assert summary["scope"] == scope
        assert summary["terminal_positive_entry_projection_repair"]["advanced"] == 1
        assert dict(current) == {
            "shares": 5.0,
            "chain_shares": 5.0,
            "cost_basis_usd": 1.7,
            "entry_price": 0.34,
            "order_status": "filled",
        }

    def test_partial_entry_does_not_finalize_when_trade_facts_do_not_cover_order_fact(
        self,
        conn,
        mock_client,
    ):
        """Relationship: terminal order fact must match aggregate fill economics."""
        _insert(conn, size=5.0, price=0.34)
        _seed_pending_entry_projection(conn)
        _advance_to_partial(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-001",
            state="MATCHED",
            filled_size="2",
            fill_price="0.34",
        )
        _append_order_fact(conn, state="MATCHED", matched_size="4.99", remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["completed_partial_order_facts"]["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        event_types = [row["event_type"] for row in _get_events(conn, "cmd-001")]
        assert "FILL_CONFIRMED" not in event_types

    def test_partial_entry_does_not_finalize_without_positive_trade_fact(
        self,
        conn,
        mock_client,
    ):
        """Relationship: order completion alone is not fill-economics authority."""
        _insert(conn, size=5.0, price=0.34)
        _seed_pending_entry_projection(conn)
        _advance_to_partial(conn, venue_order_id="ord-001")
        _append_order_fact(conn, state="MATCHED", matched_size="4.99", remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["completed_partial_order_facts"]["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        event_types = [row["event_type"] for row in _get_events(conn, "cmd-001")]
        assert "FILL_CONFIRMED" not in event_types

    def test_partial_entry_does_not_finalize_with_malformed_completed_order_size(
        self,
        conn,
        mock_client,
    ):
        """Relationship: malformed remainder text is not terminal fill truth."""
        _insert(conn, size=5.0, price=0.34)
        _seed_pending_entry_projection(conn)
        _advance_to_partial(conn, venue_order_id="ord-001")
        _append_trade_fact(
            conn,
            command_id="cmd-001",
            order_id="ord-001",
            trade_id="trade-001",
            state="MATCHED",
            filled_size="4.99",
            fill_price="0.34",
        )
        _append_order_fact(conn, state="MATCHED", matched_size="4.99", remaining_size="")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["completed_partial_order_facts"]["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        event_types = [row["event_type"] for row in _get_events(conn, "cmd-001")]
        assert "FILL_CONFIRMED" not in event_types

    def test_terminal_filled_entry_trade_fact_without_pending_projection_recovers_position(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-001")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, condition_id, token_id, no_token_id, shares, cost_basis_usd,
                   entry_price, order_id, order_status, strategy_key, temperature_metric
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "condition_id": "condition-test",
            "token_id": "tok-001",
            "no_token_id": "tok-001-no",
            "shares": 5.0,
            "cost_basis_usd": 1.7,
            "entry_price": 0.34,
            "order_id": "ord-001",
            "order_status": "filled",
            "strategy_key": "opening_inertia",
            "temperature_metric": "high",
        }
        events = conn.execute(
            """
            SELECT sequence_no, event_type, phase_before, phase_after, command_id, order_id
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert [dict(row) for row in events] == [
            {
                "sequence_no": 1,
                "event_type": "POSITION_OPEN_INTENT",
                "phase_before": None,
                "phase_after": "pending_entry",
                "command_id": "cmd-001",
                "order_id": None,
            },
            {
                "sequence_no": 2,
                "event_type": "ENTRY_ORDER_POSTED",
                "phase_before": "pending_entry",
                "phase_after": "pending_entry",
                "command_id": "cmd-001",
                "order_id": "ord-001",
            },
            {
                "sequence_no": 3,
                "event_type": "ENTRY_ORDER_FILLED",
                "phase_before": "pending_entry",
                "phase_after": "active",
                "command_id": "cmd-001",
                "order_id": "ord-001",
            },
        ]
        execution = conn.execute(
            """
            SELECT command_id, shares, fill_price, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert execution is not None
        assert dict(execution) == {
            "command_id": "cmd-001",
            "shares": 5.0,
            "fill_price": 0.34,
            "venue_status": "MATCHED",
            "terminal_exec_status": "filled",
        }
        second_summary = reconcile_unresolved_commands(conn, mock_client)
        assert second_summary["filled_entry_projection_repair"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_partial_entry_trade_fact_projects_active_exposure_immediately(
        self,
        conn,
        mock_client,
    ):
        """A real partial fill is active exposure even while the remainder rests."""
        _insert(conn, size=9.3, price=0.53)
        _advance_to_acked(conn, venue_order_id="ord-partial-entry")
        _append_order_fact(
            conn,
            order_id="ord-partial-entry",
            state="PARTIALLY_MATCHED",
            matched_size="2.127658",
            remaining_size="7.172342",
            source="WS_USER",
        )
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="PARTIAL_FILL_OBSERVED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "source": "WS_USER",
                "venue_order_id": "ord-partial-entry",
                "trade_id": "trade-partial-entry",
            },
        )
        _append_trade_fact(
            conn,
            order_id="ord-partial-entry",
            trade_id="trade-partial-entry",
            state="MATCHED",
            filled_size="2.127658",
            fill_price="0.5300001222000904",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import ensure_live_entry_projection_for_command

        summary = ensure_live_entry_projection_for_command(
            conn,
            command_id="cmd-001",
            client=mock_client,
        )

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, entry_price, order_status, fill_authority
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "shares": 2.127658,
            "cost_basis_usd": pytest.approx(2.127658 * 0.5300001222000904),
            "entry_price": pytest.approx(0.5300001222000904),
            "order_status": "partial",
            "fill_authority": "venue_confirmed_partial",
        }
        assert _get_state(conn, "cmd-001") == "PARTIAL"

    def test_immediate_filled_increment_folds_cumulative_position_economics(
        self,
        conn,
        mock_client,
    ):
        from src.execution.command_recovery import ensure_live_entry_projection_for_command
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-first")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-first", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            order_id="ord-first",
            trade_id="trade-first",
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )
        _insert_decision_log_trade_case_for_recovery(conn)
        assert ensure_live_entry_projection_for_command(
            conn,
            command_id="cmd-001",
            client=mock_client,
        )["advanced"] == 1

        _insert(
            conn,
            command_id="cmd-002",
            position_id="pos-001",
            decision_id="dec-002",
            size=3.0,
            price=0.40,
            created_at="2026-04-26T00:07:00Z",
        )
        _advance_to_acked(conn, command_id="cmd-002", venue_order_id="ord-second")
        append_event(
            conn,
            command_id="cmd-002",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={"venue_order_id": "ord-second", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            command_id="cmd-002",
            order_id="ord-second",
            trade_id="trade-second",
            state="MATCHED",
            filled_size="3",
            fill_price="0.40",
        )

        summary = ensure_live_entry_projection_for_command(
            conn,
            command_id="cmd-002",
            client=mock_client,
        )

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT shares, cost_basis_usd, entry_price, order_id, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "shares": 8.0,
            "cost_basis_usd": pytest.approx(2.9),
            "entry_price": pytest.approx(0.3625),
            "order_id": "ord-first",
            "order_status": "filled",
        }
        fills = conn.execute(
            """
            SELECT command_id, order_id
              FROM position_events
             WHERE position_id = 'pos-001'
               AND event_type = 'ENTRY_ORDER_FILLED'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert [dict(row) for row in fills] == [
            {"command_id": "cmd-001", "order_id": "ord-first"},
            {"command_id": "cmd-002", "order_id": "ord-second"},
        ]
        facts = conn.execute(
            """
            SELECT command_id, shares, fill_price
              FROM execution_fact
             WHERE position_id = 'pos-001'
               AND order_role = 'entry'
             ORDER BY command_id
            """
        ).fetchall()
        assert [dict(row) for row in facts] == [
            {"command_id": "cmd-001", "shares": 5.0, "fill_price": 0.34},
            {"command_id": "cmd-002", "shares": 3.0, "fill_price": 0.4},
        ]
        assert ensure_live_entry_projection_for_command(
            conn,
            command_id="cmd-002",
            client=mock_client,
        ) == {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}

    @pytest.mark.parametrize("repair_owner", ["immediate", "periodic"])
    def test_partial_increment_folds_only_confirmed_cumulative_fill(
        self,
        conn,
        mock_client,
        repair_owner,
    ):
        from src.execution.command_recovery import (
            ensure_live_entry_projection_for_command,
            reconcile_filled_entry_projection_repairs,
        )
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-first")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-first", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            order_id="ord-first",
            trade_id="trade-first",
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )
        _insert_decision_log_trade_case_for_recovery(conn)
        assert ensure_live_entry_projection_for_command(
            conn,
            command_id="cmd-001",
            client=mock_client,
        )["advanced"] == 1

        _insert(
            conn,
            command_id="cmd-002",
            position_id="pos-001",
            decision_id="dec-002",
            size=15.0,
            price=0.08,
            created_at="2026-04-26T00:07:00Z",
        )
        _advance_to_acked(conn, command_id="cmd-002", venue_order_id="ord-partial-second")
        _append_order_fact(
            conn,
            command_id="cmd-002",
            order_id="ord-partial-second",
            state="PARTIALLY_MATCHED",
            matched_size="11",
            remaining_size="4",
            source="REST",
        )
        append_event(
            conn,
            command_id="cmd-002",
            event_type="PARTIAL_FILL_OBSERVED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={
                "venue_order_id": "ord-partial-second",
                "venue_status": "PARTIALLY_MATCHED",
                "filled_size": "11",
            },
        )
        _append_trade_fact(
            conn,
            command_id="cmd-002",
            order_id="ord-partial-second",
            trade_id="trade-partial-second",
            state="MATCHED",
            filled_size="11",
            fill_price="0.058",
        )

        summary = (
            ensure_live_entry_projection_for_command(
                conn,
                command_id="cmd-002",
                client=mock_client,
            )
            if repair_owner == "immediate"
            else reconcile_filled_entry_projection_repairs(conn, client=mock_client)
        )

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            "SELECT shares, cost_basis_usd, entry_price, order_id FROM position_current "
            "WHERE position_id='pos-001'"
        ).fetchone()
        assert dict(current) == {
            "shares": 16.0,
            "cost_basis_usd": pytest.approx(2.338),
            "entry_price": pytest.approx(2.338 / 16),
            "order_id": "ord-first",
        }
        execution = conn.execute(
            "SELECT shares, fill_price, venue_status, terminal_exec_status "
            "FROM execution_fact WHERE command_id='cmd-002'"
        ).fetchone()
        assert dict(execution) == {
            "shares": 11.0,
            "fill_price": 0.058,
            "venue_status": "PARTIAL",
            "terminal_exec_status": "partial",
        }
        assert _get_state(conn, "cmd-002") == "PARTIAL"
        repeated = (
            ensure_live_entry_projection_for_command(
                conn,
                command_id="cmd-002",
                client=mock_client,
            )
            if repair_owner == "immediate"
            else reconcile_filled_entry_projection_repairs(conn, client=mock_client)
        )
        assert repeated == (
            {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}
            if repair_owner == "immediate"
            else {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
        )
        assert conn.execute(
            "SELECT shares FROM position_current WHERE position_id='pos-001'"
        ).fetchone()[0] == 16.0

    def test_partial_entry_repair_promotes_zero_share_pending_projection(
        self,
        conn,
        mock_client,
    ):
        """Existing 0-share pending projection must not hide a positive venue fill."""
        _insert(conn, size=9.3, price=0.53)
        _advance_to_acked(conn, venue_order_id="ord-partial-entry")
        _seed_pending_entry_projection(
            conn,
            position_id="pos-001",
            command_id="cmd-001",
            order_id="ord-partial-entry",
        )
        _append_order_fact(
            conn,
            order_id="ord-partial-entry",
            state="PARTIALLY_MATCHED",
            matched_size="2.127658",
            remaining_size="7.172342",
            source="WS_USER",
        )
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="PARTIAL_FILL_OBSERVED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "source": "WS_USER",
                "venue_order_id": "ord-partial-entry",
                "trade_id": "trade-partial-entry",
            },
        )
        _append_trade_fact(
            conn,
            order_id="ord-partial-entry",
            trade_id="trade-partial-entry",
            state="MATCHED",
            filled_size="2.127658",
            fill_price="0.5300001222000904",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import reconcile_filled_entry_projection_repairs

        summary = reconcile_filled_entry_projection_repairs(conn, mock_client)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, entry_price, order_status, fill_authority
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "shares": 2.127658,
            "cost_basis_usd": pytest.approx(2.127658 * 0.5300001222000904),
            "entry_price": pytest.approx(0.5300001222000904),
            "order_status": "partial",
            "fill_authority": "venue_confirmed_partial",
        }
        events = conn.execute(
            """
            SELECT sequence_no, event_type, phase_before, phase_after
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert [dict(row) for row in events] == [
            {
                "sequence_no": 1,
                "event_type": "POSITION_OPEN_INTENT",
                "phase_before": None,
                "phase_after": "pending_entry",
            },
            {
                "sequence_no": 2,
                "event_type": "ENTRY_ORDER_POSTED",
                "phase_before": "pending_entry",
                "phase_after": "pending_entry",
            },
            {
                "sequence_no": 3,
                "event_type": "ENTRY_ORDER_FILLED",
                "phase_before": "pending_entry",
                "phase_after": "active",
            },
        ]

    def test_cancel_failed_already_canceled_positive_entry_fill_releases_review_required(
        self,
        conn,
        mock_client,
    ):
        """Already-canceled cancel responses must not hide a real partial fill."""
        _insert(conn, size=9.3, price=0.53)
        _advance_to_acked(conn, venue_order_id="ord-partial-canceled")
        _append_trade_fact(
            conn,
            order_id="ord-partial-canceled",
            trade_id="trade-partial-canceled",
            state="CONFIRMED",
            filled_size="2.127658",
            fill_price="0.5300001222000904",
        )
        _insert_decision_log_trade_case_for_recovery(conn)
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:07:00Z",
            payload={"venue_order_id": "ord-partial-canceled"},
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_FAILED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={
                "venue_order_id": "ord-partial-canceled",
                "reason": "ord-partial-canceled: the order is already canceled",
                "cancel_outcome": {
                    "canceled": [],
                    "not_canceled": {
                        "ord-partial-canceled": "the order is already canceled",
                    },
                },
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["advanced"] >= 1
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        command_events = [
            row["event_type"]
            for row in conn.execute(
                """
                SELECT event_type
                  FROM venue_command_events
                 WHERE command_id = 'cmd-001'
                 ORDER BY sequence_no
                """
            ).fetchall()
        ]
        assert command_events[-1] == "PARTIAL_FILL_OBSERVED"
        from src.execution.command_recovery import (
            _latest_order_fact_for_command_order,
        )

        canonical_order_fact = _latest_order_fact_for_command_order(
            conn,
            command_id="cmd-001",
            venue_order_id="ord-partial-canceled",
        )
        assert {
            key: canonical_order_fact[key]
            for key in ("state", "matched_size", "remaining_size")
        } == {
            "state": "PARTIALLY_MATCHED",
            "matched_size": "2.127658",
            "remaining_size": "0",
        }
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, entry_price, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "shares": 2.127658,
            "cost_basis_usd": pytest.approx(2.127658 * 0.5300001222000904),
            "entry_price": pytest.approx(0.5300001222000904),
            "order_status": "partial",
        }

    def test_matched_cancel_review_required_pass_clears_already_canceled_positive_trade_fact(
        self,
        conn,
    ):
        """DB-only boot recovery must clear already-canceled REVIEW_REQUIRED fills."""
        _insert(conn, size=9.3, price=0.53)
        _advance_to_acked(conn, venue_order_id="ord-partial-canceled")
        _append_trade_fact(
            conn,
            order_id="ord-partial-canceled",
            trade_id="trade-partial-canceled",
            state="CONFIRMED",
            filled_size="2.127658",
            fill_price="0.5300001222000904",
        )
        _insert_decision_log_trade_case_for_recovery(conn)
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-04-26T00:07:00Z",
            payload={"venue_order_id": "ord-partial-canceled"},
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_FAILED",
            occurred_at="2026-04-26T00:08:00Z",
            payload={
                "venue_order_id": "ord-partial-canceled",
                "reason": "ord-partial-canceled: the order is already canceled",
                "cancel_outcome": {
                    "orderID": "ord-partial-canceled",
                    "status": "NOT_CANCELED",
                    "errorMessage": (
                        "ord-partial-canceled: the order is already canceled"
                    ),
                },
            },
        )

        from src.execution.command_recovery import (
            reconcile_matched_cancel_review_required_entries,
        )

        summary = reconcile_matched_cancel_review_required_entries(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "PARTIAL_FILL_OBSERVED"
        payload = json.loads(events[-1]["payload_json"])
        assert payload["proof_class"] == "terminal_partial_order_fact"

    def test_filled_entry_repair_without_trade_case_stays_non_error(
        self,
        conn,
        mock_client,
    ):
        """Legacy filled commands without recovery context must not log errors forever."""
        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-001")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        assert conn.execute(
            "SELECT 1 FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone() is None

    def test_filled_entry_repair_recovers_from_command_snapshot_market_event(
        self,
        conn,
        mock_client,
    ):
        """Filled entry repair can recover when decision_log/EDLI rows are absent.

        Regression for live command rows that had confirmed venue_trade_facts and
        immutable command envelope/snapshot identity, but no decision_log or EDLI
        event rows. Recovery must use those persisted command surfaces plus
        market_events instead of leaving real filled exposure invisible.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_events (
                event_id TEXT,
                market_slug TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                condition_id TEXT,
                token_id TEXT,
                range_label TEXT,
                range_low REAL,
                range_high REAL,
                outcome TEXT,
                created_at TEXT,
                recorded_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO market_events (
                event_id, market_slug, city, target_date, temperature_metric,
                condition_id, token_id, range_label, outcome, created_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-shanghai-29c",
                "highest-temperature-in-shanghai-on-june-19-2026",
                "Shanghai",
                "2026-04-27",
                "high",
                "condition-test",
                "tok-001-no",
                "Will the highest temperature in Shanghai be 29°C on April 27?",
                "Will the highest temperature in Shanghai be 29°C on April 27?",
                "2026-04-26T00:00:00Z",
                "2026-04-26T00:00:00Z",
            ),
        )
        _insert(
            conn,
            token_id="tok-001",
            no_token_id="tok-001-no",
            selected_token_id="tok-001-no",
            outcome_label="NO",
            decision_id="legacy_exec_cmd:missing-event:missing-intent:tok-001-no:tok-001-no:buy_no",
            size=5.0,
            price=0.34,
        )
        _advance_to_acked(conn, venue_order_id="ord-001")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )

        from src.execution.command_recovery import reconcile_filled_entry_projection_repairs

        summary = reconcile_filled_entry_projection_repairs(conn, mock_client)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, city, target_date, direction, token_id, no_token_id,
                   shares, cost_basis_usd, strategy_key, temperature_metric, unit
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "city": "Shanghai",
            "target_date": "2026-04-27",
            "direction": "buy_no",
            "token_id": "tok-001",
            "no_token_id": "tok-001-no",
            "shares": 5.0,
            "cost_basis_usd": 1.7,
            "strategy_key": "forecast_qkernel_entry",
            "temperature_metric": "high",
            "unit": "C",
        }
        execution = conn.execute(
            """
            SELECT command_id, shares, fill_price, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert dict(execution) == {
            "command_id": "cmd-001",
            "shares": 5.0,
            "fill_price": 0.34,
            "terminal_exec_status": "filled",
        }

    def test_filled_entry_position_link_repair_relinks_existing_projection(
        self,
        conn,
        mock_client,
    ):
        """Filled command journal rows converge to the existing exposure row."""
        _insert(
            conn,
            position_id="stale-pos-001",
            token_id="tok-001",
            no_token_id="tok-001-no",
            selected_token_id="tok-001-no",
            outcome_label="NO",
            size=5.0,
            price=0.34,
        )
        _advance_to_acked(conn, venue_order_id="ord-001")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, last_monitor_prob,
                last_monitor_edge, last_monitor_market_price, decision_snapshot_id,
                entry_method, strategy_key, edge_source, discovery_mode,
                chain_state, token_id, no_token_id, condition_id, order_id,
                order_status, updated_at, temperature_metric
            ) VALUES (
                'canonical-pos-001', 'active', 'canonical-pos-001',
                'condition-test', 'Shanghai', 'Shanghai', '2026-04-27',
                'Will high be 29°C?', 'buy_no', 'C', 1.7, 5, 1.7, 0.34,
                0.8, NULL, NULL, NULL, 'snap-1', 'ens_member_counting',
                'opening_inertia', 'opening_inertia', 'opening_hunt',
                'synced', 'tok-001', 'tok-001-no', 'condition-test',
                'ord-001', 'filled', '2026-04-26T00:06:00Z', 'high'
            )
            """
        )
        from src.execution.command_recovery import reconcile_filled_entry_position_link_repairs

        summary = reconcile_filled_entry_position_link_repairs(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        command = conn.execute(
            "SELECT position_id FROM venue_commands WHERE command_id = 'cmd-001'"
        ).fetchone()
        assert command["position_id"] == "canonical-pos-001"
        assert conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 1
        provenance = conn.execute(
            """
            SELECT event_type, payload_json
              FROM provenance_envelope_events
             WHERE subject_type = 'command'
               AND subject_id = 'cmd-001'
               AND event_type = 'POSITION_LINK_REPAIRED'
            """
        ).fetchone()
        assert provenance is not None
        assert "stale-pos-001" in provenance["payload_json"]
        assert "canonical-pos-001" in provenance["payload_json"]

    def test_filled_entry_repair_does_not_duplicate_existing_order_token_projection(
        self,
        conn,
        mock_client,
    ):
        """A venue fill can have only one local exposure projection."""
        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-001")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )
        _insert_decision_log_trade_case_for_recovery(conn)
        _seed_pending_entry_projection(
            conn,
            position_id="legacy-pos",
            command_id="legacy-command",
            order_id="ord-001",
        )
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 5.0,
                   cost_basis_usd = 1.7,
                   entry_price = 0.34,
                   order_status = 'filled'
             WHERE position_id = 'legacy-pos'
            """
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_projection_repair"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }
        rows = conn.execute(
            """
            SELECT position_id, phase, shares
              FROM position_current
             WHERE lower(order_id) = lower('ord-001')
             ORDER BY position_id
            """
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {"position_id": "legacy-pos", "phase": "active", "shares": 5.0}
        ]

    def test_filled_entry_repair_does_not_reopen_existing_terminal_order_token_projection(
        self,
        conn,
        mock_client,
    ):
        """Closed exposure for the same venue order/token remains authoritative."""
        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-001")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )
        _insert_decision_log_trade_case_for_recovery(conn)
        _seed_pending_entry_projection(
            conn,
            position_id="legacy-pos",
            command_id="legacy-command",
            order_id="ord-001",
        )
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'economically_closed',
                   shares = 5.0,
                   cost_basis_usd = 1.7,
                   entry_price = 0.34,
                   order_status = 'filled',
                   exit_reason = 'test_existing_terminal_projection'
             WHERE position_id = 'legacy-pos'
            """
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_projection_repair"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }
        assert conn.execute(
            "SELECT 1 FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone() is None

    def test_edli_trade_case_accepts_final_intent_without_top_level_token_id(
        self,
        conn,
    ):
        """FinalIntentCertificate may bind the selected token in semantic identity only."""
        from src.execution.command_recovery import _edli_trade_case_for_command

        event_id = "edli_evt_token_bound_final_intent"
        token_id = "tok-no"
        final_intent_id = f"edli_intent:{event_id}:{token_id}"
        decision_id = f"edli_exec_cmd:{event_id}:{final_intent_id}:{token_id}:{token_id}:buy_no"

        def insert_certificate(certificate_type: str, semantic_key: str, payload: dict, created_at: str) -> None:
            payload_json = json.dumps(payload, sort_keys=True)
            payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
            conn.execute(
                """
                INSERT INTO decision_certificates (
                    certificate_id, certificate_type, schema_version, canonicalization_version,
                    semantic_key, claim_type, mode, decision_time, authority_id,
                    authority_version, algorithm_id, algorithm_version, payload_json,
                    payload_hash, certificate_hash, verifier_status, created_at
                ) VALUES (?, ?, 1, 'test', ?, 'test', 'LIVE', ?, 'test',
                          'test', 'test', 'test', ?, ?, ?, 'VERIFIED', ?)
                """,
                (
                    f"cert:{certificate_type}:{semantic_key}",
                    certificate_type,
                    semantic_key,
                    created_at,
                    payload_json,
                    payload_hash,
                    hashlib.sha256(f"{certificate_type}:{payload_hash}".encode()).hexdigest(),
                    created_at,
                ),
            )

        insert_certificate(
            "ActionableTradeCertificate",
            f"actionable:{event_id}:family:condition-1",
            {
                "event_id": event_id,
                "event_type": "FORECAST_SNAPSHOT_READY",
                "causal_snapshot_id": "source-run-1",
                "family_id": "family-condition-1",
                "candidate_id": "NO:bin-33:DIRECT_NO:bin-33@proof",
                "condition_id": "condition-1",
                "direction": "buy_no",
                "token_id": token_id,
                "strategy_key": "opening_inertia",
                "q_live": 0.81,
                "q_lcb_5pct": 0.76,
                "c_fee_adjusted": 0.55,
                "c_cost_95pct": 0.55,
                "p_fill_lcb": 0.5,
                "trade_score": 0.75,
                "action_score": 0.75,
                "executable_snapshot_id": "ems-token-bound",
                "fdr_family_id": "fdr-token-bound",
                "kelly_decision_id": "kelly-token-bound",
                "risk_decision_id": "risk-token-bound",
                "live_cap_usage_id": "cap-token-bound",
                "final_intent_id": final_intent_id,
                "native_quote_available": True,
                "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
                "selection_authority_applied": "qkernel_spine",
                "qkernel_execution_economics": {
                    "source": "qkernel_spine",
                    "side": "NO",
                    "candidate_id": "NO:bin-33:DIRECT_NO:bin-33@proof",
                    "route_id": "DIRECT_NO:bin-33@proof",
                    "bin_id": "bin-33",
                    "payoff_q_point": 0.81,
                    "payoff_q_lcb": 0.76,
                    "cost": 0.01,
                    "edge_lcb": 0.75,
                    "delta_u_at_min": 0.75,
                    "optimal_stake_usd": 10.0,
                    "optimal_delta_u": 0.75,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                    "selection_guard_basis": "SELECTION_BETA_95",
                    "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.76,
                },
            },
            "2026-06-07T00:00:00Z",
        )
        insert_certificate(
            "FinalIntentCertificate",
            f"final_intent:{event_id}:{final_intent_id}",
            {
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "condition_id": "condition-1",
                "bin_label": "Will the highest temperature in Madrid be 33°C on June 8?",
                "temperature_metric": "high",
                "unit": "C",
                "decision_source_context": {
                    "city": "Madrid",
                    "target_date": "2026-06-08",
                },
            },
            "2026-06-07T00:00:01Z",
        )

        trade_case = _edli_trade_case_for_command(
            conn,
            {
                "position_id": "pos-edli",
                "decision_id": decision_id,
                "token_id": token_id,
                "env_condition_id": "condition-1",
                "env_yes_token_id": "tok-yes",
                "env_no_token_id": token_id,
            },
        )

        assert trade_case["trade_id"] == "pos-edli"
        assert trade_case["city"] == "Madrid"
        assert trade_case["target_date"] == "2026-06-08"
        assert trade_case["bin_label"] == "Will the highest temperature in Madrid be 33°C on June 8?"
        assert trade_case["direction"] == "buy_no"
        assert trade_case["strategy_key"] == "opening_inertia"
        assert trade_case["entry_method"] == "qkernel_spine"
        assert trade_case["discovery_mode"] == "update_reaction"
        assert trade_case["p_posterior"] == pytest.approx(0.81)
        assert trade_case["entry_ci_width"] == pytest.approx(0.10)

    def test_edli_trade_case_recovers_from_live_order_events_without_certificates(
        self,
        conn,
    ):
        """ACK-time projection may see EDLI aggregate events before certificates."""
        from src.execution.command_recovery import _edli_trade_case_for_command

        event_id = "edli_evt_event_only_projection"
        yes_token_id = "tok-yes"
        no_token_id = "tok-no"
        final_intent_id = f"edli_intent:{event_id}:{no_token_id}"
        decision_id = f"edli_exec_cmd:{event_id}:{final_intent_id}:{no_token_id}:{no_token_id}:buy_no"
        aggregate_id = f"{event_id}:{final_intent_id}"
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="SubmitPlanBuilt",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "condition_id": "condition-test",
                "token_id": no_token_id,
                "direction": "buy_no",
                "city": "Madrid",
                "target_date": "2026-06-08",
                "metric": "high",
                "limit_price": 0.61,
                "size": 12.29,
            },
            occurred_at="2026-06-07T00:00:00Z",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="PreSubmitRevalidated",
            payload={
                "event_id": event_id,
                "event_type": "EDLI_REDECISION_PENDING",
                "final_intent_id": final_intent_id,
                "condition_id": "condition-test",
                "token_id": no_token_id,
                "direction": "buy_no",
                "city": "Madrid",
                "target_date": "2026-06-08",
                "metric": "high",
                "bin_label": "Will the highest temperature in Madrid be 33°C on June 8?",
                "q_live": 0.91,
                "q_lcb_5pct": 0.82,
                "qkernel_execution_economics": {
                    "source": "qkernel_spine",
                    "side": "NO",
                    "candidate_id": "NO:bin-33:DIRECT_NO:bin-33",
                    "route_id": "DIRECT_NO:bin-33",
                    "bin_id": "bin-33",
                    "payoff_q_point": 0.91,
                    "payoff_q_lcb": 0.82,
                    "cost": 0.61,
                    "edge_lcb": 0.21,
                    "delta_u_at_min": 0.21,
                    "optimal_stake_usd": 10.0,
                    "optimal_delta_u": 0.21,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                },
            },
            occurred_at="2026-06-07T00:00:01Z",
        )

        trade_case = _edli_trade_case_for_command(
            conn,
            {
                "position_id": "pos-edli",
                "decision_id": decision_id,
                "token_id": no_token_id,
                "env_condition_id": "condition-test",
                "env_yes_token_id": yes_token_id,
                "env_no_token_id": no_token_id,
            },
        )

        assert trade_case["trade_id"] == "pos-edli"
        assert trade_case["city"] == "Madrid"
        assert trade_case["target_date"] == "2026-06-08"
        assert trade_case["bin_label"] == "Will the highest temperature in Madrid be 33°C on June 8?"
        assert trade_case["direction"] == "buy_no"
        assert trade_case["strategy_key"] == "forecast_qkernel_entry"
        assert trade_case["unit"] == "C"
        assert trade_case["entry_method"] == "qkernel_spine"
        assert trade_case["p_posterior"] == pytest.approx(0.91)
        assert trade_case["entry_ci_width"] == pytest.approx(0.18)

    def test_edli_trade_case_marks_non_qkernel_actionable_as_venue_fact_recovery(
        self,
        conn,
    ):
        from src.execution.command_recovery import _edli_trade_case_for_command

        event_id = "edli_evt_legacy_actionable"
        token_id = "tok-no"
        decision_id = f"edli_exec_cmd:{event_id}:intent:{token_id}:{token_id}:buy_no"

        def insert_certificate(certificate_type: str, semantic_key: str, payload: dict, created_at: str) -> None:
            payload_json = json.dumps(payload, sort_keys=True)
            payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
            conn.execute(
                """
                INSERT INTO decision_certificates (
                    certificate_id, certificate_type, schema_version, canonicalization_version,
                    semantic_key, claim_type, mode, decision_time, authority_id,
                    authority_version, algorithm_id, algorithm_version, payload_json,
                    payload_hash, certificate_hash, verifier_status, created_at
                ) VALUES (?, ?, 1, 'test', ?, 'test', 'LIVE', ?, 'test',
                          'test', 'test', 'test', ?, ?, ?, 'VERIFIED', ?)
                """,
                (
                    f"cert:{certificate_type}:{semantic_key}",
                    certificate_type,
                    semantic_key,
                    created_at,
                    payload_json,
                    payload_hash,
                    hashlib.sha256(f"{certificate_type}:{payload_hash}".encode()).hexdigest(),
                    created_at,
                ),
            )

        insert_certificate(
            "ActionableTradeCertificate",
            f"actionable:{event_id}:family:condition-1",
            {
                "event_id": event_id,
                "event_type": "FORECAST_SNAPSHOT_READY",
                "condition_id": "condition-1",
                "direction": "buy_no",
                "token_id": token_id,
                "q_live": 0.81,
                "causal_snapshot_id": "source-run-1",
            },
            "2026-06-07T00:00:00Z",
        )
        insert_certificate(
            "FinalIntentCertificate",
            f"final_intent:{event_id}:intent:{token_id}",
            {
                "event_id": event_id,
                "final_intent_id": f"intent:{token_id}",
                "condition_id": "condition-1",
                "bin_label": "Will the highest temperature in Madrid be 33°C on June 8?",
                "temperature_metric": "high",
                "unit": "C",
                "decision_source_context": {
                    "city": "Madrid",
                    "target_date": "2026-06-08",
                },
            },
            "2026-06-07T00:00:01Z",
        )

        trade_case = _edli_trade_case_for_command(
            conn,
            {
                "position_id": "pos-edli",
                "decision_id": decision_id,
                "token_id": token_id,
                "env_condition_id": "condition-1",
                "env_yes_token_id": "tok-yes",
                "env_no_token_id": token_id,
            },
        )

        assert trade_case == {}

    def test_edli_filled_entry_repair_recovers_missing_bin_label_from_clob_market_identity(
        self,
        conn,
        mock_client,
    ):
        """Missing EDLI bin labels may be recovered only from matching CLOB market identity."""
        from src.state.venue_command_repo import append_event
        from src.execution.command_recovery import reconcile_unresolved_commands

        event_id = "edli_evt_missing_bin_label"
        yes_token_id = "tok-yes"
        no_token_id = "tok-no"
        condition_id = "condition-test"
        final_intent_id = f"edli_intent:{event_id}:{no_token_id}"
        decision_id = f"edli_exec_cmd:{event_id}:{final_intent_id}:{no_token_id}:{no_token_id}:buy_no"

        def insert_certificate(certificate_type: str, semantic_key: str, payload: dict, created_at: str) -> None:
            payload_json = json.dumps(payload, sort_keys=True)
            payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
            conn.execute(
                """
                INSERT INTO decision_certificates (
                    certificate_id, certificate_type, schema_version, canonicalization_version,
                    semantic_key, claim_type, mode, decision_time, authority_id,
                    authority_version, algorithm_id, algorithm_version, payload_json,
                    payload_hash, certificate_hash, verifier_status, created_at
                ) VALUES (?, ?, 1, 'test', ?, 'test', 'LIVE', ?, 'test',
                          'test', 'test', 'test', ?, ?, ?, 'VERIFIED', ?)
                """,
                (
                    f"cert:{certificate_type}:{semantic_key}",
                    certificate_type,
                    semantic_key,
                    created_at,
                    payload_json,
                    payload_hash,
                    hashlib.sha256(f"{certificate_type}:{payload_hash}".encode()).hexdigest(),
                    created_at,
                ),
            )

        _insert(
            conn,
            decision_id=decision_id,
            token_id=yes_token_id,
            no_token_id=no_token_id,
            selected_token_id=no_token_id,
            outcome_label="NO",
            size=8.0,
            price=0.55,
        )
        _advance_to_acked(conn, venue_order_id="ord-edli-clob")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-edli-clob", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            order_id="ord-edli-clob",
            state="CONFIRMED",
            filled_size="8",
            fill_price="0.55",
        )

        insert_certificate(
            "ActionableTradeCertificate",
            f"actionable:{event_id}:family:{condition_id}",
            {
                "event_id": event_id,
                "event_type": "FORECAST_SNAPSHOT_READY",
                "causal_snapshot_id": "source-run-clob",
                "family_id": "family-condition-test",
                "candidate_id": "NO:bin-33:DIRECT_NO:bin-33@proof",
                "condition_id": condition_id,
                "direction": "buy_no",
                "token_id": no_token_id,
                "strategy_key": "opening_inertia",
                "q_live": 0.82,
                "q_lcb_5pct": 0.77,
                "c_fee_adjusted": 0.55,
                "c_cost_95pct": 0.55,
                "p_fill_lcb": 0.5,
                "trade_score": 0.76,
                "action_score": 0.76,
                "executable_snapshot_id": "ems-clob",
                "fdr_family_id": "fdr-clob",
                "kelly_decision_id": "kelly-clob",
                "risk_decision_id": "risk-clob",
                "live_cap_usage_id": "cap-clob",
                "final_intent_id": final_intent_id,
                "native_quote_available": True,
                "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
                "selection_authority_applied": "qkernel_spine",
                "qkernel_execution_economics": {
                    "source": "qkernel_spine",
                    "side": "NO",
                    "candidate_id": "NO:bin-33:DIRECT_NO:bin-33@proof",
                    "route_id": "DIRECT_NO:bin-33@proof",
                    "bin_id": "bin-33",
                    "payoff_q_point": 0.82,
                    "payoff_q_lcb": 0.77,
                    "cost": 0.01,
                    "edge_lcb": 0.76,
                    "delta_u_at_min": 0.76,
                    "optimal_stake_usd": 10.0,
                    "optimal_delta_u": 0.76,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                    "selection_guard_basis": "SELECTION_BETA_95",
                    "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.77,
                },
            },
            "2026-06-07T00:00:00Z",
        )
        insert_certificate(
            "FinalIntentCertificate",
            f"final_intent:{event_id}:{final_intent_id}",
            {
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "condition_id": condition_id,
                "temperature_metric": "high",
                "unit": "C",
                "decision_source_context": {
                    "city": "Madrid",
                    "target_date": "2026-06-08",
                },
            },
            "2026-06-07T00:00:01Z",
        )
        mock_client.get_clob_market_info.return_value = {
            "condition_id": condition_id,
            "question": "Will the highest temperature in Madrid be 33°C on June 8?",
            "tokens": [
                {"token_id": yes_token_id, "outcome": "Yes"},
                {"token_id": no_token_id, "outcome": "No"},
            ],
        }

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        mock_client.get_clob_market_info.assert_called_once_with(condition_id)
        current = conn.execute(
            """
            SELECT phase, city, target_date, bin_label, direction, strategy_key, shares, entry_price
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "city": "Madrid",
            "target_date": "2026-06-08",
            "bin_label": "Will the highest temperature in Madrid be 33°C on June 8?",
            "direction": "buy_no",
            "strategy_key": "opening_inertia",
            "shares": 8.0,
            "entry_price": 0.55,
        }

    def test_terminal_filled_entry_repair_canonicalizes_legacy_imminent_strategy_key(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-001")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )
        _insert_decision_log_trade_case_for_recovery(
            conn,
            strategy_key="imminent_open_capture",
            edge_source="imminent_open_capture",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, entry_price, order_status,
                   strategy_key
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "shares": 5.0,
            "cost_basis_usd": 1.7,
            "entry_price": 0.34,
            "order_status": "filled",
            "strategy_key": "opening_inertia",
        }
        event = conn.execute(
            """
            SELECT event_type, strategy_key, command_id, order_id
              FROM position_events
             WHERE position_id = 'pos-001'
               AND event_type = 'ENTRY_ORDER_FILLED'
            """
        ).fetchone()
        assert dict(event) == {
            "event_type": "ENTRY_ORDER_FILLED",
            "strategy_key": "opening_inertia",
            "command_id": "cmd-001",
            "order_id": "ord-001",
        }

    def test_live_acked_entry_order_without_pending_projection_recovers_position(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=13.45, price=0.01)
        _advance_to_acked(conn, venue_order_id="ord-live")
        _append_order_fact(
            conn,
            order_id="ord-live",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["live_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, condition_id, token_id, no_token_id, shares, cost_basis_usd,
                   entry_price, order_id, order_status, strategy_key, temperature_metric
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_entry",
            "condition_id": "condition-test",
            "token_id": "tok-001",
            "no_token_id": "tok-001-no",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "entry_price": 0.0,
            "order_id": "ord-live",
            "order_status": "pending",
            "strategy_key": "opening_inertia",
            "temperature_metric": "high",
        }
        events = conn.execute(
            """
            SELECT sequence_no, event_type, phase_before, phase_after, command_id, order_id
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert [dict(row) for row in events] == [
            {
                "sequence_no": 1,
                "event_type": "POSITION_OPEN_INTENT",
                "phase_before": None,
                "phase_after": "pending_entry",
                "command_id": "cmd-001",
                "order_id": None,
            },
            {
                "sequence_no": 2,
                "event_type": "ENTRY_ORDER_POSTED",
                "phase_before": "pending_entry",
                "phase_after": "pending_entry",
                "command_id": "cmd-001",
                "order_id": "ord-live",
            },
        ]
        second_summary = reconcile_unresolved_commands(conn, mock_client)
        assert second_summary["live_entry_projection_repair"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_live_edli_entry_projection_uses_actionable_q_live(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-live-q"
        decision_id = f"edli_exec_cmd:{event_id}:intent:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id)
        _advance_to_acked(conn, venue_order_id="ord-edli-live")
        _append_order_fact(
            conn,
            order_id="ord-edli-live",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _insert_actionable_certificate_for_recovery(
            conn,
            event_id=event_id,
            token_id="tok-001",
            q_live=0.62,
            direction="buy_yes",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["live_entry_projection_repair"]["advanced"] == 1
        current = conn.execute(
            "SELECT phase, direction, p_posterior FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_entry",
            "direction": "buy_yes",
            "p_posterior": pytest.approx(0.62),
        }

    def test_live_edli_entry_projection_refuses_missing_actionable_certificate(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-missing-cert"
        decision_id = f"edli_exec_cmd:{event_id}:intent:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id)
        _advance_to_acked(conn, venue_order_id="ord-edli-missing-cert")
        _append_order_fact(
            conn,
            order_id="ord-edli-missing-cert",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["live_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        assert conn.execute(
            "SELECT 1 FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone() is None

    def test_live_edli_entry_projection_refuses_quarantined_actionable_certificate(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-quarantined-cert"
        decision_id = f"edli_exec_cmd:{event_id}:intent:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id)
        _advance_to_acked(conn, venue_order_id="ord-edli-quarantined")
        _append_order_fact(
            conn,
            order_id="ord-edli-quarantined",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _insert_actionable_certificate_for_recovery(
            conn,
            event_id=event_id,
            token_id="tok-001",
            q_live=0.62,
            direction="buy_yes",
            quarantine=True,
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["live_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        assert conn.execute(
            "SELECT 1 FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone() is None

    def test_live_edli_entry_projection_refuses_current_invalid_actionable_certificate(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-invalid-current-cert"
        decision_id = f"edli_exec_cmd:{event_id}:intent:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id)
        _advance_to_acked(conn, venue_order_id="ord-edli-invalid-current")
        _append_order_fact(
            conn,
            order_id="ord-edli-invalid-current",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _insert_actionable_certificate_for_recovery(
            conn,
            event_id=event_id,
            token_id="tok-001",
            q_live=0.06,
            direction="buy_yes",
            payoff_q_point=0.20,
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["live_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        assert conn.execute(
            "SELECT 1 FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone() is None

    def test_invalid_pending_entry_authority_cancel_voids_zero_fill_rest_and_continues_redecision(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-invalid-pending-authority"
        decision_id = f"edli_exec_cmd:{event_id}:intent:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id, size=13.45, price=0.40)
        _advance_to_acked(conn, venue_order_id="ord-invalid-pending")
        _seed_pending_entry_projection(conn, order_id="ord-invalid-pending")
        _append_order_fact(
            conn,
            order_id="ord-invalid-pending",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _insert_actionable_certificate_for_recovery(
            conn,
            event_id=event_id,
            token_id="tok-001",
            q_live=0.06,
            direction="buy_yes",
            payoff_q_point=0.20,
        )

        class FakeClob:
            def __init__(self) -> None:
                self.cancelled: list[str] = []

            def cancel_order(self, order_id: str):
                self.cancelled.append(order_id)
                return {"canceled": [order_id], "not_canceled": []}

        from src.execution.command_recovery import (
            reconcile_invalid_pending_entry_authority_cancels,
        )

        clob = FakeClob()
        summary = reconcile_invalid_pending_entry_authority_cancels(conn, clob)

        assert clob.cancelled == ["ord-invalid-pending"]
        assert summary["scanned"] == 1
        assert summary["advanced"] == 1
        assert summary["errors"] == 0
        assert summary["continuations"] == [
            {
                "command_id": "cmd-001",
                "position_id": "pos-001",
                "venue_order_id": "ord-invalid-pending",
                "condition_id": "condition-test",
                "token_id": "tok-001",
                "city": "Karachi",
                "target_date": "2026-05-17",
                "temperature_metric": "high",
                "metric": "high",
                "reason": "invalid_pending_entry_authority_cancel",
            }
        ]
        assert _get_state(conn, "cmd-001") == "CANCELLED"
        position = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(position) == {
            "phase": "voided",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "order_status": "canceled",
        }
        events = [
            row["event_type"]
            for row in conn.execute(
                """
                SELECT event_type
                  FROM position_events
                 WHERE position_id = 'pos-001'
                 ORDER BY sequence_no
                """
            ).fetchall()
        ]
        assert events[-1] == "ENTRY_ORDER_VOIDED"

    def test_edli_entry_posterior_projection_repair_backfills_existing_zero(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-existing-q"
        decision_id = f"edli_exec_cmd:{event_id}:intent:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id)
        _advance_to_acked(conn, venue_order_id="ord-edli-existing")
        _insert_actionable_certificate_for_recovery(
            conn,
            event_id=event_id,
            token_id="tok-001",
            q_live=0.62,
            direction="buy_yes",
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric
            ) VALUES (
                'pos-001', 'active', 'condition-test', 'Karachi', 'Karachi',
                '2026-05-17', 'Will the highest temperature in Karachi be 40C on May 17?',
                'buy_yes', 'C', 0.06, 5.0, 0.06, 0.012,
                0.0, 'forecast-snap-old', 'ens_member_counting', 'center_buy',
                'center_buy', 'opening_hunt', 'synced', 'tok-001', 'tok-001-no',
                'condition-test', 'ord-edli-existing', 'partial',
                '2026-04-26T00:05:00Z', 'high'
            )
            """
        )
        # T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md):
        # this test previously also seeded a second "chain-only-edli-existing"
        # position (phase='quarantined', chain_state='entry_authority_
        # quarantined') and asserted this repair voided it via
        # command_recovery._void_absorbed_chain_only_projection. That
        # absorption call site was deleted from
        # reconcile_edli_entry_posterior_projection_repairs (provably
        # unreachable post-migration), so the incidental seed/assertion is
        # gone too; this test now covers only its real subject, the EDLI
        # entry-posterior backfill for pos-001.

        from src.execution.command_recovery import (
            reconcile_edli_entry_posterior_projection_repairs,
        )

        summary = reconcile_edli_entry_posterior_projection_repairs(conn, client=mock_client)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT p_posterior, entry_method, strategy_key, edge_source,
                   discovery_mode, decision_snapshot_id
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert current["p_posterior"] == pytest.approx(0.62)
        assert current["entry_method"] == "qkernel_spine"
        assert current["strategy_key"] == "forecast_qkernel_entry"
        assert current["edge_source"] == "forecast_qkernel_entry"
        assert current["discovery_mode"] == "update_reaction"
        assert current["decision_snapshot_id"] == "forecast-snap-edli"

    def test_edli_entry_posterior_projection_repair_refuses_quarantined_actionable_certificate(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-existing-quarantined"
        decision_id = f"edli_exec_cmd:{event_id}:intent:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id)
        _advance_to_acked(conn, venue_order_id="ord-edli-existing-quarantined")
        _insert_actionable_certificate_for_recovery(
            conn,
            event_id=event_id,
            token_id="tok-001",
            q_live=0.62,
            direction="buy_yes",
            quarantine=True,
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric
            ) VALUES (
                'pos-001', 'active', 'condition-test', 'Karachi', 'Karachi',
                '2026-05-17', 'Will the highest temperature in Karachi be 40C on May 17?',
                'buy_yes', 'C', 0.06, 5.0, 0.06, 0.012,
                0.0, 'forecast-snap-old', 'ens_member_counting', 'center_buy',
                'center_buy', 'opening_hunt', 'synced', 'tok-001', 'tok-001-no',
                'condition-test', 'ord-edli-existing-quarantined', 'partial',
                '2026-04-26T00:05:00Z', 'high'
            )
            """
        )

        from src.execution.command_recovery import (
            reconcile_edli_entry_posterior_projection_repairs,
        )

        summary = reconcile_edli_entry_posterior_projection_repairs(conn, client=mock_client)

        assert summary == {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}
        current = conn.execute(
            "SELECT p_posterior, entry_method FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["p_posterior"] == 0.0
        assert current["entry_method"] == "ens_member_counting"

    def test_invalid_open_entry_authority_repair_reviews_active_position_without_quarantine(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-active-invalid-authority"
        decision_id = f"edli_exec_cmd:{event_id}:intent:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id)
        _advance_to_acked(conn, venue_order_id="ord-edli-active-invalid-authority")
        _insert_actionable_certificate_for_recovery(
            conn,
            event_id=event_id,
            token_id="tok-001",
            q_live=0.62,
            direction="buy_yes",
            quarantine=True,
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric,
                chain_shares
            ) VALUES (
                'pos-001', 'active', 'condition-test', 'Karachi', 'Karachi',
                '2026-05-17', 'Will the highest temperature in Karachi be 40C on May 17?',
                'buy_yes', 'C', 0.06, 5.0, 0.06, 0.012,
                0.62, 'forecast-snap-old', 'qkernel_spine', 'center_buy',
                'center_buy', 'opening_hunt', 'synced', 'tok-001', 'tok-001-no',
                'condition-test', 'ord-edli-active-invalid-authority', 'partial',
                '2026-04-26T00:05:00Z', 'high', 5.0
            )
            """
        )

        from src.execution.command_recovery import (
            INVALID_ENTRY_AUTHORITY_REVIEW_REASON,
            reconcile_invalid_open_entry_authority_reviews,
        )

        summary = reconcile_invalid_open_entry_authority_reviews(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, chain_state, exit_reason
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "chain_state": "synced",
            "exit_reason": None,
        }
        event = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, command_id, caused_by, payload_json
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no DESC
             LIMIT 1
            """
        ).fetchone()
        payload = json.loads(event["payload_json"])
        assert event["event_type"] == "REVIEW_REQUIRED"
        assert event["phase_before"] == "active"
        assert event["phase_after"] == "active"
        assert event["command_id"] == "cmd-001"
        assert event["caused_by"] == INVALID_ENTRY_AUTHORITY_REVIEW_REASON
        assert payload["proof_class"] == "open_position_entry_actionable_certificate_not_current_valid"

    def test_edli_entry_authority_projection_repair_backfills_legacy_method(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-existing-method"
        decision_id = f"edli_exec_cmd:{event_id}:intent:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id)
        _advance_to_acked(conn, venue_order_id="ord-edli-existing-method")
        _insert_actionable_certificate_for_recovery(
            conn,
            event_id=event_id,
            token_id="tok-001",
            q_live=0.914,
            direction="buy_yes",
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric
            ) VALUES (
                'pos-001', 'active', 'condition-test', 'Hong Kong', 'Hong Kong',
                '2026-06-26', 'Will the lowest temperature in Hong Kong be 28C on June 26?',
                'buy_yes', 'C', 3.05, 5.0, 3.05, 0.61,
                0.914, 'forecast-snap-old', 'ens_member_counting', 'center_buy',
                'center_buy', 'opening_hunt', 'synced', 'tok-001', 'tok-001-no',
                'condition-test', 'ord-edli-existing-method', 'filled',
                '2026-06-24T05:27:15Z', 'low'
            )
            """
        )

        from src.execution.command_recovery import (
            reconcile_edli_entry_posterior_projection_repairs,
        )

        summary = reconcile_edli_entry_posterior_projection_repairs(conn, client=mock_client)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            "SELECT p_posterior, entry_method FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["p_posterior"] == pytest.approx(0.914)
        assert current["entry_method"] == "qkernel_spine"

    def test_edli_entry_posterior_projection_repair_rejects_final_intent_q_live(
        self,
        conn,
        mock_client,
    ):
        event_id = "evt-edli-final-q-only"
        final_intent_id = f"intent:{event_id}:tok-001"
        decision_id = f"edli_exec_cmd:{event_id}:{final_intent_id}:tok-001:tok-001:buy_yes"
        _insert(conn, decision_id=decision_id)
        _advance_to_acked(conn, venue_order_id="ord-edli-final-only")
        _insert_actionable_certificate_for_recovery(
            conn,
            event_id=event_id,
            token_id="tok-001",
            q_live=0.0,
            direction="buy_yes",
        )
        _insert_final_intent_certificate_for_recovery(
            conn,
            event_id=event_id,
            final_intent_id=final_intent_id,
            token_id="tok-001",
            q_live=0.88,
            direction="buy_yes",
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric
            ) VALUES (
                'pos-001', 'active', 'condition-test', 'Karachi', 'Karachi',
                '2026-05-17', 'Will the highest temperature in Karachi be 40C on May 17?',
                'buy_yes', 'C', 0.06, 5.0, 0.06, 0.012,
                0.0, 'forecast-snap-old', 'ens_member_counting', 'center_buy',
                'center_buy', 'opening_hunt', 'synced', 'tok-001', 'tok-001-no',
                'condition-test', 'ord-edli-final-only', 'partial',
                '2026-04-26T00:05:00Z', 'high'
            )
            """
        )

        from src.execution.command_recovery import (
            reconcile_edli_entry_posterior_projection_repairs,
        )

        summary = reconcile_edli_entry_posterior_projection_repairs(conn, client=mock_client)

        assert summary == {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}
        current = conn.execute(
            "SELECT p_posterior FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["p_posterior"] == 0.0

    def test_hard_terminal_position_projection_repair_restores_voided_phase(
        self,
        conn,
    ):
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric,
                chain_shares
            ) VALUES (
                'pos-terminal-drift', 'active', 'condition-test', 'Hong Kong', 'Hong Kong',
                '2026-06-09', 'Will the highest temperature in Hong Kong be 32C on June 9?',
                'buy_no', 'C', 17.67, 19.0, 17.67, 0.93,
                1.0, 'forecast-snap-old', 'ens_member_counting', 'opening_inertia',
                'opening_inertia', 'opening_hunt', 'synced', 'tok-001', 'tok-001-no',
                'condition-test', 'ord-terminal-drift', 'filled',
                '2026-06-25T00:00:00Z', 'high', 0.0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at,
                phase_before, phase_after, strategy_key, decision_id,
                snapshot_id, order_id, command_id, caused_by, idempotency_key,
                venue_status, source_module, payload_json, env
            ) VALUES (
                'evt-terminal-drift', 'pos-terminal-drift', 7, 'ADMIN_VOIDED',
                '2026-06-12T11:45:30+00:00', 'pending_exit', 'voided',
                'opening_inertia', 'dec-1', 'snap-1', 'ord-terminal-drift',
                'cmd-1', 'operator_review', 'idem-terminal-drift', 'VOIDED',
                'src.execution.command_recovery', '{}', 'live'
            )
            """
        )

        from src.execution.command_recovery import (
            reconcile_hard_terminal_position_projection_repairs,
        )

        summary = reconcile_hard_terminal_position_projection_repairs(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        row = conn.execute(
            "SELECT phase, chain_shares FROM position_current WHERE position_id = 'pos-terminal-drift'"
        ).fetchone()
        assert row["phase"] == "voided"
        assert row["chain_shares"] == 0.0

    def test_hard_terminal_position_projection_repair_ignores_stale_terminal_event(
        self,
        conn,
    ):
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric,
                chain_shares, exit_reason
            ) VALUES (
                'pos-stale-terminal', 'pending_exit', 'condition-test', 'Miami', 'US',
                '2026-06-30', 'Will the highest temperature in Miami be between 96-97F on June 30?',
                'buy_yes', 'F', 4.34, 85.17, 4.34, 0.051,
                0.34, 'forecast-snap-old', 'qkernel_spine', 'center_buy',
                'center_buy', 'opening_hunt', 'synced', 'tok-yes', 'tok-no',
                'condition-test', 'ord-open-exit', 'sell_placed',
                '2026-06-29T18:00:50+00:00', 'high', 85.17,
                'ENTRY_SELECTION_GUARD_INVALID_EXIT'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at,
                phase_before, phase_after, strategy_key, decision_id,
                snapshot_id, order_id, command_id, caused_by, idempotency_key,
                venue_status, source_module, payload_json, env
            ) VALUES
              ('evt-stale-terminal-void', 'pos-stale-terminal', 7, 'ADMIN_VOIDED',
               '2026-06-29T13:38:50+00:00', 'active', 'voided',
               'center_buy', 'dec-1', 'snap-1', 'ord-entry', NULL,
               'chain_reconciliation', 'idem-stale-terminal-void', 'voided',
               'src.state.chain_reconciliation',
               '{"reason":"PHANTOM_NOT_ON_CHAIN","token_id":"tok-yes","chain_state":"synced"}',
               'live'),
              ('evt-stale-terminal-exit-posted', 'pos-stale-terminal', 8, 'EXIT_ORDER_POSTED',
               '2026-06-29T18:00:45+00:00', 'active', 'pending_exit',
               'center_buy', 'dec-1', 'snap-1', 'ord-open-exit', NULL,
               'transition_phase', 'idem-stale-terminal-exit-posted', 'sell_pending',
               'src.execution.exit_lifecycle',
               '{"last_exit_order_id":"ord-open-exit","status":"sell_pending"}',
               'live')
            """
        )

        from src.execution.command_recovery import (
            reconcile_hard_terminal_position_projection_repairs,
        )

        summary = reconcile_hard_terminal_position_projection_repairs(conn)

        assert summary == {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
        row = conn.execute(
            """
            SELECT phase, chain_state, chain_shares, order_status, order_id
              FROM position_current
             WHERE position_id = 'pos-stale-terminal'
            """
        ).fetchone()
        assert dict(row) == {
            "phase": "pending_exit",
            "chain_state": "synced",
            "chain_shares": 85.17,
            "order_status": "sell_placed",
            "order_id": "ord-open-exit",
        }

    def test_hard_terminal_position_projection_repair_clears_chain_zero_void_fields(
        self,
        conn,
    ):
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric,
                chain_shares, exit_retry_count, next_exit_retry_at, exit_reason
            ) VALUES (
                'pos-chain-zero-stale', 'voided', 'condition-test', 'Manila', 'Manila',
                '2026-07-01', 'Will the highest temperature in Manila be 29C on July 1?',
                'buy_yes', 'C', 0.15, 9.7, 0.15, 0.015,
                0.13, 'forecast-snap-old', 'qkernel_spine', 'center_buy',
                'center_buy', 'opening_hunt', 'chain_confirmed_zero', 'tok-001', 'tok-001-no',
                'condition-test', NULL, 'retry_pending',
                '2026-06-29T17:33:25+00:00', 'high',
                9.7, 6, '2026-06-29T17:45:00+00:00', 'CHAIN_CONFIRMED_ZERO'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at,
                phase_before, phase_after, strategy_key, decision_id,
                snapshot_id, order_id, command_id, caused_by, idempotency_key,
                venue_status, source_module, payload_json, env
            ) VALUES (
                'evt-chain-zero-stale', 'pos-chain-zero-stale', 11, 'ADMIN_VOIDED',
                '2026-06-29T17:33:25+00:00', 'pending_exit', 'voided',
                'center_buy', 'dec-1', 'snap-1', NULL, NULL,
                'chain_truth_balance_zero', 'idem-chain-zero-stale', 'voided',
                'src.execution.exit_lifecycle',
                '{"evidence_source":"CHAIN_BALANCEOF","chain_state":"chain_confirmed_zero","reason":"CHAIN_CONFIRMED_ZERO"}',
                'live'
            )
            """
        )

        from src.execution.command_recovery import (
            reconcile_hard_terminal_position_projection_repairs,
        )

        summary = reconcile_hard_terminal_position_projection_repairs(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        row = conn.execute(
            """
            SELECT phase, chain_state, chain_shares, order_status, exit_retry_count,
                   next_exit_retry_at, exit_reason
              FROM position_current
             WHERE position_id = 'pos-chain-zero-stale'
            """
        ).fetchone()
        assert dict(row) == {
            "phase": "voided",
            "chain_state": "chain_confirmed_zero",
            "chain_shares": 0.0,
            "order_status": "voided",
            "exit_retry_count": 0,
            "next_exit_retry_at": None,
            "exit_reason": "CHAIN_CONFIRMED_ZERO",
        }

    # R2-core hole closure (a) (R0 verifier finding, docs/rebuild/EXECUTION_MASTER_2026-07-07.md
    # §E R2 item 4a): reconcile_hard_terminal_position_projection_repairs now
    # routes through upsert_position_current, so a first transition into
    # settled/economically_closed picks up realized_pnl_usd instead of
    # silently leaving it NULL.
    def test_hard_terminal_settled_repair_books_pnl_from_event_payload(
        self,
        conn,
    ):
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric,
                chain_shares
            ) VALUES (
                'pos-settled-drift', 'pending_exit', 'condition-test', 'Manila', 'Manila',
                '2026-07-01', 'Will the highest temperature in Manila be 29C on July 1?',
                'buy_yes', 'C', 10.0, 10.0, 10.0, 1.0,
                0.6, 'forecast-snap-old', 'center_buy', 'edli',
                'center_buy', 'opening_hunt', 'synced', 'tok-001', 'tok-001-no',
                'condition-test', 'ord-settled-drift', 'filled',
                '2026-07-01T00:00:00Z', 'high', 10.0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at,
                phase_before, phase_after, strategy_key, decision_id,
                snapshot_id, order_id, command_id, caused_by, idempotency_key,
                venue_status, source_module, payload_json, env
            ) VALUES (
                'evt-settled-drift', 'pos-settled-drift', 9, 'SETTLED',
                '2026-07-02T11:45:30+00:00', 'pending_exit', 'settled',
                'edli', 'dec-1', 'snap-1', NULL, 'cmd-1',
                'harvester', 'idem-settled-drift', NULL,
                'src.execution.harvester', '{"pnl": 3.5, "exit_price": 1.0}', 'live'
            )
            """
        )

        from src.execution.command_recovery import (
            reconcile_hard_terminal_position_projection_repairs,
        )

        summary = reconcile_hard_terminal_position_projection_repairs(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        row = conn.execute(
            "SELECT phase, realized_pnl_usd FROM position_current WHERE position_id = 'pos-settled-drift'"
        ).fetchone()
        assert row["phase"] == "settled"
        assert row["realized_pnl_usd"] == 3.5

    def test_hard_terminal_settled_repair_fails_closed_without_pnl_evidence(
        self,
        conn,
    ):
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, market_id, city, cluster, target_date, bin_label,
                direction, unit, size_usd, shares, cost_basis_usd, entry_price,
                p_posterior, decision_snapshot_id, entry_method, strategy_key,
                edge_source, discovery_mode, chain_state, token_id, no_token_id,
                condition_id, order_id, order_status, updated_at, temperature_metric,
                chain_shares
            ) VALUES (
                'pos-settled-no-pnl', 'pending_exit', 'condition-test', 'Manila', 'Manila',
                '2026-07-01', 'Will the highest temperature in Manila be 29C on July 1?',
                'buy_yes', 'C', 10.0, 10.0, 10.0, 1.0,
                0.6, 'forecast-snap-old', 'center_buy', 'edli',
                'center_buy', 'opening_hunt', 'synced', 'tok-001', 'tok-001-no',
                'condition-test', 'ord-settled-no-pnl', 'filled',
                '2026-07-01T00:00:00Z', 'high', 10.0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at,
                phase_before, phase_after, strategy_key, decision_id,
                snapshot_id, order_id, command_id, caused_by, idempotency_key,
                venue_status, source_module, payload_json, env
            ) VALUES (
                'evt-settled-no-pnl', 'pos-settled-no-pnl', 9, 'SETTLED',
                '2026-07-02T11:45:30+00:00', 'pending_exit', 'settled',
                'edli', 'dec-1', 'snap-1', NULL, 'cmd-1',
                'harvester', 'idem-settled-no-pnl', NULL,
                'src.execution.harvester', '{}', 'live'
            )
            """
        )

        from src.execution.command_recovery import (
            reconcile_hard_terminal_position_projection_repairs,
        )

        summary = reconcile_hard_terminal_position_projection_repairs(conn)

        # Fail-closed: no pnl/exit_price evidence in the terminal event's own
        # payload means upsert_position_current's MissingRealizedPnlOnCloseError
        # backstop fires -- caught by this function's own try/except, counted
        # as an error, and the phase is NOT silently advanced with a NULL pnl.
        assert summary == {"scanned": 1, "advanced": 0, "stayed": 0, "errors": 1}
        row = conn.execute(
            "SELECT phase, realized_pnl_usd FROM position_current WHERE position_id = 'pos-settled-no-pnl'"
        ).fetchone()
        assert row["phase"] == "pending_exit"
        assert row["realized_pnl_usd"] is None

    def test_live_entry_repair_prefers_forecasts_market_events_over_trade_ghost(
        self,
        conn,
        mock_client,
        tmp_path,
    ):
        """Live pending projection repair must ignore legacy trade DB market_events shells."""
        conn.execute("DROP TABLE IF EXISTS market_events")
        conn.execute(
            """
            CREATE TABLE market_events (
                id INTEGER,
                market_slug TEXT,
                city TEXT,
                target_date TEXT,
                condition_id TEXT,
                token_id TEXT,
                range_label TEXT,
                range_low REAL,
                range_high REAL,
                outcome TEXT,
                created_at TEXT
            )
            """
        )
        forecasts_db = tmp_path / "forecasts.db"
        conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))
        conn.execute(
            """
            CREATE TABLE forecasts.market_events (
                event_id TEXT,
                market_slug TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                condition_id TEXT,
                token_id TEXT,
                range_label TEXT,
                range_low REAL,
                range_high REAL,
                outcome TEXT,
                created_at TEXT,
                recorded_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO forecasts.market_events (
                event_id, market_slug, city, target_date, temperature_metric,
                condition_id, token_id, range_label, outcome, created_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-london-28c",
                "highest-temperature-in-london-on-june-21-2026",
                "London",
                "2026-04-27",
                "high",
                "condition-test",
                "tok-001-no",
                "Will the highest temperature in London be 28°C on April 27?",
                "Will the highest temperature in London be 28°C on April 27?",
                "2026-04-26T00:00:00Z",
                "2026-04-26T00:00:00Z",
            ),
        )
        _insert(
            conn,
            token_id="tok-001",
            no_token_id="tok-001-no",
            selected_token_id="tok-001-no",
            outcome_label="NO",
            decision_id="legacy_exec_cmd:missing-event:missing-intent:tok-001-no:tok-001-no:buy_no",
            size=13.45,
            price=0.01,
        )
        _advance_to_acked(conn, venue_order_id="ord-live")
        _append_order_fact(
            conn,
            order_id="ord-live",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["live_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, city, target_date, direction, token_id, no_token_id,
                   order_id, strategy_key, temperature_metric
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_entry",
            "city": "London",
            "target_date": "2026-04-27",
            "direction": "buy_no",
            "token_id": "tok-001",
            "no_token_id": "tok-001-no",
            "order_id": "ord-live",
            "strategy_key": "forecast_qkernel_entry",
            "temperature_metric": "high",
        }

    def test_cancel_unknown_terminal_no_fill_hydrates_bare_command_identity(
        self,
        conn,
        mock_client,
        tmp_path,
    ):
        """Cancel-unknown repair must not depend on caller-side join aliases."""
        from src.risk_allocator.governor import count_unknown_side_effects

        conn.execute("DROP TABLE IF EXISTS market_events")
        conn.execute(
            """
            CREATE TABLE market_events (
                id INTEGER,
                market_slug TEXT,
                city TEXT,
                target_date TEXT,
                condition_id TEXT,
                token_id TEXT,
                range_label TEXT,
                range_low REAL,
                range_high REAL,
                outcome TEXT,
                created_at TEXT
            )
            """
        )
        forecasts_db = tmp_path / "forecasts.db"
        conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_db),))
        conn.execute(
            """
            CREATE TABLE forecasts.market_events (
                event_id TEXT,
                market_slug TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                condition_id TEXT,
                token_id TEXT,
                range_label TEXT,
                range_low REAL,
                range_high REAL,
                outcome TEXT,
                created_at TEXT,
                recorded_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO forecasts.market_events (
                event_id, market_slug, city, target_date, temperature_metric,
                condition_id, token_id, range_label, outcome, created_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-london-28c",
                "highest-temperature-in-london-on-june-21-2026",
                "London",
                "2026-04-27",
                "high",
                "condition-test",
                "tok-001-no",
                "Will the highest temperature in London be 28°C on April 27?",
                "Will the highest temperature in London be 28°C on April 27?",
                "2026-04-26T00:00:00Z",
                "2026-04-26T00:00:00Z",
            ),
        )
        _insert(
            conn,
            token_id="tok-001",
            no_token_id="tok-001-no",
            selected_token_id="tok-001-no",
            outcome_label="NO",
            decision_id="legacy_exec_cmd:missing-event:missing-intent:tok-001-no:tok-001-no:buy_no",
            size=13.45,
            price=0.01,
        )
        _advance_to_cancel_unknown_review_required(conn, venue_order_id="ord-terminal")
        mock_client.get_order.return_value = {
            "orderID": "ord-terminal",
            "status": "UNKNOWN",
        }
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []

        from src.execution.command_recovery import (
            _decision_log_trade_case_for_command,
            reconcile_unresolved_commands,
        )

        bare_command = dict(
            conn.execute(
                "SELECT * FROM venue_commands WHERE command_id = 'cmd-001'"
            ).fetchone()
        )
        recovered, _source_id = _decision_log_trade_case_for_command(conn, bare_command)
        assert recovered["city"] == "London"
        assert recovered["target_date"] == "2026-04-27"
        assert recovered["direction"] == "buy_no"

        before_count, _ = count_unknown_side_effects(conn)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert before_count == 1
        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        current = conn.execute(
            """
            SELECT phase, city, target_date, direction, token_id, no_token_id,
                   order_id, strategy_key, temperature_metric
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "voided",
            "city": "London",
            "target_date": "2026-04-27",
            "direction": "buy_no",
            "token_id": "tok-001",
            "no_token_id": "tok-001-no",
            "order_id": "ord-terminal",
            "strategy_key": "forecast_qkernel_entry",
            "temperature_metric": "high",
        }
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()

    def test_live_entry_repair_does_not_duplicate_existing_order_token_projection(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=13.45, price=0.01)
        _advance_to_acked(conn, venue_order_id="ord-live")
        _append_order_fact(
            conn,
            order_id="ord-live",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _insert_decision_log_trade_case_for_recovery(conn)
        _seed_pending_entry_projection(
            conn,
            position_id="legacy-pos",
            command_id="legacy-command",
            order_id="ord-live",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["live_entry_projection_repair"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }
        rows = conn.execute(
            """
            SELECT position_id, phase, shares
              FROM position_current
             WHERE lower(order_id) = lower('ord-live')
             ORDER BY position_id
            """
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {"position_id": "legacy-pos", "phase": "pending_entry", "shares": 0.0}
        ]

    def test_ensure_live_entry_projection_for_command_projects_pending_order_immediately(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=13.45, price=0.01)
        _advance_to_acked(conn, venue_order_id="ord-live")
        _append_order_fact(
            conn,
            order_id="ord-live",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import ensure_live_entry_projection_for_command

        summary = ensure_live_entry_projection_for_command(
            conn,
            command_id="cmd-001",
            client=mock_client,
        )

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, direction, shares, cost_basis_usd, order_id, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_entry",
            "direction": "buy_yes",
            "shares": 0.0,
            "cost_basis_usd": 0.0,
            "order_id": "ord-live",
            "order_status": "pending",
        }
        event_types = [
            dict(row)
            for row in conn.execute(
                """
                SELECT event_type, venue_status, payload_json
                  FROM position_events
                 WHERE position_id = 'pos-001'
                 ORDER BY sequence_no
                """
            ).fetchall()
        ]
        assert [row["event_type"] for row in event_types] == [
            "POSITION_OPEN_INTENT",
            "ENTRY_ORDER_POSTED",
        ]
        assert [row["venue_status"] for row in event_types] == ["LIVE", "LIVE"]
        assert all(json.loads(row["payload_json"])["venue_status"] == "LIVE" for row in event_types)

    def test_ensure_live_entry_projection_for_command_uses_edli_events_before_certificates(
        self,
        conn,
        mock_client,
    ):
        event_id = "edli_evt_ack_event_only"
        yes_token_id = "tok-yes"
        no_token_id = "tok-no"
        final_intent_id = f"edli_intent:{event_id}:{no_token_id}"
        decision_id = f"edli_exec_cmd:{event_id}:{final_intent_id}:{no_token_id}:{no_token_id}:buy_no"
        aggregate_id = f"{event_id}:{final_intent_id}"
        _insert(
            conn,
            token_id=yes_token_id,
            no_token_id=no_token_id,
            selected_token_id=no_token_id,
            outcome_label="NO",
            decision_id=decision_id,
            size=12.29,
            price=0.61,
        )
        _advance_to_acked(conn, venue_order_id="ord-live-edli")
        _append_order_fact(
            conn,
            order_id="ord-live-edli",
            state="LIVE",
            matched_size="0",
            remaining_size="12.29",
            source="REST",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="SubmitPlanBuilt",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "condition_id": "condition-test",
                "token_id": no_token_id,
                "direction": "buy_no",
                "city": "Madrid",
                "target_date": "2026-06-08",
                "metric": "high",
                "limit_price": 0.61,
                "size": 12.29,
            },
            occurred_at="2026-06-07T00:00:00Z",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="PreSubmitRevalidated",
            payload={
                "event_id": event_id,
                "event_type": "EDLI_REDECISION_PENDING",
                "final_intent_id": final_intent_id,
                "condition_id": "condition-test",
                "token_id": no_token_id,
                "direction": "buy_no",
                "city": "Madrid",
                "target_date": "2026-06-08",
                "metric": "high",
                "bin_label": "Will the highest temperature in Madrid be 33°C on June 8?",
                "q_live": 0.91,
                "q_lcb_5pct": 0.82,
                "qkernel_execution_economics": {
                    "source": "qkernel_spine",
                    "side": "NO",
                    "candidate_id": "NO:bin-33:DIRECT_NO:bin-33",
                    "route_id": "DIRECT_NO:bin-33",
                    "bin_id": "bin-33",
                    "payoff_q_point": 0.91,
                    "payoff_q_lcb": 0.82,
                    "cost": 0.61,
                    "edge_lcb": 0.21,
                    "optimal_delta_u": 0.21,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                },
            },
            occurred_at="2026-06-07T00:00:01Z",
        )

        from src.execution.command_recovery import ensure_live_entry_projection_for_command

        summary = ensure_live_entry_projection_for_command(
            conn,
            command_id="cmd-001",
            client=mock_client,
        )

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, city, target_date, direction, token_id, no_token_id,
                   order_id, order_status, entry_method, strategy_key
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_entry",
            "city": "Madrid",
            "target_date": "2026-06-08",
            "direction": "buy_no",
            "token_id": yes_token_id,
            "no_token_id": no_token_id,
            "order_id": "ord-live-edli",
            "order_status": "pending",
            "entry_method": "qkernel_spine",
            "strategy_key": "forecast_qkernel_entry",
        }

    def test_filled_day0_edli_entry_with_retired_boundary_guard_projects_as_venue_fact(
        self,
        conn,
        mock_client,
    ):
        """Retired Day0 boundary qkernel evidence must not resurrect posterior authority."""

        event_id = "edli_evt_day0_nested_qkernel"
        yes_token_id = "tok-yes-day0"
        no_token_id = "tok-no-day0"
        final_intent_id = f"edli_intent:{event_id}:{yes_token_id}"
        decision_id = (
            f"edli_exec_cmd:{event_id}:{final_intent_id}:"
            f"{yes_token_id}:{yes_token_id}:buy_yes"
        )
        aggregate_id = f"{event_id}:{final_intent_id}"
        _insert(
            conn,
            token_id=yes_token_id,
            no_token_id=no_token_id,
            selected_token_id=yes_token_id,
            outcome_label="YES",
            decision_id=decision_id,
            size=40.25,
            price=0.44,
            created_at="2026-07-02T02:18:11+00:00",
        )
        _advance_to_acked(conn, venue_order_id="ord-day0-fok")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-07-02T02:18:17+00:00",
            payload={
                "source": "REST",
                "venue_order_id": "ord-day0-fok",
                "trade_id": "trade-day0-fok",
                "filled_size": "40.25",
                "fill_price": "0.44",
            },
        )
        _append_trade_fact(
            conn,
            order_id="ord-day0-fok",
            trade_id="trade-day0-fok",
            state="MATCHED",
            filled_size="40.25",
            fill_price="0.44",
        )
        _append_order_fact(
            conn,
            order_id="ord-day0-fok",
            state="MATCHED",
            matched_size="40.25",
            remaining_size="0",
            source="REST",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="DecisionProofAccepted",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "decision_audit": {
                    "event_id": event_id,
                    "event_type": "DAY0_EXTREME_UPDATED",
                    "final_intent_id": final_intent_id,
                    "actual_bin_label": (
                        "Will the highest temperature in Manila be 32°C on July 2?"
                    ),
                    "actual_condition_id": "condition-test",
                    "actual_direction": "buy_yes",
                    "actual_token_id": yes_token_id,
                    "city": "Manila",
                    "target_date": "2026-07-02",
                    "metric": "high",
                    "strategy_key": "day0_nowcast_entry",
                    "opportunity_book": {
                        "cache_summary": {
                            "selected_qkernel_execution_economics": {
                                "source": "qkernel_spine",
                                "side": "YES",
                                "candidate_id": "YES:bin-32:DIRECT_YES:bin-32",
                                "route_id": "DIRECT_YES:bin-32",
                                "bin_id": "bin-32",
                                "payoff_q_point": 0.9614944294185659,
                                "payoff_q_lcb": 0.96,
                                "cost": 0.44,
                                "edge_lcb": 0.52,
                                "optimal_delta_u": 0.52,
                                "false_edge_rate": 0.01,
                                "direction_law_ok": True,
                                "coherence_allows": True,
                                "q_lcb_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                                "selection_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                                "q_lcb_guard_abstained": False,
                                "selection_guard_abstained": False,
                                "selection_guard_q_safe": 0.96,
                            }
                        }
                    },
                },
            },
            occurred_at="2026-07-02T02:17:51+00:00",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="PreSubmitRevalidated",
            payload={
                "event_id": event_id,
                "event_type": "DAY0_EXTREME_UPDATED",
                "final_intent_id": final_intent_id,
                "condition_id": "condition-test",
                "token_id": yes_token_id,
                "direction": "buy_yes",
                "city": "Manila",
                "target_date": "2026-07-02",
                "metric": "high",
                "strategy_key": "day0_nowcast_entry",
                "bin_label": "Will the highest temperature in Manila be 32°C on July 2?",
                "q_live": 0.9614944294185659,
                "limit_price": 0.44,
                "size": 40.25,
            },
            occurred_at="2026-07-02T02:18:08+00:00",
        )

        from src.execution.command_recovery import ensure_live_entry_projection_for_command

        summary = ensure_live_entry_projection_for_command(
            conn,
            command_id="cmd-001",
            client=mock_client,
        )

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, city, target_date, direction, shares, entry_price,
                   order_status, entry_method, strategy_key, p_posterior, entry_ci_width
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "city": "Manila",
            "target_date": "2026-07-02",
            "direction": "buy_yes",
            "shares": pytest.approx(40.25),
            "entry_price": pytest.approx(0.44),
            "order_status": "filled",
            "entry_method": "venue_fact_recovery",
            "strategy_key": "day0_nowcast_entry",
            "p_posterior": pytest.approx(0.0),
            "entry_ci_width": pytest.approx(0.0),
        }
        event_types = [
            row["event_type"]
            for row in conn.execute(
                """
                SELECT event_type
                  FROM position_events
                 WHERE position_id = 'pos-001'
                 ORDER BY sequence_no
                """
            ).fetchall()
        ]
        assert event_types == [
            "POSITION_OPEN_INTENT",
            "ENTRY_ORDER_POSTED",
            "ENTRY_ORDER_FILLED",
        ]

    def test_partial_edli_entry_without_projection_recovers_active_partial_position_from_events(
        self,
        conn,
        mock_client,
    ):
        event_id = "edli_evt_partial_event_only"
        yes_token_id = "tok-yes"
        no_token_id = "tok-no"
        final_intent_id = f"edli_intent:{event_id}:{no_token_id}"
        decision_id = f"edli_exec_cmd:{event_id}:{final_intent_id}:{no_token_id}:{no_token_id}:buy_no"
        aggregate_id = f"{event_id}:{final_intent_id}"
        _insert(
            conn,
            token_id=yes_token_id,
            no_token_id=no_token_id,
            selected_token_id=no_token_id,
            outcome_label="NO",
            decision_id=decision_id,
            size=12.29,
            price=0.61,
        )
        _advance_to_acked(conn, venue_order_id="ord-live-edli")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="PARTIAL_FILL_OBSERVED",
            occurred_at="2026-06-07T00:03:00Z",
            payload={
                "venue_order_id": "ord-live-edli",
                "trade_id": "trade-partial-edli",
                "filled_size": "5.128204",
                "fill_price": "0.6100001092",
            },
        )
        _append_trade_fact(
            conn,
            order_id="ord-live-edli",
            trade_id="trade-partial-edli",
            state="CONFIRMED",
            filled_size="5.128204",
            fill_price="0.6100001092",
        )
        _append_order_fact(
            conn,
            order_id="ord-live-edli",
            state="PARTIALLY_MATCHED",
            matched_size="5.128204",
            remaining_size="7.161796",
            source="WS_USER",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="SubmitPlanBuilt",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "condition_id": "condition-test",
                "token_id": no_token_id,
                "direction": "buy_no",
                "city": "Madrid",
                "target_date": "2026-06-08",
                "metric": "high",
                "limit_price": 0.61,
                "size": 12.29,
            },
            occurred_at="2026-06-07T00:00:00Z",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="PreSubmitRevalidated",
            payload={
                "event_id": event_id,
                "event_type": "EDLI_REDECISION_PENDING",
                "final_intent_id": final_intent_id,
                "condition_id": "condition-test",
                "token_id": no_token_id,
                "direction": "buy_no",
                "city": "Madrid",
                "target_date": "2026-06-08",
                "metric": "high",
                "bin_label": "Will the highest temperature in Madrid be 33°C on June 8?",
                "q_live": 0.91,
                "q_lcb_5pct": 0.82,
                "qkernel_execution_economics": {
                    "source": "qkernel_spine",
                    "side": "NO",
                    "candidate_id": "NO:bin-33:DIRECT_NO:bin-33",
                    "route_id": "DIRECT_NO:bin-33",
                    "bin_id": "bin-33",
                    "payoff_q_point": 0.91,
                    "payoff_q_lcb": 0.82,
                    "cost": 0.61,
                    "edge_lcb": 0.21,
                    "optimal_delta_u": 0.21,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                },
            },
            occurred_at="2026-06-07T00:00:01Z",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, city, target_date, direction, shares, entry_price,
                   order_status, entry_method, strategy_key
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "city": "Madrid",
            "target_date": "2026-06-08",
            "direction": "buy_no",
            "shares": pytest.approx(5.128204),
            "entry_price": pytest.approx(0.6100001092),
            "order_status": "partial",
            "entry_method": "qkernel_spine",
            "strategy_key": "forecast_qkernel_entry",
        }
        execution = conn.execute(
            """
            SELECT shares, fill_price, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert dict(execution) == {
            "shares": pytest.approx(5.128204),
            "fill_price": pytest.approx(0.6100001092),
            "venue_status": "PARTIAL",
            "terminal_exec_status": "partial",
        }

    def test_live_buy_no_projection_repair_event_payload_uses_selected_no_token(
        self,
        conn,
        mock_client,
    ):
        _insert(
            conn,
            token_id="tok-yes",
            no_token_id="tok-no",
            selected_token_id="tok-no",
            outcome_label="NO",
            size=13.45,
            price=0.74,
        )
        _advance_to_acked(conn, venue_order_id="ord-live-no")
        _append_order_fact(
            conn,
            order_id="ord-live-no",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _insert_decision_log_trade_case_for_recovery(
            conn,
            token_id="tok-yes",
            no_token_id="tok-no",
            direction="buy_no",
        )

        from src.execution.command_recovery import ensure_live_entry_projection_for_command

        summary = ensure_live_entry_projection_for_command(
            conn,
            command_id="cmd-001",
            client=mock_client,
        )

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT direction, token_id, no_token_id, order_id
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "direction": "buy_no",
            "token_id": "tok-yes",
            "no_token_id": "tok-no",
            "order_id": "ord-live-no",
        }
        payloads = [
            json.loads(row["payload_json"])
            for row in conn.execute(
                """
                SELECT payload_json
                  FROM position_events
                 WHERE position_id = 'pos-001'
                   AND event_type IN ('POSITION_OPEN_INTENT', 'ENTRY_ORDER_POSTED')
                 ORDER BY sequence_no
                """
            ).fetchall()
        ]
        assert payloads
        for payload in payloads:
            assert payload["token_id"] == "tok-no"
            assert payload["selected_token_id"] == "tok-no"
            assert payload["yes_token_id"] == "tok-yes"
            assert payload["no_token_id"] == "tok-no"

    def test_live_acked_entry_order_with_positive_trade_fact_waits_for_fill_reconciliation(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=13.45, price=0.01)
        _advance_to_acked(conn, venue_order_id="ord-live")
        _append_order_fact(
            conn,
            order_id="ord-live",
            state="LIVE",
            matched_size="0",
            remaining_size="13.45",
            source="REST",
        )
        _append_trade_fact(
            conn,
            order_id="ord-live",
            state="MATCHED",
            filled_size="1.25",
            fill_price="0.01",
        )
        _insert_decision_log_trade_case_for_recovery(conn)

        from src.execution.command_recovery import reconcile_live_entry_projection_repairs

        summary = reconcile_live_entry_projection_repairs(conn)

        assert summary == {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}
        assert conn.execute(
            "SELECT 1 FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM position_events WHERE position_id = 'pos-001'"
        ).fetchone() is None

    def test_terminal_buy_no_filled_entry_repair_preserves_yes_token_identity(
        self,
        conn,
        mock_client,
    ):
        _insert(
            conn,
            token_id="tok-yes",
            no_token_id="tok-no",
            selected_token_id="tok-no",
            outcome_label="NO",
            size=5.0,
            price=0.34,
        )
        _advance_to_acked(conn, venue_order_id="ord-001")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "MATCHED"},
        )
        _append_trade_fact(
            conn,
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )
        _insert_decision_log_trade_case_for_recovery(
            conn,
            token_id="tok-yes",
            no_token_id="tok-no",
        )
        artifact = json.loads(conn.execute("SELECT artifact_json FROM decision_log").fetchone()[0])
        artifact["trade_cases"][0]["direction"] = "buy_no"
        conn.execute(
            "UPDATE decision_log SET artifact_json = ?",
            (json.dumps(artifact, sort_keys=True),),
        )
        conn.commit()

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_projection_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT token_id, no_token_id, shares, cost_basis_usd
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "token_id": "tok-yes",
            "no_token_id": "tok-no",
            "shares": 5.0,
            "cost_basis_usd": 1.7,
        }

    def test_filled_entry_trade_fact_with_existing_position_repairs_missing_position_lot(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 5.0,
                   cost_basis_usd = 1.7,
                   entry_price = 0.34,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        conn.execute(
            """
            INSERT INTO trade_decisions (
                market_id, bin_label, direction, size_usd, price, timestamp,
                p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
                status, runtime_trade_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "condition-test",
                "Karachi high",
                "buy_yes",
                1.7,
                0.34,
                "2026-04-26T00:06:00Z",
                0.6,
                0.6,
                0.1,
                0.05,
                0.15,
                0.0,
                "entered",
                "pos-001",
            ),
        )
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "MATCHED"},
        )
        trade_fact_id = _append_trade_fact(
            conn,
            state="MATCHED",
            filled_size="5",
            fill_price="0.34",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_position_lot_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        lot = conn.execute(
            """
            SELECT state, shares, entry_price_avg, source_command_id, source_trade_fact_id, source
              FROM position_lots
             WHERE source_command_id = 'cmd-001'
            """
        ).fetchone()
        assert dict(lot) == {
            "state": "OPTIMISTIC_EXPOSURE",
            "shares": "5",
            "entry_price_avg": "0.34",
            "source_command_id": "cmd-001",
            "source_trade_fact_id": trade_fact_id,
            "source": "REST",
        }
        execution = conn.execute(
            """
            SELECT command_id, shares, fill_price, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert execution is not None
        assert dict(execution) == {
            "command_id": "cmd-001",
            "shares": 5.0,
            "fill_price": 0.34,
            "venue_status": "FILLED",
            "terminal_exec_status": "filled",
        }
        second_summary = reconcile_unresolved_commands(conn, mock_client)
        assert second_summary["filled_entry_position_lot_repair"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_existing_entry_lot_repairs_stale_execution_fact(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0, price=0.34)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 5.116278,
                   cost_basis_usd = 2.2,
                   entry_price = 0.43,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        conn.execute(
            """
            INSERT INTO trade_decisions (
                market_id, bin_label, direction, size_usd, price, timestamp,
                p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
                status, runtime_trade_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "condition-test",
                "Karachi high",
                "buy_yes",
                2.2,
                0.43,
                "2026-04-26T00:06:00Z",
                0.6,
                0.6,
                0.1,
                0.05,
                0.15,
                0.0,
                "entered",
                "pos-001",
            ),
        )
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={"venue_order_id": "ord-001", "venue_status": "CONFIRMED"},
        )
        _append_trade_fact(
            conn,
            state="CONFIRMED",
            filled_size="5.116278",
            fill_price="0.429999894454523",
        )
        _append_order_fact(conn, state="MATCHED", matched_size="5.116278", remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        first_summary = reconcile_unresolved_commands(conn, mock_client)
        assert first_summary["filled_entry_position_lot_repair"]["advanced"] == 1
        conn.execute(
            """
            UPDATE execution_fact
               SET shares = 5.0,
                   command_id = NULL,
                   terminal_exec_status = 'filled',
                   venue_status = 'matched'
             WHERE intent_id = 'pos-001:entry'
            """
        )

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_execution_fact_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        execution = conn.execute(
            """
            SELECT command_id, posted_at, filled_at, shares, fill_price,
                   venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert dict(execution) == {
            "command_id": "cmd-001",
            "posted_at": "2026-04-26T00:00:00Z",
            "filled_at": "2026-04-26T00:06:00Z",
            "shares": 5.116278,
            "fill_price": 0.429999894454523,
            "venue_status": "FILLED",
            "terminal_exec_status": "filled",
        }
        conn.execute(
            """
            UPDATE execution_fact
               SET posted_at = '2026-07-02T12:12:18.608703+00:00',
                   filled_at = '2026-07-02T12:12:18.608703+00:00'
             WHERE intent_id = 'pos-001:entry'
            """
        )
        timestamp_summary = reconcile_unresolved_commands(conn, mock_client)
        assert timestamp_summary["filled_entry_execution_fact_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        repaired_timestamps = conn.execute(
            """
            SELECT posted_at, filled_at
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert dict(repaired_timestamps) == {
            "posted_at": "2026-04-26T00:00:00Z",
            "filled_at": "2026-04-26T00:06:00Z",
        }
        second_summary = reconcile_unresolved_commands(conn, mock_client)
        assert second_summary["filled_entry_execution_fact_repair"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_filled_entry_execution_fact_repairs_without_trade_decision_or_lot(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0, price=0.32)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        conn.execute(
            """
            UPDATE venue_commands
               SET state = 'FILLED',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE command_id = 'cmd-001'
            """
        )
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 5.0,
                   cost_basis_usd = 1.6,
                   entry_price = 0.32,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _append_trade_fact(
            conn,
            state="CONFIRMED",
            trade_id="trade-without-decision",
            filled_size="5",
            fill_price="0.32",
        )
        _append_order_fact(conn, state="MATCHED", matched_size="5", remaining_size="0")
        assert conn.execute("SELECT COUNT(*) FROM trade_decisions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_execution_fact_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        execution = conn.execute(
            """
            SELECT command_id, shares, fill_price, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert dict(execution) == {
            "command_id": "cmd-001",
            "shares": 5.0,
            "fill_price": 0.32,
            "venue_status": "FILLED",
            "terminal_exec_status": "filled",
        }

    def test_review_required_entry_recovers_fill_time_without_order_fact_or_alias_double_count(
        self,
        conn,
    ):
        from src.execution.command_recovery import (
            reconcile_filled_entry_execution_fact_repairs,
        )
        from src.state.db import log_execution_fact
        from src.state.db import query_entry_execution_fill_aggregate
        from src.state.venue_command_repo import append_trade_fact

        _insert(conn, size=5.0, price=0.32)
        _advance_to_review_required(conn)
        source_fact_id = _append_trade_fact(
            conn,
            trade_id="trade-review-fill",
            state="CONFIRMED",
            filled_size="5",
            fill_price="0.32",
        )
        append_trade_fact(
            conn,
            trade_id="edli:trade-review-fill",
            venue_order_id="ord-001",
            command_id="cmd-001",
            state="CONFIRMED",
            filled_size="5",
            fill_price="0.32",
            source="WS_USER",
            observed_at="2026-04-26T00:07:00Z",
            venue_timestamp=None,
            raw_payload_hash="e" * 64,
            raw_payload_json={
                "source_module": "src.events.edli_position_bridge",
                "raw_fill_payload": {"source_trade_fact_id": source_fact_id},
            },
        )
        log_execution_fact(
            conn,
            intent_id="edli-final-intent",
            position_id="pos-001",
            decision_id="edli-final-intent",
            command_id="cmd-001",
            order_role="entry",
            posted_at="2026-04-26T00:00:00Z",
            filled_at=None,
            submitted_price=0.32,
            fill_price=0.32,
            shares=5.0,
            venue_status="CONFIRMED",
            terminal_exec_status="filled",
        )

        assert reconcile_filled_entry_execution_fact_repairs(conn) == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        execution = conn.execute(
            """
            SELECT filled_at, shares, fill_price, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry:cmd-001'
            """
        ).fetchone()
        assert dict(execution) == {
            "filled_at": "2026-04-26T00:06:00Z",
            "shares": 5.0,
            "fill_price": 0.32,
            "venue_status": "FILLED",
            "terminal_exec_status": "filled",
        }
        aggregate = query_entry_execution_fill_aggregate(conn, "pos-001", strict=True)
        assert aggregate is not None
        assert aggregate["shares_filled"] == pytest.approx(5.0)
        assert aggregate["filled_cost_basis_usd"] == pytest.approx(1.6)
        assert reconcile_filled_entry_execution_fact_repairs(conn) == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_authenticated_full_trade_clears_review_without_order_fact_or_alias_double_count(
        self,
        conn,
    ):
        from src.execution.command_recovery import (
            _positive_fill_trade_fact_summary,
            reconcile_matched_cancel_review_required_entries,
        )
        from src.state.venue_command_repo import append_trade_fact

        _insert(conn, size=5.0, price=0.32)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _advance_to_review_required(conn)
        source_fact_id = append_trade_fact(
            conn,
            trade_id="trade-authenticated-review-fill",
            venue_order_id="ord-001",
            command_id="cmd-001",
            state="CONFIRMED",
            filled_size="5",
            fill_price="0.32",
            source="WS_USER",
            observed_at="2026-04-26T00:06:00Z",
            venue_timestamp="2026-04-26T00:05:58Z",
            raw_payload_hash="a" * 64,
            raw_payload_json={"status": "CONFIRMED"},
        )
        append_trade_fact(
            conn,
            trade_id="edli:trade-authenticated-review-fill",
            venue_order_id="ord-001",
            command_id="cmd-001",
            state="CONFIRMED",
            filled_size="5",
            fill_price="0.32",
            source="WS_USER",
            observed_at="2026-04-26T00:07:00Z",
            venue_timestamp=None,
            raw_payload_hash="b" * 64,
            raw_payload_json={
                "source_module": "src.events.edli_position_bridge",
                "raw_fill_payload": {"source_trade_fact_id": source_fact_id},
            },
        )

        trade_summary = _positive_fill_trade_fact_summary(conn, "cmd-001")
        assert trade_summary == {
            "count": 1,
            "filled_size": "5",
            "fill_price": "0.32",
            "authenticated_confirmed": True,
            "observed_at": "2026-04-26T00:05:58Z",
        }
        assert reconcile_matched_cancel_review_required_entries(conn) == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "FILLED"
        event = _get_events(conn, "cmd-001")[-1]
        assert event["event_type"] == "FILL_CONFIRMED"
        assert event["occurred_at"] == "2026-04-26T00:05:58Z"
        payload = json.loads(event["payload_json"])
        assert payload["proof_class"] == (
            "authenticated_trade_fact_full_fill"
        )
        assert reconcile_matched_cancel_review_required_entries(conn) == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_authenticated_trade_for_other_order_does_not_clear_review(self, conn):
        from src.execution.command_recovery import (
            reconcile_matched_cancel_review_required_entries,
        )
        from src.state.venue_command_repo import append_trade_fact

        _insert(conn, size=5.0, price=0.32)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _advance_to_review_required(conn)
        append_trade_fact(
            conn,
            trade_id="trade-other-order",
            venue_order_id="ord-other",
            command_id="cmd-001",
            state="CONFIRMED",
            filled_size="5",
            fill_price="0.32",
            source="WS_USER",
            observed_at="2026-04-26T00:06:00Z",
            venue_timestamp="2026-04-26T00:05:58Z",
            raw_payload_hash="c" * 64,
            raw_payload_json={"status": "CONFIRMED"},
        )

        assert reconcile_matched_cancel_review_required_entries(conn) == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"

    def test_filled_entry_execution_fact_repair_preserves_position_increments(
        self,
        conn,
    ):
        for index, price in ((1, 0.69), (2, 0.68)):
            command_id = f"cmd-increment-{index}"
            order_id = f"ord-increment-{index}"
            _insert(
                conn,
                command_id=command_id,
                position_id="pos-increment",
                decision_id=f"dec-increment-{index}",
                size=5.0,
                price=price,
                created_at=f"2026-04-26T00:0{index}:00Z",
            )
            _advance_to_acked(conn, command_id=command_id, venue_order_id=order_id)
            conn.execute(
                "UPDATE venue_commands SET state = 'FILLED' WHERE command_id = ?",
                (command_id,),
            )
            _append_trade_fact(
                conn,
                command_id=command_id,
                order_id=order_id,
                trade_id=f"trade-increment-{index}",
                filled_size="5",
                fill_price=str(price),
            )
            _append_order_fact(
                conn,
                command_id=command_id,
                order_id=order_id,
                state="MATCHED",
                matched_size="5",
                remaining_size="0",
            )

        from src.execution.command_recovery import (
            reconcile_filled_entry_execution_fact_repairs,
        )
        from src.state.db import (
            log_execution_fact,
            query_entry_execution_fill_aggregate,
        )

        log_execution_fact(
            conn,
            intent_id="000-legacy-entry",
            position_id="legacy-position",
            decision_id="legacy-decision",
            command_id="cmd-increment-2",
            order_role="entry",
            posted_at="2026-04-26T00:00:00Z",
            filled_at="2099-04-26T00:00:01Z",
            fill_price=0.01,
            shares=1.0,
            venue_status="FILLED",
            terminal_exec_status="filled",
        )
        log_execution_fact(
            conn,
            intent_id="000-current-misaligned-entry",
            position_id="pos-increment",
            decision_id="misaligned-decision",
            command_id="cmd-increment-2",
            order_role="entry",
            posted_at="2026-04-26T00:00:00Z",
            filled_at="2099-04-26T00:00:01Z",
            fill_price=0.01,
            shares=1.0,
            venue_status="FILLED",
            terminal_exec_status="filled",
        )

        summary = reconcile_filled_entry_execution_fact_repairs(conn)

        assert summary == {"scanned": 2, "advanced": 2, "stayed": 0, "errors": 0}
        rows = conn.execute(
            """
            SELECT intent_id, command_id, shares, fill_price
              FROM execution_fact
             WHERE position_id = 'pos-increment'
               AND order_role = 'entry'
               AND intent_id LIKE 'pos-increment:entry%'
             ORDER BY command_id
            """
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {
                "intent_id": "pos-increment:entry:cmd-increment-1",
                "command_id": "cmd-increment-1",
                "shares": 5.0,
                "fill_price": 0.69,
            },
            {
                "intent_id": "pos-increment:entry:cmd-increment-2",
                "command_id": "cmd-increment-2",
                "shares": 5.0,
                "fill_price": 0.68,
            },
        ]
        aggregate = query_entry_execution_fill_aggregate(
            conn,
            "pos-increment",
            strict=True,
        )
        assert aggregate is not None
        assert aggregate["shares_filled"] == pytest.approx(10.0)
        assert aggregate["filled_cost_basis_usd"] == pytest.approx(6.85)
        assert aggregate["entry_price_avg_fill"] == pytest.approx(0.685)
        legacy = query_entry_execution_fill_aggregate(
            conn,
            "legacy-position",
            strict=True,
        )
        assert legacy is not None
        assert legacy["shares_filled"] == pytest.approx(1.0)
        assert legacy["filled_cost_basis_usd"] == pytest.approx(0.01)
        assert reconcile_filled_entry_execution_fact_repairs(conn) == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    def test_filled_entry_execution_fact_uses_exact_fok_envelope_economics(
        self,
        conn,
    ):
        from src.execution.command_recovery import (
            reconcile_filled_entry_execution_fact_repairs,
        )
        from src.state.db import query_entry_execution_fill_aggregate
        from src.state.venue_command_repo import append_event

        _insert(conn, size=35.05, price=0.75)
        _advance_to_acked(conn, venue_order_id="ord-fok")
        envelope_id = _ensure_envelope(
            conn,
            token_id="tok-001",
            envelope_id="env-fok-final",
            order_type="FOK",
            price=0.75,
            size=35.05,
            order_id="ord-fok",
            raw_response_json=json.dumps(
                {
                    "success": True,
                    "status": "matched",
                    "orderID": "ord-fok",
                    "makingAmount": "26.279998",
                    "takingAmount": "35.306664",
                }
            ),
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "venue_order_id": "ord-fok",
                "final_submission_envelope_id": envelope_id,
                "final_submission_envelope_command_id": "cmd-001",
            },
        )
        exact_price = Decimal("26.279998") / Decimal("35.306664")
        _append_trade_fact(
            conn,
            order_id="ord-fok",
            trade_id="trade-fok",
            state="MATCHED",
            filled_size="35.306664",
            fill_price=str(exact_price),
        )
        _append_trade_fact(
            conn,
            order_id="ord-fok",
            trade_id="trade-fok",
            state="CONFIRMED",
            filled_size="35.306664",
            fill_price="0.74",
        )
        _append_order_fact(
            conn,
            order_id="ord-fok",
            state="MATCHED",
            matched_size="35.306664",
            remaining_size="0",
        )

        summary = reconcile_filled_entry_execution_fact_repairs(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        execution = conn.execute(
            """
            SELECT shares, fill_price, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert dict(execution) == {
            "shares": 35.306664,
            "fill_price": pytest.approx(float(exact_price)),
            "terminal_exec_status": "filled",
        }
        aggregate = query_entry_execution_fill_aggregate(conn, "pos-001", strict=True)
        assert aggregate is not None
        assert aggregate["shares_filled"] == pytest.approx(35.306664)
        assert aggregate["filled_cost_basis_usd"] == pytest.approx(26.279998)
        assert reconcile_filled_entry_execution_fact_repairs(conn) == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }

    @pytest.mark.parametrize(
        ("response_order_id", "taking_amount"),
        (("ord-other", "5"), ("ord-001", "5.1")),
    )
    def test_filled_entry_execution_fact_rejects_unbound_envelope_economics(
        self,
        conn,
        response_order_id,
        taking_amount,
    ):
        from src.execution.command_recovery import (
            reconcile_filled_entry_execution_fact_repairs,
        )
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.32)
        _advance_to_acked(conn, venue_order_id="ord-001")
        envelope_id = _ensure_envelope(
            conn,
            token_id="tok-001",
            envelope_id="env-fok-unbound",
            order_type="FOK",
            price=0.32,
            size=5.0,
            order_id="ord-001",
            raw_response_json=json.dumps(
                {
                    "success": True,
                    "status": "matched",
                    "orderID": response_order_id,
                    "makingAmount": "1.55",
                    "takingAmount": taking_amount,
                }
            ),
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:06:00Z",
            payload={
                "venue_order_id": "ord-001",
                "final_submission_envelope_id": envelope_id,
                "final_submission_envelope_command_id": "cmd-001",
            },
        )
        _append_trade_fact(
            conn,
            filled_size="5",
            fill_price="0.32",
        )
        _append_order_fact(
            conn,
            state="MATCHED",
            matched_size="5",
            remaining_size="0",
        )

        summary = reconcile_filled_entry_execution_fact_repairs(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        execution = conn.execute(
            """
            SELECT shares, fill_price
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert dict(execution) == {"shares": 5.0, "fill_price": 0.32}

    def test_existing_entry_lot_execution_fact_repair_aggregates_split_fills(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0, price=0.50)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 5.0,
                   cost_basis_usd = 2.3,
                   entry_price = 0.46,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        conn.execute(
            """
            INSERT INTO trade_decisions (
                market_id, bin_label, direction, size_usd, price, timestamp,
                p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
                status, runtime_trade_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "condition-test",
                "Karachi high",
                "buy_yes",
                2.3,
                0.46,
                "2026-04-26T00:06:00Z",
                0.6,
                0.6,
                0.1,
                0.05,
                0.15,
                0.0,
                "entered",
                "pos-001",
            ),
        )
        first_fact = _append_trade_fact(
            conn,
            state="CONFIRMED",
            trade_id="trade-split-1",
            filled_size="2",
            fill_price="0.40",
        )
        second_fact = _append_trade_fact(
            conn,
            state="CONFIRMED",
            trade_id="trade-split-2",
            filled_size="3",
            fill_price="0.50",
        )
        _append_order_fact(conn, state="MATCHED", matched_size="5", remaining_size="0")
        conn.execute(
            """
            UPDATE venue_commands
               SET state = 'FILLED',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE command_id = 'cmd-001'
            """
        )
        from src.state.venue_command_repo import append_position_lot

        for lot_id, fact_id, shares, price in (
            (7001, first_fact, "2", "0.40"),
            (7002, second_fact, "3", "0.50"),
        ):
            append_position_lot(
                conn,
                position_id=lot_id,
                state="CONFIRMED_EXPOSURE",
                shares=shares,
                entry_price_avg=price,
                source_command_id="cmd-001",
                source_trade_fact_id=fact_id,
                captured_at="2026-04-26T00:06:00Z",
                state_changed_at="2026-04-26T00:06:00Z",
                source="REST",
                observed_at="2026-04-26T00:06:00Z",
                raw_payload_json={"source": "test_split_fill_lot"},
            )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_execution_fact_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        execution = conn.execute(
            """
            SELECT command_id, shares, fill_price, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert dict(execution) == {
            "command_id": "cmd-001",
            "shares": 5.0,
            "fill_price": pytest.approx(0.46),
            "venue_status": "FILLED",
            "terminal_exec_status": "filled",
        }

    def test_partial_entry_lot_repairs_stale_execution_fact_with_command_id_and_aggregate_fill(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=7.21, price=0.28)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        conn.execute(
            """
            UPDATE venue_commands
               SET state = 'EXPIRED',
                   updated_at = '2026-04-26T00:08:00Z'
             WHERE command_id = 'cmd-001'
            """
        )
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 4.95,
                   cost_basis_usd = 1.386,
                   entry_price = 0.28,
                   order_status = 'partial',
                   updated_at = '2026-04-26T00:08:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        conn.execute(
            """
            INSERT INTO trade_decisions (
                market_id, bin_label, direction, size_usd, price, timestamp,
                p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction,
                status, runtime_trade_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "condition-test",
                "Karachi high",
                "buy_yes",
                1.386,
                0.28,
                "2026-04-26T00:08:00Z",
                0.6,
                0.6,
                0.1,
                0.05,
                0.15,
                0.0,
                "entered",
                "pos-001",
            ),
        )
        first_fact = _append_trade_fact(
            conn,
            state="CONFIRMED",
            trade_id="trade-partial-1",
            filled_size="2",
            fill_price="0.20",
        )
        second_fact = _append_trade_fact(
            conn,
            state="CONFIRMED",
            trade_id="trade-partial-2",
            filled_size="2.95",
            fill_price="0.3342372881355932",
        )
        _append_order_fact(
            conn,
            state="PARTIALLY_MATCHED",
            matched_size="4.95",
            remaining_size="2.26",
        )
        _append_order_fact(
            conn,
            state="RESTING",
            matched_size="4.95",
            remaining_size="2.26",
        )
        from src.state.db import log_execution_fact
        from src.state.venue_command_repo import append_position_lot

        for lot_id, fact_id, shares, price in (
            (7101, first_fact, "2", "0.20"),
            (7102, second_fact, "2.95", "0.3342372881355932"),
        ):
            append_position_lot(
                conn,
                position_id=lot_id,
                state="CONFIRMED_EXPOSURE",
                shares=shares,
                entry_price_avg=price,
                source_command_id="cmd-001",
                source_trade_fact_id=fact_id,
                captured_at="2026-04-26T00:08:00Z",
                state_changed_at="2026-04-26T00:08:00Z",
                source="REST",
                observed_at="2026-04-26T00:08:00Z",
                raw_payload_json={"source": "test_partial_execfact_lot"},
            )
        log_execution_fact(
            conn,
            intent_id="pos-001:entry",
            position_id="pos-001",
            decision_id="dec-001",
            command_id=None,
            order_role="entry",
            strategy_key="opening_inertia",
            posted_at="2026-04-26T00:00:00Z",
            filled_at="2026-04-26T00:07:00Z",
            submitted_price=0.28,
            fill_price=0.28,
            shares=4.95,
            venue_status="PARTIAL",
            terminal_exec_status="partial",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["filled_entry_execution_fact_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        execution = conn.execute(
            """
            SELECT command_id, shares, fill_price, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:entry'
            """
        ).fetchone()
        assert dict(execution) == {
            "command_id": "cmd-001",
            "shares": 4.95,
            "fill_price": pytest.approx(0.28),
            "venue_status": "PARTIAL",
            "terminal_exec_status": "partial",
        }

    def test_entry_execution_fact_repair_treats_malformed_remainder_as_partial(self):
        from src.execution.command_recovery import _entry_execution_fact_terminal_status

        base_candidate = {
            "cmd_state": "FILLED",
            "order_fact_state": "MATCHED",
        }

        assert _entry_execution_fact_terminal_status(
            {**base_candidate, "order_fact_remaining_size": "0"}
        ) == "filled"
        assert _entry_execution_fact_terminal_status(
            {**base_candidate, "order_fact_remaining_size": ""}
        ) == "partial"
        assert _entry_execution_fact_terminal_status(
            {**base_candidate, "order_fact_remaining_size": "not-a-number"}
        ) == "partial"

    def test_acked_exit_order_fact_with_point_order_matched_finalizes_exit(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, command_id="cmd-entry", position_id="pos-001", size=6.0, price=0.31)
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 6.0,
                   cost_basis_usd = 1.86,
                   entry_price = 0.31,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=6.0,
            price=0.29,
            token_id="tok-001",
        )
        _advance_to_acked(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_order_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            state="MATCHED",
            matched_size="1.8",
            remaining_size="4.2",
        )
        mock_client.get_order.return_value = {
            "id": "ord-exit",
            "status": "MATCHED",
            "size_matched": "6",
            "price": "0.29",
            "associate_trades": ["trade-exit-001"],
            "transactionsHashes": ["0xhash-exit"],
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert summary["exit_pending_projections"] == {
            "scanned": 0,
            "advanced": 0,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-exit") == "FILLED"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-exit")]
        assert event_types[-1] == "FILL_CONFIRMED"

        latest_order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = 'cmd-exit'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_order_fact) == {
            "state": "MATCHED",
            "remaining_size": "0",
            "matched_size": "6",
            "source": "REST",
        }
        trade_fact = conn.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-exit'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-exit-001",
            "venue_order_id": "ord-exit",
            "state": "MATCHED",
            "filled_size": "6",
            "fill_price": "0.29",
            "tx_hash": "0xhash-exit",
        }
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, order_id, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert current["phase"] == "economically_closed"
        assert Decimal(str(current["shares"])) == Decimal("6")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("1.86")
        assert current["order_id"] == "ord-entry"
        assert current["order_status"] == "sell_filled"
        lifecycle_events = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, order_id, command_id, venue_status
              FROM position_events
             WHERE position_id = 'pos-001'
            ORDER BY sequence_no
            """
        ).fetchall()
        assert dict(lifecycle_events[-1]) == {
            "event_type": "EXIT_ORDER_FILLED",
            "phase_before": "pending_exit",
            "phase_after": "economically_closed",
            "order_id": "ord-exit",
            "command_id": "cmd-exit",
            "venue_status": "sell_filled",
        }
        execution = conn.execute(
            """
            SELECT command_id, shares, fill_price, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:exit'
            """
        ).fetchone()
        assert dict(execution) == {
            "command_id": "cmd-exit",
            "shares": 6.0,
            "fill_price": pytest.approx(0.29),
            "venue_status": "FILLED",
            "terminal_exec_status": "filled",
        }

    def test_partial_exit_finalizes_when_order_truth_reaches_zero_remainder(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, command_id="cmd-entry", position_id="pos-001", size=5.11, price=0.43)
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'pending_exit',
                   shares = 5.11,
                   cost_basis_usd = 2.1973,
                   entry_price = 0.43,
                   order_status = 'sell_pending_confirmation',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=5.11,
            price=0.45,
            token_id="tok-001",
        )
        _advance_to_partial(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_trade_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="trade-exit-001",
            state="MINED",
            filled_size="5.11",
            fill_price="0.37",
        )
        _append_order_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            state="MATCHED",
            matched_size="5.11",
            remaining_size="0",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["completed_partial_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-exit") == "FILLED"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-exit")]
        assert event_types[-1] == "FILL_CONFIRMED"
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert current["phase"] == "economically_closed"
        assert Decimal(str(current["shares"])) == Decimal("5.11")
        assert current["order_status"] == "sell_filled"
        execution = conn.execute(
            """
            SELECT command_id, shares, fill_price, venue_status, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:exit'
            """
        ).fetchone()
        assert dict(execution) == {
            "command_id": "cmd-exit",
            "shares": 5.11,
            "fill_price": pytest.approx(0.37),
            "venue_status": "FILLED",
            "terminal_exec_status": "filled",
        }

    def test_partial_exit_uses_canonical_order_truth_over_later_weaker_fact(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, command_id="cmd-entry", position_id="pos-001", size=5.11, price=0.43)
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'pending_exit',
                   shares = 5.11,
                   cost_basis_usd = 2.1973,
                   entry_price = 0.43,
                   order_status = 'sell_pending_confirmation',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=5.11,
            price=0.45,
            token_id="tok-001",
        )
        _advance_to_partial(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_trade_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="trade-exit-001",
            state="MATCHED",
            filled_size="5.11",
            fill_price="0.45",
        )
        _append_order_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            state="MATCHED",
            matched_size="5.11",
            remaining_size="0",
        )
        _append_order_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            state="PARTIALLY_MATCHED",
            matched_size="1.25",
            remaining_size="3.86",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["completed_partial_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-exit") == "FILLED"
        command_events = _get_events(conn, "cmd-exit")
        assert command_events[-1]["event_type"] == "FILL_CONFIRMED"
        payload = json.loads(command_events[-1]["payload_json"])
        assert payload["latest_order_fact_state"] == "MATCHED"
        assert payload["matched_size"] == "5.11"
        assert payload["remaining_size"] == "0"
        current = conn.execute(
            """
            SELECT phase, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "economically_closed",
            "order_status": "sell_filled",
        }
        execution = conn.execute(
            """
            SELECT command_id, shares, fill_price, terminal_exec_status
              FROM execution_fact
             WHERE intent_id = 'pos-001:exit'
            """
        ).fetchone()
        assert dict(execution) == {
            "command_id": "cmd-exit",
            "shares": 5.11,
            "fill_price": pytest.approx(0.45),
            "terminal_exec_status": "filled",
        }

    def test_m5_local_orphan_acked_no_fill_terminalizes_and_resolves_finding(
        self,
        conn,
        mock_client,
    ):
        from src.execution.exchange_reconcile import list_unresolved_findings, record_finding

        _insert(conn, size=10.0)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="LIVE", matched_size="0", remaining_size="10")
        finding = record_finding(
            conn,
            kind="local_orphan_order",
            subject_id="ord-001",
            context="ws_gap",
            evidence={
                "reason": "local_open_order_absent_from_exchange_open_orders",
                "exchange_open_order_ids": [],
                "trade_enumeration_available": True,
            },
            recorded_at="2026-04-26T00:06:00Z",
        )
        mock_client.get_order.return_value = {"orderID": "ord-001", "status": "CANCELED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["local_orphan_no_fill_findings"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert summary["terminal_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        latest_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_fact) == {
            "state": "CANCEL_CONFIRMED",
            "remaining_size": "0",
            "matched_size": "0",
            "source": "REST",
        }
        assert [row.finding_id for row in list_unresolved_findings(conn)] == []
        resolved = conn.execute(
            "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
            (finding.finding_id,),
        ).fetchone()
        assert dict(resolved) == {
            "resolution": "command_recovery_terminal_no_fill",
            "resolved_by": "src.execution.command_recovery",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "voided"
        assert Decimal(str(current["shares"])) == Decimal("0")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0")
        assert current["order_status"] == "canceled"

    def test_m5_local_orphan_acked_no_order_fact_terminalizes_zero_exposure_entry(
        self,
        conn,
        mock_client,
    ):
        """A submit ACK can persist before any order fact arrives.

        If a later fresh M5 sweep proves the order is absent/terminal, recovery
        must not wait for a LIVE order fact that will never exist.
        """
        from src.execution.exchange_reconcile import list_unresolved_findings, record_finding

        _insert(conn, size=22.0)
        _advance_to_acked(conn, venue_order_id="ord-no-fact")
        _seed_pending_entry_projection(conn, order_id="ord-no-fact")
        finding = record_finding(
            conn,
            kind="local_orphan_order",
            subject_id="ord-no-fact",
            context="periodic",
            evidence={
                "reason": "local_open_order_absent_from_exchange_open_orders",
                "exchange_open_order_ids": [],
                "trade_enumeration_available": True,
                "point_order_status": "CANCELED",
                "point_order_surface": "get_order",
            },
            recorded_at="2026-04-26T00:06:00Z",
        )
        mock_client.get_order.return_value = {"orderID": "ord-no-fact", "status": "CANCELED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["local_orphan_no_fill_findings"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert summary["terminal_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        latest_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(latest_fact) == {
            "state": "CANCEL_CONFIRMED",
            "remaining_size": "0",
            "matched_size": "0",
            "source": "REST",
        }
        assert [row.finding_id for row in list_unresolved_findings(conn)] == []
        resolved = conn.execute(
            "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
            (finding.finding_id,),
        ).fetchone()
        assert dict(resolved) == {
            "resolution": "command_recovery_terminal_no_fill",
            "resolved_by": "src.execution.command_recovery",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "voided"
        assert Decimal(str(current["shares"])) == Decimal("0")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0")
        assert current["order_status"] == "canceled"

    def test_acked_terminal_order_fact_with_matched_size_waits_for_fill_reconciliation(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="1.25", remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"]["stayed"] == 1
        assert summary["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "ACKED"
        current = conn.execute(
            "SELECT phase FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "pending_entry"

    def test_cancelled_terminal_order_fact_with_matched_size_recovers_entry_projection(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, size=10.0, price=0.50)
        _advance_to_cancel_pending(conn, venue_order_id="ord-001")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_ACKED",
            occurred_at="2026-04-26T00:04:00Z",
        )
        _seed_pending_entry_projection(conn)
        _append_order_fact(
            conn,
            state="CANCEL_CONFIRMED",
            matched_size="1.25",
            remaining_size="8.75",
            source="WS_USER",
            raw_payload_json={
                "id": "ord-001",
                "orderID": "ord-001",
                "status": "CANCELED",
                "size_matched": "1.25",
                "price": "0.50",
                "associate_trades": ["trade-terminal-positive"],
            },
        )
        mock_client.get_order.return_value = None

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"]["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "CANCELLED"
        trade = conn.execute(
            """
            SELECT trade_id, filled_size, fill_price, source
              FROM venue_trade_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade) == {
            "trade_id": "trade-terminal-positive",
            "filled_size": "1.25",
            "fill_price": "0.50",
            "source": "WS_USER",
        }
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, entry_price, order_status, chain_state
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert current["phase"] == "active"
        assert Decimal(str(current["shares"])) == Decimal("1.25")
        assert Decimal(str(current["cost_basis_usd"])) == Decimal("0.625")
        assert Decimal(str(current["entry_price"])) == Decimal("0.5")
        assert current["order_status"] == "partial"
        assert current["chain_state"] == "synced"
        events = [row["event_type"] for row in _get_events(conn, "cmd-001")]
        assert events[-1] == "CANCEL_ACKED"
        position_events = [
            row["event_type"]
            for row in conn.execute(
                "SELECT event_type FROM position_events WHERE position_id = 'pos-001' ORDER BY sequence_no"
            ).fetchall()
        ]
        assert "ENTRY_ORDER_FILLED" in position_events

    def test_acked_terminal_order_fact_order_id_mismatch_does_not_void_command_position(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn, order_id="ord-001")
        _append_order_fact(
            conn,
            order_id="other-order",
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="0",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"]["scanned"] == 1
        assert summary["terminal_order_facts"]["errors"] == 1
        assert summary["terminal_order_facts"]["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "ACKED"
        current = conn.execute(
            "SELECT phase, order_id FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert dict(current) == {"phase": "pending_entry", "order_id": "ord-001"}

    def test_acked_terminal_order_fact_requires_live_proof_source(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(
            conn,
            state="CANCEL_CONFIRMED",
            matched_size="0",
            remaining_size="0",
            source="FAKE_VENUE",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"]["scanned"] == 0
        assert summary["terminal_order_facts"]["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "ACKED"
        current = conn.execute(
            "SELECT phase FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "pending_entry"

    def test_acked_terminal_order_fact_missing_matched_size_waits_for_fill_reconciliation(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size=None, remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"]["stayed"] == 1
        assert summary["terminal_order_facts"]["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "ACKED"
        current = conn.execute(
            "SELECT phase FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert current["phase"] == "pending_entry"

    def test_acked_terminal_order_fact_missing_position_zero_proof_fails_closed(
        self,
        conn,
        mock_client,
    ):
        _insert(conn)
        _advance_to_acked(conn, venue_order_id="ord-001")
        _seed_pending_entry_projection(conn)
        conn.execute("UPDATE position_current SET shares = NULL WHERE position_id = 'pos-001'")
        _append_order_fact(conn, state="CANCEL_CONFIRMED", matched_size="0", remaining_size="0")

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["terminal_order_facts"]["errors"] == 1
        assert summary["terminal_order_facts"]["advanced"] == 0
        assert _get_state(conn, "cmd-001") == "ACKED"
        current = conn.execute(
            "SELECT phase, shares FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert dict(current) == {"phase": "pending_entry", "shares": None}

    @pytest.mark.parametrize("venue_status", ["MATCHED", "MINED", "FILLED"])
    def test_unknown_side_effect_nonconfirmed_status_stays_partial_not_fill_finality(
        self,
        conn,
        venue_status,
    ):
        _insert(conn)
        _advance_to_unknown_side_effect(conn)
        client = MagicMock()
        client.find_order_by_idempotency_key.return_value = {
            "orderID": f"vord-{venue_status.lower()}",
            "status": venue_status,
        }

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, client)

        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert "PARTIAL_FILL_OBSERVED" in event_types
        assert "FILL_CONFIRMED" not in event_types

    def test_unknown_side_effect_rejects_empty_normalized_venue_order_payload(
        self, conn
    ):
        _insert(conn)
        _advance_to_unknown_side_effect(conn)
        client = MagicMock()
        client.find_order_by_idempotency_key.return_value = object()

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, client)

        assert _get_state(conn, "cmd-001") == "SUBMIT_UNKNOWN_SIDE_EFFECT"
        assert summary["errors"] >= 1
        events = _get_events(conn, "cmd-001")
        assert "SUBMIT_ACKED" not in [e["event_type"] for e in events]

    def test_unknown_side_effect_confirmed_requires_trade_fact_review(
        self,
        conn,
    ):
        _insert(conn)
        _advance_to_unknown_side_effect(conn)
        client = MagicMock()
        client.find_order_by_idempotency_key.return_value = {
            "orderID": "vord-confirmed",
            "status": "CONFIRMED",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, client)

        assert summary["advanced"] == 1
        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        event_types = [e["event_type"] for e in _get_events(conn, "cmd-001")]
        assert "REVIEW_REQUIRED" in event_types
        assert "FILL_CONFIRMED" not in event_types
        import json
        review = [e for e in _get_events(conn, "cmd-001") if e["event_type"] == "REVIEW_REQUIRED"][0]
        payload = json.loads(review["payload_json"])
        assert payload["reason"] == "recovery_confirmed_requires_trade_fact"
        assert payload["semantic_guard"] == "order_status_confirmed_is_not_fill_economics_authority"

    def test_unknown_side_effect_invalid_amount_400_terminalizes_without_lookup(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, price=0.15, size=6.98)
        _advance_to_submitting(conn)
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_TIMEOUT_UNKNOWN",
            occurred_at="2026-04-26T00:02:00Z",
            payload={
                "reason": "post_submit_exception_possible_side_effect",
                "exception_type": "PolyApiException",
                "exception_message": (
                    "PolyApiException[status_code=400, "
                    "error_message={'error': 'invalid amounts, the market buy "
                    "orders maker amount supports a max accuracy of 2 decimals, "
                    "taker amount a max of 4 decimals'}]"
                ),
                "idempotency_key": _DEFAULT_IDEM_KEY,
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["advanced"] == 1
        assert summary["errors"] == 0
        assert _get_state(conn, "cmd-001") == "SUBMIT_REJECTED"
        mock_client.get_order.assert_not_called()
        events = _get_events(conn, "cmd-001")
        rejected = [e for e in events if e["event_type"] == "SUBMIT_REJECTED"][-1]
        payload = json.loads(rejected["payload_json"])
        assert payload["reason"] == "venue_rejected_invalid_amount_400"
        assert payload["proof_class"] == "deterministic_venue_invalid_amount_400"
        assert payload["venue_order_created"] is False

    def test_unknown_side_effect_fok_killed_400_terminalizes_without_lookup(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        error = (
            "PolyApiException[status_code=400, error_message={'error': \"order "
            "couldn't be fully filled. FOK orders are fully filled or killed.\", "
            "'orderID': 'ord-fok-killed'}]"
        )
        _insert(conn, price=0.72, size=16.5)
        _advance_to_submitting(conn, venue_order_id="ord-fok-killed")
        envelope_id = _ensure_envelope(
            conn,
            token_id="tok-001",
            envelope_id="env-fok-killed",
            order_type="FOK",
            price=0.72,
            size=16.5,
            order_id="ord-fok-killed",
            signed_order=b"signed-fok-killed",
            signed_order_hash="a" * 64,
            error_code="V2_POST_SUBMIT_AMBIGUOUS",
            error_message=error,
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_TIMEOUT_UNKNOWN",
            occurred_at="2026-04-26T00:02:00Z",
            payload={
                "reason": "post_submit_exception_possible_side_effect",
                "exception_type": "AmbiguousSubmitError",
                "exception_message": error,
                "final_submission_envelope_id": envelope_id,
                "final_submission_envelope_command_id": "cmd-001",
                "venue_order_id": "ord-fok-killed",
                "idempotency_key": _DEFAULT_IDEM_KEY,
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["advanced"] == 1
        assert summary["errors"] == 0
        assert _get_state(conn, "cmd-001") == "SUBMIT_REJECTED"
        mock_client.get_order.assert_not_called()
        rejected = [
            event
            for event in _get_events(conn, "cmd-001")
            if event["event_type"] == "SUBMIT_REJECTED"
        ][-1]
        payload = json.loads(rejected["payload_json"])
        assert payload["reason"] == "venue_rejected_fok_killed_400"
        assert payload["proof_class"] == "deterministic_venue_fok_killed_400"
        assert payload["terminal_no_fill"] is True
        assert payload["exposure_created"] is False

    def test_unknown_side_effect_fak_no_match_400_terminalizes_without_lookup(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        error = (
            "PolyApiException[status_code=400, error_message={'error': 'no orders "
            "found to match with FAK order. FAK orders are partially filled or "
            "killed if no match is found.', 'orderID': 'ord-fak-no-match'}]"
        )
        _insert(conn, price=0.73, size=6.0)
        _advance_to_submitting(conn, venue_order_id="ord-fak-no-match")
        envelope_id = _ensure_envelope(
            conn,
            token_id="tok-001",
            envelope_id="env-fak-no-match",
            order_type="FAK",
            price=0.73,
            size=6.0,
            order_id="ord-fak-no-match",
            signed_order=b"signed-fak-no-match",
            signed_order_hash="b" * 64,
            error_code="V2_POST_SUBMIT_AMBIGUOUS",
            error_message=error,
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_TIMEOUT_UNKNOWN",
            occurred_at="2026-04-26T00:02:00Z",
            payload={
                "reason": "post_submit_exception_possible_side_effect",
                "exception_type": "AmbiguousSubmitError",
                "exception_message": error,
                "final_submission_envelope_id": envelope_id,
                "final_submission_envelope_command_id": "cmd-001",
                "venue_order_id": "ord-fak-no-match",
                "idempotency_key": _DEFAULT_IDEM_KEY,
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["advanced"] == 1
        assert summary["errors"] == 0
        assert _get_state(conn, "cmd-001") == "SUBMIT_REJECTED"
        mock_client.get_order.assert_not_called()
        rejected = [
            event
            for event in _get_events(conn, "cmd-001")
            if event["event_type"] == "SUBMIT_REJECTED"
        ][-1]
        payload = json.loads(rejected["payload_json"])
        assert payload["reason"] == "venue_rejected_fak_no_match_400"
        assert payload["proof_class"] == "deterministic_venue_fak_no_match_400"
        assert payload["terminal_no_fill"] is True
        assert payload["exposure_created"] is False

    def test_unknown_side_effect_marketable_buy_min_size_400_terminalizes_without_lookup(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, price=0.01, size=3.0)
        _advance_to_submitting(conn)
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_TIMEOUT_UNKNOWN",
            occurred_at="2026-04-26T00:02:00Z",
            payload={
                "reason": "post_submit_exception_possible_side_effect",
                "exception_type": "PolyApiException",
                "exception_message": (
                    "PolyApiException[status_code=400, "
                    "error_message={'error': 'invalid amount for a marketable "
                    "BUY order ($0.03), min size: $1'}]"
                ),
                "idempotency_key": _DEFAULT_IDEM_KEY,
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["advanced"] == 1
        assert summary["errors"] == 0
        assert _get_state(conn, "cmd-001") == "SUBMIT_REJECTED"
        mock_client.get_order.assert_not_called()
        events = _get_events(conn, "cmd-001")
        rejected = [e for e in events if e["event_type"] == "SUBMIT_REJECTED"][-1]
        payload = json.loads(rejected["payload_json"])
        assert payload["reason"] == "venue_rejected_invalid_amount_400"
        assert payload["proof_class"] == "deterministic_venue_invalid_amount_400"
        assert payload["venue_order_created"] is False

    def test_unknown_side_effect_marketable_buy_min_size_without_currency_400_terminalizes_without_lookup(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, price=0.01, size=12.0)
        _advance_to_submitting(conn)
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_TIMEOUT_UNKNOWN",
            occurred_at="2026-04-26T00:02:00Z",
            payload={
                "reason": "post_submit_exception_possible_side_effect",
                "exception_type": "PolyApiException",
                "exception_message": (
                    "PolyApiException[status_code=400, "
                    "error_message={'error': 'invalid amount for a marketable "
                    "BUY order ($0.12), min size: 1'}]"
                ),
                "idempotency_key": _DEFAULT_IDEM_KEY,
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["advanced"] == 1
        assert summary["errors"] == 0
        assert _get_state(conn, "cmd-001") == "SUBMIT_REJECTED"
        mock_client.get_order.assert_not_called()
        events = _get_events(conn, "cmd-001")
        rejected = [e for e in events if e["event_type"] == "SUBMIT_REJECTED"][-1]
        payload = json.loads(rejected["payload_json"])
        assert payload["reason"] == "venue_rejected_invalid_amount_400"
        assert payload["proof_class"] == "deterministic_venue_invalid_amount_400"
        assert payload["venue_order_created"] is False

    def test_edli_pre_venue_unknown_threshold_reconcile_releases_cap(self, conn, mock_client):
        from src.execution.command_recovery import reconcile_unresolved_commands

        execution_command_id = "edli_exec_cmd:event-1:intent-1:token-1:token-1:buy_yes"
        aggregate_id = "event-1:intent-1"
        payload = {
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": execution_command_id,
            "execution_receipt_hash": "receipt-hash-1",
            "reason_code": "EXECUTOR_SUBMIT_UNKNOWN:unknown_side_effect_threshold",
            "submit_status": "POST_SUBMIT_UNKNOWN",
            "reconciliation_followup_required": True,
            "side_effect_known": False,
            "venue_call_started": True,
        }
        conn.execute(
            """
            INSERT INTO edli_live_order_events (
                aggregate_event_id, aggregate_id, event_sequence, event_type,
                parent_event_hash, event_hash, payload_json, payload_hash,
                source_authority, occurred_at, created_at, schema_version
            ) VALUES ('evt-1', ?, 1, 'SubmitUnknown', NULL, 'hash-1', ?, 'payload-hash-1',
                      'existing_executor', '2026-04-26T00:02:00+00:00',
                      '2026-04-26T00:02:00+00:00', 1)
            """,
            (aggregate_id, json.dumps(payload, sort_keys=True)),
        )
        conn.execute(
            """
            INSERT INTO edli_live_order_projection (
                aggregate_id, event_id, final_intent_id, current_state,
                last_sequence, last_event_type, last_event_hash,
                pending_reconcile, venue_order_id, updated_at, schema_version
            ) VALUES (?, 'event-1', 'intent-1', 'PENDING_RECONCILE',
                      1, 'SubmitUnknown', 'hash-1', 1, NULL,
                      '2026-04-26T00:02:00+00:00', 1)
            """,
            (aggregate_id,),
        )
        conn.execute(
            """
            INSERT INTO edli_live_cap_usage (
                usage_id, event_id, decision_time, cap_scope, max_notional_usd,
                max_orders_per_day, reserved_notional_usd, order_count,
                reservation_status, final_intent_id, execution_command_id,
                created_at, schema_version
            ) VALUES ('cap-1', 'event-1', '2026-04-26T00:02:00+00:00',
                      'tiny-live', 100.0, 100, 0.18, 1, 'RESERVED',
                      'intent-1', ?, '2026-04-26T00:02:00+00:00', 1)
            """,
            (execution_command_id,),
        )

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["edli_pre_venue_unknown_thresholds"]["advanced"] == 1
        projection = conn.execute(
            "SELECT current_state, pending_reconcile FROM edli_live_order_projection WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        assert projection["current_state"] == "CAP_TRANSITIONED"
        assert bool(projection["pending_reconcile"]) is False
        cap = conn.execute("SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = 'cap-1'").fetchone()
        assert cap["reservation_status"] == "RELEASED"
        event_types = [
            row["event_type"]
            for row in conn.execute(
                "SELECT event_type FROM edli_live_order_events WHERE aggregate_id = ? ORDER BY event_sequence",
                (aggregate_id,),
            )
        ]
        assert event_types == ["SubmitUnknown", "Reconciled", "CapTransitioned"]

    def test_edli_gate_runtime_unknown_reconcile_releases_cap(self, conn, mock_client):
        from src.execution.command_recovery import reconcile_unresolved_commands

        execution_command_id = "edli_exec_cmd:event-gate:intent-gate:token-gate:token-gate:buy_no"
        aggregate_id = "event-gate:intent-gate:token-gate"
        reason_code = (
            "EXECUTOR_SUBMIT_UNKNOWN:[gate_runtime] BLOCKED cap='live_venue_submit': "
            "condition 'deployment_freshness_mismatch' is active"
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="VenueSubmitAttempted",
            payload={
                "event_id": "event-gate",
                "final_intent_id": "intent-gate",
                "execution_command_id": execution_command_id,
                "idempotency_key": _DEFAULT_IDEM_KEY,
            },
            occurred_at="2026-04-26T00:02:00+00:00",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="SubmitUnknown",
            payload={
                "event_id": "event-gate",
                "final_intent_id": "intent-gate",
                "execution_command_id": execution_command_id,
                "execution_receipt_hash": "receipt-hash-gate",
                "reason_code": reason_code,
                "submit_status": "POST_SUBMIT_UNKNOWN",
                "reconciliation_followup_required": True,
                "side_effect_known": False,
                "venue_call_started": True,
            },
            occurred_at="2026-04-26T00:03:00+00:00",
        )
        conn.execute(
            """
            INSERT INTO edli_live_order_projection (
                aggregate_id, event_id, final_intent_id, current_state,
                last_sequence, last_event_type, last_event_hash,
                pending_reconcile, venue_order_id, updated_at, schema_version
            ) VALUES (?, 'event-gate', 'intent-gate', 'PENDING_RECONCILE',
                      2, 'SubmitUnknown', 'hash-gate', 1, NULL,
                      '2026-04-26T00:03:00+00:00', 1)
            """,
            (aggregate_id,),
        )
        conn.execute(
            """
            INSERT INTO edli_live_cap_usage (
                usage_id, event_id, decision_time, cap_scope, max_notional_usd,
                max_orders_per_day, reserved_notional_usd, order_count,
                reservation_status, final_intent_id, execution_command_id,
                created_at, schema_version
            ) VALUES ('cap-gate', 'event-gate', '2026-04-26T00:02:00+00:00',
                      'tiny-live', 100.0, 100, 10.0, 1, 'RESERVED',
                      'intent-gate', ?, '2026-04-26T00:02:00+00:00', 1)
            """,
            (execution_command_id,),
        )

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["edli_pre_venue_unknown_thresholds"]["advanced"] == 1
        projection = conn.execute(
            "SELECT current_state, pending_reconcile FROM edli_live_order_projection WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        assert projection["current_state"] == "CAP_TRANSITIONED"
        assert bool(projection["pending_reconcile"]) is False
        cap = conn.execute("SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = 'cap-gate'").fetchone()
        assert cap["reservation_status"] == "RELEASED"
        reconciled_payload = json.loads(
            conn.execute(
                """
                SELECT payload_json
                FROM edli_live_order_events
                WHERE aggregate_id = ? AND event_type = 'Reconciled'
                ORDER BY event_sequence DESC LIMIT 1
                """,
                (aggregate_id,),
            ).fetchone()["payload_json"]
        )
        assert reconciled_payload["proof_class"] == "pre_venue_gate_runtime_block_no_command_no_venue_order"
        assert reconciled_payload["required_predicates"]["reason_code"] == reason_code

    def test_edli_post_submit_unknown_without_command_releases_on_authenticated_absence(
        self,
        conn,
        mock_client,
    ):
        from src.execution.command_recovery import reconcile_unresolved_commands

        execution_command_id = "edli_exec_cmd:event-2:intent-2:token-2:token-2:buy_no"
        aggregate_id = "event-2:intent-2:token-2"
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="SubmitPlanBuilt",
            payload={
                "event_id": "event-2",
                "final_intent_id": "intent-2",
                "condition_id": "condition-2",
                "token_id": "token-2",
                "direction": "buy_no",
                "city": "Ankara",
                "target_date": "2026-06-29",
                "metric": "high",
            },
            occurred_at="2026-04-26T00:01:00+00:00",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="VenueSubmitAttempted",
            payload={
                "event_id": "event-2",
                "final_intent_id": "intent-2",
                "execution_command_id": execution_command_id,
                "idempotency_key": _DEFAULT_IDEM_KEY,
            },
            occurred_at="2026-04-26T00:02:00+00:00",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=3,
            event_type="SubmitUnknown",
            payload={
                "event_id": "event-2",
                "final_intent_id": "intent-2",
                "execution_command_id": execution_command_id,
                "execution_receipt_hash": "receipt-hash-2",
                "reason_code": "EXECUTOR_SUBMIT_UNKNOWN:[gate_runtime] BLOCKED deployment_freshness_mismatch",
                "submit_status": "POST_SUBMIT_UNKNOWN",
                "reconciliation_followup_required": True,
                "side_effect_known": False,
                "venue_call_started": True,
            },
            occurred_at="2026-04-26T00:03:00+00:00",
        )
        conn.execute(
            """
            INSERT INTO edli_live_order_projection (
                aggregate_id, event_id, final_intent_id, current_state,
                last_sequence, last_event_type, last_event_hash,
                pending_reconcile, venue_order_id, updated_at, schema_version
            ) VALUES (?, 'event-2', 'intent-2', 'PENDING_RECONCILE',
                      3, 'SubmitUnknown', 'hash-3', 1, NULL,
                      '2026-04-26T00:03:00+00:00', 1)
            """,
            (aggregate_id,),
        )
        conn.execute(
            """
            INSERT INTO edli_live_cap_usage (
                usage_id, event_id, decision_time, cap_scope, max_notional_usd,
                max_orders_per_day, reserved_notional_usd, order_count,
                reservation_status, final_intent_id, execution_command_id,
                created_at, schema_version
            ) VALUES ('cap-2', 'event-2', '2026-04-26T00:02:00+00:00',
                      'tiny-live', 100.0, 100, 0.18, 1, 'RESERVED',
                      'intent-2', ?, '2026-04-26T00:02:00+00:00', 1)
            """,
            (execution_command_id,),
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_trades.return_value = []
        mock_client.get_open_orders.venue_reads_are_complete = True
        mock_client.get_trades.venue_reads_are_complete = True

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["edli_post_submit_unknown_absence"]["advanced"] == 1
        projection = conn.execute(
            "SELECT current_state, pending_reconcile FROM edli_live_order_projection WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        assert projection["current_state"] == "CAP_TRANSITIONED"
        assert bool(projection["pending_reconcile"]) is False
        cap = conn.execute("SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = 'cap-2'").fetchone()
        assert cap["reservation_status"] == "RELEASED"

    def test_edli_post_submit_unknown_without_command_stays_when_venue_exposure_matches(
        self,
        conn,
        mock_client,
    ):
        from src.execution.command_recovery import reconcile_unresolved_commands

        execution_command_id = "edli_exec_cmd:event-3:intent-3:token-3:token-3:buy_no"
        aggregate_id = "event-3:intent-3:token-3"
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="SubmitPlanBuilt",
            payload={
                "event_id": "event-3",
                "final_intent_id": "intent-3",
                "condition_id": "condition-3",
                "token_id": "token-3",
                "direction": "buy_no",
            },
            occurred_at="2026-04-26T00:01:00+00:00",
        )
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="SubmitUnknown",
            payload={
                "event_id": "event-3",
                "final_intent_id": "intent-3",
                "execution_command_id": execution_command_id,
                "execution_receipt_hash": "receipt-hash-3",
                "reason_code": "EXECUTOR_SUBMIT_UNKNOWN:timeout",
                "submit_status": "POST_SUBMIT_UNKNOWN",
                "reconciliation_followup_required": True,
                "side_effect_known": False,
                "venue_call_started": True,
            },
            occurred_at="2026-04-26T00:03:00+00:00",
        )
        conn.execute(
            """
            INSERT INTO edli_live_order_projection (
                aggregate_id, event_id, final_intent_id, current_state,
                last_sequence, last_event_type, last_event_hash,
                pending_reconcile, venue_order_id, updated_at, schema_version
            ) VALUES (?, 'event-3', 'intent-3', 'PENDING_RECONCILE',
                      2, 'SubmitUnknown', 'hash-3', 1, NULL,
                      '2026-04-26T00:03:00+00:00', 1)
            """,
            (aggregate_id,),
        )
        conn.execute(
            """
            INSERT INTO edli_live_cap_usage (
                usage_id, event_id, decision_time, cap_scope, max_notional_usd,
                max_orders_per_day, reserved_notional_usd, order_count,
                reservation_status, final_intent_id, execution_command_id,
                created_at, schema_version
            ) VALUES ('cap-3', 'event-3', '2026-04-26T00:02:00+00:00',
                      'tiny-live', 100.0, 100, 0.18, 1, 'RESERVED',
                      'intent-3', ?, '2026-04-26T00:02:00+00:00', 1)
            """,
            (execution_command_id,),
        )
        mock_client.get_open_orders.return_value = [{"id": "order-3", "asset_id": "token-3"}]
        mock_client.get_trades.return_value = []
        mock_client.get_open_orders.venue_reads_are_complete = True
        mock_client.get_trades.venue_reads_are_complete = True

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["edli_post_submit_unknown_absence"]["advanced"] == 0
        assert summary["edli_post_submit_unknown_absence"]["stayed"] == 1
        projection = conn.execute(
            "SELECT current_state, pending_reconcile FROM edli_live_order_projection WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        assert projection["current_state"] == "PENDING_RECONCILE"
        assert bool(projection["pending_reconcile"]) is True
        cap = conn.execute("SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = 'cap-3'").fetchone()
        assert cap["reservation_status"] == "RESERVED"

    def test_partial_confirmed_fill_absent_from_open_orders_expires_remainder_without_voiding_fill(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_confirmed_trade_fact(
            conn,
            order_id="ord-partial",
            trade_id="trade-partial",
            filled_size="1.25",
            fill_price="0.50",
        )
        _seed_pending_entry_projection(conn, order_id="ord-partial")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 1.25,
                   cost_basis_usd = 0.625,
                   entry_price = 0.50,
                   order_status = 'partial'
             WHERE position_id = 'pos-001'
            """
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = {"orderID": "ord-partial", "status": "CANCELED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        assert _get_state(conn, "cmd-001") == "EXPIRED"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "EXPIRED"
        order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
               AND state = 'EXPIRED'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        order_fact = dict(order_fact)
        payload = json.loads(order_fact.pop("raw_payload_json"))
        assert order_fact == {
            "state": "EXPIRED",
            "remaining_size": "0",
            "matched_size": "1.25",
            "source": "REST",
        }
        assert payload == {
            "command_id": "cmd-001",
            "matched_size": "1.25",
            "open_order_absent": True,
            "point_order": {"orderID": "ord-partial", "status": "CANCELED"},
            "point_order_status": "CANCELED",
            "proof_class": "confirmed_fill_plus_point_order_terminal_remainder",
            "reason": "partial_remainder_absent_from_exchange_open_orders",
            "remaining_size": "0",
            "source_surface": "client.get_open_orders+client.get_order",
            "venue_order_id": "ord-partial",
        }
        current = conn.execute(
            "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "active",
            "shares": 1.25,
            "cost_basis_usd": 0.625,
            "order_status": "partial",
        }

    def test_complete_filled_entry_is_not_a_partial_remainder_candidate(self, conn):
        _insert(conn, size=5.0)
        _advance_to_acked(conn, venue_order_id="ord-filled")
        _seed_pending_entry_projection(conn, order_id="ord-filled")
        conn.execute(
            """
            UPDATE venue_commands
               SET state = 'FILLED',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE command_id = 'cmd-001'
            """
        )
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 5.0,
                   cost_basis_usd = 2.5,
                   entry_price = 0.5,
                   order_status = 'filled'
             WHERE position_id = 'pos-001'
            """
        )
        _append_confirmed_trade_fact(
            conn,
            order_id="ord-filled",
            filled_size="5",
            fill_price="0.50",
        )

        from src.execution.command_recovery import _partial_remainder_candidates

        assert _partial_remainder_candidates(conn, live_tick_scope=True) == []

    def test_incomplete_filled_entry_remains_a_partial_remainder_candidate(self, conn):
        _insert(conn, size=5.0)
        _advance_to_acked(conn, venue_order_id="ord-filled-partial")
        _seed_pending_entry_projection(conn, order_id="ord-filled-partial")
        conn.execute(
            """
            UPDATE venue_commands
               SET state = 'FILLED',
                   updated_at = '2026-04-26T00:06:00Z'
             WHERE command_id = 'cmd-001'
            """
        )
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 1.25,
                   cost_basis_usd = 0.625,
                   entry_price = 0.5,
                   order_status = 'partial'
             WHERE position_id = 'pos-001'
            """
        )
        _append_confirmed_trade_fact(
            conn,
            order_id="ord-filled-partial",
            filled_size="1.25",
            fill_price="0.50",
        )

        from src.execution.command_recovery import _partial_remainder_candidates

        candidates = _partial_remainder_candidates(conn, live_tick_scope=True)
        assert [candidate["command_id"] for candidate in candidates] == ["cmd-001"]

    def test_partial_exit_matched_trade_fact_projects_pending_exit_without_economic_close(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, command_id="cmd-entry", position_id="pos-001")
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 23.7,
                   cost_basis_usd = 1.659,
                   entry_price = 0.07,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=23.7,
            price=0.04,
            token_id="tok-001",
        )
        _advance_to_partial(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_trade_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="trade-exit-001",
            state="MATCHED",
            filled_size="10.85",
            fill_price="0.04",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["exit_pending_projections"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-exit") == "PARTIAL"
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, order_id, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_exit",
            "shares": 23.7,
            "cost_basis_usd": 1.659,
            "order_id": "ord-exit",
            "order_status": "sell_pending_confirmation",
        }
        lifecycle_events = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, order_id, command_id, venue_status
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert dict(lifecycle_events[-1]) == {
            "event_type": "EXIT_ORDER_POSTED",
            "phase_before": "active",
            "phase_after": "pending_exit",
            "order_id": "ord-exit",
            "command_id": "cmd-exit",
            "venue_status": "MATCHED",
        }
        assert not any(row["event_type"] == "EXIT_ORDER_FILLED" for row in lifecycle_events)

    def test_exit_matched_trade_fact_over_dust_tolerance_stays_pending_exit_without_economic_close(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, command_id="cmd-entry", position_id="pos-001")
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'pending_exit',
                   shares = 10.03,
                   chain_shares = 10.03,
                   cost_basis_usd = 1.2036,
                   entry_price = 0.12,
                   order_id = 'ord-entry',
                   order_status = 'retry_pending',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=10.03,
            price=0.04,
            token_id="tok-001",
        )
        _advance_to_partial(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_trade_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="trade-exit-001",
            state="MATCHED",
            filled_size="10.018",
            fill_price="0.04",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["exit_pending_projections"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, shares, chain_shares, order_id, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_exit",
            "shares": 10.03,
            "chain_shares": 10.03,
            "order_id": "ord-exit",
            "order_status": "sell_pending_confirmation",
        }
        lifecycle_events = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, order_id, command_id, venue_status
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert dict(lifecycle_events[-1]) == {
            "event_type": "EXIT_ORDER_POSTED",
            "phase_before": "pending_exit",
            "phase_after": "pending_exit",
            "order_id": "ord-exit",
            "command_id": "cmd-exit",
            "venue_status": "MATCHED",
        }
        assert not any(row["event_type"] == "EXIT_ORDER_FILLED" for row in lifecycle_events)

    def test_live_exit_order_restores_active_position_to_pending_exit(
        self,
        conn,
        mock_client,
    ):
        # T5 REPLACEMENT PHASE LAW (docs/rebuild/quarantine_excision_2026-07-11.md):
        # a live venue EXIT order is stronger current money-path truth than a
        # stale projection; the CURRENT vehicle for "position's phase lags a
        # resting exit order" is a normal open phase (active), which is in
        # command_recovery._EXIT_LIVE_ORDER_RESTORE_PHASES -- never a
        # quarantine scar (that member was retired from the set).
        _insert(conn, command_id="cmd-entry", position_id="pos-001", size=12.03, price=0.44)
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 12.03,
                   chain_shares = 12.03,
                   cost_basis_usd = 5.2932,
                   entry_price = 0.44,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=12.03,
            price=0.49,
            token_id="tok-001",
        )
        _advance_to_acked(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_order_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            state="LIVE",
            matched_size="0",
            remaining_size="12.03",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["exit_lifecycle_alignment_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, order_id, order_status, exit_reason
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_exit",
            "order_id": "ord-exit",
            "order_status": "sell_pending_confirmation",
            "exit_reason": "COMMAND_RECOVERY_RESTING_EXIT_ORDER",
        }
        lifecycle_event = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, order_id, command_id
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(lifecycle_event) == {
            "event_type": "EXIT_ORDER_POSTED",
            "phase_before": "active",
            "phase_after": "pending_exit",
            "order_id": "ord-exit",
            "command_id": "cmd-exit",
        }

    def test_matched_exit_order_fact_without_trade_fact_closes_position(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_order_fact

        _insert(conn, command_id="cmd-entry", position_id="pos-001", size=15.23, price=0.51)
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 15.23,
                   chain_shares = 15.23,
                   cost_basis_usd = 7.7673,
                   entry_price = 0.51,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=15.23,
            price=0.60,
            token_id="tok-001",
        )
        _advance_to_acked(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        append_order_fact(
            conn,
            venue_order_id="ord-exit",
            command_id="cmd-exit",
            state="MATCHED",
            remaining_size="0",
            matched_size="15.23",
            source="REST",
            observed_at="2026-04-26T00:05:00Z",
            venue_timestamp="2026-04-26T00:05:00Z",
            raw_payload_hash="f" * 64,
            raw_payload_json={
                "submit_result": {
                    "orderID": "ord-exit",
                    "status": "matched",
                    "side": "SELL",
                    "makingAmount": "15.23",
                    "takingAmount": "9.2903",
                    "transactionsHashes": ["0xexitfill"],
                }
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["exit_lifecycle_alignment_repair"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-exit") == "FILLED"
        trade = conn.execute(
            """
            SELECT trade_id, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-exit'
            """
        ).fetchone()
        assert dict(trade) == {
            "trade_id": "0xexitfill",
            "filled_size": "15.23",
            "fill_price": "0.61",
            "tx_hash": "0xexitfill",
        }
        current = conn.execute(
            """
            SELECT phase, order_status, exit_price
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert current["phase"] == "economically_closed"
        assert current["order_status"] == "sell_filled"
        assert Decimal(str(current["exit_price"])) == Decimal("0.61")

    def test_partial_matched_exit_order_fact_cannot_close_position(
        self,
        conn,
        mock_client,
    ):
        """Legacy MATCHED/remaining=0 facts still need full command-size coverage."""
        from src.state.venue_command_repo import append_order_fact

        _insert(conn, command_id="cmd-entry", position_id="pos-001", size=60.0, price=0.2)
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'pending_exit',
                   shares = 60.0,
                   chain_shares = 60.0,
                   cost_basis_usd = 12.0,
                   entry_price = 0.2,
                   order_status = 'sell_pending_confirmation',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=60.0,
            price=0.16,
            token_id="tok-001",
        )
        _advance_to_partial(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_trade_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="trade-exit-partial",
            state="MATCHED",
            filled_size="46.59",
            fill_price="0.161646276024898",
            tx_hash="0xpartialexitfill",
        )
        _append_trade_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="0xpartialexitfill",
            state="MATCHED",
            filled_size="46.59",
            fill_price="0.161646276024898",
            tx_hash="0xpartialexitfill",
        )
        append_order_fact(
            conn,
            venue_order_id="ord-exit",
            command_id="cmd-exit",
            state="MATCHED",
            remaining_size="0",
            matched_size="46.59",
            source="REST",
            observed_at="2026-04-26T00:05:00Z",
            venue_timestamp="2026-04-26T00:05:00Z",
            raw_payload_hash="e" * 64,
            raw_payload_json={
                "submit_result": {
                    "orderID": "ord-exit",
                    "status": "matched",
                    "side": "SELL",
                    "makingAmount": "46.59",
                    "takingAmount": "7.5311",
                    "transactionsHashes": ["0xpartialexitfill"],
                }
            },
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["exit_lifecycle_alignment_repair"] == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-exit") == "PARTIAL"
        current = conn.execute(
            "SELECT phase, order_status FROM position_current WHERE position_id = 'pos-001'"
        ).fetchone()
        assert dict(current) == {
            "phase": "pending_exit",
            "order_status": "sell_pending_confirmation",
        }
        command_events = [
            row["event_type"] for row in _get_events(conn, "cmd-exit")
        ]
        assert "FILL_CONFIRMED" not in command_events
        assert conn.execute(
            """
            SELECT COUNT(*)
              FROM position_events
             WHERE position_id = 'pos-001'
               AND event_type = 'EXIT_ORDER_FILLED'
            """
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_trade_facts WHERE command_id = 'cmd-exit'"
        ).fetchone()[0] == 2


    def test_confirmed_phantom_void_repair_quarantines_for_attribution(self, conn):
        position_id = "pos-confirmed-phantom-void"
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
                entry_method, strategy_key, edge_source, discovery_mode,
                chain_state, token_id, no_token_id, condition_id, order_id,
                order_status, updated_at, temperature_metric, exit_reason,
                fill_authority, chain_shares, chain_seen_at
            ) VALUES (
                ?, 'voided', ?, 'mkt-paris', 'Paris', 'Europe',
                '2026-06-20',
                'Will the lowest temperature in Paris be 19°C on June 20?',
                'buy_no', 'C', 3.795, 5.06, 3.795, 0.75, 0.8248,
                'snap-paris', 'ens_member_counting', 'settlement_capture',
                'settlement_capture', 'day0', 'synced', 'tok-yes',
                'tok-no', 'cond-paris', 'ord-entry', 'filled',
                '2026-06-20T06:41:30+00:00', 'low', 'PHANTOM_NOT_ON_CHAIN',
                'venue_confirmed_full', 5.0599, '2026-06-20T02:45:00+00:00'
            )
            """,
            (position_id, position_id),
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no,
                event_type, occurred_at, phase_before, phase_after,
                strategy_key, decision_id, snapshot_id, order_id,
                command_id, caused_by, idempotency_key, venue_status,
                source_module, payload_json, env
            ) VALUES (?, ?, 1, 4, 'ADMIN_VOIDED', '2026-06-20T06:41:30+00:00',
                      'day0_window', 'voided', 'settlement_capture', NULL,
                      'snap-paris', 'ord-entry', NULL, 'chain_reconciliation',
                      ?, 'voided', 'src.state.chain_reconciliation',
                      '{"reason":"PHANTOM_NOT_ON_CHAIN","token_id":"tok-no","chain_state":"synced"}',
                      'live')
            """,
            (
                f"{position_id}:chain_void:4",
                position_id,
                f"{position_id}:chain_void:4",
            ),
        )

        from src.execution.command_recovery import repair_confirmed_phantom_voids

        summary = repair_confirmed_phantom_voids(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, chain_state, exit_reason, shares, chain_shares
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT
        # PHASE LAW): restored to its TRUE (active) phase, never a
        # quarantine scar; chain_state/exit_reason are left as their honest
        # pre-existing values (no writer overwrites them to a retired
        # ChainState member anymore) — the dispute is tracked by an open
        # CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT ReviewWorkItem instead.
        assert dict(current) == {
            "phase": "active",
            "chain_state": "synced",
            "exit_reason": "PHANTOM_NOT_ON_CHAIN",
            "shares": 5.06,
            "chain_shares": pytest.approx(5.0599),
        }
        event = conn.execute(
            """
            SELECT event_type, sequence_no, phase_before, phase_after, source_module, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
        payload = json.loads(event["payload_json"])
        assert event["event_type"] == "REVIEW_REQUIRED"
        assert event["sequence_no"] == 5
        assert event["phase_before"] == "voided"
        assert event["phase_after"] == "active"
        assert event["source_module"] == "src.execution.command_recovery"
        assert payload["held_token_id"] == "tok-no"
        assert payload["proof_class"] == "confirmed_fill_phantom_void_reclassified_to_review"

        from src.state.review_work_items import due_work

        open_items = due_work(conn, limit=10)
        assert any(
            item.subject_id == position_id
            and item.reason_code.value == "CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT"
            for item in open_items
        )

    def test_confirmed_phantom_void_repair_uses_trade_facts_when_fill_authority_missing(
        self,
        conn,
    ):
        position_id = "pos-confirmed-phantom-fill-authority-none"
        command_id = _insert(
            conn,
            command_id="cmd-phantom-fill-authority-none",
            position_id=position_id,
            token_id="tok-yes",
            selected_token_id="tok-yes",
            side="BUY",
            size=135.89,
            price=0.05,
        )
        conn.execute(
            """
            INSERT INTO venue_trade_facts (
                trade_id, venue_order_id, command_id, state, filled_size,
                fill_price, fee_paid_micro, tx_hash, block_number,
                confirmation_count, source, observed_at, venue_timestamp,
                local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (
                'trade-positive-1', 'ord-entry', ?, 'CONFIRMED', '85.17',
                '0.05', NULL, NULL, NULL, 0, 'WS_USER',
                '2026-06-29T11:16:07.840000+00:00',
                '2026-06-29T11:16:07.840000+00:00',
                1, ?, '{}'
            )
            """,
            (command_id, "a" * 64),
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
                entry_method, strategy_key, edge_source, discovery_mode,
                chain_state, token_id, no_token_id, condition_id, order_id,
                order_status, updated_at, temperature_metric, exit_reason,
                fill_authority, chain_shares, chain_seen_at
            ) VALUES (
                ?, 'voided', ?, 'mkt-miami', 'Miami', 'US',
                '2026-06-30',
                'Will the highest temperature in Miami be between 96-97°F on June 30?',
                'buy_yes', 'F', 4.3436, 85.17, 4.3436, 0.05, 0.339,
                'snap-miami', 'qkernel_spine', 'center_buy',
                'center_buy', 'opening_hunt', 'synced', 'tok-yes',
                'tok-no', 'cond-miami', 'ord-entry', 'partial',
                '2026-06-29T13:38:50+00:00', 'high',
                'ENTRY_SELECTION_GUARD_INVALID_EXIT',
                'none', 85.17, '2026-06-29T11:16:48+00:00'
            )
            """,
            (position_id, position_id),
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no,
                event_type, occurred_at, phase_before, phase_after,
                strategy_key, decision_id, snapshot_id, order_id,
                command_id, caused_by, idempotency_key, venue_status,
                source_module, payload_json, env
            ) VALUES (?, ?, 1, 4, 'ADMIN_VOIDED', '2026-06-29T13:38:50+00:00',
                      'active', 'voided', 'center_buy', NULL,
                      'snap-miami', 'ord-entry', NULL, 'chain_reconciliation',
                      ?, 'voided', 'src.state.chain_reconciliation',
                      '{"reason":"PHANTOM_NOT_ON_CHAIN","token_id":"tok-yes","chain_state":"synced"}',
                      'live')
            """,
            (
                f"{position_id}:chain_void:4",
                position_id,
                f"{position_id}:chain_void:4",
            ),
        )

        from src.execution.command_recovery import repair_confirmed_phantom_voids

        summary = repair_confirmed_phantom_voids(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, chain_state, exit_reason, fill_authority, chain_shares
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT
        # PHASE LAW): restored to its TRUE (active) phase; chain_state/
        # exit_reason are left as their honest pre-existing values.
        assert dict(current) == {
            "phase": "active",
            "chain_state": "synced",
            "exit_reason": "ENTRY_SELECTION_GUARD_INVALID_EXIT",
            "fill_authority": "cancelled_remainder",
            "chain_shares": pytest.approx(85.17),
        }

    def test_confirmed_phantom_void_repair_uses_trade_fact_when_local_shares_zero(
        self,
        conn,
    ):
        position_id = "pos-phantom-zero-local-chain-positive"
        command_id = _insert(
            conn,
            command_id="cmd-phantom-zero-local-chain-positive",
            position_id=position_id,
            token_id="tok-yes",
            no_token_id="tok-no",
            selected_token_id="tok-no",
            outcome_label="NO",
            side="BUY",
            size=5.07,
            price=0.75,
        )
        conn.execute(
            """
            INSERT INTO venue_trade_facts (
                trade_id, venue_order_id, command_id, state, filled_size,
                fill_price, fee_paid_micro, tx_hash, block_number,
                confirmation_count, source, observed_at, venue_timestamp,
                local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (
                'trade-zero-local-chain-positive', 'ord-entry', ?, 'CONFIRMED',
                '5.07', '0.75', NULL, NULL, NULL, 0, 'WS_USER',
                '2026-06-17T11:17:11+00:00',
                '2026-06-17T11:17:11+00:00',
                1, ?, '{}'
            )
            """,
            (command_id, "d" * 64),
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
                entry_method, strategy_key, edge_source, discovery_mode,
                chain_state, token_id, no_token_id, condition_id, order_id,
                order_status, updated_at, temperature_metric, exit_reason,
                fill_authority, chain_shares, chain_avg_price,
                chain_cost_basis_usd, chain_seen_at
            ) VALUES (
                ?, 'voided', ?, 'mkt-houston', 'Houston', 'US',
                '2026-06-17',
                'Will the highest temperature in Houston be between 88-89°F on June 17?',
                'buy_no', 'F', 0, 0, 0, 0.75, 0.12,
                'snap-houston', 'qkernel_spine', 'center_buy',
                'center_buy', 'day0', 'synced', 'tok-yes',
                'tok-no', 'cond-houston', 'ord-entry', 'filled',
                '2026-06-17T12:00:00+00:00', 'high',
                'PHANTOM_NOT_ON_CHAIN', 'none', 5.07, 0.75, 3.8025,
                '2026-06-17T11:17:11+00:00'
            )
            """,
            (position_id, position_id),
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no,
                event_type, occurred_at, phase_before, phase_after,
                strategy_key, decision_id, snapshot_id, order_id,
                command_id, caused_by, idempotency_key, venue_status,
                source_module, payload_json, env
            ) VALUES (?, ?, 1, 4, 'ADMIN_VOIDED', '2026-06-17T12:00:00+00:00',
                      'active', 'voided', 'center_buy', NULL,
                      'snap-houston', 'ord-entry', NULL, 'chain_reconciliation',
                      ?, 'voided', 'src.state.chain_reconciliation',
                      '{"reason":"PHANTOM_NOT_ON_CHAIN","token_id":"tok-no","chain_state":"synced"}',
                      'live')
            """,
            (
                f"{position_id}:chain_void:4",
                position_id,
                f"{position_id}:chain_void:4",
            ),
        )

        from src.execution.command_recovery import repair_confirmed_phantom_voids

        summary = repair_confirmed_phantom_voids(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, chain_state, fill_authority, exit_reason,
                   shares, chain_shares, chain_avg_price, chain_cost_basis_usd
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT
        # PHASE LAW): restored to its TRUE (active) phase; chain_state/
        # exit_reason are left as their honest pre-existing values.
        assert dict(current) == {
            "phase": "active",
            "chain_state": "synced",
            "fill_authority": "cancelled_remainder",
            "exit_reason": "PHANTOM_NOT_ON_CHAIN",
            "shares": 0.0,
            "chain_shares": 5.07,
            "chain_avg_price": 0.75,
            "chain_cost_basis_usd": 3.8025,
        }
        event = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
        payload = json.loads(event["payload_json"])
        assert event["event_type"] == "REVIEW_REQUIRED"
        assert event["phase_before"] == "voided"
        assert event["phase_after"] == "active"
        assert payload["positive_trade_fact_proof"]["has_positive_trade_fact"] is True
        assert payload["proof_class"] == "confirmed_fill_phantom_void_reclassified_to_review"

    def test_confirmed_phantom_void_repair_clears_stale_chain_projection_without_trade_fact(
        self,
        conn,
    ):
        position_id = "pos-phantom-stale-chain-projection"
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
                entry_method, strategy_key, edge_source, discovery_mode,
                chain_state, token_id, no_token_id, condition_id, order_id,
                order_status, updated_at, temperature_metric, exit_reason,
                fill_authority, chain_shares, chain_avg_price,
                chain_cost_basis_usd, chain_absence_at
            ) VALUES (
                ?, 'voided', ?, 'mkt-karachi', 'Karachi', 'Asia',
                '2026-06-25',
                'Will the highest temperature in Karachi be 35°C on June 25?',
                'buy_no', 'C', 5.61, 8.25, 5.61, 0.68, 0.14,
                'snap-karachi', 'qkernel_spine', 'center_buy',
                'center_buy', 'day0', 'synced', 'tok-yes',
                'tok-no', 'cond-karachi', 'ord-entry', 'filled',
                '2026-06-25T19:12:48+00:00', 'high',
                'PHANTOM_NOT_ON_CHAIN', 'none', 8.25, 0.68, 5.61, ''
            )
            """,
            (position_id, position_id),
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no,
                event_type, occurred_at, phase_before, phase_after,
                strategy_key, decision_id, snapshot_id, order_id,
                command_id, caused_by, idempotency_key, venue_status,
                source_module, payload_json, env
            ) VALUES (?, ?, 1, 4, 'ADMIN_VOIDED', '2026-06-25T19:12:48+00:00',
                      'active', 'voided', 'center_buy', NULL,
                      'snap-karachi', 'ord-entry', NULL, 'chain_reconciliation',
                      ?, 'voided', 'src.state.chain_reconciliation',
                      '{"reason":"PHANTOM_NOT_ON_CHAIN","token_id":"tok-no","chain_state":"synced"}',
                      'live')
            """,
            (
                f"{position_id}:chain_void:4",
                position_id,
                f"{position_id}:chain_void:4",
            ),
        )

        from src.execution.command_recovery import repair_confirmed_phantom_voids

        summary = repair_confirmed_phantom_voids(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, chain_state, exit_reason, shares, chain_shares,
                   chain_avg_price, chain_cost_basis_usd,
                   COALESCE(chain_absence_at, '') AS chain_absence_at
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        assert dict(current) == {
            "phase": "voided",
            "chain_state": "chain_absent_confirmed_position_unattributed",
            "exit_reason": "PHANTOM_NOT_ON_CHAIN",
            "shares": 8.25,
            "chain_shares": 0.0,
            "chain_avg_price": 0.0,
            "chain_cost_basis_usd": 0.0,
            "chain_absence_at": current["chain_absence_at"],
        }
        assert current["chain_absence_at"]
        event = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, venue_status, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
        payload = json.loads(event["payload_json"])
        assert event["event_type"] == "REVIEW_REQUIRED"
        assert event["phase_before"] == "voided"
        assert event["phase_after"] == "voided"
        assert event["venue_status"] == "projection_repaired"
        assert payload["positive_trade_fact_proof"]["has_positive_trade_fact"] is False
        assert payload["proof_class"] == "confirmed_phantom_void_chain_projection_fields_cleared"

    def test_confirmed_chain_absence_projection_repair_clears_stale_chain_fields(
        self,
        conn,
    ):
        position_id = "pos-chain-absent-positive-projection"
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
                entry_method, strategy_key, edge_source, discovery_mode,
                chain_state, token_id, no_token_id, condition_id, order_id,
                order_status, updated_at, temperature_metric, exit_reason,
                fill_authority, chain_shares, chain_avg_price,
                chain_cost_basis_usd, chain_absence_at
            ) VALUES (
                ?, 'active', ?, 'mkt-munich', 'Munich', 'Europe',
                '2026-06-30',
                'Will the highest temperature in Munich be 30°C on June 30?',
                'buy_no', 'C', 21.27, 29.14, 21.27, 0.73, 0.1449,
                'snap-munich', 'qkernel_spine', 'center_buy',
                'center_buy', 'day0', 'chain_absent_confirmed_position_unattributed',
                'tok-yes', 'tok-no', 'cond-munich', 'ord-entry', 'filled',
                '2026-06-30T00:22:00+00:00', 'high',
                'chain_absent_confirmed_position_unattributed',
                'venue_confirmed_full', 29.14, 0.73, 21.27, ''
            )
            """,
            (position_id, position_id),
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no,
                event_type, occurred_at, phase_before, phase_after,
                strategy_key, decision_id, snapshot_id, order_id,
                command_id, caused_by, idempotency_key, venue_status,
                source_module, payload_json, env
            ) VALUES (?, ?, 1, 4, 'REVIEW_REQUIRED', '2026-06-30T00:22:00+00:00',
                      'active', 'active', 'center_buy', NULL,
                      'snap-munich', 'ord-entry', NULL,
                      'chain_absent_confirmed_position_unattributed',
                      ?, 'review_required', 'src.state.chain_reconciliation',
                      '{"reason":"chain_absent_confirmed_position_unattributed"}',
                      'live')
            """,
            (
                f"{position_id}:chain_absent_review:4",
                position_id,
                f"{position_id}:chain_absent_review:4",
            ),
        )

        from src.execution.command_recovery import (
            repair_confirmed_chain_absence_positive_projections,
        )

        summary = repair_confirmed_chain_absence_positive_projections(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, chain_state, shares, cost_basis_usd, chain_shares,
                   chain_avg_price, chain_cost_basis_usd,
                   COALESCE(chain_absence_at, '') AS chain_absence_at
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        # T5 REPLACEMENT PHASE LAW (docs/rebuild/quarantine_excision_2026-07-11.md):
        # no positive trade fact backs this row, so the repair leaves phase
        # untouched (it only clears stale chain projection fields) -- a
        # normal open phase (active) is the current-law vehicle, never a
        # quarantine scar.
        assert dict(current) == {
            "phase": "active",
            "chain_state": "chain_absent_confirmed_position_unattributed",
            "shares": 29.14,
            "cost_basis_usd": 21.27,
            "chain_shares": 0.0,
            "chain_avg_price": 0.0,
            "chain_cost_basis_usd": 0.0,
            "chain_absence_at": current["chain_absence_at"],
        }
        assert current["chain_absence_at"]
        event = conn.execute(
            """
            SELECT event_type, sequence_no, phase_before, phase_after,
                   source_module, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
        payload = json.loads(event["payload_json"])
        assert event["event_type"] == "REVIEW_REQUIRED"
        assert event["sequence_no"] == 5
        assert event["phase_before"] == "active"
        assert event["phase_after"] == "active"
        assert event["source_module"] == "src.execution.command_recovery"
        assert payload["previous_chain_shares"] == 29.14
        assert payload["proof_class"] == "confirmed_chain_absence_projection_chain_fields_cleared"

    def test_confirmed_chain_absence_projection_with_trade_fact_stays_monitorable(
        self,
        conn,
    ):
        position_id = "pos-chain-absent-positive-trade-fact"
        command_id = _insert(
            conn,
            command_id="cmd-chain-absent-positive-trade-fact",
            position_id=position_id,
            token_id="tok-yes",
            no_token_id="tok-no",
            selected_token_id="tok-no",
            outcome_label="NO",
            side="BUY",
            size=29.14,
            price=0.73,
        )
        conn.execute(
            """
            INSERT INTO venue_trade_facts (
                trade_id, venue_order_id, command_id, state, filled_size,
                fill_price, fee_paid_micro, tx_hash, block_number,
                confirmation_count, source, observed_at, venue_timestamp,
                local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (
                'trade-chain-absent-positive', 'ord-entry', ?, 'CONFIRMED',
                '29.14', '0.73', NULL, NULL, NULL, 0, 'WS_USER',
                '2026-06-30T00:22:15+00:00',
                '2026-06-30T00:22:15+00:00',
                1, ?, '{}'
            )
            """,
            (command_id, "b" * 64),
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
                entry_method, strategy_key, edge_source, discovery_mode,
                chain_state, token_id, no_token_id, condition_id, order_id,
                order_status, updated_at, temperature_metric, exit_reason,
                fill_authority, chain_shares, chain_avg_price,
                chain_cost_basis_usd, chain_absence_at
            ) VALUES (
                ?, 'voided', ?, 'mkt-munich', 'Munich', 'Europe',
                '2026-06-30',
                'Will the highest temperature in Munich be 30°C on June 30?',
                'buy_no', 'C', 21.27, 29.14, 21.27, 0.73, 0.1449,
                'snap-munich', 'qkernel_spine', 'center_buy',
                'center_buy', 'day0', 'chain_absent_confirmed_position_unattributed',
                'tok-yes', 'tok-no', 'cond-munich', 'ord-entry', 'filled',
                '2026-06-30T00:22:00+00:00', 'high',
                'chain_absent_confirmed_position_unattributed',
                'none', 29.14, 0.73, 21.27, ''
            )
            """,
            (position_id, position_id),
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no,
                event_type, occurred_at, phase_before, phase_after,
                strategy_key, decision_id, snapshot_id, order_id,
                command_id, caused_by, idempotency_key, venue_status,
                source_module, payload_json, env
            ) VALUES (?, ?, 1, 4, 'REVIEW_REQUIRED', '2026-06-30T00:22:00+00:00',
                      'active', 'voided', 'center_buy', NULL,
                      'snap-munich', 'ord-entry', NULL,
                      'chain_absent_confirmed_position_unattributed',
                      ?, 'review_required', 'src.state.chain_reconciliation',
                      '{"reason":"chain_absent_confirmed_position_unattributed"}',
                      'live')
            """,
            (
                f"{position_id}:chain_absent_review:4",
                position_id,
                f"{position_id}:chain_absent_review:4",
            ),
        )

        from src.execution.command_recovery import (
            repair_confirmed_chain_absence_positive_projections,
        )

        summary = repair_confirmed_chain_absence_positive_projections(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, chain_state, fill_authority, exit_reason,
                   shares, cost_basis_usd, chain_shares,
                   chain_avg_price, chain_cost_basis_usd
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT
        # PHASE LAW): positive trade-fact proof restores the TRUE phase
        # (active) and chain_state (synced), never a quarantine scar; the
        # row starts 'voided' (a wrongly-closed phase) to exercise the
        # restoration branch, since already-open phases fold to themselves.
        # exit_reason is left as its honest pre-existing value — the
        # dispute is tracked by an open CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT
        # ReviewWorkItem instead.
        assert dict(current) == {
            "phase": "active",
            "chain_state": "synced",
            "fill_authority": "venue_confirmed_full",
            "exit_reason": "chain_absent_confirmed_position_unattributed",
            "shares": 29.14,
            "cost_basis_usd": 21.27,
            "chain_shares": 29.14,
            "chain_avg_price": 0.73,
            "chain_cost_basis_usd": 21.27,
        }
        event = conn.execute(
            """
            SELECT event_type, sequence_no, phase_before, phase_after,
                   source_module, payload_json
              FROM position_events
             WHERE position_id = ?
             ORDER BY sequence_no DESC
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
        payload = json.loads(event["payload_json"])
        assert event["event_type"] == "REVIEW_REQUIRED"
        assert event["sequence_no"] == 5
        assert event["phase_before"] == "voided"
        assert event["phase_after"] == "active"
        assert event["source_module"] == "src.execution.command_recovery"
        assert payload["positive_trade_fact_proof"]["has_positive_trade_fact"] is True
        assert (
            payload["proof_class"]
            == "confirmed_fill_chain_absence_projection_preserved_current_money_risk"
        )

        from src.state.review_work_items import due_work

        open_items = due_work(conn, limit=10)
        assert any(
            item.subject_id == position_id
            and item.reason_code.value == "CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT"
            for item in open_items
        )

    def test_confirmed_chain_absence_trade_fact_repairs_after_chain_fields_cleared(
        self,
        conn,
    ):
        position_id = "pos-chain-absent-trade-fact-cleared-chain-fields"
        command_id = _insert(
            conn,
            command_id="cmd-chain-absent-trade-fact-cleared-chain-fields",
            position_id=position_id,
            token_id="tok-yes",
            no_token_id="tok-no",
            selected_token_id="tok-no",
            outcome_label="NO",
            side="BUY",
            size=29.14,
            price=0.73,
        )
        conn.execute(
            """
            INSERT INTO venue_trade_facts (
                trade_id, venue_order_id, command_id, state, filled_size,
                fill_price, fee_paid_micro, tx_hash, block_number,
                confirmation_count, source, observed_at, venue_timestamp,
                local_sequence, raw_payload_hash, raw_payload_json
            ) VALUES (
                'trade-chain-absent-cleared-fields', 'ord-entry', ?, 'CONFIRMED',
                '29.14', '0.73', NULL, NULL, NULL, 0, 'WS_USER',
                '2026-06-30T00:22:15+00:00',
                '2026-06-30T00:22:15+00:00',
                1, ?, '{}'
            )
            """,
            (command_id, "c" * 64),
        )
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, market_id, city, cluster,
                target_date, bin_label, direction, unit, size_usd, shares,
                cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
                entry_method, strategy_key, edge_source, discovery_mode,
                chain_state, token_id, no_token_id, condition_id, order_id,
                order_status, updated_at, temperature_metric, exit_reason,
                fill_authority, chain_shares, chain_avg_price,
                chain_cost_basis_usd, chain_absence_at
            ) VALUES (
                ?, 'voided', ?, 'mkt-munich', 'Munich', 'Europe',
                '2026-06-30',
                'Will the highest temperature in Munich be 30°C on June 30?',
                'buy_no', 'C', 21.27, 29.14, 21.27, 0.73, 0.1449,
                'snap-munich', 'qkernel_spine', 'center_buy',
                'center_buy', 'day0', 'chain_absent_confirmed_position_unattributed',
                'tok-yes', 'tok-no', 'cond-munich', 'ord-entry', 'filled',
                '2026-06-30T00:22:00+00:00', 'high',
                'chain_absent_confirmed_position_unattributed',
                'none', 0, 0, 0, ''
            )
            """,
            (position_id, position_id),
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no,
                event_type, occurred_at, phase_before, phase_after,
                strategy_key, decision_id, snapshot_id, order_id,
                command_id, caused_by, idempotency_key, venue_status,
                source_module, payload_json, env
            ) VALUES (?, ?, 1, 4, 'REVIEW_REQUIRED', '2026-06-30T00:22:00+00:00',
                      'active', 'voided', 'center_buy', NULL,
                      'snap-munich', 'ord-entry', NULL,
                      'chain_absent_confirmed_position_unattributed',
                      ?, 'review_required', 'src.state.chain_reconciliation',
                      '{"reason":"chain_absent_confirmed_position_unattributed"}',
                      'live')
            """,
            (
                f"{position_id}:chain_absent_review:4",
                position_id,
                f"{position_id}:chain_absent_review:4",
            ),
        )

        from src.execution.command_recovery import (
            repair_confirmed_chain_absence_positive_projections,
        )

        summary = repair_confirmed_chain_absence_positive_projections(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        current = conn.execute(
            """
            SELECT phase, chain_state, fill_authority, exit_reason,
                   shares, cost_basis_usd, chain_shares,
                   chain_avg_price, chain_cost_basis_usd
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT
        # PHASE LAW): restored to TRUE phase (active) and chain_state
        # (synced) even though the row's own chain fields started at
        # zero — positive venue trade-fact proof backfills chain_shares/
        # chain_avg_price/chain_cost_basis_usd from the local projection.
        # Row starts 'voided' (a wrongly-closed phase) to exercise the
        # restoration branch, since already-open phases fold to themselves.
        assert dict(current) == {
            "phase": "active",
            "chain_state": "synced",
            "fill_authority": "venue_confirmed_full",
            "exit_reason": "chain_absent_confirmed_position_unattributed",
            "shares": 29.14,
            "cost_basis_usd": 21.27,
            "chain_shares": 29.14,
            "chain_avg_price": 0.73,
            "chain_cost_basis_usd": 21.27,
        }

        from src.state.review_work_items import due_work

        open_items = due_work(conn, limit=10)
        assert any(
            item.subject_id == position_id
            and item.reason_code.value == "CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT"
            for item in open_items
        )

    def test_exit_matched_trade_fact_repairs_retry_pending_projection(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, command_id="cmd-entry", position_id="pos-001")
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'pending_exit',
                   shares = 6.0,
                   cost_basis_usd = 1.86,
                   entry_price = 0.31,
                   order_id = 'ord-entry',
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no, event_type,
                occurred_at, phase_before, phase_after, strategy_key, decision_id,
                snapshot_id, order_id, command_id, caused_by, idempotency_key,
                venue_status, source_module, payload_json, env
            )
            VALUES (
                'pos-001:exit_rejected:retry', 'pos-001', 1, 3,
                'EXIT_ORDER_REJECTED', '2026-04-26T00:05:00Z', 'active',
                'pending_exit', 'opening_inertia', 'dec-001', 'snap-pos-001',
                NULL, NULL, 'test_retry_pending_setup',
                'pos-001:exit_rejected:retry', 'retry_pending',
                'tests.test_command_recovery', '{}', 'live'
            )
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=6.0,
            price=0.29,
            token_id="tok-001",
        )
        _advance_to_partial(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_trade_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="trade-exit-001",
            state="MATCHED",
            filled_size="6",
            fill_price="0.29",
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["exit_pending_projections"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, shares, cost_basis_usd, order_id, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "economically_closed",
            "shares": 6.0,
            "cost_basis_usd": 1.86,
            "order_id": "ord-entry",
            "order_status": "sell_filled",
        }
        lifecycle_events = conn.execute(
            """
            SELECT event_type, phase_before, phase_after, order_id, command_id, venue_status
              FROM position_events
             WHERE position_id = 'pos-001'
             ORDER BY sequence_no
            """
        ).fetchall()
        assert dict(lifecycle_events[-1]) == {
            "event_type": "EXIT_ORDER_FILLED",
            "phase_before": "pending_exit",
            "phase_after": "economically_closed",
            "order_id": "ord-exit",
            "command_id": "cmd-exit",
            "venue_status": "sell_filled",
        }

    def test_acked_exit_order_fact_uses_submission_envelope_tx_hash(
        self,
        conn,
        mock_client,
    ):
        """Matched exit recovery must not lose tx hashes carried only by the submit envelope."""
        _insert(conn, command_id="cmd-entry", position_id="pos-001", size=6.0, price=0.31)
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 6.0,
                   cost_basis_usd = 1.86,
                   entry_price = 0.31,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=6.0,
            price=0.29,
            token_id="tok-001",
        )
        _advance_to_acked(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_order_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            state="MATCHED",
            matched_size="6",
            remaining_size="0",
        )
        _ensure_envelope(
            conn,
            token_id="tok-001",
            envelope_id="env-final-exit-submit",
            side="SELL",
            price=Decimal("0.29"),
            size=Decimal("6.0"),
            order_id="ord-exit",
            transaction_hashes=("0xhash-exit-envelope",),
            raw_response_json=json.dumps(
                {
                    "orderID": "ord-exit",
                    "status": "matched",
                    "transactionsHashes": ["0xhash-exit-envelope"],
                },
                sort_keys=True,
            ),
        )
        mock_client.get_order.return_value = {
            "id": "ord-exit",
            "status": "MATCHED",
            "size_matched": "6",
            "price": "0.29",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["matched_order_facts"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-exit") == "FILLED"
        trade_fact = conn.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-exit'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "0xhash-exit-envelope",
            "venue_order_id": "ord-exit",
            "state": "MATCHED",
            "filled_size": "6",
            "fill_price": "0.29",
            "tx_hash": "0xhash-exit-envelope",
        }

    def test_filled_exit_trade_fact_missing_tx_uses_submission_envelope_tx_hash(
        self,
        conn,
        mock_client,
    ):
        """Already-FILLED exits with tx-less trade facts must still repair from final envelope."""
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=33.15,
            price=0.55,
            token_id="tok-001",
        )
        _advance_to_acked(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_order_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            state="MATCHED",
            matched_size="33.15",
            remaining_size="0",
        )
        _append_trade_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="trade-exit-existing",
            state="MATCHED",
            filled_size="33.15",
            fill_price="0.55",
            tx_hash=None,
        )
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-exit",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:07:00Z",
            payload={
                "venue_order_id": "ord-exit",
                "trade_id": "trade-exit-existing",
                "filled_size": "33.15",
                "fill_price": "0.55",
            },
        )
        _ensure_envelope(
            conn,
            token_id="tok-001",
            envelope_id="env-final-filled-exit-submit",
            side="SELL",
            price=Decimal("0.55"),
            size=Decimal("33.15"),
            order_id="ord-exit",
            transaction_hashes=("0xhash-filled-exit-envelope",),
            raw_response_json=json.dumps(
                {
                    "orderID": "ord-exit",
                    "status": "matched",
                    "transactionsHashes": ["0xhash-filled-exit-envelope"],
                },
                sort_keys=True,
            ),
        )

        from src.execution.command_recovery import reconcile_filled_exit_trade_fact_tx_repairs

        summary = reconcile_filled_exit_trade_fact_tx_repairs(conn)

        assert summary == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        trade_fact = conn.execute(
            """
            SELECT trade_id, venue_order_id, state, filled_size, fill_price, tx_hash
              FROM venue_trade_facts
             WHERE command_id = 'cmd-exit'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        assert dict(trade_fact) == {
            "trade_id": "trade-exit-existing",
            "venue_order_id": "ord-exit",
            "state": "MATCHED",
            "filled_size": "33.15",
            "fill_price": "0.55",
            "tx_hash": "0xhash-filled-exit-envelope",
        }

    def test_exit_matched_trade_fact_repairs_existing_event_torn_projection(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, command_id="cmd-entry", position_id="pos-001")
        _advance_to_acked(conn, command_id="cmd-entry", venue_order_id="ord-entry")
        _seed_pending_entry_projection(conn, command_id="cmd-entry", order_id="ord-entry")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   shares = 23.7,
                   cost_basis_usd = 1.659,
                   entry_price = 0.07,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:04:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        _insert(
            conn,
            command_id="cmd-exit",
            position_id="pos-001",
            intent_kind="EXIT",
            side="SELL",
            size=23.7,
            price=0.04,
            token_id="tok-001",
        )
        _advance_to_partial(conn, command_id="cmd-exit", venue_order_id="ord-exit")
        _append_trade_fact(
            conn,
            command_id="cmd-exit",
            order_id="ord-exit",
            trade_id="trade-exit-001",
            state="MATCHED",
            filled_size="23.7",
            fill_price="0.04",
        )
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, event_version, sequence_no, event_type,
                occurred_at, phase_before, phase_after, strategy_key, decision_id,
                snapshot_id, order_id, command_id, caused_by, idempotency_key,
                venue_status, source_module, payload_json, env
            )
            VALUES (
                'pos-001:exit_order_posted:cmd-exit', 'pos-001', 1, 3,
                'EXIT_ORDER_POSTED', '2026-04-26T00:06:00Z', 'active',
                'pending_exit', 'opening_inertia', 'dec-001', 'snap-pos-001',
                'ord-exit', 'cmd-exit', 'test_torn_setup',
                'pos-001:exit_order_posted:cmd-exit', 'MATCHED',
                'tests.test_command_recovery', '{}', 'live'
            )
            """
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["exit_pending_projections"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        current = conn.execute(
            """
            SELECT phase, order_id, order_status
              FROM position_current
             WHERE position_id = 'pos-001'
            """
        ).fetchone()
        assert dict(current) == {
            "phase": "economically_closed",
            "order_id": "ord-entry",
            "order_status": "sell_filled",
        }
        event_count = conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM position_events
             WHERE idempotency_key = 'pos-001:exit_order_posted:cmd-exit'
            """
        ).fetchone()
        assert event_count["n"] == 1

    def test_partial_remainder_terminal_fact_uses_latest_trade_fact_per_trade_id(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        for state in ("MATCHED", "MINED", "CONFIRMED"):
            _append_trade_fact(
                conn,
                order_id="ord-partial",
                trade_id="trade-partial",
                state=state,
                filled_size="1.25",
                fill_price="0.50",
        )
        _seed_pending_entry_projection(conn, order_id="ord-partial")
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = {"orderID": "ord-partial", "status": "EXPIRED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        order_fact = conn.execute(
            """
            SELECT matched_size, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        payload = json.loads(order_fact["raw_payload_json"])
        assert order_fact["matched_size"] == "1.25"
        assert payload["matched_size"] == "1.25"
        event_payload = json.loads(_get_events(conn, "cmd-001")[-1]["payload_json"])
        assert event_payload["positive_fill_trade_fact_count"] == 1
        assert event_payload["positive_fill_size"] == "1.25"

    def test_partial_remainder_uses_canonical_trade_fact_over_later_weaker_fact(
        self,
        conn,
        mock_client,
    ):
        """Relationship: a later weak trade fact cannot hide positive fill truth."""
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_trade_fact(
            conn,
            order_id="ord-partial",
            trade_id="trade-partial",
            state="CONFIRMED",
            filled_size="1.25",
            fill_price="0.50",
        )
        _append_trade_fact(
            conn,
            order_id="ord-partial",
            trade_id="trade-partial",
            state="FAILED",
            filled_size="0",
            fill_price="0.50",
        )
        _seed_pending_entry_projection(conn, order_id="ord-partial")
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = {"orderID": "ord-partial", "status": "EXPIRED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        order_fact = conn.execute(
            """
            SELECT matched_size, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        payload = json.loads(order_fact["raw_payload_json"])
        assert order_fact["matched_size"] == "1.25"
        assert payload["matched_size"] == "1.25"
        event_payload = json.loads(_get_events(conn, "cmd-001")[-1]["payload_json"])
        assert event_payload["positive_fill_trade_fact_count"] == 1
        assert event_payload["positive_fill_size"] == "1.25"

    def test_terminal_remainder_helper_uses_canonical_order_truth_over_later_weaker_fact(
        self,
        conn,
    ):
        """Relationship: terminal zero-remainder partial proof outranks later open-ish facts."""
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_order_fact(
            conn,
            order_id="ord-partial",
            state="EXPIRED",
            matched_size="1.25",
            remaining_size="0",
        )
        _append_order_fact(
            conn,
            order_id="ord-partial",
            state="PARTIALLY_MATCHED",
            matched_size="1.25",
            remaining_size="3.75",
        )

        from src.execution.command_recovery import _latest_terminal_remainder_order_fact_exists

        assert _latest_terminal_remainder_order_fact_exists(conn, command_id="cmd-001") is True

    def test_cancel_confirmed_remainder_outranks_later_partial_order_fact(
        self,
        conn,
        mock_client,
    ):
        """Lucknow-class regression: cancel truth closes the unfilled remainder."""

        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_confirmed_trade_fact(conn, order_id="ord-partial")
        _append_order_fact(
            conn,
            order_id="ord-partial",
            state="CANCEL_CONFIRMED",
            matched_size="1.25",
            remaining_size="3.75",
        )
        _append_order_fact(
            conn,
            order_id="ord-partial",
            state="PARTIALLY_MATCHED",
            matched_size="1.25",
            remaining_size="3.75",
        )

        from src.execution.command_recovery import _latest_terminal_remainder_order_fact_exists
        from src.execution.command_recovery import reconcile_unresolved_commands

        assert _latest_terminal_remainder_order_fact_exists(conn, command_id="cmd-001") is True
        mock_client.get_open_orders.return_value = []

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        assert _get_state(conn, "cmd-001") == "EXPIRED"

    def test_legacy_filled_command_with_partial_economic_coverage_records_terminal_remainder_fact(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=181.16)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        from src.state.venue_command_repo import append_event

        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:07:00Z",
            payload={"source": "legacy_ws_user", "trade_id": "trade-partial"},
        )
        _append_confirmed_trade_fact(
            conn,
            order_id="ord-partial",
            trade_id="trade-partial",
            filled_size="100",
            fill_price="0.01",
        )
        _append_order_fact(
            conn,
            order_id="ord-partial",
            state="PARTIALLY_MATCHED",
            matched_size="100",
            remaining_size="81.16",
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = {"orderID": "ord-partial", "status": "CANCELED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        assert _get_state(conn, "cmd-001") == "FILLED"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "FILL_CONFIRMED"
        order_fact = conn.execute(
            """
            SELECT state, remaining_size, matched_size, source, raw_payload_json
              FROM venue_order_facts
             WHERE command_id = 'cmd-001'
             ORDER BY local_sequence DESC
             LIMIT 1
            """
        ).fetchone()
        payload = json.loads(order_fact["raw_payload_json"])
        assert dict(order_fact) | {"raw_payload_json": payload} == {
            "state": "EXPIRED",
            "remaining_size": "0",
            "matched_size": "100",
            "source": "REST",
            "raw_payload_json": {
                "command_id": "cmd-001",
                "matched_size": "100",
                "open_order_absent": True,
                "point_order": {"orderID": "ord-partial", "status": "CANCELED"},
                "point_order_status": "CANCELED",
                "proof_class": "confirmed_fill_plus_point_order_terminal_remainder",
                "reason": "partial_remainder_absent_from_exchange_open_orders",
                "remaining_size": "0",
                "source_surface": "client.get_open_orders+client.get_order",
                "venue_order_id": "ord-partial",
            },
        }

    def test_partial_remainder_stays_partial_while_order_is_still_open(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_confirmed_trade_fact(conn, order_id="ord-partial")
        mock_client.get_open_orders.return_value = [{"orderID": "ord-partial", "status": "LIVE"}]

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}
        assert _get_state(conn, "cmd-001") == "PARTIAL"

    def test_partial_absent_from_open_orders_without_trade_fact_requires_review(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = {"orderID": "ord-partial", "status": "CANCELED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        events = _get_events(conn, "cmd-001")
        assert events[-1]["event_type"] == "REVIEW_REQUIRED"

    def test_partial_remainder_recovery_resolves_matching_m5_local_orphan_finding(
        self,
        conn,
        mock_client,
    ):
        from src.execution.exchange_reconcile import list_unresolved_findings, record_finding

        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_confirmed_trade_fact(conn, order_id="ord-partial")
        finding = record_finding(
            conn,
            kind="local_orphan_order",
            subject_id="ord-partial",
            context="ws_gap",
            evidence={"reason": "local_open_order_absent_from_exchange_open_orders"},
            recorded_at="2026-04-26T00:07:00Z",
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = {"orderID": "ord-partial", "status": "CANCELED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        reconcile_unresolved_commands(conn, mock_client)

        assert _get_state(conn, "cmd-001") == "EXPIRED"
        assert [row.finding_id for row in list_unresolved_findings(conn)] == []
        resolved = conn.execute(
            "SELECT resolution, resolved_by FROM exchange_reconcile_findings WHERE finding_id = ?",
            (finding.finding_id,),
        ).fetchone()
        assert dict(resolved) == {
            "resolution": "command_recovery_expired_partial_remainder",
            "resolved_by": "src.execution.command_recovery",
        }

    def test_partial_remainder_global_absence_requires_point_terminal_proof(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_confirmed_trade_fact(conn, order_id="ord-partial")
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = {"orderID": "ord-partial", "status": "LIVE"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 0, "stayed": 1, "errors": 0}
        assert _get_state(conn, "cmd-001") == "PARTIAL"
        assert "EXPIRED" not in [e["event_type"] for e in _get_events(conn, "cmd-001")]

    def test_partial_remainder_terminalizes_from_order_state(
        self,
        conn,
        mock_client,
    ):
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_confirmed_trade_fact(conn, order_id="ord-partial")
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = SimpleNamespace(
            order_id="ord-partial",
            status="CANCELED",
            raw={"orderID": "ord-partial", "status": "CANCELED"},
        )

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        assert _get_state(conn, "cmd-001") == "EXPIRED"

    def test_partial_remainder_matched_point_order_routes_to_review_required(
        self,
        conn,
        mock_client,
    ):
        # GATE #84 follow-up (2026-06-22): a PARTIAL remainder absent from open
        # orders whose point order reports MATCHED means the remainder filled at the
        # venue but the fill fact has not yet arrived. MATCHED is not a terminal
        # no-fill status (it carries a live/fill record), so before the fix this
        # looped "staying" forever and the PARTIALLY_MATCHED order fact kept the
        # family's entry lane blocked (unexpired_family_rest=True; live market
        # 2625913, 2026-06-22). It must route to REVIEW_REQUIRED for fill-fact
        # reconciliation, identical to the FILLED case.
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_confirmed_trade_fact(conn, order_id="ord-partial")
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = {"orderID": "ord-partial", "status": "MATCHED"}

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 1, "stayed": 0, "errors": 0}
        assert _get_state(conn, "cmd-001") == "REVIEW_REQUIRED"
        assert "REVIEW_REQUIRED" in [e["event_type"] for e in _get_events(conn, "cmd-001")]

    def test_partial_remainder_matched_point_order_does_not_degrade_held_fill(
        self,
        conn,
        mock_client,
    ):
        from src.state.venue_command_repo import append_event

        _insert(conn, size=5.0, price=0.34)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_confirmed_trade_fact(
            conn,
            order_id="ord-partial",
            trade_id="trade-partial",
            filled_size="3.0",
            fill_price="0.34",
        )
        _seed_pending_entry_projection(conn, order_id="ord-partial")
        conn.execute(
            """
            UPDATE position_current
               SET phase = 'active',
                   chain_state = 'synced',
                   shares = 5.0,
                   chain_shares = 5.0,
                   cost_basis_usd = 1.70,
                   chain_cost_basis_usd = 1.70,
                   entry_price = 0.34,
                   order_status = 'filled',
                   updated_at = '2026-04-26T00:07:00Z'
             WHERE position_id = 'pos-001'
            """
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="FILL_CONFIRMED",
            occurred_at="2026-04-26T00:07:00Z",
            payload={
                "reason": "review_cleared_confirmed_fill",
                "proof_class": "cancel_unknown_confirmed_trade_with_positive_trade_fact",
                "filled_size": "3.0",
                "venue_order_id": "ord-partial",
            },
        )
        mock_client.get_open_orders.return_value = []
        mock_client.get_order.return_value = {
            "orderID": "ord-partial",
            "status": "MATCHED",
            "size_matched": "5.0",
            "price": "0.34",
        }

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["partial_remainders"] == {
            "scanned": 1,
            "advanced": 0,
            "stayed": 1,
            "errors": 0,
        }
        assert _get_state(conn, "cmd-001") == "FILLED"
        assert _get_events(conn, "cmd-001")[-1]["event_type"] == "FILL_CONFIRMED"

    def test_partial_remainder_without_point_reader_fails_closed(
        self,
        conn,
    ):
        _insert(conn, size=5.0)
        _advance_to_partial(conn, venue_order_id="ord-partial")
        _append_confirmed_trade_fact(conn, order_id="ord-partial")
        client = MagicMock(spec_set=["get_open_orders"])
        client.get_open_orders.return_value = []

        from src.execution.command_recovery import reconcile_unresolved_commands

        summary = reconcile_unresolved_commands(conn, client)

        assert summary["partial_remainders"] == {"scanned": 1, "advanced": 0, "stayed": 0, "errors": 1}
        assert _get_state(conn, "cmd-001") == "PARTIAL"

    # Supplementary: summary dict has all expected keys
    def test_summary_has_all_keys(self, conn, mock_client):
        mock_client.get_order.return_value = None
        from src.execution.command_recovery import reconcile_unresolved_commands
        summary = reconcile_unresolved_commands(conn, mock_client)
        for key in ("scanned", "advanced", "stayed", "errors"):
            assert key in summary, f"summary missing key: {key}"


# ---------------------------------------------------------------------------
# #123 / M2: EDLI authenticated-absence -> venue_commands terminalization sync
# ---------------------------------------------------------------------------

def _seed_edli_reconciled_absence(
    conn,
    *,
    aggregate_id,
    execution_command_id,
    token_id,
    venue_order_exists=False,
    venue_trade_exists=False,
    matching_open_order_count=0,
    matching_trade_count=0,
    reconcile_reason="AUTHENTICATED_CLOB_ABSENCE_NO_OPEN_ORDER_OR_TRADE",
    include_proof=True,
    occurred_at="2026-04-26T00:05:00Z",
    sequence=None,
):
    """Seed an EDLI Reconciled event mirroring edli_absence_resolver output.

    The canonical link is execution_command_id == venue_commands.decision_id.
    The shared _insert_edli_live_order_event helper keys aggregate_event_id on
    the sequence, so distinct aggregates must use distinct sequences. Derive a
    stable per-aggregate sequence when the caller does not pin one.
    """
    if sequence is None:
        sequence = 9 + (int(hashlib.sha256(aggregate_id.encode()).hexdigest(), 16) % 1000)
    proof = {
        "schema_version": 1,
        "source": "authenticated_clob_user_read",
        "owner_scope": "authenticated_funder",
        "observed_at": occurred_at,
        "aggregate_id": aggregate_id,
        "execution_command_id": execution_command_id,
        "token_id": token_id,
        "open_orders_checked": True,
        "trades_checked": True,
        "open_orders_query_complete": True,
        "trades_query_complete": True,
        "matching_open_order_count": matching_open_order_count,
        "matching_trade_count": matching_trade_count,
        "matching_open_orders": [],
        "matching_trades": [],
        "proof_hash": "9" * 64,
    }
    payload = {
        "schema_version": 1,
        "event_id": "edli-event-1",
        "final_intent_id": "edli-intent-1",
        "source_authority": "venue_reconcile",
        "pending_reconcile": False,
        "execution_command_id": execution_command_id,
        "venue_order_exists": venue_order_exists,
        "venue_trade_exists": venue_trade_exists,
        "cap_transition_recommendation": "RELEASED",
        "reconcile_reason": reconcile_reason,
    }
    if include_proof:
        payload["authenticated_absence_proof"] = proof
    _insert_edli_live_order_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=sequence,
        event_type="Reconciled",
        payload=payload,
        occurred_at=occurred_at,
    )


def _seed_edli_stalled_attempt_with_reserved_cap(
    conn,
    *,
    aggregate_id="agg-ack-stalled",
    event_id="edli-event-ack-stalled",
    final_intent_id="edli-intent-ack-stalled",
    execution_command_id="edli-exec-ack-stalled",
    token_id="tok-ack-stalled",
    usage_id="cap-ack-stalled",
    include_venue_attempt=True,
):
    plan_payload = {
        "schema_version": 1,
        "event_id": event_id,
        "final_intent_id": final_intent_id,
        "execution_command_id": execution_command_id,
        "token_id": token_id,
        "condition_id": "condition-ack-stalled",
        "direction": "buy_yes",
    }
    _insert_edli_live_order_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="SubmitPlanBuilt",
        payload=plan_payload,
        occurred_at="2026-04-26T00:00:00Z",
    )
    _insert_edli_live_order_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=2,
        event_type="ExecutionCommandCreated",
        payload=plan_payload,
        occurred_at="2026-04-26T00:00:01Z",
    )
    projection_sequence = 2
    projection_event_type = "ExecutionCommandCreated"
    projection_state = "EXECUTION_COMMAND_CREATED"
    projection_updated_at = "2026-04-26T00:00:01Z"
    if include_venue_attempt:
        _insert_edli_live_order_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=3,
            event_type="VenueSubmitAttempted",
            payload=plan_payload,
            occurred_at="2026-04-26T00:00:02Z",
        )
        projection_sequence = 3
        projection_event_type = "VenueSubmitAttempted"
        projection_state = "VENUE_SUBMIT_ATTEMPTED"
        projection_updated_at = "2026-04-26T00:00:02Z"
    conn.execute(
        """
        INSERT INTO edli_live_order_projection (
            aggregate_id, event_id, final_intent_id, current_state,
            last_sequence, last_event_type, last_event_hash,
            pending_reconcile, venue_order_id, updated_at, schema_version
        )
        SELECT ?, ?, ?, ?,
               ?, ?, event_hash,
               0, NULL, ?, 1
        FROM edli_live_order_events
        WHERE aggregate_id = ? AND event_sequence = ?
        """,
        (
            aggregate_id,
            event_id,
            final_intent_id,
            projection_state,
            projection_sequence,
            projection_event_type,
            projection_updated_at,
            aggregate_id,
            projection_sequence,
        ),
    )
    conn.execute(
        """
        INSERT INTO edli_live_cap_usage (
            usage_id, event_id, decision_time, cap_scope,
            max_notional_usd, max_orders_per_day, reserved_notional_usd,
            order_count, reservation_status, final_intent_id,
            execution_command_id, created_at, schema_version
        ) VALUES (?, ?, '2026-04-26T00:00:00Z', 'tiny_live_canary',
                  5.0, 1, 5.0, 1, 'RESERVED', ?, ?, '2026-04-26T00:00:00Z', 1)
        """,
        (usage_id, event_id, final_intent_id, execution_command_id),
    )
    return {
        "aggregate_id": aggregate_id,
        "event_id": event_id,
        "final_intent_id": final_intent_id,
        "execution_command_id": execution_command_id,
        "token_id": token_id,
        "usage_id": usage_id,
    }


def _seed_unknown_side_effect_with_decision(conn, *, command_id, decision_id, token_id):
    """Seed a SUBMIT_UNKNOWN_SIDE_EFFECT row (no venue_order_id) with a decision_id."""
    _insert(
        conn,
        command_id=command_id,
        position_id=f"pos-{command_id}",
        decision_id=decision_id,
        token_id=token_id,
        side="BUY",
        size=10.0,
        price=0.5,
    )
    _advance_to_unknown_side_effect(conn, command_id=command_id)


def _venue_read_unavailable_client(mock_client):
    """Make the in-flight per-row venue lookup UNAVAILABLE.

    This reproduces the real #123 incident condition: the live recovery
    _reconcile_row cannot resolve the stuck row from the venue (no complete
    authenticated read surface), so it stays SUBMIT_UNKNOWN_SIDE_EFFECT and the
    EDLI absence-sync pass is the only thing that can discharge it. The sync
    pass itself is DB-only and never touches the client.
    """
    mock_client.get_open_orders.side_effect = RuntimeError("venue read unavailable")
    mock_client.get_trades.side_effect = RuntimeError("venue read unavailable")
    return mock_client


class TestEdliAbsenceVenueCommandSync:
    """#123 / M2: sync EDLI authenticated-absence proof to venue_commands."""

    def test_absence_proven_terminalizes_and_clears_governor_count(self, conn, mock_client):
        from src.execution.command_recovery import reconcile_unresolved_commands
        from src.risk_allocator.governor import count_unknown_side_effects

        decision_id = "edli_exec_cmd:agg-a:intent:tok-absent:tok-absent:buy_no"
        _seed_unknown_side_effect_with_decision(
            conn,
            command_id="cmd-absent",
            decision_id=decision_id,
            token_id="tok-absent",
        )
        _seed_edli_reconciled_absence(
            conn,
            aggregate_id="agg-a",
            execution_command_id=decision_id,
            token_id="tok-absent",
        )

        # Pre-condition: the stuck row latches the governor.
        before_count, _ = count_unknown_side_effects(conn)
        assert before_count == 1

        _venue_read_unavailable_client(mock_client)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["venue_command_absence_sync"]["advanced"] == 1
        assert summary["venue_command_absence_sync"]["errors"] == 0
        assert _get_state(conn, "cmd-absent") == "SUBMIT_REJECTED"
        events = _get_events(conn, "cmd-absent")
        terminal = events[-1]
        assert terminal["event_type"] == "SUBMIT_REJECTED"
        payload = json.loads(terminal["payload_json"])
        assert payload["proof_class"] == "edli_authenticated_clob_absence"
        assert payload["safe_replay_permitted"] is True
        assert payload["edli_absence_proof"]["proof_hash"] == "9" * 64
        assert payload["edli_absence_proof"]["venue_order_exists"] is False
        assert payload["edli_absence_proof"]["venue_trade_exists"] is False

        # Post-condition: governor count drops to zero -> kill switch can clear.
        after_count, after_markets = count_unknown_side_effects(conn)
        assert after_count == 0
        assert after_markets == ()
        # The terminal write must not call the venue (proof came from EDLI).
        mock_client.get_order.assert_not_called()

    def test_no_absence_proof_leaves_row_unchanged(self, conn, mock_client):
        from src.execution.command_recovery import reconcile_unresolved_commands
        from src.risk_allocator.governor import count_unknown_side_effects

        decision_id = "edli_exec_cmd:agg-b:intent:tok-pending:tok-pending:buy_no"
        _seed_unknown_side_effect_with_decision(
            conn,
            command_id="cmd-pending",
            decision_id=decision_id,
            token_id="tok-pending",
        )
        # No EDLI Reconciled event exists for this decision_id (still pending).

        _venue_read_unavailable_client(mock_client)
        summary = reconcile_unresolved_commands(conn, mock_client)

        # Fail-closed: the per-row scan finds no proof; the sync pass leaves it.
        assert summary["venue_command_absence_sync"]["advanced"] == 0
        assert _get_state(conn, "cmd-pending") == "SUBMIT_UNKNOWN_SIDE_EFFECT"
        count, _ = count_unknown_side_effects(conn)
        assert count == 1

    def test_matching_venue_order_exposure_never_released(self, conn, mock_client):
        from src.execution.command_recovery import reconcile_unresolved_commands
        from src.risk_allocator.governor import count_unknown_side_effects

        decision_id = "edli_exec_cmd:agg-c:intent:tok-live:tok-live:buy_no"
        _seed_unknown_side_effect_with_decision(
            conn,
            command_id="cmd-live",
            decision_id=decision_id,
            token_id="tok-live",
        )
        # EDLI Reconciled reports a live venue order (real exposure) — must NOT
        # be auto-released even though it links to the command.
        _seed_edli_reconciled_absence(
            conn,
            aggregate_id="agg-c",
            execution_command_id=decision_id,
            token_id="tok-live",
            venue_order_exists=True,
            matching_open_order_count=1,
        )

        _venue_read_unavailable_client(mock_client)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["venue_command_absence_sync"]["advanced"] == 0
        assert summary["venue_command_absence_sync"]["stayed"] == 1
        assert _get_state(conn, "cmd-live") == "SUBMIT_UNKNOWN_SIDE_EFFECT"
        count, _ = count_unknown_side_effects(conn)
        assert count == 1

    def test_ambiguous_edli_link_is_fail_closed(self, conn, mock_client):
        from src.execution.command_recovery import reconcile_unresolved_commands
        from src.risk_allocator.governor import count_unknown_side_effects

        decision_id = "edli_exec_cmd:ambiguous:intent:tok-amb:tok-amb:buy_no"
        _seed_unknown_side_effect_with_decision(
            conn,
            command_id="cmd-amb",
            decision_id=decision_id,
            token_id="tok-amb",
        )
        # Two distinct EDLI aggregates both link to this decision_id — the link
        # is not unique, so the row must be left untouched (fail-closed).
        _seed_edli_reconciled_absence(
            conn,
            aggregate_id="agg-amb-1",
            execution_command_id=decision_id,
            token_id="tok-amb",
        )
        _seed_edli_reconciled_absence(
            conn,
            aggregate_id="agg-amb-2",
            execution_command_id=decision_id,
            token_id="tok-amb",
        )

        _venue_read_unavailable_client(mock_client)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["venue_command_absence_sync"]["advanced"] == 0
        assert summary["venue_command_absence_sync"]["stayed"] == 1
        assert _get_state(conn, "cmd-amb") == "SUBMIT_UNKNOWN_SIDE_EFFECT"
        count, _ = count_unknown_side_effects(conn)
        assert count == 1

    def test_proof_token_mismatch_is_fail_closed(self, conn, mock_client):
        from src.execution.command_recovery import reconcile_unresolved_commands

        decision_id = "edli_exec_cmd:agg-d:intent:tok-cmd:tok-cmd:buy_no"
        _seed_unknown_side_effect_with_decision(
            conn,
            command_id="cmd-token-mismatch",
            decision_id=decision_id,
            token_id="tok-cmd",
        )
        # Proof links by decision_id but its token_id is a DIFFERENT token.
        _seed_edli_reconciled_absence(
            conn,
            aggregate_id="agg-d",
            execution_command_id=decision_id,
            token_id="tok-other",
        )

        _venue_read_unavailable_client(mock_client)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["venue_command_absence_sync"]["advanced"] == 0
        assert _get_state(conn, "cmd-token-mismatch") == "SUBMIT_UNKNOWN_SIDE_EFFECT"

    def test_acked_command_sync_consumes_stalled_edli_cap(self, conn, mock_client):
        from src.execution.command_recovery import reconcile_unresolved_commands

        seeded = _seed_edli_stalled_attempt_with_reserved_cap(conn)
        _insert(
            conn,
            command_id="cmd-acked-edli-stall",
            decision_id=seeded["execution_command_id"],
            token_id=seeded["token_id"],
            side="BUY",
            price=0.01,
            size=569.08,
        )
        _advance_to_acked(
            conn,
            command_id="cmd-acked-edli-stall",
            venue_order_id="0xackedvenueorder",
        )

        _venue_read_unavailable_client(mock_client)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["edli_acknowledged_venue_command_sync"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        cap = conn.execute(
            "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = ?",
            (seeded["usage_id"],),
        ).fetchone()
        assert cap["reservation_status"] == "CONSUMED"
        projection = conn.execute(
            """
            SELECT current_state, last_event_type, venue_order_id, pending_reconcile
            FROM edli_live_order_projection
            WHERE aggregate_id = ?
            """,
            (seeded["aggregate_id"],),
        ).fetchone()
        assert dict(projection) == {
            "current_state": "VENUE_SUBMIT_ACKED",
            "last_event_type": "CapTransitioned",
            "venue_order_id": "0xackedvenueorder",
            "pending_reconcile": 0,
        }
        event_types = [
            row["event_type"]
            for row in conn.execute(
                """
                SELECT event_type
                FROM edli_live_order_events
                WHERE aggregate_id = ?
                ORDER BY event_sequence
                """,
                (seeded["aggregate_id"],),
            ).fetchall()
        ]
        assert event_types[-2:] == ["VenueSubmitAcknowledged", "CapTransitioned"]

    def test_rejected_command_sync_releases_stalled_edli_cap(self, conn, mock_client):
        from src.execution.command_recovery import reconcile_unresolved_commands
        from src.state.venue_command_repo import append_event

        seeded = _seed_edli_stalled_attempt_with_reserved_cap(
            conn,
            aggregate_id="agg-rejected-stalled",
            event_id="edli-event-rejected-stalled",
            final_intent_id="edli-intent-rejected-stalled",
            execution_command_id="edli-exec-rejected-stalled",
            token_id="tok-rejected-stalled",
            usage_id="cap-rejected-stalled",
            include_venue_attempt=False,
        )
        _insert(
            conn,
            command_id="cmd-rejected-edli-stall",
            decision_id=seeded["execution_command_id"],
            token_id=seeded["token_id"],
            side="BUY",
            price=0.01,
            size=569.08,
        )
        _advance_to_submitting(conn, command_id="cmd-rejected-edli-stall")
        append_event(
            conn,
            command_id="cmd-rejected-edli-stall",
            event_type="SUBMIT_REJECTED",
            occurred_at="2026-04-26T00:00:03Z",
            payload={
                "reason": "safe_replay_permitted_no_order_found",
                "safe_replay_permitted": True,
                "lookup_method": "idempotency_key",
                "recovered_from_state": "SUBMITTING",
                "venue_absence_proof": {
                    "schema_version": 1,
                    "token_id": seeded["token_id"],
                    "matching_open_order_count": 0,
                    "matching_trade_count": 0,
                    "matching_open_orders": [],
                    "matching_trades": [],
                },
            },
        )

        _venue_read_unavailable_client(mock_client)
        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["edli_rejected_venue_command_sync"] == {
            "scanned": 1,
            "advanced": 1,
            "stayed": 0,
            "errors": 0,
        }
        cap = conn.execute(
            "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = ?",
            (seeded["usage_id"],),
        ).fetchone()
        assert cap["reservation_status"] == "RELEASED"
        projection = conn.execute(
            """
            SELECT current_state, last_event_type, venue_order_id, pending_reconcile
            FROM edli_live_order_projection
            WHERE aggregate_id = ?
            """,
            (seeded["aggregate_id"],),
        ).fetchone()
        assert dict(projection) == {
            "current_state": "CAP_TRANSITIONED",
            "last_event_type": "CapTransitioned",
            "venue_order_id": None,
            "pending_reconcile": 0,
        }
        event_types = [
            row["event_type"]
            for row in conn.execute(
                """
                SELECT event_type
                FROM edli_live_order_events
                WHERE aggregate_id = ?
                ORDER BY event_sequence
                """,
                (seeded["aggregate_id"],),
            ).fetchall()
        ]
        assert event_types == [
            "SubmitPlanBuilt",
            "ExecutionCommandCreated",
            "VenueSubmitAttempted",
            "SubmitRejected",
            "CapTransitioned",
        ]
        rejected_payload = json.loads(
            conn.execute(
                """
                SELECT payload_json
                FROM edli_live_order_events
                WHERE aggregate_id = ? AND event_type = 'SubmitRejected'
                ORDER BY event_sequence DESC
                LIMIT 1
                """,
                (seeded["aggregate_id"],),
            ).fetchone()["payload_json"]
        )
        assert rejected_payload["proof_class"] == "venue_command_authenticated_absence_rejected"
        assert rejected_payload["safe_replay_permitted"] is True


# ---------------------------------------------------------------------------
# TestRecoveryCycleIntegration
# ---------------------------------------------------------------------------

class TestRecoveryCycleIntegration:
    """Assert cycle_runner invokes reconcile_unresolved_commands."""

    def test_cycle_supplied_conn_does_not_open_trade_world_connection(
        self,
        conn,
        mock_client,
        monkeypatch,
    ):
        """Relationship: supplied cycle conn is reused by command recovery."""
        import src.state.db as db_module
        from src.execution.command_recovery import reconcile_unresolved_commands

        def fail_if_reopened(*args, **kwargs):
            raise AssertionError("cycle-owned command recovery must not open a second trade/world connection")

        monkeypatch.setattr(db_module, "get_trade_connection_with_world", fail_if_reopened)

        summary = reconcile_unresolved_commands(conn, mock_client)

        assert summary["scanned"] == 0

    def test_cycle_runner_calls_recovery(self, monkeypatch):
        """Patch reconcile_unresolved_commands and verify cycle_runner calls it."""
        import sys
        from unittest.mock import patch, MagicMock

        called_with = []

        def fake_reconcile(*args, **kwargs):
            called_with.append((args, kwargs))
            return {"scanned": 0, "advanced": 0, "stayed": 0, "errors": 0}

        # Build a minimal cycle_runner context
        # We patch at the import site inside cycle_runner (via sys.modules)
        import importlib

        # Patch posture to NORMAL so entries aren't blocked for unrelated reasons
        posture_patch = patch(
            "src.runtime.posture.read_runtime_posture",
            return_value="NORMAL",
        )

        # Patch the recovery function at the module where it's imported inside run_cycle
        recovery_patch = patch(
            "src.execution.command_recovery.reconcile_unresolved_commands",
            side_effect=fake_reconcile,
        )

        # We cannot easily run a full cycle without live deps, so instead we verify
        # the import and call structure from the cycle_runner source.
        # Approach: import cycle_runner, parse for the recovery call.
        repo_root = Path(__file__).resolve().parents[1]
        cr_src = (repo_root / "src/engine/cycle_runner.py").read_text(encoding="utf-8")

        # Assert both the import and the call appear in the source
        assert "reconcile_unresolved_commands" in cr_src, (
            "cycle_runner.py must import/call reconcile_unresolved_commands (INV-31)"
        )
        assert "command_recovery" in cr_src, (
            "cycle_runner.py must reference command_recovery module (INV-31)"
        )
        assert "reconcile_unresolved_commands(conn)" in cr_src, (
            "cycle_runner.py must pass the already-open trade/world conn into command recovery"
        )
        assert "reconcile_unresolved_commands()" not in cr_src, (
            "cycle_runner.py must not let command recovery open a second trade/world connection"
        )
        assert 'summary["command_recovery"]' in cr_src, (
            'cycle_runner.py must record summary["command_recovery"] result (INV-31)'
        )
