# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator redeem directive 2026-06-09 (GS013 negRisk Safe wrap
#   on 6 settled REDEEM_OPERATOR_REQUIRED rows; chain-truth redeem inputs).
# Lifecycle: created=2026-06-09; last_reviewed=2026-06-09; last_reused=never
# Purpose: Antibody — submit_redeem() reads the winning negRisk position's LIVE
#   on-chain ERC1155 balance before submitting. Zero balance => terminal
#   REDEEM_CONFIRMED with provenance (no operator purgatory, no force-retry).
#   Nonzero-but-mismatched balance => self-heal with the live amount.
# Reuse: Run when modifying the chain-truth gate in submit_redeem,
#   adapter.get_negrisk_winning_position_balance, or the
#   redeem_terminal_no_tx terminal branch.
"""Antibody tests: chain-truth redeem inputs for negRisk Safe redemption.

Root cause (2026-06-09 operator directive): 6 settled winning positions latched
REDEEM_OPERATOR_REQUIRED with errorCode REDEEM_GAS_ESTIMATE_REVERTED / GS013.
On-chain diagnosis: every condition was RESOLVED (payoutDenominator=1) but the
Safe held ZERO ERC1155 balance of every relevant position token. The recorded
token_amounts_json amount is a settlement-time SNAPSHOT, not chain truth at
submit time; redeeming a stale amount that exceeds the live balance makes the
inner negRisk redeemPositions burn revert -> Safe execTransaction GS013 ->
endless operator purgatory.

Structural fix (K<<N, one decision): read the winning position's live balance
before submitting.
  * balance == 0 -> nothing to redeem. Terminal REDEEM_CONFIRMED with chain
    provenance. NOT operator-required, NOT force-retried.
  * balance  > 0 and != recorded -> self-heal: submit the LIVE balance.
  * probe failure -> fail-soft: proceed with the recorded amount (no regression).

Antibody contracts (sed-flip verifiable):
  A1: balance==0 -> state REDEEM_CONFIRMED, adapter.redeem NOT called, payload
      carries redeem_terminal_no_tx + chain_evidence.
  A2: balance>0 != recorded -> adapter.redeem called with amount_per_slot == live
      balance (self-heal), not the stale recorded micro amount.
  A3: probe returns ok=False -> adapter.redeem still called with recorded amount
      (fail-soft; chain-truth gate never blocks or fabricates).
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.state.db import init_schema


NOW = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
_CONDITION_ID = "0x" + "ab" * 32
_NO_TOKEN = "0xno_token_id_1234"
_YES_TOKEN = "0xyes_token_id_5678"


@pytest.fixture()
def plain_trade_conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    init_schema(db)
    yield db
    db.close()


@pytest.fixture()
def world_db_with_snapshot():
    """Real-file world DB with a negRisk snapshot row for _CONDITION_ID so the
    submitter's neg_risk lookup succeeds without a Gamma call."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    world_conn = sqlite3.connect(path)
    world_conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          condition_id TEXT NOT NULL,
          yes_token_id TEXT NOT NULL DEFAULT '',
          no_token_id TEXT NOT NULL DEFAULT '',
          neg_risk INTEGER NOT NULL DEFAULT 0,
          captured_at TEXT NOT NULL DEFAULT (datetime('now')),
          freshness_deadline TEXT NOT NULL DEFAULT (datetime('now', '+1 day'))
        )
        """
    )
    world_conn.execute(
        "INSERT INTO executable_market_snapshots "
        "(snapshot_id, condition_id, yes_token_id, no_token_id, neg_risk) "
        "VALUES (?, ?, ?, ?, 1)",
        ("snap-1", _CONDITION_ID, _YES_TOKEN, _NO_TOKEN),
    )
    world_conn.commit()
    world_conn.close()
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


class _ProbeAdapter:
    """Fake adapter exposing the chain-truth balance probe + redeem seam."""

    def __init__(self, *, probe_result):
        self._probe_result = probe_result
        self.redeem_calls = []
        self.probe_calls = []

    def get_negrisk_winning_position_balance(self, condition_id, index_set, *, holder=None):
        self.probe_calls.append((condition_id, index_set))
        return self._probe_result

    def redeem(self, condition_id, *, index_sets=None, neg_risk=False, amount_per_slot=None, **_kw):
        self.redeem_calls.append({
            "condition_id": condition_id,
            "index_sets": index_sets,
            "neg_risk": neg_risk,
            "amount_per_slot": amount_per_slot,
        })
        return {"success": True, "tx_hash": "0x" + "d" * 64}


def _insert_command(conn, *, token_amount: str = "5.0") -> str:
    from src.execution.settlement_commands import request_redeem

    cmd_id = request_redeem(
        _CONDITION_ID,
        "USDC",
        market_id=_CONDITION_ID,
        # NO won -> winning_index_set ["1"]. Recorded token map keyed arbitrarily.
        token_amounts={_NO_TOKEN: token_amount},
        winning_index_set='["1"]',
        conn=conn,
        requested_at=NOW,
    )
    conn.commit()
    return cmd_id


def _patch_gates(monkeypatch, sc, world_path):
    monkeypatch.setattr(
        "src.execution.settlement_commands.redemption_decision",
        lambda: SimpleNamespace(allow_redemption=True, block_reason=None, state="LIVE_ENABLED"),
    )
    monkeypatch.setattr(
        "src.execution.settlement_commands.require_pusd_redemption_allowed",
        lambda fx: fx,
    )
    import src.state.db as _db_mod
    monkeypatch.setattr(sc, "ZEUS_WORLD_DB_PATH", pathlib.Path(world_path), raising=False)
    _db_mod.ZEUS_WORLD_DB_PATH = pathlib.Path(world_path)


def test_a1_zero_balance_is_terminal_confirmed_no_adapter_call(
    plain_trade_conn, world_db_with_snapshot, monkeypatch
):
    """A1: live balance == 0 -> REDEEM_CONFIRMED with provenance, NO redeem call.

    Sed-flip: delete the `if _live_micro <= 0` branch in submit_redeem -> adapter
    is called with the stale amount -> on real chain GS013 -> RED."""
    import src.execution.settlement_commands as sc
    import src.state.db as _db_mod
    orig_world = _db_mod.ZEUS_WORLD_DB_PATH
    _patch_gates(monkeypatch, sc, world_db_with_snapshot)
    try:
        cmd_id = _insert_command(plain_trade_conn)
        adapter = _ProbeAdapter(probe_result={
            "ok": True, "balance_micro": 0, "position_id": 12345,
            "wcol": "0x" + "1" * 40, "holder": "0x" + "2" * 40,
        })
        result = sc.submit_redeem(cmd_id, adapter, SimpleNamespace(), conn=plain_trade_conn)

        assert not adapter.redeem_calls, (
            "A1 FAIL: adapter.redeem was called despite zero live balance — "
            "would GS013 on-chain. Chain-truth terminal gate missing."
        )
        assert result.state == sc.SettlementState.REDEEM_CONFIRMED, (
            f"A1 FAIL: state={result.state}, expected REDEEM_CONFIRMED."
        )
        row = plain_trade_conn.execute(
            "SELECT state, terminal_at FROM settlement_commands WHERE command_id = ?",
            (cmd_id,),
        ).fetchone()
        assert row["state"] == "REDEEM_CONFIRMED"
        assert row["terminal_at"] is not None, "A1 FAIL: terminal_at not stamped."
        # Provenance must be recorded in the event payload.
        ev = plain_trade_conn.execute(
            "SELECT payload_json FROM settlement_command_events "
            "WHERE command_id = ? AND event_type = 'REDEEM_CONFIRMED'",
            (cmd_id,),
        ).fetchone()
        assert ev is not None and "chain_evidence" in ev["payload_json"], (
            "A1 FAIL: confirmed without chain_evidence provenance in payload."
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig_world


def test_a2_nonzero_mismatch_self_heals_to_live_balance(
    plain_trade_conn, world_db_with_snapshot, monkeypatch
):
    """A2: live balance > 0 and != recorded -> redeem uses the LIVE balance.

    Sed-flip: delete the `elif _live_micro != amount_per_slot` self-heal -> redeem
    is called with the stale recorded 5_000_000 micro instead of live 3_210_000."""
    import src.execution.settlement_commands as sc
    import src.state.db as _db_mod
    orig_world = _db_mod.ZEUS_WORLD_DB_PATH
    _patch_gates(monkeypatch, sc, world_db_with_snapshot)
    try:
        cmd_id = _insert_command(plain_trade_conn, token_amount="5.0")  # recorded 5_000_000
        adapter = _ProbeAdapter(probe_result={
            "ok": True, "balance_micro": 3_210_000, "position_id": 999,
            "wcol": "0x" + "1" * 40, "holder": "0x" + "2" * 40,
        })
        sc.submit_redeem(cmd_id, adapter, SimpleNamespace(), conn=plain_trade_conn)

        assert adapter.redeem_calls, "A2 FAIL: adapter.redeem not called for nonzero balance."
        assert adapter.redeem_calls[0]["amount_per_slot"] == 3_210_000, (
            f"A2 FAIL: amount_per_slot={adapter.redeem_calls[0]['amount_per_slot']}, "
            f"expected live 3_210_000 (self-heal), not stale recorded 5_000_000."
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig_world


def test_a3_probe_failure_is_fail_soft_proceeds_with_recorded(
    plain_trade_conn, world_db_with_snapshot, monkeypatch
):
    """A3: probe ok=False -> redeem still called with recorded amount (no block,
    no fabrication, no regression vs pre-fix behavior)."""
    import src.execution.settlement_commands as sc
    import src.state.db as _db_mod
    orig_world = _db_mod.ZEUS_WORLD_DB_PATH
    _patch_gates(monkeypatch, sc, world_db_with_snapshot)
    try:
        cmd_id = _insert_command(plain_trade_conn, token_amount="5.0")
        adapter = _ProbeAdapter(probe_result={
            "ok": False, "errorCode": "REDEEM_BALANCE_PROBE_FAILED",
            "errorMessage": "rpc timeout",
        })
        sc.submit_redeem(cmd_id, adapter, SimpleNamespace(), conn=plain_trade_conn)

        assert adapter.redeem_calls, (
            "A3 FAIL: probe failure blocked the redeem — gate must be fail-soft."
        )
        assert adapter.redeem_calls[0]["amount_per_slot"] == 5_000_000, (
            "A3 FAIL: recorded amount not used on probe failure."
        )
    finally:
        _db_mod.ZEUS_WORLD_DB_PATH = orig_world
