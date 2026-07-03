# Created: 2026-07-02
# Last reused or audited: 2026-07-02
# Purpose: Truth-table antibodies for the SCH-W1.2-ORDER-STATE derived predicates
#          (is_stale_pending_cancel, is_delayed, rest_deadline_exceeded).
# Reuse: Run when changing src/state/order_state_predicates.py or W4 wires these
#        predicates to the cancel path / REST_ELIGIBLE surface.
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
"""Tests for src/state/order_state_predicates.py (SCH-W1.2-ORDER-STATE)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.state.order_state_predicates import (
    bootstrap_rest_deadline_minutes,
    is_delayed,
    is_stale_pending_cancel,
    rest_deadline_exceeded,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# is_stale_pending_cancel — full truth table (packet tests_required)
# ---------------------------------------------------------------------------

class TestIsStalePendingCancel:
    def test_stamped_differs_and_open_is_true(self):
        assert is_stale_pending_cancel("q-old", "q-new", True) is True

    def test_stamped_equals_current_is_false(self):
        assert is_stale_pending_cancel("q-current", "q-current", True) is False

    def test_null_stamp_is_indeterminate_never_true(self):
        result = is_stale_pending_cancel(None, "q-current", True)
        assert result is None

    def test_family_no_servable_q_is_indeterminate(self):
        # Family readiness BLOCKED: no current servable q at all.
        result = is_stale_pending_cancel("q-old", None, True)
        assert result is None

    def test_both_null_is_indeterminate(self):
        assert is_stale_pending_cancel(None, None, True) is None

    def test_closed_order_is_false_regardless_of_q_mismatch(self):
        # Not open -> nothing to cancel; never stale-pending-cancel.
        assert is_stale_pending_cancel("q-old", "q-new", False) is False

    def test_closed_order_with_null_stamp_is_false_not_indeterminate(self):
        assert is_stale_pending_cancel(None, "q-current", False) is False

    def test_indeterminate_is_never_true_fail_closed(self):
        # Explicit fail-closed contract: neither INDETERMINATE branch ever
        # returns True, so a caller that only checks `is True` for cancel
        # eligibility cannot accidentally churn cancels on missing evidence.
        assert is_stale_pending_cancel(None, "q-current", True) is not True
        assert is_stale_pending_cancel("q-old", None, True) is not True


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
# rest_deadline_exceeded — applies to ALL open rests regardless of q_version
# ---------------------------------------------------------------------------

class TestRestDeadlineExceeded:
    def test_open_rest_past_deadline_is_true(self):
        resting_since = NOW - timedelta(minutes=25)
        assert rest_deadline_exceeded(
            order_open=True, resting_since=resting_since, now=NOW, deadline_minutes=20.0
        ) is True

    def test_open_rest_within_deadline_is_false(self):
        resting_since = NOW - timedelta(minutes=5)
        assert rest_deadline_exceeded(
            order_open=True, resting_since=resting_since, now=NOW, deadline_minutes=20.0
        ) is False

    def test_closed_order_is_false_regardless_of_age(self):
        resting_since = NOW - timedelta(days=1)
        assert rest_deadline_exceeded(
            order_open=False, resting_since=resting_since, now=NOW, deadline_minutes=20.0
        ) is False

    def test_null_q_rest_past_deadline_is_true_the_leak_closure_case(self):
        # Critic ruling 2: this predicate, not q-staleness, retires NULL-q rests.
        # The predicate takes no q_version input at all — it must fire on age alone.
        resting_since = NOW - timedelta(minutes=45)
        assert rest_deadline_exceeded(
            order_open=True, resting_since=resting_since, now=NOW, deadline_minutes=20.0
        ) is True

    def test_accepts_iso_string_for_resting_since(self):
        resting_since = (NOW - timedelta(minutes=25)).isoformat()
        assert rest_deadline_exceeded(
            order_open=True, resting_since=resting_since, now=NOW, deadline_minutes=20.0
        ) is True

    def test_exactly_at_deadline_is_true(self):
        resting_since = NOW - timedelta(minutes=20)
        assert rest_deadline_exceeded(
            order_open=True, resting_since=resting_since, now=NOW, deadline_minutes=20.0
        ) is True


class TestBootstrapRestDeadlineMinutes:
    def test_bootstraps_from_incumbent_maker_rest_escalation_deadline(self):
        from src.strategy.live_inference.mode_consistent_ev import (
            MAKER_REST_ESCALATION_DEADLINE_MINUTES,
        )

        assert bootstrap_rest_deadline_minutes() == float(MAKER_REST_ESCALATION_DEADLINE_MINUTES)
