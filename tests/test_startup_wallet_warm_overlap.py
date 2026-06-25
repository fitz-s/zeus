# Created: 2026-06-05
# Last reused/audited: 2026-06-19
# Authority basis: efficiency #3 boot wallet warm-overlap (background warm -> join -> gate) + live monitor continuity on wallet RPC faults
# Lifecycle: created=2026-06-05; last_reviewed=2026-06-19; last_reused=2026-06-19
# Purpose: Relationship/concurrency antibody — boot warms bankroll_provider.current() on a background thread, joins it, then the wallet gate consumes the joined record (warm + gate = exactly ONE on-chain fetch; None -> submit fail-closed without killing monitor).
# Reuse: Re-run when _start_boot_wallet_warm / _join_boot_wallet_warm / _startup_wallet_check or the bankroll_provider warm-cache TTL changes.
"""Relationship test — boot wallet warm-overlap (concurrency invariant).

Cross-module / concurrency invariant under test (efficiency #3, built on the
#1 dedupe at 38ddec092e):

  At boot, the on-chain wallet RPC is network-bound (5-30s, ~38/hr blips) while
  the schema-ready gate / registry assert / f109 consolidator / freshness /
  boot-guards are DB-bound. #1 already collapsed the two on-chain fetches into
  one. #3 overlaps that ONE fetch with the DB-bound boot work: a daemon thread
  warms bankroll_provider.current() starting right after the venue heartbeat,
  the DB steps run concurrently, then the warm thread is JOINED immediately
  before the wallet gate so the gate stays deterministic (warm cache, no race).

Boundary properties asserted here (the three the brief enumerates):

  (1) SINGLE ACQUISITION across the whole boot — the warm thread and the gate
      together issue exactly ONE bankroll_provider.current()-level acquisition.
      The warm thread fetches; the gate CONSUMES the record the warm produced
      (handed across the join) instead of calling current() a second time.
      Counted via the conftest seam (stub bankroll_provider.current with a
      counter) used by tests/test_startup_wallet_dedup.py.

  (2) JOIN-BEFORE-GATE — _join_boot_wallet_warm() joins the daemon thread, so
      by the time _startup_wallet_check runs the warm result is already present
      (deterministic; no race). We prove the thread is finished at join return
      and that the gate sees the warm record (current() not re-invoked).

  (3) WARM EXCEPTION DOES NOT CRASH BOOT, gate still fail-closes submit — if the warm
      thread's current() raises, the exception is swallowed+logged (boot never
      dies in the warm thread), the handed record is None, and the gate's
      None-record path keeps daemon startup alive while downstream submit/sizing
      remains fail-closed on the cold bankroll cache.

It also asserts the STRUCTURAL overlap exists on the production main() body
(warm spawn after the heartbeat, before the schema-ready gate; join immediately
before the wallet gate) — this is the part that goes RED on 38ddec092e, where
there is no warm thread at all.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys
import types

import pytest

import src.main as main_mod
from src.runtime import bankroll_provider
from src.state import collateral_ledger


@pytest.fixture(autouse=True)
def _reset_ledger_global():
    collateral_ledger.configure_global_ledger(None)
    yield
    collateral_ledger.configure_global_ledger(None)


def _record(value=123.45):
    return bankroll_provider.BankrollOfRecord(
        value_usd=value,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source="polymarket_wallet",
        authority="canonical",
        staleness_seconds=0.0,
        cached=False,
    )


class _CurrentCounter:
    """Counts bankroll_provider.current() calls; optionally raises or returns None."""

    def __init__(self, value=123.45, returns_none=False, raises=False):
        self.calls = 0
        self._value = value
        self._returns_none = returns_none
        self._raises = raises

    def __call__(self, **_kwargs):
        self.calls += 1
        if self._raises:
            raise RuntimeError("warm_wallet_rpc_blew_up")
        return None if self._returns_none else _record(self._value)


# ---------------------------------------------------------------------------
# (1) SINGLE ACQUISITION — warm fetches, gate consumes the handed record.
# ---------------------------------------------------------------------------
def test_warm_then_gate_is_single_current_acquisition(monkeypatch):
    """Warm thread + gate together call bankroll_provider.current() EXACTLY ONCE.

    The warm thread is the single acquisition; the gate consumes the record the
    warm handed across the join (it does NOT call current() again). On
    38ddec092e there is no warm thread / no _start_boot_wallet_warm seam, so
    this test cannot even be constructed (AttributeError) → RED.
    """
    counter = _CurrentCounter(value=123.45)
    monkeypatch.setattr(bankroll_provider, "current", counter)

    # Warm the wallet on a background daemon thread, then join it (the boot
    # overlap window collapses to instantaneous under a stub current()).
    thread, holder = main_mod._start_boot_wallet_warm()
    main_mod._join_boot_wallet_warm(thread)

    assert counter.calls == 1, (
        f"warm thread did not perform exactly one current() acquisition "
        f"(calls={counter.calls})"
    )

    # The gate consumes the warm record — NO second current() call.
    main_mod._startup_wallet_check(clob=None, bankroll_record=holder.record)

    assert counter.calls == 1, (
        f"gate issued a SECOND current() acquisition instead of consuming the "
        f"warm record (calls={counter.calls}); warm-overlap dedupe broken."
    )
    assert collateral_ledger.get_global_ledger() is not None, (
        "CollateralLedger global singleton not installed on the warm-consume path"
    )


# ---------------------------------------------------------------------------
# (2) JOIN-BEFORE-GATE — thread finished and record present at join return.
# ---------------------------------------------------------------------------
def test_warm_thread_is_joined_before_gate(monkeypatch):
    """After _join_boot_wallet_warm() returns, the warm thread is finished and the
    warm record is populated — the gate runs against a deterministic warm cache,
    never a live race."""
    counter = _CurrentCounter(value=321.0)
    monkeypatch.setattr(bankroll_provider, "current", counter)

    thread, holder = main_mod._start_boot_wallet_warm()
    main_mod._join_boot_wallet_warm(thread)

    assert not thread.is_alive(), "warm thread still alive after join — race window open"
    assert holder.record is not None, "warm record not present after join"
    assert holder.record.value_usd == 321.0


# ---------------------------------------------------------------------------
# (3) WARM EXCEPTION DOES NOT CRASH BOOT; gate fail-closes on cold cache.
# ---------------------------------------------------------------------------
def test_warm_exception_does_not_crash_boot_and_submit_stays_fail_closed(monkeypatch):
    """Warm thread current() raises → exception swallowed+logged (thread/join do
    NOT propagate) → holder.record is None (cold cache) → gate's None-record path
    does not crash monitoring/redecision, and the cold bankroll cache keeps
    submit/sizing fail-closed."""
    counter = _CurrentCounter(raises=True)
    monkeypatch.setattr(bankroll_provider, "current", counter)

    # The warm thread must NOT raise out of start/join.
    thread, holder = main_mod._start_boot_wallet_warm()
    main_mod._join_boot_wallet_warm(thread)  # must not raise

    assert not thread.is_alive()
    assert holder.record is None, "warm exception must leave a cold (None) record"

    main_mod._startup_wallet_check(clob=None, bankroll_record=holder.record)
    assert bankroll_provider.cached() is None


# ---------------------------------------------------------------------------
# STRUCTURAL OVERLAP — warm spawn after heartbeat & before schema gate; join
# immediately before the wallet gate. This is the RED anchor on 38ddec092e.
# ---------------------------------------------------------------------------
def test_main_overlaps_wallet_warm_with_db_boot_work():
    source = Path(main_mod.__file__).read_text()
    body = source[source.index("def main():"):]

    heartbeat = body.index("_start_venue_heartbeat_loop_if_needed()")
    warm_spawn = body.index("_start_boot_wallet_warm()")
    schema_gate = body.index("_startup_world_schema_ready_check()")
    warm_join = body.index("_join_boot_wallet_warm(")
    wallet_gate = body.index("_startup_wallet_check(")

    # Heartbeat stays before the wallet RPC (heartbeat-before-boot-http invariant).
    assert heartbeat < warm_spawn, "warm wallet RPC spawned before venue heartbeat"
    # Warm spawns BEFORE the DB-bound boot work so they overlap.
    assert warm_spawn < schema_gate, "warm not spawned before the schema-ready DB gate"
    # The DB-bound boot work runs between spawn and join (the overlap window).
    assert schema_gate < warm_join, "schema gate not inside the warm/join overlap window"
    # Join happens immediately before the deterministic wallet gate.
    assert warm_join < wallet_gate, "warm thread not joined before the wallet gate"


def test_startup_data_health_check_uses_existence_probes_for_large_tables(monkeypatch):
    """Startup reminders must not full-scan large live tables before scheduler boot."""

    class _Cursor:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _Conn:
        def __init__(self):
            self.sql: list[str] = []

        def execute(self, sql):
            self.sql.append(sql)
            if "COUNT(DISTINCT city) FROM forecast_skill" in sql:
                return _Cursor([len(main_mod.cities_by_name)])
            if "COUNT(DISTINCT city) FROM model_bias" in sql:
                return _Cursor([len(main_mod.cities_by_name)])
            if "COUNT(*) FROM model_bias" in sql:
                return _Cursor([0])
            return _Cursor((1,))

    fake_validation = types.SimpleNamespace(
        run_validation=lambda: {"valid": True, "mismatches": []}
    )
    monkeypatch.setitem(sys.modules, "scripts.validate_assumptions", fake_validation)

    conn = _Conn()
    main_mod._startup_data_health_check(conn)

    large_tables = {
        "asos_wu_offsets",
        "observation_instants",
        "diurnal_curves",
        "diurnal_peak_prob",
        "temp_persistence",
        "solar_daily",
    }
    for table in large_tables:
        assert any(
            f"SELECT 1 FROM {table} LIMIT 1" in sql for sql in conn.sql
        ), f"{table} should use bounded existence probe"
        assert not any(
            f"SELECT COUNT(*) FROM {table}" in sql for sql in conn.sql
        ), f"{table} must not be full-counted during startup"
