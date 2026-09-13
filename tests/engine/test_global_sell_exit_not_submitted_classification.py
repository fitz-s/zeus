# Created: 2026-09-13
# Last reused or audited: 2026-09-13
# Lifecycle: created=2026-09-13; last_reviewed=2026-09-13
# Authority basis: three rounds of review on the GLOBAL_SELL_EXIT_REJECTED /
#   GLOBAL_SELL_EXIT_BLOCKED split each found a sibling execute_exit early-out
#   this classification mis-handled while the logic lived inline in
#   event_reactor_adapter.py's SELL-exit receipt builder and was untestable in
#   isolation: round 2 found exit_lifecycle.py:8663's post-observe duplicate
#   of the pre-venue "unsafe_open_exit_cancel_pending" early-out; round 3
#   found _adopt_active_exit_sell's post-observe "sell_pending:
#   active_prior_exit_sell ..." forward-progress return six lines below it.
#   The logic was extracted into
#   _global_sell_exit_not_submitted_reason_prefix specifically so every
#   execute_exit / _execute_live_exit early-out string can be pinned directly
#   against it, without driving the full SELL-actuation integration harness.
"""Exhaustive coverage of the not-submitted SELL-exit classification.

TERMINAL (GLOBAL_SELL_EXIT_REJECTED) is a whitelist: only an outcome whose
text is one of the two known genuine-rejection shapes
(_EXIT_LIFECYCLE_ADJUDICATED_REJECTION_PREFIXES) AND that was reached after a
real venue call started counts as a venue adjudication of THIS attempt. Every
other not-submitted outcome -- known retry/redecision vocabulary, an
active-order adoption, or any unrecognized future string -- must classify
GLOBAL_SELL_EXIT_BLOCKED (TRANSIENT): replaying a block is cheap; dead-
lettering forward progress or a retryable block loses the exit.
"""
from __future__ import annotations

import pytest

from src.engine.event_reactor_adapter import (
    _global_sell_exit_not_submitted_reason_prefix,
)

# Every execute_exit / _execute_live_exit (src/execution/exit_lifecycle.py)
# early-out return string that reaches the not-submitted classification
# branch, paired with whether execution_evidence.observe() had already run
# (venue_call_started) at that return site. Rows are (outcome_text,
# venue_call_started, expected_prefix, producer file:line).
_PRE_OBSERVE_BLOCKED_CASES = (
    ("exit_deferred: red_handoff_required", "exit_lifecycle.py:7707"),
    (
        "exit_blocked: TOKEN_AGGREGATE_BLOCKED_PENDING_RESOLUTION",
        "exit_lifecycle.py:7759",
    ),
    ("exit_blocked: market_closed_hold_to_settlement", "exit_lifecycle.py:7772/7786"),
    ("exit_blocked: incomplete_context", "exit_lifecycle.py:7776"),
    ("exit_blocked: stale_market_price", "exit_lifecycle.py:7790"),
    (
        "sell_blocked_dust: existing_canonical_dust_hold:trade-1",
        "exit_lifecycle.py:7847",
    ),
    ("exit_blocked: no_token_id", "exit_lifecycle.py:7862"),
    (
        "exit_blocked: unsafe_open_exit_cancel_pending",
        "exit_lifecycle.py:7873 (pre-venue twin)",
    ),
    (
        "sell_pending: active_prior_exit_sell command_id=c1 order=pending_ack state=LIVE",
        "exit_lifecycle.py:7874 (pre-venue _adopt_active_exit_sell)",
    ),
    ("exit_blocked: proof_missing", "exit_lifecycle.py:7967"),
    ("exit_blocked: global_sell_order_type_mismatch", "exit_lifecycle.py:7975"),
    ("exit_blocked: closure_failed", "exit_lifecycle.py:7985"),
    ("exit_blocked: exit_intent_persistence_failed", "exit_lifecycle.py:7993"),
    ("exit_blocked: executable_snapshot_error", "exit_lifecycle.py:8049"),
    ("exit_blocked: authority_missing", "exit_lifecycle.py:8182"),
    ("exit_blocked: residual_missing", "exit_lifecycle.py:8213"),
    ("sell_blocked_dust: dust_below_min", "exit_lifecycle.py:8244"),
    ("exit_blocked: executable_snapshot_unavailable", "exit_lifecycle.py:8273"),
    ("exit_blocked: liquidity_insufficient", "exit_lifecycle.py:8331"),
    ("exit_blocked: cancel_unavailable", "exit_lifecycle.py:8364"),
    ("exit_blocked: cancel_unknown", "exit_lifecycle.py:8402/8468"),
    ("exit_blocked: cancel_pending", "exit_lifecycle.py:8419/8459/8471"),
    (
        "exit_retry: adopted_order_cancelled",
        "exit_lifecycle.py:8442 (not exit_blocked:/exit_deferred: vocabulary at all)",
    ),
    (
        "exit_blocked: fresh_capital_authority_required_after_cancel",
        "exit_lifecycle.py:8483",
    ),
    ("exit_deferred: red_b2_attestation_invalid", "exit_lifecycle.py:8620"),
    ("exit_redecision_required: red_force_exit_cleared", "exit_lifecycle.py:8624"),
    ("exit_deferred: red_handoff_release_failed", "exit_lifecycle.py:8625"),
)

# Rows reached AFTER execution_evidence.observe() ran (exit_lifecycle.py:8627)
# against a real, definite non-ack sell_result -- venue_call_started is True.
_POST_OBSERVE_BLOCKED_CASES = (
    (
        "exit_blocked: unsafe_open_exit_cancel_pending",
        "exit_lifecycle.py:8663 (post-observe duplicate; round 2 defect)",
    ),
    (
        "sell_pending: active_prior_exit_sell command_id=c2 order=o2 state=REJECTED",
        "exit_lifecycle.py:8664-8669 (post-observe _adopt_active_exit_sell; round 3 defect)",
    ),
)

# The only two producer prefixes that represent a genuine venue adjudication
# of this attempt, always reached post-observe.
_POST_OBSERVE_REJECTED_CASES = (
    ("sell_blocked_dust: order_size_below_minimum", "exit_lifecycle.py:8687"),
    ("sell_error: invalid order size", "exit_lifecycle.py:8725"),
)


@pytest.mark.parametrize(
    "outcome_text,producer",
    _PRE_OBSERVE_BLOCKED_CASES,
    ids=[case[1] for case in _PRE_OBSERVE_BLOCKED_CASES],
)
def test_pre_observe_early_outs_are_blocked_regardless_of_producer(
    outcome_text, producer
):
    assert (
        _global_sell_exit_not_submitted_reason_prefix(
            outcome_text, venue_call_started=False
        )
        == "GLOBAL_SELL_EXIT_BLOCKED"
    ), producer


@pytest.mark.parametrize(
    "outcome_text,producer",
    _POST_OBSERVE_BLOCKED_CASES,
    ids=[case[1] for case in _POST_OBSERVE_BLOCKED_CASES],
)
def test_post_observe_non_adjudicating_early_outs_are_blocked(outcome_text, producer):
    """venue_call_started=True alone must never be sufficient for TERMINAL --
    both of these strings are reached with a real, observed venue rejection
    already recorded, yet neither is an adjudication of this exact attempt.
    """
    assert (
        _global_sell_exit_not_submitted_reason_prefix(
            outcome_text, venue_call_started=True
        )
        == "GLOBAL_SELL_EXIT_BLOCKED"
    ), producer


@pytest.mark.parametrize(
    "outcome_text,producer",
    _POST_OBSERVE_REJECTED_CASES,
    ids=[case[1] for case in _POST_OBSERVE_REJECTED_CASES],
)
def test_post_observe_genuine_rejections_stay_terminal(outcome_text, producer):
    assert (
        _global_sell_exit_not_submitted_reason_prefix(
            outcome_text, venue_call_started=True
        )
        == "GLOBAL_SELL_EXIT_REJECTED"
    ), producer


def test_unknown_future_string_with_venue_call_started_fails_open_transient():
    """The fail-open direction for an unrecognized outcome must be TRANSIENT:
    replaying a block is cheap, dead-lettering forward progress or a
    retryable block loses the exit.
    """
    assert (
        _global_sell_exit_not_submitted_reason_prefix(
            "totally_novel_future_outcome: whatever",
            venue_call_started=True,
        )
        == "GLOBAL_SELL_EXIT_BLOCKED"
    )


def test_unknown_future_string_without_venue_call_started_is_blocked():
    assert (
        _global_sell_exit_not_submitted_reason_prefix(
            "totally_novel_future_outcome: whatever",
            venue_call_started=False,
        )
        == "GLOBAL_SELL_EXIT_BLOCKED"
    )


def test_adjudicated_rejection_prefix_requires_venue_call_started():
    """A genuine-rejection-shaped string reached WITHOUT a venue call (e.g. a
    hypothetical future refactor that emits it pre-venue) must not classify
    TERMINAL merely by text match -- the flag is a required, not incidental,
    gate.
    """
    assert (
        _global_sell_exit_not_submitted_reason_prefix(
            "sell_error: hypothetical_pre_venue_emission",
            venue_call_started=False,
        )
        == "GLOBAL_SELL_EXIT_BLOCKED"
    )
