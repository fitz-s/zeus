# Created: 2026-06-09
# Last reused/audited: 2026-06-09
# Authority basis: FIX-4 (P2) — separate proof_accepted from live_submit_attempts
#   in the EDLI reactor status pulse. No-submit / degraded cycles must report
#   live_submit_attempts=0 even when proof_accepted > 0.
"""FIX-4 relationship tests: status pulse separates proof_accepted from live_submit_attempts."""
from __future__ import annotations


# ---------------------------------------------------------------------------
# _build_edli_status_pulse helper: unit tests
# ---------------------------------------------------------------------------

def _call_pulse(**overrides):
    from src.main import _build_edli_status_pulse

    defaults = dict(
        started_at="2026-06-09T18:00:00+00:00",
        completed_at="2026-06-09T18:00:01+00:00",
        candidates=5,
        processed=5,
        proof_accepted=3,
        rejected=1,
        retried=1,
        dead_lettered=0,
        rejection_reason_counts={"TRADE_SCORE_BLOCKED": 1},
        submit_disabled_effective_mode=False,
        live_submit_attempts=0,
    )
    defaults.update(overrides)
    return _build_edli_status_pulse(**defaults)


def test_no_submit_cycle_live_submit_attempts_is_zero() -> None:
    """Core FIX-4 invariant: when live_submit_attempts=0 (no-submit cycle),
    the pulse reports submit_attempts=0, not proof_accepted."""
    pulse = _call_pulse(proof_accepted=3, live_submit_attempts=0)

    assert pulse["submit_attempts"] == 0, (
        f"No-submit cycle must report submit_attempts=0, got {pulse['submit_attempts']}"
    )
    # proof_accepted is preserved correctly.
    assert pulse["proof_accepted"] == 3


def test_proof_accepted_does_not_leak_into_submit_attempts() -> None:
    """Even with multiple proof_accepted, submit_attempts only reflects actual venue calls."""
    pulse = _call_pulse(proof_accepted=7, live_submit_attempts=0)

    assert pulse["submit_attempts"] == 0
    assert pulse["proof_accepted"] == 7
    # The two counters must be independent.
    assert pulse["submit_attempts"] != pulse["proof_accepted"]


def test_live_cycle_submit_attempts_reflects_actual_calls() -> None:
    """When live submits actually occur, submit_attempts matches the call count."""
    pulse = _call_pulse(proof_accepted=2, live_submit_attempts=2)

    assert pulse["submit_attempts"] == 2
    assert pulse["proof_accepted"] == 2


def test_live_cycle_partial_submit() -> None:
    """If 3 proofs are accepted but only 1 actually reaches the venue (e.g. gate
    blocked 2 before executor_submit), submit_attempts=1."""
    pulse = _call_pulse(proof_accepted=3, live_submit_attempts=1)

    assert pulse["submit_attempts"] == 1
    assert pulse["proof_accepted"] == 3


def test_deterministic_rejections_reported_in_no_submit_mode() -> None:
    """In submit_disabled_effective_mode with proof_accepted>0, the pulse records
    real_order_submit_disabled in deterministic_rejections."""
    pulse = _call_pulse(
        proof_accepted=4,
        live_submit_attempts=0,
        submit_disabled_effective_mode=True,
    )

    assert pulse["deterministic_rejections"] == {"real_order_submit_disabled": 4}
    assert pulse["submit_attempts"] == 0


def test_no_deterministic_rejections_when_live_submit() -> None:
    """In live mode (submit_disabled_effective_mode=False), deterministic_rejections is empty."""
    pulse = _call_pulse(
        proof_accepted=2,
        live_submit_attempts=2,
        submit_disabled_effective_mode=False,
    )

    assert pulse["deterministic_rejections"] == {}


def test_final_intents_built_equals_proof_accepted() -> None:
    """final_intents_built reflects proof_accepted (intent is built when proof is accepted)."""
    pulse = _call_pulse(proof_accepted=5, live_submit_attempts=0)

    assert pulse["final_intents_built"] == 5


# ---------------------------------------------------------------------------
# _live_submit_count attribute on the live adapter (Fix-4 counter plumbing)
# ---------------------------------------------------------------------------

def test_live_adapter_exposes_live_submit_count_attribute() -> None:
    """The live adapter callable must carry a _live_submit_count attribute (list[int])
    so main.py can read the per-cycle counter after process_pending."""
    import sqlite3
    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel

    submit = adapter.event_bound_live_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
        edli_live_scope="forecast_only",
    )

    count_ref = getattr(submit, "_live_submit_count", None)
    assert count_ref is not None, "_live_submit_count attribute must be present on live adapter"
    assert isinstance(count_ref, list) and len(count_ref) == 1, (
        "_live_submit_count must be a 1-element list"
    )
    assert count_ref[0] == 0, "Counter must start at 0"


def test_no_submit_adapter_missing_count_attribute_gives_zero_default() -> None:
    """The no-submit adapter does NOT carry _live_submit_count.
    The getattr fallback in main.py must yield [0] → live_submit_attempts=0."""
    import sqlite3
    from src.engine import event_reactor_adapter as adapter
    from src.riskguard.risk_level import RiskLevel

    no_submit = adapter.event_bound_no_submit_adapter_from_trade_conn(
        sqlite3.connect(":memory:"),
        get_current_level=lambda: RiskLevel.GREEN,
    )

    count_ref = getattr(no_submit, "_live_submit_count", [0])
    assert count_ref[0] == 0, "No-submit adapter must report 0 via getattr fallback"
