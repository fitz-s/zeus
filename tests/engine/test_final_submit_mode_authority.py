# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator review 2026-06-10 P0 mode-authority — execution_mode_intent
#   must be the final-submit authority; recapture and final intent must not mode-flip.
"""Relationship tests (P0 mode-authority, operator review 2026-06-10).

The cross-module invariant under test spans the boundary
  recapture proof (execution_mode_intent) -> EventSubmissionReceipt
  -> actionable payload -> final command builder -> final intent certificate.

The PROVEN proof maker/taker mode is the SOLE final-submit authority. The final
command builder may NOT re-decide the mode. Properties asserted across the boundary:

  (a) proof execution_mode_intent=MAKER + final-stage conditions that would force
      TAKER  ->  SUBMIT_ABORTED_MODE_FLIPPED, NO order built (and the reverse
      TAKER->MAKER also aborts; NO inline flip in EITHER direction).
  (b) proof mode survives unchanged  ->  the validated mode that drives the final
      intent certificate's order_mode EQUALS the proof execution_mode_intent.
  (c) the receipt carries execution_mode_intent + maker_limit_price as FIRST-CLASS
      fields end-to-end (receipt field is the authority, overriding the opportunity
      -book back-channel), and they thread into the actionable payload.

These exercise the REAL production seam (_validate_final_order_mode_or_abort,
_actionable_payload_from_receipt, the typed _SubmitAbortedModeFlipped, the
SUBMIT_ABORTED_MODE_FLIPPED lifecycle state), not a re-implemented copy of the logic.
"""
from __future__ import annotations

import ast
import inspect
import types
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Lifecycle-state + reversal-reason registration (fix requirements #3 + #4).
# ---------------------------------------------------------------------------

class TestLifecycleStateRegistered:
    def test_submit_aborted_mode_flipped_state_exists(self):
        from src.strategy.redecision import CandidateLifecycleState
        assert hasattr(CandidateLifecycleState, "SUBMIT_ABORTED_MODE_FLIPPED")

    def test_mode_flip_is_a_submit_abort_state(self):
        from src.strategy.redecision import (
            CandidateLifecycleState,
            SUBMIT_ABORT_STATES,
        )
        assert (
            CandidateLifecycleState.SUBMIT_ABORTED_MODE_FLIPPED in SUBMIT_ABORT_STATES
        )

    def test_mode_flipped_reversal_reason_exists(self):
        from src.strategy.redecision import ReversalReason
        assert "MODE_FLIPPED" in ReversalReason.__members__

    def test_receipt_reason_map_has_mode_flipped(self):
        from src.engine.event_reactor_adapter import _SUBMIT_ABORT_RECEIPT_REASON
        from src.strategy.redecision import CandidateLifecycleState
        assert (
            _SUBMIT_ABORT_RECEIPT_REASON[
                CandidateLifecycleState.SUBMIT_ABORTED_MODE_FLIPPED
            ]
            == "SUBMIT_ABORTED_MODE_FLIPPED"
        )

    def test_state_registered_in_money_path_yaml(self):
        """CI classify-change gate requires the new lifecycle state to be registered."""
        repo_root = Path(__file__).resolve().parents[2]
        text = (repo_root / "architecture" / "money_path_objects.yaml").read_text()
        # Registered under candidate_lifecycle.states AND reversal_reason.states.
        assert "SUBMIT_ABORTED_MODE_FLIPPED" in text
        assert "- MODE_FLIPPED" in text


# ---------------------------------------------------------------------------
# (a) proof MAKER + final-stage TAKER  ->  SUBMIT_ABORTED_MODE_FLIPPED, no order.
#     Both flip directions abort. Missing/unknown proof mode fails closed.
# ---------------------------------------------------------------------------

class TestModeFlipAborts:
    def test_proof_maker_fresh_taker_aborts(self):
        from src.engine.event_reactor_adapter import (
            _SubmitAbortedModeFlipped,
            _validate_final_order_mode_or_abort,
        )
        with pytest.raises(_SubmitAbortedModeFlipped, match="SUBMIT_ABORTED_MODE_FLIPPED"):
            _validate_final_order_mode_or_abort(
                proof_mode="MAKER",
                fresh_mode="TAKER",
                fresh_best_bid=0.48,
                fresh_best_ask=0.52,
            )

    def test_proof_taker_fresh_maker_aborts(self):
        """The REVERSE direction (TAKER->MAKER) must also abort — NO inline flip."""
        from src.engine.event_reactor_adapter import (
            _SubmitAbortedModeFlipped,
            _validate_final_order_mode_or_abort,
        )
        with pytest.raises(_SubmitAbortedModeFlipped, match="SUBMIT_ABORTED_MODE_FLIPPED"):
            _validate_final_order_mode_or_abort(
                proof_mode="TAKER",
                fresh_mode="MAKER",
                fresh_best_bid=0.10,
                fresh_best_ask=0.90,
            )

    def test_missing_proof_mode_fails_closed(self):
        """Fail-closed: a missing proven mode at the final stage aborts, never taker."""
        from src.engine.event_reactor_adapter import (
            _SubmitAbortedModeFlipped,
            _validate_final_order_mode_or_abort,
        )
        with pytest.raises(_SubmitAbortedModeFlipped, match="MISSING_OR_UNKNOWN_PROOF_MODE"):
            _validate_final_order_mode_or_abort(
                proof_mode=None,
                fresh_mode="TAKER",
                fresh_best_bid=0.48,
                fresh_best_ask=0.52,
            )

    def test_unknown_proof_mode_fails_closed(self):
        from src.engine.event_reactor_adapter import (
            _SubmitAbortedModeFlipped,
            _validate_final_order_mode_or_abort,
        )
        with pytest.raises(_SubmitAbortedModeFlipped, match="MISSING_OR_UNKNOWN_PROOF_MODE"):
            _validate_final_order_mode_or_abort(
                proof_mode="GARBAGE",
                fresh_mode="MAKER",
                fresh_best_bid=0.48,
                fresh_best_ask=0.52,
            )

    def test_abort_is_a_value_error_subclass(self):
        """Propagates through the existing `except Exception` submit boundary."""
        from src.engine.event_reactor_adapter import _SubmitAbortedModeFlipped
        assert issubclass(_SubmitAbortedModeFlipped, ValueError)

    def test_no_late_taker_rebuild_remains_in_command_builder(self):
        """ANTIBODY: the late EV-override re-build that flipped proven-MAKER -> TAKER
        bypassing the validator is GONE. There must be no second, unconditional
        ``order_mode="TAKER"`` build that is NOT the proof-mode-driven first build.

        Source-level structural test: inside _build_live_execution_command_certificates,
        every build_final_intent_certificate_from_actionable call's order_mode argument
        must be the variable ``order_mode`` (the validated proven mode), never the literal
        "TAKER". A literal-"TAKER" re-build is the resurrected bypass hole.
        """
        from src.engine import event_reactor_adapter as era
        src = inspect.getsource(era._build_live_execution_command_certificates)
        tree = ast.parse(src.lstrip())
        literal_taker_order_mode_calls = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != "build_final_intent_certificate_from_actionable":
                    continue
                for kw in node.keywords:
                    if kw.arg == "order_mode":
                        if isinstance(kw.value, ast.Constant) and kw.value.value == "TAKER":
                            literal_taker_order_mode_calls += 1
        assert literal_taker_order_mode_calls == 0, (
            "a literal order_mode=\"TAKER\" final-intent re-build is back — that is the "
            "validator-bypassing mode-flip hole this P0 closed"
        )


# ---------------------------------------------------------------------------
# (b) proof mode survives  ->  validated mode == proof mode  (drives order_mode).
# ---------------------------------------------------------------------------

class TestProofModeSurvives:
    def test_maker_proof_maker_fresh_returns_maker(self):
        from src.engine.event_reactor_adapter import _validate_final_order_mode_or_abort
        order_mode = _validate_final_order_mode_or_abort(
            proof_mode="MAKER",
            fresh_mode="MAKER",
            fresh_best_bid=0.49,
            fresh_best_ask=0.51,
        )
        assert order_mode == "MAKER"

    def test_taker_proof_taker_fresh_returns_taker(self):
        from src.engine.event_reactor_adapter import _validate_final_order_mode_or_abort
        order_mode = _validate_final_order_mode_or_abort(
            proof_mode="TAKER",
            fresh_mode="TAKER",
            fresh_best_bid=0.49,
            fresh_best_ask=0.50,
        )
        assert order_mode == "TAKER"

    def test_validated_mode_is_proof_mode_not_fresh_mode_normalized(self):
        """Lower-case proof mode normalizes; the returned mode is the PROVEN mode."""
        from src.engine.event_reactor_adapter import _validate_final_order_mode_or_abort
        order_mode = _validate_final_order_mode_or_abort(
            proof_mode="maker",
            fresh_mode="MAKER",
            fresh_best_bid=0.49,
            fresh_best_ask=0.51,
        )
        assert order_mode == "MAKER"

    def test_final_cert_order_mode_equals_input_order_mode(self):
        """The cert builder faithfully writes the (validated proof) order_mode to the
        FINAL_INTENT payload — so the validated proof mode IS the final intent mode.

        Uses the real _order_spec_for_mode seam the cert builder uses to map
        order_mode -> the order-type tuple it stamps on the payload.
        """
        from src.decision_kernel.certificates.execution import _order_spec_for_mode
        maker_spec = _order_spec_for_mode(order_mode="MAKER", order_type=None, time_in_force=None)
        taker_spec = _order_spec_for_mode(order_mode="TAKER", order_type=None, time_in_force=None)
        # The payload stamps "order_mode": order_spec.mode (execution.py:171).
        assert maker_spec.mode == "MAKER"
        assert maker_spec.post_only is True
        assert taker_spec.mode == "TAKER"
        assert taker_spec.post_only is False


# ---------------------------------------------------------------------------
# (c) receipt carries execution_mode_intent + maker_limit_price end-to-end.
# ---------------------------------------------------------------------------

class TestReceiptCarriesModeFields:
    def test_receipt_has_first_class_mode_fields(self):
        from src.events.reactor import EventSubmissionReceipt
        r = EventSubmissionReceipt(
            submitted=False,
            event_id="evt-1",
            causal_snapshot_id="snap-1",
            execution_mode_intent="MAKER",
            maker_limit_price=0.32,
        )
        assert r.execution_mode_intent == "MAKER"
        assert r.maker_limit_price == 0.32

    def _cap(self):
        return types.SimpleNamespace(
            payload={"usage_id": "u1", "reserved_notional_usd": "10.0"},
        )

    def test_actionable_payload_sources_mode_from_receipt_field(self):
        """The receipt's first-class execution_mode_intent is the authority threaded
        into proof_execution_mode_intent on the actionable payload."""
        from src.events.reactor import EventSubmissionReceipt
        from src.engine.event_reactor_adapter import _actionable_payload_from_receipt
        r = EventSubmissionReceipt(
            submitted=False,
            event_id="evt-1",
            causal_snapshot_id="snap-1",
            execution_mode_intent="MAKER",
            maker_limit_price=0.32,
        )
        payload = _actionable_payload_from_receipt(r, self._cap())
        assert payload["proof_execution_mode_intent"] == "MAKER"
        assert payload["proof_maker_limit_price"] == 0.32

    def test_receipt_field_overrides_opportunity_book_mode(self):
        """Provenance authority: the receipt FIELD (proven through recapture) overrides a
        conflicting opportunity-book candidate mode. The book is a back-channel; the field
        is the law — they can never disagree at the final-stage validator."""
        from src.events.reactor import EventSubmissionReceipt
        from src.engine.event_reactor_adapter import _actionable_payload_from_receipt
        book = {
            "selected_candidate_id": "cand-1",
            "candidates": [
                {"candidate_id": "cand-1", "execution_mode_intent": "TAKER"}  # stale/back-channel
            ],
        }
        r = EventSubmissionReceipt(
            submitted=False,
            event_id="evt-1",
            causal_snapshot_id="snap-1",
            candidate_id="cand-1",
            opportunity_book=book,
            execution_mode_intent="MAKER",  # the PROVEN authority
            maker_limit_price=0.32,
        )
        payload = _actionable_payload_from_receipt(r, self._cap())
        assert payload["proof_execution_mode_intent"] == "MAKER"  # field wins, not "TAKER"

    def test_no_mode_field_and_no_book_means_fail_closed_downstream(self):
        """A receipt with neither a mode field nor a book yields no proof_execution_mode_intent
        on the actionable payload — which the final-stage validator treats as missing and
        FAILS CLOSED (asserted in TestModeFlipAborts.test_missing_proof_mode_fails_closed)."""
        from src.events.reactor import EventSubmissionReceipt
        from src.engine.event_reactor_adapter import _actionable_payload_from_receipt
        r = EventSubmissionReceipt(
            submitted=False,
            event_id="evt-1",
            causal_snapshot_id="snap-1",
            opportunity_book=None,
            execution_mode_intent=None,
        )
        payload = _actionable_payload_from_receipt(r, self._cap())
        assert "proof_execution_mode_intent" not in payload


class TestMakerMarketIdentityContext:
    """ANTIBODY (live 2026-06-12 00:52-01:13Z, five maker intents
    PRE_SUBMIT_ERROR): the final intent's market_event_id (the venue-event
    identity the executor's pre-venue guard compares against the snapshot row)
    comes from _executable_market_context_from_snapshot(<snapshot object>).
    Hydrating that object ONLY inside the TAKER depth block left every MAKER
    intent with market_event_id=None, so the intent fell back to the EDLI
    opportunity event id and EVERY maker submit died
    'FinalExecutionIntent event_id does not match executable snapshot'.
    Relationship pinned: the context argument is the mode-independent
    _snap_for_context, and its hydration is NOT gated on order_mode."""

    def test_market_context_arg_is_mode_independent_snapshot(self):
        import ast
        import inspect

        from src.engine import event_reactor_adapter as era

        src = inspect.getsource(era._build_live_execution_command_certificates)
        tree = ast.parse(src.lstrip())
        context_args = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name == "_executable_market_context_from_snapshot":
                    for arg in node.args:
                        context_args.append(getattr(arg, "id", None))
        assert context_args, "context builder call disappeared — re-audit the maker identity path"
        assert all(a == "_snap_for_context" for a in context_args), (
            f"market context must be built from the mode-independent _snap_for_context, "
            f"got {context_args} — feeding the TAKER-only _snap_for_depth re-opens the "
            "maker PRE_SUBMIT_ERROR wall"
        )

    def test_snap_for_context_hydration_not_gated_on_taker(self):
        import inspect
        import re

        from src.engine import event_reactor_adapter as era

        src = inspect.getsource(era._build_live_execution_command_certificates)
        # The hydration assignment must appear BEFORE the TAKER conditional and
        # be guarded only by trade_conn presence.
        hydration = src.find("_snap_for_context = get_snapshot(")
        taker_gate = src.find('if str(order_mode).strip().upper() == "TAKER"')
        assert hydration != -1, "snapshot hydration for market context missing"
        assert taker_gate != -1
        assert hydration < taker_gate, (
            "_snap_for_context hydration moved inside/after the TAKER gate — "
            "maker intents lose market_event_id again"
        )

    def test_context_builder_carries_event_id(self):
        from types import SimpleNamespace

        from src.engine.event_reactor_adapter import _executable_market_context_from_snapshot

        snap = SimpleNamespace(
            event_id="highest-temperature-in-kuala-lumpur-on-june-13-2026",
            event_slug="highest-temperature-in-kuala-lumpur-on-june-13-2026",
            market_end_at="2026-06-13T16:00:00+00:00",
            market_close_at=None,
        )
        context = _executable_market_context_from_snapshot(snap)
        assert context is not None
        assert context["event_id"] == "highest-temperature-in-kuala-lumpur-on-june-13-2026"
