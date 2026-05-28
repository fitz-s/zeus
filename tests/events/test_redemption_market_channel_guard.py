# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: docs/operations/edli_v1/PR328_REDEMPTION_PACKAGE.md R4 market-channel proof.

import pytest

from src.events.triggers.market_channel_ingestor import (
    MarketChannelAction,
    assert_market_channel_not_fill_authority,
    handle_public_market_message,
)


def test_market_channel_cannot_write_fill_state():
    with pytest.raises(ValueError, match="cannot write fill truth"):
        assert_market_channel_not_fill_authority(MarketChannelAction(write_fill_truth=True))


def test_tick_size_change_invalidates_snapshot():
    action = handle_public_market_message("tick_size_change")

    assert action.refresh_snapshot is True
    assert action.create_live_trade is False


def test_public_market_message_is_evidence_only():
    action = handle_public_market_message("book")

    assert action.refresh_snapshot is False
    assert action.create_live_trade is False
    assert action.write_fill_truth is False
