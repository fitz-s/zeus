# Created: 2026-05-13
# Last reused or audited: 2026-05-13
# Authority basis: ECMWF hang antibody bundle — /tmp/zeus_module_audit.md row "rglob on stale mount"
#   Daemon-thread-safe timeout for blocking I/O calls. APScheduler runs jobs in
#   ThreadPoolExecutor workers (see src/ingest_main.py:1141 "fast"/"default"
#   executor pools), so signal.alarm cannot be used (it raises ValueError in
#   non-main threads). This helper uses a single-shot ThreadPoolExecutor + .result(timeout=)
#   so callers fail loud on stalls (e.g. stale NFS / 51 source data mount).
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
  * the next hang has an explicit log line with the operation label.

For ``rglob`` against a stale mount or any other I/O call where a 12h
hang would otherwise hold the BULK writer-lock indefinitely (witnessed
2026-05-12 13:31 PDT, see ``/tmp/zeus_ecmwf_critic_review.md``), this is
the right antibody: convert silent forever-block into a loud
``TimeoutError`` at a known boundary.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeoutError
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
    # Each call gets its own single-worker pool — we never want to share
    # a wedged worker between unrelated callers.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"timeout_guard_{label}") as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=seconds)
        except _FutTimeoutError as exc:
            logger.warning(
                "timeout_guard: %s exceeded %.1fs — thread leaked, daemon should restart",
                label,
                seconds,
            )
            # Do NOT shutdown(wait=False) — that's the default behaviour of
            # the context-manager exit, but we want to raise immediately so
            # the with-block exit happens after the raise propagates.
            raise TimeoutError(
                f"timeout_guard: {label} exceeded {seconds:.1f}s"
            ) from exc


@contextmanager
def timeout_guard(seconds: float, label: str) -> Iterator[Callable[[Callable[[], T]], T]]:
    """Context-manager flavour: ``with timeout_guard(30, "rglob_json_scan") as run: run(lambda: ...)``.

    Provided for call sites that want a more readable inline form than
    ``run_with_timeout(lambda: ..., seconds=30, label="rglob_json_scan")``.
    """

    def _runner(fn: Callable[[], T]) -> T:
        return run_with_timeout(fn, seconds=seconds, label=label)

    yield _runner
