# Created: 2026-05-04
# Last reused or audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1F/phase.json
"""T1F-PLACEHOLDER-ENVELOPE-FAKE-SDK-COUNT-ZERO and T1F-COMPAT-SUBMIT-LIMIT-ORDER-REJECTS-OR-FAKE.

Verifies that:
1. A placeholder envelope passed to submit() is rejected before any SDK call.
2. submit_limit_order() rejects by default (live-mode gate) before any SDK call.
3. Both paths leave fake_client call counts at zero.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_placeholder_envelope(
    *,
    condition_id: str = "legacy:0xabc",
    question_id: str = "legacy-compat",
) -> VenueSubmissionEnvelope:
    payload = json.dumps(
        {"condition_id": condition_id, "side": "BUY", "price": "0.5", "size": "1.0"},
        sort_keys=True,
    )
    payload_hash = _sha256(payload)
    raw_hash = _sha256(payload + ":raw")
    return VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="0.0.0",
        host="https://clob.polymarket.com",
        chain_id=137,
        funder_address="0xfunder",
        condition_id=condition_id,
        question_id=question_id,
        yes_token_id="0xtoken",
        no_token_id="0xtoken",
        selected_outcome_token_id="0xtoken",
        outcome_label="YES",
        side="BUY",
        price=Decimal("0.5"),
        size=Decimal("1.0"),
        order_type="GTC",
        post_only=False,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
        fee_details={"bps": 0, "builder_fee_bps": 0},
        canonical_pre_sign_payload_hash=payload_hash,
        signed_order=None,
        signed_order_hash=None,
        raw_request_hash=raw_hash,
        raw_response_json=None,
        order_id=None,
        trade_ids=(),
        transaction_hashes=(),
        error_code=None,
        error_message=None,
        captured_at="2026-05-04T00:00:00+00:00",
    )


def _build_adapter(tmp_path: Path, fake_client):
    """Construct a PolymarketV2Adapter wired to fake_client."""
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    evidence = tmp_path / "q1_zeus_egress_2026-04-27.txt"
    evidence.write_text("daemon host probe ok\n")
    return PolymarketV2Adapter(
        host="https://clob.polymarket.com",
        funder_address="0xfunder",
        signer_key="test-key",
        chain_id=137,
        q1_egress_evidence_path=evidence,
        client_factory=lambda **kwargs: fake_client,
    )


class _FakePreflightClient:
    """Minimal client that satisfies preflight (get_ok) but tracks SDK order calls."""

    def __init__(self):
        self.create_and_post_order = MagicMock(return_value={"orderID": "x", "status": "LIVE"})
        self.create_order = MagicMock(return_value=b"fake-signed")
        self.post_order = MagicMock(return_value={"orderID": "x", "status": "LIVE"})
        self.get_ok_calls = 0
        self.get_neg_risk_calls = 0
        self.get_tick_size_calls = 0
        self.get_fee_rate_bps_calls = 0

    def get_ok(self):
        self.get_ok_calls += 1
        return {"ok": True}

    def get_neg_risk(self, token_id):
        self.get_neg_risk_calls += 1
        return False

    def get_tick_size(self, token_id):
        self.get_tick_size_calls += 1
        return "0.01"

    def get_fee_rate_bps(self, token_id):
        self.get_fee_rate_bps_calls += 1
        return 0


# ---------------------------------------------------------------------------
# Test: direct submit() with placeholder envelope — SDK must NOT be called.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "condition_id,question_id",
    [
        pytest.param("legacy:0xabc", "legacy-compat", id="both-legacy-markers"),
        pytest.param("legacy:0xabc", "real-question", id="legacy-condition-id-only"),
        pytest.param("0xrealcondition", "legacy-compat", id="legacy-question-id-only"),
    ],
)
def test_submit_placeholder_envelope_sdk_call_count_zero(tmp_path, condition_id, question_id):
    """submit() with a placeholder envelope must reject before any SDK call."""
    fake_client = _FakePreflightClient()
    adapter = _build_adapter(tmp_path, fake_client)

    envelope = _make_placeholder_envelope(condition_id=condition_id, question_id=question_id)
    assert envelope.is_compatibility_placeholder is True

    result = adapter.submit(envelope)

    assert result.status == "rejected"
    assert result.error_code == "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY"
    # Core invariant: zero SDK order calls.
    assert fake_client.create_and_post_order.call_count == 0
    assert fake_client.post_order.call_count == 0
    assert fake_client.create_order.call_count == 0


# ---------------------------------------------------------------------------
# Test: submit_limit_order() default (live-mode gate) — SDK must NOT be called.
# ---------------------------------------------------------------------------

def test_submit_limit_order_default_rejects_without_sdk_call(tmp_path):
    """submit_limit_order() must reject before SDK contact when _allow_compat_for_test is False."""
    fake_client = _FakePreflightClient()
    adapter = _build_adapter(tmp_path, fake_client)

    result = adapter.submit_limit_order(
        token_id="yes-token",
        price=0.5,
        size=3.0,
        side="BUY",
        # _allow_compat_for_test defaults to False — gate must fire.
    )

    assert result.status == "rejected"
    assert result.error_code == "COMPAT_SUBMIT_NOT_PERMITTED_IN_LIVE"
    # Core invariant: zero SDK order calls.
    assert fake_client.create_and_post_order.call_count == 0
    assert fake_client.post_order.call_count == 0
    assert fake_client.create_order.call_count == 0


def test_submit_limit_order_explicit_false_rejects(tmp_path):
    """Explicit _allow_compat_for_test=False also triggers rejection."""
    fake_client = _FakePreflightClient()
    adapter = _build_adapter(tmp_path, fake_client)

    result = adapter.submit_limit_order(
        token_id="yes-token",
        price=0.5,
        size=3.0,
        side="BUY",
        _allow_compat_for_test=False,
    )

    assert result.status == "rejected"
    assert result.error_code == "COMPAT_SUBMIT_NOT_PERMITTED_IN_LIVE"
    assert fake_client.create_and_post_order.call_count == 0
    assert fake_client.post_order.call_count == 0
    assert fake_client.create_order.call_count == 0


def test_submit_limit_order_allow_compat_for_test_true_reaches_compat_path(tmp_path):
    """With _allow_compat_for_test=True the compat path proceeds past the gate."""
    fake_client = _FakePreflightClient()
    adapter = _build_adapter(tmp_path, fake_client)

    # With the flag set, the call proceeds past the gate and reaches the
    # preflight + snapshot path. The submit() will then reject it via
    # assert_live_submit_bound() (T1F-ADAPTER-ASSERTS-LIVE-BOUND-BEFORE-SDK),
    # so result is still rejected but for a *different* error code.
    result = adapter.submit_limit_order(
        token_id="yes-token",
        price=0.5,
        size=3.0,
        side="BUY",
        _allow_compat_for_test=True,
    )

    # The compat path calls submit() which now asserts live-bound first.
    # So even with _allow_compat_for_test=True, a legacy envelope can't reach
    # the SDK because the adapter.submit() guard fires.
    assert result.status == "rejected"
    assert result.error_code == "BOUND_ENVELOPE_NOT_LIVE_AUTHORITY"
    # SDK order calls still zero.
    assert fake_client.create_and_post_order.call_count == 0
    assert fake_client.post_order.call_count == 0
    assert fake_client.create_order.call_count == 0
