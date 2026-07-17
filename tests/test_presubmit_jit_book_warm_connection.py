# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 forward real-chain incident + frontier consult
#   REQ-20260622-044035. The pre-submit JIT /book fetch (GATE #84) constructed a
#   FRESH PolymarketClient per call, so every submit paid a cold TLS handshake.
#   Measured cold handshake to clob.polymarket.com = 2.18-2.66s; warm reuse = 0.66s.
#   The inner connect budget (coupled to read via the scalar public_http_timeout)
#   was 2.0s, so 118 of 120 JIT fetches over 2026-06-17..06-22 failed with
#   "_ssl.c:1064: The handshake operation timed out" -> 112
#   PRE_SUBMIT_BOOK_AUTHORITY_{STALE,MISSING} requeues vs 22 PreSubmitRevalidated
#   (~84% of edge-positive orders never reached the venue). The 2.0s bound came from
#   the 2026-06-19 "bound pre-submit venue reads" daemon-protection commit.
"""ANTIBODIES for the cold-handshake order-emission regression:
(1) the submit-time JIT book timeout fails-closed BEFORE the outer daemon guard
    even though httpcore applies the connect budget to TCP and TLS SEPARATELY
    (worst case 2*connect) — the daemon-protection invariant;
(2) a GENEROUS warmup timeout (used by the boot pre-warm + keepalive pinger,
    OUTSIDE the submit worker) clears the measured cold-handshake floor;
(3) the JIT book provider REUSES one warm client across calls (no per-call cold
    handshake); the dedicated client limits keep the connection warm across the
    60s reactor cycle; the keepalive pinger tick is fail-soft."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone


def test_default_pre_submit_outer_guard_clears_live_clob_tail(monkeypatch):
    """The unset live default must leave enough budget for a warm /book tail read.

    A 3s default gave the JIT provider only ~2.55s and reproduced
    PRE_SUBMIT_BOOK_AUTHORITY_STALE globally under live CLOB latency. Operators may
    still lower the value explicitly via env, but the no-env production default must
    match the 6s guard assumed by the warm-connection design.
    """
    # R4-b3 (2026-07-08): _edli_pre_submit_jit_outer_timeout_seconds moved from
    # src/main.py to src.events.reactor with the reactor+prune cluster;
    # _edli_pre_submit_clob_timeout_seconds is used broadly outside that
    # cluster and stayed in main.py.
    import src.main as main
    from src.events import reactor

    monkeypatch.delenv("ZEUS_PRE_SUBMIT_CLOB_TIMEOUT_SECONDS", raising=False)
    assert main._edli_pre_submit_clob_timeout_seconds() >= 6.0
    assert reactor._edli_pre_submit_jit_outer_timeout_seconds() >= 4.5


def test_jit_book_strict_timeout_fails_closed_before_outer_guard_with_double_applied_connect():
    """httpcore applies the connect timeout to connect_tcp AND start_tls separately,
    so the worst-case connect cost is 2*connect. 2*connect + read + write + pool must
    stay strictly under the outer daemon guard so the inner venue IO times out FIRST."""
    import src.main as main
    from src.events import reactor

    t = reactor._edli_pre_submit_jit_book_timeout()
    outer = main._edli_pre_submit_clob_timeout_seconds()

    assert t.connect and t.connect > 0
    assert t.read and t.read >= 1.5, (
        f"JIT /book read budget {t.read}s is below observed live CLOB tail latency"
    )
    worst_case = 2 * t.connect + t.read + t.write + t.pool
    assert worst_case < outer, (
        f"worst-case inner budget {worst_case:.2f}s (2*connect+read+write+pool) must stay "
        f"< outer guard {outer}s or a hung handshake leaks a worker (2026-06-19 pathology)"
    )


def test_jit_warmup_timeout_connect_clears_cold_handshake_floor():
    """The pre-warm/pinger run OUTSIDE the submit worker and must give a cold TLS
    handshake (~2.2-2.7s measured) enough connect budget to complete there, so the
    submit-time fetch reuses an already-warm connection."""
    from src.events import reactor

    w = reactor._edli_pre_submit_jit_warmup_timeout()
    assert w.connect and w.connect > 2.7, (
        f"warmup connect {w.connect} must exceed the cold-handshake floor (~2.7s)"
    )


def test_jit_client_limits_keepalive_spans_reactor_cycle():
    """The dedicated JIT limits must keep a warmed connection alive longer than the
    60s reactor cycle, or a requeued candidate re-enters cold every cycle."""
    from src.data.polymarket_client import PRESUBMIT_JIT_CLOB_HTTP_LIMITS

    assert PRESUBMIT_JIT_CLOB_HTTP_LIMITS.keepalive_expiry >= 60.0


def test_jit_book_provider_reuses_warm_client_across_calls(monkeypatch):
    """The provider must construct the CLOB client AT MOST ONCE across repeated
    fetches (warm reuse), not a fresh cold-handshaking client per call."""
    from src.events import reactor
    import src.data.polymarket_client as pmc

    constructed = {"count": 0}

    class _StubClient:
        def __init__(self, **kwargs):
            constructed["count"] += 1

        def get_orderbook_snapshot(self, token_id):
            return {"bids": [{"price": "0.40"}], "asks": [{"price": "0.60"}], "hash": "h1"}

        def warm_public_connection(self, *, timeout=None):
            return True

        def close(self):
            pass

    monkeypatch.setattr(pmc, "PolymarketClient", _StubClient)
    reactor._edli_reset_pre_submit_jit_clob_client()
    try:
        # R4-b4 (2026-07-08): _edli_pre_submit_jit_book_quote_provider (R4-b3)
        # and the warm-client reset/construct primitives it wraps (R4-b4) both
        # now live in src.events.reactor -- no more cross-module singleton.
        fetch = reactor._edli_pre_submit_jit_book_quote_provider()
        for _ in range(3):
            book = fetch("token-xyz")
            assert book and book.get("hash") == "h1"
        assert constructed["count"] == 1, (
            f"warm reuse violated: client constructed {constructed['count']}x across 3 "
            "fetches — each construction is a cold TLS handshake (the regression)"
        )
    finally:
        reactor._edli_reset_pre_submit_jit_clob_client()


def test_jit_book_provider_uses_fresh_projection_then_rest_fallback(monkeypatch):
    from src.events import reactor

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            selected_outcome_token_id TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE INDEX idx_snapshot_latest_selected_token_captured
            ON executable_market_snapshot_latest (
                selected_outcome_token_id,
                freshness_deadline DESC
            );
        """
    )
    token = "token-projected"
    projected = {
        "asset_id": token,
        "hash": "projected-hash",
        "bids": [{"price": "0.40", "size": "100"}],
        "asks": [{"price": "0.60", "size": "100"}],
    }
    captured_at = datetime.now(timezone.utc) - timedelta(milliseconds=50)
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?)",
        ("snapshot-1", token, json.dumps(projected), captured_at.isoformat()),
    )
    conn.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?,?)",
        (
            "condition-1",
            token,
            "snapshot-1",
            (captured_at + timedelta(minutes=3)).isoformat(),
        ),
    )

    rest_calls = []

    class StubClient:
        def get_orderbook_snapshot(self, token_id):
            rest_calls.append(token_id)
            return {**projected, "hash": "rest-hash"}

    monkeypatch.setattr(
        reactor,
        "_edli_pre_submit_jit_clob_client",
        lambda: StubClient(),
    )
    monkeypatch.setattr(
        reactor,
        "_edli_run_pre_submit_clob_call",
        lambda _name, call, **_kwargs: call(),
    )
    fetch = reactor._edli_pre_submit_jit_book_quote_provider(
        conn,
        max_quote_age_ms=1000,
    )

    book, observed_at, authority_id = fetch(token)
    assert book["hash"] == "projected-hash"
    assert observed_at == captured_at
    assert authority_id == "price_channel_projection"
    assert rest_calls == []
    assert fetch.consume_last(token) == (book, observed_at, authority_id)
    assert fetch.consume_last(token) is None

    conn.execute(
        "UPDATE executable_market_snapshots SET captured_at = ?",
        ((datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),),
    )
    rest_book, _rest_observed_at, rest_authority_id = fetch(token)
    assert rest_book["hash"] == "rest-hash"
    assert rest_authority_id == "clob_jit_book"
    assert rest_calls == [token]


def test_prewarm_uses_warmup_timeout_and_keepalive_tick_is_fail_soft(monkeypatch):
    """Pre-warm must warm the connection with the GENEROUS warmup timeout; the
    keepalive tick must never raise even if the probe fails (fail-soft)."""
    from src.events import reactor
    import src.data.polymarket_client as pmc

    seen = {"timeouts": [], "raise": False}

    class _StubClient:
        def __init__(self, **kwargs):
            pass

        def warm_public_connection(self, *, timeout=None):
            if seen["raise"]:
                raise RuntimeError("handshake exploded")
            seen["timeouts"].append(timeout)
            return True

        def get_orderbook_snapshot(self, token_id):
            return {"bids": [{"price": "0.40"}], "asks": [{"price": "0.60"}], "hash": "h1"}

        def close(self):
            pass

    monkeypatch.setattr(pmc, "PolymarketClient", _StubClient)
    reactor._edli_reset_pre_submit_jit_clob_client()
    try:
        assert reactor._edli_prewarm_pre_submit_jit_client() is True
        warmup = reactor._edli_pre_submit_jit_warmup_timeout()
        assert seen["timeouts"] and seen["timeouts"][0].connect == warmup.connect, (
            "pre-warm must pass the generous warmup timeout to warm_public_connection"
        )
        # Fail-soft: a raising probe must not propagate out of the keepalive tick.
        seen["raise"] = True
        reactor._edli_reset_pre_submit_jit_clob_client()
        reactor.run_edli_presubmit_jit_keepalive_cycle()  # must not raise
    finally:
        reactor._edli_reset_pre_submit_jit_clob_client()
