# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: Integrated money-path lifecycle replay across command, order, trade,
#   position, settlement, redeem, and telemetry crash/restart boundaries.
# Reuse: Run before live-money release claims or when touching execution,
#   recovery, reconcile, settlement, decision-event, or no-trade state.
# Created: 2026-05-21
# Last reused/audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_live_release_proof_p0p3/task.md P0-4
"""Integrated money-path lifecycle replay with crash/restart boundaries."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.effective_kelly_context import EffectiveKellyContext
from src.contracts.execution_intent import DecisionSourceContext
from src.contracts.no_trade_reason import NoTradeReason
from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
from src.execution.order_truth_reducer import PARTIAL_WITH_REMAINDER, TERMINAL_FILLED, VenueOrderTruthReducer
from src.execution.settlement_commands import SettlementState, reconcile_pending_redeems, request_redeem, submit_redeem
from src.state.db import _install_connection_functions, init_schema
from src.state.decision_events import write_decision_event
from src.state.no_trade_events import write_no_trade_event
from src.state.snapshot_repo import get_snapshot, insert_snapshot
from src.state.venue_command_repo import (
    append_event,
    append_order_fact,
    append_position_lot,
    append_trade_fact,
    insert_command,
    insert_submission_envelope,
)

NOW = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
PAYOUT_TOPIC = "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"


class FakeRedeemAdapter:
    def __init__(self, tx_hash: str = "0x" + "ab" * 32) -> None:
        self.tx_hash = tx_hash
        self.calls: list[tuple[str, list[int] | None]] = []

    def redeem(self, condition_id: str, *, index_sets=None, **_ignored):
        self.calls.append((condition_id, list(index_sets) if index_sets else None))
        return {"success": True, "tx_hash": self.tx_hash}


class FakeEth:
    def __init__(self, receipt: dict[str, object]) -> None:
        self._receipt = receipt
        self.block_number = int(receipt["blockNumber"]) + 8

    def get_transaction_receipt(self, tx_hash: str):  # noqa: ARG002
        return self._receipt


class FakeWeb3:
    def __init__(self, receipt: dict[str, object]) -> None:
        self.eth = FakeEth(receipt)


@dataclass
class ReplayHarness:
    db_path: Path
    conn: sqlite3.Connection

    @classmethod
    def create(cls, tmp_path: Path) -> "ReplayHarness":
        db_path = tmp_path / "zeus-world.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        _attach_world_overlay(conn)
        return cls(db_path=db_path, conn=conn)

    def crash_restart(self) -> None:
        self.conn.commit()
        self.conn.close()
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        _install_connection_functions(self.conn)
        _attach_world_overlay(self.conn)

    def close(self) -> None:
        self.conn.close()


def _attach_world_overlay(conn: sqlite3.Connection) -> None:
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" not in attached:
        conn.execute("ATTACH DATABASE ':memory:' AS world")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS world.executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          gamma_market_id TEXT NOT NULL DEFAULT '',
          event_id TEXT NOT NULL DEFAULT '',
          condition_id TEXT NOT NULL,
          question_id TEXT NOT NULL DEFAULT '',
          yes_token_id TEXT NOT NULL DEFAULT '',
          no_token_id TEXT NOT NULL DEFAULT '',
          enable_orderbook INTEGER NOT NULL DEFAULT 1,
          active INTEGER NOT NULL DEFAULT 1,
          closed INTEGER NOT NULL DEFAULT 0,
          min_tick_size TEXT NOT NULL DEFAULT '0.01',
          min_order_size TEXT NOT NULL DEFAULT '5',
          fee_details_json TEXT NOT NULL DEFAULT '{}',
          token_map_json TEXT NOT NULL DEFAULT '{}',
          neg_risk INTEGER NOT NULL DEFAULT 0,
          orderbook_top_bid TEXT NOT NULL DEFAULT '0',
          orderbook_top_ask TEXT NOT NULL DEFAULT '1',
          orderbook_depth_json TEXT NOT NULL DEFAULT '{}',
          raw_gamma_payload_hash TEXT NOT NULL DEFAULT '',
          raw_clob_market_info_hash TEXT NOT NULL DEFAULT '',
          raw_orderbook_hash TEXT NOT NULL DEFAULT '',
          authority_tier TEXT NOT NULL DEFAULT 'CLOB',
          captured_at TEXT NOT NULL DEFAULT (datetime('now')),
          freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO world.executable_market_snapshots (
          snapshot_id, condition_id, yes_token_id, no_token_id, neg_risk
        ) VALUES ('snap-cond-live', 'cond-live', 'tok-yes', 'tok-no', 0)
        """
    )
    conn.commit()


def _ensure_snapshot(conn: sqlite3.Connection) -> str:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2

    snapshot_id = "snap-entry"
    if get_snapshot(conn, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-live",
            event_id="event-live",
            event_slug="weather-live",
            condition_id="cond-live",
            question_id="question-live",
            yes_token_id="tok-yes",
            no_token_id="tok-no",
            selected_outcome_token_id="tok-yes",
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            fee_details={},
            token_map_raw={"YES": "tok-yes", "NO": "tok-no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.41"),
            orderbook_top_ask=Decimal("0.42"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=NOW,
            freshness_deadline=NOW + timedelta(days=1),
        ),
    )
    return snapshot_id


def _ensure_envelope(conn: sqlite3.Connection) -> str:
    envelope_id = "env-entry"
    if conn.execute("SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?", (envelope_id,)).fetchone():
        return envelope_id
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2",
            sdk_version="test",
            host="https://clob-v2.polymarket.com",
            chain_id=137,
            funder_address="0xfunder",
            condition_id="cond-live",
            question_id="question-live",
            yes_token_id="tok-yes",
            no_token_id="tok-no",
            selected_outcome_token_id="tok-yes",
            outcome_label="YES",
            side="BUY",
            price=Decimal("0.42"),
            size=Decimal("10"),
            order_type="GTC",
            post_only=False,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
            fee_details={},
            canonical_pre_sign_payload_hash="d" * 64,
            signed_order="signed",
            signed_order_hash="e" * 64,
            raw_request_hash="f" * 64,
            raw_response_json=None,
            order_id=None,
            trade_ids=(),
            transaction_hashes=(),
            error_code=None,
            error_message=None,
            captured_at=NOW.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    return envelope_id


def _source_context() -> DecisionSourceContext:
    return DecisionSourceContext(
        source_id="ecmwf-open-data",
        model_family="ecmwf-ens",
        forecast_issue_time="2026-05-21T00:00:00Z",
        forecast_valid_time="2026-05-22T00:00:00Z",
        forecast_fetch_time="2026-05-21T00:10:00Z",
        forecast_available_at="2026-05-21T00:15:00Z",
        raw_payload_hash="1" * 64,
        degradation_level="NONE",
        forecast_source_role="entry_primary",
        authority_tier="FORECAST",
        decision_time="2026-05-21T12:00:00Z",
        decision_time_status="OK",
        observation_time="2026-05-22T23:59:00Z",
        observation_available_at="2026-05-23T00:05:00Z",
        polymarket_end_anchor_source="gamma_explicit",
        first_member_observed_time="2026-05-21T00:11:00Z",
        run_complete_time="2026-05-21T00:14:00Z",
        zeus_submit_intent_time="2026-05-21T12:00:01Z",
        venue_ack_time="2026-05-21T12:00:02Z",
        first_inclusion_block_time="2026-05-21T12:00:10Z",
        finality_confirmed_time="2026-05-21T12:02:00Z",
        clock_skew_estimate_ms=12,
        raw_orderbook_hash_transition_delta_ms=250,
    )


def _seed_entry_command(conn: sqlite3.Connection) -> None:
    insert_command(
        conn,
        command_id="cmd-entry",
        snapshot_id=_ensure_snapshot(conn),
        envelope_id=_ensure_envelope(conn),
        position_id="pos-entry",
        decision_id="dec-entry",
        idempotency_key="idem-entry",
        intent_kind="ENTRY",
        market_id="market-live",
        token_id="tok-yes",
        side="BUY",
        size=10,
        price=0.42,
        created_at="2026-05-21T12:00:01Z",
    )


def _assert_command_state(conn: sqlite3.Connection, state: str) -> None:
    row = conn.execute("SELECT state FROM venue_commands WHERE command_id='cmd-entry'").fetchone()
    assert row is not None
    assert row["state"] == state


def _write_position_current(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
          position_id, phase, trade_id, market_id, city, cluster, target_date,
          bin_label, direction, unit, size_usd, shares, cost_basis_usd,
          entry_price, p_posterior, decision_snapshot_id, entry_method,
          strategy_key, edge_source, discovery_mode, chain_state, token_id,
          no_token_id, condition_id, order_id, order_status, updated_at,
          temperature_metric
        ) VALUES (
          'pos-entry', 'active', 'trade-live', 'market-live', 'Chicago',
          'Chicago', '2026-05-22', '70-71', 'buy_yes', 'F', 2.52, 6, 2.52,
          0.42, 0.57, 'snap-entry', 'live', 'center_buy', 'forecast',
          'opening_hunt', 'local_projected', 'tok-yes', 'tok-no',
          'cond-live', 'ord-entry', 'partial', '2026-05-21T12:00:10Z',
          'high'
        )
        """
    )
    conn.commit()


def _receipt(tx_hash: str) -> dict[str, object]:
    from src.venue.polymarket_v2_adapter import POLYGON_CTF_ADDRESS

    return {
        "status": 1,
        "transactionHash": tx_hash,
        "blockNumber": 1234,
        "logs": [
            {
                "address": POLYGON_CTF_ADDRESS.lower(),
                "topics": [PAYOUT_TOPIC],
                "data": "0x",
            }
        ],
    }


def test_money_path_lifecycle_replay_converges_across_crash_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEUS_PUSD_FX_CLASSIFIED", "fx_line_item")
    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: SimpleNamespace(allow_redemption=True, block_reason=None, state="LIVE_ENABLED"),
    )
    replay = ReplayHarness.create(tmp_path)
    try:
        natural_key = make_decision_natural_key("weather-live", "high", "2026-05-22", "2026-05-21T12:00:00Z", 0)
        write_decision_event(
            natural_key,
            _source_context(),
            EffectiveKellyContext(spread_usd=Decimal("0.01"), depth_at_best_ask=200, order_type="GTC"),
            direction="YES",
            strategy_key="center_buy",
            target_size_usd=4.20,
            limit_price=0.42,
            edge=0.04,
            p_posterior=0.57,
            conn=replay.conn,
        )
        write_no_trade_event(
            natural_key,
            NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP,
            "sibling family bin dropped before submit",
            "2026-05-21T12:00:00Z",
            conn=replay.conn,
        )
        replay.crash_restart()
        assert replay.conn.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 1
        assert replay.conn.execute("SELECT reason FROM no_trade_events").fetchone()[0] == NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP.value

        _seed_entry_command(replay.conn)
        replay.crash_restart()
        _assert_command_state(replay.conn, "INTENT_CREATED")

        append_event(
            replay.conn,
            command_id="cmd-entry",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-05-21T12:00:02Z",
            payload={"pre_side_effect": True},
        )
        append_event(
            replay.conn,
            command_id="cmd-entry",
            event_type="SUBMIT_TIMEOUT_UNKNOWN",
            occurred_at="2026-05-21T12:00:03Z",
            payload={"reason": "timeout_after_sdk_call"},
        )
        replay.crash_restart()
        _assert_command_state(replay.conn, "SUBMIT_UNKNOWN_SIDE_EFFECT")

        append_event(
            replay.conn,
            command_id="cmd-entry",
            event_type="SUBMIT_ACKED",
            occurred_at="2026-05-21T12:00:04Z",
            payload={"venue_order_id": "ord-entry"},
        )
        append_order_fact(
            replay.conn,
            venue_order_id="ord-entry",
            command_id="cmd-entry",
            state="LIVE",
            remaining_size="10",
            matched_size="0",
            source="REST",
            observed_at="2026-05-21T12:00:04Z",
            raw_payload_hash="2" * 64,
            raw_payload_json={"status": "LIVE"},
        )
        replay.crash_restart()
        _assert_command_state(replay.conn, "ACKED")
        assert VenueOrderTruthReducer.reduce(
            order_facts=replay.conn.execute("SELECT * FROM venue_order_facts WHERE venue_order_id='ord-entry'").fetchall(),
            trade_filled_size="0",
            command_size="10",
            command_state="ACKED",
        ).state == "LIVE"

        append_event(
            replay.conn,
            command_id="cmd-entry",
            event_type="PARTIAL_FILL_OBSERVED",
            occurred_at="2026-05-21T12:00:05Z",
            payload={"venue_order_id": "ord-entry", "trade_id": "trade-live"},
        )
        append_order_fact(
            replay.conn,
            venue_order_id="ord-entry",
            command_id="cmd-entry",
            state="PARTIALLY_MATCHED",
            remaining_size="4",
            matched_size="6",
            source="WS_USER",
            observed_at="2026-05-21T12:00:05Z",
            raw_payload_hash="3" * 64,
            raw_payload_json={"status": "PARTIALLY_MATCHED"},
        )
        trade_fact_id = append_trade_fact(
            replay.conn,
            trade_id="trade-live",
            venue_order_id="ord-entry",
            command_id="cmd-entry",
            state="MATCHED",
            filled_size="6",
            fill_price="0.42",
            source="REST",
            observed_at="2026-05-21T12:00:06Z",
            raw_payload_hash="4" * 64,
            raw_payload_json={"status": "MATCHED"},
        )
        append_position_lot(
            replay.conn,
            position_id=1,
            state="OPTIMISTIC_EXPOSURE",
            shares="6",
            entry_price_avg="0.42",
            captured_at="2026-05-21T12:00:06Z",
            state_changed_at="2026-05-21T12:00:06Z",
            source_command_id="cmd-entry",
            source_trade_fact_id=trade_fact_id,
            source="REST",
        )
        _write_position_current(replay.conn)
        replay.crash_restart()
        _assert_command_state(replay.conn, "PARTIAL")
        partial_truth = VenueOrderTruthReducer.reduce(
            order_facts=replay.conn.execute("SELECT * FROM venue_order_facts WHERE venue_order_id='ord-entry'").fetchall(),
            trade_filled_size="6",
            command_size="10",
            command_state="PARTIAL",
        )
        assert partial_truth.proof_class == PARTIAL_WITH_REMAINDER
        assert partial_truth.matched_size == Decimal("6")
        assert replay.conn.execute("SELECT shares FROM position_lots WHERE source_trade_fact_id=?", (trade_fact_id,)).fetchone()[0] == "6"

        append_event(
            replay.conn,
            command_id="cmd-entry",
            event_type="CANCEL_REQUESTED",
            occurred_at="2026-05-21T12:00:07Z",
            payload={"venue_order_id": "ord-entry", "reason": "cancel_remainder"},
        )
        append_order_fact(
            replay.conn,
            venue_order_id="ord-entry",
            command_id="cmd-entry",
            state="MATCHED",
            remaining_size="0",
            matched_size="6",
            source="REST",
            observed_at="2026-05-21T12:00:08Z",
            raw_payload_hash="5" * 64,
            raw_payload_json={"status": "MATCHED", "terminal_partial": True},
        )
        append_event(
            replay.conn,
            command_id="cmd-entry",
            event_type="EXPIRED",
            occurred_at="2026-05-21T12:00:09Z",
            payload={"venue_order_id": "ord-entry", "reason": "remaining_size_zero_after_cancel"},
        )
        replay.crash_restart()
        _assert_command_state(replay.conn, "EXPIRED")
        terminal_truth = VenueOrderTruthReducer.reduce(
            order_facts=replay.conn.execute("SELECT * FROM venue_order_facts WHERE venue_order_id='ord-entry'").fetchall(),
            trade_filled_size="6",
            command_size="6",
            command_state="EXPIRED",
        )
        assert terminal_truth.proof_class == TERMINAL_FILLED
        assert terminal_truth.remaining_size == Decimal("0")
        assert dict(
            replay.conn.execute(
                "SELECT phase, shares, cost_basis_usd, order_status FROM position_current WHERE position_id='pos-entry'"
            ).fetchone()
        ) == {"phase": "active", "shares": 6.0, "cost_basis_usd": 2.52, "order_status": "partial"}

        command_id = request_redeem(
            "cond-live",
            "pUSD",
            market_id="market-live",
            pusd_amount_micro=2_520_000,
            token_amounts={"tok-yes": "6"},
            winning_index_set='["2"]',
            polymarket_end_anchor_source="gamma_explicit",
            conn=replay.conn,
            requested_at="2026-05-21T12:10:00Z",
        )
        replay.crash_restart()
        assert replay.conn.execute("SELECT state FROM settlement_commands WHERE command_id=?", (command_id,)).fetchone()[0] == SettlementState.REDEEM_INTENT_CREATED.value

        adapter = FakeRedeemAdapter()
        result = submit_redeem(
            command_id,
            adapter,
            object(),
            conn=replay.conn,
            submitted_at="2026-05-21T12:11:00Z",
        )
        assert result.state is SettlementState.REDEEM_TX_HASHED
        replay.crash_restart()
        tx_hash = replay.conn.execute("SELECT tx_hash FROM settlement_commands WHERE command_id=?", (command_id,)).fetchone()[0]
        assert tx_hash == adapter.tx_hash
        assert adapter.calls == [("cond-live", [2])]

        [confirmed] = reconcile_pending_redeems(FakeWeb3(_receipt(tx_hash)), replay.conn)
        replay.crash_restart()
        assert confirmed.state is SettlementState.REDEEM_CONFIRMED
        row = replay.conn.execute(
            "SELECT state, block_number, confirmation_count, terminal_at FROM settlement_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        assert row["state"] == SettlementState.REDEEM_CONFIRMED.value
        assert row["block_number"] == 1234
        assert row["confirmation_count"] == 9
        assert row["terminal_at"] is not None

        assert replay.conn.execute("SELECT COUNT(*) FROM venue_command_events WHERE command_id='cmd-entry'").fetchone()[0] == 7
        assert replay.conn.execute("SELECT COUNT(*) FROM settlement_command_events WHERE command_id=?", (command_id,)).fetchone()[0] == 4
        assert replay.conn.execute("SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id='trade-live'").fetchone()[0] == 1
        assert replay.conn.execute("SELECT COUNT(*) FROM position_lots WHERE source_command_id='cmd-entry'").fetchone()[0] == 1
    finally:
        replay.close()
