# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 forward real-chain incident — the pre-submit JIT
#   /book fetch (GATE #84) constructed a FRESH PolymarketClient per call, so every
#   submit paid a cold TLS handshake. Measured cold handshake to clob.polymarket.com
#   = 2.18–2.66s; the inner connect budget (coupled to read via the scalar
#   public_http_timeout) was 2.0s, so 118 of 120 JIT fetches over 2026-06-17..06-22
#   failed with "_ssl.c:1064: The handshake operation timed out" → 112
#   PRE_SUBMIT_BOOK_AUTHORITY_{STALE,MISSING} requeues vs 22 PreSubmitRevalidated
#   (~84% of edge-positive orders never reached the venue). The 2.0s bound was set
#   by the 2026-06-19 "bound pre-submit venue reads" daemon-protection commit.
"""ANTIBODIES for the cold-handshake order-emission regression:
(1) the pre-submit JIT book client gives a cold TLS handshake a connect budget
    larger than the measured cold-handshake floor, while keeping connect+read
    strictly below the outer daemon-protection guard (inner fires first);
(2) the JIT book provider REUSES one warm client across calls (no per-call cold
    handshake) — the warm reuse that drops the fetch from ~2.2s to ~0.66s."""
from __future__ import annotations

import pytest


def test_jit_book_connect_budget_exceeds_cold_handshake_and_fits_outer_guard():
    """connect must exceed the measured ~2.2-2.7s cold handshake yet connect+read
    must stay under the 6.0s outer circuit breaker so the inner venue IO times out
    FIRST (the daemon-protection invariant the 2026-06-19 commit enforced)."""
    import src.main as main

    timeout = main._edli_pre_submit_jit_book_timeout()
    outer = main._edli_pre_submit_clob_timeout_seconds()
    inner_io = main._edli_pre_submit_inner_io_timeout_seconds()

    # Cold handshake measured 2.18–2.66s forward (2026-06-22). The prior coupled
    # 2.0s connect budget timed out 118/120 submits; the new budget must clear it.
    assert timeout.connect is not None and timeout.connect > 2.7, (
        f"connect budget {timeout.connect} must exceed the cold-handshake floor (~2.7s)"
    )
    # Read stays bounded (warm reads are ~0.66s); don't inflate read to 2x like the
    # scalar path did, or connect+read could exceed the outer guard.
    assert timeout.read is not None and timeout.read <= inner_io + 1e-6, (
        f"read budget {timeout.read} must stay <= inner IO bound {inner_io}"
    )
    # Inner (connect + read) must complete before the outer daemon guard fires.
    assert timeout.connect + timeout.read < outer, (
        f"connect+read {timeout.connect + timeout.read} must stay < outer guard {outer}"
    )


def test_jit_book_provider_reuses_warm_client_across_calls(monkeypatch):
    """The provider must construct the CLOB client AT MOST ONCE across repeated
    fetches (warm reuse), not a fresh cold-handshaking client per call."""
    import src.main as main
    import src.data.polymarket_client as pmc

    constructed = {"count": 0}
    closed = {"count": 0}

    class _StubClient:
        def __init__(self, **kwargs):
            constructed["count"] += 1

        def get_orderbook_snapshot(self, token_id):
            return {"bids": [{"price": "0.40"}], "asks": [{"price": "0.60"}], "hash": "h1"}

        def close(self):
            closed["count"] += 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    monkeypatch.setattr(pmc, "PolymarketClient", _StubClient)
    # Reset any warm client carried from a prior call so this test is isolated.
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
