# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T2F/phase.json
"""Unit tests for src/observability/counters.py typed counter sink.

Asserted invariants:
  T2F-COUNTER-SINK-TYPED-API:
    - increment(name, labels=...) -> None; read(name, labels=...) -> int
    - Thread-safe (threading.Lock)
    - Un-incremented (name, labels) returns 0
    - Negative delta raises ValueError
    - Different label dicts increment independently (test_counter_increment_read_isolated_per_label_set)
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

import src.observability.counters as _mod
from src.observability.counters import increment, read, reset_all


# ---------------------------------------------------------------------------
# Fixture: isolate counter state per test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_counters():
    """Reset all counters before and after each test for isolation."""
    reset_all()
    yield
    reset_all()


# ---------------------------------------------------------------------------
# Basic read/increment
# ---------------------------------------------------------------------------

def test_read_unincremented_returns_zero():
    """Un-incremented (name, labels) returns 0."""
    assert read("never_incremented") == 0
    assert read("never_incremented", labels={"field": "x"}) == 0


def test_increment_and_read_basic():
    """increment then read returns 1."""
    increment("my_counter")
    assert read("my_counter") == 1


def test_increment_multiple_times_is_monotonic():
    """Multiple increments accumulate monotonically."""
    for i in range(1, 6):
        increment("mono_counter")
        assert read("mono_counter") == i


def test_increment_with_delta():
    """increment(delta=5) adds 5 in one call."""
    increment("delta_counter", delta=5)
    assert read("delta_counter") == 5


def test_increment_accumulates_across_delta_calls():
    """Successive delta calls accumulate."""
    increment("acc_counter", delta=3)
    increment("acc_counter", delta=7)
    assert read("acc_counter") == 10


# ---------------------------------------------------------------------------
# Label isolation (T2F-COUNTER-SINK-TYPED-API named test)
# ---------------------------------------------------------------------------

def test_counter_increment_read_isolated_per_label_set():
    """Counters with different label dicts increment independently.

    This is the named test cited in T2F-COUNTER-SINK-TYPED-API invariant text.
    """
    name = "cost_basis_chain_mutation_blocked_total"

    increment(name, labels={"field": "entry_price"})
    increment(name, labels={"field": "entry_price"})
    increment(name, labels={"field": "cost_basis_usd"})
    increment(name, labels={"field": "shares"})

    assert read(name, labels={"field": "entry_price"}) == 2
    assert read(name, labels={"field": "cost_basis_usd"}) == 1
    assert read(name, labels={"field": "shares"}) == 1
    # size_usd was never incremented
    assert read(name, labels={"field": "size_usd"}) == 0
    # no-label bucket is separate from any label bucket
    assert read(name) == 0


def test_no_label_and_empty_label_dict_are_equivalent():
    """labels=None and labels={} resolve to the same bucket."""
    increment("bucket_test", labels=None)
    increment("bucket_test", labels={})
    assert read("bucket_test", labels=None) == 2
    assert read("bucket_test", labels={}) == 2


def test_different_names_do_not_share_state():
    """Distinct counter names are fully independent."""
    increment("counter_a")
    increment("counter_b")
    increment("counter_b")
    assert read("counter_a") == 1
    assert read("counter_b") == 2


def test_label_order_does_not_matter():
    """Label dicts with the same keys/values but different insertion order map to same bucket."""
    increment("order_test", labels={"a": "1", "b": "2"})
    # Reverse insertion order — must map to same frozenset key
    assert read("order_test", labels={"b": "2", "a": "1"}) == 1


# ---------------------------------------------------------------------------
# Negative delta rejection
# ---------------------------------------------------------------------------

def test_negative_delta_raises_value_error():
    """increment with delta < 0 raises ValueError."""
    with pytest.raises(ValueError, match="delta must be positive"):
        increment("bad_counter", delta=-1)


def test_zero_delta_raises_value_error():
    """increment with delta=0 raises ValueError (not a meaningful increment)."""
    with pytest.raises(ValueError, match="delta must be positive"):
        increment("zero_counter", delta=0)


def test_counter_not_mutated_after_rejected_delta():
    """A rejected negative/zero delta does not mutate the counter."""
    increment("safe_counter", delta=3)
    with pytest.raises(ValueError):
        increment("safe_counter", delta=-1)
    assert read("safe_counter") == 3


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

def test_thread_safe_concurrent_increments():
    """1000 concurrent increments from 10 threads yield exactly 10000."""
    name = "thread_counter"
    n_threads = 10
    n_increments = 1000

    def _worker():
        for _ in range(n_increments):
            increment(name)

    threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert read(name) == n_threads * n_increments


def test_thread_safe_concurrent_labeled_increments():
    """Labeled concurrent increments from 5 threads per label yield exact counts."""
    name = "threaded_labeled"
    labels_a = {"reason": "label_a"}
    labels_b = {"reason": "label_b"}
    n_threads = 5
    n_each = 200

    def _worker_a():
        for _ in range(n_each):
            increment(name, labels=labels_a)

    def _worker_b():
        for _ in range(n_each):
            increment(name, labels=labels_b)

    threads = (
        [threading.Thread(target=_worker_a) for _ in range(n_threads)]
        + [threading.Thread(target=_worker_b) for _ in range(n_threads)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert read(name, labels=labels_a) == n_threads * n_each
    assert read(name, labels=labels_b) == n_threads * n_each


# ---------------------------------------------------------------------------
# reset_all isolation
# ---------------------------------------------------------------------------

def test_reset_all_clears_all_counters():
    """reset_all() returns all counters to 0."""
    increment("r1")
    increment("r2", labels={"x": "y"})
    assert read("r1") == 1
    reset_all()
    assert read("r1") == 0
    assert read("r2", labels={"x": "y"}) == 0


# ---------------------------------------------------------------------------
# emit_typed_counter
# ---------------------------------------------------------------------------

def test_emit_typed_counter_increments_and_calls_log_fn():
    """emit_typed_counter calls increment AND log_fn."""
    from src.observability.counters import emit_typed_counter

    calls: list[tuple] = []

    def fake_log(msg, *args):
        calls.append((msg,) + args)

    emit_typed_counter(
        "test_emit_event",
        {"field": "entry_price"},
        fake_log,
        "telemetry_counter event=test_emit_event field=%s",
        "entry_price",
    )

    assert read("test_emit_event", labels={"field": "entry_price"}) == 1
    assert len(calls) == 1
    assert "telemetry_counter event=test_emit_event" in calls[0][0]


def test_emit_typed_counter_no_labels():
    """emit_typed_counter with labels=None works (no-label bucket)."""
    from src.observability.counters import emit_typed_counter

    logged = []
    emit_typed_counter(
        "no_label_event",
        None,
        logged.append,
        "telemetry_counter event=no_label_event",
    )
    assert read("no_label_event") == 1
    assert logged == ["telemetry_counter event=no_label_event"]
