# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: src/main.py:_edli_event_reactor_cycle (inline _refresh_pending_family_snapshots
#   coupling) + _edli_bankroll_warm_cycle precedent (#45 follow-up, the decoupled-warm pattern) +
#   src/main.py:_refresh_pending_family_snapshots (universe Gamma scan + per-token CLOB capture,
#   measured 76s cold for _get_active_events alone).
"""Relationship test for the dedicated EDLI market-substrate warm cycle (throughput).

Cross-module invariant under test (Fitz methodology — test the boundary between the
reactor's DECISION loop and the venue-I/O SUBSTRATE refresh, not a single function):

    The expensive executable-market-snapshot refresh (a universe Gamma scan +
    per-token CLOB /book capture — measured ~76s cold for the active-events scan
    alone, plus per-token book fetches) MUST be DECOUPLED from the per-cycle EDLI
    reactor decision loop. The reactor cycle must read ALREADY-captured snapshots
    (DB-only, microseconds) so a crossable positive-edge candidate reaches submit
    within a fast cycle, instead of the reactor blocking on the refresh until the
    cycle wall-clock blows past the APScheduler interval (overlapping triggers are
    coalesced/skipped → 0 completed cycles → 0 trades).

Background (live evidence 2026-06-01):
    `_edli_event_reactor_cycle` called `_refresh_pending_family_snapshots(...)` INLINE
    at the top of every cycle. That helper runs `find_weather_markets()` →
    `_get_active_events(include_slug_pattern=True)`, a full-universe Gamma scan
    benchmarked at ~76s COLD (TTL 300s, so it re-runs roughly every cycle), followed
    by per-token CLOB `/book` capture across all pending-family bins. The reactor
    interval is 1 min with max_instances=1/coalesce=True, so a 20+ min refresh-bound
    cycle starves `process_pending` → "EDLI reactor cycle result" never logs and no
    crossable candidate ever reaches submit. THIS coupling is the structural defect.

The fix MOVES the refresh to a dedicated decoupled scheduler job (mirroring
`_edli_bankroll_warm_cycle`, #45). It does NOT change any decision, gate, or the
just-in-time submit `/book` (the reactor's no-submit path + full gate chain + JIT
submit are byte-for-byte unchanged — they just read snapshots a background job
captured). Fail-closed is preserved: a family not yet captured still requeues via
the reactor's existing EXECUTABLE_SNAPSHOT_RETRY path.

These tests lock:
  RED-before-fix #1 (coupling proof): the reactor cycle must NOT invoke
    `_refresh_pending_family_snapshots` inline (the expensive venue-I/O is off the
    decision critical path).
  RED-before-fix #2 (decoupled job exists): a dedicated `_edli_market_substrate_warm_cycle`
    job exists and, when EDLI is enabled, DOES invoke the refresh exactly once.
  Gate: when edli_v1 is disabled the warm job does no refresh.
  Fail-soft: a refresh that raises does NOT propagate out of the warm job.
"""

from __future__ import annotations

import inspect

import src.main as main_module


def _enable_edli_cfg(monkeypatch, *, enabled: bool = True) -> None:
    monkeypatch.setattr(
        main_module,
        "_settings_section",
        lambda name, default=None: (
            {"enabled": enabled} if name == "edli_v1" else (default if default is not None else {})
        ),
    )


def test_reactor_cycle_does_not_refresh_inline():
    """RED-before-fix: the reactor decision cycle must not call the expensive
    `_refresh_pending_family_snapshots` inline — that venue-I/O belongs on the
    decoupled warm job, off the decision critical path.

    Static-source assertion (the inline call is a direct lexical call in the cycle
    body) so the test is deterministic and does not depend on DB/venue state.
    """
    src = inspect.getsource(main_module._edli_event_reactor_cycle)
    assert "_refresh_pending_family_snapshots(" not in src, (
        "reactor cycle still calls _refresh_pending_family_snapshots INLINE — the "
        "expensive universe Gamma scan + per-token CLOB capture must be decoupled to "
        "the dedicated _edli_market_substrate_warm_cycle so the reactor reaches submit "
        "in seconds."
    )


def test_market_substrate_warm_cycle_exists_and_refreshes_once(monkeypatch):
    """GREEN-after-fix: a dedicated warm job exists and, when EDLI is enabled, invokes
    the family-snapshot refresh exactly once per tick."""
    assert hasattr(main_module, "_edli_market_substrate_warm_cycle"), (
        "expected a dedicated _edli_market_substrate_warm_cycle scheduler job (mirroring "
        "_edli_bankroll_warm_cycle) that owns the decoupled substrate refresh."
    )

    calls: list[int] = []
    monkeypatch.setattr(
        main_module,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(1),
    )
    # The warm job opens world/forecasts connections; stub them so no real DB/venue work
    # runs. The test only asserts the refresh is invoked exactly once.
    monkeypatch.setattr(main_module, "get_world_connection", lambda: _FakeConn(), raising=False)
    monkeypatch.setattr(
        main_module, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    main_module._edli_market_substrate_warm_cycle()
    assert calls == [1], "warm job must invoke the family-snapshot refresh exactly once"


def test_market_substrate_warm_cycle_noop_when_edli_disabled(monkeypatch):
    """Config gate: disabled edli_v1 → no refresh side effect."""
    calls: list[int] = []
    monkeypatch.setattr(
        main_module,
        "_refresh_pending_family_snapshots",
        lambda *a, **k: calls.append(1),
    )
    monkeypatch.setattr(main_module, "get_world_connection", lambda: _FakeConn(), raising=False)
    monkeypatch.setattr(
        main_module, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    _enable_edli_cfg(monkeypatch, enabled=False)

    main_module._edli_market_substrate_warm_cycle()
    assert calls == [], "disabled edli_v1 must skip the refresh"


def test_market_substrate_warm_cycle_failsoft_on_refresh_error(monkeypatch):
    """Fail-soft: a refresh that raises must not propagate out of the warm job (the
    next tick retries; the reactor's EXECUTABLE_SNAPSHOT_RETRY keeps decisions
    fail-closed in the interim)."""
    def _raising(*a, **k):
        raise RuntimeError("gamma scan timeout")

    monkeypatch.setattr(main_module, "_refresh_pending_family_snapshots", _raising)
    monkeypatch.setattr(main_module, "get_world_connection", lambda: _FakeConn(), raising=False)
    monkeypatch.setattr(
        main_module, "get_forecasts_connection_read_only", lambda: _FakeConn(), raising=False
    )
    _enable_edli_cfg(monkeypatch, enabled=True)

    # Must not raise.
    main_module._edli_market_substrate_warm_cycle()


class _FakeConn:
    """Minimal connection stub: supports the ATTACH/PRAGMA/close calls the warm job
    may make, and is a no-op for everything else."""

    def execute(self, *a, **k):
        class _Cur:
            def fetchall(self_inner):
                return []

            def fetchone(self_inner):
                return None

        return _Cur()

    def commit(self):
        pass

    def close(self):
        pass
