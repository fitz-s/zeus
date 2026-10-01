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
# entry_rest_disposition — the one keep/resize/cancel rule for an open ENTRY rest
# ---------------------------------------------------------------------------

def _dispose(remaining, target, gain=0.01, minimum="5"):
    return entry_rest_disposition(
        open_remaining=D(remaining),
        target_remaining=D(target),
        minimum_order_size=D(minimum),
        conditional_gain=gain,
    )


class TestEntryRestDisposition:
    @pytest.mark.parametrize(
        "remaining,target,expected",
        [
            ("10", "10", "KEEP"),       # at target
            ("10", "12", "KEEP"),       # target above remainder: keep working
            ("10", "5.01", "KEEP"),     # reduction 4.99 < one lot
            ("10", "5", "RESIZE"),      # reduction exactly one lot
            ("10.01", "5", "RESIZE"),   # reduction above one lot
            ("10", "4.99", "CANCEL"),   # target below one legal lot
            ("10", "0", "CANCEL"),
        ],
    )
    def test_lot_boundaries(self, remaining, target, expected):
        assert _dispose(remaining, target)[0] == expected

    @pytest.mark.parametrize("gain", [0.0, -1e-9])
    def test_non_positive_conditional_value_cancels(self, gain):
        assert _dispose("10", "10", gain=gain) == ("CANCEL", "CURRENT_MEAN_VALUE_NON_POSITIVE")

    def test_target_below_lot_wins_over_value(self):
        assert _dispose("10", "1", gain=-1.0)[1] == "FRACTIONAL_KELLY_TARGET_BELOW_MINIMUM_LOT"

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(open_remaining=D("0"), target_remaining=D("5"), minimum_order_size=D("5"), conditional_gain=0.1),
            dict(open_remaining=D("5"), target_remaining=D("-1"), minimum_order_size=D("5"), conditional_gain=0.1),
            dict(open_remaining=D("5"), target_remaining=D("5"), minimum_order_size=D("0"), conditional_gain=0.1),
            dict(open_remaining=D("NaN"), target_remaining=D("5"), minimum_order_size=D("5"), conditional_gain=0.1),
            dict(open_remaining=D("5"), target_remaining=D("5"), minimum_order_size=D("5"), conditional_gain=float("nan")),
            dict(open_remaining=5, target_remaining=D("5"), minimum_order_size=D("5"), conditional_gain=0.1),
        ],
    )
    def test_invalid_inputs_raise(self, kwargs):
        with pytest.raises(ValueError, match="ENTRY_REST_VALUE_INVALID"):
            entry_rest_disposition(**kwargs)

    def test_rule_has_no_age_or_identity_input(self):
        import inspect

        params = set(inspect.signature(entry_rest_disposition).parameters)
        assert params == {
            "open_remaining", "target_remaining", "minimum_order_size", "conditional_gain",
        }

    def test_age_and_identity_predicates_are_deleted(self):
        for name in (
            "is_stale_pending_cancel",
            "rest_deadline_exceeded",
            "bootstrap_rest_deadline_minutes",
        ):
            assert not hasattr(predicates, name), name
