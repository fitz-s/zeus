# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Authority basis: .omc/plans/sqlite_contention_structural_design_v4_2026_05_07.md
#                  Phase 0 §11 — helpers + tests; production callers untouched.
"""Phase 0 tests for src/state/db_writer_lock.py.

Tests in this module cover the helper surface only; no production caller is
exercised. The five Phase 0 tests required by the brief:

  1. test_live_writer_p99_under_bulk_lease_90s — §9.1 CI version
  2. test_max_live_wait_ms_under_350ms — §9.1 max threshold (v4 raised
     from 250→350 ms; resolves v3-critic MF1)
  3. test_chunker_yield_check_or_raise — §9.6 (v4 dual-channel watchdog;
     resolves v3-critic MF5 critical bug)
  4. test_cross_db_attach_no_deadlock_mixed — §9.5 three-subprocess
     mixed cross-DB + standalone (v4 resolves MF3)
  5. test_bulk_chunker_enter_exit_cleanup — context-manager lifecycle;
     no daemon-thread leak

These tests are bench-style and use parametrized-down watchdog
intervals so they finish in < 30 s total.
"""

from __future__ import annotations

import multiprocessing as mp
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from src.state.db_writer_lock import (
    BulkChunker,
    BulkChunkerNotPolledError,
    WriteClass,
    canonical_lock_order,
    db_writer_lock,
    subprocess_run_with_write_class,
    subprocess_with_write_class,
)


# --------------------------------------------------------------------------
# 1+2. Live writer latency under BULK lease (§9.1 CI version)
# --------------------------------------------------------------------------


def _bulk_holder(db_path_str: str, hold_s: float, ready_event: Any) -> None:  # noqa: ANN401
    """Subprocess: acquire bulk flock, signal readiness, hold for hold_s."""
    from pathlib import Path as _P

    from src.state.db_writer_lock import WriteClass, db_writer_lock

    with db_writer_lock(_P(db_path_str), WriteClass.BULK):
        ready_event.set()
        time.sleep(hold_s)


def _live_probe(
    db_path_str: str,
    n_probes: int,
    cadence_s: float,
    measurements: list,
) -> None:
    """In-process probe: acquire LIVE flock repeatedly, record wait times."""
    from pathlib import Path as _P

    from src.state.db_writer_lock import WriteClass, db_writer_lock

    db_path = _P(db_path_str)
    for _ in range(n_probes):
        cycle_start = time.monotonic()
        t0 = time.monotonic()
        with db_writer_lock(db_path, WriteClass.LIVE):
            wait_ms = (time.monotonic() - t0) * 1000.0
            measurements.append(wait_ms)
        # Maintain probe cadence.
        elapsed = time.monotonic() - cycle_start
        sleep_left = cadence_s - elapsed
        if sleep_left > 0:
            time.sleep(sleep_left)


def test_live_writer_p99_under_bulk_lease_90s(tmp_path: Path) -> None:
    """§9.1 CI version (90 s wall-clock budget; parametrized down to ~5 s).

    The CI assertion in plan §3.1.4 is `p99(live_wait_ms) ≤ 100 ms`.
    Phase 0 surface: LIVE and BULK use *different* lock files (per-class
    flock topology, plan §3.1.2), so a BULK lease does NOT block LIVE. p99
    is therefore expected ≈ flock-acquire-only (a few hundred microseconds
    at most). Assert p99 ≤ 100 ms.
    """
    db_path = tmp_path / "zeus_trades.db"
    db_path.touch()

    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    holder = ctx.Process(
        target=_bulk_holder,
        args=(str(db_path), 4.0, ready),
    )
    holder.start()
    try:
        # Wait for BULK holder to signal it has the bulk flock.
        assert ready.wait(timeout=5.0), "bulk holder failed to start"

        measurements: list[float] = []
        # 30 probes at 100 ms cadence = 3 s — well within the 4 s holder window.
        _live_probe(str(db_path), n_probes=30, cadence_s=0.1, measurements=measurements)

        assert len(measurements) == 30, (
            f"expected 30 probes, got {len(measurements)}"
        )
        # Per-class flock isolation: LIVE never blocks on BULK.
        p99 = statistics.quantiles(measurements, n=100)[98]
        assert p99 <= 100.0, (
            f"LIVE p99 wait {p99:.2f} ms exceeds 100 ms budget; "
            f"per-class flock isolation may be broken. "
            f"measurements (max 5)={sorted(measurements, reverse=True)[:5]}"
        )
    finally:
        holder.join(timeout=10.0)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=2.0)


def test_max_live_wait_ms_under_350ms(tmp_path: Path) -> None:
    """§9.1 max-wait threshold (v4 raised 250→350 ms; resolves MF1).

    Compositional ceiling per §3.1.4 is 325 ms; the 350 ms test threshold
    sits one chunk_ms above. Phase 0 has no LIVE-vs-BULK contention path
    yet (Phase 1+ retrofit), so max-wait is dominated by the flock
    syscall. Assert max ≤ 350 ms.
    """
    db_path = tmp_path / "zeus_trades.db"
    db_path.touch()

    measurements: list[float] = []
    _live_probe(str(db_path), n_probes=20, cadence_s=0.05, measurements=measurements)

    assert len(measurements) == 20
    max_wait = max(measurements)
    assert max_wait <= 350.0, (
        f"LIVE max wait {max_wait:.2f} ms exceeds 350 ms threshold "
        f"(plan §9.1 v4)"
    )


# --------------------------------------------------------------------------
# 3. BulkChunker yield-check / dual-channel watchdog (§9.6)
# --------------------------------------------------------------------------


class _StubConn:
    """Stub sqlite3 connection — exercises BulkChunker without a real DB."""

    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_chunker_yield_check_or_raise() -> None:
    """§9.6 — watchdog dual-channel abort surfaces in main thread.

    Phase 0 assertion: when the watchdog fires (cooperative flag set), the
    next call to ``yield_if_live_contended()`` raises
    ``BulkChunkerNotPolledError`` from the main thread (NOT a swallowed
    exception in the daemon thread, NOT just a KeyboardInterrupt).

    This is the v3-critic MF5 critical-bug regression antibody.
    """
    conn = _StubConn()
    # watchdog_s=1, poll every 0.1 s — fires in ~1.1 s.
    with BulkChunker(
        conn,
        caller_module="test_chunker_yield_check_or_raise",
        watchdog_s=1,
        watchdog_poll_s=0.1,
    ) as chunker:
        # Sleep past the watchdog deadline. interrupt_main may inject a
        # KeyboardInterrupt at this point — catch it so the cooperative
        # path is the one we assert on.
        try:
            time.sleep(2.0)
        except KeyboardInterrupt:
            pass
        # Cooperative channel: main-thread call sees the flag and raises
        # the structured exception.
        with pytest.raises(BulkChunkerNotPolledError) as excinfo:
            chunker.yield_if_live_contended()
        assert "test_chunker_yield_check_or_raise" in str(excinfo.value)
        assert "watchdog_s=1" in str(excinfo.value)


def test_chunker_yield_check_resets_clock() -> None:
    """If yield is called inside the deadline, watchdog does NOT fire."""
    conn = _StubConn()
    with BulkChunker(
        conn,
        caller_module="test_chunker_yield_check_resets_clock",
        watchdog_s=2,
        watchdog_poll_s=0.1,
    ) as chunker:
        # Call yield 5 times spaced 0.5 s apart — total 2.5 s > watchdog_s,
        # but each yield resets the clock so watchdog never fires.
        for _ in range(5):
            chunker.yield_if_live_contended()
            time.sleep(0.5)
        # Final yield should still succeed (no abort).
        chunker.yield_if_live_contended()


def test_chunker_commit_chunk_also_checks_abort() -> None:
    """commit_chunk() raises BulkChunkerNotPolledError if watchdog fired."""
    conn = _StubConn()
    with BulkChunker(
        conn,
        caller_module="test_commit_chunk_abort",
        watchdog_s=1,
        watchdog_poll_s=0.1,
    ) as chunker:
        try:
            time.sleep(2.0)
        except KeyboardInterrupt:
            pass
        with pytest.raises(BulkChunkerNotPolledError):
            chunker.commit_chunk()


# --------------------------------------------------------------------------
# 4. Cross-DB ATTACH no-deadlock mixed (§9.5 — three subprocesses)
# --------------------------------------------------------------------------


def _flock_grab_release(
    db_path_str: str,
    write_class_value: str,
    hold_s: float,
    ready_event: Any,
    done_event: Any,
) -> None:
    """Grab one flock, signal ready, hold, then release."""
    from pathlib import Path as _P

    from src.state.db_writer_lock import WriteClass, db_writer_lock

    wc = WriteClass(write_class_value)
    with db_writer_lock(_P(db_path_str), wc):
        ready_event.set()
        time.sleep(hold_s)
    done_event.set()


def _cross_db_canonical_order_grab(
    trade_db: str,
    world_db: str,
    write_class_value: str,
    hold_s: float,
    ready_event: Any,
    done_event: Any,
) -> None:
    """Grab cross-DB flocks in canonical order (alphabetical), hold, release.

    Per plan §3.1.3 the canonical order is alphabetical, so for the
    (zeus-world.db, zeus_trades.db) pair the order is world → trades.
    """
    from pathlib import Path as _P

    from src.state.db_writer_lock import (
        WriteClass,
        canonical_lock_order,
        db_writer_lock,
    )

    wc = WriteClass(write_class_value)
    ordered = canonical_lock_order([_P(trade_db), _P(world_db)])
    # Acquire in canonical order using nested context managers.
    with db_writer_lock(ordered[0], wc):
        with db_writer_lock(ordered[1], wc):
            ready_event.set()
            time.sleep(hold_s)
    done_event.set()


def test_cross_db_attach_no_deadlock(tmp_path: Path) -> None:
    """§9.5 v4 — three subprocesses, mixed cross-DB + standalone.

    Layout:
      A: cross-DB (world, trades) — canonical order acquire
      B: cross-DB (world, trades) — same canonical order (no opposite-order
         deadlock by construction in the helper)
      C: standalone trades-only — joins half-way through A's lease

    All three must complete within 5 s; no OperationalError; no deadlock.
    The mixed case (C standalone trades flock, while A holds it) tests that
    standalone single-DB acquires queue cleanly behind cross-DB acquires
    without livelock.
    """
    trade_db = tmp_path / "zeus_trades.db"
    world_db = tmp_path / "zeus-world.db"
    trade_db.touch()
    world_db.touch()

    ctx = mp.get_context("spawn")
    a_ready = ctx.Event()
    a_done = ctx.Event()
    b_ready = ctx.Event()
    b_done = ctx.Event()
    c_ready = ctx.Event()
    c_done = ctx.Event()

    # A holds cross-DB flocks for 1.5 s.
    a = ctx.Process(
        target=_cross_db_canonical_order_grab,
        args=(str(trade_db), str(world_db), WriteClass.LIVE.value, 1.5, a_ready, a_done),
    )
    # B requests the same cross-DB pair (will wait for A).
    b = ctx.Process(
        target=_cross_db_canonical_order_grab,
        args=(str(trade_db), str(world_db), WriteClass.LIVE.value, 0.2, b_ready, b_done),
    )
    # C requests standalone trades-LIVE flock (will wait for A's trades release).
    c = ctx.Process(
        target=_flock_grab_release,
        args=(str(trade_db), WriteClass.LIVE.value, 0.2, c_ready, c_done),
    )

    a.start()
    assert a_ready.wait(timeout=3.0), "A failed to acquire cross-DB flocks"
    # Launch B and C halfway into A's lease to maximize interleave.
    time.sleep(0.7)
    b.start()
    c.start()

    try:
        # All three should complete within ~5 s of A's start.
        deadline = time.monotonic() + 5.0
        for proc, label in ((a, "A"), (b, "B"), (c, "C")):
            remaining = max(0.1, deadline - time.monotonic())
            proc.join(timeout=remaining)
            assert not proc.is_alive(), f"{label} did not complete (deadlock?)"
            assert proc.exitcode == 0, (
                f"{label} exited with code {proc.exitcode} (expected 0; "
                f"OperationalError?)"
            )
        assert a_done.is_set() and b_done.is_set() and c_done.is_set()
    finally:
        for proc in (a, b, c):
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=2.0)


# --------------------------------------------------------------------------
# 5. BulkChunker __enter__/__exit__ cleanup (no daemon-thread leak)
# --------------------------------------------------------------------------


def test_bulk_chunker_enter_exit_cleanup() -> None:
    """Watchdog thread is joined deterministically on context-manager exit.

    Asserts the v4 §3.1.5 lifecycle: __enter__ starts the watchdog;
    __exit__ sets _closed and joins within 2 s. Without this, daemon
    threads leak across BULK runs.
    """
    conn = _StubConn()

    threads_before = {t.ident for t in threading.enumerate()}

    with BulkChunker(
        conn,
        caller_module="test_enter_exit_cleanup",
        watchdog_s=10,
        watchdog_poll_s=0.05,
    ) as chunker:
        # Watchdog thread should now be running.
        assert chunker._watchdog_thread is not None
        assert chunker._watchdog_thread.is_alive()
        # Mark a yield to keep the watchdog quiet.
        chunker.yield_if_live_contended()

    # After __exit__: watchdog is joined.
    assert chunker._watchdog_thread is not None
    # Allow a tiny grace window for the join to settle.
    chunker._watchdog_thread.join(timeout=1.0)
    assert not chunker._watchdog_thread.is_alive(), (
        "watchdog thread leaked across BulkChunker context exit"
    )

    threads_after = {t.ident for t in threading.enumerate()}
    leaked = threads_after - threads_before
    # No new persistent threads from the chunker. (We tolerate test-runner
    # internals appearing in either set; assert the watchdog is absent.)
    chunker_threads = [
        t for t in threading.enumerate()
        if t.name.startswith("BulkChunker-watchdog-test_enter_exit_cleanup")
    ]
    assert chunker_threads == [], (
        f"BulkChunker watchdog thread leaked: {chunker_threads}"
    )


# --------------------------------------------------------------------------
# Sanity tests for ancillary helpers
# --------------------------------------------------------------------------


def test_subprocess_with_write_class_propagates_env(tmp_path: Path) -> None:
    """subprocess_with_write_class injects ZEUS_DB_WRITE_CLASS into env."""
    out_path = tmp_path / "env.out"
    cmd = [
        sys.executable,
        "-c",
        f"import os, pathlib; "
        f"pathlib.Path({str(out_path)!r}).write_text("
        f"os.environ.get('ZEUS_DB_WRITE_CLASS', '<UNSET>'))",
    ]
    proc = subprocess_with_write_class(cmd, WriteClass.BULK)
    assert proc.wait(timeout=10) == 0
    assert out_path.read_text() == "bulk"


def test_subprocess_run_with_write_class_propagates_env() -> None:
    """subprocess_run_with_write_class injects ZEUS_DB_WRITE_CLASS into env."""
    completed = subprocess_run_with_write_class(
        [
            sys.executable,
            "-c",
            "import os, sys; sys.stdout.write(os.environ.get('ZEUS_DB_WRITE_CLASS','<UNSET>'))",
        ],
        WriteClass.LIVE,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0
    assert completed.stdout == "live"


def test_canonical_lock_order_alphabetical(tmp_path: Path) -> None:
    """canonical_lock_order returns paths sorted by basename (alphabetical)."""
    a = tmp_path / "zeus_trades.db"
    b = tmp_path / "zeus-world.db"
    c = tmp_path / "risk_state.db"
    ordered = canonical_lock_order([a, b, c])
    # Alphabetical by basename: risk_state < zeus-world < zeus_trades
    # (ASCII '-' (0x2d) < '_' (0x5f); the helper uses default string sort).
    assert [p.name for p in ordered] == [
        "risk_state.db",
        "zeus-world.db",
        "zeus_trades.db",
    ]


def test_db_writer_lock_creates_lock_file(tmp_path: Path) -> None:
    """The flock file is created next to the DB if absent."""
    db_path = tmp_path / "zeus_trades.db"
    db_path.touch()
    lock_path = db_path.with_name(db_path.name + ".writer-lock.live")
    assert not lock_path.exists()
    with db_writer_lock(db_path, WriteClass.LIVE):
        assert lock_path.exists()
    # Lock file persists after release (this is fine; flock state is the OS's).
    assert lock_path.exists()


def test_db_writer_lock_separate_files_per_class(tmp_path: Path) -> None:
    """LIVE and BULK use different lock files (plan §3.1.2)."""
    db_path = tmp_path / "zeus_trades.db"
    db_path.touch()
    with db_writer_lock(db_path, WriteClass.LIVE):
        with db_writer_lock(db_path, WriteClass.BULK):
            # Both locks held simultaneously — only possible if they are
            # different files (per-class flock topology).
            live_lock = db_path.with_name(db_path.name + ".writer-lock.live")
            bulk_lock = db_path.with_name(db_path.name + ".writer-lock.bulk")
            assert live_lock.exists()
            assert bulk_lock.exists()
            assert live_lock != bulk_lock


def test_db_writer_lock_blocking_serializes(tmp_path: Path) -> None:
    """Two acquires of the same (db, class) flock serialize."""
    db_path = tmp_path / "zeus_trades.db"
    db_path.touch()

    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    holder = ctx.Process(
        target=_bulk_holder,
        args=(str(db_path), 1.0, ready),
    )
    holder.start()
    try:
        assert ready.wait(timeout=3.0), "holder failed to start"
        # In-process acquire of the SAME class blocks until holder releases.
        t0 = time.monotonic()
        with db_writer_lock(db_path, WriteClass.BULK):
            elapsed_ms = (time.monotonic() - t0) * 1000.0
        # Holder held for 1 s (with ~0 s already elapsed at ready); we
        # should have waited ≥ 500 ms before getting through.
        assert elapsed_ms >= 500.0, (
            f"acquire returned in {elapsed_ms:.1f} ms — holder did not "
            f"actually serialize the lock"
        )
    finally:
        holder.join(timeout=5.0)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=2.0)




# --------------------------------------------------------------------------
# §10.4 — FastPoolExecutor / add_job_with_write_class tests
# --------------------------------------------------------------------------


class _FakeScheduler:
    """Duck-typed APScheduler stand-in. Captures add_job(...) invocations."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def add_job(self, func: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        record = {"func": func, "args": args, "kwargs": kwargs}
        self.jobs.append(record)
        return record


def test_add_job_with_write_class_sets_env_during_call() -> None:
    """add_job_with_write_class wraps func so ZEUS_DB_WRITE_CLASS is set."""
    from src.state.db_writer_lock import add_job_with_write_class
    import os

    sched = _FakeScheduler()
    captured: list[str | None] = []

    def my_tick() -> str:
        captured.append(os.environ.get("ZEUS_DB_WRITE_CLASS"))
        return "ok"

    prior = os.environ.pop("ZEUS_DB_WRITE_CLASS", None)
    try:
        add_job_with_write_class(sched, my_tick, write_class="live")
        assert len(sched.jobs) == 1
        wrapped = sched.jobs[0]["func"]
        assert wrapped() == "ok"
        assert captured == ["live"]
        # Env restored to None after the call.
        assert os.environ.get("ZEUS_DB_WRITE_CLASS") is None
    finally:
        if prior is not None:
            os.environ["ZEUS_DB_WRITE_CLASS"] = prior


def test_add_job_with_write_class_restores_prior_env() -> None:
    """If ZEUS_DB_WRITE_CLASS was already set, the wrapper restores it."""
    from src.state.db_writer_lock import add_job_with_write_class
    import os

    sched = _FakeScheduler()
    prior = os.environ.get("ZEUS_DB_WRITE_CLASS")
    os.environ["ZEUS_DB_WRITE_CLASS"] = "bulk"
    try:
        def f() -> None:
            assert os.environ["ZEUS_DB_WRITE_CLASS"] == "live"

        add_job_with_write_class(sched, f, write_class="live")
        sched.jobs[0]["func"]()
        # Restored to the prior value, not removed.
        assert os.environ["ZEUS_DB_WRITE_CLASS"] == "bulk"
    finally:
        if prior is None:
            os.environ.pop("ZEUS_DB_WRITE_CLASS", None)
        else:
            os.environ["ZEUS_DB_WRITE_CLASS"] = prior


def test_fast_pool_executor_default_write_class() -> None:
    """FastPoolExecutor.add_job uses default_write_class when not overridden."""
    from src.state.db_writer_lock import FastPoolExecutor
    import os

    sched = _FakeScheduler()
    fast = FastPoolExecutor(sched, default_write_class="bulk")
    captured: list[str | None] = []

    def f() -> None:
        captured.append(os.environ.get("ZEUS_DB_WRITE_CLASS"))

    prior = os.environ.pop("ZEUS_DB_WRITE_CLASS", None)
    try:
        fast.add_job(f)
        sched.jobs[0]["func"]()
        assert captured == ["bulk"]
    finally:
        if prior is not None:
            os.environ["ZEUS_DB_WRITE_CLASS"] = prior


def test_fast_pool_executor_per_job_override() -> None:
    """FastPoolExecutor.add_job(write_class=...) overrides the default."""
    from src.state.db_writer_lock import FastPoolExecutor
    import os

    sched = _FakeScheduler()
    fast = FastPoolExecutor(sched, default_write_class="bulk")
    captured: list[str | None] = []

    def f() -> None:
        captured.append(os.environ.get("ZEUS_DB_WRITE_CLASS"))

    prior = os.environ.pop("ZEUS_DB_WRITE_CLASS", None)
    try:
        fast.add_job(f, write_class="live")
        sched.jobs[0]["func"]()
        assert captured == ["live"]
    finally:
        if prior is not None:
            os.environ["ZEUS_DB_WRITE_CLASS"] = prior


def test_fast_pool_executor_passes_through_args_kwargs() -> None:
    """add_job kwargs (id=, max_instances=, ...) reach the scheduler intact."""
    from src.state.db_writer_lock import FastPoolExecutor

    sched = _FakeScheduler()
    fast = FastPoolExecutor(sched)

    def f() -> None:
        pass

    fast.add_job(f, "cron", minute=0, id="my_tick", max_instances=1)
    job = sched.jobs[0]
    assert job["args"] == ("cron",)
    assert job["kwargs"] == {"minute": 0, "id": "my_tick", "max_instances": 1}


def test_fast_pool_executor_resolve_write_class_string_or_enum() -> None:
    """Both 'live' (str) and WriteClass.LIVE (enum) resolve the same way."""
    from src.state.db_writer_lock import FastPoolExecutor, WriteClass

    sched1 = _FakeScheduler()
    sched2 = _FakeScheduler()
    fast1 = FastPoolExecutor(sched1, default_write_class="live")
    fast2 = FastPoolExecutor(sched2, default_write_class=WriteClass.LIVE)
    assert fast1._default is WriteClass.LIVE
    assert fast2._default is WriteClass.LIVE
