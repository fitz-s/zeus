# Created: 2026-05-31
# Last reused/audited: 2026-06-07
# Authority basis: /tmp/contested_bin_root_2026_05_31.md (KILLER 1) + docs/operations/task_2026-05-01_bankroll_truth_chain/architect_memo.md §7
"""Relationship test for bankroll_provider RESILIENCE across transient wallet-RPC blips.

THE RELATIONSHIP UNDER TEST (cross-call invariant, not a single-function check):

    warm-fetch SUCCESS (value=185)  →  next warm-fetch FAILURE  →  cached() STILL 185

The on-chain wallet RPC fails intermittently (~38/hr observed in live, and failures
CLUSTER). A single transient blip must NOT make ``cached()`` return None when a recent
good value exists, because a None cached() kills every positive-edge EDLI candidate with
``KELLY_PROOF_MISSING:bankroll_provider_unavailable`` (161/308 lost candidates in 24h).

Wallet balance moves only on our own fills/settlements (never venue-side between cycles),
so serving the last good value across a transient RPC outage is faithful and strictly
better than fail-closing the canary. The genuine fail-closed semantics are still proven
here: never-fetched → None, and stale-beyond-resilient-bound → None.

NOTE on the autouse ``_bankroll_provider_test_isolation`` conftest fixture: it monkeypatches
``current`` to a constant and FORBIDS ``_fetch_balance``. These tests deliberately restore
the REAL ``current()`` and install a controllable ``_fetch_balance`` so the actual
retain-last-good + cached()-bound relationship is exercised, not the fixture stub.
"""

from __future__ import annotations

import importlib

import pytest

from src.runtime import bankroll_provider


@pytest.fixture
def real_provider(monkeypatch):
    """Restore the real current()/cached() and give a controllable _fetch_balance.

    Yields a dict ``state`` whose ``state["value"]`` controls the next fetch:
    - a float → fetch returns it
    - None or an Exception instance → fetch raises (simulates an RPC blip)
    """
    # Re-import to recover the genuine module-level current() the autouse fixture
    # replaced with a constant stub.
    importlib.reload(bankroll_provider)
    bankroll_provider.reset_cache_for_tests()

    state = {"value": 185.0}

    def _controllable_fetch() -> float:
        v = state["value"]
        if v is None:
            raise RuntimeError("simulated wallet-RPC blip (None)")
        if isinstance(v, Exception):
            raise v
        return float(v)

    monkeypatch.setattr(bankroll_provider, "_fetch_balance", _controllable_fetch)
    yield state
    bankroll_provider.reset_cache_for_tests()
    # Reload once more so the next test's autouse fixture re-patches a clean module.
    importlib.reload(bankroll_provider)


def test_warm_success_then_failure_keeps_cached_value(real_provider, monkeypatch):
    """CORE RELATIONSHIP: warm success(185) → warm failure → cached() STILL 185.

    This is the exact KILLER-1 scenario. Before the resilience fix, a cluster of
    failures aging the value past 300s blanked cached() → None. After the fix,
    cached()'s resilient bound (30 min default) serves the last good value.
    """
    # 1. Warm succeeds — force a real fetch (max_age_seconds=0.0 like the live warmer).
    first = bankroll_provider.current(max_age_seconds=0.0)
    assert first is not None
    assert first.value_usd == 185.0
    assert first.cached is False

    # 2. Next warm RAISES (transient RPC blip).
    real_provider["value"] = None  # next _fetch_balance() raises
    second = bankroll_provider.current(max_age_seconds=0.0)
    # current() retains the last good value within its own fail-closed window (300s);
    # a fresh blip must NOT blank the module global.
    assert second is not None
    assert second.value_usd == 185.0
    assert second.cached is True

    # 3. THE INVARIANT: cached() — the proof/no-submit read path — STILL returns 185,
    #    NOT None. A single transient blip does not kill the Kelly proof.
    cv = bankroll_provider.cached()
    assert cv is not None, (
        "cached() blanked to None after ONE transient RPC blip — this is the "
        "KELLY_PROOF_MISSING:bankroll_provider_unavailable defect (KILLER 1)."
    )
    assert cv.value_usd == 185.0
    assert cv.authority == "canonical"
    assert cv.source == "polymarket_wallet"


def test_fetch_balance_returns_equity_and_spendable_cash(monkeypatch):
    import src.data.polymarket_client as polymarket_client_module
    import src.runtime.bankroll_provider as bp

    importlib.reload(bp)
    bp.reset_cache_for_tests()

    class FakePolymarketClient:
        def get_wallet_balance(self):
            return 100.0

        def get_positions_from_api(self):
            return [
                {"current_value": 12.25},
                {"current_value": "28.25"},
                {"current_value": 0.0},
            ]

    monkeypatch.setattr(polymarket_client_module, "PolymarketClient", FakePolymarketClient)

    record = bp.current(max_age_seconds=0.0)

    assert record is not None
    assert record.value_usd == pytest.approx(140.5)
    assert record.spendable_cash_usd == pytest.approx(100.0)
    assert record.source == "polymarket_wallet"
    assert record.authority == "canonical"
    bp.reset_cache_for_tests()
    importlib.reload(bp)


def test_cached_survives_sustained_blip_cluster_beyond_old_300s_window(real_provider, monkeypatch):
    """A blip cluster older than the OLD 300s window still serves last-good.

    Simulate the value aging to ~400s (past the retired 300s cached() bound) while the
    wallet RPC is down. The resilient bound (default 1800s) keeps cached() serving 185.
    """
    import src.runtime.bankroll_provider as bp

    # Seed a good value.
    seeded = bp.current(max_age_seconds=0.0)
    assert seeded is not None and seeded.value_usd == 185.0

    # Age the last fetch to 400s ago (past the OLD 300s cached() window, within the
    # new 1800s resilient bound). Manipulate the module global directly.
    from datetime import timedelta

    with bp._lock:
        bp._last_fetched_at = bp._now_utc() - timedelta(seconds=400)

    cv = bp.cached()
    assert cv is not None, (
        "cached() blanked at 400s — the retired 300s window would have done this; "
        "the resilient 1800s bound must keep serving last-good."
    )
    assert cv.value_usd == 185.0
    assert cv.staleness_seconds == pytest.approx(400.0, abs=5.0)


def test_cached_fails_closed_beyond_resilient_bound(real_provider, monkeypatch):
    """Genuine fail-closed preserved: stale BEYOND the resilient bound → None."""
    import src.runtime.bankroll_provider as bp
    from datetime import timedelta

    seeded = bp.current(max_age_seconds=0.0)
    assert seeded is not None

    # Age past the 1800s resilient bound (e.g. 2000s) — wallet has been unreachable
    # far too long; this is a genuine outage and MUST fail closed.
    with bp._lock:
        bp._last_fetched_at = bp._now_utc() - timedelta(seconds=2000)

    assert bp.cached() is None, (
        "cached() must fail closed when the last good value is older than the "
        "resilient bound — that is a genuine sustained wallet outage."
    )


def test_cached_fails_closed_when_never_fetched(real_provider, monkeypatch):
    """Genuine fail-closed preserved: never-fetched → None."""
    import src.runtime.bankroll_provider as bp

    bp.reset_cache_for_tests()
    assert bp.cached() is None


def test_env_override_tightens_resilient_bound(real_provider, monkeypatch):
    """ZEUS_BANKROLL_CACHED_BOUND_SECONDS overrides the default resilient bound."""
    import src.runtime.bankroll_provider as bp
    from datetime import timedelta

    seeded = bp.current(max_age_seconds=0.0)
    assert seeded is not None

    # Tighten the bound to 100s via env; a 200s-old value must now fail closed.
    monkeypatch.setenv("ZEUS_BANKROLL_CACHED_BOUND_SECONDS", "100")
    with bp._lock:
        bp._last_fetched_at = bp._now_utc() - timedelta(seconds=200)
    assert bp.cached() is None

    # Within the tightened bound (50s old) it still serves.
    with bp._lock:
        bp._last_fetched_at = bp._now_utc() - timedelta(seconds=50)
    cv = bp.cached()
    assert cv is not None and cv.value_usd == 185.0
