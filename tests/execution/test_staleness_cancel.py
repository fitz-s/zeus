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
