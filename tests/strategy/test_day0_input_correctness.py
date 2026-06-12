# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: docs/operations/day0_multiangle_critique_2026-06-12.md Blind
#   spot C, re-scoped 2026-06-12 (operator anti-over-design: correctness check,
#   NOT a cap). Antibodies for the quote-after-observation input-ordering gate.
"""Antibody tests: day0 quote-after-observation input-ordering correctness.

The cross-module invariant: a day0 decision's orderbook snapshot (quote) must be
captured strictly AFTER the observation availability that produced its
probability. The check fires ONLY on a genuine inversion; it is not a configurable
window and has no notion of a "quiet period".
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.strategy.live_inference.day0_input_correctness import (
    Day0InputOrderingVerdict,
    evaluate_quote_after_observation,
)

UTC = timezone.utc


def test_quote_after_observation_passes_when_quote_is_newer():
    v = evaluate_quote_after_observation(
        quote_captured_at="2026-05-24T14:30:00+00:00",
        observation_available_at="2026-05-24T14:05:00+00:00",
    )
    assert v.applicable is True
    assert v.rejection_reason is None
    assert v.lag_seconds == 25 * 60.0
    assert "ok" in v.annotation


def test_quote_before_observation_is_rejected():
    v = evaluate_quote_after_observation(
        quote_captured_at="2026-05-24T14:00:00+00:00",
        observation_available_at="2026-05-24T14:05:00+00:00",
    )
    assert v.applicable is True
    assert v.rejection_reason is not None
    assert v.rejection_reason.startswith("DAY0_QUOTE_PRECEDES_OBSERVATION")
    assert v.lag_seconds == -5 * 60.0
    assert "VIOLATED" in v.annotation


def test_quote_equal_to_observation_is_rejected_strict_ordering():
    """Boundary: equal timestamps are NOT strictly-after -> rejected (the quote
    does not price a post-update book)."""
    ts = "2026-05-24T14:05:00+00:00"
    v = evaluate_quote_after_observation(
        quote_captured_at=ts, observation_available_at=ts
    )
    assert v.rejection_reason is not None
    assert v.rejection_reason.startswith("DAY0_QUOTE_PRECEDES_OBSERVATION")
    assert v.lag_seconds == 0.0


def test_datetime_inputs_accepted():
    v = evaluate_quote_after_observation(
        quote_captured_at=datetime(2026, 5, 24, 14, 30, tzinfo=UTC),
        observation_available_at=datetime(2026, 5, 24, 14, 5, tzinfo=UTC),
    )
    assert v.rejection_reason is None
    assert v.lag_seconds == 25 * 60.0


def test_naive_datetime_treated_as_utc():
    v = evaluate_quote_after_observation(
        quote_captured_at=datetime(2026, 5, 24, 14, 30),  # naive
        observation_available_at=datetime(2026, 5, 24, 14, 5),  # naive
    )
    assert v.rejection_reason is None


def test_missing_timestamp_is_not_applicable_not_a_rejection():
    """A missing/unparseable timestamp makes the check NOT applicable — it never
    invents an inversion. The caller's honest-data freshness gates own the
    missing-data case; this check does not duplicate them."""
    v = evaluate_quote_after_observation(
        quote_captured_at="2026-05-24T14:30:00+00:00",
        observation_available_at=None,
    )
    assert v.applicable is False
    assert v.rejection_reason is None
    assert "not_applicable" in v.annotation

    v2 = evaluate_quote_after_observation(
        quote_captured_at="not-a-timestamp",
        observation_available_at="2026-05-24T14:05:00+00:00",
    )
    assert v2.applicable is False
    assert v2.rejection_reason is None


def test_rejection_reason_base_is_registered():
    """The rejection reason base must be a registered DESIGNED_GATE."""
    from src.contracts.rejection_reasons import (
        RejectionCategory,
        base_reason,
        classify_rejection_reason,
        is_registered_rejection_reason,
    )

    v = evaluate_quote_after_observation(
        quote_captured_at="2026-05-24T14:00:00+00:00",
        observation_available_at="2026-05-24T14:05:00+00:00",
    )
    assert is_registered_rejection_reason(v.rejection_reason)
    assert base_reason(v.rejection_reason) == "DAY0_QUOTE_PRECEDES_OBSERVATION"
    assert classify_rejection_reason(v.rejection_reason) == RejectionCategory.DESIGNED_GATE


def test_verdict_is_frozen_dataclass():
    v = evaluate_quote_after_observation(
        quote_captured_at="2026-05-24T14:30:00+00:00",
        observation_available_at="2026-05-24T14:05:00+00:00",
    )
    assert isinstance(v, Day0InputOrderingVerdict)
