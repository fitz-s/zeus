# Created: 2026-07-02
# Last reused or audited: 2026-10-02
# Purpose: Truth-table antibodies for the SCH-W1.2-ORDER-STATE derived predicates
#          (is_delayed, entry_rest_disposition) and the selector's fresh BUY
#          target holding (fresh_buy_target_holding).
# Reuse: Run when changing src/state/order_state_predicates.py or the C3 standing
#        ENTRY valuation that consumes entry_rest_disposition.
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md;
#                  standing ENTRY keep-by-value law (operator, 2026-09-30).
"""Tests for src/state/order_state_predicates.py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

import src.state.order_state_predicates as predicates
from src.state.order_state_predicates import entry_rest_disposition, is_delayed

UTC = timezone.utc
NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# is_delayed
# ---------------------------------------------------------------------------

class TestIsDelayed:
    @pytest.mark.parametrize("state", ["SUBMITTING", "POSTING", "SIGNED_PERSISTED"])
    def test_in_flight_state_past_sla_is_delayed(self, state):
        command = {"state": state, "updated_at": (NOW - timedelta(seconds=30)).isoformat()}
        assert is_delayed(command, now=NOW, submit_flight_sla_seconds=10.0) is True

    @pytest.mark.parametrize("state", ["SUBMITTING", "POSTING", "SIGNED_PERSISTED"])
    def test_in_flight_state_within_sla_is_not_delayed(self, state):
        command = {"state": state, "updated_at": (NOW - timedelta(seconds=5)).isoformat()}
        assert is_delayed(command, now=NOW, submit_flight_sla_seconds=10.0) is False

    def test_terminal_command_never_delayed(self):
        command = {"state": "FILLED", "updated_at": (NOW - timedelta(days=1)).isoformat()}
        assert is_delayed(command, now=NOW, submit_flight_sla_seconds=10.0) is False

    def test_intent_created_not_in_flight_is_not_delayed(self):
        command = {"state": "INTENT_CREATED", "updated_at": (NOW - timedelta(days=1)).isoformat()}
        assert is_delayed(command, now=NOW, submit_flight_sla_seconds=10.0) is False

    def test_missing_updated_at_is_not_delayed(self):
        command = {"state": "SUBMITTING"}
        assert is_delayed(command, now=NOW, submit_flight_sla_seconds=10.0) is False

    def test_accepts_datetime_object_for_updated_at(self):
        command = {"state": "POSTING", "updated_at": NOW - timedelta(seconds=100)}
        assert is_delayed(command, now=NOW, submit_flight_sla_seconds=10.0) is True


# ---------------------------------------------------------------------------
# entry_rest_disposition — the one keep/cancel rule for an open ENTRY rest,
# valued as the order it is (held h, remainder r, the selector's fresh target
# holding H* from the holding before the order's own fills).
# ---------------------------------------------------------------------------

def _dispose(held, remaining, *, target="10", gain=0.01):
    return entry_rest_disposition(
        held_shares=D(held),
        open_remaining=D(remaining),
        target_holding_shares=D(target),
        remainder_gain=gain,
    )


class TestEntryRestDisposition:
    @pytest.mark.parametrize(
        "held,remaining,expected",
        [
            ("0", "10", "KEEP"),       # exactly the selector's fresh target
            ("0", "6", "KEEP"),        # below target: keep working toward it
            ("0", "10.01", "CANCEL"),  # above what the selector would post
            ("4", "6", "KEEP"),        # the same 10-share order, 4 filled
            ("4", "6.01", "CANCEL"),
        ],
    )
    def test_target_boundary(self, held, remaining, expected):
        assert _dispose(held, remaining)[0] == expected

    def test_lot_floor_applies_only_to_a_new_order(self):
        # A 1-share remainder is below a fresh lot but is an order that exists.
        assert _dispose("4", "1") == ("KEEP", "CURRENT_ENTRY_REST_VALUE_POSITIVE")

    def test_above_the_selectors_target_cancels_under_its_own_reason(self):
        assert _dispose("0", "20", target="5") == ("CANCEL", "CURRENT_FRACTIONAL_TARGET_REDUCED")

    def test_there_is_no_resize_action(self):
        actions = {
            _dispose(h, r, gain=g)[0]
            for h in ("0", "2", "4")
            for r in ("1", "5", "10", "60")
            for g in (-1.0, 0.0, 0.01)
        }
        assert actions == {"KEEP", "CANCEL"}

    @pytest.mark.parametrize("gain", [0.0, -1e-9])
    def test_non_positive_remainder_value_cancels(self, gain):
        assert _dispose("0", "10", gain=gain) == ("CANCEL", "CURRENT_MEAN_VALUE_NON_POSITIVE")

    @pytest.mark.parametrize("size", ["5", "10", "15", "21"])
    @pytest.mark.parametrize("target", ["0", "4.9", "10", "18"])
    def test_disposition_is_monotone_in_filled_size(self, size, target):
        # The same order, filled 0..size-0.01, against the same fresh target
        # (computed from the holding before the order's fills): one verdict.
        size_d = D(size)
        steps = [size_d * D(i) / D(20) for i in range(20)]
        verdicts = {
            _dispose(str(h), str(size_d - h), target=target)[0] for h in steps
        }
        assert len(verdicts) == 1, (size, target, verdicts)

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(held_shares=D("0"), open_remaining=D("0"), target_holding_shares=D("10"),
                 remainder_gain=0.1),
            dict(held_shares=D("-1"), open_remaining=D("5"), target_holding_shares=D("10"),
                 remainder_gain=0.1),
            dict(held_shares=D("0"), open_remaining=D("5"), target_holding_shares=D("-1"),
                 remainder_gain=0.1),
            dict(held_shares=D("0"), open_remaining=D("NaN"), target_holding_shares=D("10"),
                 remainder_gain=0.1),
            dict(held_shares=D("0"), open_remaining=D("5"), target_holding_shares=D("10"),
                 remainder_gain=float("nan")),
            dict(held_shares=0, open_remaining=D("5"), target_holding_shares=D("10"),
                 remainder_gain=0.1),
        ],
    )
    def test_invalid_inputs_raise(self, kwargs):
        with pytest.raises(ValueError, match="ENTRY_REST_VALUE_INVALID"):
            entry_rest_disposition(**kwargs)

    def test_rule_has_no_age_or_identity_input(self):
        import inspect

        params = set(inspect.signature(entry_rest_disposition).parameters)
        assert params == {"held_shares", "open_remaining", "target_holding_shares", "remainder_gain"}

    def test_age_and_identity_predicates_are_deleted(self):
        for name in (
            "is_stale_pending_cancel",
            "rest_deadline_exceeded",
            "bootstrap_rest_deadline_minutes",
        ):
            assert not hasattr(predicates, name), name


# ---------------------------------------------------------------------------
# fresh_buy_target_holding — the holding the selector sizes a fresh BUY to.
# Pinned against the selector's own sizer: from any prior holding, the order
# _score_global_single_order_buy_expected chooses never takes the holding
# above it, and whenever it places one the bound is reached up to one quantum.
# ---------------------------------------------------------------------------


class TestFreshBuyTargetHolding:
    def _target(self, prior, full, frac, lot="5", cap="1000"):
        from src.solve.solver import fresh_buy_target_holding

        return fresh_buy_target_holding(
            prior_token_shares=D(prior), full_kelly_target_shares=D(full),
            fractional_kelly_target_shares=D(frac), legal_lot_shares=D(lot),
            max_order_shares=D(cap),
        )

    def test_fractional_target_when_it_admits_a_lot(self):
        assert self._target("0", "80", "10") == D("10")
        assert self._target("4", "80", "10") == D("10")

    def test_small_capital_rule_is_exactly_one_lot(self):
        # The reviewer's band: kT 2.75 < L 5, T 22 -> one lot, never up to T.
        assert self._target("0", "22", "2.75") == D("5")
        assert self._target("1", "22", "2.75") == D("6")

    def test_no_legal_fresh_order_keeps_the_prior_holding(self):
        assert self._target("8", "80", "10") == D("8")      # kT - h < L, kT >= L
        assert self._target("3", "7", "2") == D("3")        # h + L > T: no small-capital lot
        assert self._target("0", "80", "10", cap="4") == D("0")  # envelope below a lot

    def test_capital_envelope_bounds_the_order(self):
        assert self._target("0", "80", "10", cap="7") == D("7")
        assert self._target("0", "22", "2.75", cap="4.99") == D("0")
