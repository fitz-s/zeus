# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: live venue invalid_amount 400 loop 2026-06-10 (venue_command_events
# 39517e446ba94b60 / 5cec15b1de484fbb: "the market buy orders maker amount supports a
# max accuracy of 2 decimals, taker amount a max of 4 decimals") + consolidated
# overhaul K2 (typed contracts at every boundary).
"""Venue amount-grid contract must model the SDK's FLOAT build, not ideal Decimal.

THE INCIDENT: 8.7 shares @ 0.70 is exact-Decimal cents-aligned (6.090) so the old
contract waved it through — but py_clob_client_v2 builds BUY amounts with float
math: round_down(8.7, 2) -> 8.69 (float floor truncates a cent), then
8.69 * 0.7 = 6.0829999... -> the venue's <=2-decimal maker rule 400s it. The
daemon looped REJECTED on the same LA opportunity every ~9 minutes (22:41,
22:50, 22:54Z). Code correctness != data semantics: the contract's notion of
"venue-valid" must equal the maker amount the venue actually receives.
"""

from decimal import Decimal

import pytest

from src.contracts.execution_intent import (
    quantize_submit_shares_for_venue_at_most,
    venue_submit_amount_precision_error,
)


class TestLiveIncidentGoldenCases:
    def test_rejected_live_order_is_flagged(self):
        """The venue-400ed sizing (commands 39517e44/5cec15b1, 8.7 @ 0.70) must be
        ILLEGAL: round_down(8.7, 2) truncates a cent to 8.69 (float 8.7 sits just
        BELOW the exact value) and 8.69*0.7 -> maker 6.083 (3dp > venue 2dp)."""
        err = venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=Decimal("0.7"),
            submitted_shares=Decimal("8.7"),
            order_type="FOK",
        )
        assert err is not None and "SDK-built" in err

    def test_dyadic_share_count_survives_the_rescue(self):
        """8.5 is float-exact (dyadic), so round_down keeps 8.5 and the SDK's
        round_up(+4) rescue lands maker on 5.95 (2dp) — LEGAL. The model must
        NOT over-tighten float noise the SDK itself rescues."""
        err = venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=Decimal("0.7"),
            submitted_shares=Decimal("8.5"),
            order_type="FOK",
        )
        assert err is None

    @pytest.mark.parametrize(
        "shares,price",
        [("12.5", "0.66"), ("9.0", "0.67"), ("14.15", "0.8"), ("5.0", "0.72")],
    )
    def test_actually_filled_live_orders_stay_legal(self, shares, price):
        """All sizings the venue ACCEPTED today must stay legal (no over-tightening)."""
        err = venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=Decimal(price),
            submitted_shares=Decimal(shares),
            order_type="FOK",
        )
        assert err is None

    def test_quantizer_steps_down_to_a_venue_legal_size(self):
        quantized = quantize_submit_shares_for_venue_at_most(
            "buy_no",
            Decimal("8.7"),
            final_limit_price=Decimal("0.7"),
            order_type="FOK",
        )
        assert quantized < Decimal("8.7")
        assert (
            venue_submit_amount_precision_error(
                direction="buy_no",
                final_limit_price=Decimal("0.7"),
                submitted_shares=quantized,
                order_type="FOK",
            )
            is None
        )


class TestModelShape:
    def test_maker_only_rule_applies_to_immediate_buys(self):
        """GTC/GTD (resting maker) paths are not in the immediate-BUY grid rule."""
        assert (
            venue_submit_amount_precision_error(
                direction="buy_no",
                final_limit_price=Decimal("0.7"),
                submitted_shares=Decimal("8.7"),
                order_type="GTC",
            )
            is None
        )

    def test_taker_grid_four_decimals(self):
        err = venue_submit_amount_precision_error(
            direction="buy_yes",
            final_limit_price=Decimal("0.5"),
            submitted_shares=Decimal("8.70001"),
            order_type="FOK",
        )
        assert err is not None

    def test_subcent_tick_price_decimals_respected(self):
        """A 0.001-tick market keeps 3 price decimals in the SDK build; the
        contract must price-round at the tick's precision, not always 2dp."""
        err_fine_tick = venue_submit_amount_precision_error(
            direction="buy_yes",
            final_limit_price=Decimal("0.055"),
            submitted_shares=Decimal("100"),
            order_type="FOK",
            tick_size=Decimal("0.001"),
        )
        # 100 * 0.055 = 5.5 exactly (float-exact): legal under the fine tick.
        assert err_fine_tick is None

    def test_exhaustive_cents_grid_quantizer_never_emits_illegal(self):
        """Property sweep: for every cents-grid share count in [5, 25) at the
        incident price, the at_most quantizer's output is venue-legal."""
        price = Decimal("0.7")
        shares = Decimal("5.00")
        while shares < Decimal("25.00"):
            q = quantize_submit_shares_for_venue_at_most(
                "buy_no", shares, final_limit_price=price, order_type="FOK"
            )
            assert (
                venue_submit_amount_precision_error(
                    direction="buy_no",
                    final_limit_price=price,
                    submitted_shares=q,
                    order_type="FOK",
                )
                is None
            ), f"quantizer emitted illegal size {q} from {shares}"
            shares += Decimal("0.37")
