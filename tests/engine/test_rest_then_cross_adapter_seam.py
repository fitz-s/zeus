# Created: 2026-06-10
# Last reused or audited: 2026-06-20 (lifecycle conversion fix: escalation arm-floor
#   = screen maker-window; fixed stale 120-min-regime test; added screen-pulled
#   RED-on-revert arming test)
# Authority basis: docs/operations/consolidated_systemic_overhaul_2026-06-11.md K4.0
"""K4.0 adapter-seam relationship tests for REST-THEN-CROSS.

Pins the two seams the policy crosses:
1. _family_rest_state: the venue-truth derivation of the antibody input
   (unexpired rest blocks ANY new order) and the escalation license
   (cancelled-unfilled >= deadline -> TAKER_ESCALATED_AFTER_REST lawful).
2. _select_edli_order_mode leg 3: the fresh-mode witness is SUBORDINATED to the
   proof's policy — a REST proof rests regardless of any fresh-book EV
   preference for crossing; only TAKER_* policies witness TAKER. A legacy proof
   (no policy field) witnesses MAKER (fail-closed migration: in-flight legacy
   TAKER proofs abort MODE_FLIPPED once and re-rank under the policy).
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import src.engine.event_reactor_adapter as adapter
from src.events.continuous_redecision import REST_VALUE_REFRESH_MIN_AGE_SECONDS
from src.strategy.live_inference.mode_consistent_ev import (
    MAKER_REST_ESCALATION_DEADLINE_MINUTES,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 10, 22, 0, 0, tzinfo=UTC)
# The live escalation deadline (cut 120 -> 20 on 2026-06-16). The arm FLOOR after
# which a cancelled-unfilled rest licenses the cross is the screen's own
# minimum-maker-window (REST_VALUE_REFRESH_MIN_AGE_SECONDS = 5 min), since the
# continuous-redecision screen cancels most rests at 5-20 min BEFORE the deadline
# job fires (conversion death-line, 2026-06-20).
DEADLINE_MIN = float(MAKER_REST_ESCALATION_DEADLINE_MINUTES)
ARM_FLOOR_MIN = min(DEADLINE_MIN, float(REST_VALUE_REFRESH_MIN_AGE_SECONDS) / 60.0)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY, intent_kind TEXT, market_id TEXT,
            token_id TEXT, side TEXT, size REAL, price REAL,
            venue_order_id TEXT, state TEXT, created_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY, venue_order_id TEXT, command_id TEXT,
            state TEXT, remaining_size TEXT, matched_size TEXT,
            observed_at TEXT, local_sequence INTEGER)"""
    )
    return conn


def _family(token="tok_yes", no_token="tok_no"):
    candidate = SimpleNamespace(yes_token_id=token, no_token_id=no_token)
    return SimpleNamespace(
        candidates=(candidate,), city="TestCity", target_date="2026-06-12"
    )


def _add(
    conn,
    *,
    command_id="c1",
    token_id="tok_yes",
    command_state="ACKED",
    venue_order_id="o1",
    created_at=NOW - timedelta(minutes=30),
    facts=(),
):
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            command_id,
            "ENTRY",
            "m1",
            token_id,
            "BUY",
            10.0,
            0.5,
            venue_order_id,
            command_state,
            created_at.isoformat(),
        ),
    )
    for i, (state, matched, observed_at) in enumerate(facts):
        conn.execute(
            "INSERT INTO venue_order_facts VALUES (NULL,?,?,?,?,?,?,?)",
            (venue_order_id, command_id, state, "10", matched, observed_at.isoformat(), i),
        )


class TestFamilyRestState:
    def test_open_rest_blocks(self):
        conn = _db()
        _add(conn, facts=[("LIVE", "0", NOW - timedelta(minutes=29))])
        assert adapter._family_rest_state(conn, family=_family(), decision_time=NOW) == (
            True,
            False,
        )

    def test_acked_without_facts_blocks(self):
        """Order acknowledged but no venue fact yet: treated as an open rest."""
        conn = _db()
        _add(conn, command_state="ACKED", facts=[])
        assert adapter._family_rest_state(conn, family=_family(), decision_time=NOW) == (
            True,
            False,
        )

    def test_cancelled_unfilled_past_deadline_escalates(self):
        conn = _db()
        created = NOW - timedelta(minutes=DEADLINE_MIN + 60)
        cancelled_at = created + timedelta(minutes=DEADLINE_MIN + 5)
        _add(
            conn,
            created_at=created,
            command_state="CANCELLED",
            facts=[
                ("LIVE", "0", created + timedelta(seconds=5)),
                ("CANCEL_CONFIRMED", "0", cancelled_at),
            ],
        )
        assert adapter._family_rest_state(conn, family=_family(), decision_time=NOW) == (
            False,
            True,
        )

    def test_cancelled_unfilled_below_arm_floor_does_not_escalate(self):
        """A rest cancelled UNFILLED before the maker-window arm floor (5 min) did
        not have a real maker window -> no escalation license. Below-floor fails
        toward REST (the Karachi rest-first antibody)."""
        conn = _db()
        created = NOW - timedelta(minutes=90)
        # Cancelled 1 min after posting: below the 5-min arm floor.
        _add(
            conn,
            created_at=created,
            command_state="CANCELLED",
            facts=[("CANCEL_CONFIRMED", "0", created + timedelta(minutes=1))],
        )
        assert adapter._family_rest_state(conn, family=_family(), decision_time=NOW) == (
            False,
            False,
        )

    def test_screen_pulled_unfilled_rest_between_floor_and_deadline_escalates(self):
        """RED-ON-REVERT (conversion death-line, 2026-06-20). The live break: the
        continuous-redecision SCREEN cancels most rests at 5-20 min (CONFIRMED_VALUE
        _REFRESH @5min / BOOK_MOVED @1 tick) BEFORE the 20-min deadline job — 60 of
        64 terminal-unfilled rests on 06-19/06-20 died in this window. On the UNFIXED
        tree such a rest (aged >= 5 min but < 20 min, cancelled UNFILLED) returns
        escalated=False, so the family re-posts a fresh REST_DEFAULT and the screen
        pulls it again -> infinite re-rest loop, 0 crosses. After the fix it returns
        escalated=True, so the next decision can CROSS (still capped by FIX B's
        conservative q_lcb bound) instead of re-resting the identical unfillable rest.
        """
        # 12 min: a genuine maker window (>= 5-min floor) but < the 20-min deadline.
        assert ARM_FLOOR_MIN <= 12.0 < DEADLINE_MIN
        conn = _db()
        created = NOW - timedelta(minutes=30)
        cancelled_at = created + timedelta(minutes=12)
        _add(
            conn,
            created_at=created,
            command_state="CANCELLED",
            facts=[
                ("LIVE", "0", created + timedelta(seconds=5)),
                ("CANCEL_CONFIRMED", "0", cancelled_at),
            ],
        )
        assert adapter._family_rest_state(conn, family=_family(), decision_time=NOW) == (
            False,
            True,
        )

    def test_filled_order_neither_blocks_nor_escalates(self):
        conn = _db()
        created = NOW - timedelta(minutes=DEADLINE_MIN + 60)
        _add(
            conn,
            created_at=created,
            command_state="FILLED",
            facts=[("MATCHED", "10", created + timedelta(minutes=200))],
        )
        assert adapter._family_rest_state(conn, family=_family(), decision_time=NOW) == (
            False,
            False,
        )

    def test_other_family_tokens_invisible(self):
        conn = _db()
        _add(conn, token_id="someone_elses_token", facts=[("LIVE", "0", NOW)])
        assert adapter._family_rest_state(conn, family=_family(), decision_time=NOW) == (
            False,
            False,
        )

    def test_no_conn_fails_toward_rest(self):
        assert adapter._family_rest_state(None, family=_family(), decision_time=NOW) == (
            False,
            False,
        )

    def test_sibling_no_token_rest_blocks_the_whole_family(self):
        """The antibody is FAMILY-scoped: a rest on the NO token blocks new
        orders for the YES token too (same family)."""
        conn = _db()
        _add(conn, token_id="tok_no", facts=[("RESTING", "0", NOW)])
        assert adapter._family_rest_state(conn, family=_family(), decision_time=NOW) == (
            True,
            False,
        )


class TestFreshSeamSubordination:
    def _select(self, policy):
        payload = {
            "direction": "buy_no",
            "c_fee_adjusted": 0.66,
            "trade_score": 0.05,
            "p_fill_lcb": 0.9,
        }
        if policy is not None:
            payload["rest_then_cross_policy"] = policy
        return adapter._select_edli_order_mode(
            actionable_payload=payload,
            quote_payload={},
            best_bid=0.58,
            best_ask=0.66,
            executable_snapshot=SimpleNamespace(payload={}),
            fresh_best_bid=0.58,
            fresh_best_ask=0.66,
        )

    def test_rest_policy_witnesses_maker(self):
        assert self._select("REST_DEFAULT") == "MAKER"

    def test_hold_policy_witnesses_maker(self):
        assert self._select("HOLD_REST_IN_PROGRESS") == "MAKER"

    def test_taker_policy_witnesses_taker(self):
        assert self._select("TAKER_FLEETING_EDGE") == "TAKER"
        assert self._select("TAKER_ESCALATED_AFTER_REST") == "TAKER"
        assert self._select("TAKER_EVENT_END_NEAR") == "TAKER"

    def test_legacy_proof_without_policy_witnesses_maker(self):
        assert self._select(None) == "MAKER"

    def test_blown_out_fresh_spread_still_forces_maker_even_for_taker_policy(self):
        """Leg 0 (spread guard) dominates the policy lane: a fresh book whose
        relative spread breaches the guard witnesses MAKER -> the validator
        aborts the TAKER proof MODE_FLIPPED (fail-closed, never a wide cross)."""
        payload = {
            "direction": "buy_no",
            "c_fee_adjusted": 0.40,
            "trade_score": 0.20,
            "p_fill_lcb": 0.9,
            "rest_then_cross_policy": "TAKER_FLEETING_EDGE",
        }
        mode = adapter._select_edli_order_mode(
            actionable_payload=payload,
            quote_payload={},
            best_bid=0.10,
            best_ask=0.40,
            executable_snapshot=SimpleNamespace(payload={}),
            fresh_best_bid=0.10,
            fresh_best_ask=0.40,
        )
        assert mode == "MAKER"


class TestMinutesToEventEnd:
    def test_unknown_city_returns_none_conservative(self):
        family = SimpleNamespace(
            candidates=(), city="NoSuchCityXYZ", target_date="2026-06-12"
        )
        assert adapter._minutes_to_family_event_end(family, NOW) is None

    def test_known_timezone_computes_local_day_end(self, monkeypatch):
        monkeypatch.setattr(
            adapter,
            "runtime_cities_by_name",
            lambda: {"TestCity": SimpleNamespace(timezone="UTC")},
        )
        family = SimpleNamespace(candidates=(), city="TestCity", target_date="2026-06-10")
        # End of 2026-06-10 UTC = 2026-06-11T00:00Z; NOW is 22:00Z -> 120 minutes.
        minutes = adapter._minutes_to_family_event_end(family, NOW)
        assert minutes is not None and abs(minutes - 120.0) < 0.01
