# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: pr36_scaffold.md §5 — R-3.1 through R-3.5 + R-3.NEW-a/b/c
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Relationship tests for DecisionSourceContext PR3 observation-chain ordering validators (R-3.x).
# Reuse: relationship tests only; Path F (provider_reported_time=None) conditional suppression verified here.
"""Relationship tests for DecisionSourceContext PR 3 timing chain.

Tests the observation-chain ordering validators (R-3.x) added by PR 3:
- obs_after_provider (conditional — only when provider_reported_time is populated)
- provider_after_available (conditional)
- available_after_decision (unconditional)

Path F: provider_reported_time=None means "source doesn't expose separate reported-at".
Conditional validators MUST NOT fire when provider_reported_time is None.
"""

import pytest

from src.contracts.execution_intent import DecisionSourceContext, _PR3_REQUIRED_FIELDS_EPOCH
from src.contracts.snapshot_ingest_contract import CausalityStatus, INTEGRITY_ERROR_TO_CAUSALITY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pr3_ctx(**kwargs) -> DecisionSourceContext:
    """Build a minimal DecisionSourceContext that satisfies PR3 required-field gate.

    Uses decision_time >= _PR3_REQUIRED_FIELDS_EPOCH so gating is active.
    All required fields have valid defaults; override via kwargs.
    """
    defaults = dict(
        source_id="ecmwf_open_data",
        decision_time="2026-05-19T10:00:00Z",
        observation_time="2026-05-19T09:55:00Z",
        provider_reported_time="2026-05-19T09:54:00Z",
        observation_available_at="2026-05-19T09:56:00Z",
        polymarket_end_anchor_source="gamma_explicit",
        first_member_observed_time="2026-05-19T06:00:00Z",
        run_complete_time="2026-05-19T06:30:00Z",
        zeus_submit_intent_time="2026-05-19T09:59:00Z",
        venue_ack_time="2026-05-19T10:00:00Z",
    )
    defaults.update(kwargs)
    return DecisionSourceContext(**defaults)


# ---------------------------------------------------------------------------
# R-3.1: obs_after_provider — fires when observation_time > provider_reported_time
# ---------------------------------------------------------------------------

def test_r3_1_obs_after_provider():
    """R-3.1: observation_time after provider_reported_time triggers obs_after_provider."""
    ctx = _make_pr3_ctx(
        provider_reported_time="2026-01-01T10:00:00Z",
        observation_time="2026-01-01T10:01:00Z",
        observation_available_at="2026-01-01T10:02:00Z",
        decision_time="2026-01-01T10:03:00Z",
    )
    errors = ctx.integrity_errors()
    assert "obs_after_provider" in errors, (
        "Expected obs_after_provider when observation_time > provider_reported_time"
    )


# ---------------------------------------------------------------------------
# R-3.2: provider_after_available — fires when provider_reported_time > observation_available_at
# ---------------------------------------------------------------------------

def test_r3_2_provider_after_available():
    """R-3.2: provider_reported_time after observation_available_at triggers provider_after_available."""
    ctx = _make_pr3_ctx(
        observation_time="2026-01-01T10:00:00Z",
        provider_reported_time="2026-01-01T10:02:00Z",
        observation_available_at="2026-01-01T10:01:00Z",
        decision_time="2026-01-01T10:03:00Z",
    )
    errors = ctx.integrity_errors()
    assert "provider_after_available" in errors, (
        "Expected provider_after_available when provider_reported_time > observation_available_at"
    )


# ---------------------------------------------------------------------------
# R-3.3: available_after_decision — unconditional; fires regardless of provider_reported_time
# ---------------------------------------------------------------------------

def test_r3_3_available_after_decision_unconditional():
    """R-3.3: available_after_decision fires even when provider_reported_time is None."""
    ctx = _make_pr3_ctx(
        provider_reported_time=None,
        observation_available_at="2026-01-01T10:05:00Z",
        decision_time="2026-01-01T10:00:00Z",
    )
    errors = ctx.integrity_errors()
    assert "available_after_decision" in errors, (
        "Expected available_after_decision to fire unconditionally when obs_avail > decision"
    )


def test_r3_3_available_after_decision_with_provider():
    """R-3.3: available_after_decision also fires when provider_reported_time is set."""
    ctx = _make_pr3_ctx(
        observation_time="2026-01-01T09:55:00Z",
        provider_reported_time="2026-01-01T09:54:00Z",
        observation_available_at="2026-01-01T10:05:00Z",
        decision_time="2026-01-01T10:00:00Z",
    )
    errors = ctx.integrity_errors()
    assert "available_after_decision" in errors


# ---------------------------------------------------------------------------
# R-3.4: happy path — in-order timestamps, no causality errors
# ---------------------------------------------------------------------------

def test_r3_4_happy_path_no_errors():
    """R-3.4: valid timestamps in causal order produce no causality errors.

    Causal chain: observation_time < provider_reported_time < observation_available_at < decision_time
    """
    ctx = _make_pr3_ctx(
        observation_time="2026-05-19T05:58:00Z",    # observed at 05:58
        provider_reported_time="2026-05-19T05:59:00Z",  # reported at 05:59 (after obs)
        observation_available_at="2026-05-19T06:01:00Z",  # available at 06:01
        decision_time="2026-05-19T10:00:00Z",
    )
    errors = ctx.integrity_errors()
    causality_errors = [
        e for e in errors
        if e in ("obs_after_provider", "provider_after_available", "available_after_decision")
    ]
    assert causality_errors == [], f"Unexpected causality errors: {causality_errors}"


# ---------------------------------------------------------------------------
# R-3.5: CausalityStatus Literal accepts all 10 values
# ---------------------------------------------------------------------------

def test_r3_5_causality_status_literal_values():
    """R-3.5: All 10 CausalityStatus values are accepted by the Literal type."""
    # These are all values from pr36_scaffold.md §CausalityStatus enum
    expected_values = {
        "AVAILABLE_AFTER_DECISION",
        "CLOCK_DRIFT_WARNING",
        "DECISION_BEFORE_FORECAST_AVAILABLE",
        "EXCESSIVE_CLOCK_DRIFT",
        "INCLUSION_AFTER_FINALITY",
        "MISSING_CAUSALITY_FIELD",
        "OBS_AFTER_PROVIDER",
        "OK",
        "PROVIDER_AFTER_AVAILABLE",
        "SUBMIT_AFTER_ACK",
    }
    # CausalityStatus is a Literal — extract its __args__
    import typing
    literal_args = set(typing.get_args(CausalityStatus))
    assert literal_args == expected_values, (
        f"CausalityStatus args mismatch.\nGot: {sorted(literal_args)}\nExpected: {sorted(expected_values)}"
    )


# ---------------------------------------------------------------------------
# R-3.NEW-a: Path F — obs_after_provider does NOT fire when provider_reported_time is None
# ---------------------------------------------------------------------------

def test_r3_new_a_none_provider_no_obs_after_provider():
    """R-3.NEW-a: obs_after_provider must NOT fire when provider_reported_time is None.

    Even if observation_time is set to a value that would trigger the error if
    provider_reported_time were populated, Path F suppresses the check entirely.
    """
    ctx = _make_pr3_ctx(
        provider_reported_time=None,
        # observation_time in "future" relative to a would-be provider_reported_time
        observation_time="2099-01-01T10:01:00Z",
        observation_available_at="2099-01-01T10:02:00Z",
        decision_time="2099-01-01T10:03:00Z",
    )
    errors = ctx.integrity_errors()
    assert "obs_after_provider" not in errors, (
        "obs_after_provider must not fire when provider_reported_time is None (Path F)"
    )


# ---------------------------------------------------------------------------
# R-3.NEW-b: Path F — provider_after_available does NOT fire when provider_reported_time is None
# ---------------------------------------------------------------------------

def test_r3_new_b_none_provider_no_provider_after_available():
    """R-3.NEW-b: provider_after_available must NOT fire when provider_reported_time is None."""
    ctx = _make_pr3_ctx(
        provider_reported_time=None,
        observation_time="2026-01-01T09:55:00Z",
        # observation_available_at before a would-be provider_reported_time
        observation_available_at="2026-01-01T09:50:00Z",
        decision_time="2026-01-01T10:03:00Z",
    )
    errors = ctx.integrity_errors()
    assert "provider_after_available" not in errors, (
        "provider_after_available must not fire when provider_reported_time is None (Path F)"
    )


# ---------------------------------------------------------------------------
# R-3.NEW-c: Path F — available_after_decision still fires even when provider_reported_time is None
# ---------------------------------------------------------------------------

def test_r3_new_c_none_provider_available_after_decision_still_fires():
    """R-3.NEW-c: available_after_decision is unconditional and fires regardless of provider_reported_time=None."""
    ctx = _make_pr3_ctx(
        provider_reported_time=None,
        observation_time="2026-01-01T09:55:00Z",
        observation_available_at="2026-01-01T10:05:00Z",
        decision_time="2026-01-01T10:00:00Z",
    )
    errors = ctx.integrity_errors()
    assert "available_after_decision" in errors, (
        "available_after_decision must fire even when provider_reported_time is None"
    )
    # And the two conditional ones must NOT fire
    assert "obs_after_provider" not in errors
    assert "provider_after_available" not in errors


# ---------------------------------------------------------------------------
# Backward-compat: pre-epoch decisions do not trigger new missing_* errors
# ---------------------------------------------------------------------------

def test_backward_compat_pre_epoch_no_missing_errors():
    """Pre-epoch decisions (decision_time < _PR3_REQUIRED_FIELDS_EPOCH) must not emit PR3 missing_* errors.

    The pre-existing required-field checks (model_family, forecast_issue_time etc.) may still
    fire for legacy-incomplete contexts; that is expected behavior. Only the NEW PR3 required
    fields (observation_time, observation_available_at, polymarket_end_anchor_source,
    first_member_observed_time, run_complete_time, zeus_submit_intent_time, venue_ack_time)
    must NOT fire when decision_time < epoch.
    """
    _PR3_NEW_REQUIRED = {
        "observation_time",
        "observation_available_at",
        "polymarket_end_anchor_source",
        "first_member_observed_time",
        "run_complete_time",
        "zeus_submit_intent_time",
        "venue_ack_time",
    }
    ctx = DecisionSourceContext(
        source_id="ecmwf_open_data",
        decision_time="2026-01-01T00:00:00Z",  # before epoch
        # All new required fields left empty (default)
    )
    errors = ctx.integrity_errors()
    new_missing_errors = [
        e for e in errors
        if e.startswith("missing_") and e[len("missing_"):] in _PR3_NEW_REQUIRED
    ]
    assert new_missing_errors == [], (
        f"Pre-epoch decisions must not emit PR3 new missing_* errors; got {new_missing_errors}"
    )
