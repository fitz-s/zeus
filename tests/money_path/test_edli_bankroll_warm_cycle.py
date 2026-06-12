# Created: 2026-05-31
# Last reused/audited: 2026-05-31
# Authority basis: src/runtime/bankroll_provider.py (cached() RESILIENT bound, KILLER 1
#   2026-05-31: default 1800s, supersedes the prior 300s fail-closed window that blanked
#   last-good across transient wallet-RPC blip clusters) + src/main.py:_edli_event_reactor_cycle
#   bankroll-warm coupling (warm-once-at-cycle-start vs ~330s cycle).
"""Relationship test for the dedicated EDLI bankroll-warm cycle.

Cross-module invariant under test (Fitz methodology — test the boundary, not a
function):

    Bankroll freshness for the per-event no-submit Kelly proof must be DECOUPLED
    from the slow (~330s) reactor cycle. A dedicated frequent (~60s) warm job
    keeps ``bankroll_provider._last_fetched_at`` advancing so that
    ``bankroll_provider.cached()`` (300s window) ALWAYS resolves regardless of
    how long the reactor cycle runs.

Background (live evidence 2026-05-31):
    The reactor cycle warmed the cache ONCE at cycle start. But the canary cycle
    takes ~330s (heavy MC re-pricing + live /book fetches + submit path). By the
    time the allocator refresh and per-event Kelly proofs run near cycle END,
    cache age > 300s → ``cached()`` returns None → allocator fail-closes
    (bankroll_unavailable) AND all candidates reject with
    ``KELLY_PROOF_MISSING:bankroll_provider_unavailable``. The canary can never
    fill. There was NO dedicated bankroll-refresh job — freshness was coupled to
    the slow reactor cycle. THIS is the structural defect.

The fix keeps the cache FRESH (a frequent independent warm), it does NOT widen
the ``cached()`` window or weaken any fail-closed semantics. These tests lock:

  RED-before-fix #1 (bug proof): with the cache last-fetched >300s ago and NO
    warm tick, ``cached()`` returns None (the live failure mode).
  GREEN-after-fix #1: running the warm tick (which forces ``current(
    max_age_seconds=0.0)``) refreshes ``_last_fetched_at`` so ``cached()`` is
    immediately non-None even though the PRIOR fetch was >300s ago.
  Fail-soft: a warm fetch that raises does NOT propagate out of the warm job
    (the consumers already fail-closed correctly when bankroll is genuinely
    unavailable; a failed warm just means this tick's freshness didn't advance).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import src.main as main_module
from src.runtime import bankroll_provider


def _set_cache(*, value_usd: float | None, fetched_age_seconds: float | None) -> None:
    """Force the module-global bankroll cache into a known (value, age) state."""
    bankroll_provider._last_value_usd = value_usd
    if fetched_age_seconds is None:
        bankroll_provider._last_fetched_at = None
    else:
        bankroll_provider._last_fetched_at = (
            datetime.now(timezone.utc) - timedelta(seconds=fetched_age_seconds)
        )


def _enable_warm_cfg(monkeypatch) -> None:
    """Make the warm cycle config-active so it executes its body."""
    monkeypatch.setattr(
        main_module,
        "_settings_section",
        lambda name, default=None: (
            {"enabled": True} if name == "edli" else (default if default is not None else {})
        ),
    )


def _install_real_refresh_current(monkeypatch, *, fresh_value_usd: float, call_log: list) -> None:
    """Override the conftest autouse `current` stub with one that ACTUALLY refreshes
    the module cache (advances `_last_fetched_at`), mirroring real
    `current(max_age_seconds=0.0)`. This is the behaviour the warm tick depends on:
    the warm calls `current()`, which must update the module-global cache so the
    downstream `cached()` (300s window) resolves. The conftest default stub returns
    a record WITHOUT touching the cache, so the boundary can't be exercised without
    this override. No live wallet fetch occurs — we set the globals directly.
    """
    def _refreshing_current(**_kwargs):
        call_log.append(1)
        now = datetime.now(timezone.utc)
        bankroll_provider._last_value_usd = fresh_value_usd
        bankroll_provider._last_fetched_at = now
        return bankroll_provider.BankrollOfRecord(
            value_usd=fresh_value_usd,
            fetched_at=now.isoformat(),
            source="polymarket_wallet",
            authority="canonical",
            staleness_seconds=0.0,
            cached=False,
        )

    monkeypatch.setattr(bankroll_provider, "current", _refreshing_current)


def test_cached_resilient_within_bound_failclosed_beyond(monkeypatch):
    """RESILIENCE CONTRACT (KILLER 1, 2026-05-31): a value 320s old — past the OLD
    300s window — now STILL serves via cached()'s resilient bound (default 1800s);
    only a value beyond the resilient bound fails closed.

    This SUPERSEDES the prior `test_cached_is_none_after_300s_without_warm`, which
    encoded the defective 300s-blanking contract. The on-chain wallet RPC fails in
    clusters (~38/hr); blanking cached() to None after one >300s cluster killed
    161/308 positive-edge candidates with KELLY_PROOF_MISSING. Wallet balance moves
    only on our own fills/settlements, so a 320s-old last-good value is faithful.
    """
    try:
        # 320s old (matches live log age=320.4s) — within the resilient bound.
        _set_cache(value_usd=199.40, fetched_age_seconds=320.0)
        record = bankroll_provider.cached()
        assert record is not None, (
            "cached() must NOT blank at 320s — the resilient bound serves last-good "
            "(this was the KILLER-1 KELLY_PROOF_MISSING defect)."
        )
        assert record.value_usd == 199.40

        # Beyond the resilient bound (2000s > 1800s default) → genuine fail-closed.
        _set_cache(value_usd=199.40, fetched_age_seconds=2000.0)
        assert bankroll_provider.cached() is None
    finally:
        bankroll_provider.reset_cache_for_tests()


def test_warm_cycle_refreshes_aged_cache_so_cached_is_fresh(monkeypatch):
    """GREEN-after-fix: warm tick after a beyond-resilient-bound fetch recovers cached().

    The boundary the warm job must hold: it forces a fresh on-chain fetch
    (current(max_age_seconds=0.0)) which advances _last_fetched_at, so the downstream
    cached() resolves even though the PRIOR warm aged past the resilient bound.
    """
    try:
        # Prior warm aged PAST the resilient bound (2000s) → cached() fails closed.
        _set_cache(value_usd=199.40, fetched_age_seconds=2000.0)
        assert bankroll_provider.cached() is None  # pre-warm: genuinely stale → None

        # The warm calls current(max_age_seconds=0.0); install a current() that
        # actually refreshes the module cache (real-fetch semantics, no live wallet).
        call_log: list[int] = []
        _install_real_refresh_current(monkeypatch, fresh_value_usd=201.10, call_log=call_log)
        _enable_warm_cfg(monkeypatch)

        # Run the dedicated warm tick.
        main_module._edli_bankroll_warm_cycle()

        # The warm forced exactly one fresh fetch (max_age_seconds=0.0).
        assert call_log == [1]

        # cached() now resolves non-None and reflects the fresh fetch.
        record = bankroll_provider.cached()
        assert record is not None
        assert record.value_usd == 201.10
        assert record.staleness_seconds < 1.0
    finally:
        bankroll_provider.reset_cache_for_tests()


def test_warm_cycle_failsoft_on_fetch_error(monkeypatch):
    """The warm itself is fail-soft: a wallet-RPC error logs but does NOT crash.

    Consumers (allocator / Kelly) already fail-closed correctly when bankroll is
    genuinely unavailable, so a failed warm just means this tick's freshness did
    not advance — it must NOT propagate an exception out of the scheduler job.
    """
    try:
        _set_cache(value_usd=None, fetched_age_seconds=None)  # cold

        def _raising_current(**_kwargs):
            raise RuntimeError("wallet RPC timeout")

        monkeypatch.setattr(bankroll_provider, "current", _raising_current)
        _enable_warm_cfg(monkeypatch)

        # Must not raise — the warm is fail-soft (and the @_scheduler_job decorator
        # would swallow anyway, but the warm body must not depend on that).
        main_module._edli_bankroll_warm_cycle()

        # Cache stays cold (failed warm did not invent a value).
        assert bankroll_provider.cached() is None
    finally:
        bankroll_provider.reset_cache_for_tests()


def test_warm_cycle_noop_when_edli_disabled(monkeypatch):
    """Config gate: when edli is disabled the warm job does no fetch."""
    try:
        _set_cache(value_usd=None, fetched_age_seconds=None)

        call_log: list[int] = []

        def _tracking_current(**_kwargs):
            call_log.append(1)
            return None

        monkeypatch.setattr(bankroll_provider, "current", _tracking_current)
        monkeypatch.setattr(
            main_module,
            "_settings_section",
            lambda name, default=None: ({"enabled": False} if name == "edli" else (default or {})),
        )

        main_module._edli_bankroll_warm_cycle()
        assert call_log == []  # gated off → no current()/wallet side effect
    finally:
        bankroll_provider.reset_cache_for_tests()
