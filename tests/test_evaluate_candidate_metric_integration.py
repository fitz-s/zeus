# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: PR #19 phase 4 closeout completion + phase 1 A3 +
#                  phase 3 reviewer M3 (integration test gap)
"""Slice P5-3 — A3 integration test (minimal coverage).

Phase 1 A3 + A3-fix1 hardened `_normalize_temperature_metric` to raise
on invalid input AND fixed evaluator.py:775 to pass None instead of
`getattr(..., "high")` defaulting. Existing antibody tests at
test_evaluator_metric_normalizer_failclosed.py pin the normalizer's
behavior in isolation.

The integration gap (flagged by phase 1 + phase 3 reviewers): does the
normalizer's raise actually PROPAGATE out of evaluate_candidate to the
caller, or does an outer try/except silently swallow it?

This test drives evaluate_candidate with a candidate whose
temperature_metric is None and asserts the ValueError surfaces at the
caller rather than being suppressed. Covers the A3 contract end-to-end
through the function's entry seam — proves the antibody isn't just
unit-test theatrics.

Heavier-fixture integration tests (full evaluate_candidate execution
with mocked DB + ENS + market data) remain deferred per phase 3 plan §11.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.evaluator import evaluate_candidate


def _candidate(temperature_metric):
    """Duck-typed candidate sufficient to reach evaluator.py:775 normalizer.

    evaluate_candidate accesses .city / .target_date / .outcomes /
    .discovery_mode / .temperature_metric BEFORE the L775 normalizer
    call. SimpleNamespace satisfies all attribute lookups; the test
    short-circuits at L775's ValueError before any deeper code runs.
    """
    return SimpleNamespace(
        city=SimpleNamespace(
            name="NYC", lat=40.7, lon=-74.0,
            timezone="America/New_York",
            settlement_unit="F", cluster="US-Northeast",
            wu_station="KNYC",
        ),
        target_date="2026-04-15",
        outcomes=[],
        hours_since_open=12.0,
        hours_to_resolution=24.0,
        temperature_metric=temperature_metric,
        discovery_mode="",
        observation=None,
        event_id="",
        slug="",
    )


def test_evaluate_candidate_propagates_normalizer_raise_on_none_metric():
    """A3 integration antibody: candidate with temperature_metric=None
    must raise at evaluator.py:775 normalizer call. Pre-A3 the silent
    default to "high" hid this. Post-A3 the ValueError propagates to
    the caller — which is the contract that proves the antibody isn't
    suppressed by an outer try/except."""
    candidate = _candidate(temperature_metric=None)
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        evaluate_candidate(
            candidate,
            conn=None,
            portfolio=None,
            clob=None,
            limits=None,
        )


def test_evaluate_candidate_propagates_raise_on_garbage_string_metric():
    """Same antibody, different invalid input shape. The normalizer is
    case-tolerant ("HIGH" / " low " accepted via .strip().lower()) but
    rejects out-of-domain strings like "medium" / "garbage"."""
    candidate = _candidate(temperature_metric="garbage")
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        evaluate_candidate(
            candidate, conn=None, portfolio=None, clob=None, limits=None,
        )


def test_evaluate_candidate_accepts_case_tolerant_high_metric():
    """Companion: case + whitespace tolerance is INTENTIONAL.
    "HIGH" and " low " must NOT raise at the normalizer.
    (They'll raise later at .city.cluster or DB access — that's a
    different surface; we verify only that L775 doesn't gate them.)"""
    # "HIGH" → "high" via .lower(); should pass L775.
    # Construct a candidate that will raise EARLIER than L775 if L775 doesn't
    # fail, so we can detect "L775 didn't raise on HIGH" by absence of the
    # specific normalizer ValueError.
    candidate = _candidate(temperature_metric="HIGH")
    # Expect SOME error (downstream code accesses None portfolio / clob),
    # but NOT the normalizer's specific message.
    try:
        evaluate_candidate(
            candidate, conn=None, portfolio=None, clob=None, limits=None,
        )
    except ValueError as exc:
        assert "must be 'high' or 'low'" not in str(exc), (
            "L775 normalizer must accept 'HIGH' (case-tolerant); "
            f"got rejection: {exc}"
        )
    except (AttributeError, TypeError):
        pass  # downstream None-access is expected; L775 normalizer didn't gate


def test_evaluate_candidate_propagates_raise_on_empty_metric():
    candidate = _candidate(temperature_metric="")
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        evaluate_candidate(
            candidate, conn=None, portfolio=None, clob=None, limits=None,
        )


def test_evaluate_candidate_propagates_raise_on_int_metric():
    """Numeric input must raise — the normalizer is type-strict."""
    candidate = _candidate(temperature_metric=42)
    with pytest.raises(ValueError, match="must be 'high' or 'low'"):
        evaluate_candidate(
            candidate, conn=None, portfolio=None, clob=None, limits=None,
        )
