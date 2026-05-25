# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §11.2, §16 A08.
from __future__ import annotations

from datetime import datetime, timezone

from src.decision_kernel import claims
from src.decision_kernel.adapters.market_channel_adapter import (
    build_market_channel_quote_certificate,
    build_market_data_certificate,
)


def test_market_channel_creates_market_data_certificate_only():
    cert = build_market_data_certificate(
        semantic_key="market-data:token",
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
        payload={"token_id": "token-1"},
    )
    assert cert.certificate_type == claims.MARKET_DATA
    assert cert.payload["source_kind"] == claims.PUBLIC_MARKET_CHANNEL_SOURCE


def test_market_channel_quote_has_no_fill_claim():
    cert = build_market_channel_quote_certificate(
        semantic_key="quote:token",
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
        payload={"token_id": "token-1", "best_ask": 0.42},
    )
    assert cert.certificate_type == claims.QUOTE_FEASIBILITY
    assert cert.payload["fill_claim"] is False
