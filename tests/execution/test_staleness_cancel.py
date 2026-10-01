# Created: 2026-07-03
# Last reused/audited: 2026-10-01
# Authority basis: docs/rebuild/schema_packets/w1_2_order_state_extension_schema_packet_2026-07-02.md
#   (SCH-W1.2-ORDER-STATE) C3 path; standing ENTRY keep-by-value law (operator, 2026-09-30):
#   an open ENTRY rest is kept or cancelled on current fractional-Kelly value, never on
#   age or posterior identity.
"""C3 standing ENTRY valuation: readers, family resolution, persistence, cancel,
and the reconciled-redecision gate."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import src.execution.staleness_cancel as staleness_cancel_module
from src.execution.staleness_cancel import (
    StandingEntryValuation,
    _merge_cancel_proposals,
    find_open_entry_rests,
    persist_standing_entry_values,
    read_journaled_identities,
    resolve_order_families,
    run_c3_staleness_cancel_cycle,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 3, 22, 0, 0, tzinfo=UTC)
FAMILY = ("Miami", "2026-07-04", "high")


@pytest.fixture(autouse=True)
def _dry_run_entry_fixture_mode(monkeypatch):
    """Fixtures seed already-acknowledged rests; live ENTRY admission is not under test."""
    monkeypatch.delenv("ZEUS_ENTRY_Q_VERSION_STRICT", raising=False)
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
    monkeypatch.setenv("ZEUS_MODE", "dry_run")


def _entry(command_id: str, *, q_version, age_minutes: float, family=FAMILY) -> dict:
    return {
        "command_id": command_id,
        "venue_order_id": f"vord-{command_id}",
        "token_id": f"tok-{command_id}",
        "market_id": "mkt-1",
        "created_at": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "q_version": q_version,
        "fact_state": "LIVE",
        "command_side": "BUY",
        "matched_size": "0",
        "min_order_size": "5",
    }


def _keep(command_id: str, venue_order_id: str, token_id: str) -> StandingEntryValuation:
    return StandingEntryValuation(
        command_id=command_id,
        venue_order_id=venue_order_id,
        token_id=token_id,
        family=FAMILY,
        action="KEEP",
        reason="CURRENT_ENTRY_REST_VALUE_POSITIVE",
        evidence={"authority_valid": True, "probability_witness_identity": "w"},
    )


def _trade_db() -> sqlite3.Connection:
    from src.state.db import init_schema, init_schema_trade_only

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    return conn


def _forecasts_db() -> sqlite3.Connection:
    from src.state.schema.v2_schema import apply_canonical_schema
    from src.state.db import (
        _create_readiness_state, _create_source_run, _create_source_run_coverage,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_canonical_schema(conn)
    _create_source_run(conn)
    _create_source_run_coverage(conn)
    _create_readiness_state(conn)
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
    matched_size: str = "0",
    remaining_size: str = "10",
    min_order_size: Decimal = Decimal("5"),
) -> None:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.snapshot_repo import insert_snapshot
    from src.state.venue_command_repo import insert_submission_envelope

    init_collateral_schema(conn)
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
            min_order_size=min_order_size,
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
    now = created_at.isoformat()
    # An already-acknowledged rest is an input fixture: insert the ACKED row
    # directly. Live ENTRY admission (certificate closure, SUBMIT_REQUESTED
    # capability payload) is not under test; C3 reads only the current
    # venue_commands row, its order facts and its submission snapshot.
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, created_at, updated_at, q_version
        ) VALUES (?, ?, ?, ?, ?, ?, 'ENTRY', ?, ?, 'BUY', 10.0, 0.50, ?, 'ACKED', ?, ?, ?)
        """,
        (
            command_id, snapshot_id, envelope_id, f"pos-{command_id}", f"decision-{command_id}",
            command_id.ljust(32, "0")[:32], f"cond-{token_id}", token_id, venue_order_id,
            now, now, q_version,
        ),
    )
    conn.execute(
        "INSERT INTO venue_order_facts (venue_order_id, command_id, state, remaining_size, matched_size, "
        "source, observed_at, local_sequence, raw_payload_hash) VALUES (?, ?, ?, ?, ?, 'REST', ?, 0, ?)",
        (venue_order_id, command_id, fact_state, remaining_size, matched_size, now, "f" * 64),
    )
    conn.commit()


def _seed_submit_requested_forecast_q_payload(
    conn,
    *,
    command_id: str,
    q_version: str,
    source_id: str = "openmeteo_ecmwf_ifs9_bayes_fusion",
    authority_tier: str = "FORECAST",
    forecast_source_role: str = "entry_primary",
) -> None:
    sequence = conn.execute(
        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM venue_command_events WHERE command_id = ?",
        (command_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after
        ) VALUES (?, ?, ?, 'SUBMIT_REQUESTED', ?, ?, 'SUBMITTING')
        """,
        (
            f"{command_id}:submit_requested:{sequence}",
            command_id,
            sequence,
            NOW.isoformat(),
            json.dumps(
                {
                    "execution_capability": {
                        "components": [
                            {
                                "component": "decision_source_integrity",
                                "details": {
                                    "source_id": source_id,
                                    "authority_tier": authority_tier,
                                    "forecast_source_role": forecast_source_role,
                                    "raw_payload_hash": q_version,
                                },
                            }
                        ]
                    }
                },
                sort_keys=True,
            ),
        ),
    )
    conn.commit()



def _seed_market_event(conn, *, token_id: str, city: str, target_date: str, metric: str) -> None:
    conn.execute(
        "INSERT INTO market_events (market_slug, city, target_date, temperature_metric, condition_id, token_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f"slug-{token_id}", city, target_date, metric, f"cond-{token_id}", token_id),
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
        assert entries[0]["min_order_size"] == "5"

    def test_legacy_null_q_forecast_rest_recovers_q_from_submit_payload(self):
        conn = _trade_db()
        q_version = "a" * 64
        _seed_open_entry(conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version=None)
        _seed_submit_requested_forecast_q_payload(
            conn,
            command_id="c1",
            q_version=q_version,
        )

        entries = find_open_entry_rests(conn)

        assert len(entries) == 1
        assert entries[0]["q_version"] == q_version
        assert entries[0]["q_version_source"] == "submit_requested_decision_source"

    def test_legacy_null_q_day0_or_external_rest_stays_null(self):
        conn = _trade_db()
        q_version = "b" * 64
        _seed_open_entry(conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version=None)
        _seed_submit_requested_forecast_q_payload(
            conn,
            command_id="c1",
            q_version=q_version,
            source_id="ecmwf_open_data",
            authority_tier="OBSERVATION",
            forecast_source_role="day0_live_observation",
        )

        entries = find_open_entry_rests(conn)

        assert len(entries) == 1
        assert entries[0]["q_version"] is None
        assert entries[0]["q_version_authority"] == "day0_observation"
        assert "q_version_source" not in entries[0]

    def test_pre_migration_schema_treats_q_version_as_null(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE venue_commands (
                command_id TEXT PRIMARY KEY,
                venue_order_id TEXT,
                token_id TEXT,
                market_id TEXT,
                created_at TEXT,
                intent_kind TEXT,
                state TEXT
            );
            CREATE TABLE venue_order_facts (
                venue_order_id TEXT,
                command_id TEXT,
                state TEXT,
                matched_size TEXT,
                local_sequence INTEGER
            );
            INSERT INTO venue_commands (
                command_id, venue_order_id, token_id, market_id, created_at, intent_kind, state
            ) VALUES ('c-live-pre-migration', 'v1', 'tok1', 'mkt1', '2026-07-03T21:30:00+00:00', 'ENTRY', 'ACKED');
            INSERT INTO venue_order_facts (
                venue_order_id, command_id, state, matched_size, local_sequence
            ) VALUES ('v1', 'c-live-pre-migration', 'LIVE', '0', 1);
            """
        )

        entries = find_open_entry_rests(conn)

        assert len(entries) == 1
        assert entries[0]["command_id"] == "c-live-pre-migration"
        assert entries[0]["q_version"] is None

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


def _pending_cancel_reader_db(
    *,
    side="BUY",
    fact_state="LIVE",
    command_state="CANCEL_PENDING",
    event_type="CANCEL_REQUESTED",
    event_order_id="v-pending",
    batch=True,
    venue_order_id="v-pending",
    include_fact=True,
    include_event=True,
):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            venue_order_id TEXT,
            token_id TEXT,
            market_id TEXT,
            created_at TEXT,
            intent_kind TEXT,
            state TEXT,
            side TEXT,
            q_version TEXT
        );
        CREATE TABLE venue_order_facts (
            venue_order_id TEXT,
            state TEXT,
            matched_size TEXT,
            local_sequence INTEGER
        );
        CREATE TABLE venue_command_events (
            command_id TEXT,
            sequence_no INTEGER,
            event_type TEXT,
            payload_json TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?, ?, 'tok-pending', 'mkt-pending', ?, 'ENTRY', ?, ?, 'q-same')",
        ("c-pending", venue_order_id, NOW.isoformat(), command_state, side),
    )
    if include_fact and venue_order_id:
        conn.execute(
            "INSERT INTO venue_order_facts VALUES (?, ?, '0', 1)",
            (venue_order_id, fact_state),
        )
    payload = {
        "venue_order_id": event_order_id,
        "batch": batch,
    }
    if include_event:
        conn.execute(
            "INSERT INTO venue_command_events VALUES ('c-pending', 1, ?, ?)",
            (event_type, json.dumps(payload)),
        )
    conn.commit()
    return conn


def _seed_existing_pending_cancel(conn):
    """Inject an already-journaled command; bypass new ENTRY admission gates."""
    now = NOW.isoformat()
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size, price,
            venue_order_id, state, last_event_id, created_at, updated_at,
            review_required_reason, q_version
        ) VALUES (
                'c-pending', 'snap-pending', 'env-pending', 'pos-pending', 'decision-pending',
            'pending-idempotency-key-000000', 'ENTRY', 'mkt-pending', 'tok-pending',
                'BUY', 10.0, 0.50, 'v-pending', 'CANCEL_PENDING',
                'c-pending:cancel-requested', ?, ?, NULL, 'q-same'
        )
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO venue_order_facts (
            venue_order_id, command_id, state, remaining_size, matched_size,
            source, observed_at, local_sequence, raw_payload_hash
        ) VALUES ('v-pending', 'c-pending', 'LIVE', '10', '0', 'REST', ?, 1, ?)
        """,
        (now, "f" * 64),
    )
    conn.execute(
        """
        INSERT INTO venue_command_events (
            event_id, command_id, sequence_no, event_type, occurred_at,
            payload_json, state_after
        ) VALUES ('c-pending:cancel-requested', 'c-pending', 1,
                  'CANCEL_REQUESTED', ?, ?, 'CANCEL_PENDING')
        """,
        (now, json.dumps({"venue_order_id": "v-pending", "batch": True})),
    )
    conn.commit()


class TestPendingCancelReader:
    def test_c3_reader_returns_matching_pending_batch_intent(self):
        conn = _pending_cancel_reader_db()

        assert find_open_entry_rests(conn) == []
        entries = find_open_entry_rests(conn, include_pending_cancels=True)

        assert len(entries) == 1
        assert entries[0]["command_state"] == "CANCEL_PENDING"
        assert entries[0]["pending_cancel"] is True
        assert entries[0]["venue_order_id"] == "v-pending"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"side": "SELL"},
            {"fact_state": "UNKNOWN"},
            {"fact_state": "CANCEL_CONFIRMED"},
            {"event_type": "CANCEL_ACKED"},
            {"event_order_id": "v-other"},
            {"batch": False},
            {"venue_order_id": ""},
            {"include_fact": False},
            {"include_event": False},
        ],
    )
    def test_c3_reader_rejects_non_matching_pending_shapes(self, kwargs):
        conn = _pending_cancel_reader_db(**kwargs)

        assert find_open_entry_rests(conn, include_pending_cancels=True) == []

class TestResolveOrderFamilies:
    def test_order_resolves_through_its_own_submission_snapshot(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q-old")
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])

        entries = find_open_entry_rests(trade_conn)
        families = resolve_order_families(entries, trade_conn, forecasts_conn)

        assert families["c1"] == FAMILY

    def test_unresolvable_condition_maps_to_none(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(trade_conn, command_id="c1", token_id="tok-orphan", venue_order_id="v1", q_version="q-old")

        entries = find_open_entry_rests(trade_conn)
        families = resolve_order_families(entries, trade_conn, forecasts_conn)

        assert families["c1"] is None

    def test_high_low_alias_on_one_condition_is_ambiguous_not_guessed(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q-old")
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric="high")
        forecasts_conn.execute(
            "INSERT INTO market_events (market_slug, city, target_date, temperature_metric, condition_id, token_id) "
            "VALUES ('slug-low', ?, ?, 'low', 'cond-tok1', 'tok1-low')",
            (FAMILY[0], FAMILY[1]),
        )
        forecasts_conn.commit()

        families = resolve_order_families(find_open_entry_rests(trade_conn), trade_conn, forecasts_conn)

        assert families["c1"] is None

    def test_token_not_in_its_own_snapshot_is_none(self):
        trade_conn = _trade_db()
        forecasts_conn = _forecasts_db()
        _seed_open_entry(trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q-old")
        _seed_market_event(forecasts_conn, token_id="tok1", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
        entries = find_open_entry_rests(trade_conn)
        entries[0]["token_id"] = "tok-other"

        assert resolve_order_families(entries, trade_conn, forecasts_conn)["c1"] is None


# ---------------------------------------------------------------------------
# Persistence: KEEP authority is durable, append-only, keyed by time-independent
# economics, and never rewrites the submission; CANCEL goes through the batch
# journal (with its reason) before the venue call; a journal fault never blocks it.
# ---------------------------------------------------------------------------


class _FakeGatewayClient:
    """The batch gateway shape ``cancel_commands_batch`` calls."""

    def __init__(self, cancel_responses):
        self._responses = list(cancel_responses)
        self.cancel_calls: list[list[str]] = []

    def cancel_orders_batch(self, order_ids):
        self.cancel_calls.append(list(order_ids))
        return self._responses.pop(0)


def conn_state(conn: sqlite3.Connection, command_id: str) -> str:
    return conn.execute(
        "SELECT state FROM venue_commands WHERE command_id = ?", (command_id,)
    ).fetchone()[0]


def _standing_rows(conn) -> list[dict]:
    return [
        json.loads(row[0])
        for row in conn.execute(
            "SELECT artifact_json FROM decision_log WHERE mode = 'standing_entry_revaluation' ORDER BY id"
        )
    ]


def _valuation(
    action: str,
    *,
    reason: str = "R",
    witness: str = "w-new",
    posterior: str = "posterior-a",
    acting_q: float = 0.75,
) -> StandingEntryValuation:
    return StandingEntryValuation(
        command_id="c1",
        venue_order_id="v1",
        token_id="tok1",
        family=FAMILY,
        action=action,
        reason=reason,
        evidence={
            "authority_valid": True,
            "probability_witness_identity": witness,
            "wealth_witness_identity": f"wealth-{witness}",
            "q_version": f"q-{posterior}",
            "posterior_identity_hash": posterior,
            "acting_q": acting_q,
            "target_remaining": "10",
            "open_remaining": "10",
            "limit_price": "0.5",
        },
    )


def _persist(conn, valuations, *, now):
    return persist_standing_entry_values(
        conn, valuations, now=now, journaled=read_journaled_identities(conn, now=now)
    )


class TestStandingEntryPersistence:
    def test_keep_journals_new_authority_without_touching_the_order(self):
        conn = _trade_db()
        _seed_open_entry(conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q-submitted")
        before = conn.execute(
            "SELECT state, venue_order_id, q_version, last_event_id, envelope_id FROM venue_commands"
        ).fetchone()
        events_before = conn.execute("SELECT COUNT(*) FROM venue_command_events").fetchone()[0]

        written = _persist(conn, [_valuation("KEEP")], now=datetime.now(UTC))

        assert written == 1
        after = conn.execute(
            "SELECT state, venue_order_id, q_version, last_event_id, envelope_id FROM venue_commands"
        ).fetchone()
        assert tuple(after) == tuple(before)  # submission certificate and q_version untouched
        assert conn.execute("SELECT COUNT(*) FROM venue_command_events").fetchone()[0] == events_before
        rows = _standing_rows(conn)
        assert len(rows) == 1
        assert rows[0]["action"] == "KEEP"
        assert rows[0]["venue_order_id"] == "v1"
        assert rows[0]["evidence"]["probability_witness_identity"] == "w-new"
        assert not conn.in_transaction

    def test_advancing_clock_with_unchanged_economics_journals_exactly_one_row(self):
        # Witness identities hash their capture time, so they differ every
        # tick in production. The same economics must still journal once.
        conn = _trade_db()
        _seed_open_entry(conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q")
        at = datetime.now(UTC)

        _persist(conn, [_valuation("KEEP", witness="w-tick-1")], now=at)
        _persist(conn, [_valuation("KEEP", witness="w-tick-2")], now=at + timedelta(minutes=5))

        assert len(_standing_rows(conn)) == 1

    def test_changed_economics_or_posterior_journals_again(self):
        conn = _trade_db()
        _seed_open_entry(conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q")
        at = datetime.now(UTC)

        _persist(conn, [_valuation("KEEP")], now=at)
        _persist(conn, [_valuation("KEEP", posterior="posterior-b")], now=at + timedelta(minutes=5))
        _persist(conn, [_valuation("KEEP", posterior="posterior-b", acting_q=0.7)], now=at + timedelta(minutes=10))

        assert [
            (r["evidence"]["posterior_identity_hash"], r["evidence"]["acting_q"]) for r in _standing_rows(conn)
        ] == [("posterior-a", 0.75), ("posterior-b", 0.75), ("posterior-b", 0.7)]

    def test_the_journal_read_is_outside_the_write_lease(self, monkeypatch):
        # Inside the lease the only statements are the INSERTs and the bounded
        # retention walk: no journal row is read under the lock.
        conn = _trade_db()
        _seed_open_entry(conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q")
        at = datetime.now(UTC)
        journaled = read_journaled_identities(conn, now=at)
        statements: list[str] = []
        conn.set_trace_callback(lambda sql: statements.append(sql) if conn.in_transaction else None)
        try:
            persist_standing_entry_values(conn, [_valuation("KEEP")], now=at, journaled=journaled)
        finally:
            conn.set_trace_callback(None)

        under_lease = [sql for sql in statements if "decision_log" in sql and "SELECT" in sql.upper()]
        assert not any("json_extract" in sql for sql in under_lease)

    def test_journal_window_read_seeks_the_window_not_the_whole_mode(self):
        conn = _trade_db()
        _seed_open_entry(conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q")
        at = datetime.now(UTC)
        _persist(conn, [_valuation("KEEP")], now=at - timedelta(hours=30))
        # Outside the re-journal window the unchanged KEEP journals again.
        assert read_journaled_identities(conn, now=at) == {}
        _persist(conn, [_valuation("KEEP")], now=at)
        assert len(_standing_rows(conn)) == 2

    def test_a_failed_journal_write_leaves_the_order_and_reservation_intact(self):
        conn = _trade_db()
        _seed_open_entry(conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q")
        conn.execute(
            "CREATE TRIGGER fail_standing BEFORE INSERT ON decision_log "
            "BEGIN SELECT RAISE(ABORT, 'fault'); END"
        )
        conn.commit()

        with pytest.raises(sqlite3.DatabaseError):
            _persist(conn, [_valuation("KEEP")], now=NOW)

        assert conn_state(conn, "c1") == "ACKED"
        assert not conn.in_transaction


def replace_valuation(valuation: StandingEntryValuation, **changes) -> StandingEntryValuation:
    from dataclasses import replace

    return replace(valuation, **changes)


class TestRunC3StandingValuation:
    """Orchestration: KEEP takes no venue action; CANCEL is journaled
    CANCEL_REQUESTED (with its reason) before the single batch SDK call."""

    def _run(self, monkeypatch, valuations, *, responses, rate_budget=None, journal_fault=False):
        trade_conn = _trade_db()
        _seed_open_entry(trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q")
        monkeypatch.setattr(staleness_cancel_module, "resolve_order_families", lambda *_a: {"c1": FAMILY})
        monkeypatch.setattr(
            staleness_cancel_module, "_capture_standing_entry_values", lambda *_a, **_k: valuations
        )
        if journal_fault:
            from src.state.write_coordinator import WriteLeaseTimeout

            def _timeout(*_a, **_k):
                raise WriteLeaseTimeout("standing_entry_value lease timed out")

            monkeypatch.setattr(staleness_cancel_module, "persist_standing_entry_values", _timeout)
        import src.execution.day0_hard_fact_exit as day0_hard_fact_exit

        monkeypatch.setattr(
            day0_hard_fact_exit, "classify_day0_dead_bin_entry_cancels", lambda *_a, **_k: []
        )
        observed_before_sdk: list[tuple[str, dict]] = []

        class _Client(_FakeGatewayClient):
            def cancel_orders_batch(self, order_ids):
                state = conn_state(trade_conn, "c1")
                payload = json.loads(
                    trade_conn.execute(
                        "SELECT payload_json FROM venue_command_events "
                        "WHERE command_id='c1' AND event_type='CANCEL_REQUESTED'"
                    ).fetchone()[0]
                )
                observed_before_sdk.append((state, payload))
                return super().cancel_orders_batch(order_ids)

        client = _Client(cancel_responses=responses)
        result = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, object(), client,
            world_conn_ro=object(), now=NOW, rate_budget=rate_budget,
        )
        return trade_conn, client, result, observed_before_sdk

    def test_keep_takes_no_venue_action(self, monkeypatch):
        trade_conn, client, result, _ = self._run(
            monkeypatch, [_valuation("KEEP")], responses=[]
        )

        assert client.cancel_calls == []
        assert result["kept"] == 1
        assert result["cancel_set_size"] == 0
        assert result["confirmed_families"] == set()
        assert conn_state(trade_conn, "c1") == "ACKED"

    def test_cancel_journals_its_reason_before_sdk_then_confirms_family(self, monkeypatch):
        trade_conn, client, result, observed = self._run(
            monkeypatch,
            [_valuation("CANCEL", reason="CURRENT_FRACTIONAL_TARGET_REDUCED")],
            responses=[[{"canceled": True, "orderID": "v1"}]],
        )

        assert client.cancel_calls == [["v1"]]
        assert observed == [(
            "CANCEL_PENDING",
            {"venue_order_id": "v1", "batch": True, "cancel_reason": "CURRENT_FRACTIONAL_TARGET_REDUCED"},
        )]
        assert conn_state(trade_conn, "c1") == "CANCELLED"
        # The family's fresh redecision is gated on the durable CANCELLED read.
        assert result["confirmed_families"] == {FAMILY}

    def test_a_raising_journal_still_sends_the_protective_cancel(self, monkeypatch):
        protective = StandingEntryValuation(
            command_id="c1",
            venue_order_id="v1",
            token_id="tok1",
            family=FAMILY,
            action="CANCEL",
            reason="ENTRY_REST_PROBABILITY_BLOCKED:ValueError:HWM",
            evidence={"authority_valid": False},
        )
        trade_conn, client, result, observed = self._run(
            monkeypatch,
            [protective],
            responses=[[{"canceled": True, "orderID": "v1"}]],
            journal_fault=True,
        )

        assert client.cancel_calls == [["v1"]]
        assert observed[0][0] == "CANCEL_PENDING"
        assert conn_state(trade_conn, "c1") == "CANCELLED"
        assert result["journaled"] == 0

    def test_a_raising_capture_cancels_every_active_rest_protectively(self, monkeypatch):
        trade_conn = _trade_db()
        _seed_open_entry(trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q")
        monkeypatch.setattr(staleness_cancel_module, "resolve_order_families", lambda *_a: {"c1": FAMILY})

        def _boom(*_a, **_k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(staleness_cancel_module, "_capture_standing_entry_values", _boom)
        import src.execution.day0_hard_fact_exit as day0_hard_fact_exit

        monkeypatch.setattr(day0_hard_fact_exit, "classify_day0_dead_bin_entry_cancels", lambda *_a, **_k: [])
        client = _FakeGatewayClient(cancel_responses=[[{"canceled": True, "orderID": "v1"}]])

        result = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, object(), client, world_conn_ro=object(), now=NOW
        )

        assert client.cancel_calls == [["v1"]]
        assert result["valuations"][0].reason == "ENTRY_REST_VALUATION_FAILED:OperationalError"

    def test_budget_denial_defers_never_drops_the_intent(self, monkeypatch):
        class _DenyingBudget:
            def try_acquire(self, request_class):
                from src.venue.rate_budget import BudgetDecision, BudgetResult

                return BudgetResult(BudgetDecision.DENIED, request_class, wait_seconds=15.0)

        trade_conn, client, result, _ = self._run(
            monkeypatch,
            [_valuation("CANCEL")],
            responses=[],
            rate_budget=_DenyingBudget(),
        )

        assert client.cancel_calls == []
        assert result["cancel_set_size"] == 1
        assert result["confirmed_families"] == set()
        assert conn_state(trade_conn, "c1") == "ACKED"
        assert result["outcomes"][0].status == "not_attempted"

    def test_family_scoped_pass_values_only_those_families(self, monkeypatch):
        trade_conn = _trade_db()
        _seed_open_entry(trade_conn, command_id="c-in", token_id="tok-in", venue_order_id="v-in", q_version="q")
        _seed_open_entry(trade_conn, command_id="c-out", token_id="tok-out", venue_order_id="v-out", q_version="q")
        other = ("Paris", "2026-07-04", "low")
        monkeypatch.setattr(
            staleness_cancel_module, "resolve_order_families", lambda *_a: {"c-in": FAMILY, "c-out": other}
        )
        valued: list[list[str]] = []

        def _capture(_trade, _forecasts, _world, active, **_k):
            valued.append([str(e["command_id"]) for e in active])
            return []

        monkeypatch.setattr(staleness_cancel_module, "_capture_standing_entry_values", _capture)
        import src.execution.day0_hard_fact_exit as day0_hard_fact_exit

        def _no_day0(*_a, **_k):
            raise AssertionError("a wake pass leaves the Day0 lane to the full tick")

        monkeypatch.setattr(day0_hard_fact_exit, "classify_day0_dead_bin_entry_cancels", _no_day0)

        result = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, object(), _FakeGatewayClient([]),
            world_conn_ro=object(), now=NOW, families={("miami", "2026-07-04", "HIGH")},
        )

        assert valued == [["c-in"]]
        assert result["scanned"] == 1

    def test_mixed_outcomes_in_same_family_suppress_the_whole_family(self, monkeypatch):
        trade_conn = _trade_db()
        _seed_open_entry(trade_conn, command_id="c-good", token_id="tok-good", venue_order_id="v-good", q_version="q")
        _seed_open_entry(trade_conn, command_id="c-bad", token_id="tok-bad", venue_order_id="v-bad", q_version="q")
        monkeypatch.setattr(
            staleness_cancel_module,
            "resolve_order_families",
            lambda *_a: {"c-good": FAMILY, "c-bad": FAMILY},
        )
        monkeypatch.setattr(
            staleness_cancel_module,
            "_capture_standing_entry_values",
            lambda *_a, **_k: [
                replace_valuation(_valuation("CANCEL"), command_id=cid, venue_order_id=vid, token_id=tok)
                for cid, vid, tok in (("c-good", "v-good", "tok-good"), ("c-bad", "v-bad", "tok-bad"))
            ],
        )
        import src.execution.day0_hard_fact_exit as day0_hard_fact_exit

        monkeypatch.setattr(day0_hard_fact_exit, "classify_day0_dead_bin_entry_cancels", lambda *_a, **_k: [])
        client = _FakeGatewayClient(
            cancel_responses=[[
                {"canceled": True, "orderID": "v-good"},
                {"orderID": "v-bad", "status": "NOT_CANCELED", "errorMessage": "still live"},
            ]]
        )

        result = run_c3_staleness_cancel_cycle(
            trade_conn, trade_conn, object(), client, world_conn_ro=object(), now=NOW
        )

        assert conn_state(trade_conn, "c-good") == "CANCELLED"
        assert conn_state(trade_conn, "c-bad") != "CANCELLED"
        assert result["confirmed_families"] == set()


class TestMainC3StandingValuationGlue:
    """The scheduler job revalues every open rest whether or not any
    SOURCE_RUN_ARRIVED event is claimed, and a claim-lane fault never
    suppresses the valuation."""

    @pytest.mark.parametrize("event_lane", ["empty", "raising"])
    def test_valuation_runs_without_and_despite_the_event_lane(self, monkeypatch, event_lane):
        import src.data.polymarket_client as polymarket_client_module
        import src.events.event_store as event_store_module
        import src.execution.command_recovery as command_recovery_module
        import src.main as main_module
        import src.state.db as state_db

        class _EventStore:
            def __init__(self, conn, *, consumer_name):
                pass

            def fetch_pending_by_event_type(self, *, event_type, decision_time, limit):
                if event_lane == "raising":
                    raise sqlite3.OperationalError("simulated world DB fault")
                return []

            def claim(self, event_id):
                raise AssertionError("nothing to claim")

        class _World:
            def commit(self):
                pass

            def close(self):
                pass

        calls: list[dict] = []

        def _run(trade_ro, trade_rw, forecasts_ro, client, **kwargs):
            calls.append(kwargs)
            return {
                "scanned": 1, "kept": 1, "journaled": 0, "cancel_set_size": 0,
                "confirmed_families": set(), "valuations": [], "outcomes": [],
                "day0_cancel_set_size": 0,
            }

        class _Conn:
            def close(self):
                pass

        monkeypatch.setattr(main_module, "_settings_section", lambda name, default=None: {})
        monkeypatch.setattr(main_module, "get_mode", lambda: "live")
        monkeypatch.setattr(main_module, "_defer_for_held_position_monitor", lambda job_name: False)
        monkeypatch.setattr(command_recovery_module, "find_invalid_pending_entry_authority_cancels", lambda conn: [])
        monkeypatch.setattr(polymarket_client_module, "PolymarketClient", lambda: object())
        monkeypatch.setattr(event_store_module, "EventStore", _EventStore)
        monkeypatch.setattr(staleness_cancel_module, "run_c3_staleness_cancel_cycle", _run)
        monkeypatch.setattr(state_db, "get_world_connection", lambda: _World())
        monkeypatch.setattr(state_db, "get_world_connection_read_only", lambda: _Conn())
        monkeypatch.setattr(state_db, "get_trade_connection_read_only", lambda: _Conn())
        monkeypatch.setattr(state_db, "get_trade_connection", lambda write_class=None: _Conn())
        monkeypatch.setattr(state_db, "get_forecasts_connection_read_only", lambda: _Conn())

        main_module._c3_staleness_cancel_cycle()

        assert len(calls) == 1
        assert "affected_cities" not in calls[0]
        assert calls[0]["families"] is None
        assert isinstance(calls[0]["world_conn_ro"], _Conn)

    def test_belief_and_day0_wakes_run_the_same_valuation_on_their_families(self, monkeypatch):
        import src.main as main_module

        runs: list[frozenset] = []
        monkeypatch.setattr(
            main_module,
            "_run_standing_entry_valuation",
            lambda *, now, families: runs.append(families) or {
                "scanned": 0, "kept": 0, "cancel_set_size": 0, "confirmed_families": set(),
            },
        )
        family = ("Miami", "2026-07-04", "high")
        day0_family = ("Paris", "2026-07-04", "low")
        monkeypatch.setattr(
            main_module, "_day0_wake_target_families", lambda event_ids: frozenset({day0_family})
        )
        wakes = (
            SimpleNamespace(reason="forecast_posterior_advanced", forecast_families=(family,), event_ids=()),
            SimpleNamespace(reason="day0_extreme_event_committed", forecast_families=(), event_ids=("e1",)),
        )
        main_module._standing_entry_valuation_lock.acquire()
        main_module._standing_entry_wake_valuation(wakes)

        assert runs == [frozenset({family, day0_family})]
        assert not main_module._standing_entry_valuation_lock.locked()

    def test_new_queued_wakes_start_one_pass_without_being_consumed(self, monkeypatch):
        import src.main as main_module
        import src.runtime.reactor_wake as reactor_wake

        family = ("Miami", "2026-07-04", "high")
        wake = SimpleNamespace(
            wake_id="w1", reason="forecast_posterior_advanced", forecast_families=(family,), event_ids=()
        )
        monkeypatch.setattr(main_module, "get_mode", lambda: "live")
        monkeypatch.setattr(
            reactor_wake,
            "reactor_wakes_for_reason",
            lambda reason, **_k: (wake,) if reason == "forecast_posterior_advanced" else (),
        )
        started: list[tuple] = []

        class _Thread:
            def __init__(self, *, target, args, name, daemon):
                started.append(args[0])

            def start(self):
                main_module._standing_entry_valuation_lock.release()

        monkeypatch.setattr(main_module.threading, "Thread", _Thread)
        monkeypatch.setattr(main_module, "_standing_entry_valued_wake_ids", set())

        main_module._value_standing_entries_for_new_wakes()
        main_module._value_standing_entries_for_new_wakes()

        assert [tuple(w.wake_id for w in batch) for batch in started] == [("w1",)]

    def test_a_held_valuation_lock_defers_the_wake_to_the_next_poll(self, monkeypatch):
        import src.main as main_module
        import src.runtime.reactor_wake as reactor_wake

        wake = SimpleNamespace(
            wake_id="w2", reason="forecast_posterior_advanced",
            forecast_families=(("Miami", "2026-07-04", "high"),), event_ids=(),
        )
        monkeypatch.setattr(main_module, "get_mode", lambda: "live")
        monkeypatch.setattr(
            reactor_wake, "reactor_wakes_for_reason",
            lambda reason, **_k: (wake,) if reason == "forecast_posterior_advanced" else (),
        )
        monkeypatch.setattr(main_module, "_standing_entry_valued_wake_ids", set())
        main_module._standing_entry_valuation_lock.acquire()
        try:
            main_module._value_standing_entries_for_new_wakes()
        finally:
            main_module._standing_entry_valuation_lock.release()

        assert main_module._standing_entry_valued_wake_ids == set()


def test_pending_cancel_is_retried_without_valuation_or_day0_classification(monkeypatch):
    from src.execution import batch_order_submission
    from src.execution import day0_hard_fact_exit
    from src.state import venue_command_repo

    entry = {
        **_entry("c-pending", q_version="q-same", age_minutes=1.0),
        "venue_order_id": "v-pending",
        "command_state": "CANCEL_PENDING",
        "pending_cancel": True,
    }
    monkeypatch.setattr(
        staleness_cancel_module,
        "find_open_entry_rests",
        lambda _conn, **kwargs: (
            assert_pending_reader_flag(kwargs),
            [entry],
        )[1],
    )
    monkeypatch.setattr(
        staleness_cancel_module,
        "resolve_order_families",
        lambda *_args: {"c-pending": FAMILY},
    )
    monkeypatch.setattr(
        staleness_cancel_module,
        "_capture_standing_entry_values",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pending retry valued")),
    )
    day0_calls = []
    monkeypatch.setattr(
        day0_hard_fact_exit,
        "classify_day0_dead_bin_entry_cancels",
        lambda entries, **_kwargs: (day0_calls.append(entries), [])[1],
    )
    submitted = []

    def _cancel_batch(_conn, _client, command_ids, **_kwargs):
        submitted.append(list(command_ids))
        return [SimpleNamespace(command_id="c-pending", status="acked")]

    monkeypatch.setattr(batch_order_submission, "cancel_commands_batch", _cancel_batch)
    monkeypatch.setattr(venue_command_repo, "get_command", lambda *_args: {"state": "CANCELLED"})

    result = run_c3_staleness_cancel_cycle(
        object(), object(), object(), object(), world_conn_ro=object(), now=NOW
    )

    assert submitted == [["c-pending"]]
    assert result["cancel_set_size"] == 1
    assert result["confirmed_families"] == {FAMILY}
    assert day0_calls == []


def assert_pending_reader_flag(kwargs):
    assert kwargs == {"include_pending_cancels": True}


def test_pending_cancel_real_batch_retry_rate_denial_ack_and_dedup(monkeypatch, caplog):
    from src.execution import staleness_cancel

    trade_conn = _trade_db()
    _seed_existing_pending_cancel(trade_conn)
    forecasts_conn = object()
    monkeypatch.setattr(
        staleness_cancel,
        "resolve_order_families",
        lambda *_args: {"c-pending": None},
    )
    monkeypatch.setattr(
        staleness_cancel,
        "_capture_standing_entry_values",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pending retry valued")),
    )
    client = _FakeGatewayClient(
        cancel_responses=[[{"canceled": True, "orderID": "v-pending"}]]
    )

    class _RateBudget:
        def __init__(self):
            self.decisions = [False, True]

        def try_acquire(self, _request_class):
            granted = self.decisions.pop(0)
            return SimpleNamespace(
                granted=granted,
                decision=SimpleNamespace(value="DENIED"),
            )

    budget = _RateBudget()
    first = run_c3_staleness_cancel_cycle(
        trade_conn, trade_conn, forecasts_conn, client,
        world_conn_ro=object(), now=NOW, rate_budget=budget,
    )
    assert first["outcomes"][0].status == "not_attempted"
    assert "command_id=c-pending status=not_attempted reason=rate_budget_DENIED" in caplog.text
    assert client.cancel_calls == []
    assert conn_state(trade_conn, "c-pending") == "CANCEL_PENDING"
    assert trade_conn.execute(
        "SELECT COUNT(*) FROM venue_command_events "
        "WHERE command_id='c-pending' AND event_type='CANCEL_REQUESTED'"
    ).fetchone()[0] == 1

    second = run_c3_staleness_cancel_cycle(
        trade_conn, trade_conn, forecasts_conn, client,
        world_conn_ro=object(), now=NOW, rate_budget=budget,
    )
    assert second["outcomes"][0].status == "acked"
    assert client.cancel_calls == [["v-pending"]]
    assert conn_state(trade_conn, "c-pending") == "CANCELLED"
    assert trade_conn.execute(
        "SELECT COUNT(*) FROM venue_command_events "
        "WHERE command_id='c-pending' AND event_type='CANCEL_REQUESTED'"
    ).fetchone()[0] == 1
    assert trade_conn.execute(
        "SELECT COUNT(*) FROM venue_command_events "
        "WHERE command_id='c-pending' AND event_type='CANCEL_ACKED'"
    ).fetchone()[0] == 1

    third = run_c3_staleness_cancel_cycle(
        trade_conn, trade_conn, forecasts_conn, client,
        world_conn_ro=object(), now=NOW, rate_budget=budget,
    )
    assert third["cancel_set_size"] == 0
    assert client.cancel_calls == [["v-pending"]]



def test_day0_classifier_selects_only_dead_local_day_entry(monkeypatch):
    from src.execution import day0_hard_fact_exit

    day0_hard_fact_exit._reset_wu_memo_for_tests()
    identities = {
        "dead": {
            "city": "Miami",
            "target_date": "2026-07-03",
            "metric": "high",
            "range_low": 25.0,
            "range_high": 25.0,
            "direction": "buy_yes",
        },
        "winner": {
            "city": "Miami",
            "target_date": "2026-07-03",
            "metric": "high",
            "range_low": 25.0,
            "range_high": 25.0,
            "direction": "buy_no",
        },
        "tomorrow": {
            "city": "Miami",
            "target_date": "2026-07-04",
            "metric": "high",
            "range_low": 25.0,
            "range_high": 25.0,
            "direction": "buy_yes",
        },
    }
    monkeypatch.setattr(
        day0_hard_fact_exit,
        "_resolve_order_bin_identity",
        lambda _conn, token_id, **_kwargs: identities[token_id],
    )
    monkeypatch.setattr(
        day0_hard_fact_exit,
        "_wu_hard_fact_evidence",
        lambda **_kwargs: SimpleNamespace(rounded_extreme=26.0),
    )
    entries = [
        {
            "command_id": key,
            "token_id": key,
            "command_side": "BUY",
            "created_at": NOW.isoformat(),
        }
        for key in identities
    ]

    proposals = day0_hard_fact_exit.classify_day0_dead_bin_entry_cancels(
        entries,
        trade_conn=object(),
        forecasts_conn=object(),
        cities_by_name={"Miami": SimpleNamespace(timezone="UTC")},
        now=NOW,
    )

    assert [proposal["command_id"] for proposal in proposals] == ["dead"]
    assert proposals[0]["cancel_reason"] == "HARD_FACT_BIN_DEAD"


def test_day0_classifier_rejects_entry_sell(monkeypatch):
    from src.execution import day0_hard_fact_exit

    monkeypatch.setattr(
        day0_hard_fact_exit,
        "_resolve_order_bin_identity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("SELL must be rejected before identity resolution")
        ),
    )

    proposals = day0_hard_fact_exit.classify_day0_dead_bin_entry_cancels(
        [
            {
                "command_id": "sell-entry",
                "token_id": "token",
                "command_side": "SELL",
            }
        ],
        trade_conn=object(),
        forecasts_conn=object(),
        cities_by_name={},
        now=NOW,
    )

    assert proposals == []


def test_day0_classifier_warns_on_unresolved_canonical_identity(monkeypatch, caplog):
    from src.execution import day0_hard_fact_exit

    monkeypatch.setattr(
        day0_hard_fact_exit,
        "_resolve_order_bin_identity",
        lambda *_args, **_kwargs: None,
    )
    caplog.set_level("WARNING", logger="src.execution.day0_hard_fact_exit")

    proposals = day0_hard_fact_exit.classify_day0_dead_bin_entry_cancels(
        [
            {"command_id": "missing-token", "command_side": "BUY"},
            {
                "command_id": "unresolved-token",
                "token_id": "token",
                "command_side": "BUY",
            },
        ],
        trade_conn=object(),
        forecasts_conn=object(),
        cities_by_name={},
        now=NOW,
    )

    assert proposals == []
    assert "missing canonical identity" in caplog.text
    assert "unresolved token identity" in caplog.text


def test_open_entry_scan_excludes_entry_sell() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT, venue_order_id TEXT, token_id TEXT, market_id TEXT,
            created_at TEXT, q_version TEXT, intent_kind TEXT, side TEXT,
            state TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            venue_order_id TEXT, state TEXT, matched_size TEXT,
            local_sequence INTEGER
        )
        """
    )
    for command_id, side in (("buy", "BUY"), ("sell", "SELL")):
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?)",
            (
                command_id,
                f"order-{command_id}",
                f"token-{command_id}",
                "market",
                NOW.isoformat(),
                "q-current",
                "ENTRY",
                side,
                "ACKED",
            ),
        )
        conn.execute(
            "INSERT INTO venue_order_facts VALUES (?,?,?,?)",
            (f"order-{command_id}", "LIVE", "0", 1),
        )

    entries = find_open_entry_rests(conn)

    assert [(entry["command_id"], entry["command_side"]) for entry in entries] == [
        ("buy", "BUY")
    ]


def test_day0_identity_joins_trade_token_to_forecast_bin() -> None:
    from src.execution.day0_hard_fact_exit import _resolve_order_bin_identity

    trade_conn = sqlite3.connect(":memory:")
    trade_conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            condition_id TEXT, yes_token_id TEXT, no_token_id TEXT,
            captured_at TEXT
        )
        """
    )
    trade_conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?)",
        ("condition", "yes-token", "no-token", NOW.isoformat()),
    )
    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.execute(
        """
        CREATE TABLE market_events (
            city TEXT, target_date TEXT, range_low REAL, range_high REAL,
            temperature_metric TEXT, condition_id TEXT, token_id TEXT
        )
        """
    )
    forecasts_conn.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?,?)",
        ("Miami", "2026-07-03", 30.0, None, "high", "condition", "yes-token"),
    )

    identity = _resolve_order_bin_identity(
        trade_conn,
        "no-token",
        market_conn=forecasts_conn,
    )

    assert identity == {
        "city": "Miami",
        "target_date": "2026-07-03",
        "range_low": 30.0,
        "range_high": None,
        "metric": "high",
        "condition_id": "condition",
        "direction": "buy_no",
    }


def test_c3_day0_cancel_uses_batch_journal_and_confirms_family(monkeypatch):
    from src.execution import day0_hard_fact_exit
    from src.execution import staleness_cancel

    trade_conn = _trade_db()
    forecasts_conn = _forecasts_db()
    created_at = (NOW - timedelta(minutes=1)).isoformat()
    trade_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at, q_version
        ) VALUES (?, ?, ?, ?, ?, ?, 'ENTRY', ?, ?, 'BUY', 10, 0.5, ?,
                  'ACKED', ?, ?, ?)
        """,
        (
            "c-day0",
            "snap-day0",
            "env-day0",
            "pos-day0",
            "decision-day0",
            "c-day0".ljust(32, "0")[:32],
            "cond-day0",
            "tok-day0",
            "v-day0",
            created_at,
            created_at,
            "q-current",
        ),
    )
    trade_conn.execute(
        """
        INSERT INTO venue_order_facts (
            venue_order_id, command_id, state, remaining_size, matched_size,
            source, observed_at, local_sequence, raw_payload_hash
        ) VALUES ('v-day0', 'c-day0', 'LIVE', '10', '0', 'REST', ?, 0, ?)
        """,
        (created_at, "f" * 64),
    )
    trade_conn.commit()
    entry = {
        "command_id": "c-day0",
        "venue_order_id": "v-day0",
        "token_id": "tok-day0",
        "market_id": "cond-day0",
        "created_at": created_at,
        "q_version": "q-current",
        "fact_state": "LIVE",
        "command_side": "BUY",
        "matched_size": "0",
    }
    monkeypatch.setattr(
        staleness_cancel,
        "find_open_entry_rests",
        lambda _conn, **_kwargs: [entry],
    )
    monkeypatch.setattr(
        staleness_cancel,
        "resolve_order_families",
        lambda *_args: {"c-day0": FAMILY},
    )
    # The Day0 lane is under test; the value lane keeps this rest.
    monkeypatch.setattr(
        staleness_cancel,
        "_capture_standing_entry_values",
        lambda *_args, **_kwargs: [_keep("c-day0", "v-day0", "tok-day0")],
    )
    monkeypatch.setattr(
        day0_hard_fact_exit,
        "classify_day0_dead_bin_entry_cancels",
        lambda entries, **_kwargs: [
            {
                **entries[0],
                "family": FAMILY,
                "cancel_reason": "HARD_FACT_BIN_DEAD",
                "cancel_action": "CANCEL_REPLACE",
                "cancel_detail": {"trigger": "day0_dead_bin_cancel"},
            }
        ],
    )
    client = _FakeGatewayClient(
        cancel_responses=[[{"canceled": True, "orderID": "v-day0"}]]
    )

    result = run_c3_staleness_cancel_cycle(
        trade_conn,
        trade_conn,
        forecasts_conn,
        client,
        world_conn_ro=object(),
        now=NOW,
    )

    assert result["day0_cancel_set_size"] == 1
    assert result["cancel_set_size"] == 1
    assert result["confirmed_families"] == {FAMILY}
    assert conn_state(trade_conn, "c-day0") == "CANCELLED"
    assert client.cancel_calls == [["v-day0"]]


def test_c3_merge_preserves_every_lane_reason_and_family() -> None:
    families = {"c1": None}

    merged = _merge_cancel_proposals(
        (
            (
                "value",
                [
                    {
                        "command_id": "c1",
                        "cancel_reason": "CURRENT_MEAN_VALUE_NON_POSITIVE",
                        "cancel_detail": {"value": True},
                    }
                ],
            ),
            (
                "day0",
                [
                    {
                        "command_id": "c1",
                        "family": FAMILY,
                        "cancel_reason": "HARD_FACT_BIN_DEAD",
                        "cancel_detail": {"dead": True},
                    }
                ],
            ),
        ),
        families,
    )

    assert len(merged) == 1
    assert merged[0]["cancel_reason"] == "CURRENT_MEAN_VALUE_NON_POSITIVE+HARD_FACT_BIN_DEAD"
    assert merged[0]["cancel_detail_by_lane"] == {
        "value": {"value": True},
        "day0": {"dead": True},
    }
    assert families["c1"] == FAMILY


def test_day0_classification_failure_does_not_suppress_valuation(monkeypatch) -> None:
    from src import config
    from src.execution import batch_order_submission
    from src.execution import day0_hard_fact_exit
    from src.execution import staleness_cancel

    trade_conn = _trade_db()
    _seed_open_entry(trade_conn, command_id="c1", token_id="tok1", venue_order_id="v1", q_version="q-current")
    monkeypatch.setattr(staleness_cancel, "resolve_order_families", lambda *_args: {"c1": FAMILY})
    monkeypatch.setattr(config, "runtime_cities_by_name", lambda: {})
    monkeypatch.setattr(
        day0_hard_fact_exit,
        "classify_day0_dead_bin_entry_cancels",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("day0 unavailable")),
    )
    monkeypatch.setattr(
        staleness_cancel,
        "_capture_standing_entry_values",
        lambda *_args, **_kwargs: [
            staleness_cancel.StandingEntryValuation(
                command_id="c1", venue_order_id="v1", token_id="tok1", family=FAMILY,
                action="CANCEL", reason="CURRENT_MEAN_VALUE_NON_POSITIVE",
                evidence={"authority_valid": True},
            )
        ],
    )
    submitted: list[list[str]] = []

    def _cancel_batch(_conn, _client, command_ids, **_kwargs):
        submitted.append(list(command_ids))
        return [SimpleNamespace(command_id="c1", status="acked")]

    monkeypatch.setattr(batch_order_submission, "cancel_commands_batch", _cancel_batch)

    result = run_c3_staleness_cancel_cycle(
        trade_conn, trade_conn, object(), object(), world_conn_ro=object(), now=NOW
    )

    assert submitted == [["c1"]]
    assert result["cancel_set_size"] == 1
    assert result["day0_cancel_set_size"] == 0
