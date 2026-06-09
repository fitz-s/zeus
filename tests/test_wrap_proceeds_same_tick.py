# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator redeem directive 2026-06-09 (the wrap must trigger
#   in the SAME tick that confirms redemptions — proceeds-driven, not a slow
#   separate balance poll; a confirmed redemption batch leaves ZERO USDC.e
#   unwrapped after the same tick completes).
# Lifecycle: created=2026-06-09; last_reviewed=2026-06-09; last_reused=never
# Purpose: Antibody — wrap_proceeds_now drives enqueue + APPROVE + WRAP +
#   confirm synchronously in one call; the redeem submitter and reconciler
#   ticks invoke it (wiring pinned).
# Reuse: Run when modifying wrap_proceeds_now, _wrap_proceeds_same_tick, the
#   _redeem_submitter_cycle / _redeem_reconciler_cycle wiring, or the wrap
#   state machine.
"""Antibody tests for the same-tick proceeds-driven wrap.

Root cause (2026-06-09): the periodic wrap state machine (intent creator /
submitter / reconciler at 5-min ticks) advanced ~one step per tick, so fresh
redemption proceeds sat as unwrapped USDC.e for up to ~25 minutes ("Confirm
pending deposit" in the Polymarket UI) after every redemption batch.

Contracts (sed-flip verifiable):
  T1 (the operator antibody): proceeds above threshold at call time -> after
      wrap_proceeds_now RETURNS, the wrap row is WRAP_CONFIRMED and the
      simulated Safe USDC.e balance is ZERO. One call, no extra ticks.
  T2: a mid-flight WRAP_APPROVED row (stranded by the old per-tick machine) is
      driven to WRAP_CONFIRMED in the same call.
  T3: reverted tx -> WRAP_FAILED (terminal, honest), not silent success.
  T4: balance below threshold + no pending rows -> no enqueue, no Safe txs.
  W2 (wiring): _redeem_reconciler_cycle invokes the same-tick wrap after a
      REDEEM_CONFIRMED batch.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest


_SAFE = "0x6a096d5042cba434521E2cdb95A1fBa789a09b7f"
_EOA = "0xB19Ce122089237025aD046a0eA61E66a5Fa4cc8b"


@pytest.fixture()
def world_conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    yield db
    db.close()


class _FakeWrapAdapter:
    """Simulates the Safe + chain for the wrap flow.

    USDC.e balance is held in self.balance_micro; a successful WRAP step zeroes
    it (the onramp consumed the USDC.e), mirroring chain behavior.
    """

    def __init__(self, *, balance_micro: int, revert_kind: str | None = None):
        self.balance_micro = balance_micro
        self.revert_kind = revert_kind  # "APPROVE" | "WRAP" | None
        self.polygon_rpc_url = "https://rpc.example"
        self.wrap_calls = []
        self._tx_n = 0
        self._tx_status: dict[str, str] = {}
        self.balance_refreshed = 0

    def _rpc_call(self, url, method, params):
        if method == "eth_call":
            data = params[0]["data"]
            if data.startswith("0x70a08231"):  # balanceOf
                return "0x" + format(self.balance_micro, "064x")
            raise AssertionError(f"unexpected eth_call {data[:10]}")
        if method == "eth_getTransactionReceipt":
            tx = params[0]
            return {"status": self._tx_status.get(tx, "0x1"), "blockNumber": "0x5429b4"}
        raise AssertionError(f"unexpected rpc {method}")

    def _wrap_via_safe(self, *, safe_address, amount_micro, tx_kind, signer_eoa):
        self.wrap_calls.append(tx_kind)
        self._tx_n += 1
        tx = "0x" + format(self._tx_n, "064x")
        if self.revert_kind == tx_kind:
            self._tx_status[tx] = "0x0"  # mined but reverted
            return {"success": True, "tx_hash": tx}
        self._tx_status[tx] = "0x1"
        if tx_kind == "WRAP":
            # Onramp consumed the USDC.e: balance goes to zero on-chain.
            self.balance_micro = 0
        return {"success": True, "tx_hash": tx}

    def update_balance_allowance(self, _arg):
        self.balance_refreshed += 1


def test_t1_confirmed_batch_leaves_zero_usdce_same_call(world_conn):
    """T1 (operator antibody): after ONE wrap_proceeds_now call, the proceeds
    are fully wrapped — WRAP_CONFIRMED row, ZERO USDC.e left, CLOB refreshed.

    Sed-flip: remove the synchronous step-drive loop (leave enqueue only) ->
    the row stays WRAP_REQUESTED and balance stays nonzero -> RED."""
    from src.execution.wrap_unwrap_commands import wrap_proceeds_now

    adapter = _FakeWrapAdapter(balance_micro=857_476_847)
    out = wrap_proceeds_now(
        world_conn, adapter, _SAFE, _EOA, poll_interval_s=0.0,
    )
    assert out["enqueued"] is not None, "T1 FAIL: proceeds not enqueued."
    assert out["confirmed"] == [out["enqueued"]], (
        f"T1 FAIL: wrap not driven to CONFIRMED in the same call: {out}"
    )
    assert adapter.wrap_calls == ["APPROVE", "WRAP"], (
        f"T1 FAIL: expected APPROVE then WRAP, got {adapter.wrap_calls}"
    )
    assert adapter.balance_micro == 0, (
        "T1 FAIL: USDC.e left unwrapped after the tick completed."
    )
    row = world_conn.execute(
        "SELECT state, amount_micro FROM wrap_unwrap_commands WHERE command_id = ?",
        (out["enqueued"],),
    ).fetchone()
    assert row["state"] == "WRAP_CONFIRMED"
    assert row["amount_micro"] == 857_476_847
    assert adapter.balance_refreshed == 1, "T1 FAIL: CLOB balance not refreshed."
    assert out["pending"] == [], "T1 FAIL: pending wrap rows remain."


def test_t2_stranded_mid_flight_row_driven_to_confirmed(world_conn):
    """T2: a WRAP_APPROVED row stranded by the old per-tick machine (the
    d6d2e6f0 case) completes in one call."""
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        init_wrap_unwrap_schema,
        wrap_proceeds_now,
    )

    init_wrap_unwrap_schema(world_conn)
    world_conn.execute(
        "INSERT INTO wrap_unwrap_commands (command_id, state, direction, amount_micro, requested_at) "
        "VALUES ('stranded1', ?, 'WRAP', 5000000, '2026-06-09T22:48:34')",
        (WrapUnwrapState.WRAP_APPROVED.value,),
    )
    world_conn.commit()
    adapter = _FakeWrapAdapter(balance_micro=5_000_000)
    out = wrap_proceeds_now(world_conn, adapter, _SAFE, _EOA, poll_interval_s=0.0)
    assert "stranded1" in out["confirmed"], f"T2 FAIL: stranded row not completed: {out}"
    assert adapter.wrap_calls == ["WRAP"], (
        "T2 FAIL: WRAP_APPROVED row must go straight to the WRAP step."
    )
    assert adapter.balance_micro == 0


def test_t3_reverted_wrap_marks_failed(world_conn):
    from src.execution.wrap_unwrap_commands import wrap_proceeds_now

    adapter = _FakeWrapAdapter(balance_micro=5_000_000, revert_kind="WRAP")
    out = wrap_proceeds_now(world_conn, adapter, _SAFE, _EOA, poll_interval_s=0.0)
    assert out["enqueued"] is not None
    assert out["enqueued"] in out["failed"], (
        f"T3 FAIL: reverted WRAP tx not marked failed: {out}"
    )
    row = world_conn.execute(
        "SELECT state FROM wrap_unwrap_commands WHERE command_id = ?",
        (out["enqueued"],),
    ).fetchone()
    assert row["state"] == "WRAP_FAILED"


def test_t4_below_threshold_is_noop(world_conn):
    from src.execution.wrap_unwrap_commands import wrap_proceeds_now

    adapter = _FakeWrapAdapter(balance_micro=50_000)  # below $0.10 default
    out = wrap_proceeds_now(world_conn, adapter, _SAFE, _EOA, poll_interval_s=0.0)
    assert out["enqueued"] is None
    assert adapter.wrap_calls == [], "T4 FAIL: Safe tx sent below threshold."


def test_w2_reconciler_cycle_triggers_same_tick_wrap(monkeypatch):
    """W2 (wiring): a REDEEM_CONFIRMED batch from reconcile_pending_redeems
    makes _redeem_reconciler_cycle invoke _wrap_proceeds_same_tick.

    Sed-flip: delete the same-tick wrap call in the reconciler -> RED."""
    import src.main as main_mod
    from src.execution.settlement_commands import SettlementState, SettlementResult

    monkeypatch.setattr("src.main.get_mode", lambda: "live")

    @contextmanager
    def _fake_lock(name):
        yield True

    import src.data.dual_run_lock as _lock_mod
    monkeypatch.setattr(_lock_mod, "acquire_lock", _fake_lock)

    class _FakeConn:
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass

    import src.state.db as db_mod
    monkeypatch.setattr(db_mod, "get_trade_connection", lambda **_kw: _FakeConn())

    # One TX_HASHED row exists so the cycle proceeds to reconcile.
    import src.execution.settlement_commands as sc
    monkeypatch.setattr(sc, "list_commands", lambda conn, state=None: [{"command_id": "x"}])
    confirmed = SettlementResult("cmdX", SettlementState.REDEEM_CONFIRMED, tx_hash="0x" + "a" * 64)
    monkeypatch.setattr(sc, "reconcile_pending_redeems", lambda w3, conn: [confirmed])

    # Fake web3 (imported inside the cycle).
    _web3_mod = pytest.importorskip("web3")

    class _FakeWeb3:
        class HTTPProvider:
            def __init__(self, *a, **kw): pass
        def __init__(self, *a, **kw): pass
        eth = SimpleNamespace()

    monkeypatch.setattr(_web3_mod, "Web3", _FakeWeb3)

    # Fake creds + adapter construction inside the confirmed-batch branch.
    import src.data.polymarket_client as pc
    monkeypatch.setattr(
        pc, "resolve_polymarket_credentials",
        lambda: {"private_key": "0x" + "1" * 64, "funder_address": _SAFE},
    )
    monkeypatch.setattr(pc, "_resolve_clob_v2_signature_type", lambda: 2)
    import src.venue.polymarket_v2_adapter as va
    monkeypatch.setattr(va, "PolymarketV2Adapter", lambda **kw: SimpleNamespace(**kw))

    calls = []
    monkeypatch.setattr(
        main_mod, "_wrap_proceeds_same_tick", lambda creds, adapter: calls.append(1)
    )
    monkeypatch.setattr("src.main._write_scheduler_health", lambda *a, **kw: None)

    main_mod._redeem_reconciler_cycle()
    assert calls, (
        "W2 FAIL: _redeem_reconciler_cycle did not invoke the same-tick wrap "
        "after a REDEEM_CONFIRMED batch — proceeds would sit unwrapped again."
    )
