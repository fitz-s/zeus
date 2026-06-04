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
    """THREAD-LOCAL SEMANTICS (2026-06-04 P1 fix): world_mutex_is_held() returns
    True ONLY in the thread that acquired the mutex. An observer thread that has
    NOT acquired the mutex sees False even while the holder is inside the critical
    section. This is the correct property: background I/O threads are NOT
    violating the "no I/O under the mutex" contract just because some OTHER thread
    holds it.

    This test was previously documenting the WRONG cross-thread behaviour (observer
    saw True). After the thread-local fix the observer correctly sees False.
    """
    _reset_guard()
    mutex = world_write_mutex()
    observer_saw_held: list[bool] = []
    holder_saw_held: list[bool] = []
    holder_inside = threading.Event()
    release_holder = threading.Event()

    def _holder() -> None:
        mutex.acquire()
        try:
            holder_saw_held.append(world_mutex_is_held())  # holder's own view: True
            holder_inside.set()
            release_holder.wait(timeout=2.0)
        finally:
            mutex.release()

    t = threading.Thread(target=_holder, name="holder", daemon=True)
    t.start()
    assert holder_inside.wait(timeout=2.0)
    # Observer thread (main) has NOT acquired the mutex → must see False
    observer_saw_held.append(world_mutex_is_held())
    release_holder.set()
    t.join(timeout=2.0)
    assert holder_saw_held == [True], "holder must see its own acquisition as held"
    assert observer_saw_held == [False], (
        "observer thread must NOT see the holder's acquisition — thread-local "
        "semantics: only the acquiring thread's held flag is True"
    )
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


# ---------------------------------------------------------------------------
# 5th instance — PolymarketClient.get_orderbook_snapshot (the live WAL offender)
# ---------------------------------------------------------------------------


def test_polymarket_client_get_orderbook_snapshot_raises_under_world_mutex():
    """CATEGORY CLOSE — 5th instance. PolymarketClient.get_orderbook_snapshot is
    the call path that caused the 488→601 MB zeus-world.db WAL bloat:
    market_channel_ingestor.on_connect→seed_from_rest→fetch_orderbook (REST /book)
    while holding with _world_mutex.

    After adding the guard to get_orderbook_snapshot, holding the world mutex and
    calling it must raise WorldMutexIOViolation BEFORE any HTTP socket is touched.
    """
    _reset_guard()
    from unittest.mock import MagicMock, patch

    from src.data.polymarket_client import PolymarketClient

    client = PolymarketClient.__new__(PolymarketClient)

    mutex = world_write_mutex()
    mutex.acquire()
    try:
        with pytest.raises(WorldMutexIOViolation):
            client.get_orderbook_snapshot("0xdeadbeef")
    finally:
        mutex.release()


def test_polymarket_client_get_orderbook_snapshot_guard_not_raised_off_mutex():
    """REGRESSION: off the lock, get_orderbook_snapshot passes the guard (no
    WorldMutexIOViolation).  The call will fail at the network layer (no real
    CLOB connection in tests), but the guard itself must be silent — proving the
    antibody does not break legitimate off-lock snapshot fetches."""
    _reset_guard()
    from unittest.mock import MagicMock, patch

    from src.data.polymarket_client import PolymarketClient

    client = PolymarketClient.__new__(PolymarketClient)
    # Patch _public_get so we never touch the network in CI — we only want to
    # confirm the guard does NOT fire (the mock result replaces the HTTP layer).
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json.return_value = {"asks": [], "bids": [], "asset_id": "0xabc"}

    assert world_mutex_is_held() is False
    with patch.object(client, "_public_get", return_value=fake_resp):
        result = client.get_orderbook_snapshot("0xabc")
    assert isinstance(result, dict)
    # Guard must not have fired — if it had, WorldMutexIOViolation would have
    # been raised before we reached _public_get.


# ---------------------------------------------------------------------------
# Thread-local semantics — background-thread I/O correctness
# ---------------------------------------------------------------------------


def test_background_thread_io_not_flagged_when_reactor_thread_holds_mutex():
    """RELATIONSHIP TEST (Phase 1 / thread-local fix 2026-06-04): the false-positive
    root cause of 283× advisory/2min in prod was the process-global held depth.
    When Thread A (reactor/emit) holds the world mutex, Thread B (venue background
    maintenance) called get_open_orders → assert_no_world_mutex_held_for_io →
    world_mutex_is_held() returned True (reading Thread A's flag) → advisory fired
    → WAL held → 2.9 GB bloat.

    After the thread-local fix: Thread B's world_mutex_is_held() returns False
    while Thread A holds the mutex. Background I/O is NOT a violation.

    This test verifies the cross-thread isolation directly:
    - Thread A holds the world mutex (reactor role)
    - Thread B calls assert_no_world_mutex_held_for_io (venue maintenance role)
    - Thread B must NOT raise WorldMutexIOViolation
    """
    _reset_guard()
    mutex = world_write_mutex()
    errors_in_background: list[Exception] = []
    holder_inside = threading.Event()
    background_done = threading.Event()
    release_holder = threading.Event()

    def _holder() -> None:
        mutex.acquire()
        try:
            holder_inside.set()
            release_holder.wait(timeout=2.0)
        finally:
            mutex.release()

    def _background_io() -> None:
        """Simulates venue background maintenance thread doing I/O."""
        try:
            holder_inside.wait(timeout=2.0)
            # This is the guard call that was falsely raising in production.
            # After thread-local fix it must be silent (the background thread
            # does NOT hold the mutex).
            assert_no_world_mutex_held_for_io("background.venue_maintenance_io")
        except Exception as exc:
            errors_in_background.append(exc)
        finally:
            background_done.set()

    t_holder = threading.Thread(target=_holder, name="reactor", daemon=True)
    t_bg = threading.Thread(target=_background_io, name="bg-maintenance", daemon=True)
    t_holder.start()
    t_bg.start()
    assert background_done.wait(timeout=3.0), "background thread did not complete"
    release_holder.set()
    t_holder.join(timeout=2.0)
    t_bg.join(timeout=2.0)

    assert errors_in_background == [], (
        f"Background thread raised unexpectedly (cross-thread false positive "
        f"still present): {errors_in_background}"
    )


def test_same_thread_io_still_raises_with_thread_local_fix():
    """Regression: the thread-local fix must NOT disable the guard for the
    same-thread case. If the CALLING thread holds the mutex AND tries to do I/O,
    WorldMutexIOViolation must still raise — this is the actual disease."""
    _reset_guard()
    mutex = world_write_mutex()
    mutex.acquire()
    try:
        with pytest.raises(WorldMutexIOViolation):
            assert_no_world_mutex_held_for_io("same_thread.venue_call_under_mutex")
    finally:
        mutex.release()
