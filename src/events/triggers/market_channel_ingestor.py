"""Public market-channel guardrails for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


MarketChannelMessageType = Literal["book", "price_change", "best_bid_ask", "tick_size_change"]


@dataclass(frozen=True)
class MarketChannelAction:
    refresh_snapshot: bool = False
    write_fill_truth: bool = False
    create_live_trade: bool = False
    reason: str = "ignored"


def handle_public_market_message(message_type: MarketChannelMessageType) -> MarketChannelAction:
    if message_type == "tick_size_change":
        return MarketChannelAction(refresh_snapshot=True, reason="tick_size_change")
    return MarketChannelAction(reason=f"{message_type}_evidence_only")


def assert_market_channel_not_fill_authority(action: MarketChannelAction) -> None:
    if action.write_fill_truth or action.create_live_trade:
        raise ValueError("public market channel cannot write fill truth or create live trades")
