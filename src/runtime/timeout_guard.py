# Created: 2026-05-13
# Last reused or audited: 2026-09-13
# Authority basis:
#   - 2026-05-13: ECMWF hang antibody bundle — /tmp/zeus_module_audit.md row "rglob on stale mount"
#   - 2026-05-14: ECMWF wedge telemetry — latent deadlock fix. The original
#     `with ThreadPoolExecutor(...) as ex:` form calls `shutdown(wait=True)`
#     on exit, which blocks forever on a wedged worker thread. The TimeoutError
#     raised from `fut.result(timeout=...)` triggers the `with` __exit__ BEFORE
#     the exception propagates — so the caller never sees TimeoutError, just
#     a deadlock. This is the original wedge mode `run_with_timeout` was meant
#     to PREVENT. Fix: manage the executor explicitly, shutdown(wait=False,
#     cancel_futures=True) on timeout, ensuring the raise leaves the function.
#   - 2026-09-13 (T-collateral2): ThreadPoolExecutor itself defeats a one-shot
#     child's ability to exit at all. `concurrent.futures.thread` registers
#     every worker thread it ever creates in the module-global
#     `_threads_queues` dict and its `atexit`-registered `_python_exit()`
#     unconditionally `t.join()`s each one with no timeout — a call this
#     module already made unreachable at the `with`-statement level in 2026-05-14
#     survives one level up, at interpreter shutdown. A wedged worker (e.g. a
#     socket read stuck past its own configured timeout) then blocks process
#     exit indefinitely: `_COLLATERAL_CHILD_EXIT_GRACE_SECONDS` and every other
#     child's exit grace can only ever be satisfied by the parent's SIGKILL,
#     never by the child exiting on its own. Fix: stop using
#     ThreadPoolExecutor. Run `fn` on a bare `threading.Thread(daemon=True)` —
#     daemon threads are never registered with `_python_exit` and are never
#     joined by the interpreter's own shutdown sequence either, so a wedged
#     worker leaks (as documented) without blocking this process, or any
#     process that imports this module, from exiting.
#   Daemon-thread-safe timeout for blocking I/O calls. APScheduler runs jobs in
#   ThreadPoolExecutor workers (see src/ingest_main.py:1141 "fast"/"default"
#   executor pools), so signal.alarm cannot be used (it raises ValueError in
#   non-main threads). This helper runs `fn` on a single daemon thread and
#   bounds it with `Thread.join(timeout=...)` so callers fail loud on stalls
#   (e.g. stale NFS / 51 source data mount) without that thread ever blocking
#   this process's own exit.
"""Thread-safe timeout guard for blocking operations.

Why
---
``signal.alarm`` is the canonical way to interrupt a blocking syscall in
Python — but it only works from the main thread of the main interpreter.
Zeus's ingest daemon runs every cron job inside an APScheduler
``ThreadPoolExecutor`` worker (``src/ingest_main.py``), so any code we
want to fail-fast on a stall must use a thread-based mechanism.

Trade-off
---------
We cannot actually interrupt the blocked thread — Python has no portable
``Thread.kill``. The wedged thread leaks until the next process restart.
What we DO get is:
  * the caller observes a ``TimeoutError`` and can record/log/recover;
  * the daemon's other scheduler jobs continue to run;
  * the next hang has an explicit log line with the operation label;
  * (2026-09-13) the leaked thread is a daemon thread, so it can never keep
    THIS process (or a one-shot subprocess wrapping this call) from exiting
    — the thread leaks, the process does not.

For ``rglob`` against a stale mount or any other I/O call where a 12h
hang would otherwise hold the BULK writer-lock indefinitely (witnessed
2026-05-12 13:31 PDT, see ``/tmp/zeus_ecmwf_critic_review.md``), this is
the right antibody: convert silent forever-block into a loud
``TimeoutError`` at a known boundary.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_with_timeout(
    fn: Callable[[], T],
    *,
    seconds: float,
    label: str,
) -> T:
    """Run ``fn()`` in a worker thread; raise ``TimeoutError`` after ``seconds``.

    Parameters
    ----------
    fn :
        Zero-arg callable. Bind kwargs with ``functools.partial`` or a
        lambda at the call site.
    seconds :
        Wall-clock timeout. Must be > 0.
    label :
        Short human/log identifier for the operation (used in the
        timeout exception message and the warning log line). Keep short
        — appears in production logs.

    Raises
    ------
    TimeoutError
        If ``fn`` does not return within ``seconds``. The underlying
        worker thread is left running (Python has no portable interrupt);
        callers MUST treat the daemon as compromised and not retry blindly.
    Any exception raised by ``fn`` propagates unchanged.
    """
    if seconds <= 0:
        raise ValueError(f"timeout_guard seconds must be > 0, got {seconds}")
    # NOTE 2026-09-13 (T-collateral2): do NOT use ThreadPoolExecutor here.
    # Every worker thread it creates is registered in the module-global
    # `concurrent.futures.thread._threads_queues` and unconditionally
    # `.join()`ed with no timeout by that module's own `atexit` handler
    # (`_python_exit`) — so a wedged worker blocks not just this function's
    # `with`-statement (the 2026-05-14 fix below) but the *process's own
    # exit*, indefinitely. A one-shot subprocess whose whole purpose is to be
    # killable on a bounded deadline (e.g. the collateral-refresh child) can
    # then only ever be reaped by an external SIGKILL, never by exiting on
    # its own once its internal TimeoutError has already fired and been
    # logged. A bare `threading.Thread(daemon=True)` is never registered with
    # `_python_exit` and is never joined by the interpreter's own shutdown
    # sequence either: the wedged thread still leaks (Python has no portable
    # `Thread.kill`) — by design, matching the trade-off documented above —
    # but the process itself remains free to exit the moment this function
    # returns or raises.
    outcome: dict[str, object] = {}

    def _runner() -> None:
        try:
            outcome["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
            outcome["exc"] = exc

    worker = threading.Thread(
        target=_runner,
        name=f"timeout_guard_{label}",
        daemon=True,
    )
    worker.start()
    worker.join(timeout=seconds)
    if worker.is_alive():
        logger.warning(
            "timeout_guard: %s exceeded %.1fs — thread leaked, daemon should restart",
            label,
            seconds,
        )
        raise TimeoutError(f"timeout_guard: {label} exceeded {seconds:.1f}s")
    if "exc" in outcome:
        raise outcome["exc"]  # type: ignore[misc]
    return outcome["result"]  # type: ignore[return-value]


@contextmanager
def timeout_guard(seconds: float, label: str) -> Iterator[Callable[[Callable[[], T]], T]]:
    """Context-manager flavour: ``with timeout_guard(30, "rglob_json_scan") as run: run(lambda: ...)``.

    Provided for call sites that want a more readable inline form than
    ``run_with_timeout(lambda: ..., seconds=30, label="rglob_json_scan")``.
    """

    def _runner(fn: Callable[[], T]) -> T:
        return run_with_timeout(fn, seconds=seconds, label=label)

    yield _runner
