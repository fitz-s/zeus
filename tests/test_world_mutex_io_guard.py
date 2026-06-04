# Created: 2026-06-04
# Last reused or audited: 2026-06-04
# Authority basis: M5 exchange_reconcile lock-starvation kill — CATEGORY ANTIBODY.
#   STEP-7 (5638cf59c6) + #95 each fixed ONE "I/O under the world write lock"
#   site by hand; M5 exchange_reconcile was an uncovered third instance that
#   wedged the daemon (STAT=U, zeus-world.db-wal bloat). This antibody makes the
#   whole CATEGORY structurally detectable: any blocking venue/on-chain I/O while
#   world_write_mutex is held raises WorldMutexIOViolation at the offending site.
#
# RELATIONSHIP TEST (cross-module invariant), per Fitz methodology:
#   "When Module A (any world-DB writer) holds the world write mutex and control
#    flows into Module B (the venue HTTP client / on-chain RPC reader), the
#    property 'no blocking I/O is performed' must hold across that boundary."
#   The bare threading.Lock could not express that relationship; the guard makes
#   the violation an immediate, located exception instead of a silent wedge.
from __future__ import annotations

import threading

import pytest

from src.state.db import (
    WorldMutexIOViolation,
    assert_no_world_mutex_held_for_io,
    world_mutex_is_held,
    world_write_mutex,
)


def _reset_guard() -> None:
    """Defensive: ensure the world mutex is not left held by a prior test."""
    mutex = world_write_mutex()
    while mutex.locked():
        try:
            mutex.release()
        except RuntimeError:
            break


def test_guard_flag_tracks_world_mutex_acquire_release():
    """world_mutex_is_held() flips with the mutex's own acquire/release — the
    single source of truth the I/O assertion reads. RED before _GuardedWorldMutex
    wraps the lock (a bare threading.Lock has no held-depth flag)."""
    _reset_guard()
    mutex = world_write_mutex()
    assert world_mutex_is_held() is False
    mutex.acquire()
    try:
        assert world_mutex_is_held() is True
    finally:
        mutex.release()
    assert world_mutex_is_held() is False


def test_guard_flag_tracks_context_manager_use():
    """`with world_write_mutex():` also feeds the held flag, so the contextual
    acquire sites (and any future `with` users) are covered with no code change."""
    _reset_guard()
    mutex = world_write_mutex()
    assert world_mutex_is_held() is False
    with mutex:
        assert world_mutex_is_held() is True
    assert world_mutex_is_held() is False


def test_io_assertion_raises_when_world_mutex_held():
    """THE CORE ANTIBODY: attempting blocking I/O while the world mutex is held
    raises WorldMutexIOViolation naming the operation. This is the structural
    conversion of a silent multi-hour wedge into an immediate located failure.

    RED before the guard exists (assert_no_world_mutex_held_for_io / the held
    flag are absent); GREEN after."""
    _reset_guard()
    mutex = world_write_mutex()
    mutex.acquire()
    try:
        with pytest.raises(WorldMutexIOViolation) as excinfo:
            assert_no_world_mutex_held_for_io("venue.get_trades")
        assert "venue.get_trades" in str(excinfo.value)
    finally:
        mutex.release()


def test_io_assertion_passes_when_world_mutex_not_held():
    """The dual: with the mutex released (the correct off-lock I/O path), the
    assertion is a no-op. A reconcile cycle that pre-captures venue reads OFF the
    lock must never trip the guard."""
    _reset_guard()
    assert world_mutex_is_held() is False
    # Must not raise.
    assert_no_world_mutex_held_for_io("venue.get_trades")
    assert_no_world_mutex_held_for_io("onchain.eth_call")


def test_guard_flag_clears_after_exception_inside_lock():
    """Releasing on the exception path still clears the held flag, so a failing
    writer cannot leave the guard armed and falsely fail subsequent legitimate
    I/O. Mirrors world_write_lock's rollback-then-release discipline."""
    _reset_guard()
    mutex = world_write_mutex()
    with pytest.raises(ValueError):
        with mutex:
            assert world_mutex_is_held() is True
            raise ValueError("boom")
    assert world_mutex_is_held() is False


def test_held_flag_is_thread_local_to_the_holder_window_not_leaked():
    """The held flag reflects the global lock state: while the holder thread is
    inside the critical section, world_mutex_is_held() is True for any observer
    (the I/O assertion runs on whatever thread attempts the I/O). Confirm a
    second thread blocked on acquire does NOT see a false 'unheld' window."""
    _reset_guard()
    mutex = world_write_mutex()
    observed_held_while_holder_inside: list[bool] = []
    holder_inside = threading.Event()
    release_holder = threading.Event()

    def _holder() -> None:
        mutex.acquire()
        try:
            holder_inside.set()
            release_holder.wait(timeout=2.0)
        finally:
            mutex.release()

    t = threading.Thread(target=_holder, name="holder", daemon=True)
    t.start()
    assert holder_inside.wait(timeout=2.0)
    observed_held_while_holder_inside.append(world_mutex_is_held())
    release_holder.set()
    t.join(timeout=2.0)
    assert observed_held_while_holder_inside == [True]
    assert world_mutex_is_held() is False


# ---------------------------------------------------------------------------- #
# WIRED-ENTRYPOINT relationship tests: the venue adapter's real read methods +
# the on-chain RPC entrypoint assert the guard. These pin the antibody at the
# exact module boundary the M5 reconcile wedge crossed (refresh_unresolved_
# reconcile_findings -> adapter.get_trades -> py_clob_client httpx read).
# ---------------------------------------------------------------------------- #


def _adapter(client):
    """Construct a PolymarketV2Adapter with an injected SDK client + rpc_call so
    no real network is touched; the guard runs BEFORE either is reached."""
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    rpc_reached: list[tuple[str, list]] = []

    def _rpc_call(url, method, params):
        rpc_reached.append((method, params))
        return "0x0"

    adapter = PolymarketV2Adapter(
        funder_address="0x" + "0" * 40,
        signer_key="0x" + "1" * 64,
        polygon_rpc_url="https://example.invalid/rpc",
        rpc_call=_rpc_call,
        q1_egress_evidence_path=None,
        client_factory=lambda **_kw: client,
        sdk_version="test",
    )
    return adapter, rpc_reached


class _FakeSdkClient:
    """Records which read methods the adapter reached (i.e. got PAST the guard)."""

    def __init__(self):
        self.reached: list[str] = []

    def get_trades(self, only_first_page=False):
        self.reached.append("get_trades")
        return []

    def get_open_orders(self, only_first_page=False):
        self.reached.append("get_open_orders")
        return []

    def get_order(self, order_id):
        self.reached.append("get_order")
        return {"status": "LIVE", "order_id": order_id}


def test_adapter_get_trades_raises_under_world_mutex():
    """RED before get_trades is wired: holding the world mutex and calling the
    real adapter.get_trades() (the exact M5 reconcile read) must raise
    WorldMutexIOViolation BEFORE the SDK/network is reached."""
    _reset_guard()
    sdk = _FakeSdkClient()
    adapter, _rpc = _adapter(sdk)
    mutex = world_write_mutex()
    mutex.acquire()
    try:
        with pytest.raises(WorldMutexIOViolation):
            adapter.get_trades()
    finally:
        mutex.release()
    assert sdk.reached == [], "guard must fire BEFORE the venue SDK read is reached"


def test_adapter_get_open_orders_and_get_order_raise_under_world_mutex():
    """The other reconcile-relevant venue reads are guarded identically."""
    _reset_guard()
    sdk = _FakeSdkClient()
    adapter, _rpc = _adapter(sdk)
    mutex = world_write_mutex()
    mutex.acquire()
    try:
        with pytest.raises(WorldMutexIOViolation):
            adapter.get_open_orders()
        with pytest.raises(WorldMutexIOViolation):
            adapter.get_order("ord-1")
    finally:
        mutex.release()
    assert sdk.reached == []


def test_adapter_reads_proceed_when_world_mutex_not_held():
    """REGRESSION: off the lock (the correct pre-capture path), the venue reads
    pass the guard and reach the SDK — the antibody does not break legitimate
    off-lock reconcile I/O."""
    _reset_guard()
    sdk = _FakeSdkClient()
    adapter, _rpc = _adapter(sdk)
    assert world_mutex_is_held() is False
    adapter.get_trades()
    adapter.get_open_orders()
    adapter.get_order("ord-1")
    assert sdk.reached == ["get_trades", "get_open_orders", "get_order"]


def test_onchain_rpc_raises_under_world_mutex():
    """The single on-chain RPC entrypoint (_json_rpc_call) is guarded: an
    eth_call while the world mutex is held raises rather than wedging."""
    _reset_guard()
    from src.venue.polymarket_v2_adapter import _json_rpc_call

    mutex = world_write_mutex()
    mutex.acquire()
    try:
        with pytest.raises(WorldMutexIOViolation):
            _json_rpc_call("https://example.invalid/rpc", "eth_call", [])
    finally:
        mutex.release()
