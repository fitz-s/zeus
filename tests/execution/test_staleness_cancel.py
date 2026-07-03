# Created: 2026-07-03
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
#   (SCH-W1.2-ORDER-STATE) + docs/operations/current/plans/order_engine_rebuild_execution_plan_2026-07-02.md
#   W4 row (C3 staleness path, same packet: DELETE maker_rest_escalation).
"""W4.2 C3 staleness cancel path: classification, family resolution, and the
scan -> classify -> cancel -> confirm -> reconciled re-solve orchestration."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.execution.staleness_cancel import (
    classify_cancel_set,
    find_open_entry_rests,
    read_current_family_q_versions,
    resolve_order_families,
    run_c3_staleness_cancel_cycle,
)
from src.state.order_state_predicates import bootstrap_rest_deadline_minutes

UTC = timezone.utc
NOW = datetime(2026, 7, 3, 22, 0, 0, tzinfo=UTC)
DEADLINE_MIN = bootstrap_rest_deadline_minutes()
FAMILY = ("Miami", "2026-07-04", "high")


def _entry(command_id: str, *, q_version, age_minutes: float, family=FAMILY) -> dict:
    return {
        "command_id": command_id,
        "venue_order_id": f"vord-{command_id}",
        "token_id": f"tok-{command_id}",
        "market_id": "mkt-1",
        "created_at": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "q_version": q_version,
        "fact_state": "LIVE",
        "matched_size": "0",
    }


# ---------------------------------------------------------------------------
# classify_cancel_set: pure predicate wiring
# ---------------------------------------------------------------------------


class TestNoOrphanedGtcHandoverProof:
    """rest_deadline_exceeded is the sole GTC TTL owner: every open rest older
    than the deadline lands in the cancel-set REGARDLESS of q_version — the
    same unconditional per-order backstop maker_rest_escalation used to own.
    """

    @pytest.mark.parametrize(
        "q_version,current_q,label",
        [
            (None, None, "null_stamp_blind_family"),
            (None, "q-fresh", "null_stamp_known_family"),
            ("q-fresh", "q-fresh", "matching_q_not_stale"),
            ("q-old", "q-fresh", "stale_q"),
            ("q-old", None, "blind_family_indeterminate"),
        ],
    )
    def test_aged_past_deadline_always_cancelled(self, q_version, current_q, label):
        entry = _entry("c1", q_version=q_version, age_minutes=DEADLINE_MIN + 5)
        families_by_command = {"c1": FAMILY}
        q_by_family = {FAMILY: current_q}

        cancel_set = classify_cancel_set(
            [entry], families_by_command, q_by_family, now=NOW, deadline_minutes=DEADLINE_MIN
        )

        assert len(cancel_set) == 1, label
        assert "REST_DEADLINE_EXCEEDED" in cancel_set[0]["cancel_reason"]


class TestIndeterminateNoCancel:
    """INDETERMINATE (NULL stamp, or family with no servable q) never
    contributes a q-staleness cancel. A FRESH order (well under the TTL
    deadline) in that state must not be cancelled at all — the fail-closed
    "do not churn cancels on a blind family" law.
    """

    def test_null_stamp_fresh_order_not_cancelled(self):
        entry = _entry("c1", q_version=None, age_minutes=5.0)
        cancel_set = classify_cancel_set(
            [entry], {"c1": FAMILY}, {FAMILY: "q-fresh"}, now=NOW, deadline_minutes=DEADLINE_MIN
        )
        assert cancel_set == []

    def test_blocked_family_fresh_order_not_cancelled(self):
        entry = _entry("c1", q_version="q-old", age_minutes=5.0)
        cancel_set = classify_cancel_set(
            [entry], {"c1": FAMILY}, {FAMILY: None}, now=NOW, deadline_minutes=DEADLINE_MIN
        )
        assert cancel_set == []

    def test_unresolved_family_fresh_order_not_cancelled(self):
        entry = _entry("c1", q_version="q-old", age_minutes=5.0)
        cancel_set = classify_cancel_set(
            [entry], {"c1": None}, {}, now=NOW, deadline_minutes=DEADLINE_MIN
        )
        assert cancel_set == []


class TestQVersionStaleCancel:
    def test_stale_fresh_order_is_cancelled_for_staleness_only(self):
        entry = _entry("c1", q_version="q-old", age_minutes=5.0)
        cancel_set = classify_cancel_set(
            [entry], {"c1": FAMILY}, {FAMILY: "q-new"}, now=NOW, deadline_minutes=DEADLINE_MIN
        )
        assert len(cancel_set) == 1
        assert cancel_set[0]["cancel_reason"] == "Q_VERSION_STALE"

    def test_matching_q_fresh_order_untouched(self):
        entry = _entry("c1", q_version="q-same", age_minutes=5.0)
        cancel_set = classify_cancel_set(
            [entry], {"c1": FAMILY}, {FAMILY: "q-same"}, now=NOW, deadline_minutes=DEADLINE_MIN
        )
        assert cancel_set == []


# ---------------------------------------------------------------------------
# find_open_entry_rests / resolve_order_families / read_current_family_q_versions
# ---------------------------------------------------------------------------


def _trade_db() -> sqlite3.Connection:
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _forecasts_db() -> sqlite3.Connection:
    from src.state.schema.v2_schema import apply_canonical_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn)
    return conn


def _seed_open_entry(
    conn,
    *,
    command_id: str,
    token_id: str,
    venue_order_id: str,
    q_version: str | None,
    created_at: datetime = NOW - timedelta(minutes=30),
    fact_state: str = "LIVE",
) -> None:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.execution.command_bus import IntentKind
    from src.state.snapshot_repo import insert_snapshot
    from src.state.venue_command_repo import insert_command, insert_submission_envelope

    snapshot_id = f"snap-{command_id}"
    insert_snapshot(
        conn,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id=f"gamma-{token_id}",
            event_id=f"event-{token_id}",
            event_slug=f"event-{token_id}",
            condition_id=f"cond-{token_id}",
            question_id=f"q-{token_id}",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            fee_details={"bps": 0, "builder_fee_bps": 0},
            token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.56"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=created_at,
            freshness_deadline=created_at + timedelta(days=365),
        ),
    )
    envelope_id = f"env-{command_id}"
    insert_submission_envelope(
        conn,
        VenueSubmissionEnvelope(
            sdk_package="py-clob-client-v2", sdk_version="test", host="https://clob-v2.polymarket.com",
            chain_id=137, funder_address="0xfunder", condition_id=f"cond-{token_id}", question_id=f"q-{token_id}",
            yes_token_id=token_id, no_token_id=f"{token_id}-no", selected_outcome_token_id=token_id,
            outcome_label="YES", side="BUY", price=Decimal("0.50"), size=Decimal("10"), order_type="GTC",
            post_only=True, tick_size=Decimal("0.01"), min_order_size=Decimal("0.01"), neg_risk=False,
            fee_details={"source": "test", "token_id": token_id, "fee_rate_fraction": 0.0, "fee_rate_bps": 0.0,
                         "fee_rate_source_field": "fee_rate_fraction", "fee_rate_raw_unit": "fraction"},
            canonical_pre_sign_payload_hash="a" * 64, signed_order=None, signed_order_hash=None,
            raw_request_hash="b" * 64, raw_response_json=None, order_id=None, trade_ids=(), transaction_hashes=(),
            error_code=None, error_message=None, captured_at=created_at.isoformat(),
        ),
        envelope_id=envelope_id,
    )
    insert_command(
        conn, command_id=command_id, snapshot_id=snapshot_id, envelope_id=envelope_id, position_id=f"pos-{command_id}",
        decision_id=f"decision-{command_id}", idempotency_key=command_id.ljust(32, "0")[:32],
        intent_kind=IntentKind.ENTRY.value, market_id=f"cond-{token_id}", token_id=token_id, side="BUY",
        size=10.0, price=0.50, created_at=created_at.isoformat(), snapshot_checked_at=created_at.isoformat(),
        q_version=q_version,
    )
    now = created_at.isoformat()
    # Advance straight to ACKED+venue_order_id by direct UPDATE rather than
    # append_event(SUBMIT_REQUESTED/SUBMIT_ACKED): ENTRY SUBMIT_REQUESTED
    # validates a full execution_capability payload this fixture has no need
    # to construct — find_open_entry_rests/cancel_commands_batch only read the
    # CURRENT venue_commands.state/venue_order_id, not the event history.
    conn.execute(
        "UPDATE venue_commands SET state = 'ACKED', venue_order_id = ?, updated_at = ? WHERE command_id = ?",
        (venue_order_id, now, command_id),
    )
    conn.execute(
        "INSERT INTO venue_order_facts (venue_order_id, command_id, state, remaining_size, matched_size, "
        "source, observed_at, local_sequence, raw_payload_hash) VALUES (?, ?, ?, ?, ?, 'REST', ?, 0, ?)",
        (venue_order_id, command_id, fact_state, "10", "0", now, "f" * 64),
    )
    conn.commit()


def _seed_market_event(conn, *, token_id: str, city: str, target_date: str, metric: str) -> None:
    conn.execute(
        "INSERT INTO market_events (market_slug, city, target_date, temperature_metric, condition_id, token_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f"slug-{token_id}", city, target_date, metric, f"cond-{token_id}", token_id),
    )
    conn.commit()


def _seed_posterior(conn, *, family, posterior_identity_hash: str, source_cycle_time: str) -> None:
    city, target_date, metric = family
    conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date, temperature_metric,
            source_cycle_time, source_available_at, computed_at, q_json, posterior_method,
            posterior_identity_hash
        ) VALUES ('openmeteo', 'openmeteo_ecmwf_ifs9_bayes_fusion_v1', 'v1', ?, ?, ?, ?, ?, ?, '{}', 'bayes', ?)
        """,
        (city, target_date, metric, source_cycle_time, source_cycle_time, source_cycle_time,
         posterior_identity_hash),
    )
    conn.commit()


class TestFindOpenEntryRests:
    def test_open_entry_rest_is_found_with_its_q_version(self):
        conn = _trade_db()
        _seed_open_entry(conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q-old")

        entries = find_open_entry_rests(conn)

        assert len(entries) == 1
        assert entries[0]["command_id"] == "c1"
        assert entries[0]["q_version"] == "q-old"

    def test_exit_orders_are_never_returned(self):
        conn = _trade_db()
        from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
        from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
        from src.execution.command_bus import IntentKind
        from src.state.snapshot_repo import insert_snapshot
        from src.state.venue_command_repo import append_event, insert_command, insert_submission_envelope

        insert_snapshot(
            conn,
            ExecutableMarketSnapshot(
                snapshot_id="snap-x", gamma_market_id="gamma-x", event_id="event-x", event_slug="event-x",
                condition_id="cond-x", question_id="q-x", yes_token_id="tok-x", no_token_id="tok-x-no",
                selected_outcome_token_id="tok-x", outcome_label="YES", enable_orderbook=True, active=True,
                closed=False, accepting_orders=True, market_start_at=None, market_end_at=None,
                market_close_at=None, sports_start_at=None, min_tick_size=Decimal("0.01"),
                min_order_size=Decimal("5"), fee_details={"bps": 0, "builder_fee_bps": 0},
                token_map_raw={"YES": "tok-x", "NO": "tok-x-no"}, rfqe=None, neg_risk=False,
                orderbook_top_bid=Decimal("0.49"), orderbook_top_ask=Decimal("0.56"),
                orderbook_depth_jsonb="{}", raw_gamma_payload_hash="a" * 64, raw_clob_market_info_hash="b" * 64,
                raw_orderbook_hash="c" * 64, authority_tier="CLOB", captured_at=NOW,
                freshness_deadline=NOW + timedelta(days=365),
            ),
        )
        insert_submission_envelope(
            conn,
            VenueSubmissionEnvelope(
                sdk_package="py-clob-client-v2", sdk_version="test", host="https://clob-v2.polymarket.com",
                chain_id=137, funder_address="0xfunder", condition_id="cond-x", question_id="q-x",
                yes_token_id="tok-x", no_token_id="tok-x-no", selected_outcome_token_id="tok-x",
                outcome_label="YES", side="SELL", price=Decimal("0.50"), size=Decimal("10"), order_type="GTC",
                post_only=False, tick_size=Decimal("0.01"), min_order_size=Decimal("0.01"), neg_risk=False,
                fee_details={"source": "test", "token_id": "tok-x", "fee_rate_fraction": 0.0, "fee_rate_bps": 0.0,
                             "fee_rate_source_field": "fee_rate_fraction", "fee_rate_raw_unit": "fraction"},
                canonical_pre_sign_payload_hash="a" * 64, signed_order=None, signed_order_hash=None,
                raw_request_hash="b" * 64, raw_response_json=None, order_id=None, trade_ids=(), transaction_hashes=(),
                error_code=None, error_message=None, captured_at=NOW.isoformat(),
            ),
            envelope_id="env-x",
        )
        insert_command(
            conn, command_id="c-exit", snapshot_id="snap-x", envelope_id="env-x", position_id="pos-x",
            decision_id="decision-x", idempotency_key="x" * 32, intent_kind=IntentKind.EXIT.value,
            market_id="cond-x", token_id="tok-x", side="SELL", size=10.0, price=0.50,
            created_at=NOW.isoformat(), snapshot_checked_at=NOW.isoformat(),
        )
        now = NOW.isoformat()
        append_event(conn, command_id="c-exit", event_type="SUBMIT_REQUESTED", occurred_at=now, payload={})
        append_event(conn, command_id="c-exit", event_type="SUBMIT_ACKED", occurred_at=now, payload={"order_id": "v-exit"})
        conn.execute(
            "INSERT INTO venue_order_facts (venue_order_id, command_id, state, remaining_size, matched_size, "
            "source, observed_at, local_sequence, raw_payload_hash) "
            "VALUES ('v-exit', 'c-exit', 'LIVE', '10', '0', 'REST', ?, 0, ?)",
            (now, "f" * 64),
        )
        conn.commit()

        assert find_open_entry_rests(conn) == []


class TestResolveOrderFamilies:
    def test_token_resolves_through_condition_to_family(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q-old")
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])

        entries = find_open_entry_rests(trade_conn)
        families = resolve_order_families(entries, trade_conn, forecasts_conn)

        assert families["c1"] == FAMILY

    def test_unresolvable_token_maps_to_none(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(trade_conn, command_id="c1", token_id="tok-orphan", venue_order_id="v1", q_version="q-old")

        entries = find_open_entry_rests(trade_conn)
        families = resolve_order_families(entries, trade_conn, forecasts_conn)

        assert families["c1"] is None


class TestReadCurrentFamilyQVersions:
    def test_freshest_posterior_wins(self):
        conn = _forecasts_db()
        _seed_posterior(conn, family=FAMILY, posterior_identity_hash="q-old", source_cycle_time="2026-07-03T00:00:00+00:00")
        _seed_posterior(conn, family=FAMILY, posterior_identity_hash="q-new", source_cycle_time="2026-07-03T12:00:00+00:00")

        result = read_current_family_q_versions(conn, [FAMILY])

        assert result[FAMILY] == "q-new"

    def test_family_with_no_posterior_is_none(self):
        conn = _forecasts_db()
        result = read_current_family_q_versions(conn, [FAMILY])
        assert result[FAMILY] is None


# ---------------------------------------------------------------------------
# run_c3_staleness_cancel_cycle: end-to-end orchestration
# ---------------------------------------------------------------------------


class _FakeGatewayClient:
    """Mirrors tests/execution/test_batch_order_submission.py's FakeGatewayClient
    shape (the real cancel_commands_batch's expected interface): NOT the
    PolymarketClient wrapper, so no cutover_guard call here — unit scope is the
    staleness_cancel orchestration, not the venue gate (already covered by
    tests/execution/test_batch_order_submission.py and polymarket_client tests).
    """

    def __init__(self, cancel_responses):
        self._responses = list(cancel_responses)
        self.cancel_calls: list[list[str]] = []

    def cancel_orders_batch(self, order_ids):
        self.cancel_calls.append(list(order_ids))
        return self._responses.pop(0)


class TestRunC3StalenessCancelCycle:
    def test_stale_order_is_cancelled_and_family_confirmed(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(
            trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1",
            q_version="q-old", created_at=NOW - timedelta(minutes=5),
        )
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        _seed_posterior(forecasts_conn, family=FAMILY, posterior_identity_hash="q-new", source_cycle_time=NOW.isoformat())
        client = _FakeGatewayClient(cancel_responses=[[{"canceled": True, "orderID": "v1"}]])

        # q-version staleness only fires within a claimed source event's
        # affected_cities (TestTtlEventClockSplit covers the TTL-vs-event split
        # itself); this test is about the q-stale classification+confirm path.
        result = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, forecasts_conn, client, now=NOW,
            affected_cities=frozenset({FAMILY[0]}),
        )

        assert result["cancel_set_size"] == 1
        assert result["confirmed_families"] == {FAMILY}
        assert conn_state(trade_conn, "c1") == "CANCELLED"

    def test_fresh_matching_q_order_is_never_touched(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(
            trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1",
            q_version="q-same", created_at=NOW - timedelta(minutes=5),
        )
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        _seed_posterior(forecasts_conn, family=FAMILY, posterior_identity_hash="q-same", source_cycle_time=NOW.isoformat())
        client = _FakeGatewayClient(cancel_responses=[])

        result = run_c3_staleness_cancel_cycle(trade_conn, trade_conn, forecasts_conn, client, now=NOW)

        assert result["cancel_set_size"] == 0
        assert result["confirmed_families"] == set()
        assert client.cancel_calls == []
        assert conn_state(trade_conn, "c1") == "ACKED"

    def test_budget_denial_defers_never_drops_the_intent(self):
        """A rate-budget denial must leave the command open and un-journaled —
        never silently dropped. The next tick's fresh scan sees the SAME still-
        open, still-stale order and can retry it; there is no cancel-set removal
        or dead-letter for a deferred command."""
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(
            trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1",
            q_version="q-old", created_at=NOW - timedelta(minutes=5),
        )
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        _seed_posterior(forecasts_conn, family=FAMILY, posterior_identity_hash="q-new", source_cycle_time=NOW.isoformat())
        client = _FakeGatewayClient(cancel_responses=[])  # never reached: budget denies first

        class _DenyingBudget:
            def try_acquire(self, request_class):
                from src.venue.rate_budget import BudgetDecision, BudgetResult

                return BudgetResult(BudgetDecision.DENIED, request_class, wait_seconds=15.0)

        result = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, forecasts_conn, client, now=NOW, rate_budget=_DenyingBudget(),
            affected_cities=frozenset({FAMILY[0]}),
        )

        assert result["cancel_set_size"] == 1  # classified as cancel-worthy...
        assert result["confirmed_families"] == set()  # ...but NOT confirmed cancelled
        assert client.cancel_calls == []  # the SDK was never even called
        # the command is untouched — still ACKED/open, not CANCEL_PENDING/journaled,
        # so a fresh classification next tick reclassifies and retries it cleanly.
        assert conn_state(trade_conn, "c1") == "ACKED"
        outcome = result["outcomes"][0]
        assert outcome.status == "not_attempted"
        assert "rate_budget" in (outcome.error_message or "")

    def test_indeterminate_blind_family_fresh_order_not_cancelled_end_to_end(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(
            trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1",
            q_version="q-old", created_at=NOW - timedelta(minutes=5),
        )
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        # No posterior seeded: family has no servable q -> BLOCKED/INDETERMINATE.
        client = _FakeGatewayClient(cancel_responses=[])

        result = run_c3_staleness_cancel_cycle(trade_conn, trade_conn, forecasts_conn, client, now=NOW)

        assert result["cancel_set_size"] == 0
        assert client.cancel_calls == []
        assert conn_state(trade_conn, "c1") == "ACKED"

    def test_affected_cities_filter_scopes_the_scan(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        other_family = ("Toronto", "2026-07-04", "high")
        _seed_open_entry(
            trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1",
            q_version="q-old", created_at=NOW - timedelta(minutes=5),
        )
        _seed_open_entry(
            trade_conn, command_id="c2", token_id="tok2", venue_order_id="v2",
            q_version="q-old", created_at=NOW - timedelta(minutes=5),
        )
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        _seed_market_event(forecasts_conn, token_id="tok2", city=other_family[0], target_date=other_family[1], metric=other_family[2])
        _seed_posterior(forecasts_conn, family=FAMILY, posterior_identity_hash="q-new-1", source_cycle_time=NOW.isoformat())
        _seed_posterior(forecasts_conn, family=other_family, posterior_identity_hash="q-new-2", source_cycle_time=NOW.isoformat())
        client = _FakeGatewayClient(cancel_responses=[[{"canceled": True, "orderID": "v1"}]])

        result = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, forecasts_conn, client, now=NOW,
            affected_cities=frozenset({FAMILY[0]}),
        )

        # scanned reflects the FULL global scan (both cities) -- affected_cities
        # only scopes the q-version staleness pass, never the TTL pass's scan.
        assert result["scanned"] == 2
        assert result["confirmed_families"] == {FAMILY}
        assert conn_state(trade_conn, "c2") == "ACKED"  # out-of-scope city, fresh q, not past TTL -> untouched


class TestFamilyLevelRedecisionGating:
    """Consult review round 2 BLOCKER: confirmed_families must be FAMILY-level
    conservative, not per-command. A family with one durably-cancelled command
    and one command stuck ambiguous (REVIEW_REQUIRED / not_canceled / unknown)
    in the SAME cycle must be excluded ENTIRELY -- that family still carries a
    recovery-owned ambiguous venue exposure; emitting a redecision for it
    anyway risks a duplicate/overlapping submit against that exposure."""

    def test_mixed_outcomes_in_same_family_suppress_the_whole_family(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(
            trade_conn, command_id="c-good", token_id="tok-good", venue_order_id="v-good",
            q_version=None, created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
        )
        _seed_open_entry(
            trade_conn, command_id="c-bad", token_id="tok-bad", venue_order_id="v-bad",
            q_version=None, created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
        )
        # BOTH commands resolve to the SAME family.
        _seed_market_event(forecasts_conn, token_id="tok-good", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        _seed_market_event(forecasts_conn, token_id="tok-bad", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        # ONE batch call, per-order mixed outcome: c-good acks cleanly; c-bad
        # comes back NOT_CANCELED (ambiguous -- venue truth still open).
        client = _FakeGatewayClient(
            cancel_responses=[[
                {"canceled": True, "orderID": "v-good"},
                {"orderID": "v-bad", "status": "NOT_CANCELED", "errorMessage": "still live"},
            ]]
        )

        result = run_c3_staleness_cancel_cycle(trade_conn, trade_conn, forecasts_conn, client, now=NOW)

        assert result["cancel_set_size"] == 2
        assert conn_state(trade_conn, "c-good") == "CANCELLED"
        assert conn_state(trade_conn, "c-bad") != "CANCELLED"
        # The family is excluded ENTIRELY, not partially confirmed, because
        # c-bad's ambiguous outcome makes the family's venue exposure unclear.
        assert result["confirmed_families"] == set()

    def test_ambiguous_family_does_not_block_an_unrelated_confirmed_family(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        other_family = ("Toronto", "2026-07-04", "high")
        _seed_open_entry(
            trade_conn, command_id="c-good", token_id="tok-good", venue_order_id="v-good",
            q_version=None, created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
        )
        _seed_open_entry(
            trade_conn, command_id="c-bad", token_id="tok-bad", venue_order_id="v-bad",
            q_version=None, created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
        )
        _seed_open_entry(
            trade_conn, command_id="c-clean", token_id="tok-clean", venue_order_id="v-clean",
            q_version=None, created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
        )
        _seed_market_event(forecasts_conn, token_id="tok-good", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        _seed_market_event(forecasts_conn, token_id="tok-bad", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        _seed_market_event(
            forecasts_conn, token_id="tok-clean", city=other_family[0], target_date=other_family[1], metric=other_family[2]
        )
        client = _FakeGatewayClient(
            cancel_responses=[[
                {"canceled": True, "orderID": "v-good"},
                {"orderID": "v-bad", "status": "NOT_CANCELED", "errorMessage": "still live"},
                {"canceled": True, "orderID": "v-clean"},
            ]]
        )

        result = run_c3_staleness_cancel_cycle(trade_conn, trade_conn, forecasts_conn, client, now=NOW)

        assert result["confirmed_families"] == {other_family}  # FAMILY (ambiguous) excluded, other_family confirmed


class TestTtlEventClockSplit:
    """The composition fix: TTL (rest_deadline_exceeded) is a GLOBAL,
    UNCONDITIONAL pass over every open rest on every call, independent of
    whether any SOURCE_RUN_ARRIVED event fired or which cities it named.
    q-version staleness is the only pass scoped to affected_cities. Regression
    coverage for the orphaned-GTC scheduler-composition bug: gating the TTL
    scan behind claimed events, or filtering entries by affected_cities BEFORE
    classification, stranded expired rests during quiet periods / in
    non-event cities."""

    def test_no_source_event_still_cancels_expired_rest(self):
        """affected_cities=None (no SOURCE_RUN_ARRIVED claimed this tick) must
        NOT suppress the TTL pass -- an expired rest is still cancelled."""
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(
            trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1",
            q_version=None, created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
        )
        # No market_events/posterior seeded at all -- family is unresolvable,
        # proving TTL fires without ANY q-version machinery available.
        client = _FakeGatewayClient(cancel_responses=[[{"canceled": True, "orderID": "v1"}]])

        result = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, forecasts_conn, client, now=NOW, affected_cities=None,
        )

        assert result["cancel_set_size"] == 1
        assert client.cancel_calls == [["v1"]]
        assert conn_state(trade_conn, "c1") == "CANCELLED"

    def test_source_event_city_does_not_starve_other_city_ttl(self):
        """A SOURCE_RUN_ARRIVED for city A must not prevent an expired rest in
        city B (untouched by the event) from being cancelled by TTL."""
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        city_b = ("Toronto", "2026-07-04", "high")
        _seed_open_entry(
            trade_conn, command_id="c-fresh-a", token_id="tok-a", venue_order_id="v-a",
            q_version="q-old", created_at=NOW - timedelta(minutes=5),
        )
        _seed_open_entry(
            trade_conn, command_id="c-expired-b", token_id="tok-b", venue_order_id="v-b",
            q_version="q-b", created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
        )
        _seed_market_event(forecasts_conn, token_id="tok-a", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        _seed_market_event(forecasts_conn, token_id="tok-b", city=city_b[0], target_date=city_b[1], metric=city_b[2])
        _seed_posterior(forecasts_conn, family=FAMILY, posterior_identity_hash="q-new", source_cycle_time=NOW.isoformat())
        # city_b's rest is well past TTL and NOT stale on q (no posterior is even
        # seeded for city_b -- TTL alone must carry it). Both commands land in
        # ONE merged cancel-set, so ONE batch call carries both order IDs.
        client = _FakeGatewayClient(
            cancel_responses=[[{"canceled": True, "orderID": "v-a"}, {"canceled": True, "orderID": "v-b"}]]
        )

        result = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, forecasts_conn, client, now=NOW,
            affected_cities=frozenset({FAMILY[0]}),  # event only names city A
        )

        assert result["scanned"] == 2
        cancelled_order_ids = {oid for chunk in client.cancel_calls for oid in chunk}
        assert cancelled_order_ids == {"v-a", "v-b"}
        assert conn_state(trade_conn, "c-fresh-a") == "CANCELLED"  # q-stale, scoped pass
        assert conn_state(trade_conn, "c-expired-b") == "CANCELLED"  # TTL, unscoped pass -- not starved

    def test_duplicate_source_event_replay_is_idempotent_no_double_cancel(self):
        """A replayed/duplicate SOURCE_RUN_ARRIVED driving a second
        run_c3_staleness_cancel_cycle call over the SAME already-cancelled
        order must not produce a duplicate venue side effect -- the second
        pass sees the command already CANCELLED and cancel_commands_batch
        skips it as not_requestable (no second SDK call, no second journal
        entry, no double cancel)."""
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(
            trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1",
            q_version="q-old", created_at=NOW - timedelta(minutes=5),
        )
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        _seed_posterior(forecasts_conn, family=FAMILY, posterior_identity_hash="q-new", source_cycle_time=NOW.isoformat())
        client = _FakeGatewayClient(cancel_responses=[[{"canceled": True, "orderID": "v1"}], [None]])

        first = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, forecasts_conn, client, now=NOW,
            affected_cities=frozenset({FAMILY[0]}),
        )
        assert first["confirmed_families"] == {FAMILY}
        assert conn_state(trade_conn, "c1") == "CANCELLED"

        cancel_events_after_first = trade_conn.execute(
            "SELECT COUNT(*) FROM venue_command_events WHERE command_id = 'c1' AND event_type = 'CANCEL_ACKED'"
        ).fetchone()[0]
        assert cancel_events_after_first == 1

        # REPLAY: the same command is still returned by find_open_entry_rests'
        # underlying state? No -- it is CANCELLED now, so a second identical
        # tick's TTL/q-stale classification would not even re-select it (the
        # scan only returns state IN ('ACKED','POST_ACKED','PARTIAL')). This
        # proves the idempotency at the SOURCE: a duplicate SOURCE_RUN_ARRIVED
        # driving a second cycle finds nothing left to cancel for c1.
        second = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, forecasts_conn, client, now=NOW,
            affected_cities=frozenset({FAMILY[0]}),
        )
        assert second["cancel_set_size"] == 0
        assert client.cancel_calls == [["v1"]]  # the SDK was never called a second time
        cancel_events_after_second = trade_conn.execute(
            "SELECT COUNT(*) FROM venue_command_events WHERE command_id = 'c1' AND event_type = 'CANCEL_ACKED'"
        ).fetchone()[0]
        assert cancel_events_after_second == 1  # no duplicate journal entry


def conn_state(conn: sqlite3.Connection, command_id: str) -> str:
    return conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = ?", (command_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# main._c3_staleness_cancel_cycle: the GLUE layer itself, not just the
# extracted run_c3_staleness_cancel_cycle function.
#
# The E1 BLOCKER (early return on empty claimed_ids, filtering TTL by
# affected_cities) lived entirely in this glue, not in the pure function above
# -- every prior test in this file called run_c3_staleness_cancel_cycle
# directly and would stay green even if the glue silently regressed. This
# closes that gap: it drives the real @_scheduler_job-decorated main.py
# function end-to-end (monkeypatched dependencies only, no behavior change),
# with the exact scenario the BLOCKER broke -- zero claimed SOURCE_RUN_ARRIVED
# events -- and asserts the TTL-expired rest is still cancelled.
# ---------------------------------------------------------------------------


class TestMainC3StalenessCancelCycleGlue:
    def test_zero_claimed_events_still_cancels_expired_rest_through_the_real_scheduler_job(
        self, monkeypatch, tmp_path
    ):
        import src.data.polymarket_client as polymarket_client_module
        import src.events.event_store as event_store_module
        import src.execution.command_recovery as command_recovery_module
        import src.main as main_module
        import src.state.db as state_db
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_canonical_schema

        trade_db_path = tmp_path / "trade.db"
        forecasts_db_path = tmp_path / "forecasts.db"

        seed_trade = sqlite3.connect(str(trade_db_path))
        seed_trade.row_factory = sqlite3.Row
        init_schema(seed_trade)
        _seed_open_entry(
            seed_trade, command_id="c1", token_id="tok1", venue_order_id="v1",
            q_version=None,
            created_at=datetime.now(UTC) - timedelta(minutes=DEADLINE_MIN + 5),
        )
        seed_trade.commit()
        seed_trade.close()

        seed_forecasts = sqlite3.connect(str(forecasts_db_path))
        seed_forecasts.row_factory = sqlite3.Row
        apply_canonical_schema(seed_forecasts)
        seed_forecasts.commit()
        seed_forecasts.close()

        def _open_trade():
            conn = sqlite3.connect(str(trade_db_path))
            conn.row_factory = sqlite3.Row
            return conn

        def _open_forecasts():
            conn = sqlite3.connect(str(forecasts_db_path))
            conn.row_factory = sqlite3.Row
            return conn

        class _FakeEventStore:
            """Zero claimed events -- the exact scenario the BLOCKER broke."""

            def __init__(self, conn, *, consumer_name):
                pass

            def fetch_pending_by_event_type(self, *, event_type, decision_time, limit):
                return []

            def claim(self, event_id):
                return True

            def mark_processed(self, event_id):
                pass

        class _FakeWorldConn:
            def commit(self):
                pass

            def close(self):
                pass

        cancel_calls: list[list[str]] = []

        class _FakeGatewayClient:
            def cancel_orders_batch(self, order_ids):
                cancel_calls.append(list(order_ids))
                return [{"canceled": True, "orderID": oid} for oid in order_ids]

        monkeypatch.setattr(
            main_module, "_settings_section",
            lambda name, default=None: {"enabled": True, "event_writer_enabled": False},
        )
        monkeypatch.setattr(main_module, "get_mode", lambda: "live")
        monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda job_name: False)
        # Invalid-entry-authority lane is orthogonal to this glue proof -- stub
        # it to a no-op so this test stays focused on the TTL/event-clock seam.
        monkeypatch.setattr(
            command_recovery_module, "find_invalid_pending_entry_authority_cancels", lambda conn: []
        )
        monkeypatch.setattr(polymarket_client_module, "PolymarketClient", _FakeGatewayClient)
        monkeypatch.setattr(event_store_module, "EventStore", _FakeEventStore)
        monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeWorldConn())
        monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: _open_trade())
        monkeypatch.setattr(state_db, "get_trade_connection", lambda write_class=None: _open_trade())
        monkeypatch.setattr(state_db, "get_forecasts_connection_read_only", lambda: _open_forecasts())

        main_module._c3_staleness_cancel_cycle()

        assert cancel_calls == [["v1"]]
        check_conn = _open_trade()
        try:
            assert conn_state(check_conn, "c1") == "CANCELLED"
        finally:
            check_conn.close()

    def test_event_store_failure_still_runs_ttl_pass(self, monkeypatch, tmp_path):
        """HIGH (consult round 2): the retired maker_rest_escalation TTL owner
        never depended on the event lane at all -- a fault in the
        SOURCE_RUN_ARRIVED claim lane (EventStore raising) must degrade to
        "no source event this tick," never take down the unconditional TTL
        pass. Without the fail-soft wrap, an EventStore exception propagates
        out of _c3_staleness_cancel_cycle (caught only by @_scheduler_job,
        which marks the WHOLE tick failed and skips the TTL scan too) -- an
        availability regression versus the deleted job."""
        import src.data.polymarket_client as polymarket_client_module
        import src.events.event_store as event_store_module
        import src.execution.command_recovery as command_recovery_module
        import src.main as main_module
        import src.state.db as state_db
        from src.state.db import init_schema
        from src.state.schema.v2_schema import apply_canonical_schema

        trade_db_path = tmp_path / "trade.db"
        forecasts_db_path = tmp_path / "forecasts.db"

        seed_trade = sqlite3.connect(str(trade_db_path))
        seed_trade.row_factory = sqlite3.Row
        init_schema(seed_trade)
        _seed_open_entry(
            seed_trade, command_id="c1", token_id="tok1", venue_order_id="v1",
            q_version=None,
            created_at=datetime.now(UTC) - timedelta(minutes=DEADLINE_MIN + 5),
        )
        seed_trade.commit()
        seed_trade.close()

        seed_forecasts = sqlite3.connect(str(forecasts_db_path))
        seed_forecasts.row_factory = sqlite3.Row
        apply_canonical_schema(seed_forecasts)
        seed_forecasts.commit()
        seed_forecasts.close()

        def _open_trade():
            conn = sqlite3.connect(str(trade_db_path))
            conn.row_factory = sqlite3.Row
            return conn

        def _open_forecasts():
            conn = sqlite3.connect(str(forecasts_db_path))
            conn.row_factory = sqlite3.Row
            return conn

        class _RaisingEventStore:
            def __init__(self, conn, *, consumer_name):
                pass

            def fetch_pending_by_event_type(self, *, event_type, decision_time, limit):
                raise sqlite3.OperationalError("simulated world DB fault")

            def claim(self, event_id):
                raise AssertionError("must not be reached: fetch already raised")

            def mark_processed(self, event_id):
                raise AssertionError("must not be reached: nothing was claimed")

        class _FakeWorldConn:
            def commit(self):
                raise AssertionError("must not be reached: fetch raised before commit")

            def close(self):
                pass

        cancel_calls: list[list[str]] = []

        class _FakeGatewayClient:
            def cancel_orders_batch(self, order_ids):
                cancel_calls.append(list(order_ids))
                return [{"canceled": True, "orderID": oid} for oid in order_ids]

        monkeypatch.setattr(
            main_module, "_settings_section",
            lambda name, default=None: {"enabled": True, "event_writer_enabled": False},
        )
        monkeypatch.setattr(main_module, "get_mode", lambda: "live")
        monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda job_name: False)
        monkeypatch.setattr(
            command_recovery_module, "find_invalid_pending_entry_authority_cancels", lambda conn: []
        )
        monkeypatch.setattr(polymarket_client_module, "PolymarketClient", _FakeGatewayClient)
        monkeypatch.setattr(event_store_module, "EventStore", _RaisingEventStore)
        monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeWorldConn())
        monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: _open_trade())
        monkeypatch.setattr(state_db, "get_trade_connection", lambda write_class=None: _open_trade())
        monkeypatch.setattr(state_db, "get_forecasts_connection_read_only", lambda: _open_forecasts())

        # Must not raise: the fault is caught and degraded, the TTL pass runs regardless.
        main_module._c3_staleness_cancel_cycle()

        assert cancel_calls == [["v1"]]
        check_conn = _open_trade()
        try:
            assert conn_state(check_conn, "c1") == "CANCELLED"
        finally:
            check_conn.close()
