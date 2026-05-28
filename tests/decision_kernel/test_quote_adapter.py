# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §13.2, §16 A14-A17.
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.decision_kernel.adapters.quote_adapter import (
    NativeQuote,
    build_quote_feasibility_certificate,
    native_execution_price,
    reject_display_price,
)


def test_buy_yes_native_yes_ask():
    quote = NativeQuote(outcome="YES", best_bid=0.41, best_ask=0.43)
    assert native_execution_price(side="BUY", quote=quote) == 0.43


def test_buy_no_native_no_ask():
    quote = NativeQuote(outcome="NO", best_bid=0.57, best_ask=0.59)
    assert native_execution_price(side="BUY", quote=quote) == 0.59


def test_sell_held_token_bid():
    quote = NativeQuote(outcome="YES", best_bid=0.41, best_ask=0.43)
    assert native_execution_price(side="SELL", quote=quote) == 0.41


def test_midpoint_last_trade_forbidden():
    for kind in ("midpoint", "display_probability", "last_trade", "complement_cost"):
        with pytest.raises(ValueError, match="forbidden"):
            reject_display_price(kind)


def test_public_visible_depth_not_fill():
    cert = build_quote_feasibility_certificate(
        semantic_key="quote:token",
        decision_time=datetime(2026, 5, 25, 12, tzinfo=timezone.utc),
        side="BUY",
        quote=NativeQuote(outcome="YES", best_bid=0.41, best_ask=0.43, visible_depth=100.0),
    )
    assert cert.payload["fill_claim"] is False
    assert cert.payload["native_execution_price"] == 0.43
