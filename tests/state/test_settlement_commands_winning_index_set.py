# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: PR-I.5.a / autonomous redeem prep (PR_I5_WEB3_WIRE.md)
"""Antibody tests: winning_index_set populated at REDEEM_INTENT_CREATED time.

PR-I.5.a adds winning_index_set to settlement_commands so that the future
PR-I.5.c web3 redeemPositions wire can construct correct indexSets calldata.

Invariants:
  - After enqueue_redeem_command with direction=buy_yes (YES won):
      winning_index_set = '["2"]'  (binary YES = CTF index 1, indexSet = 1<<1)
  - After enqueue_redeem_command with direction=buy_no (NO won):
      winning_index_set = '["1"]'  (binary NO  = CTF index 0, indexSet = 1<<0)
  - None is valid (for non-binary markets; V1 limitation documented in
    settlement_commands.py::request_redeem docstring).

Binary market convention reference:
  YES outcome → outcome index 1 → indexSet = 1 << 1 = 2 → JSON '["2"]'
  NO  outcome → outcome index 0 → indexSet = 1 << 0 = 1 → JSON '["1"]'
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.execution.harvester import enqueue_redeem_command
from src.execution.settlement_commands import init_settlement_command_schema


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_settlement_command_schema(conn)
    conn.commit()
    return conn


def _fetch_winning_index_set(conn: sqlite3.Connection, command_id: str) -> str | None:
    row = conn.execute(
        "SELECT winning_index_set FROM settlement_commands WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row is not None, f"command_id {command_id!r} not found in settlement_commands"
    return row["winning_index_set"]


# ---------------------------------------------------------------------------
# Parametrized core antibody
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("direction,expected_json,case_label", [
    ("buy_yes", '["2"]', "yes_resolved"),
    ("buy_no",  '["1"]', "no_resolved"),
])
def test_winning_index_set_populated_at_enqueue(direction, expected_json, case_label):
    """After enqueue, winning_index_set matches CTF binary convention.

    Uses USDC_E payout to bypass Q-FX-1 gate (no ZEUS_PUSD_FX_CLASSIFIED env
    in test environment). State is REDEEM_REVIEW_REQUIRED, which is fine —
    the column value is what we are testing, not the payout asset path.

    Relationship tested:
      harvester._settle_positions (direction + won) →
      enqueue_redeem_command (winning_index_set param) →
      request_redeem INSERT → settlement_commands.winning_index_set column
    """
    conn = _make_conn()

    result = enqueue_redeem_command(
        conn,
        condition_id=f"0xdeadbeef{case_label}",
        payout_asset="USDC_E",  # bypasses Q-FX-1 gate; column value still tested
        market_id=f"market-{case_label}",
        pusd_amount_micro=500_000,
        token_amounts={"tok1": 0.5},
        trade_id=f"trade-{case_label}",
        winning_index_set=expected_json,
    )
    conn.commit()

    assert result["status"] == "queued", f"Expected queued, got {result}"
    command_id = result["command_id"]
    assert command_id is not None

    stored = _fetch_winning_index_set(conn, command_id)
    assert stored == expected_json, (
        f"[{case_label}] winning_index_set mismatch: "
        f"expected {expected_json!r}, got {stored!r}"
    )
    # Validate it parses as a valid JSON uint256 array
    parsed = json.loads(stored)
    assert isinstance(parsed, list) and len(parsed) == 1
    assert int(parsed[0]) in (1, 2), f"Binary indexSet must be 1 or 2, got {parsed[0]}"


def test_winning_index_set_none_for_multi_bin_unsupported():
    """V1 limitation: callers pass None for non-binary (ranged) markets.

    winning_index_set=None is stored as NULL and does not break enqueue.
    Multi-bin encoding is out of scope for PR-I.5.a (LIMITATION documented).
    """
    conn = _make_conn()
    result = enqueue_redeem_command(
        conn,
        condition_id="0xmultibinunsupported",
        payout_asset="USDC_E",  # bypasses Q-FX-1 gate
        market_id="market-multibin",
        pusd_amount_micro=1_000_000,
        token_amounts={},
        trade_id="trade-multibin",
        winning_index_set=None,  # V1: not supported for ranged markets
    )
    conn.commit()

    assert result["status"] == "queued"
    stored = _fetch_winning_index_set(conn, result["command_id"])
    assert stored is None, f"Expected NULL for multi-bin unsupported case, got {stored!r}"


def test_winning_index_set_idempotent_on_duplicate_enqueue():
    """Duplicate enqueue (same condition_id/market_id/payout_asset) returns the
    existing command_id without overwriting winning_index_set."""
    conn = _make_conn()
    first = enqueue_redeem_command(
        conn,
        condition_id="0xidempotent",
        payout_asset="USDC_E",  # bypasses Q-FX-1 gate
        market_id="market-idem",
        winning_index_set='["2"]',
    )
    conn.commit()
    second = enqueue_redeem_command(
        conn,
        condition_id="0xidempotent",
        payout_asset="USDC_E",
        market_id="market-idem",
        winning_index_set='["2"]',
    )
    conn.commit()

    # Both calls return the same command_id (idempotency)
    assert first["command_id"] == second["command_id"]
    stored = _fetch_winning_index_set(conn, first["command_id"])
    assert stored == '["2"]'
