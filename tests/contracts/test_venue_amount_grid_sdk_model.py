# Created: 2026-06-10
# Last reused or audited: 2026-09-12
# Authority basis: live venue invalid_amount 400 loop 2026-06-10 and the
# installed SDK's Decimal OrderArgs path.
"""Venue amount-grid contract must match the SDK's Decimal-input build.

The SDK receives Decimal price and size from the signed OrderArgs. Its rounding
helpers then perform their documented float arithmetic and rescue step. A
contract check that converts to float before those helpers can floor 2.01 to
2.00 and falsely approve the live 2.01 @ 0.58 order.
"""

from decimal import Decimal

import pytest

from src.contracts.execution_intent import (
    quantize_submit_shares_for_venue_at_most,
    venue_submit_amount_precision_error,
)


class TestLiveIncidentGoldenCases:
    def test_decimal_2p01_at_0p58_is_flagged_for_fok_and_fak(self):
        """Decimal 2.01 survives SDK size rounding and creates maker 1.1658."""
        for order_type in ("FOK", "FAK"):
            err = venue_submit_amount_precision_error(
                direction="buy_no",
                final_limit_price=Decimal("0.58"),
                submitted_shares=Decimal("2.01"),
                order_type=order_type,
            )
            assert err is not None and "SDK-built" in err

    def test_decimal_2p01_at_0p66_is_also_flagged(self):
        """The same size remains invalid at a different two-decimal price."""
        err = venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=Decimal("0.66"),
            submitted_shares=Decimal("2.01"),
            order_type="FOK",
        )
        assert err is not None and "SDK-built" in err

    def test_decimal_8p7_at_0p70_is_legal(self):
        """Decimal input prevents the stale pre-SDK float floor of 8.7."""
        err = venue_submit_amount_precision_error(
            direction="buy_no",
            final_limit_price=Decimal("0.70"),
            submitted_shares=Decimal("8.7"),
            order_type="FOK",
        )
        assert err is None

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
            Decimal("2.01"),
            final_limit_price=Decimal("0.58"),
            order_type="FOK",
        )
        assert quantized == Decimal("2.00")
        assert (
            venue_submit_amount_precision_error(
                direction="buy_no",
                final_limit_price=Decimal("0.58"),
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

    @pytest.mark.parametrize("order_type", ["FOK", "FAK"])
    @pytest.mark.parametrize("direction", ["sell_yes", "sell_no"])
    def test_sell_twin_is_unchanged_by_buy_amount_gate(self, direction, order_type):
        assert (
            venue_submit_amount_precision_error(
                direction=direction,
                final_limit_price=Decimal("0.58"),
                submitted_shares=Decimal("2.01"),
                order_type=order_type,
            )
            is None
        )

    @pytest.mark.parametrize("price", ["0.05", "0.95"])
    def test_price_boundary_inputs_keep_amount_gate_independent(self, price):
        assert (
            venue_submit_amount_precision_error(
                direction="buy_yes",
                final_limit_price=Decimal(price),
                submitted_shares=Decimal("2.00"),
                order_type="FAK",
            )
            is None
        )

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
