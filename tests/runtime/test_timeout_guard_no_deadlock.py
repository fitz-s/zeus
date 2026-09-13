# Created: 2026-05-14
# Last reused or audited: 2026-09-13
# Authority basis: 2026-05-13 ECMWF wedge diagnostic — relationship test for
#   src/runtime/timeout_guard.py. Asserts the cross-module invariant the
#   helper exists to enforce: when ``fn`` is wedged, the caller observes a
#   TimeoutError within ~timeout seconds and is NOT held by the helper's
#   own teardown. Catches the latent ``with ThreadPoolExecutor`` deadlock
#   (shutdown(wait=True) blocking on a wedged worker) that converted the
#   helper into a silent forever-hold.
#   - 2026-09-13 (T-collateral2): added a one-shot-subprocess variant of the
#     same invariant. A wedged fn's internal TimeoutError firing inside
#     run_with_timeout is not the same guarantee as the *process* being able
#     to exit afterward — concurrent.futures.thread's atexit handler joins
#     every worker thread it ever created, with no timeout, which blocked a
#     one-shot child (e.g. the collateral-refresh child,
#     src/ingest/post_trade_capital_daemon.py::_collateral_snapshot_refresh_isolated)
#     from ever exiting on its own; only the parent's outer SIGKILL ever
#     reaped it (1,020 "collateral refresh child exceeded 25.0s and was
#     killed" log lines on 2026-09-13). This test reproduces that shape
#     directly: a wedged run_with_timeout call inside a one-shot subprocess,
#     bounded by an outer subprocess.run(timeout=) that is deliberately much
#     larger than the inner timeout — before the fix this always hit the
#     outer SIGKILL; after the fix the child exits at ~its own deadline.
"""Relationship test — timeout_guard MUST NOT deadlock its caller on wedge."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.runtime.timeout_guard import run_with_timeout

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_run_with_timeout_returns_to_caller_when_worker_wedges() -> None:
    """When ``fn`` blocks longer than ``seconds``, the caller MUST see a
    TimeoutError within ~2× the configured timeout. A naive implementation
    that uses ``with ThreadPoolExecutor(...) as ex:`` will deadlock here:
    shutdown(wait=True) at the context-manager exit waits forever for the
    wedged worker thread, so the TimeoutError never propagates.
    """
    # Use an Event so the wedged thread can be released cleanly at test end;
    # we never actually release it during the timing window — we want a real
    # wedge for the duration of the timeout assertion.
    release = threading.Event()

    def wedged() -> None:
        # Block far longer than the timeout; only release at teardown.
        release.wait(timeout=10.0)

    timeout_s = 0.5
    deadline = timeout_s * 4.0  # generous: 2s for a 0.5s timeout
    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match="timeout_guard: wedge_test"):
        run_with_timeout(wedged, seconds=timeout_s, label="wedge_test")
    elapsed = time.monotonic() - t0

    # Release the leaked worker thread so the test process can shut down cleanly.
    release.set()

    assert elapsed < deadline, (
        f"run_with_timeout deadlocked: elapsed={elapsed:.2f}s exceeded "
        f"{deadline:.2f}s budget for a {timeout_s:.2f}s timeout. "
        "The helper must NOT wait on its own wedged worker thread."
    )


def test_run_with_timeout_normal_return_still_works() -> None:
    """Success path: fast fn returns its value normally."""
    result = run_with_timeout(lambda: 42, seconds=1.0, label="fast_path")
    assert result == 42


def test_run_with_timeout_propagates_fn_exception() -> None:
    """Exceptions from ``fn`` propagate unchanged (not wrapped as TimeoutError)."""

    class _Marker(RuntimeError):
        pass

    def boom() -> None:
        raise _Marker("inner failure")

    with pytest.raises(_Marker, match="inner failure"):
        run_with_timeout(boom, seconds=1.0, label="boom_path")


def test_run_with_timeout_lets_a_one_shot_child_exit_before_the_outer_kill() -> None:
    """T-collateral2 probe 1: a wedged ``run_with_timeout`` call, wrapped in a
    one-shot child process, MUST exit on its own once its internal
    ``TimeoutError`` fires — it must not survive to be SIGKILLed by an outer
    ``subprocess.run(timeout=...)`` (the collateral-refresh child's own
    wrapper shape: ``subprocess.run([...], timeout=deadline+grace)``).

    Before the fix (``ThreadPoolExecutor`` worker thread): the worker thread
    is registered with ``concurrent.futures.thread``'s own ``atexit`` handler,
    which unconditionally ``.join()``s it with no timeout, so the child can
    never exit while the worker is wedged — this test would time out at the
    outer 5s bound (returncode -9 / SIGKILL) every run.

    After the fix (bare daemon thread): the child raises TimeoutError, logs
    it, and exits at ~2s — well inside the outer 5s bound.
    """
    child_code = (
        "import sys, time\n"
        "from src.runtime.timeout_guard import run_with_timeout\n"
        "try:\n"
        "    run_with_timeout(lambda: time.sleep(30), seconds=2, label='oneshot_probe')\n"
        "except TimeoutError:\n"
        "    sys.exit(0)\n"
        "sys.exit(1)\n"
    )

    t0 = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", child_code],
        cwd=_REPO_ROOT,
        timeout=5,
        capture_output=True,
        text=True,
    )
    elapsed = time.monotonic() - t0

    assert result.returncode == 0, (
        "child did not exit cleanly on its own internal TimeoutError: "
        f"returncode={result.returncode} stderr={result.stderr!r}"
    )
    assert elapsed < 4.0, (
        f"child took {elapsed:.2f}s to exit an inner 2s timeout — it should exit "
        "at ~its own deadline, not survive toward the outer 5s subprocess bound "
        "(a leaked non-daemon worker thread blocking process exit)."
    )
