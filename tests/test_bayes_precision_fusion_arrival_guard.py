# Created: 2026-06-16
# Last reused or audited: 2026-08-03
# Authority basis: timing-semantics fix — fusion pre-arrival guard (M-ledger);
#   docs/evidence/timing_audit/shadow_validation_method_2026-06-16.md
"""Unit invariants for the T2 precision-fusion pre-arrival guard.

``_available_after_decision(available_at, decision_utc)`` is the gate that
excludes a provider whose source data became available STRICTLY AFTER the
decision instant (no-future-leakage). Missing / empty / unparseable availability
is also excluded: absent provenance is not live authority. Naive timestamps are
interpreted as UTC (Zeus persists UTC wall-clocks).

FRESHNESS CONTRACT: every exclusion on missing/malformed evidence must emit a
WARNING (LOUD, never silent) so the caller is observable in logs.
"""

import logging
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from src.data.bayes_precision_fusion_capture import _available_after_decision

_DECISION = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_available_before_decision_is_not_after():
    assert _available_after_decision("2026-06-01T10:00:00+00:00", _DECISION) is False


def test_available_after_decision_is_after():
    assert _available_after_decision("2026-06-01T14:00:00+00:00", _DECISION) is True


def test_available_equal_to_decision_is_not_after():
    # strictly-after semantics: an arrival exactly at the decision instant is
    # admitted (not a future leak).
    assert _available_after_decision("2026-06-01T12:00:00+00:00", _DECISION) is False


def test_missing_availability_fails_closed_exclude():
    assert _available_after_decision(None, _DECISION) is True
    assert _available_after_decision("", _DECISION) is True
    assert _available_after_decision("   ", _DECISION) is True


def test_unparseable_availability_fails_closed_exclude():
    assert _available_after_decision("not-a-timestamp", _DECISION) is True


def test_naive_timestamp_interpreted_as_utc():
    assert _available_after_decision("2026-06-01T14:00:00", _DECISION) is True
    assert _available_after_decision("2026-06-01T10:00:00", _DECISION) is False


def test_z_suffix_parsed_as_utc():
    assert _available_after_decision("2026-06-01T14:00:00Z", _DECISION) is True


def test_datetime_input_accepted():
    after = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
    before = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert _available_after_decision(after, _DECISION) is True
    assert _available_after_decision(before, _DECISION) is False


# ---------------------------------------------------------------------------
# Freshness contract: LOUD warns on missing/malformed evidence (non-silent)
# ---------------------------------------------------------------------------

def test_missing_availability_none_emits_warning(caplog):
    """A None available_at exclusion must emit a WARNING (not silent)."""
    with caplog.at_level(logging.WARNING, logger="zeus.bayes_precision_fusion_capture"):
        result = _available_after_decision(None, _DECISION, model_label="test_model")
    assert result is True
    assert any("MISSING available_at" in r.message or "MISSING" in r.message for r in caplog.records), (
        f"Expected WARNING about MISSING available_at in logs, got: {[r.message for r in caplog.records]}"
    )


def test_missing_availability_empty_string_emits_warning(caplog):
    """An empty-string available_at exclusion must emit a WARNING (not silent)."""
    with caplog.at_level(logging.WARNING, logger="zeus.bayes_precision_fusion_capture"):
        result = _available_after_decision("", _DECISION, model_label="test_model")
    assert result is True
    assert any("EMPTY available_at" in r.message or "EMPTY" in r.message or "MISSING" in r.message for r in caplog.records), (
        f"Expected WARNING about EMPTY available_at in logs, got: {[r.message for r in caplog.records]}"
    )


def test_unparseable_availability_emits_warning(caplog):
    """An unparseable available_at exclusion must emit a WARNING (not silent)."""
    with caplog.at_level(logging.WARNING, logger="zeus.bayes_precision_fusion_capture"):
        result = _available_after_decision("not-a-timestamp", _DECISION, model_label="test_model")
    assert result is True
    assert any("UNPARSEABLE" in r.message for r in caplog.records), (
        f"Expected WARNING about UNPARSEABLE available_at in logs, got: {[r.message for r in caplog.records]}"
    )


def test_model_label_appears_in_warning(caplog):
    """The model label must appear in the warning so operators know WHICH model was admitted."""
    with caplog.at_level(logging.WARNING, logger="zeus.bayes_precision_fusion_capture"):
        _available_after_decision(None, _DECISION, model_label="gfs_global")
    assert any("gfs_global" in r.message for r in caplog.records), (
        "Model label must appear in the missing-availability warning for traceability"
    )


def test_valid_past_timestamp_no_warning(caplog):
    """A valid available_at BEFORE the decision must NOT emit a warning (normal admit)."""
    with caplog.at_level(logging.WARNING, logger="zeus.bayes_precision_fusion_capture"):
        result = _available_after_decision("2026-06-01T10:00:00+00:00", _DECISION, model_label="gfs_global")
    assert result is False
    # No warning for a legitimately-present-and-past availability
    assert not any("admitted" in r.message.lower() for r in caplog.records), (
        f"Should not warn on a valid past availability, got: {[r.message for r in caplog.records]}"
    )


def test_missing_availability_drops_models_not_admit_counter():
    """capture_bayes_precision_instruments must drop models missing availability.

    When decision_utc is supplied but model_available_at is absent for a model,
    that model cannot enter the likelihood.
    """
    from datetime import date
    from src.data.bayes_precision_fusion_capture import capture_bayes_precision_instruments

    # Provide a stub live_fetch that returns None immediately (drops models) so we
    # don't hit the network; only need the arrival-guard telemetry path to fire for
    # at least one model whose availability is absent from model_available_at.
    decision = datetime(2026, 6, 1, 6, 0, 0, tzinfo=timezone.utc)
    result = capture_bayes_precision_instruments(
        city="test_city",
        metric="high",
        latitude=52.5,
        longitude=13.4,
        timezone_name="Europe/Berlin",
        run=datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        target_local_date=date(2026, 6, 2),
        lead_days=1,
        forecast_hours=48,
        anchor_z_corrected=25.0,
        live_fetch=lambda **_: None,  # drop all models (fail-soft)
        decision_utc=decision,
        model_available_at={},  # no availability provided -> all models have missing evidence
    )
    assert result.admitted_on_missing_availability == 0
    assert result.likelihood == ()
    assert result.dropped_models


# ---------------------------------------------------------------------------
# Capture ordering: candidate value before arrival authority (Paris, lead 1)
# ---------------------------------------------------------------------------

_PARIS = (48.8566, 2.3522)
_CAPTURE_RUN = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
_CAPTURE_TARGET = date(2026, 6, 2)


def _capture_paris(*, candidate_value, model_available_at):
    calls: list[str] = []

    def _live_fetch(*, model, **_kwargs):
        calls.append(model)
        return candidate_value if model == "icon_d2" else None

    from src.data.bayes_precision_fusion_capture import capture_bayes_precision_instruments

    result = capture_bayes_precision_instruments(
        city="Paris",
        metric="high",
        latitude=_PARIS[0],
        longitude=_PARIS[1],
        timezone_name="Europe/Paris",
        run=_CAPTURE_RUN,
        target_local_date=_CAPTURE_TARGET,
        lead_days=1,
        anchor_z_corrected=20.0,
        live_fetch=_live_fetch,
        decision_utc=_DECISION,
        model_available_at=model_available_at,
    )
    return result, calls


@pytest.mark.parametrize("candidate_value", [None, float("nan"), float("inf"), float("-inf")])
def test_missing_or_nonfinite_value_does_not_trigger_missing_availability_guard(
    candidate_value, caplog
):
    with caplog.at_level(logging.WARNING, logger="zeus.bayes_precision_fusion_capture"):
        result, calls = _capture_paris(candidate_value=candidate_value, model_available_at={})

    assert "icon_d2" in calls, "value must be fetched before availability is checked"
    assert result.likelihood == ()
    assert "icon_d2" in result.dropped_models
    assert not any("MISSING available_at" in record.message for record in caplog.records)


def test_present_value_with_missing_availability_is_still_rejected(caplog):
    with caplog.at_level(logging.WARNING, logger="zeus.bayes_precision_fusion_capture"):
        result, calls = _capture_paris(candidate_value=20.5, model_available_at={})

    assert "icon_d2" in calls
    assert result.likelihood == ()
    assert "icon_d2" in result.dropped_models
    assert any("MISSING available_at" in record.message for record in caplog.records)


def test_present_value_with_valid_availability_enters_capture():
    result, calls = _capture_paris(
        candidate_value=20.5,
        model_available_at={"icon_d2": "2026-06-01T10:00:00+00:00"},
    )

    assert "icon_d2" in calls
    assert [instrument.model for instrument in result.likelihood] == ["icon_d2"]
    assert result.likelihood[0].z == pytest.approx(20.5)
