# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: 2026-05-13 ECMWF wedge diagnostic — relationship test for
#   src/runtime/timeout_guard.py. Asserts the cross-module invariant the
#   helper exists to enforce: when ``fn`` is wedged, the caller observes a
#   TimeoutError within ~timeout seconds and is NOT held by the helper's
#   own teardown. Catches the latent ``with ThreadPoolExecutor`` deadlock
#   (shutdown(wait=True) blocking on a wedged worker) that converted the
#   helper into a silent forever-hold.
"""Relationship test — timeout_guard MUST NOT deadlock its caller on wedge."""
from __future__ import annotations

import threading
import time

import pytest

from src.runtime.timeout_guard import run_with_timeout


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
