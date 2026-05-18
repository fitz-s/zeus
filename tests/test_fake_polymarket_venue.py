# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: R3 T1 FakePolymarketVenue protocol and failure-injection antibodies.
# Reuse: Run when fake/live venue adapter protocol, failure modes, or fake/live adapter parity changes.
# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/T1.yaml
"""R3 T1 fake venue unit antibodies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from src.contracts import Direction, ExecutionIntent
from src.contracts.slippage_bps import SlippageBps


@dataclass(frozen=True)
class FakeSnapshot:
    condition_id: str = "cond-t1"
    question_id: str = "question-t1"
    yes_token_id: str = "yes-token"
    no_token_id: str = "no-token"
    tick_size: Decimal = Decimal("0.01")
    min_order_size: Decimal = Decimal("5")
    neg_risk: bool = False
    fee_details: dict = field(default_factory=lambda: {"bps": 0, "builder_fee_bps": 0})
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    freshness_window_seconds: int = 300


def _intent(token_id: str = "yes-token") -> ExecutionIntent:
    return ExecutionIntent(
        direction=Direction.YES,
        target_size_usd=10.0,
        limit_price=0.50,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=True,
        market_id="market-t1",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.10,
    )


def test_fake_polymarket_venue_implements_runtime_adapter_protocol():
    from src.venue.polymarket_v2_adapter import PolymarketV2AdapterProtocol
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    fake = FakePolymarketVenue()

    assert isinstance(fake, PolymarketV2AdapterProtocol)


def test_fake_submit_uses_same_submit_result_and_envelope_shape():
    from src.venue.polymarket_v2_adapter import SubmitResult
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    fake = FakePolymarketVenue()
    envelope = fake.create_submission_envelope(_intent(), FakeSnapshot(), "GTC")

    result = fake.submit(envelope)

    assert isinstance(result, SubmitResult)
    assert result.status == "accepted"
    assert result.envelope.order_id == "fake-ord-000001"
    assert set(result.envelope.to_dict()) == {
        "schema_version",
        "sdk_package",
        "sdk_version",
        "host",
        "chain_id",
        "funder_address",
        "condition_id",
        "question_id",
        "yes_token_id",
        "no_token_id",
        "selected_outcome_token_id",
        "outcome_label",
        "side",
        "price",
        "size",
        "order_type",
        "post_only",
        "tick_size",
        "min_order_size",
        "neg_risk",
        "fee_details",
        "canonical_pre_sign_payload_hash",
        "signed_order",
        "signed_order_hash",
        "raw_request_hash",
        "raw_response_json",
        "order_id",
        "trade_ids",
        "transaction_hashes",
        "error_code",
        "error_message",
        "captured_at",
    }


def test_failure_injection_timeout_after_post_creates_unknown_side_effect_shape():
    from tests.fakes.polymarket_v2 import FailureMode, FakePolymarketVenue

    fake = FakePolymarketVenue()
    fake.inject(FailureMode.TIMEOUT_AFTER_POST)
    envelope = fake.create_submission_envelope(_intent(), FakeSnapshot(), "GTC")

    try:
        fake.submit(envelope)
    except TimeoutError as exc:
        assert "after post" in str(exc)
    else:  # pragma: no cover - test must fail loudly if fake stops raising.
        raise AssertionError("timeout_after_post must raise")

    assert fake.get_open_orders()[0].order_id == "fake-ord-000001"


def test_fake_redeem_preserves_r1_command_ledger_boundary():
    from tests.fakes.polymarket_v2 import FakePolymarketVenue

    result = FakePolymarketVenue().redeem("condition-123")

    assert result["success"] is False
    assert result["errorCode"] == "REDEEM_DEFERRED_TO_R1"


def test_venue_auth_fallback_logs_greppable_warning(caplog):
    """F92: VENUE_AUTH_FALLBACK_TRIGGERED must appear in logs when
    create_or_derive_api_key is used (no static api_creds provided).
    Regression: silent fallback means invisible degradation."""
    import logging
    from unittest.mock import MagicMock, patch
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    fake_creds = object()
    mock_client = MagicMock()
    mock_client.create_or_derive_api_key.return_value = fake_creds

    with patch.object(
        PolymarketV2Adapter,
        "_default_client_factory",
        side_effect=PolymarketV2Adapter._default_client_factory,
    ):
        pass  # patch not needed — exercise via factory kwargs directly

    # Invoke _default_client_factory directly without api_creds so the fallback fires
    adapter = PolymarketV2Adapter.__new__(PolymarketV2Adapter)

    with caplog.at_level(logging.WARNING, logger="src.venue.polymarket_v2_adapter"):
        with patch("py_clob_client_v2.client.ClobClient", return_value=mock_client):
            adapter._default_client_factory(
                host="https://clob.polymarket.com",
                chain_id=137,
                signer_key="0x" + "a" * 64,
            )

    assert any(
        "VENUE_AUTH_FALLBACK_TRIGGERED" in record.message
        for record in caplog.records
    ), "VENUE_AUTH_FALLBACK_TRIGGERED log line must fire when no static api_creds provided"
