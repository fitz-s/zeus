# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: zero-trade root-cause 2026-06-08 — RiskGuard dependency-DB
#   within-tick retry (Fitz #5 lock-CATEGORY kill). The daemon tick read on
#   zeus_trades + ATTACHed world/forecasts lost a transient WAL lock on ~half of
#   ticks, never landed a genuine fresh full_risk row inside the 5-min freshness
#   window, so get_current_level flapped to DATA_DEGRADED and the GREEN-only entry
#   gate blocked all entries. These are RELATIONSHIP tests of the tick() wrapper
#   <-> _tick_once boundary: written before the implementation was trusted.
import sqlite3

import pytest

from src.riskguard import riskguard
from src.riskguard.riskguard import RiskLevel


def _locked() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("database is locked")


def test_transient_lock_then_success_yields_genuine_level(monkeypatch):
    """RELATIONSHIP (tick wrapper -> _tick_once): a tick whose dependency read
    locks transiently then succeeds returns the GENUINE computed level, NOT a
    lock-attestation. This is the cross-boundary property that keeps a fresh
    full_risk row landing nearly every tick so the 5-min freshness window never
    lapses (the zero-trade flap)."""
    calls = {"n": 0}

    def fake_once():
        calls["n"] += 1
        if calls["n"] <= 2:  # lock twice, succeed on the 3rd attempt
            raise _locked()
        return RiskLevel.GREEN

    monkeypatch.setattr(riskguard, "_tick_once", fake_once)
    monkeypatch.setattr(riskguard.time, "sleep", lambda *_: None)
    monkeypatch.setattr(riskguard, "_riskguard_dependency_lock_retries", lambda: 3)

    def _attestation_must_not_run(*_a, **_k):
        raise AssertionError("attestation ran despite a RECOVERABLE transient lock")

    monkeypatch.setattr(
        riskguard, "_persist_dependency_db_locked_attestation", _attestation_must_not_run
    )

    assert riskguard.tick() == RiskLevel.GREEN
    assert calls["n"] == 3  # two locks + one success, no extra attempts


def test_persistent_lock_falls_back_to_attestation(monkeypatch):
    """RELATIONSHIP: when EVERY attempt locks, tick() falls back to the
    lock-attestation (preserve-fresh-<5min-or-degrade). A PERSISTENT lock still
    degrades — the retry only absorbs transient windows, no safety boundary is
    bypassed."""
    monkeypatch.setattr(riskguard, "_tick_once", lambda: (_ for _ in ()).throw(_locked()))
    monkeypatch.setattr(riskguard.time, "sleep", lambda *_: None)
    monkeypatch.setattr(riskguard, "_riskguard_dependency_lock_retries", lambda: 3)

    seen = {}

    def fake_attest(exc):
        seen["exc"] = exc
        return RiskLevel.DATA_DEGRADED

    monkeypatch.setattr(riskguard, "_persist_dependency_db_locked_attestation", fake_attest)

    assert riskguard.tick() == RiskLevel.DATA_DEGRADED
    assert riskguard._is_sqlite_database_locked(seen["exc"])


def test_non_lock_operationalerror_propagates(monkeypatch):
    """A non-lock OperationalError (e.g. a genuine schema fault) is NOT retried
    or swallowed as a lock-attestation — it propagates loudly so a real fault is
    never masked by the lock-tolerance path."""
    monkeypatch.setattr(
        riskguard, "_tick_once",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("no such table: risk_state")),
    )
    monkeypatch.setattr(riskguard, "_riskguard_dependency_lock_retries", lambda: 3)
    monkeypatch.setattr(
        riskguard, "_persist_dependency_db_locked_attestation",
        lambda *_a, **_k: pytest.fail("attestation ran for a NON-lock error"),
    )
    with pytest.raises(sqlite3.OperationalError):
        riskguard.tick()


def test_retries_zero_restores_single_attempt(monkeypatch):
    """retries=0 restores the pre-fix single-attempt behavior: exactly one
    _tick_once call, then straight to the attestation on a lock."""
    calls = {"n": 0}

    def fake_once():
        calls["n"] += 1
        raise _locked()

    monkeypatch.setattr(riskguard, "_tick_once", fake_once)
    monkeypatch.setattr(riskguard.time, "sleep", lambda *_: None)
    monkeypatch.setattr(riskguard, "_riskguard_dependency_lock_retries", lambda: 0)
    monkeypatch.setattr(
        riskguard, "_persist_dependency_db_locked_attestation", lambda _exc: RiskLevel.DATA_DEGRADED
    )

    assert riskguard.tick() == RiskLevel.DATA_DEGRADED
    assert calls["n"] == 1  # no retry when the budget is zero


def test_backoff_is_bounded_and_nonnegative():
    """The backoff schedule is monotone, non-negative, and capped at 8s so a
    contended tick still completes well inside the 60s cadence."""
    vals = [riskguard._riskguard_dependency_lock_backoff_seconds(i) for i in range(10)]
    assert all(v >= 0.0 for v in vals)
    assert max(vals) <= 8.0
    assert vals[0] <= vals[-1]
