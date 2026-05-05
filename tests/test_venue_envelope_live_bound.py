# Created: 2026-05-04
# Last reused or audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1F/phase.json
"""T1F-ADAPTER-ASSERTS-LIVE-BOUND-BEFORE-SDK contract-level relationship test.

Parametrizes over (condition_id, question_id) pairs that are placeholder vs
live-bound and asserts that assert_live_submit_bound() raises for the former
and passes for the latter.  No SDK, no network.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest

from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_envelope(
    *,
    condition_id: str,
    question_id: str,
    yes_token_id: str = "0xaaa",
    no_token_id: str = "0xbbb",
    selected_outcome_token_id: str = "0xaaa",
    outcome_label: str = "YES",
) -> VenueSubmissionEnvelope:
    """Build a minimal VenueSubmissionEnvelope with controlled identity fields."""
    payload = json.dumps(
        {
            "condition_id": condition_id,
            "side": "BUY",
            "price": "0.5",
            "size": "1.0",
        },
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
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        selected_outcome_token_id=selected_outcome_token_id,
        outcome_label=outcome_label,
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


# ---------------------------------------------------------------------------
# Placeholder pairs — assert_live_submit_bound() MUST raise ValueError.
# ---------------------------------------------------------------------------

PLACEHOLDER_CASES = [
    pytest.param(
        "legacy:0xabc", "legacy-compat",
        id="both-legacy-markers",
    ),
    pytest.param(
        "legacy:0xabc", "real-question-id",
        id="legacy-condition-id-only",
    ),
    pytest.param(
        "0xrealcondition", "legacy-compat",
        id="legacy-question-id-only",
    ),
    pytest.param(
        "legacy:0x000000000000", "legacy-compat",
        id="legacy-both-long",
    ),
]


@pytest.mark.parametrize("condition_id,question_id", PLACEHOLDER_CASES)
def test_placeholder_envelope_assert_live_submit_bound_raises(condition_id, question_id):
    """Placeholder envelopes must be rejected by assert_live_submit_bound()."""
    envelope = _make_envelope(
        condition_id=condition_id,
        question_id=question_id,
        # collapsed yes/no tokens make it an additional placeholder signal;
        # we deliberately use distinct tokens here so only condition_id /
        # question_id drives the assertion.
        yes_token_id="0xaaa",
        no_token_id="0xbbb",
        selected_outcome_token_id="0xaaa",
    )
    assert envelope.is_compatibility_placeholder is True
    with pytest.raises(ValueError, match="compatibility submission envelope"):
        envelope.assert_live_submit_bound()


# ---------------------------------------------------------------------------
# Live-bound pairs — assert_live_submit_bound() MUST NOT raise.
# ---------------------------------------------------------------------------

LIVE_BOUND_CASES = [
    pytest.param(
        "0xrealconditionabc123", "question-real-456",
        "0xtoken-yes", "0xtoken-no", "0xtoken-yes", "YES",
        id="live-yes-outcome",
    ),
    pytest.param(
        "0xrealconditionabc123", "question-real-456",
        "0xtoken-yes", "0xtoken-no", "0xtoken-no", "NO",
        id="live-no-outcome",
    ),
]


@pytest.mark.parametrize(
    "condition_id,question_id,yes_token_id,no_token_id,selected_token_id,outcome_label",
    LIVE_BOUND_CASES,
)
def test_live_bound_envelope_assert_live_submit_bound_does_not_raise(
    condition_id, question_id, yes_token_id, no_token_id, selected_token_id, outcome_label
):
    """Live-bound envelopes must pass assert_live_submit_bound() without raising."""
    envelope = _make_envelope(
        condition_id=condition_id,
        question_id=question_id,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        selected_outcome_token_id=selected_token_id,
        outcome_label=outcome_label,
    )
    assert envelope.is_compatibility_placeholder is False
    # Must not raise.
    envelope.assert_live_submit_bound()


def test_collapsed_yes_no_token_identity_is_placeholder():
    """An envelope where yes_token_id == no_token_id is a placeholder."""
    envelope = _make_envelope(
        condition_id="0xrealcondition",
        question_id="real-question",
        yes_token_id="0xsametoken",
        no_token_id="0xsametoken",
        selected_outcome_token_id="0xsametoken",
    )
    assert envelope.is_compatibility_placeholder is True
    with pytest.raises(ValueError, match="compatibility submission envelope"):
        envelope.assert_live_submit_bound()
