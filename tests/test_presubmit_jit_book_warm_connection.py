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


def test_jit_book_strict_timeout_fails_closed_before_outer_guard_with_double_applied_connect():
    """httpcore applies the connect timeout to connect_tcp AND start_tls separately,
    so the worst-case connect cost is 2*connect. 2*connect + read + write + pool must
    stay strictly under the outer daemon guard so the inner venue IO times out FIRST."""
    import src.main as main

    t = main._edli_pre_submit_jit_book_timeout()
    outer = main._edli_pre_submit_clob_timeout_seconds()

    assert t.connect and t.connect > 0
    assert t.read and t.read > 0
    worst_case = 2 * t.connect + t.read + t.write + t.pool
    assert worst_case < outer, (
        f"worst-case inner budget {worst_case:.2f}s (2*connect+read+write+pool) must stay "
        f"< outer guard {outer}s or a hung handshake leaks a worker (2026-06-19 pathology)"
    )


def test_jit_warmup_timeout_connect_clears_cold_handshake_floor():
    """The pre-warm/pinger run OUTSIDE the submit worker and must give a cold TLS
    handshake (~2.2-2.7s measured) enough connect budget to complete there, so the
    submit-time fetch reuses an already-warm connection."""
    import src.main as main

    w = main._edli_pre_submit_jit_warmup_timeout()
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
    import src.main as main
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
    main._edli_reset_pre_submit_jit_clob_client()
    try:
        fetch = main._edli_pre_submit_jit_book_quote_provider()
        for _ in range(3):
            book = fetch("token-xyz")
            assert book and book.get("hash") == "h1"
        assert constructed["count"] == 1, (
            f"warm reuse violated: client constructed {constructed['count']}x across 3 "
            "fetches — each construction is a cold TLS handshake (the regression)"
        )
    finally:
        main._edli_reset_pre_submit_jit_clob_client()


def test_prewarm_uses_warmup_timeout_and_keepalive_tick_is_fail_soft(monkeypatch):
    """Pre-warm must warm the connection with the GENEROUS warmup timeout; the
    keepalive tick must never raise even if the probe fails (fail-soft)."""
    import src.main as main
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
    main._edli_reset_pre_submit_jit_clob_client()
    try:
        assert main._edli_prewarm_pre_submit_jit_client() is True
        warmup = main._edli_pre_submit_jit_warmup_timeout()
        assert seen["timeouts"] and seen["timeouts"][0].connect == warmup.connect, (
            "pre-warm must pass the generous warmup timeout to warm_public_connection"
        )
        # Fail-soft: a raising probe must not propagate out of the keepalive tick.
        seen["raise"] = True
        main._edli_reset_pre_submit_jit_clob_client()
        main._edli_pre_submit_jit_keepalive_tick()  # must not raise
    finally:
        main._edli_reset_pre_submit_jit_clob_client()
