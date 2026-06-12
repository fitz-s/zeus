# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: operator redeem directive 2026-06-09 (the wrap must trigger
#   in the SAME tick that confirms redemptions — proceeds-driven, not a slow
#   separate balance poll) + operator P0 brief 2026-06-09 (P0-1 re-read gap,
#   P0-2 non-atomic state machine, P0-3 synchronous chain-wait in scheduler).
# Lifecycle: created=2026-06-09; last_reviewed=2026-06-09; last_reused=2026-06-09
# Purpose: Antibody — wrap_proceeds_now enqueues + submits + bounded-receipt-
#   checks every pending wrap so NO USDC.e above threshold is left UNCOMMITTED
#   after the tick, with a CAS state machine (no illegal reversion) and a
#   bounded wall-clock (no multi-minute synchronous chain wait in a scheduler
#   job). The redeem submitter and reconciler ticks invoke it (wiring pinned).
# Reuse: Run when modifying wrap_proceeds_now, _wrap_proceeds_same_tick, the
#   _redeem_submitter_cycle / _redeem_reconciler_cycle wiring, the wrap state
#   machine, or _transition's CAS guard.
"""Antibody tests for the same-tick proceeds-driven wrap.

Root cause (2026-06-09): the periodic wrap state machine (intent creator /
submitter / reconciler at 5-min ticks) advanced ~one step per tick, so fresh
redemption proceeds sat as unwrapped USDC.e for up to ~25 minutes ("Confirm
pending deposit" in the Polymarket UI) after every redemption batch.

Contracts (sed-flip verifiable):
  T1 (the operator antibody, honest form): proceeds above threshold at call
      time -> after wrap_proceeds_now RETURNS, NO USDC.e above threshold is
      UNCOMMITTED. With a fast receipt the row is WRAP_CONFIRMED and balance is
      ZERO in one call; with a slow receipt it is left *_TX_HASHED (committed to
      the pipeline) for the fast reconciler. Either way: not naked.
  T2: a mid-flight WRAP_APPROVED row (stranded by the old per-tick machine) is
      driven forward in the same call.
  T3: reverted tx -> WRAP_FAILED (terminal, honest), not silent success, and
      NO re-enqueue storm against the same reverting balance.
  T4: balance below threshold + no pending rows -> no enqueue, no Safe txs.
  P0_1: small stale pending row + large fresh proceeds -> after the call NO
      USDC.e above threshold is UNCOMMITTED (a NEW row exists for the residual).
  P0_2: a stale reconciler read interleaved with a same-tick confirm cannot
      revert the terminal WRAP_CONFIRMED row (CAS rejected + logged).
  P0_3: wrap_proceeds_now wall-time is bounded even when the receipt never
      lands (slow chain) — the row is left TX_HASHED for the reconciler.
  W2 (wiring): _redeem_reconciler_cycle invokes the same-tick wrap after a
      REDEEM_CONFIRMED batch.
"""

from __future__ import annotations

import sqlite3
import time
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

    receipt_available: if False, eth_getTransactionReceipt returns None forever
    (simulates a slow chain) so the short-receipt check times out and the row is
    left TX_HASHED — exercises P0-3.
    """

    def __init__(
        self,
        *,
        balance_micro: int,
        revert_kind: str | None = None,
        receipt_available: bool = True,
    ):
        self.balance_micro = balance_micro
        self.revert_kind = revert_kind  # "APPROVE" | "WRAP" | None
        self.receipt_available = receipt_available
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
            if not self.receipt_available:
                return None
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
    """T1 (operator antibody): with a fast receipt, after ONE wrap_proceeds_now
    call the proceeds are fully wrapped — WRAP_CONFIRMED row, ZERO USDC.e left,
    CLOB refreshed.

    Sed-flip: remove the synchronous step-drive loop (leave enqueue only) ->
    the row stays WRAP_REQUESTED and balance stays nonzero -> RED."""
    from src.execution.wrap_unwrap_commands import wrap_proceeds_now

    adapter = _FakeWrapAdapter(balance_micro=857_476_847)
    out = wrap_proceeds_now(
        world_conn, adapter, _SAFE, _EOA, poll_interval_s=0.0,
    )
    assert out["enqueued"], "T1 FAIL: proceeds not enqueued."
    cid = out["enqueued"][0]
    assert out["confirmed"] == [cid], (
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
        (cid,),
    ).fetchone()
    assert row["state"] == "WRAP_CONFIRMED"
    assert row["amount_micro"] == 857_476_847
    assert adapter.balance_refreshed == 1, "T1 FAIL: CLOB balance not refreshed."
    assert out["pending"] == [], "T1 FAIL: pending wrap rows remain."


def test_t2_stranded_mid_flight_row_driven_forward(world_conn):
    """T2: a WRAP_APPROVED row stranded by the old per-tick machine (the
    d6d2e6f0 case) advances in one call."""
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


def test_t3_reverted_wrap_marks_failed_no_storm(world_conn):
    """T3: a reverted WRAP tx is marked WRAP_FAILED (terminal, honest) and the
    P0-1 re-read loop does NOT re-enqueue against the same residual balance
    (anti-storm: re-driving a persistently reverting wrap just burns gas)."""
    from src.execution.wrap_unwrap_commands import wrap_proceeds_now

    adapter = _FakeWrapAdapter(balance_micro=5_000_000, revert_kind="WRAP")
    out = wrap_proceeds_now(world_conn, adapter, _SAFE, _EOA, poll_interval_s=0.0)
    assert out["enqueued"], f"T3 FAIL: nothing enqueued: {out}"
    cid = out["enqueued"][0]
    assert cid in out["failed"], (
        f"T3 FAIL: reverted WRAP tx not marked failed: {out}"
    )
    row = world_conn.execute(
        "SELECT state FROM wrap_unwrap_commands WHERE command_id = ?",
        (cid,),
    ).fetchone()
    assert row["state"] == "WRAP_FAILED"
    # Anti-storm: exactly one wrap intent enqueued, not a retry storm.
    assert len(out["enqueued"]) == 1, (
        f"T3 FAIL: re-enqueue storm against reverting balance: {out['enqueued']}"
    )
    # APPROVE then the reverting WRAP — and then STOP (no second APPROVE).
    assert adapter.wrap_calls == ["APPROVE", "WRAP"], (
        f"T3 FAIL: expected exactly APPROVE,WRAP then stop; got {adapter.wrap_calls}"
    )


def test_t4_below_threshold_is_noop(world_conn):
    from src.execution.wrap_unwrap_commands import wrap_proceeds_now

    adapter = _FakeWrapAdapter(balance_micro=50_000)  # below $0.10 default
    out = wrap_proceeds_now(world_conn, adapter, _SAFE, _EOA, poll_interval_s=0.0)
    assert out["enqueued"] == [], f"T4 FAIL: enqueued below threshold: {out}"
    assert adapter.wrap_calls == [], "T4 FAIL: Safe tx sent below threshold."


def test_p0_1_stale_small_pending_plus_large_proceeds_leaves_nothing_naked(world_conn):
    """P0-1 (RE-READ GAP antibody): a small stale WRAP_REQUESTED pending row
    (amount=5 USDC.e) co-exists with large fresh proceeds (805 USDC.e) on the
    Safe. After the call, NO USDC.e above threshold is UNCOMMITTED — the small
    row is driven AND a NEW row is enqueued for the residual proceeds.

    Sed-flip: remove the P0-1 re-read enqueue loop -> only the stale 5-USDC.e
    row drives, the 800-USDC.e residual stays naked (no row) -> RED."""
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        init_wrap_unwrap_schema,
        wrap_proceeds_now,
    )

    init_wrap_unwrap_schema(world_conn)
    # Stale small pending row: amount 5 USDC.e, but the Safe actually holds 805.
    world_conn.execute(
        "INSERT INTO wrap_unwrap_commands (command_id, state, direction, amount_micro, requested_at) "
        "VALUES ('stale_small', ?, 'WRAP', 5000000, '2026-06-09T20:00:00')",
        (WrapUnwrapState.WRAP_REQUESTED.value,),
    )
    world_conn.commit()

    # Adapter where WRAP does NOT auto-zero (a 5-USDC.e wrap can't consume 805).
    class _PartialAdapter(_FakeWrapAdapter):
        def _wrap_via_safe(self, *, safe_address, amount_micro, tx_kind, signer_eoa):
            self.wrap_calls.append((tx_kind, amount_micro))
            self._tx_n += 1
            tx = "0x" + format(self._tx_n, "064x")
            self._tx_status[tx] = "0x1"
            if tx_kind == "WRAP":
                # The onramp consumes exactly amount_micro of USDC.e.
                self.balance_micro = max(0, self.balance_micro - amount_micro)
            return {"success": True, "tx_hash": tx}

    adapter = _PartialAdapter(balance_micro=805_000_000)
    out = wrap_proceeds_now(world_conn, adapter, _SAFE, _EOA, poll_interval_s=0.0)

    # The stale small row was driven (5 USDC.e wrapped) AND a new row enqueued
    # for the ~800-USDC.e residual.
    assert "stale_small" in out["confirmed"], (
        f"P0-1 FAIL: stale small pending row not driven: {out}"
    )
    assert len(out["enqueued"]) >= 1, (
        f"P0-1 FAIL: residual proceeds left UNCOMMITTED (no new row): {out}"
    )
    # Honest antibody: NO USDC.e above threshold is UNCOMMITTED after the tick.
    # Every micro-dollar of balance is either wrapped (balance below threshold)
    # OR covered by a pending/committed row.
    threshold = 100_000
    total_pending_amount = 0
    for r in world_conn.execute(
        "SELECT amount_micro FROM wrap_unwrap_commands "
        "WHERE state NOT IN ('WRAP_CONFIRMED','WRAP_FAILED','UNWRAP_CONFIRMED','UNWRAP_FAILED')"
    ).fetchall():
        total_pending_amount += r["amount_micro"]
    naked = adapter.balance_micro - total_pending_amount
    assert naked <= threshold, (
        f"P0-1 FAIL: {naked} micro-USDC.e left UNCOMMITTED above threshold "
        f"(balance={adapter.balance_micro}, committed={total_pending_amount})"
    )


def test_p0_2_stale_reconciler_read_cannot_revert_confirmed(world_conn):
    """P0-2 (CONCURRENCY antibody): interleave a stale reconciler read with a
    same-tick confirm — the final state stays WRAP_CONFIRMED, the event log
    contains NO illegal reversion, and the rejected CAS is visible (logged).

    Simulates: reconciler snapshots a row at WRAP_APPROVE_TX_HASHED, then the
    same-tick path drives it APPROVE_TX_HASHED -> APPROVED -> ... -> CONFIRMED,
    then the stale reconciler tries mark_wrap_approved() from its old snapshot.
    The CAS rejects (CONFIRMED is terminal, not a legal predecessor of
    APPROVED), the row is NOT reverted, and the rejection is recorded.

    Sed-flip: drop the AND state IN (...) CAS predicate from _transition ->
    mark_wrap_approved reverts the terminal CONFIRMED row -> RED."""
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        WrapTransitionRejected,
        confirm_wrap,
        init_wrap_unwrap_schema,
        mark_wrap_approve_tx_hashed,
        mark_wrap_approved,
        mark_wrap_tx_hashed,
    )

    init_wrap_unwrap_schema(world_conn)
    world_conn.execute(
        "INSERT INTO wrap_unwrap_commands (command_id, state, direction, amount_micro, requested_at) "
        "VALUES ('race1', ?, 'WRAP', 5000000, '2026-06-09T20:00:00')",
        (WrapUnwrapState.WRAP_REQUESTED.value,),
    )
    world_conn.commit()

    # Same-tick path drives the row to terminal CONFIRMED.
    mark_wrap_approve_tx_hashed("race1", "0x" + "a" * 64, conn=world_conn)
    # Reconciler took its snapshot HERE (state == WRAP_APPROVE_TX_HASHED).
    mark_wrap_approved("race1", conn=world_conn)
    mark_wrap_tx_hashed("race1", "0x" + "b" * 64, conn=world_conn)
    confirm_wrap("race1", confirmation_count=1, conn=world_conn)
    world_conn.commit()
    assert world_conn.execute(
        "SELECT state FROM wrap_unwrap_commands WHERE command_id='race1'"
    ).fetchone()["state"] == WrapUnwrapState.WRAP_CONFIRMED.value

    # Stale reconciler now acts on its OLD snapshot: mark_wrap_approved again.
    # CAS must reject (CONFIRMED is not a legal predecessor of APPROVED).
    with pytest.raises(WrapTransitionRejected):
        mark_wrap_approved("race1", conn=world_conn)
    world_conn.commit()

    # The terminal state survived — NO illegal reversion.
    final = world_conn.execute(
        "SELECT state FROM wrap_unwrap_commands WHERE command_id='race1'"
    ).fetchone()
    assert final["state"] == WrapUnwrapState.WRAP_CONFIRMED.value, (
        f"P0-2 FAIL: terminal CONFIRMED row was reverted to {final['state']}"
    )

    # The rejected CAS is visible in the event log.
    events = [
        r["event_type"]
        for r in world_conn.execute(
            "SELECT event_type FROM wrap_unwrap_events WHERE command_id='race1' "
            "ORDER BY id"
        ).fetchall()
    ]
    assert any(e.startswith("CAS_REJECTED") for e in events), (
        f"P0-2 FAIL: rejected CAS not recorded in event log: {events}"
    )
    # And no event records an illegal WRAP_APPROVED AFTER WRAP_CONFIRMED.
    confirmed_idx = events.index(WrapUnwrapState.WRAP_CONFIRMED.value)
    after_confirmed = events[confirmed_idx + 1:]
    assert WrapUnwrapState.WRAP_APPROVED.value not in after_confirmed, (
        f"P0-2 FAIL: illegal WRAP_APPROVED recorded after WRAP_CONFIRMED: {events}"
    )


def test_p0_3_bounded_walltime_when_receipt_never_lands(world_conn):
    """P0-3 (bounded-walltime antibody): when the receipt never lands (slow
    chain), wrap_proceeds_now returns within a small budget and leaves the row
    in a *_TX_HASHED state for the periodic wrap_reconciler — it does NOT block
    the scheduler job for minutes.

    Sed-flip: restore the 120s synchronous chain-wait -> this call blocks far
    past the budget -> RED (wall-time assertion fails)."""
    from src.execution.wrap_unwrap_commands import wrap_proceeds_now

    adapter = _FakeWrapAdapter(balance_micro=5_000_000, receipt_available=False)
    t0 = time.monotonic()
    out = wrap_proceeds_now(
        world_conn, adapter, _SAFE, _EOA,
        total_budget_s=2.0, receipt_check_s=1.0, poll_interval_s=0.1,
    )
    elapsed = time.monotonic() - t0
    # Bounded: well under the old 120s; allow generous slack for CI jitter.
    assert elapsed < 10.0, f"P0-3 FAIL: wall-time {elapsed:.1f}s exceeds budget."

    # The row is committed to the pipeline in a *_TX_HASHED state — NOT failed,
    # NOT naked. The reconciler will finalize it.
    assert out["enqueued"], f"P0-3 FAIL: nothing enqueued: {out}"
    assert out["confirmed"] == [], "P0-3 FAIL: confirmed without a receipt."
    assert out["failed"] == [], "P0-3 FAIL: marked failed on a slow (not reverted) tx."
    states = {
        r["state"]
        for r in world_conn.execute(
            "SELECT state FROM wrap_unwrap_commands"
        ).fetchall()
    }
    assert states & {"WRAP_APPROVE_TX_HASHED", "WRAP_TX_HASHED"}, (
        f"P0-3 FAIL: row not left in a TX_HASHED state for the reconciler: {states}"
    )


def test_p0_3_legacy_mined_timeout_is_clamped(world_conn):
    """P0-3: a legacy caller passing mined_timeout_s=120 must NOT cause a 120s
    block — it is clamped to the short per-tx receipt check."""
    from src.execution.wrap_unwrap_commands import wrap_proceeds_now

    adapter = _FakeWrapAdapter(balance_micro=5_000_000, receipt_available=False)
    t0 = time.monotonic()
    wrap_proceeds_now(
        world_conn, adapter, _SAFE, _EOA,
        mined_timeout_s=120.0, receipt_check_s=1.0,
        total_budget_s=2.0, poll_interval_s=0.1,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0, (
        f"P0-3 FAIL: legacy mined_timeout_s=120 not clamped (wall-time {elapsed:.1f}s)."
    )


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
