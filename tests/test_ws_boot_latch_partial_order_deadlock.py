# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: 2026-06-09 ws-boot-latch deadlock incident. Three requirements formed
#   a cycle that latched submits FOREVER after any daemon restart with a resting order:
#   (1) the pong clean-boot transition (not_configured -> AUTHED) demanded an EMPTY local
#   side-effect surface (a PARTIAL GTC venue_command from 2026-06-08 blocked it);
#   (2) main's M5 clear pass defers on DISCONNECTED:not_configured (the boot state);
#   (3) ws_gap_guard.clear_after_m5_reconcile demands a healthy (pong-fed) subscription.
#   Plus the refresh leg: pongs refused to refresh while the M5 latch was armed, so the
#   guard went stale 30s after AUTHED and clear_after_m5_reconcile failed closed forever
#   ("cannot clear ws gap without healthy subscription", the 12:26Z loop).
"""RELATIONSHIP tests: pong keepalive -> M5 sweep -> ws_gap submit latch.

Cross-module invariant (polymarket_user_channel -> ws_gap_guard -> exchange_reconcile):
  TWO proofs, TWO owners. A pong proves transport+auth: it must transition the clean-boot
  latch to AUTHED and KEEP liveness fresh, but never clear submit authority while the
  local side-effect surface is non-empty. The full M5 sweep proves the surface: with a
  healthy AUTHED subscription and a zero-finding sweep it must clear submit authority —
  even with a resting PARTIAL order present. No reachable state may be un-clearable by
  the (pong stream + clean sweep) pair.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.control import ws_gap_guard
from src.execution.exchange_reconcile import (
    init_exchange_reconcile_schema,
    run_ws_gap_reconcile_and_clear,
)
from src.ingest.polymarket_user_channel import PolymarketUserChannelIngestor, WSAuth
from src.state.db import init_schema

NOW = datetime(2026, 6, 9, 23, 40, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_schema(c)
    init_exchange_reconcile_schema(c)
    ws_gap_guard.clear_for_test(observed_at=NOW)
    ws_gap_guard.configure_status(
        ws_gap_guard.WSGapStatus(
            connected=False,
            last_message_at=None,
            subscription_state="DISCONNECTED",
            gap_reason="not_configured",
            m5_reconcile_required=True,
            updated_at=NOW,
            stale_after_seconds=30,
        )
    )
    yield c
    c.close()
    ws_gap_guard.clear_for_test(observed_at=NOW)


def _ingestor(c) -> PolymarketUserChannelIngestor:
    return PolymarketUserChannelIngestor(
        adapter=object(),
        condition_ids=["condition-ws"],
        auth=WSAuth("key", "secret", "pass"),
        conn_factory=lambda: c,
        own_connection=False,
    )


def _seed_partial_command(c) -> None:
    # The 2026-06-08 survivor: a PARTIAL GTC order resting across the restart.
    c.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, created_at, updated_at
        ) VALUES ('cmdpartial', 'snap', 'env', 'pos', 'dec', 'idem', 'EXIT',
                  '0xmarket', 'tok', 'SELL', '5', '0.32', '0xrest', 'PARTIAL', ?, ?)
        """,
        (NOW.isoformat(), NOW.isoformat()),
    )


class _CleanSweepAdapter:
    """Venue truth: the resting order is the ONLY open order; trades enumerable."""

    def __init__(self):
        self.read_freshness = {"open_orders": True, "trades": True, "positions": True}

    def get_open_orders(self):
        return [
            {
                "id": "0xrest",
                "market": "0xmarket",
                "asset_id": "tok",
                "side": "SELL",
                "original_size": "5",
                "size_matched": "1.65",
                "status": "LIVE",
                "order_type": "GTC",
            }
        ]

    def get_trades(self):
        return []


# ---- the deadlock, leg by leg ----------------------------------------------------------
def test_pong_with_resting_order_marks_authed_but_keeps_latch(conn) -> None:
    _seed_partial_command(conn)
    status = _ingestor(conn)._record_transport_keepalive(observed_at=NOW)
    assert status.subscription_state == "AUTHED", (
        "the pong proves transport+auth; refusing the AUTHED transition because a "
        "resting order exists is leg 1 of the boot-latch deadlock"
    )
    assert status.m5_reconcile_required is True, (
        "the pong must NOT clear submit authority — the surface is non-empty; only "
        "the M5 sweep owns that proof"
    )
    assert not status.to_summary(now=NOW)["entry"]["allow_submit"]


def test_pong_keeps_liveness_fresh_while_latched(conn) -> None:
    _seed_partial_command(conn)
    ing = _ingestor(conn)
    ing._record_transport_keepalive(observed_at=NOW)
    later = NOW + timedelta(seconds=29)
    status = ing._record_transport_keepalive(observed_at=later)
    assert status.last_message_at == later, (
        "pongs must keep refreshing liveness while the M5 latch is armed; a stale "
        "guard makes clear_after_m5_reconcile fail closed forever (12:26Z loop)"
    )
    assert status.m5_reconcile_required is True  # refresh never clears the latch
    assert not status.is_stale(now=later + timedelta(seconds=10))


def test_clean_m5_sweep_clears_latch_despite_partial_order(conn) -> None:
    _seed_partial_command(conn)
    ing = _ingestor(conn)
    ing._record_transport_keepalive(observed_at=NOW)
    result = run_ws_gap_reconcile_and_clear(
        _CleanSweepAdapter(), conn, observed_at=NOW + timedelta(seconds=5)
    )
    assert result["status"] == "cleared", result
    summary = ws_gap_guard.summary(now=NOW + timedelta(seconds=6))
    assert summary["entry"]["allow_submit"] is True, (
        "pong (channel proof) + zero-finding sweep (surface proof) must reopen "
        "submit authority even with a resting PARTIAL order — no reachable state "
        "may be un-clearable by that pair"
    )


def test_empty_surface_pong_still_full_clears(conn) -> None:
    # Regression: the original clean-boot fast path is unchanged.
    status = _ingestor(conn)._record_transport_keepalive(observed_at=NOW)
    assert status.subscription_state == "AUTHED"
    assert status.m5_reconcile_required is False
    assert status.to_summary(now=NOW)["entry"]["allow_submit"] is True


def test_unresolved_finding_keeps_sweep_from_clearing(conn) -> None:
    # Fail-closed regression: the sweep proof requires ZERO unresolved findings.
    from src.execution.exchange_reconcile import record_finding

    _seed_partial_command(conn)
    record_finding(
        conn,
        kind="position_drift",
        subject_id="tokX",
        context="ws_gap",
        evidence={"reason": "drift"},
        recorded_at=NOW,
    )
    ing = _ingestor(conn)
    ing._record_transport_keepalive(observed_at=NOW)
    result = run_ws_gap_reconcile_and_clear(
        _CleanSweepAdapter(), conn, observed_at=NOW + timedelta(seconds=5)
    )
    assert result["status"] == "blocked"
    assert ws_gap_guard.summary(now=NOW + timedelta(seconds=6))["entry"]["allow_submit"] is False
