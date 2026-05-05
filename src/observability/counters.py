# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T2F/phase.json
"""Typed in-process counter sink.

Provides a thread-safe, monotonically-incrementing counter primitive
that allows read-back by name and optional label dict. This is the
canonical typed counter sink for Zeus telemetry.

T2F invariants:
  T2F-COUNTER-SINK-TYPED-API: increment/read API with label isolation.
  T2F-EVERY-T1-COUNTER-EMITS-VIA-SINK: all T1 call sites wire through here.

Design decisions (T2F phase.json §_planner_notes):
  - In-process only; NO persistence to disk (deferred to future phase).
  - Thread-safe via threading.Lock per the typed API spec.
  - Negative deltas rejected at call time with ValueError.
  - Labels stored as frozenset of (key, value) pairs for dict-key safety.
  - Reading an un-incremented (name, labels) pair returns 0.

emit_typed_counter(name, labels, log_fn, message, *args):
  Convenience helper that calls both increment() AND the legacy log
  line in a single call. Use this to co-locate the typed increment
  with the existing logger.warning line without reformatting the message.
"""

from __future__ import annotations

import threading
from typing import Callable

__all__ = ["increment", "read", "reset_all", "emit_typed_counter"]

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_lock: threading.Lock = threading.Lock()
# Key: (name, frozenset_of_label_items) -> int
_counters: dict[tuple[str, frozenset], int] = {}


def _make_key(name: str, labels: dict[str, str] | None) -> tuple[str, frozenset]:
    """Build a hashable dict key from name + optional label dict."""
    if labels:
        return (name, frozenset(labels.items()))
    return (name, frozenset())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def increment(
    name: str,
    *,
    labels: dict[str, str] | None = None,
    delta: int = 1,
) -> None:
    """Increment a named counter by delta (default 1).

    Args:
        name: Counter event name (e.g. "compat_submit_rejected_total").
        labels: Optional label dict (e.g. {"field": "entry_price"}).
        delta: Positive integer increment. ValueError on negative or zero.

    Raises:
        ValueError: If delta is <= 0.
    """
    if delta <= 0:
        raise ValueError(
            f"Counter delta must be positive; got delta={delta!r} for counter {name!r}"
        )
    key = _make_key(name, labels)
    with _lock:
        _counters[key] = _counters.get(key, 0) + delta


def read(
    name: str,
    *,
    labels: dict[str, str] | None = None,
) -> int:
    """Read the current value of a counter. Returns 0 if never incremented.

    Args:
        name: Counter event name.
        labels: Optional label dict; must match exactly the labels passed to increment().

    Returns:
        Current counter value (>= 0).
    """
    key = _make_key(name, labels)
    with _lock:
        return _counters.get(key, 0)


def reset_all() -> None:
    """Reset all counters to zero. Intended for test isolation only.

    Production code must not call this — it is a test-support primitive.
    """
    with _lock:
        _counters.clear()


def emit_typed_counter(
    name: str,
    labels: dict[str, str] | None,
    log_fn: Callable[..., None],
    message: str,
    *args: object,
) -> None:
    """Increment the typed counter AND emit the legacy log line.

    This is the recommended call pattern for T1 call sites: it preserves
    the existing logger.warning(...) telemetry_counter string verbatim so
    log-grep observability is not regressed, while also wiring the typed
    sink increment.

    Example:
        emit_typed_counter(
            "compat_submit_rejected_total",
            None,
            logger.warning,
            "telemetry_counter event=compat_submit_rejected_total path=submit_limit_order",
        )

    Args:
        name: Counter event name string.
        labels: Optional label dict passed to increment().
        log_fn: Callable to emit the log line (e.g. logger.warning).
        message: Log message string (first positional arg to log_fn).
        *args: Additional positional args forwarded to log_fn.
    """
    increment(name, labels=labels)
    log_fn(message, *args)
