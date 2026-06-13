# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: t0_live_problem_report_2026-06-13 T0-1 + dimension-#4
#   conn-across-IO. RELATIONSHIP test of the _tick_once boundary between the
#   bankroll NETWORK fetch and the write-class DB connection opens.
#
# THE INVARIANT (RED-on-revert): in riskguard._tick_once, the bankroll fetch
# (bankroll_provider.current(), which on a stale 30s cache performs a Polymarket
# wallet NETWORK fetch) MUST happen BEFORE either write-class DB connection
# (zeus_conn via _get_runtime_trade_connection, risk_conn via get_connection) is
# opened. Holding a write-class conn across network IO is the conn-across-IO
# lock-contention class (the report's unconfirmed "2113 RISK_GUARD_BLOCKED/17h").
#
# WHY THIS IS A RELATIONSHIP TEST, NOT A UNIT TEST: it asserts a property that
# holds ACROSS the fetch->conn-open boundary (call ordering), not the output of
# any single function. If a future edit moves the fetch back inside the conn
# block (after a conn opens), the conn-open sentinel below fires BEFORE the fetch
# marker is recorded, so the ordering assertion goes RED. That is the whole point.
import sqlite3

import pytest

from src.riskguard import riskguard
from src.runtime import bankroll_provider


class _ConnOpenSentinel(Exception):
    """Raised by the conn-open stub so the tick aborts the moment it tries to
    open a write-class connection. By the time this fires, the bankroll fetch
    marker must ALREADY be in the order list — that ordering is the invariant."""


def test_bankroll_fetch_precedes_db_conn_open_in_tick_once(monkeypatch):
    """RELATIONSHIP (_tick_once: bankroll fetch <-> write-class conn open):

    The bankroll fetch must be recorded BEFORE the first conn-open is attempted.
    We record call order in two probes:
      - bankroll_provider.current -> appends "bankroll_fetch", returns a value
      - _get_runtime_trade_connection (the FIRST conn opened in _tick_once) ->
        appends "conn_open" then RAISES a sentinel, aborting the tick.
    get_connection (risk_conn) is also probed for completeness, but the first
    conn-open is the runtime-trade conn, so the sentinel fires there first.

    Assert: "bankroll_fetch" is recorded, and it precedes every "conn_open".
    On a revert that opens a conn before fetching, the sentinel fires first and
    "bankroll_fetch" is never recorded -> this test fails (RED-on-revert).
    """
    order: list[str] = []

    # The hoisted network-fetch point. Returning None here is fine: the ordering
    # is recorded before any branch on the value, and the early conn-open
    # sentinel aborts the tick well before the None is consumed.
    def _record_fetch(*args, **kwargs):
        order.append("bankroll_fetch")
        return None

    def _record_conn_open_and_abort(*args, **kwargs):
        order.append("conn_open")
        raise _ConnOpenSentinel()

    monkeypatch.setattr(bankroll_provider, "current", _record_fetch)
    # Patch the names as resolved inside the riskguard module namespace.
    monkeypatch.setattr(
        riskguard, "_get_runtime_trade_connection", _record_conn_open_and_abort
    )
    monkeypatch.setattr(riskguard, "get_connection", _record_conn_open_and_abort)

    with pytest.raises(_ConnOpenSentinel):
        riskguard._tick_once()

    assert "bankroll_fetch" in order, (
        "bankroll_provider.current() was never called before the first DB conn "
        "open — the fetch was NOT hoisted above the conn opens (conn-across-IO "
        "invariant T0-1 violated)."
    )
    assert "conn_open" in order, "expected the conn-open sentinel to have fired"
    assert order.index("bankroll_fetch") < order.index("conn_open"), (
        f"bankroll fetch must precede the first DB conn open; got order={order}. "
        "A write-class conn was opened before the wallet network fetch "
        "(conn-across-IO invariant T0-1 violated)."
    )


def test_bankroll_fetch_is_module_level_call_not_under_conn(monkeypatch):
    """Belt-and-suspenders: even when the fetch returns a usable value, the
    fetch is still recorded before any conn open. Same sentinel-abort shape;
    this variant guards against a revert that only reorders on the None path."""
    order: list[str] = []

    def _record_fetch(*args, **kwargs):
        order.append("bankroll_fetch")
        # Return a minimal truthy-ish object; the conn-open sentinel aborts the
        # tick before this value is dereferenced, so its shape does not matter.
        return object()

    def _record_conn_open_and_abort(*args, **kwargs):
        order.append("conn_open")
        raise _ConnOpenSentinel()

    monkeypatch.setattr(bankroll_provider, "current", _record_fetch)
    monkeypatch.setattr(
        riskguard, "_get_runtime_trade_connection", _record_conn_open_and_abort
    )
    monkeypatch.setattr(riskguard, "get_connection", _record_conn_open_and_abort)

    with pytest.raises(_ConnOpenSentinel):
        riskguard._tick_once()

    assert order[:1] == ["bankroll_fetch"], (
        f"the FIRST recorded action in _tick_once must be the bankroll fetch; "
        f"got order={order}. The fetch was not hoisted above the conn opens."
    )
