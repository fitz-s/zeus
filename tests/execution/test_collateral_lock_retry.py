# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: #122 / GOAL #83 — ARCH_PLAN_EVIDENCE
#   docs/evidence/qkernel_rebuild/fix_122_collateral_lock_retry_2026-06-16.md
"""A TRANSIENT `database is locked` on the pre-submit collateral refresh must RETRY,
not reject the decided order as CollateralInsufficient (the #122 conflation that
discarded armed harvest crosses on transient zeus_trades.db write-contention)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


def _patch(monkeypatch, refresh_side_effect):
    import src.execution.executor as ex  # noqa: F401  (imported for the function under test)
    from src.state.collateral_ledger import CollateralInsufficient

    class _StubClient:
        def _ensure_v2_adapter(self):
            return object()

    class _StubLedger:
        def __init__(self, conn):  # noqa: D401
            pass

        def refresh(self, adapter):
            return refresh_side_effect()

    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient", lambda *a, **k: _StubClient()
    )
    monkeypatch.setattr("src.state.collateral_ledger.CollateralLedger", _StubLedger)
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)  # no real backoff in tests
    return ex, CollateralInsufficient


def _ok_snapshot():
    return SimpleNamespace(
        authority_tier="CHAIN", captured_at=datetime(2026, 6, 16, tzinfo=timezone.utc)
    )


def test_transient_lock_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def side():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return _ok_snapshot()

    ex, _ci = _patch(monkeypatch, side)
    out = ex._refresh_entry_collateral_snapshot_for_submit(sqlite3.connect(":memory:"))
    assert calls["n"] == 3  # two transient locks retried, third succeeds
    assert out["allowed"] is True


def test_genuine_insufficiency_does_not_retry(monkeypatch):
    calls = {"n": 0}

    def side():
        calls["n"] += 1
        from src.state.collateral_ledger import CollateralInsufficient

        raise CollateralInsufficient("real_shortfall")

    ex, ci = _patch(monkeypatch, side)
    with pytest.raises(ci):
        ex._refresh_entry_collateral_snapshot_for_submit(sqlite3.connect(":memory:"))
    assert calls["n"] == 1  # genuine insufficiency surfaces immediately, no retry


def test_non_lock_operational_error_surfaces_immediately(monkeypatch):
    calls = {"n": 0}

    def side():
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: foo")

    ex, ci = _patch(monkeypatch, side)
    with pytest.raises(ci):
        ex._refresh_entry_collateral_snapshot_for_submit(sqlite3.connect(":memory:"))
    assert calls["n"] == 1  # not a lock → no retry, surfaces as collateral_refresh_failed


def test_persistent_lock_surfaces_after_retries(monkeypatch):
    calls = {"n": 0}

    def side():
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    ex, ci = _patch(monkeypatch, side)
    with pytest.raises(ci):
        ex._refresh_entry_collateral_snapshot_for_submit(sqlite3.connect(":memory:"))
    assert calls["n"] == 5  # bounded retries exhausted, then surfaces
