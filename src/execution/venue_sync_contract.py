# Created: 2026-06-11
# Last reused or audited: 2026-07-11
# Authority basis: operator directive 2026-06-11 ("cleanest STRUCTURAL fix, no patches")
#   + the dependency_db_locked live incident (riskguard DATA_DEGRADED since ~03:36Z,
#   all submissions RISK_GUARD_BLOCKED). Lock holder was the EDLI command-recovery
#   sweep (_edli_command_recovery_cycle -> reconcile_unresolved_commands), which held
#   ONE write-class trade connection across ~2m18s of venue REST I/O threaded through
#   ~15 reconcile sub-passes, starving every other zeus_trades writer.
"""Three-phase venue/DB synchronisation contract.

THE CATEGORY THIS MODULE MAKES UNCONSTRUCTABLE
----------------------------------------------
A DB connection may never be held across network I/O, and a multi-pass sweep
may never thread one long-lived connection through more than one pass.

In WAL mode (zeus_trades / zeus-world / zeus-forecasts are all WAL) a writer
blocks only other writers — but a connection in Python's default deferred
isolation mode acquires the WAL *write* lock on its first write statement and
holds it until ``commit()``. A sweep that opens one ``write_class="live"``
connection, writes once, then spends minutes doing venue REST calls before
committing, pins that write lock for the whole sweep. Every other writer
(``position_current`` upserts, the CollateralLedger heartbeat, market substrate
inserts) then logs "database is locked", and riskguard (a separate process)
fails conservative to DATA_DEGRADED and blocks all orders.

``src/execution/exchange_reconcile.py`` already solved this exact category on
2026-06-04 with its ``fresh_reconcile_snapshot`` pattern (capture all venue
surfaces OFF any write lock, then reconcile against the immutable snapshot,
enforced by ``assert_no_world_mutex_held_for_io``). This module ports that same
structural discipline to the *connection* surface (SQLite's own WAL write lock,
which the world-mutex guard does not cover) and gives it a tiny, testable API.

THE CONTRACT
------------
``run_three_phase(snapshot, network, apply, *, conn_factory, label)``

  1. SNAPSHOT phase  — opens a connection, runs ``snapshot(conn)`` (read queries
     only, by convention), and CLOSES it before returning. Milliseconds.
  2. NETWORK phase   — receives ONLY the snapshot result. It structurally has no
     connection in scope; it does the blocking venue REST I/O.
  3. APPLY phase     — opens a FRESH connection, wraps ``apply(conn, result)`` in
     ONE bounded transaction (BEGIN IMMEDIATE ... COMMIT, rollback on error),
     and closes it.

No connection object survives across the network boundary, and no connection
survives across two phases. ``run_db_only_pass`` is the degenerate form for a
pass that touches no venue surface: open -> run -> commit -> close on its own
short-lived connection, so the multi-pass sweep never threads one connection
through several passes either.

OPEN-CONNECTION REGISTRY (antibody surface)
-------------------------------------------
Every connection handed out by ``open_tracked`` (used by the default
``conn_factory``) registers itself as "open" for the life of the with-block.
``assert_no_open_connection(operation)`` raises ``ConnectionHeldAcrossIOError``
if any tracked connection is open when it is called. The contract calls it at
the top of the NETWORK phase, so an accidental "hold a connection across the
venue call" regression raises immediately, named, at the offending site — the
same located-failure posture as ``assert_no_world_mutex_held_for_io``.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

S = TypeVar("S")
R = TypeVar("R")
A = TypeVar("A")


class ConnectionHeldAcrossIOError(RuntimeError):
    """Raised when network I/O is attempted while a tracked DB connection is open.

    This is the located-failure antibody for the dependency_db_locked category:
    it turns a silent WAL-write-lock starvation wedge into an immediate, named
    error at the exact call site that held the connection across the I/O.
    """


class _CanonicalFlockedConnection:
    """Release canonical cross-DB flocks with the wrapped connection.

    Recovery uses TRADE as MAIN with WORLD attached. Price-channel uses the
    inverse SQLite layout, so ``BEGIN IMMEDIATE`` alone can reserve the two WAL
    writers in opposite orders. The outer canonical flocks make that internal
    SQLite order unobservable to concurrent writers.
    """

    def __init__(self, conn: sqlite3.Connection, context) -> None:
        self._conn = conn
        self._context = context
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._context.__exit__(None, None, None)


# Thread-local set of currently-open tracked connections. Thread-local because
# the WAL write lock is per-connection and the scheduler runs each job on its
# own thread; a connection open on thread A must not make thread B's network
# phase raise.
_open_conns = threading.local()


def _open_set() -> set:
    existing = getattr(_open_conns, "conns", None)
    if existing is None:
        existing = set()
        _open_conns.conns = existing
    return existing


def assert_no_open_connection(operation: str) -> None:
    """Fail loudly if any tracked DB connection is open on this thread.

    Wire this at the top of any network phase. ``operation`` is a short label
    for the I/O being attempted (e.g. ``"recovery.network:matched_order_facts"``)
    and appears in the error so the wedge's root site is identified.
    """
    held = _open_set()
    if held:
        raise ConnectionHeldAcrossIOError(
            f"network I/O {operation!r} attempted while {len(held)} DB "
            f"connection(s) are open on this thread — a connection must never "
            f"be held across venue/network I/O (dependency_db_locked category). "
            f"Open-connection labels: {sorted(_conn_labels(held))}"
        )


def _conn_labels(conns) -> list[str]:
    return [getattr(c, "_venue_sync_label", "<unlabelled>") for c in conns]


@contextlib.contextmanager
def open_tracked(conn_factory: Callable[[], sqlite3.Connection], *, label: str):
    """Open a connection, register it as held for the with-block, close on exit.

    The registration is what makes ``assert_no_open_connection`` able to detect a
    connection that has leaked across a network call.
    """
    conn = conn_factory()
    try:
        conn._venue_sync_label = label  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — some fakes forbid attribute set; non-fatal
        pass
    _open_set().add(conn)
    try:
        yield conn
    finally:
        _open_set().discard(conn)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            logger.warning("venue_sync_contract: close failed for %s", label, exc_info=True)


@contextlib.contextmanager
def _bounded_write(conn: sqlite3.Connection, *, label: str):
    """One bounded write transaction: BEGIN IMMEDIATE ... COMMIT (rollback on error).

    BEGIN IMMEDIATE acquires the WAL write lock up front, so the lock is held
    only for the duration of the apply writes (which contain no network I/O by
    contract) and is released by COMMIT before the connection is closed.

    Transaction-lifecycle handling mirrors ``src.state.db.world_write_lock``:
    Python's sqlite3 default ``isolation_level=""`` forbids a nested BEGIN, so we
    only issue ``BEGIN IMMEDIATE`` when the connection is not already in a
    transaction, and we commit/rollback via the Python-level handles so the
    driver's transaction state stays consistent. A fresh contract connection is
    never already in a transaction, but the guard keeps the helper correct under
    any caller.
    """
    began = False
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
        began = True
    try:
        yield conn
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            logger.warning("venue_sync_contract: rollback failed for %s", label, exc_info=True)
        raise
    finally:
        _ = began


def run_three_phase(
    snapshot: Callable[[sqlite3.Connection], S],
    network: Callable[[S], R],
    apply: Callable[[sqlite3.Connection, R], A],
    *,
    conn_factory: Callable[[], sqlite3.Connection],
    snapshot_conn_factory: Callable[[], sqlite3.Connection] | None = None,
    label: str,
) -> A:
    """Run one snapshot -> network -> apply unit with strict phase separation.

    SNAPSHOT opens+closes its own connection. NETWORK runs with NO connection in
    scope (and asserts none is open). APPLY opens a fresh connection and runs in
    one bounded BEGIN IMMEDIATE ... COMMIT transaction. No connection survives
    across the network boundary or across two phases.
    """
    read_factory = snapshot_conn_factory or conn_factory
    with open_tracked(read_factory, label=f"{label}:snapshot") as conn:
        snap = snapshot(conn)

    assert_no_open_connection(f"{label}:network")
    result = network(snap)

    with open_tracked(conn_factory, label=f"{label}:apply") as conn:
        with _bounded_write(conn, label=f"{label}:apply"):
            return apply(conn, result)


def run_db_only_pass(
    pass_fn: Callable[[sqlite3.Connection], A],
    *,
    conn_factory: Callable[[], sqlite3.Connection],
    label: str,
) -> A:
    """Run a pure-DB pass on its own short-lived connection.

    Open -> run pass(conn) in one bounded transaction -> commit -> close. The
    pass touches no venue surface, so there is no network phase; the point is
    only that the multi-pass sweep never threads one connection through more
    than one pass.
    """
    with open_tracked(conn_factory, label=f"{label}:db") as conn:
        with _bounded_write(conn, label=label):
            return pass_fn(conn)


def run_client_pass(
    pass_fn: Callable[..., A],
    *,
    conn_factory: Callable[[], sqlite3.Connection],
    client,
    label: str,
) -> A:
    """Run a client-taking reconcile pass on its own short-lived connection.

    The pass's own per-row body still owns its event grammar and savepoint
    discipline; the contract's contribution is (a) a fresh short-lived
    connection per pass (so no connection threads across passes) and (b) wrapping
    the pass body in ONE bounded BEGIN IMMEDIATE ... COMMIT, with the venue
    ``client`` proxied so that every network call first asserts no connection is
    held — turning any future "hold-across-network" regression into a located
    failure instead of a silent WAL-lock wedge.

    NOTE: this preserves a single pass's internal network/DB ordering. The
    cross-pass and cross-sweep topology (one connection per pass, released
    between passes) is what eliminates the multi-minute lock-hold that caused
    the incident; the per-pass network burst is bounded to that pass's candidate
    set, and the connection is fully released the moment the pass returns.
    """
    proxied = _NetworkAssertingClient(client, label=label)
    with open_tracked(conn_factory, label=f"{label}:client") as conn:
        with _bounded_write(conn, label=label):
            return pass_fn(conn, proxied)


class _NetworkAssertingClient:
    """Proxy that asserts no *foreign* connection is open before each venue call.

    "Foreign" = any tracked connection other than the one the current client
    pass legitimately holds. Within a single client pass the pass's own apply
    connection is necessarily open (that is the documented single-pass shape),
    so this proxy does not fire on it; it fires only if a connection from a
    *different* pass leaked into scope — the cross-pass threading the contract
    forbids.
    """

    def __init__(self, inner, *, label: str):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_label", label)

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def _wrapped(*args, **kwargs):
            held = _open_set()
            foreign = [
                c for c in held
                if getattr(c, "_venue_sync_label", "").split(":")[0] != self._label
            ]
            if foreign:
                raise ConnectionHeldAcrossIOError(
                    f"venue call {self._label}.{name} attempted while a foreign "
                    f"DB connection is open: {sorted(_conn_labels(foreign))} — a "
                    f"connection from another pass must never be held across this "
                    f"pass's network I/O (dependency_db_locked category)."
                )
            return attr(*args, **kwargs)

        return _wrapped


def default_trade_conn_factory() -> sqlite3.Connection:
    """The sanctioned live trade connection factory for the recovery sweep.

    Each short connection holds the canonical WORLD+TRADE live flocks until
    ``close()``. This prevents inverse MAIN/ATTACH layouts from deadlocking at
    ``BEGIN IMMEDIATE`` while preserving the same canonical schemas.
    """
    from src.state.db import trade_connection_with_world_flocked

    context = trade_connection_with_world_flocked(write_class="live")
    conn = context.__enter__()
    return _CanonicalFlockedConnection(conn, context)  # type: ignore[return-value]


default_trade_conn_factory.requires_writer_flocks = True  # type: ignore[attr-defined]


def default_trade_read_conn_factory() -> sqlite3.Connection:
    """Canonical attached recovery read connection without writer flocks."""

    from src.state.db import get_trade_connection_with_world_required

    return get_trade_connection_with_world_required(write_class=None)


class SnapshotMissError(RuntimeError):
    """A venue read during APPLY referenced a key the snapshot phase did not prime.

    This is a *located* failure: it means the snapshot phase under-collected, and
    the apply phase would otherwise have fallen back to live network under the
    write lock — re-introducing the dependency_db_locked category. Raising here
    forces the snapshot phase to be corrected rather than silently regressing.
    """


class VenueReadSnapshot:
    """Immutable replay of venue reads captured during the NETWORK phase.

    Built off any DB write lock (the network phase holds no connection), this
    wrapper serves the same venue surface the live client would during the APPLY
    phase WITHOUT touching the network — so a reconcile pass's UNCHANGED body
    runs against an open write connection while issuing zero real I/O. Same
    discipline as ``exchange_reconcile.fresh_reconcile_snapshot`` (2026-06-04),
    scoped to the command-recovery client surface.

    Surfaces:
      - ``get_open_orders()`` / ``get_trades()`` : account-wide, captured once.
      - ``get_order(order_id)``                  : per-order point reads, primed
        for every candidate order id collected across all passes in the snapshot
        phase. An un-primed read raises ``SnapshotMissError`` (located failure).
      - ``find_order_by_idempotency_key(key)`` / ``get_clob_market_info(cid)`` :
        primed per collected key; un-primed -> ``SnapshotMissError``.

    Non-network introspection the passes use (``__class__.__name__``,
    ``_ensure_v2_adapter`` shape, ``venue_reads_are_complete``) is served from a
    DISABLED live client whose network methods are stubbed, so an introspection
    path can never accidentally fall through to real I/O during apply.
    """

    # Network methods this snapshot fully owns (served from cache, never live).
    _OWNED_NETWORK_METHODS = frozenset(
        {"get_open_orders", "get_trades", "get_order", "find_order_by_idempotency_key", "get_clob_market_info"}
    )

    def __init__(self, *, live_client, orders, open_orders, trades, idempotency, market_info):
        object.__setattr__(self, "_live_client", live_client)
        object.__setattr__(self, "_orders", dict(orders))
        object.__setattr__(self, "_open_orders", list(open_orders) if open_orders is not None else None)
        object.__setattr__(self, "_trades", list(trades) if trades is not None else None)
        object.__setattr__(self, "_idempotency", dict(idempotency))
        object.__setattr__(self, "_market_info", dict(market_info))

    def get_open_orders(self):
        if self._open_orders is None:
            raise SnapshotMissError("get_open_orders not captured in venue snapshot")
        return list(self._open_orders)

    def get_trades(self):
        if self._trades is None:
            raise SnapshotMissError("get_trades not captured in venue snapshot")
        return list(self._trades)

    def get_order(self, order_id):
        key = str(order_id)
        if key not in self._orders:
            raise SnapshotMissError(f"get_order({key!r}) not primed in venue snapshot")
        return self._orders[key]

    def find_order_by_idempotency_key(self, key):
        k = str(key)
        if k not in self._idempotency:
            raise SnapshotMissError(f"find_order_by_idempotency_key({k!r}) not primed")
        return self._idempotency[k]

    def get_clob_market_info(self, condition_id):
        k = str(condition_id)
        if k not in self._market_info:
            raise SnapshotMissError(f"get_clob_market_info({k!r}) not primed")
        return self._market_info[k]

    @property
    def venue_reads_are_complete(self) -> bool:
        # Account surfaces were captured eagerly and fully; declare completeness
        # so absence-proof passes treat the snapshot as authoritative.
        return True

    @property
    def __class__(self):  # noqa: A003 — preserve identity so `client.__class__.__name__`
        # reads in proof-payload pagination_scope strings stay byte-identical to
        # the live path (e.g. "PolymarketClient.get_open_orders:...").
        return type(self._live_client)

    def __getattr__(self, name):
        # Only NON-network attributes reach here (owned network methods are
        # defined explicitly above). Adapter-shape / flag introspection is served
        # from the live client object — but the V2 adapter accessor is masked so
        # an introspection path can never reach the network during apply: the
        # passes' adapter branch is gated on `_ensure_v2_adapter` being callable,
        # and we deny it so they fall through to the snapshot's owned methods.
        if name == "_ensure_v2_adapter":
            raise AttributeError(name)
        return getattr(self._live_client, name)


def capture_venue_read_snapshot(
    client,
    *,
    order_ids,
    idempotency_keys=(),
    condition_ids=(),
) -> VenueReadSnapshot:
    """NETWORK phase: capture every venue read the apply phase will need.

    Runs with NO DB connection in scope (the caller asserts this). Over-priming
    is safe; under-priming raises ``SnapshotMissError`` at apply time so the gap
    is located, never silently re-introducing live-network-under-write-lock.
    """
    assert_no_open_connection("recovery.capture_venue_read_snapshot")

    venue_sources = [client]
    ensure_v2 = getattr(client, "_ensure_v2_adapter", None)
    if callable(ensure_v2):
        try:
            adapter = ensure_v2()
        except Exception:  # noqa: BLE001 — fall back to the outer client surface.
            logger.warning("venue_sync_contract: v2 adapter unavailable during snapshot", exc_info=True)
        else:
            if adapter is not client:
                venue_sources.append(adapter)

    def _safe_account(method):
        saw_callable = False
        for source in venue_sources:
            fn = getattr(source, method, None)
            if not callable(fn):
                continue
            saw_callable = True
            try:
                return list(fn() or [])
            except Exception:  # noqa: BLE001 — try the next venue source if available.
                logger.warning("venue_sync_contract: account read %s unavailable", method, exc_info=True)
        if saw_callable:
            return None
        return None

    open_orders = _safe_account("get_open_orders")
    trades = _safe_account("get_trades")

    orders: dict = {}
    get_order_source = next((getattr(source, "get_order", None) for source in venue_sources if callable(getattr(source, "get_order", None))), None)
    if callable(get_order_source):
        for oid in {str(o) for o in order_ids if str(o).strip()}:
            try:
                orders[oid] = get_order_source(oid)
            except Exception:  # noqa: BLE001 — record the miss as None (== venue not found)
                logger.warning("venue_sync_contract: get_order(%s) failed during snapshot", oid, exc_info=True)
                orders[oid] = None

    idempotency: dict = {}
    finder = next(
        (
            getattr(source, "find_order_by_idempotency_key", None)
            for source in venue_sources
            if callable(getattr(source, "find_order_by_idempotency_key", None))
        ),
        None,
    )
    if callable(finder):
        for key in {str(k) for k in idempotency_keys if str(k).strip()}:
            try:
                idempotency[key] = finder(key)
            except Exception:  # noqa: BLE001
                idempotency[key] = None

    market_info: dict = {}
    market_getter = next(
        (
            getattr(source, "get_clob_market_info", None)
            for source in venue_sources
            if callable(getattr(source, "get_clob_market_info", None))
        ),
        None,
    )
    if callable(market_getter):
        for cid in {str(c) for c in condition_ids if str(c).strip()}:
            try:
                market_info[cid] = market_getter(cid)
            except Exception:  # noqa: BLE001
                market_info[cid] = None

    return VenueReadSnapshot(
        live_client=client,
        orders=orders,
        open_orders=open_orders,
        trades=trades,
        idempotency=idempotency,
        market_info=market_info,
    )
