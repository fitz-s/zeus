# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: GATE #84 would_cross taker conditioning
"""
Safety matrix: pre-submit would_cross_book gate conditioned on post_only.

Invariant: the would_cross_book=False enforcement exists to protect MAKER
(post_only=True) orders from crossing the book and violating venue post-only
semantics.  A TAKER order (post_only=False, FOK/FAK) is designed to cross the
book to fill immediately, so would_cross_book=True is EXPECTED for a taker and
must NOT be rejected.

Fail-closed: when post_only is missing or None, the check STILL enforces
would_cross_book=False (we don't know it's safe to cross, so we treat as maker).

NOTE on Layer 1 (_validate_pre_submit_revalidation_payload):
  Layer 1 ALSO enforces post_only=True (a separate existing gate at line 634)
  for executor-law compliance.  The would_cross_book conditioning is tested here
  at both layers.  For Layer 1 cases where post_only=False would be needed, the
  full matrix (c/d) is more cleanly isolated at Layer 2 (verifier), which does
  NOT carry the post_only=True executor-law enforcement.  Layer 1 tests (a/b/e)
  directly prove the maker-safety case and fail-closed case via the Layer 1 path.

Tested at BOTH enforcement layers:
  Layer 1: _validate_pre_submit_revalidation_payload  (live_order_aggregate)
  Layer 2: _verify_pre_submit_revalidation_for_command (verifier)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.verifier import _verify_pre_submit_revalidation_for_command
from src.events.live_order_aggregate import (
    LiveOrderAggregateError,
    _validate_pre_submit_revalidation_payload,
)

NOW = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)

_PROVENANCE = {
    "book_authority_id": "execution_feasibility_evidence",
    "book_captured_at": NOW.isoformat(),
    "heartbeat_authority_id": "heartbeat_supervisor",
    "heartbeat_checked_at": NOW.isoformat(),
    "user_ws_authority_id": "ws_gap_guard",
    "user_ws_checked_at": NOW.isoformat(),
    "venue_connectivity_authority_id": "polymarket_public_orderbook",
    "venue_connectivity_checked_at": NOW.isoformat(),
    "balance_allowance_authority_id": "polymarket_wallet_readonly",
    "balance_allowance_checked_at": NOW.isoformat(),
}


def _qkernel_economics_for(payload: dict) -> dict:
    side = "NO" if str(payload.get("direction") or "").endswith("_no") else "YES"
    return {
        "route_id": f"DIRECT_{side}:bin-1@proof",
        "side": side,
        "payoff_q_point": payload.get("q_live", 0.70),
        "payoff_q_lcb": payload.get("q_lcb_5pct", 0.60),
        "direction_law_ok": True,
        "coherence_allows": True,
        "selection_guard_basis": "SELECTION_BETA_95",
        "selection_guard_abstained": False,
        "selection_guard_q_safe": payload.get("q_lcb_5pct", 0.60),
    }


def _maker_pre_submit(**overrides) -> dict:
    """Valid MAKER pre-submit payload (post_only=True, GTC, would_cross_book=False by default)."""
    payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "side": "BUY",
        "direction": "buy_yes",
        "order_type": "POST_ONLY_LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": NOW.isoformat(),
        "quote_seen_at": NOW.isoformat(),
        "quote_age_ms": 0,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash-1",
        "current_best_bid": 0.39,
        "current_best_ask": 0.41,
        "limit_price": 0.40,
        "size": 10.0,
        "q_live": 0.70,
        "q_lcb_5pct": 0.60,
        "expected_edge": 0.10,
        "min_entry_price": 0.05,
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.02,
        "expected_edge_source_certificate_hash": "actionable-hash-1",
        "cost_basis_source_certificate_hash": "cost-hash-1",
        "would_cross_book": False,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 1.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        **_PROVENANCE,
    }
    payload.update(overrides)
    payload.setdefault("qkernel_execution_economics", _qkernel_economics_for(payload))
    return payload


def _taker_pre_submit(**overrides) -> dict:
    """TAKER pre-submit payload (post_only=False, FOK, would_cross_book=True by default).

    Used at Layer 2 only — Layer 1 also enforces post_only=True executor-law
    which is a separate gate outside this task's scope.
    """
    payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "side": "BUY",
        "direction": "buy_yes",
        "order_type": "FOK",
        "time_in_force": "FOK",
        "post_only": False,
        "checked_at": NOW.isoformat(),
        "quote_seen_at": NOW.isoformat(),
        "quote_age_ms": 0,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash-1",
        "current_best_bid": 0.60,
        "current_best_ask": 0.61,
        "limit_price": 0.61,
        "size": 10.0,
        "q_live": 0.75,
        "q_lcb_5pct": 0.70,
        "expected_edge": 0.05,
        "min_entry_price": 0.05,
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.02,
        "expected_edge_source_certificate_hash": "actionable-hash-1",
        "cost_basis_source_certificate_hash": "cost-hash-1",
        "would_cross_book": True,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 1.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "live_cap_usage_id": "cap-1",
        **_PROVENANCE,
    }
    payload.update(overrides)
    payload.setdefault("qkernel_execution_economics", _qkernel_economics_for(payload))
    return payload


# ---------------------------------------------------------------------------
# Layer 2 helpers — minimal command + supporting dicts
# ---------------------------------------------------------------------------

def _good_command(pre_submit: dict) -> dict:
    """Minimal execution-command dict matching pre_submit for Layer 2."""
    return {
        "event_id": pre_submit.get("event_id", "event-1"),
        "final_intent_id": pre_submit.get("final_intent_id", "intent-1"),
        "condition_id": pre_submit.get("condition_id", "condition-1"),
        "token_id": pre_submit.get("token_id", "yes-1"),
        "side": pre_submit.get("side", "BUY"),
        "direction": pre_submit.get("direction", "buy_yes"),
        "order_type": pre_submit.get("order_type", "FOK"),
        "time_in_force": pre_submit.get("time_in_force", "FOK"),
        "post_only": pre_submit.get("post_only"),
        "limit_price": pre_submit.get("limit_price", 0.61),
        "tick_size": pre_submit.get("tick_size", 0.01),
        "min_order_size": pre_submit.get("min_order_size", 1.0),
        "neg_risk": pre_submit.get("neg_risk", False),
        "aggregate_pre_submit_event_hash": "pre-submit-hash",
        "aggregate_execution_command_event_hash": "cmd-hash",
        "live_cap_usage_id": "cap-1",
    }


def _good_final_intent() -> dict:
    return {"final_intent_id": "intent-1"}


def _good_live_cap() -> dict:
    return {"usage_id": "cap-1"}


# ===========================================================================
# LAYER 1 — _validate_pre_submit_revalidation_payload
#
# Layer 1 also enforces post_only=True at line 634 (executor-law gate, separate
# from would_cross_book).  We test the would_cross_book gate here for maker
# payloads (post_only=True passes line 634, then reaches the would_cross check).
# ===========================================================================

class TestLayer1ValidatePreSubmit:
    """Safety matrix at live_order_aggregate._validate_pre_submit_revalidation_payload.

    Cases (a)/(b)/(e) test the would_cross_book gate in the maker context.
    Cases (c)/(d) for takers are isolated at Layer 2 (see below) because Layer 1
    carries a separate post_only=True executor-law gate outside this task scope.
    """

    # (a) post_only=True + would_cross_book=True → MUST raise (maker safety preserved)
    def test_maker_crossing_raises(self):
        payload = _maker_pre_submit(would_cross_book=True)
        with pytest.raises(LiveOrderAggregateError, match="would_cross_book"):
            _validate_pre_submit_revalidation_payload(payload)

    # (b) post_only=True + would_cross_book=False → passes
    def test_maker_not_crossing_passes(self):
        payload = _maker_pre_submit(would_cross_book=False)
        _validate_pre_submit_revalidation_payload(payload)  # must not raise

    # (e) post_only missing/None + would_cross_book=True → MUST raise (fail-closed)
    # Note: missing post_only also hits line 634 (post_only is not True → raise).
    # The test verifies that a crossing order with ambiguous post_only status is
    # rejected — fail-closed regardless of which gate fires.
    def test_missing_post_only_crossing_raises_fail_closed(self):
        payload = _maker_pre_submit(would_cross_book=True)
        del payload["post_only"]
        with pytest.raises(LiveOrderAggregateError):
            _validate_pre_submit_revalidation_payload(payload)

    def test_none_post_only_crossing_raises_fail_closed(self):
        payload = _maker_pre_submit(post_only=None, would_cross_book=True)
        with pytest.raises(LiveOrderAggregateError):
            _validate_pre_submit_revalidation_payload(payload)


# ===========================================================================
# LAYER 2 — _verify_pre_submit_revalidation_for_command
#
# Layer 2 (verifier) does NOT carry the post_only=True executor-law gate, so
# it isolates the would_cross_book conditioning cleanly.  Full safety matrix:
# (a) maker crossing → raises; (b) maker not crossing → passes;
# (c) taker crossing → MUST pass (THE FIX; currently raises pre-fix);
# (d) taker not crossing → passes; (e) missing/None post_only + crossing → raises.
# ===========================================================================

class TestLayer2VerifyPreSubmitForCommand:
    """Full 5-case safety matrix at verifier._verify_pre_submit_revalidation_for_command."""

    def _call(self, pre_submit: dict) -> None:
        ps = dict(pre_submit)
        ps.setdefault("aggregate_event_hash", "pre-submit-hash")
        ps.setdefault("live_cap_usage_id", "cap-1")
        command = _good_command(ps)
        _verify_pre_submit_revalidation_for_command(
            command,
            ps,
            _good_final_intent(),
            _good_live_cap(),
        )

    # (a) post_only=True + would_cross_book=True → MUST raise (maker safety preserved)
    def test_maker_crossing_raises(self):
        ps = _taker_pre_submit(post_only=True, would_cross_book=True, time_in_force="GTC", order_type="POST_ONLY_LIMIT")
        with pytest.raises(CertificateVerificationError, match="would_cross_book"):
            self._call(ps)

    # (b) post_only=True + would_cross_book=False → passes
    def test_maker_not_crossing_passes(self):
        ps = _taker_pre_submit(post_only=True, would_cross_book=False, time_in_force="GTC", order_type="POST_ONLY_LIMIT")
        self._call(ps)  # must not raise

    # (c) post_only=False + would_cross_book=True → MUST pass (taker, THE FIX)
    def test_taker_crossing_passes(self):
        ps = _taker_pre_submit(post_only=False, would_cross_book=True)
        # PRE-FIX: this raises CertificateVerificationError("would_cross_book must be false")
        # POST-FIX: must not raise
        self._call(ps)  # must not raise

    # (d) post_only=False + would_cross_book=False → passes
    def test_taker_not_crossing_passes(self):
        ps = _taker_pre_submit(post_only=False, would_cross_book=False)
        self._call(ps)  # must not raise

    # (e) post_only missing + would_cross_book=True → MUST raise (fail-closed)
    def test_missing_post_only_crossing_raises_fail_closed(self):
        ps = _taker_pre_submit(would_cross_book=True)
        del ps["post_only"]
        with pytest.raises(CertificateVerificationError, match="would_cross_book"):
            self._call(ps)

    # (e-variant) post_only=None + would_cross_book=True → MUST raise (fail-closed)
    def test_none_post_only_crossing_raises_fail_closed(self):
        ps = _taker_pre_submit(post_only=None, would_cross_book=True)
        with pytest.raises(CertificateVerificationError, match="would_cross_book"):
            self._call(ps)

    def test_negative_submit_edge_raises(self):
        ps = _maker_pre_submit(q_live=0.0054, q_lcb_5pct=0.003, limit_price=0.006)
        with pytest.raises(CertificateVerificationError, match="submit q_lcb-minus-limit"):
            self._call(ps)

    def test_micro_edge_density_raises(self):
        ps = _maker_pre_submit(
            direction="buy_no",
            token_id="no-1",
            q_live=0.986261171798223,
            q_lcb_5pct=0.986261171798223,
            limit_price=0.98,
            size=21.99,
            expected_edge=0.005,
            min_expected_profit_usd=0.05,
            min_submit_edge_density=0.02,
        )
        with pytest.raises(CertificateVerificationError, match="submit edge density"):
            self._call(ps)

    def test_qkernel_direct_payoff_above_receipt_lcb_raises(self):
        ps = _maker_pre_submit(
            direction="buy_no",
            token_id="no-1",
            q_live=0.986261171798223,
            q_lcb_5pct=0.986261171798223,
            limit_price=0.98,
            expected_edge=0.005,
            min_submit_edge_density=0.0,
            qkernel_execution_economics={
                "route_id": "DIRECT_NO:b24@proof",
                "side": "NO",
                "payoff_q_point": 0.986261171798223,
                "payoff_q_lcb": 0.998678563135879,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.986261171798223,
            },
        )
        with pytest.raises(CertificateVerificationError, match="payoff_q_lcb exceeds"):
            self._call(ps)
