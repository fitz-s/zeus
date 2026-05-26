# Created: 2026-05-26
# Last reused/audited: 2026-05-26
# Authority basis: PR332 EDLI live canary gate promotion package.
from __future__ import annotations

from scripts.check_edli_live_canary_gate import (
    CANARY_PROOF_PASS,
    FAIL,
    WAITING_FOR_QUALIFYING_EVENT,
    evaluate_canary_artifact,
    load_canary_artifact,
)


def test_missing_canary_artifact_waits_for_qualifying_event(tmp_path):
    assert load_canary_artifact(tmp_path / "missing.json") is None
    result = evaluate_canary_artifact(None)
    assert result.status == WAITING_FOR_QUALIFYING_EVENT


def test_canary_without_user_channel_or_reconcile_fails():
    artifact = _valid_artifact()
    artifact.pop("user_channel_observation")

    result = evaluate_canary_artifact(artifact)

    assert result.status == FAIL
    assert "CANARY_REQUIRES_USER_CHANNEL_OR_RECONCILE" in result.reasons


def test_canary_with_unresolved_submit_unknown_fails():
    artifact = _valid_artifact(unresolved_submit_unknown=True, submit_unknown={"status": "POST_SUBMIT_UNKNOWN"})

    result = evaluate_canary_artifact(artifact)

    assert result.status == FAIL
    assert "CANARY_SUBMIT_UNKNOWN_UNRESOLVED" in result.reasons


def test_canary_with_mismatched_economic_object_fails():
    artifact = _valid_artifact(pre_submit={"condition_id": "condition-1", "token_id": "other-token", "side": "BUY"})

    result = evaluate_canary_artifact(artifact)

    assert result.status == FAIL
    assert "CANARY_PRE_SUBMIT_TOKEN_ID_MISMATCH" in result.reasons


def test_canary_with_stale_quote_fails():
    artifact = _valid_artifact(quote_age_ms=1500)

    result = evaluate_canary_artifact(artifact, max_quote_age_ms=1000)

    assert result.status == FAIL
    assert "CANARY_QUOTE_STALE" in result.reasons


def test_canary_with_confirmed_lifecycle_and_cap_transition_passes():
    result = evaluate_canary_artifact(_valid_artifact())

    assert result.status == CANARY_PROOF_PASS
    assert result.reasons == ()


def _valid_artifact(**overrides):
    artifact = {
        "event_id": "event-1",
        "aggregate_id": "event-1:intent-1",
        "final_intent_id": "intent-1",
        "execution_command_id": "command-1",
        "condition_id": "condition-1",
        "token_id": "token-1",
        "direction": "YES",
        "side": "BUY",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "book_hash": "book-hash-1",
        "quote_seen_at": "2026-05-26T12:00:00+00:00",
        "quote_age_ms": 100,
        "best_bid": 0.42,
        "best_ask": 0.43,
        "limit_price": 0.42,
        "tickSize": "0.01",
        "negRisk": False,
        "balance_allowance_witness": {"status": "OK"},
        "heartbeat_witness": {"status": "OK"},
        "idempotency_key": "idem-1",
        "live_cap_usage_id": "usage-1",
        "venue_order_id": "venue-1",
        "user_channel_observation": {"trade_status": "CONFIRMED", "fill_authority_state": "FILL_CONFIRMED"},
        "cap_transition": {"to_status": "CONSUMED"},
        "order_lifecycle_projection": {"current_state": "USER_TRADE_OBSERVED", "pending_reconcile": False},
        "expected_edge": 0.01,
        "realized_state": "CONFIRMED",
        "pre_submit": {"condition_id": "condition-1", "token_id": "token-1", "side": "BUY"},
    }
    artifact.update(overrides)
    return artifact
