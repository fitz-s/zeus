# Created: 2026-09-17
# Authority basis: docs/operations/current/presubmit_receipt_decomposition_2026-09-17.md
#   — the pre-submit receipt store measures p50 0.516s between "decided to trade"
#   and actuate_winner, and a payload-level decomposition attributed only ~138 ms
#   of that median, leaving ~73% in the component builders. These tests pin the
#   attribution instrumentation that replaces that residual with a measurement.
"""Per-stage timing for the pre-submit receipt store.

These cover the instrumentation contract only: that stages accumulate while
collecting, that they are inert otherwise (so monitor/exit callers of the same
builders pay nothing and are never attributed to an auction), and that the
summary orders stages slowest-first so the dominant term reads off the log line.
"""
from __future__ import annotations

import threading

from src.engine.global_batch_runtime import (
    _receipt_stage,
    _receipt_stage_summary,
    _receipt_stages_begin,
    _receipt_stages_end,
)


def test_stage_is_inert_when_not_collecting():
    """A builder called outside a receipt store must not record or raise."""
    _receipt_stages_end()
    with _receipt_stage("delta_candidate_evaluations"):
        pass
    # Nothing to assert beyond "did not raise and did not start collecting":
    # a fresh collection must start empty rather than inherit that call.
    stages = _receipt_stages_begin()
    try:
        assert stages == {}
    finally:
        _receipt_stages_end()


def test_stages_accumulate_count_and_time_per_label():
    stages = _receipt_stages_begin()
    try:
        for _ in range(3):
            with _receipt_stage("delta_book_native_side"):
                pass
        with _receipt_stage("encode_audit_context"):
            pass
    finally:
        _receipt_stages_end()

    assert set(stages) == {"delta_book_native_side", "encode_audit_context"}
    assert stages["delta_book_native_side"][1] == 3
    assert stages["encode_audit_context"][1] == 1
    assert all(total >= 0.0 for total, _count in stages.values())


def test_stage_records_even_when_the_builder_raises():
    """A failed delta still consumed the decision thread's time budget."""
    stages = _receipt_stages_begin()
    try:
        try:
            with _receipt_stage("delta_holding_coverage"):
                raise ValueError("GLOBAL_AUCTION_RECEIPT_CANDIDATE_DELTA_HASH_MISMATCH")
        except ValueError:
            pass
    finally:
        _receipt_stages_end()
    assert stages["delta_holding_coverage"][1] == 1


def test_collection_ends_so_later_calls_are_not_attributed():
    stages = _receipt_stages_begin()
    try:
        with _receipt_stage("encode_candidate_evaluations"):
            pass
    finally:
        _receipt_stages_end()
    with _receipt_stage("encode_candidate_evaluations"):
        pass
    assert stages["encode_candidate_evaluations"][1] == 1


def test_summary_orders_slowest_first():
    summary = _receipt_stage_summary(
        {"fast": [1.0, 1], "slowest": [50.0, 2], "middle": [10.0, 1]}
    )
    assert summary.index("slowest=") < summary.index("middle=")
    assert summary.index("middle=") < summary.index("fast=")
    assert "slowest=50.0ms/2" in summary


def test_summary_of_no_stages_is_explicit():
    assert _receipt_stage_summary({}) == "none"


def test_receipt_builders_are_actually_wrapped_on_the_runtime_path():
    """Antibody: the helpers above all pass even if nothing calls them.

    The defect this guards against is instrumentation that exists but is not
    wired — the "exists-but-unwired" class. Unwrapping any builder or encode in
    `_store_global_auction_receipt` must fail here, so this asserts against the
    module source that each stage label is applied at a real call site rather
    than merely defined.
    """
    import inspect

    from src.engine import global_batch_runtime

    source = inspect.getsource(global_batch_runtime)
    expected_labels = {
        # the five component builders that hold the unattributed ~73%
        "book_native_side_receipt",
        "delta_audit_context",
        "delta_candidate_evaluations",
        "delta_holding_coverage",
        "delta_book_native_side",
        # the zlib-9+base64 encodes measured at 123.6 ms combined
        "encode_minimum_repair",
        "encode_candidate_evaluations",
        "encode_holding_coverage",
        "encode_audit_context",
        # The artifact write itself and the hash over the whole receipt. The
        # first instrumented pass (n=887) attributed only 60 ms of a 354 ms p50
        # to the builders and encodes above, so 83% was still unattributed and
        # these are where the remainder is spent.
        "summary_hash_compact",
        "persist_compact",
        "summary_hash_full",
        "persist_full",
    }
    for label in sorted(expected_labels):
        assert f'_receipt_stage("{label}")' in source, (
            f"receipt stage {label!r} is no longer wrapped on the runtime path; "
            "the pre-submit 516 ms would go unattributed again"
        )
    # And the collector must bracket the store call, or every stage records into
    # a frame nobody reads.
    assert "_receipt_stages_begin()" in source
    assert "_receipt_stages_end()" in source
    assert "stages=%s" in source


def test_collection_is_thread_local():
    """The process also runs monitor/exit work; a shared dict would blend stages."""
    stages = _receipt_stages_begin()
    other: dict[str, list[float]] = {}

    def worker() -> None:
        # No _receipt_stages_begin() here: this thread is not storing a receipt,
        # so its builder call must record nowhere.
        with _receipt_stage("delta_audit_context"):
            pass
        other.update(getattr(__import__(
            "src.engine.global_batch_runtime", fromlist=["_RECEIPT_STAGE_TIMINGS"],
        )._RECEIPT_STAGE_TIMINGS, "stages", None) or {})

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    try:
        assert stages == {}
        assert other == {}
    finally:
        _receipt_stages_end()
