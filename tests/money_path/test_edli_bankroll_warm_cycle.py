# Created: 2026-05-31
# Last reused/audited: 2026-09-08
# Authority basis: src/runtime/bankroll_provider.py (cached() RESILIENT bound, KILLER 1
#   2026-05-31: default 1800s, supersedes the prior 300s fail-closed window that blanked
#   last-good across transient wallet-RPC blip clusters) + src/main.py:_edli_event_reactor_cycle
#   bankroll-warm coupling (warm-once-at-cycle-start vs ~330s cycle).
"""Relationship test for the dedicated EDLI bankroll-warm cycle.

Cross-module invariant under test (Fitz methodology — test the boundary, not a
function):

    Bankroll freshness for the per-event no-submit Kelly proof must be DECOUPLED
    from the slow (~330s) reactor cycle and from live wallet network I/O in the
    trading daemon. A dedicated frequent (~60s) warm job consumes the durable
    CollateralLedger snapshot produced by the post-trade-capital sidecar and
    keeps ``bankroll_provider._last_fetched_at`` advancing so that
    ``bankroll_provider.cached()`` resolves regardless of how long the reactor
    cycle runs.

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

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.main as main_module
from src.events import reactor
from src.runtime import bankroll_provider
from src.state.collateral_ledger import (
    CollateralLedger,
    CollateralSnapshot,
    configure_global_ledger,
)


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
    monkeypatch.setattr(
        main_module,
        "_refresh_global_execution_authority",
        lambda: {"configured": True},
    )


def test_warm_cycle_refreshes_execution_authority_after_bankroll(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main_module,
        "_settings_section",
        lambda name, default=None: (
            {"enabled": True}
            if name == "edli"
            else (default if default is not None else {})
        ),
    )
    monkeypatch.setattr(
        bankroll_provider,
        "run_warm_cycle",
        lambda: calls.append("bankroll") or True,
    )
    monkeypatch.setattr(
        main_module,
        "_refresh_global_execution_authority",
        lambda: calls.append("authority") or {"configured": True},
    )

    main_module._edli_bankroll_warm_cycle()

    assert calls == ["bankroll", "authority"]


def test_chain_collateral_publish_refreshes_allocator_with_same_snapshot_identity(monkeypatch):
    """A committed CHAIN snapshot restores reduce-only authority before the next warm tick."""
    captured_at = datetime.now(timezone.utc).isoformat()
    record = bankroll_provider.BankrollOfRecord(
        value_usd=10.0,
        spendable_cash_usd=10.0,
        fetched_at=captured_at,
        source="collateral_ledger_snapshot",
    )
    calls = []
    monkeypatch.setattr(
        bankroll_provider,
        "warm_from_collateral_snapshot",
        lambda: record,
    )
    monkeypatch.setattr(
        main_module,
        "_refresh_global_execution_authority",
        lambda *, bankroll_record: (
            calls.append(bankroll_record) or {"configured": True}
        ),
    )

    result = main_module._refresh_global_execution_authority_after_collateral_publish(
        captured_at=captured_at,
    )

    assert result == {"configured": True}
    assert calls == [record]


def test_identity_bound_allocator_publish_does_not_reread_bankroll_cache(monkeypatch):
    """The verified record remains the drawdown input through allocator publication."""
    captured_at = datetime.now(timezone.utc).isoformat()
    record = bankroll_provider.BankrollOfRecord(
        value_usd=10.0,
        spendable_cash_usd=10.0,
        fetched_at=captured_at,
        source="collateral_ledger_snapshot",
    )
    published = []
    monkeypatch.setattr(
        bankroll_provider,
        "cached",
        lambda: pytest.fail("identity-bound publish must not re-read mutable cache"),
    )
    monkeypatch.setattr(
        "src.risk_allocator.refresh_global_allocator",
        lambda conn, **kwargs: (
            published.append((conn, kwargs)) or {"configured": True}
        ),
    )
    monkeypatch.setattr("src.control.heartbeat_supervisor.summary", lambda: {})
    monkeypatch.setattr("src.control.ws_gap_guard.summary", lambda: {})
    monkeypatch.setattr(
        "src.riskguard.riskguard.get_current_level",
        lambda: SimpleNamespace(value="GREEN"),
    )
    conn = object()

    result = main_module._edli_refresh_global_allocator(
        conn,
        portfolio_snapshot=SimpleNamespace(daily_baseline_total=20.0),
        bankroll_record=record,
    )

    assert result == {"configured": True}
    assert published[0][0] is conn
    assert published[0][1]["ledger"]["current_drawdown_pct"] == 50.0


def test_chain_collateral_publish_emits_identity_bound_authority_wake(monkeypatch, tmp_path):
    """The sidecar wakes the order daemon only after its CHAIN snapshot is durable."""
    from src.execution import post_trade_capital
    from src.runtime.reactor_wake import COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON

    captured_at = datetime.now(timezone.utc)
    snapshot = CollateralSnapshot(
        pusd_balance_micro=10_000_000,
        pusd_allowance_micro=10_000_000,
        usdc_e_legacy_balance_micro=0,
        ctf_token_balances={},
        ctf_token_allowances={},
        reserved_pusd_for_buys_micro=0,
        reserved_tokens_for_sells={},
        captured_at=captured_at,
        authority_tier="CHAIN",
    )
    emitted = []
    trade_db = tmp_path / "trades.db"
    CollateralLedger(db_path=trade_db).close()
    monkeypatch.setattr("src.state.db._zeus_trade_db_path", lambda: trade_db)
    monkeypatch.setattr(
        "src.runtime.timeout_guard.run_with_timeout",
        lambda *_args, **_kwargs: ({}, None, ""),
    )
    monkeypatch.setattr(CollateralLedger, "refresh", lambda _self, _adapter: snapshot)
    monkeypatch.setattr(
        "src.runtime.reactor_wake.publish_reactor_wake",
        lambda **kwargs: emitted.append(kwargs),
    )

    post_trade_capital.collateral_snapshot_refresh_cycle()

    assert emitted == [
        {
            "source": "post_trade_capital",
            "reason": COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
            "published_at": captured_at,
        }
    ]


@pytest.mark.parametrize("configured", [True, False])
def test_collateral_authority_wake_services_only_allocator(monkeypatch, configured):
    """Collateral authority drains independently of higher-priority alpha wakes."""
    from src.runtime.reactor_wake import COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON

    captured_at = datetime.now(timezone.utc).isoformat()
    older = SimpleNamespace(
        wake_id="collateral-wake-older",
        reason=COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
        published_at="2026-08-03T00:00:00+00:00",
    )
    latest = SimpleNamespace(
        wake_id="collateral-wake-latest",
        reason=COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
        published_at=captured_at,
    )
    calls = []
    monkeypatch.setattr(
        "src.runtime.reactor_wake.reactor_wakes_for_reason",
        lambda *_args, **_kwargs: (latest, older),
    )
    monkeypatch.setattr(
        main_module,
        "_refresh_global_execution_authority_after_collateral_publish",
        lambda *, captured_at: (
            calls.append(("refresh", captured_at))
            or {
                "configured": configured,
                "error": None if configured else "collateral_snapshot_unavailable",
            }
        ),
    )
    monkeypatch.setattr(
        main_module,
        "_acknowledge_edli_reactor_wake_batch",
        lambda selected, wakes, *, day0_wake: (
            calls.append(("ack", selected, wakes, day0_wake)) or True
        ),
    )
    main_module._edli_last_collateral_authority_captured_at = None

    assert main_module._service_pending_collateral_authority_wake() is True
    assert calls[0] == ("refresh", captured_at)
    assert calls[1] == ("ack", latest, (latest, older), False)


def test_collateral_authority_wake_ack_failure_backs_off_and_yields(monkeypatch):
    """An unacknowledged failure cannot monopolize the listener every second."""
    from src.runtime.reactor_wake import COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON

    collateral = SimpleNamespace(
        wake_id="collateral-wake",
        reason=COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
        published_at=datetime.now(timezone.utc).isoformat(),
    )
    excluded_reads = []

    def _read(*_args, exclude_wake_ids=(), **_kwargs):
        excluded = frozenset(exclude_wake_ids)
        excluded_reads.append(excluded)
        return () if collateral.wake_id in excluded else (collateral,)

    monkeypatch.setattr("src.runtime.reactor_wake.reactor_wakes_for_reason", _read)
    monkeypatch.setattr(
        main_module,
        "_refresh_global_execution_authority_after_collateral_publish",
        lambda **_kwargs: {"configured": False, "error": "stale"},
    )
    monkeypatch.setattr(
        main_module,
        "_acknowledge_edli_reactor_wake_batch",
        lambda *_args, **_kwargs: False,
    )
    main_module._edli_collateral_authority_wake_backoff_until.clear()
    main_module._edli_last_collateral_authority_captured_at = None

    assert main_module._service_pending_collateral_authority_wake() is None
    assert collateral.wake_id in main_module._collateral_authority_wake_backoff_ids()
    assert main_module._service_pending_collateral_authority_wake() is None
    assert excluded_reads[-1] == frozenset({collateral.wake_id})

    main_module._edli_collateral_authority_wake_backoff_until.clear()
    main_module._edli_last_collateral_authority_captured_at = None


def test_collateral_ack_failure_then_canonical_warm_never_replays_old_authority(monkeypatch):
    """Old durable debt cannot revoke a newer canonical-warm publication."""
    from src.runtime.reactor_wake import COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON

    old = SimpleNamespace(
        wake_id="old",
        reason=COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
        published_at="2026-08-03T00:00:00+00:00",
    )
    new = SimpleNamespace(
        wake_id="new",
        reason=COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
        published_at="2026-08-03T00:01:00+00:00",
    )
    batches = iter(((old,), (old,)))
    acknowledgements = iter((False, True))
    refreshed = []
    monkeypatch.setattr(
        "src.runtime.reactor_wake.reactor_wakes_for_reason",
        lambda *_args, **_kwargs: next(batches),
    )
    monkeypatch.setattr(
        main_module,
        "_refresh_global_execution_authority_after_collateral_publish",
        lambda *, captured_at: refreshed.append(captured_at) or {"configured": True},
    )
    monkeypatch.setattr(
        main_module,
        "_acknowledge_edli_reactor_wake_batch",
        lambda *_args, **_kwargs: next(acknowledgements),
    )
    main_module._edli_collateral_authority_wake_backoff_until.clear()
    main_module._edli_last_collateral_authority_captured_at = None

    assert main_module._service_pending_collateral_authority_wake() is None
    new_record = bankroll_provider.BankrollOfRecord(
        value_usd=17.0,
        fetched_at=new.published_at,
    )

    class _TradeConn:
        def close(self):
            return None

    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: _TradeConn(),
    )
    monkeypatch.setattr(
        "src.state.portfolio.load_runtime_open_portfolio",
        lambda _conn: SimpleNamespace(daily_baseline_total=0.0),
    )
    monkeypatch.setattr(
        main_module,
        "_edli_refresh_global_allocator",
        lambda *_args, **_kwargs: {"configured": True},
    )
    assert main_module._refresh_global_execution_authority(
        bankroll_record=new_record,
    ) == {"configured": True}
    main_module._edli_collateral_authority_wake_backoff_until.clear()
    assert main_module._service_pending_collateral_authority_wake() is True
    assert refreshed == [old.published_at]

    main_module._edli_collateral_authority_wake_backoff_until.clear()
    main_module._edli_last_collateral_authority_captured_at = None


def test_direct_allocator_and_failed_warm_cannot_publish_older_identity(monkeypatch):
    """Every allocator caller shares the same monotonic publication fence."""
    old = bankroll_provider.BankrollOfRecord(
        value_usd=16.0,
        fetched_at="2026-08-03T00:00:00+00:00",
    )
    main_module._edli_last_collateral_authority_captured_at = datetime(
        2026,
        8,
        3,
        0,
        1,
        tzinfo=timezone.utc,
    )
    monkeypatch.setattr(bankroll_provider, "cached", lambda: old)
    monkeypatch.setattr(
        main_module,
        "_edli_refresh_global_allocator_unfenced",
        lambda *_args, **_kwargs: pytest.fail("older identity must not publish or revoke"),
    )

    assert main_module._edli_refresh_global_allocator(object()) == {
        "configured": None,
        "superseded": True,
    }
    monkeypatch.setattr(bankroll_provider, "run_warm_cycle", lambda: False)
    main_module._edli_bankroll_warm_cycle()

    main_module._edli_last_collateral_authority_captured_at = None


def test_collateral_publish_identity_mismatch_revokes_reduce_only_authority(monkeypatch):
    """A wake may never restore the allocator from a different collateral snapshot."""
    from src.control.heartbeat_supervisor import HeartbeatHealth
    from src.risk_allocator import RiskAllocator, assert_global_submit_allows, configure_global_allocator
    from src.risk_allocator.governor import AllocationDenied, GovernorState

    captured_at = datetime.now(timezone.utc)
    configure_global_allocator(
        RiskAllocator(),
        GovernorState(0.0, HeartbeatHealth.HEALTHY, False, 0, 0),
    )
    try:
        assert_global_submit_allows(reduce_only=True)
        monkeypatch.setattr(
            bankroll_provider,
            "warm_from_collateral_snapshot",
            lambda: SimpleNamespace(
                fetched_at=(captured_at - timedelta(seconds=1)).isoformat()
            ),
        )

        result = main_module._refresh_global_execution_authority_after_collateral_publish(
            captured_at=captured_at.isoformat(),
        )

        assert result["configured"] is False
        assert result["error"] == "collateral_snapshot_identity_mismatch"
        with pytest.raises(AllocationDenied) as excinfo:
            assert_global_submit_allows(reduce_only=True)
        assert excinfo.value.decision.reason == "allocator_not_configured"
    finally:
        configure_global_allocator(None, None)


def test_execution_authority_refresh_uses_canonical_open_portfolio(monkeypatch):
    calls = []
    portfolio = object()

    class _TradeConn:
        def close(self):
            calls.append("closed")

    trade_conn = _TradeConn()
    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: trade_conn,
    )
    monkeypatch.setattr(
        "src.state.portfolio.load_runtime_open_portfolio",
        lambda conn: calls.append(("portfolio", conn)) or portfolio,
    )
    monkeypatch.setattr(
        main_module,
        "_edli_refresh_global_allocator",
        lambda conn, *, portfolio_snapshot: (
            calls.append(("allocator", conn, portfolio_snapshot))
            or {"configured": True}
        ),
    )

    result = main_module._refresh_global_execution_authority()

    assert result == {"configured": True}
    assert calls == [
        ("portfolio", trade_conn),
        ("allocator", trade_conn, portfolio),
        "closed",
    ]


def _install_collateral_snapshot(
    *,
    fresh_value_usd: float,
    age_seconds: float = 0.0,
    authority_tier: str = "CHAIN",
) -> None:
    captured_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ledger = CollateralLedger()
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=int(fresh_value_usd * 1_000_000),
            pusd_allowance_micro=int(fresh_value_usd * 1_000_000),
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=captured_at,
            authority_tier=authority_tier,
        )
    )
    configure_global_ledger(ledger)


@pytest.mark.parametrize("authority_tier", ["CHAIN", "VENUE"])
def test_collateral_snapshot_warm_accepts_real_authority_tiers(authority_tier):
    try:
        _install_collateral_snapshot(
            fresh_value_usd=12.5,
            authority_tier=authority_tier,
        )

        record = bankroll_provider.warm_from_collateral_snapshot()

        assert isinstance(record, bankroll_provider.BankrollOfRecord)
        assert record.value_usd == 12.5
        assert record.source == "collateral_ledger_snapshot"
    finally:
        configure_global_ledger(None)
        bankroll_provider.reset_cache_for_tests()


def test_collateral_snapshot_warm_rejects_real_degraded_authority():
    try:
        _install_collateral_snapshot(
            fresh_value_usd=12.5,
            authority_tier="DEGRADED",
        )

        assert bankroll_provider.warm_from_collateral_snapshot() is None
    finally:
        configure_global_ledger(None)
        bankroll_provider.reset_cache_for_tests()


def _install_mixed_collateral_snapshot_history(
    tmp_path,
    *,
    newest_pusd_authority: str = "CHAIN",
):
    trade_db = tmp_path / "mixed-collateral.db"
    ledger = CollateralLedger(db_path=trade_db)
    newer_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    older_at = newer_at - timedelta(seconds=1)

    # Persist the newer pUSD-only fact first, then simulate an already-captured
    # target-token read committing later. Row id therefore disagrees with fact
    # time, exactly as two concurrent live producers can interleave.
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=20_000_000,
            pusd_allowance_micro=20_000_000,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={},
            ctf_token_allowances={},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=newer_at,
            authority_tier=newest_pusd_authority,
        )
    )
    ledger.set_snapshot(
        CollateralSnapshot(
            pusd_balance_micro=10_000_000,
            pusd_allowance_micro=10_000_000,
            usdc_e_legacy_balance_micro=0,
            ctf_token_balances={"held-token": 5_000_000},
            ctf_token_allowances={"held-token": 5_000_000},
            reserved_pusd_for_buys_micro=0,
            reserved_tokens_for_sells={},
            captured_at=older_at,
            authority_tier="CHAIN",
        )
    )
    with sqlite3.connect(trade_db) as conn:
        conn.execute(
            """
            CREATE TABLE position_current (
                phase TEXT,
                shares REAL,
                chain_shares REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO position_current VALUES ('active', 5.0, 5.0)"
        )
    return ledger, newer_at


def test_bankroll_warm_uses_newest_pusd_witness_not_older_target_ctf_row(
    monkeypatch,
    tmp_path,
):
    ledger, newer_at = _install_mixed_collateral_snapshot_history(tmp_path)
    configure_global_ledger(ledger)
    try:
        # Portfolio consumers still retain the target-token witness.
        assert ledger.snapshot().ctf_token_balances == {"held-token": 5_000_000}

        record = bankroll_provider.warm_from_collateral_snapshot()

        assert record is not None
        assert record.value_usd == 20.0
        assert record.fetched_at == newer_at.isoformat()
        monkeypatch.setattr(
            main_module,
            "_refresh_global_execution_authority",
            lambda *, bankroll_record: {
                "configured": bankroll_record.fetched_at == newer_at.isoformat()
            },
        )
        assert main_module._refresh_global_execution_authority_after_collateral_publish(
            captured_at=newer_at.isoformat(),
        ) == {"configured": True}
    finally:
        configure_global_ledger(None)
        bankroll_provider.reset_cache_for_tests()


def test_bankroll_warm_does_not_hide_newest_degraded_pusd_behind_old_ctf(tmp_path):
    ledger, _ = _install_mixed_collateral_snapshot_history(
        tmp_path,
        newest_pusd_authority="DEGRADED",
    )
    configure_global_ledger(ledger)
    try:
        assert bankroll_provider.warm_from_collateral_snapshot() is None
    finally:
        configure_global_ledger(None)
        bankroll_provider.reset_cache_for_tests()


def test_post_trade_durable_snapshot_wake_refreshes_allocator_without_entry_reactor(
    monkeypatch,
    tmp_path,
):
    """Exercise the isolated durable sidecar-to-listener allocator handoff."""
    from src.control.heartbeat_supervisor import HeartbeatHealth
    from src.execution import post_trade_capital
    from src.risk_allocator import (
        RiskAllocator,
        assert_global_submit_allows,
        configure_global_allocator,
    )
    from src.risk_allocator.governor import GovernorState
    from src.runtime import reactor_wake

    trade_db = tmp_path / "trades.db"
    CollateralLedger(db_path=trade_db).close()
    wake_path = tmp_path / "edli-reactor-wake.json"
    payload = {
        "pusd_balance_micro": 17_000_000,
        "pusd_allowance_micro": 17_000_000,
        "authority_tier": "CHAIN",
    }
    published_records = []

    monkeypatch.setattr("src.state.db._zeus_trade_db_path", lambda: trade_db)
    monkeypatch.setattr(
        "src.runtime.timeout_guard.run_with_timeout",
        lambda *_args, **_kwargs: (payload, None, ""),
    )
    monkeypatch.setattr("src.config.state_path", lambda _name: wake_path)
    monkeypatch.setattr(
        main_module,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: pytest.fail("collateral listener must not run entry reactor"),
    )

    class _TradeConn:
        def close(self):
            return None

    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: _TradeConn(),
    )
    monkeypatch.setattr(
        "src.state.portfolio.load_runtime_open_portfolio",
        lambda _conn: SimpleNamespace(daily_baseline_total=0.0),
    )

    def _publish_allocator(
        _conn,
        *,
        portfolio_snapshot,
        bankroll_record,
    ):
        published_records.append((portfolio_snapshot, bankroll_record))
        configure_global_allocator(
            RiskAllocator(),
            GovernorState(0.0, HeartbeatHealth.HEALTHY, False, 0, 0),
        )
        return {"configured": True}

    monkeypatch.setattr(main_module, "_edli_refresh_global_allocator", _publish_allocator)

    try:
        reactor_wake.publish_reactor_wake(
            source="test",
            reason=reactor_wake.COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
            path=wake_path,
            published_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        day0 = reactor_wake.publish_reactor_wake(
            source="test",
            reason="day0_extreme_event_committed",
            path=wake_path,
            published_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        post_trade_capital.collateral_snapshot_refresh_cycle()
        configure_global_ledger(CollateralLedger(db_path=trade_db))
        main_module._edli_last_reactor_wake_id = None
        main_module._edli_last_collateral_authority_captured_at = None

        assert main_module._service_pending_collateral_authority_wake() is True
        assert len(published_records) == 1
        record = published_records[0][1]
        assert isinstance(record, bankroll_provider.BankrollOfRecord)
        assert record.value_usd == 17.0
        assert assert_global_submit_allows(reduce_only=True).allowed is True
        assert reactor_wake.read_reactor_wake(path=wake_path) == day0
        assert reactor_wake.reactor_wakes_for_reason(
            reactor_wake.COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
            path=wake_path,
        ) == ()
    finally:
        configure_global_allocator(None, None)
        configure_global_ledger(None)
        bankroll_provider.reset_cache_for_tests()
        main_module._edli_last_collateral_authority_captured_at = None


def test_collateral_reason_drain_is_bounded_and_includes_legacy_fallback(tmp_path):
    """Exact-reason selection is newest-first, bounded, and legacy-complete."""
    from src.runtime import reactor_wake

    wake_path = tmp_path / "edli-reactor-wake.json"
    published = [
        reactor_wake.publish_reactor_wake(
            source="test",
            reason=reactor_wake.COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
            path=wake_path,
            published_at=datetime(2026, 8, 3, 0, minute, tzinfo=timezone.utc),
        )
        for minute in range(3)
    ]
    selected = reactor_wake.reactor_wakes_for_reason(
        reactor_wake.COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
        path=wake_path,
        max_wakes=2,
    )
    assert [wake.wake_id for wake in selected] == [
        published[2].wake_id,
        published[1].wake_id,
    ]

    for queue_file in wake_path.with_name(wake_path.name + ".d").glob("*.json"):
        queue_file.unlink()
    legacy = reactor_wake.reactor_wakes_for_reason(
        reactor_wake.COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
        path=wake_path,
    )
    assert legacy == (published[2],)


_SUBPROCESS_PYTHON = Path(sys.executable)


def _run_relationship_subprocess(
    source: str,
    *args: object,
    state_root: Path,
) -> dict:
    env = dict(os.environ)
    env["ZEUS_TEST_STATE_ROOT"] = str(state_root)
    completed = subprocess.run(
        [_SUBPROCESS_PYTHON, "-c", textwrap.dedent(source), *(str(arg) for arg in args)],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


_COLLATERAL_PRODUCER_SOURCE = r"""
import json
import os
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src.execution import post_trade_capital
from src.runtime.reactor_wake import read_reactor_wake
from src.state.collateral_ledger import CollateralLedger

trade_db = Path(sys.argv[1])
CollateralLedger(db_path=trade_db).close()
wake_path = Path(sys.argv[2])
authority_tier = sys.argv[3]
stale_seconds = float(sys.argv[4])
payload = {
    "pusd_balance_micro": 23_000_000,
    "pusd_allowance_micro": 23_000_000,
    "authority_tier": authority_tier,
}

class _CapturedAt(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz) - timedelta(seconds=stale_seconds)

degraded = False
with ExitStack() as stack:
    stack.enter_context(patch("src.state.db._zeus_trade_db_path", return_value=trade_db))
    stack.enter_context(
        patch(
            "src.runtime.timeout_guard.run_with_timeout",
            return_value=(payload, None, ""),
        )
    )
    stack.enter_context(patch("src.config.state_path", return_value=wake_path))
    if stale_seconds:
        stack.enter_context(patch("src.state.collateral_ledger.datetime", _CapturedAt))
    try:
        post_trade_capital.collateral_snapshot_refresh_cycle()
    except post_trade_capital.CollateralSnapshotDegraded:
        degraded = True

snapshot = CollateralLedger(db_path=trade_db).snapshot()
wake = read_reactor_wake(path=wake_path)
assert wake is not None
assert wake.published_at == snapshot.captured_at.isoformat()
assert snapshot.authority_tier == authority_tier
assert degraded is (authority_tier == "DEGRADED")
print(
    json.dumps(
        {
            "pid": os.getpid(),
            "authority_tier": snapshot.authority_tier,
            "snapshot_identity": snapshot.captured_at.isoformat(),
            "wake_identity": wake.published_at,
            "wake_id": wake.wake_id,
            "degraded_exception": degraded,
        },
        sort_keys=True,
    )
)
"""


_COLLATERAL_CONSUMER_SOURCE = r"""
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import src.main as main_module
from src.control.heartbeat_supervisor import HeartbeatHealth
from src.risk_allocator import (
    RiskAllocator,
    assert_global_submit_allows,
    configure_global_allocator,
    summary,
)
from src.risk_allocator.governor import AllocationDenied, GovernorState
from src.runtime import bankroll_provider
from src.runtime.reactor_wake import read_reactor_wake
from src.state.collateral_ledger import CollateralLedger, configure_global_ledger

trade_db = Path(sys.argv[1])
wake_path = Path(sys.argv[2])
expected_identity = sys.argv[3]
should_restore = sys.argv[4] == "restore"
published_identities = []
entry_reactor_calls = []

class _TradeConn:
    def close(self):
        return None

def _publish_allocator(conn, *, ledger, heartbeat, ws_status, cap_policy=None):
    configure_global_allocator(
        RiskAllocator(),
        GovernorState(0.0, HeartbeatHealth.HEALTHY, False, 0, 0),
    )
    return summary()

_original_allocator_refresh = main_module._edli_refresh_global_allocator

def _identity_bound_allocator_refresh(
    conn,
    *,
    portfolio_snapshot,
    bankroll_record,
):
    published_identities.append(bankroll_record.fetched_at)
    return _original_allocator_refresh(
        conn,
        portfolio_snapshot=portfolio_snapshot,
        bankroll_record=bankroll_record,
    )

def _entry_reactor_forbidden(**kwargs):
    entry_reactor_calls.append(kwargs)
    raise AssertionError("collateral listener must not run entry reactor")

configure_global_allocator(None, None)
configure_global_ledger(CollateralLedger(db_path=trade_db))
main_module._edli_last_reactor_wake_id = None
main_module._edli_collateral_authority_wake_backoff_until.clear()
try:
    with patch("src.config.state_path", return_value=wake_path), patch(
        "src.state.db.get_trade_connection_read_only", return_value=_TradeConn()
    ), patch(
        "src.state.portfolio.load_runtime_open_portfolio",
        return_value=SimpleNamespace(daily_baseline_total=0.0),
    ), patch(
        "src.risk_allocator.refresh_global_allocator",
        side_effect=_publish_allocator,
    ), patch.object(
        main_module,
        "_edli_refresh_global_allocator",
        side_effect=_identity_bound_allocator_refresh,
    ), patch.object(
        main_module,
        "_edli_event_reactor_cycle",
        side_effect=_entry_reactor_forbidden,
    ):
        stop_event = threading.Event()
        listener = threading.Thread(
            target=main_module._run_edli_reactor_wake_listener,
            kwargs={"stop_event": stop_event, "poll_seconds": 0.01},
            name="relationship-order-daemon-listener",
        )
        listener.start()
        deadline = time.monotonic() + 5.0
        while read_reactor_wake(path=wake_path) is not None:
            assert listener.is_alive()
            if time.monotonic() >= deadline:
                raise AssertionError("production listener did not drain collateral wake")
            time.sleep(0.01)
        stop_event.set()
        listener.join(timeout=2.0)
        assert not listener.is_alive()

    allocator_state = summary()
    try:
        reduce_only_allowed = assert_global_submit_allows(reduce_only=True).allowed
        denial_reason = None
    except AllocationDenied as exc:
        reduce_only_allowed = False
        denial_reason = exc.decision.reason
    wake_remaining = read_reactor_wake(path=wake_path) is not None

    assert not entry_reactor_calls
    assert not wake_remaining
    if should_restore:
        assert allocator_state["configured"] is True
        assert reduce_only_allowed is True
        assert published_identities == [expected_identity]
        assert denial_reason is None
    else:
        assert allocator_state["configured"] is False
        assert reduce_only_allowed is False
        assert published_identities == []
        assert denial_reason == "allocator_not_configured"

    print(
        json.dumps(
            {
                "pid": os.getpid(),
                "listener_entrypoint": "_run_edli_reactor_wake_listener",
                "listener_stopped": not listener.is_alive(),
                "configured": allocator_state["configured"],
                "reduce_only_allowed": reduce_only_allowed,
                "denial_reason": denial_reason,
                "published_identities": published_identities,
                "expected_identity": expected_identity,
                "wake_remaining": wake_remaining,
                "entry_reactor_calls": len(entry_reactor_calls),
            },
            sort_keys=True,
        )
    )
finally:
    configure_global_allocator(None, None)
    configure_global_ledger(None)
    bankroll_provider.reset_cache_for_tests()
"""


@pytest.mark.parametrize(
    ("authority_tier", "stale_seconds", "should_restore"),
    [
        ("CHAIN", 0.0, True),
        ("DEGRADED", 0.0, False),
        ("CHAIN", 3600.0, False),
    ],
    ids=("fresh-chain", "degraded", "stale-chain"),
)
def test_post_trade_collateral_wake_cross_process_relationship(
    tmp_path,
    authority_tier,
    stale_seconds,
    should_restore,
):
    """Prove durable producer-to-order-daemon authority transfer across PIDs."""
    assert _SUBPROCESS_PYTHON.is_file()
    case_root = tmp_path / f"{authority_tier.lower()}-{int(stale_seconds)}"
    case_root.mkdir()
    trade_db = case_root / "trades.db"
    wake_path = case_root / "edli-reactor-wake.json"

    producer = _run_relationship_subprocess(
        _COLLATERAL_PRODUCER_SOURCE,
        trade_db,
        wake_path,
        authority_tier,
        stale_seconds,
        state_root=case_root,
    )
    consumer = _run_relationship_subprocess(
        _COLLATERAL_CONSUMER_SOURCE,
        trade_db,
        wake_path,
        producer["snapshot_identity"],
        "restore" if should_restore else "reject",
        state_root=case_root,
    )

    assert producer["pid"] != consumer["pid"]
    assert producer["snapshot_identity"] == producer["wake_identity"]
    assert consumer == {
        "configured": should_restore,
        "denial_reason": None if should_restore else "allocator_not_configured",
        "entry_reactor_calls": 0,
        "expected_identity": producer["snapshot_identity"],
        "listener_entrypoint": "_run_edli_reactor_wake_listener",
        "listener_stopped": True,
        "pid": consumer["pid"],
        "published_identities": (
            [producer["snapshot_identity"]] if should_restore else []
        ),
        "reduce_only_allowed": should_restore,
        "wake_remaining": False,
    }


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


def test_warm_cycle_refreshes_from_collateral_snapshot_without_wallet_current(monkeypatch):
    """GREEN-after-fix: warm tick after a beyond-resilient-bound fetch recovers cached().

    The boundary the warm job must hold: it forces a fresh on-chain fetch
    (current(max_age_seconds=0.0)) which advances _last_fetched_at, so the downstream
    cached() resolves even though the PRIOR warm aged past the resilient bound.
    """
    try:
        # Prior warm aged PAST the resilient bound (2000s) → cached() fails closed.
        _set_cache(value_usd=199.40, fetched_age_seconds=2000.0)
        assert bankroll_provider.cached() is None  # pre-warm: genuinely stale → None

        call_log: list[int] = []

        def _forbidden_current(**_kwargs):
            call_log.append(1)
            raise AssertionError("bankroll warm must not perform live wallet I/O")

        monkeypatch.setattr(bankroll_provider, "current", _forbidden_current)
        _install_collateral_snapshot(fresh_value_usd=201.10)
        _enable_warm_cfg(monkeypatch)

        # Run the dedicated warm tick.
        main_module._edli_bankroll_warm_cycle()

        assert call_log == []

        # cached() now resolves non-None and reflects the fresh fetch.
        record = bankroll_provider.cached()
        assert record is not None
        assert record.value_usd == 201.10
        assert record.source == "collateral_ledger_snapshot"
        assert record.staleness_seconds < 1.0
    finally:
        bankroll_provider.reset_cache_for_tests()
        configure_global_ledger(None)


def test_warm_cycle_failsoft_on_missing_collateral_snapshot(monkeypatch):
    """The warm itself is fail-soft: missing ledger data does NOT crash.

    Consumers (allocator / Kelly) already fail-closed correctly when bankroll is
    genuinely unavailable, so a failed warm just means this tick's freshness did
    not advance — it must NOT propagate an exception out of the scheduler job.
    """
    try:
        _set_cache(value_usd=None, fetched_age_seconds=None)  # cold
        configure_global_ledger(None)
        _enable_warm_cfg(monkeypatch)

        # Must not raise — the warm is fail-soft (and the @_scheduler_job decorator
        # would swallow anyway, but the warm body must not depend on that).
        main_module._edli_bankroll_warm_cycle()

        # Cache stays cold (failed warm did not invent a value).
        assert bankroll_provider.cached() is None
    finally:
        bankroll_provider.reset_cache_for_tests()
        configure_global_ledger(None)


def test_missing_current_collateral_revokes_prior_execution_authority():
    from src.risk_allocator import (
        RiskAllocator,
        assert_global_submit_allows,
        configure_global_allocator,
        snapshot_global_auction_capital_authority,
    )
    from src.risk_allocator.governor import AllocationDenied

    try:
        _set_cache(value_usd=199.40, fetched_age_seconds=300.0)
        configure_global_ledger(None)
        configure_global_allocator(RiskAllocator(), None)
        snapshot_global_auction_capital_authority()

        main_module._edli_bankroll_warm_cycle()

        with pytest.raises(AllocationDenied):
            snapshot_global_auction_capital_authority()
        with pytest.raises(AllocationDenied):
            assert_global_submit_allows(reduce_only=True)
    finally:
        configure_global_allocator(None, None)
        bankroll_provider.reset_cache_for_tests()
        configure_global_ledger(None)


def test_warm_cycle_does_not_obey_retired_edli_enabled_gate(monkeypatch):
    """The registered live job keeps truth fresh despite an obsolete flag."""
    try:
        _set_cache(value_usd=None, fetched_age_seconds=None)
        _install_collateral_snapshot(fresh_value_usd=177.25)
        monkeypatch.setattr(
            main_module,
            "_settings_section",
            lambda name, default=None: (
                {"enabled": False} if name == "edli" else (default or {})
            ),
        )
        monkeypatch.setattr(
            main_module,
            "_refresh_global_execution_authority",
            lambda: {"configured": True},
        )

        main_module._edli_bankroll_warm_cycle()
        record = bankroll_provider.cached()
        assert record is not None
        assert record.value_usd == 177.25
    finally:
        bankroll_provider.reset_cache_for_tests()
        configure_global_ledger(None)


def test_event_reactor_bankroll_warm_is_snapshot_only() -> None:
    """Static money-path guard: reactor must not do wallet network refreshes."""
    import inspect

    source = inspect.getsource(reactor.run_edli_event_reactor_cycle)

    assert "warm_from_collateral_snapshot" in source
    assert "current(max_age_seconds=0.0)" not in source
