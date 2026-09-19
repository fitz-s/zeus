"""An entry event must carry the decision that opened its position.

`build_entry_fill_only_canonical_write` took `decision_id` as an optional
parameter, and NEITHER live caller passed it (fill_tracker.py:223,
exchange_reconcile.py:7136). The resulting ENTRY_ORDER_FILLED row had a NULL
decision_id while its POSITION_OPEN_INTENT and ENTRY_ORDER_POSTED siblings
carried the full identity.

`load_held_entry_calibration` counts entry events whose decision_id is not a
non-empty TEXT and refuses with ENTRY_PROVENANCE_AMBIGUOUS on any such row
(market_anchored_live_fit.py:3558-3567). That exception is swallowed by
`Position._exit_q_mean_and_source` into `entry_calibration_unavailable`, so the
position reports INCOMPLETE_EXIT_CONTEXT and can never produce an exit decision
again. On 2026-09-18 this held BOTH live positions -- including a -$19.44 loser
with FLASH_CRASH_PANIC raised and zero exit commands ever created -- and 319 of
1,575 historical fill events (20.3%) carry the same NULL.
"""

from __future__ import annotations

from src.engine.lifecycle_events import (
    build_entry_canonical_write,
    build_entry_fill_only_canonical_write,
)
from src.state.portfolio import Position

_DECISION = "edli_exec_cmd:evt:edli_intent:evt:tok:tok:buy_no"


def _position(decision_id: str | None = _DECISION) -> Position:
    """The production Position, so this pins the real shape, not a stub."""

    return Position(
        trade_id="pos-decision-identity",
        market_id="m-decision-identity",
        city="Hong Kong",
        cluster="Asia",
        target_date="2026-09-19",
        bin_label="25-26°C",
        direction="buy_no",
        unit="C",
        env="live",
        size_usd=21.6,
        entry_price=0.30,
        p_posterior=0.78,
        decision_snapshot_id="snap-decision-identity",
        strategy_key="day0",
        strategy="day0",
        edge_source="day0",
        order_id="0xabc",
        entry_order_id="0xabc",
        order_status="FILLED",
        order_posted_at="2026-09-18T15:13:29Z",
        entered_at="2026-09-18T18:23:05Z",
        state="active",
        chain_state="synced",
        token_id="t1",
        condition_id="cond-decision-identity",
        decision_id=decision_id,
    )


def _fill_event(position, **kwargs):
    events, _projection = build_entry_fill_only_canonical_write(
        position, sequence_no=3, **kwargs
    )
    return events[0]


class TestFillEventIdentity:
    def test_the_fill_event_carries_the_positions_decision(self):
        assert _fill_event(_position())["decision_id"] == _DECISION

    def test_no_caller_needs_to_remember_to_pass_it(self):
        """Both live callers omit the argument; that must still be correct."""

        event = _fill_event(_position())
        assert event["decision_id"], (
            "a NULL decision_id makes entry provenance ambiguous forever and "
            "permanently denies the position its exit authority"
        )

    def test_an_explicit_decision_id_still_wins(self):
        event = _fill_event(_position(), decision_id="explicit-override")
        assert event["decision_id"] == "explicit-override"

    def test_a_blank_explicit_value_falls_back_to_the_position(self):
        assert _fill_event(_position(), decision_id="")["decision_id"] == _DECISION
        assert _fill_event(_position(), decision_id="   ")["decision_id"] == _DECISION

    def test_a_position_without_one_still_yields_null_not_a_crash(self):
        """Absence stays absence; this fix supplies truth, it never invents it."""

        assert _fill_event(_position(None))["decision_id"] is None
        assert _fill_event(_position(""))["decision_id"] is None

    def test_the_fill_event_matches_its_posted_sibling(self):
        """The three entry events must agree, which is what the gate checks."""

        position = _position()
        from src.state.lifecycle_manager import LifecyclePhase

        posted_events, _ = build_entry_canonical_write(
            position,
            phase_after=LifecyclePhase.PENDING_ENTRY.value,
            decision_id=_DECISION,
        )
        posted = [
            event
            for event in posted_events
            if event["event_type"] == "ENTRY_ORDER_POSTED"
        ][0]

        assert _fill_event(position)["decision_id"] == posted["decision_id"]
