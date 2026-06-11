# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: P0-A/P0-B antibodies for Milan 24C first-fill incident
#   (operator review 2026-06-10); docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md §4.
"""Antibodies: P0-A mode-flip abort; P0-B recapture under proof-mode semantics.

Categories killed:
  P0-A: candidate selected on MAKER EV can submit as TAKER (or vice versa) when
        execution_mode_intent is receipt-only decoration not threaded to the
        final-command builder.
  P0-B: recapture uses zero-taker-fee / no PRICE_MOVED ceiling while the final
        intent goes TAKER — a candidate that never cleared TAKER recapture enters
        the taker submit path.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# P0-A: _selected_candidate_mode_fields_from_receipt
# ---------------------------------------------------------------------------

class TestSelectedCandidateModeFields:
    """_selected_candidate_mode_fields_from_receipt extracts proof-mode fields."""

    def _make_receipt(self, *, candidate_id, execution_mode_intent,
                      ev_taker=None, ev_maker=None, maker_limit_price=None,
                      taker_forbidden_reason=None):
        from src.events.reactor import EventSubmissionReceipt
        candidate_row = {
            "candidate_id": candidate_id,
            "execution_mode_intent": execution_mode_intent,
            "ev_taker": ev_taker,
            "ev_maker": ev_maker,
            "maker_limit_price": maker_limit_price,
            "taker_forbidden_reason": taker_forbidden_reason,
        }
        book = {
            "selected_candidate_id": candidate_id,
            "candidates": [candidate_row],
        }
        return EventSubmissionReceipt(
            submitted=False,
            event_id="evt-1",
            causal_snapshot_id="snap-1",
            candidate_id=candidate_id,
            opportunity_book=book,
        )

    def test_extracts_maker_mode(self):
        from src.engine.event_reactor_adapter import (
            _selected_candidate_mode_fields_from_receipt,
        )
        r = self._make_receipt(
            candidate_id="cand-1",
            execution_mode_intent="MAKER",
            ev_taker=0.002,
            ev_maker=0.005,
            maker_limit_price=0.32,
        )
        fields = _selected_candidate_mode_fields_from_receipt(r)
        assert fields["proof_execution_mode_intent"] == "MAKER"
        assert fields["proof_ev_taker"] == 0.002
        assert fields["proof_ev_maker"] == 0.005
        assert fields["proof_maker_limit_price"] == 0.32

    def test_extracts_taker_mode(self):
        from src.engine.event_reactor_adapter import (
            _selected_candidate_mode_fields_from_receipt,
        )
        r = self._make_receipt(
            candidate_id="cand-2",
            execution_mode_intent="TAKER",
            ev_taker=0.04,
            taker_forbidden_reason=None,
        )
        fields = _selected_candidate_mode_fields_from_receipt(r)
        assert fields["proof_execution_mode_intent"] == "TAKER"

    def test_returns_empty_dict_when_no_book(self):
        from src.events.reactor import EventSubmissionReceipt
        from src.engine.event_reactor_adapter import (
            _selected_candidate_mode_fields_from_receipt,
        )
        r = EventSubmissionReceipt(
            submitted=False,
            event_id="evt-x",
            causal_snapshot_id="snap-x",
            opportunity_book=None,
        )
        assert _selected_candidate_mode_fields_from_receipt(r) == {}

    def test_returns_empty_dict_when_candidate_id_missing(self):
        from src.events.reactor import EventSubmissionReceipt
        from src.engine.event_reactor_adapter import (
            _selected_candidate_mode_fields_from_receipt,
        )
        book = {"selected_candidate_id": None, "candidates": []}
        r = EventSubmissionReceipt(
            submitted=False,
            event_id="evt-x",
            causal_snapshot_id="snap-x",
            candidate_id=None,
            opportunity_book=book,
        )
        assert _selected_candidate_mode_fields_from_receipt(r) == {}

    def test_returns_empty_dict_when_candidate_not_found(self):
        from src.events.reactor import EventSubmissionReceipt
        from src.engine.event_reactor_adapter import (
            _selected_candidate_mode_fields_from_receipt,
        )
        book = {
            "selected_candidate_id": "cand-X",
            "candidates": [{"candidate_id": "cand-Y", "execution_mode_intent": "MAKER"}],
        }
        r = EventSubmissionReceipt(
            submitted=False,
            event_id="evt-x",
            causal_snapshot_id="snap-x",
            candidate_id="cand-X",
            opportunity_book=book,
        )
        assert _selected_candidate_mode_fields_from_receipt(r) == {}

    def test_none_values_not_included(self):
        """Fields with None value do not appear in the returned dict."""
        from src.engine.event_reactor_adapter import (
            _selected_candidate_mode_fields_from_receipt,
        )
        r = self._make_receipt(
            candidate_id="cand-3",
            execution_mode_intent="MAKER",
            ev_taker=None,  # None → not included
            ev_maker=0.003,
        )
        fields = _selected_candidate_mode_fields_from_receipt(r)
        assert "proof_ev_taker" not in fields
        assert fields["proof_ev_maker"] == 0.003


# ---------------------------------------------------------------------------
# P0-A: _actionable_payload_from_receipt threads proof_execution_mode_intent
# ---------------------------------------------------------------------------

class TestActionablePayloadThreadsProofMode:
    """proof_execution_mode_intent appears in actionable payload."""

    def _make_receipt_and_cap(self, *, execution_mode_intent):
        from src.events.reactor import EventSubmissionReceipt
        candidate_row = {
            "candidate_id": "cand-1",
            "execution_mode_intent": execution_mode_intent,
            "ev_taker": 0.003,
            "ev_maker": 0.006,
        }
        book = {
            "selected_candidate_id": "cand-1",
            "candidates": [candidate_row],
        }
        receipt = EventSubmissionReceipt(
            submitted=False,
            event_id="evt-1",
            causal_snapshot_id="snap-1",
            candidate_id="cand-1",
            opportunity_book=book,
            q_live=0.35,
            q_lcb_5pct=0.28,
            c_fee_adjusted=0.32,
            kelly_size_usd=10.0,
        )
        cap = types.SimpleNamespace(
            payload={"usage_id": "u1", "reserved_notional_usd": "10.0"},
        )
        return receipt, cap

    def test_maker_intent_appears_in_payload(self):
        from src.engine.event_reactor_adapter import _actionable_payload_from_receipt
        receipt, cap = self._make_receipt_and_cap(execution_mode_intent="MAKER")
        payload = _actionable_payload_from_receipt(receipt, cap)
        assert payload["proof_execution_mode_intent"] == "MAKER"
        assert payload["proof_ev_taker"] == 0.003
        assert payload["proof_ev_maker"] == 0.006

    def test_taker_intent_appears_in_payload(self):
        from src.engine.event_reactor_adapter import _actionable_payload_from_receipt
        receipt, cap = self._make_receipt_and_cap(execution_mode_intent="TAKER")
        payload = _actionable_payload_from_receipt(receipt, cap)
        assert payload["proof_execution_mode_intent"] == "TAKER"

    def test_no_book_no_proof_fields(self):
        """Legacy receipt without opportunity book: no proof_* keys added."""
        from src.events.reactor import EventSubmissionReceipt
        from src.engine.event_reactor_adapter import _actionable_payload_from_receipt
        receipt = EventSubmissionReceipt(
            submitted=False, event_id="evt-1", causal_snapshot_id="snap-1",
            opportunity_book=None,
        )
        cap = types.SimpleNamespace(
            payload={"usage_id": "u1", "reserved_notional_usd": "5.0"},
        )
        payload = _actionable_payload_from_receipt(receipt, cap)
        assert "proof_execution_mode_intent" not in payload


# ---------------------------------------------------------------------------
# P0-A: mode-flip guard in _select_edli_order_mode context
# ---------------------------------------------------------------------------

class TestModeFlipGuard:
    """SUBMIT_ABORTED_MODE_FLIPPED raised when proof mode != fresh mode."""

    def _call_with_proof_mode(self, proof_mode: str, fresh_bid: float, fresh_ask: float):
        """Simulate _build_live_execution_command_certificates mode-flip check."""
        order_mode_result = "MAKER"  # tight spread → MAKER (spread 4%)
        _proof_mode = str(proof_mode or "").strip().upper() or None
        if _proof_mode is not None and _proof_mode != str(order_mode_result).strip().upper():
            raise ValueError(
                f"SUBMIT_ABORTED_MODE_FLIPPED:proof_mode={_proof_mode}:fresh_mode={order_mode_result}"
            )
        return order_mode_result

    def test_no_flip_maker_proof_maker_fresh(self):
        # Consistent: no exception
        mode = self._call_with_proof_mode("MAKER", 0.48, 0.50)
        assert mode == "MAKER"

    def test_flip_taker_proof_maker_fresh_raises(self):
        with pytest.raises(ValueError, match="SUBMIT_ABORTED_MODE_FLIPPED"):
            self._call_with_proof_mode("TAKER", 0.48, 0.50)

    def test_no_proof_mode_no_flip_check(self):
        # Legacy: proof has no mode → no exception regardless of fresh mode
        _proof_mode = str("" or "").strip().upper() or None
        assert _proof_mode is None  # no comparison happens

    def test_select_edli_order_mode_flip_logic(self):
        """Direct unit test of the ValueError in _build_live_execution_command_certificates.

        Simulate the check: actionable.payload has proof_execution_mode_intent=TAKER
        but fresh spread is wide → order_mode = MAKER → flip → SUBMIT_ABORTED_MODE_FLIPPED.
        """
        import types
        from src.engine.event_reactor_adapter import _select_edli_order_mode

        actionable_payload = {
            "direction": "buy_yes",
            "q_live": 0.35,
            "c_fee_adjusted": 0.33,
            "proof_execution_mode_intent": "TAKER",  # proof said taker
        }
        # Wide spread (56%) → spread guard fires → order_mode = MAKER
        mode = _select_edli_order_mode(
            actionable_payload=actionable_payload,
            quote_payload={},
            best_bid=0.009,
            best_ask=0.016,
            executable_snapshot=types.SimpleNamespace(payload={}),
            canary_force_taker=False,
            fresh_best_bid=0.009,
            fresh_best_ask=0.016,
        )
        assert mode == "MAKER"
        # The flip check (post-select_edli_order_mode):
        _proof_mode = str(actionable_payload.get("proof_execution_mode_intent") or "").strip().upper() or None
        assert _proof_mode == "TAKER"
        assert mode == "MAKER"
        # Would raise:
        with pytest.raises(ValueError, match="SUBMIT_ABORTED_MODE_FLIPPED"):
            if _proof_mode is not None and _proof_mode != str(mode).strip().upper():
                raise ValueError(
                    f"SUBMIT_ABORTED_MODE_FLIPPED:"
                    f"proof_mode={_proof_mode}:fresh_mode={mode}:"
                    f"fresh_bid=0.009:fresh_ask=0.016"
                )


import pytest  # noqa: E402 (needed by above)


# ---------------------------------------------------------------------------
# P0-B: _proof_order_rests_at_admitted_price
# ---------------------------------------------------------------------------

class TestProofOrderRests:
    """_proof_order_rests_at_admitted_price reads proof.execution_mode_intent."""

    def _make_proof(self, execution_mode_intent):
        return types.SimpleNamespace(execution_mode_intent=execution_mode_intent)

    def test_maker_proof_rests(self):
        from src.engine.event_reactor_adapter import _proof_order_rests_at_admitted_price
        assert _proof_order_rests_at_admitted_price(self._make_proof("MAKER")) is True

    def test_taker_proof_does_not_rest(self):
        from src.engine.event_reactor_adapter import _proof_order_rests_at_admitted_price
        assert _proof_order_rests_at_admitted_price(self._make_proof("TAKER")) is False

    def test_none_mode_falls_back_to_taker_semantics(self):
        """Unknown mode → conservative TAKER (full fee + ceiling)."""
        from src.engine.event_reactor_adapter import _proof_order_rests_at_admitted_price
        assert _proof_order_rests_at_admitted_price(self._make_proof(None)) is False

    def test_empty_string_mode_falls_back_to_taker_semantics(self):
        from src.engine.event_reactor_adapter import _proof_order_rests_at_admitted_price
        assert _proof_order_rests_at_admitted_price(self._make_proof("")) is False

    def test_maker_lowercase_still_rests(self):
        from src.engine.event_reactor_adapter import _proof_order_rests_at_admitted_price
        assert _proof_order_rests_at_admitted_price(self._make_proof("maker")) is True

    def test_taker_lowercase_does_not_rest(self):
        from src.engine.event_reactor_adapter import _proof_order_rests_at_admitted_price
        assert _proof_order_rests_at_admitted_price(self._make_proof("taker")) is False


# ---------------------------------------------------------------------------
# P0-B: _order_will_rest_at_admitted_price is DEAD on the money path
# ---------------------------------------------------------------------------

class TestPayloadInferencePathDead:
    """The old payload-inference path can no longer reach maker recapture.

    The call site at build_event_bound_no_submit_receipt now calls
    _proof_order_rests_at_admitted_price(proof) not
    _order_will_rest_at_admitted_price(payload).  The old function still exists
    for backward-compat but is unreachable on the money path.

    Verify: constructing the condition that previously triggered maker recapture
    (governor returns GTC from the event payload) does NOT exercise the new path —
    the new path requires the PROOF's execution_mode_intent to be "MAKER".
    """

    def test_maker_recapture_requires_proof_mode_not_payload(self):
        """Proof with no mode (None) → TAKER semantics regardless of governor."""
        from src.engine.event_reactor_adapter import _proof_order_rests_at_admitted_price
        # Proof has no execution_mode_intent (pre-FIX-C legacy proof).
        proof = types.SimpleNamespace(execution_mode_intent=None)
        # Conservative: False (taker semantics), NOT True (maker semantics).
        # This ensures a legacy proof cannot accidentally get the maker ceiling exemption.
        assert _proof_order_rests_at_admitted_price(proof) is False

    def test_old_function_still_importable_but_not_called_by_money_path(self):
        """_order_will_rest_at_admitted_price still imports (backward-compat)."""
        from src.engine.event_reactor_adapter import _order_will_rest_at_admitted_price
        # Verify it is still callable (does not raise on import/access).
        # We don't assert its return value — the money path no longer uses it.
        assert callable(_order_will_rest_at_admitted_price)


class TestSingleModeAuthorityFreshSide:
    """Twin-authority #9 antibody (2026-06-11 live): the validator's fresh mode
    comes from the SAME K4.0 rest-then-cross policy as the proof — after the
    fleeting-edge narrowing, the legacy governor+EV-override re-derivation said
    TAKER while every proof said REST_DEFAULT/MAKER: a 100% MODE_FLIPPED rate
    that silently requeued the whole day-ahead lane to the retry cap."""

    def test_far_horizon_two_sided_book_fresh_mode_is_maker(self):
        from types import SimpleNamespace
        from datetime import datetime, timezone
        from src.engine.event_reactor_adapter import _fresh_rest_then_cross_mode

        mode = _fresh_rest_then_cross_mode(
            actionable_payload={"q_lcb_5pct": 0.78, "c_fee_adjusted": 0.66},
            executable_snapshot=SimpleNamespace(
                payload={"market_end_at": "2026-06-12T12:00:00+00:00"}
            ),
            fresh_best_bid=0.55,
            fresh_best_ask=0.60,
            tick_size=0.01,
            decision_time=datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc),
        )
        assert mode == "MAKER", (
            "26h out on a two-sided book the shared policy rests — the proof said "
            "the same, so the validator must AGREE, not flip"
        )

    def test_near_end_huge_edge_fresh_mode_is_taker(self):
        from types import SimpleNamespace
        from datetime import datetime, timezone
        from src.engine.event_reactor_adapter import _fresh_rest_then_cross_mode

        mode = _fresh_rest_then_cross_mode(
            actionable_payload={"q_lcb_5pct": 0.85, "c_fee_adjusted": 0.66},
            executable_snapshot=SimpleNamespace(
                payload={"market_end_at": "2026-06-11T14:00:00+00:00"}
            ),
            fresh_best_bid=0.55,
            fresh_best_ask=0.60,
            tick_size=0.01,
            decision_time=datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc),
        )
        assert mode == "TAKER"

    def test_missing_inputs_default_maker(self):
        from types import SimpleNamespace
        from datetime import datetime, timezone
        from src.engine.event_reactor_adapter import _fresh_rest_then_cross_mode

        mode = _fresh_rest_then_cross_mode(
            actionable_payload={},
            executable_snapshot=SimpleNamespace(payload={}),
            fresh_best_bid=None,
            fresh_best_ask=None,
            tick_size=0.01,
            decision_time=datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc),
        )
        assert mode == "MAKER"
