# Created: 2026-07-02
# Last reused or audited: 2026-10-01
# Purpose: Truth-table antibodies for the SCH-W1.2-ORDER-STATE derived predicates
#          (is_delayed, entry_rest_disposition).
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
# valued as the order it is (held h, remainder r, Kelly T / kT, lot L).
# ---------------------------------------------------------------------------

def _dispose(held, remaining, *, full="20", fractional="10", lot="5", gain=0.01):
    return entry_rest_disposition(
        held_shares=D(held),
        open_remaining=D(remaining),
        full_kelly_target_shares=D(full),
        fractional_kelly_target_shares=D(fractional),
        legal_lot_shares=D(lot),
        remainder_gain=gain,
    )


class TestEntryRestDisposition:
    @pytest.mark.parametrize(
        "held,remaining,expected",
        [
            ("0", "10", "KEEP"),      # at the fractional target
            ("0", "6", "KEEP"),       # below target: keep working toward it
            ("0", "14.99", "KEEP"),   # overshoot 4.99 < one lot: no partial cut exists
            ("0", "15", "CANCEL"),    # overshoot of one lot: cancel, fresh redecision
            ("4", "11", "CANCEL"),    # same full order size, partly filled: same verdict
            ("4", "10.99", "KEEP"),
        ],
    )
    def test_lot_boundaries(self, held, remaining, expected):
        assert _dispose(held, remaining)[0] == expected

    def test_lot_floor_applies_only_to_a_new_order(self):
        # A 1-share remainder is below a fresh lot but is an order that exists.
        assert _dispose("4", "1") == ("KEEP", "CURRENT_ENTRY_REST_VALUE_POSITIVE")

    def test_reduction_of_one_lot_cancels_under_its_own_reason(self):
        assert _dispose("0", "15") == ("CANCEL", "CURRENT_FRACTIONAL_TARGET_REDUCED")

    def test_small_capital_rule_keeps_up_to_full_kelly(self):
        # kT < L: the selector admits one lot inside full Kelly T.
        assert _dispose("2", "3", full="8", fractional="1")[0] == "KEEP"
        assert _dispose("2", "6.01", full="8", fractional="1") == (
            "CANCEL", "CURRENT_FULL_KELLY_TARGET_EXCEEDED"
        )

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
    @pytest.mark.parametrize("fractional", ["1", "4.9", "10", "18"])
    def test_disposition_is_monotone_in_filled_size(self, size, fractional):
        # The same order, filled 0..size-0.01: the verdict never flips with fills.
        size_d = D(size)
        steps = [size_d * D(i) / D(20) for i in range(20)]
        verdicts = {
            _dispose(str(h), str(size_d - h), fractional=fractional)[0] for h in steps
        }
        assert len(verdicts) == 1, (size, fractional, verdicts)

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(held_shares=D("0"), open_remaining=D("0"), full_kelly_target_shares=D("20"),
                 fractional_kelly_target_shares=D("10"), legal_lot_shares=D("5"), remainder_gain=0.1),
            dict(held_shares=D("-1"), open_remaining=D("5"), full_kelly_target_shares=D("20"),
                 fractional_kelly_target_shares=D("10"), legal_lot_shares=D("5"), remainder_gain=0.1),
            dict(held_shares=D("0"), open_remaining=D("5"), full_kelly_target_shares=D("20"),
                 fractional_kelly_target_shares=D("10"), legal_lot_shares=D("0"), remainder_gain=0.1),
            dict(held_shares=D("0"), open_remaining=D("NaN"), full_kelly_target_shares=D("20"),
                 fractional_kelly_target_shares=D("10"), legal_lot_shares=D("5"), remainder_gain=0.1),
            dict(held_shares=D("0"), open_remaining=D("5"), full_kelly_target_shares=D("20"),
                 fractional_kelly_target_shares=D("10"), legal_lot_shares=D("5"),
                 remainder_gain=float("nan")),
            dict(held_shares=0, open_remaining=D("5"), full_kelly_target_shares=D("20"),
                 fractional_kelly_target_shares=D("10"), legal_lot_shares=D("5"), remainder_gain=0.1),
        ],
    )
    def test_invalid_inputs_raise(self, kwargs):
        with pytest.raises(ValueError, match="ENTRY_REST_VALUE_INVALID"):
            entry_rest_disposition(**kwargs)

    def test_rule_has_no_age_or_identity_input(self):
        import inspect

        params = set(inspect.signature(entry_rest_disposition).parameters)
        assert params == {
            "held_shares", "open_remaining", "full_kelly_target_shares",
            "fractional_kelly_target_shares", "legal_lot_shares", "remainder_gain",
        }

    def test_age_and_identity_predicates_are_deleted(self):
        for name in (
            "is_stale_pending_cancel",
            "rest_deadline_exceeded",
            "bootstrap_rest_deadline_minutes",
        ):
            assert not hasattr(predicates, name), name
