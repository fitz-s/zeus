# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: RED test for W1 certificate dual-chain source_run binding (benign 00Z->12Z passes; fabricated still fails).
# Reuse: Run with pytest; update if no-submit cert schema changes.
# Authority basis: WAVE-1 W1-T3 cert dual-chain
# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: docs/operations task WAVE-1 (unblock-W1) W1-T3 — certificate
#   dual-chain source_run binding. The cert asserts a single cross-chain
#   source_run_id equality (compiler.py:381 + verifier.py:760)
#   source_truth.source_run_id == forecast.source_run_id, which kills the 11
#   benign 00Z→12Z run advances at NO_SUBMIT_CERTIFICATE. We add a SECOND key
#   derived_from_source_run_id (the reader-elected executable run) and bind BOTH
#   causal source_run_id (forecast causality) AND derived_from_source_run_id
#   (executable). The relaxation rescues the benign advance WITHOUT weakening
#   causal integrity.
"""W1-T3 RED relationship test (RT-4): dual-chain source_run binding.

RT-4: causal=00Z, forecast=12Z, derived=12Z → VERIFIED; THEN a FABRICATED
forecast (source_run_id ≠ derived) → STILL FAILS. RED today because the cert
binds source_truth.source_run_id == forecast.source_run_id, so the benign
00Z→12Z advance fails (00Z≠12Z) before the fabrication test can even run.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.decision_kernel.compiler import DecisionCompiler
from src.events.opportunity_event import (
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)
from src.events.reactor import EventSubmissionReceipt
from tests.decision_kernel.no_submit_fixtures import build_test_no_submit_proof_bundle

UTC = timezone.utc

_CAUSAL_RUN = "run-00Z"   # the event's causal trigger run (forecast causality)
_DERIVED_RUN = "run-12Z"  # the reader-ELECTED executable run (freshest fully-captured)


def _event(source_run_id: str = _CAUSAL_RUN):
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-25",
        metric="high",
        source_id="opendata",
        source_run_id=source_run_id,
        cycle="00",
        track="live",
        snapshot_id="snap-1",
        snapshot_hash="hash-1",
        captured_at="2026-05-25T10:00:00+00:00",
        available_at="2026-05-25T10:01:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0],
        observed_steps=[0],
        expected_members=51,
        source_run_status="SUCCESS",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-25|high",
        source="forecast_live",
        observed_at="2026-05-25T10:00:00+00:00",
        available_at="2026-05-25T10:01:00+00:00",
        received_at="2026-05-25T10:02:00+00:00",
        causal_snapshot_id="snap-1",
        payload=payload,
    )


def _receipt(event_id: str):
    return EventSubmissionReceipt(
        submitted=False,
        proof_accepted=True,
        event_id=event_id,
        causal_snapshot_id="snap-1",
        executable_snapshot_id="snapshot-exec-1",
        trade_score_positive=True,
        fdr_pass=True,
        fdr_family_id="family-1",
        fdr_hypothesis_count=2,
        kelly_pass=True,
        kelly_execution_price_type="ExecutionPrice",
        kelly_price_fee_deducted=True,
        kelly_size_usd=1.0,
        kelly_cost_basis_id="cost-1",
        kelly_decision_id="kelly-1",
        risk_decision_id="risk-1",
        final_intent_id="intent-1",
    )


def _build_dual_chain_bundle(*, derived_run: str, forecast_run: str):
    """Build a no-submit bundle where the causal source_run (00Z) differs from
    the reader-elected executable run (12Z). source_truth carries:
      - source_run_id = causal (00Z)          [forecast causality]
      - derived_from_source_run_id = derived  [reader-elected executable]
    and forecast.source_run_id = forecast_run [should equal derived]."""
    event = _event(source_run_id=_CAUSAL_RUN)
    decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=UTC)
    bundle = build_test_no_submit_proof_bundle(
        event, _receipt(event.event_id), decision_time=decision_time
    )
    # Causal chain: the event/source_truth source_run_id is the 00Z causal run.
    bundle.source_truth.payload["source_run_id"] = _CAUSAL_RUN
    # Executable chain: the reader elected the 12Z run.
    bundle.source_truth.payload["derived_from_source_run_id"] = derived_run
    bundle.forecast_authority.payload["source_run_id"] = forecast_run
    return event, decision_time, bundle


class TestRT4CertDualChainSourceRun:
    def test_benign_advance_verified_when_dual_chain_enabled(self, monkeypatch):
        """causal=00Z, forecast=12Z, derived=12Z → VERIFIED.

        RED today: the single-chain equality source_truth.source_run_id ==
        forecast.source_run_id fails (00Z≠12Z)."""
        from src.config import settings as _settings

        monkeypatch.setitem(
            _settings["edli_v1"], "edli_source_run_dual_chain_enabled", True
        )

        event, decision_time, bundle = _build_dual_chain_bundle(
            derived_run=_DERIVED_RUN, forecast_run=_DERIVED_RUN
        )
        result = DecisionCompiler().compile_no_submit(
            event, decision_time=decision_time, proof_bundle=bundle
        )
        assert result.status == "VERIFIED", (
            "benign 00Z→12Z advance was NOT rescued by the dual-chain binding; "
            f"status={result.status!r}, failures={getattr(result, 'failures', None)!r}"
        )

    def test_fabricated_forecast_still_fails_when_dual_chain_enabled(self, monkeypatch):
        """causal=00Z, derived=12Z, but forecast.source_run_id=FAKE (≠ derived)
        → STILL FAILS. The relaxation must not weaken executable integrity."""
        from src.config import settings as _settings

        monkeypatch.setitem(
            _settings["edli_v1"], "edli_source_run_dual_chain_enabled", True
        )

        event, decision_time, bundle = _build_dual_chain_bundle(
            derived_run=_DERIVED_RUN, forecast_run="run-FABRICATED"
        )
        result = DecisionCompiler().compile_no_submit(
            event, decision_time=decision_time, proof_bundle=bundle
        )
        assert result.status != "VERIFIED", (
            "a FABRICATED forecast (source_run_id ≠ derived_from_source_run_id) "
            "was wrongly VERIFIED; the executable chain must still bind."
        )

    def test_flag_off_preserves_legacy_single_chain(self, monkeypatch):
        """Flag OFF (default): matching source_run_id on both chains still
        VERIFIES (byte-identical legacy behavior), and a mismatch still fails.
        Shadow-safety: the merge is inert until the operator enables the flag."""
        from src.config import settings as _settings

        monkeypatch.setitem(
            _settings["edli_v1"], "edli_source_run_dual_chain_enabled", False
        )

        # Legacy happy path: causal == forecast run (the historical norm).
        event = _event(source_run_id=_DERIVED_RUN)
        decision_time = datetime(2026, 5, 25, 10, 3, tzinfo=UTC)
        bundle = build_test_no_submit_proof_bundle(
            event, _receipt(event.event_id), decision_time=decision_time
        )
        bundle.source_truth.payload["source_run_id"] = _DERIVED_RUN
        bundle.forecast_authority.payload["source_run_id"] = _DERIVED_RUN
        result = DecisionCompiler().compile_no_submit(
            event, decision_time=decision_time, proof_bundle=bundle
        )
        assert result.status == "VERIFIED", (
            f"legacy single-chain happy path regressed: status={result.status!r}"
        )

        # Legacy mismatch must still fail with the flag OFF.
        event2, decision_time2, bundle2 = _build_dual_chain_bundle(
            derived_run=_DERIVED_RUN, forecast_run=_DERIVED_RUN
        )
        # With the flag OFF, source_truth.source_run_id (00Z) != forecast (12Z)
        # → legacy equality fails regardless of derived_from_source_run_id.
        result2 = DecisionCompiler().compile_no_submit(
            event2, decision_time=decision_time2, proof_bundle=bundle2
        )
        assert result2.status != "VERIFIED", (
            "flag OFF must preserve the legacy single-chain equality that fails "
            "the 00Z≠12Z advance (so the merge is inert until the flag flips)."
        )
