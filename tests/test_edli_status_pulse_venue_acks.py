# Created: 2026-06-09
# Last reused/audited: 2026-06-09
# Authority basis: FIX-4 venue_acks wiring — _live_ack_count on live adapter;
#   mirrors test_edli_status_pulse_submit_counter.py pattern.
"""Antibody: venue_acks counter in the EDLI status pulse.

venue_acks was hardcoded to 0 in _build_edli_status_pulse.  The fix wires
_live_ack_count (a mutable 1-element list exposed on the live adapter callable,
incremented when submit_result.venue_ack_received is True) into the pulse,
mirroring the existing _live_submit_count / live_submit_attempts pattern.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# _build_edli_status_pulse: venue_acks unit tests
# ---------------------------------------------------------------------------


def _call_pulse(**overrides):
    from src.events.reactor import _build_edli_status_pulse

    defaults = dict(
        started_at="2026-06-09T18:00:00+00:00",
        completed_at="2026-06-09T18:00:01+00:00",
        candidates=5,
        processed=5,
        proof_accepted=3,
        rejected=1,
        retried=1,
        dead_lettered=0,
        rejection_reason_counts={},
        submit_disabled_effective_mode=False,
        live_submit_attempts=0,
        live_venue_acks=0,
    )
    defaults.update(overrides)
    return _build_edli_status_pulse(**defaults)


def test_venue_acks_zero_in_no_submit_cycle() -> None:
    """No-submit cycle: venue_acks must be 0."""
    pulse = _call_pulse(live_submit_attempts=0, live_venue_acks=0)

    assert pulse["venue_acks"] == 0


def test_venue_acks_reflects_actual_ack_count() -> None:
    """When venue ACKs are received, venue_acks matches the count."""
    pulse = _call_pulse(live_submit_attempts=2, live_venue_acks=2)

    assert pulse["venue_acks"] == 2
    assert pulse["submit_attempts"] == 2


def test_venue_acks_can_be_less_than_submit_attempts() -> None:
    """Partial ACK: 3 submits attempted but only 2 received venue ACK."""
    pulse = _call_pulse(live_submit_attempts=3, live_venue_acks=2)

    assert pulse["submit_attempts"] == 3
    assert pulse["venue_acks"] == 2
    assert pulse["venue_acks"] <= pulse["submit_attempts"]


def test_venue_acks_default_is_zero() -> None:
    """live_venue_acks defaults to 0 (backward-compat for existing callers
    that do not pass the new parameter)."""
    from src.events.reactor import _build_edli_status_pulse

    pulse = _build_edli_status_pulse(
        started_at="2026-06-09T18:00:00+00:00",
        completed_at="2026-06-09T18:00:01+00:00",
        candidates=1,
        processed=1,
        proof_accepted=1,
        rejected=0,
        retried=0,
        dead_lettered=0,
        rejection_reason_counts={},
        submit_disabled_effective_mode=False,
        live_submit_attempts=1,
        # live_venue_acks intentionally omitted → must default to 0
    )
    assert pulse["venue_acks"] == 0


# ---------------------------------------------------------------------------
# _live_ack_count attribute on the live adapter
# ---------------------------------------------------------------------------


def test_live_adapter_exposes_live_ack_count_attribute() -> None:
    """The live adapter callable must carry a _live_ack_count attribute
    (list[int]) so main.py can read per-cycle venue ACK count."""
    import sqlite3
    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        edli_live_scope="forecast_only",
    )

    count_ref = getattr(submit, "_live_ack_count", None)
    assert count_ref is not None, "_live_ack_count attribute must be present on live adapter"
    assert isinstance(count_ref, list) and len(count_ref) == 1, (
        "_live_ack_count must be a 1-element list"
    )
    assert count_ref[0] == 0, "ACK counter must start at 0"


def test_no_submit_adapter_missing_ack_attribute_gives_zero_default() -> None:
    """The no-submit adapter does not carry _live_ack_count.
    getattr fallback in main.py must yield [0] → live_venue_acks=0."""
    import sqlite3
    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel

    no_submit = adapter.event_bound_no_submit_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
    )

    count_ref = getattr(no_submit, "_live_ack_count", [0])
    assert count_ref[0] == 0, "No-submit adapter must report 0 via getattr fallback"


def test_venue_acks_in_pulse_is_independent_of_proof_accepted() -> None:
    """venue_acks and proof_accepted are independent counters.

    A proof-accepted event that did not reach the venue (no-submit mode)
    must NOT inflate venue_acks.
    """
    pulse = _call_pulse(proof_accepted=5, live_submit_attempts=0, live_venue_acks=0)

    assert pulse["venue_acks"] == 0
    assert pulse["proof_accepted"] == 5
