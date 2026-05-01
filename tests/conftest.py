# Created: 2026-04-27
# Last reused/audited: 2026-05-01
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/T1.yaml
#                  + docs/operations/task_2026-05-01_bankroll_truth_chain/architect_memo.md §7
"""Shared pytest fixtures for R3 T1 fake venue parity tests."""

from __future__ import annotations

import os

import pytest

from tests.fakes.polymarket_v2 import FakeClock, FakeCollateralLedger, FakePolymarketVenue


os.environ.setdefault("ZEUS_MODE", "live")


@pytest.fixture(autouse=True)
def _bankroll_provider_test_isolation(monkeypatch):
    """P0-A antibody: deterministic bankroll, no live wallet fetches in tests.

    The bankroll provider wraps an on-chain wallet query. Without this fixture
    every ``riskguard.tick()`` codepath would silently dial out to the live
    Polymarket endpoint during pytest collection, AND the module-level cache
    would leak real wallet values across tests.

    Default behaviour: every test gets a deterministic
    ``BankrollOfRecord(value_usd=150.0, source='polymarket_wallet',
    authority='canonical')`` so legacy tests (which expected
    ``initial_bankroll == capital_base_usd == 150``) continue to pass without
    edits. Tests that need a different wallet value monkeypatch
    ``src.runtime.bankroll_provider.current`` over this default. Live fetches
    are explicitly forbidden — ``_fetch_balance`` raises if any path slips
    through the default.
    """
    from datetime import datetime, timezone

    from src.runtime import bankroll_provider

    bankroll_provider.reset_cache_for_tests()

    def _default_current(**_kwargs):
        return bankroll_provider.BankrollOfRecord(
            value_usd=150.0,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            source="polymarket_wallet",
            authority="canonical",
            staleness_seconds=0.0,
            cached=False,
        )

    def _forbid_live_fetch():
        raise AssertionError(
            "bankroll_provider._fetch_balance was invoked from a test. "
            "Live wallet queries are forbidden in unit tests; monkeypatch "
            "bankroll_provider.current() with a BankrollOfRecord fixture."
        )

    monkeypatch.setattr(bankroll_provider, "current", _default_current)
    monkeypatch.setattr(bankroll_provider, "_fetch_balance", _forbid_live_fetch)
    yield
    bankroll_provider.reset_cache_for_tests()


@pytest.fixture
def fake_venue() -> FakePolymarketVenue:
    return FakePolymarketVenue(ledger=FakeCollateralLedger(), clock=FakeClock())


@pytest.fixture
def failure_injector(fake_venue: FakePolymarketVenue):
    def _inject(mode, **params):
        fake_venue.inject(mode, **params)
        return fake_venue

    return _inject


@pytest.fixture(autouse=True)
def r3_default_risk_allocator_for_unit_tests():
    """Keep legacy live-executor unit tests focused on their targeted guard.

    Production defaults fail closed when the A2 allocator has not been
    refreshed by the cycle runner.  Older executor/collateral/heartbeat tests
    predate A2 and patch only their local guard under test; this fixture gives
    those tests an explicit healthy allocator baseline while still allowing
    individual risk tests to call ``clear_global_allocator()`` and assert the
    fail-closed default directly.
    """

    from src.control.heartbeat_supervisor import HeartbeatHealth
    from src.control import ws_gap_guard
    from src.risk_allocator import (
        AllocationDecision,
        GovernorState,
        RiskAllocator,
        clear_global_allocator,
        configure_global_allocator,
    )

    class UnitTestRiskAllocator(RiskAllocator):
        def can_allocate(self, intent, governor_state):  # type: ignore[override]
            return AllocationDecision(True, "unit_test_default", 0)

        def maker_or_taker(self, snapshot, governor_state):  # type: ignore[override]
            return "MAKER"

        def kill_switch_reason(self, governor_state):  # type: ignore[override]
            return None

        def reduce_only_mode_active(self, governor_state):  # type: ignore[override]
            return False

    ws_gap_guard.clear_for_test()
    configure_global_allocator(
        UnitTestRiskAllocator(),
        GovernorState(
            current_drawdown_pct=0.0,
            heartbeat_health=HeartbeatHealth.HEALTHY,
            ws_gap_active=False,
            ws_gap_seconds=0,
            unknown_side_effect_count=0,
            reconcile_finding_count=0,
        ),
    )
    try:
        yield
    finally:
        clear_global_allocator()
        ws_gap_guard.clear_for_test()
