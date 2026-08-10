# Created: 2026-04-26
# Last reused/audited: 2026-08-10
# Lifecycle: created=2026-04-26; last_reviewed=2026-08-10; last_reused=2026-08-10
# Purpose: Lock venue command journal invariants, transitions, recovery, and U1 snapshot gate.
# Reuse: Run when venue_command_repo, command schema, or executable snapshot gate changes.
# Authority basis: command-bus INV-28/NC-18 plus schema-21 global receipt closure;
#                  2026-08-10 typed command creation/adoption/absorption helpers and legal taker persistence.
"""Tests for src/state/venue_command_repo.py (P1.S1 — INV-28 / NC-18)."""
from __future__ import annotations

import ast
import glob
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.contracts.global_auction_receipt import (
    GlobalAuctionReceiptRef,
    GlobalSellReceiptClosure,
)

ROOT = Path(__file__).resolve().parents[1]
_NOW = datetime(2026, 4, 26, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory DB with full schema (via init_schema)."""
    from src.state.db import init_schema, init_schema_trade_only

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    init_schema_trade_only(c)
    c.execute("ATTACH DATABASE ':memory:' AS world")
    c.execute(
        """
        CREATE TABLE world.decision_certificates (
            certificate_hash TEXT PRIMARY KEY,
            certificate_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            verifier_status TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    yield c
    c.close()


@pytest.fixture(autouse=True)
def offline_q_version_env(monkeypatch):
    monkeypatch.delenv("ZEUS_ENTRY_Q_VERSION_STRICT", raising=False)
    monkeypatch.delenv("ZEUS_MODE", raising=False)
    monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)


def _insert(c, *, command_id="cmd-001", position_id="pos-001",
            decision_id="dec-001", idempotency_key="idem-001",
            intent_kind="ENTRY", market_id="mkt-001", token_id="tok-001",
            side="BUY", size=10.0, price=0.5,
            created_at="2026-04-26T00:00:00Z", q_version=None,
            decision_certificate_hash=None,
            decision_certificate_payload_extra=None):
    from src.state.venue_command_repo import insert_command
    snapshot_id = _ensure_snapshot(c, token_id=token_id)
    envelope = _make_envelope(
        token_id=token_id,
        side=side,
        price=price,
        size=size,
    )
    envelope_id = f"env-{command_id}"
    submission_envelope = envelope if intent_kind == "ENTRY" else None
    if submission_envelope is None:
        envelope_id = _ensure_envelope(
            c,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
        )
    if intent_kind == "ENTRY":
        decision_certificate_hash = decision_certificate_hash or f"cert-{command_id}"
        _ensure_entry_certificate(
            c,
            certificate_hash=decision_certificate_hash,
            envelope=envelope,
            payload_extra=decision_certificate_payload_extra,
        )
    insert_command(
        c,
        command_id=command_id,
        snapshot_id=snapshot_id,
        envelope_id=envelope_id,
        submission_envelope=submission_envelope,
        position_id=position_id,
        decision_id=decision_id,
        idempotency_key=idempotency_key,
        intent_kind=intent_kind,
        market_id=market_id,
        token_id=token_id,
        side=side,
        size=size,
        price=price,
        created_at=created_at,
        q_version=q_version,
        decision_certificate_hash=decision_certificate_hash,
    )


class TestAbsoluteLivePriceBand:
    @pytest.mark.parametrize(
        ("intent_kind", "side", "price"),
        (("ENTRY", "BUY", 0.05), ("EXIT", "SELL", 0.95)),
    )
    def test_inclusive_boundaries_persist(self, conn, intent_kind, side, price):
        _insert(conn, intent_kind=intent_kind, side=side, price=price)

        row = conn.execute(
            "SELECT intent_kind, side, price FROM venue_commands WHERE command_id = 'cmd-001'"
        ).fetchone()
        assert dict(row) == {
            "intent_kind": intent_kind,
            "side": side,
            "price": pytest.approx(price),
        }

    @pytest.mark.parametrize("order_type", ["FOK", "FAK"])
    def test_buy_taker_capable_envelope_persists_before_command(self, conn, order_type):
        from src.state.venue_command_repo import insert_command

        envelope = _make_envelope(token_id="tok-taker").with_updates(
            order_type=order_type, post_only=False
        )
        snapshot_id = _ensure_snapshot(conn, token_id="tok-taker")
        _ensure_entry_certificate(
            conn,
            certificate_hash="cert-taker",
            envelope=envelope,
        )

        insert_command(
            conn,
            command_id=f"cmd-taker-{order_type.lower()}",
            snapshot_id=snapshot_id,
            envelope_id=f"env-taker-{order_type.lower()}",
            submission_envelope=envelope,
            position_id=f"pos-taker-{order_type.lower()}",
            decision_id=f"dec-taker-{order_type.lower()}",
            idempotency_key=f"idem-taker-{order_type.lower()}",
            intent_kind="ENTRY",
            market_id="mkt-taker",
            token_id="tok-taker",
            side="BUY",
            size=10.0,
            price=0.5,
            created_at="2026-08-01T00:00:00Z",
            decision_certificate_hash="cert-taker",
        )

        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE command_id LIKE 'cmd-taker-%'"
        ).fetchone()[0] == 1

    def test_persisted_taker_exit_envelope_persists_before_command(
        self, conn
    ):
        from src.state.venue_command_repo import (
            insert_command,
            insert_submission_envelope,
        )

        envelope = _make_envelope(
            token_id="tok-exit-taker",
            side="SELL",
        ).with_updates(order_type="FAK", post_only=False)
        insert_submission_envelope(conn, envelope, envelope_id="env-exit-taker")
        snapshot_id = _ensure_snapshot(conn, token_id="tok-exit-taker")

        insert_command(
            conn,
            command_id="cmd-exit-taker",
            snapshot_id=snapshot_id,
            envelope_id="env-exit-taker",
            position_id="pos-exit-taker",
            decision_id="dec-exit-taker",
            idempotency_key="idem-exit-taker",
            intent_kind="EXIT",
            market_id="mkt-exit-taker",
            token_id="tok-exit-taker",
            side="SELL",
            size=10.0,
            price=0.5,
            created_at="2026-08-01T00:00:00Z",
        )

        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE command_id='cmd-exit-taker'"
        ).fetchone()[0] == 1

    @pytest.mark.parametrize("fill_price", ["0.049", "0.951", "0.999"])
    def test_out_of_band_trade_fact_persists_as_observed_truth(
        self, conn, fill_price
    ):
        from src.state.venue_command_repo import append_trade_fact

        _insert(conn)
        append_trade_fact(
            conn,
            trade_id=f"trade-{fill_price}",
            venue_order_id="order-band",
            command_id="cmd-001",
            state="CONFIRMED",
            filled_size="1",
            fill_price=fill_price,
            source="FAKE_VENUE",
            observed_at="2026-08-01T18:00:00Z",
            raw_payload_hash="a" * 64,
        )

        assert conn.execute(
            "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id = ?",
            (f"trade-{fill_price}",),
        ).fetchone()[0] == 1
        provenance = json.loads(
            conn.execute(
                """
                SELECT payload_json
                  FROM provenance_envelope_events
                 WHERE subject_type = 'trade' AND subject_id = ?
                """,
                (f"trade-{fill_price}",),
            ).fetchone()[0]
        )
        assert provenance["fill_price_in_live_order_band"] is False

    @pytest.mark.parametrize("fill_price", ["0", "-0.01", "1.001", "NaN"])
    def test_invalid_trade_fact_price_rejects_before_persistence(
        self, conn, fill_price
    ):
        from src.state.venue_command_repo import append_trade_fact

        _insert(conn)
        with pytest.raises(ValueError, match="positive finite fill economics"):
            append_trade_fact(
                conn,
                trade_id=f"trade-invalid-{fill_price}",
                venue_order_id="order-band",
                command_id="cmd-001",
                state="CONFIRMED",
                filled_size="1",
                fill_price=fill_price,
                source="FAKE_VENUE",
                observed_at="2026-08-01T18:00:00Z",
                raw_payload_hash="b" * 64,
            )

    @pytest.mark.parametrize(
        ("intent_kind", "side", "price"),
        (
            ("ENTRY", "BUY", 0.0499),
            ("ENTRY", "BUY", 0.998),
            ("EXIT", "SELL", 0.0499),
            ("EXIT", "SELL", 0.9501),
            ("DERISK", "SELL", 0.998),
        ),
    )
    def test_out_of_band_command_is_rejected_before_persistence(
        self, conn, intent_kind, side, price
    ):
        with pytest.raises(ValueError, match=r"inclusive \[0\.05, 0\.95\]"):
            _insert(conn, intent_kind=intent_kind, side=side, price=price)

        assert conn.execute("SELECT COUNT(*) FROM venue_commands").fetchone()[0] == 0


def _attribution_row(c, position_id: str):
    """position_decision_attribution row for position_id, or None.

    The table is created lazily (record_position_decision_attribution calls
    ensure_table on first write) — a fresh conn that never wrote an attribution
    fact legitimately has no such table yet, which reads identically to "no row".
    """
    try:
        return c.execute(
            "SELECT * FROM position_decision_attribution WHERE position_id = ?",
            (position_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _valid_execution_capability_payload() -> dict:
    """Minimal payload satisfying venue_command_repo._validate_entry_submit_payload
    for ENTRY SUBMIT_REQUESTED (shape mirrors the production builder in
    src/execution/executor.py::_build_execution_capability /
    _entry_economics_component). Fresh dict per call so callers can safely mutate."""
    return {
        "execution_capability": {
            "allowed": True,
            "components": [
                {
                    "component": "entry_economics",
                    "allowed": True,
                    "details": {
                        "q_live": 0.7,
                        "q_lcb_5pct": 0.6,
                        "expected_edge": 0.1,
                        "min_entry_price": 0.01,
                        "limit_price": 0.5,
                        "submit_edge": 0.1,
                        "expected_profit_usd": 1.0,
                        "min_expected_profit_usd": 0.01,
                        "submit_edge_density": 0.1,
                        "min_submit_edge_density": 0.01,
                        "shares": 10.0,
                        "qkernel_side": "buy_yes",
                    },
                },
                {"component": "entry_actionable_certificate", "allowed": True},
            ],
        },
    }


def _ensure_snapshot(
    c,
    *,
    token_id: str,
    snapshot_id: str | None = None,
    yes_token_id: str | None = None,
    no_token_id: str | None = None,
) -> str:
    from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    snapshot_id = snapshot_id or f"snap-{token_id}"
    yes_token_id = yes_token_id or token_id
    no_token_id = no_token_id or f"{token_id}-no"
    outcome_label = "YES" if token_id == yes_token_id else "NO"
    if get_snapshot(c, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        c,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-test",
            event_id="event-test",
            event_slug="event-test",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            selected_outcome_token_id=token_id,
            outcome_label=outcome_label,
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            fee_details={},
            token_map_raw={"YES": yes_token_id, "NO": no_token_id},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.51"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


def _ensure_envelope(
    c,
    *,
    token_id: str,
    envelope_id: str | None = None,
    side: str = "BUY",
    price: float | Decimal = 0.5,
    size: float | Decimal = 10.0,
) -> str:
    from src.state.venue_command_repo import insert_submission_envelope

    envelope = _make_envelope(
        token_id=token_id,
        side=side,
        price=price,
        size=size,
    )
    envelope_id = envelope_id or f"env-{token_id}-{side}-{envelope.price}-{envelope.size}"
    if c.execute(
        "SELECT 1 FROM venue_submission_envelopes WHERE envelope_id = ?",
        (envelope_id,),
    ).fetchone():
        return envelope_id
    insert_submission_envelope(
        c,
        envelope,
        envelope_id=envelope_id,
    )
    return envelope_id


def _make_envelope(
    *,
    token_id: str,
    side: str = "BUY",
    price: float | Decimal = 0.5,
    size: float | Decimal = 10.0,
    yes_token_id: str | None = None,
    no_token_id: str | None = None,
):
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    yes_token_id = yes_token_id or token_id
    no_token_id = no_token_id or f"{token_id}-no"
    outcome_label = "YES" if token_id == yes_token_id else "NO"
    return VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="test",
        host="https://clob-v2.polymarket.com",
        chain_id=137,
        funder_address="0xfunder",
        condition_id="condition-test",
        question_id="question-test",
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        selected_outcome_token_id=token_id,
        outcome_label=outcome_label,
        side=side,
        price=Decimal(str(price)),
        size=Decimal(str(size)),
        order_type="GTC",
        post_only=True,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        neg_risk=False,
        fee_details={},
        canonical_pre_sign_payload_hash="d" * 64,
        signed_order=None,
        signed_order_hash=None,
        raw_request_hash="e" * 64,
        raw_response_json=None,
        order_id=None,
        trade_ids=(),
        transaction_hashes=(),
        error_code=None,
        error_message=None,
        captured_at=_NOW.isoformat(),
    )


def _ensure_entry_certificate(
    c,
    *,
    certificate_hash: str,
    envelope,
    direction: str | None = None,
    payload_extra: dict | None = None,
) -> None:
    token_id = str(envelope.selected_outcome_token_id)
    direction = direction or (
        "buy_yes" if token_id == str(envelope.yes_token_id) else "buy_no"
    )
    payload = {
        "condition_id": str(envelope.condition_id),
        "token_id": token_id,
        "direction": direction,
    }
    payload.update(payload_extra or {})
    c.execute(
        """
        INSERT OR REPLACE INTO world.decision_certificates(
            certificate_hash, certificate_type, mode, verifier_status, payload_json
        ) VALUES (?, 'ActionableTradeCertificate', 'LIVE', 'VERIFIED', ?)
        """,
        (
            certificate_hash,
            json.dumps(payload),
        ),
    )


def _insert_global_auction_receipt(c) -> GlobalAuctionReceiptRef:
    from src.state.decision_chain import CycleArtifact, store_artifact

    summary = {
        "schema_version": 21,
        "selection_epoch_identity": "epoch-command",
        "selection_cut_at_utc": "2026-08-09T00:00:00+00:00",
        "decision_at_utc": "2026-08-09T00:00:01+00:00",
        "full_scope_identity": "scope-command",
        "book_epoch_identity": "book-command",
        "wealth_witness_identity": "wealth-command",
        "wealth_economic_identity": "wealth-economic-command",
        "winner_event_id": "event-command",
        "winner_candidate_id": "candidate-command",
        "winner_actuation_identity": "actuation-command",
        "payload_identity": "1" * 64,
        "decision_payload_identity": "2" * 64,
        "audit_context_sha256": "3" * 64,
        "book_native_side_states_sha256": "4" * 64,
        "candidate_evaluations_sha256": "5" * 64,
        "buy_minimum_marketable_repairs_sha256": "6" * 64,
        "holding_auction_coverage_sha256": "7" * 64,
        "receipt_hash": "8" * 64,
    }
    binding_fields = (
        "schema_version",
        "selection_epoch_identity",
        "selection_cut_at_utc",
        "decision_at_utc",
        "full_scope_identity",
        "book_epoch_identity",
        "wealth_witness_identity",
        "wealth_economic_identity",
        "winner_event_id",
        "winner_candidate_id",
        "winner_actuation_identity",
        "payload_identity",
        "decision_payload_identity",
        "audit_context_sha256",
        "book_native_side_states_sha256",
        "candidate_evaluations_sha256",
        "buy_minimum_marketable_repairs_sha256",
        "holding_auction_coverage_sha256",
    )
    binding_payload = {field: summary[field] for field in binding_fields}
    binding_payload["binding_version"] = "global-auction-execution-binding-v1"
    summary["execution_binding_hash"] = hashlib.sha256(
        json.dumps(
            binding_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    summary_for_hash = dict(summary)
    summary_for_hash.pop("artifact_summary_hash", None)
    summary["artifact_summary_hash"] = hashlib.sha256(
        json.dumps(
            summary_for_hash,
            default=str,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    mode = "global_single_order_auction"
    row_id = store_artifact(
        c,
        CycleArtifact(
            mode=mode,
            started_at="2026-08-09T00:00:00+00:00",
            completed_at="2026-08-09T00:00:01+00:00",
            skipped_reason="",
            summary=summary,
        ),
    )
    assert row_id is not None
    return GlobalAuctionReceiptRef(
        decision_log_id=row_id,
        decision_log_mode=mode,
        receipt_hash=str(summary["receipt_hash"]),
        execution_binding_hash=str(summary["execution_binding_hash"]),
        artifact_summary_hash=str(summary["artifact_summary_hash"]),
        schema_version=21,
        winner_event_id="event-command",
        winner_candidate_id="candidate-command",
        winner_actuation_identity="actuation-command",
        selection_epoch_identity="epoch-command",
    )


def _global_certificate_payload(ref: GlobalAuctionReceiptRef) -> dict:
    receipt_payload = ref.as_payload()
    return {
        "global_auction_receipt": receipt_payload,
        "qkernel_execution_economics": {
            "global_actuation_identity": ref.winner_actuation_identity,
            "global_winner_event_id": ref.winner_event_id,
            "global_candidate_id": ref.winner_candidate_id,
            "global_selection_epoch_identity": ref.selection_epoch_identity,
            "global_auction_receipt": receipt_payload,
        },
    }


def _global_sell_closure(
    ref: GlobalAuctionReceiptRef,
    *,
    position_id: str = "pos-sell-closure",
    condition_id: str = "condition-test",
    token_id: str = "tok-sell-closure",
    execution_mode: str = "MAKER_REST",
    **overrides,
) -> GlobalSellReceiptClosure:
    fields = {
        "receipt_ref": ref,
        "position_id": position_id,
        "condition_id": condition_id,
        "token_id": token_id,
        "action": "SELL",
        "execution_mode": execution_mode,
        "winner_event_id": ref.winner_event_id,
        "winner_candidate_id": ref.winner_candidate_id,
        "winner_actuation_identity": ref.winner_actuation_identity,
        "selection_epoch_identity": ref.selection_epoch_identity,
    }
    fields.update(overrides)
    return GlobalSellReceiptClosure(**fields)


class TestGlobalSellReceiptClosure:
    def _insert_closure_command(self, conn, *, closure, envelope=None, **kwargs):
        from src.state.venue_command_repo import insert_command

        command_id = kwargs.pop("command_id", "cmd-global-sell-closure")
        envelope_id = f"pre-submit:{command_id}"
        token_id = str(kwargs.pop("token_id", closure.token_id))
        position_id = str(kwargs.pop("position_id", closure.position_id))
        snapshot_id = _ensure_snapshot(conn, token_id=token_id)
        envelope = envelope or _make_envelope(token_id=token_id, side="SELL")
        insert_command(
            conn,
            command_id=command_id,
            snapshot_id=snapshot_id,
            envelope_id=envelope_id,
            submission_envelope=envelope,
            position_id=position_id,
            decision_id="decision-global-sell-closure",
            idempotency_key=kwargs.pop("idempotency_key", "idem-global-sell-closure"),
            intent_kind=kwargs.pop("intent_kind", "EXIT"),
            market_id="market-global-sell-closure",
            token_id=token_id,
            side=kwargs.pop("side", "SELL"),
            size=10.0,
            price=0.5,
            created_at="2026-08-09T00:00:00Z",
            global_sell_receipt_closure=closure,
            **kwargs,
        )
        return command_id, envelope_id

    @staticmethod
    def _assert_zero_rows(conn, *, command_id: str, envelope_id: str) -> None:
        for table, column, value in (
            ("venue_commands", "command_id", command_id),
            ("venue_command_events", "command_id", command_id),
            ("provenance_envelope_events", "subject_id", command_id),
            ("venue_submission_envelopes", "envelope_id", envelope_id),
        ):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} = ?",
                (value,),
            ).fetchone()[0] == 0

    def test_success_persists_exact_closure_event_and_provenance(self, conn):
        ref = _insert_global_auction_receipt(conn)
        closure = _global_sell_closure(ref)
        self._insert_closure_command(conn, closure=closure)
        expected = {"global_sell_receipt_closure": closure.as_payload()}
        event = conn.execute(
            "SELECT payload_json FROM venue_command_events WHERE command_id = ?",
            ("cmd-global-sell-closure",),
        ).fetchone()
        provenance = conn.execute(
            """
            SELECT payload_json FROM provenance_envelope_events
             WHERE subject_type = 'command' AND subject_id = ?
               AND event_type = 'INTENT_CREATED'
            """,
            ("cmd-global-sell-closure",),
        ).fetchone()
        assert json.loads(event["payload_json"]) == expected
        provenance_payload = json.loads(provenance["payload_json"])
        assert provenance_payload["global_sell_receipt_closure"] == expected[
            "global_sell_receipt_closure"
        ]
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE command_id = ?",
            ("cmd-global-sell-closure",),
        ).fetchone()[0] == 1
        assert GlobalSellReceiptClosure.from_payload(
            json.loads(event["payload_json"])["global_sell_receipt_closure"]
        ) == closure

    def test_taker_limit_closure_persists_with_fak_mapping(self, conn):
        ref = _insert_global_auction_receipt(conn)
        closure = _global_sell_closure(
            ref,
            token_id="tok-sell-taker",
            execution_mode="TAKER_LIMIT",
        )
        envelope = _make_envelope(
            token_id="tok-sell-taker",
            side="SELL",
        ).with_updates(order_type="FAK", post_only=False)
        command_id, _ = self._insert_closure_command(
            conn,
            closure=closure,
            envelope=envelope,
            command_id="cmd-global-sell-taker",
            idempotency_key="idem-global-sell-taker",
        )
        row = conn.execute(
            "SELECT intent_kind, side FROM venue_commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        assert tuple(row) == ("EXIT", "SELL")

    def test_closure_persisted_envelope_sqlite_row_binding(self, conn):
        from src.state.venue_command_repo import insert_command

        ref = _insert_global_auction_receipt(conn)
        closure = _global_sell_closure(ref)
        snapshot_id = _ensure_snapshot(conn, token_id=closure.token_id)
        envelope_id = _ensure_envelope(
            conn,
            token_id=closure.token_id,
            envelope_id="persisted-global-sell-envelope",
            side="SELL",
        )
        insert_command(
            conn,
            command_id="cmd-global-sell-persisted",
            snapshot_id=snapshot_id,
            envelope_id=envelope_id,
            position_id=closure.position_id,
            decision_id="decision-global-sell-persisted",
            idempotency_key="idem-global-sell-persisted",
            intent_kind="EXIT",
            market_id="market-global-sell-persisted",
            token_id=closure.token_id,
            side="SELL",
            size=10.0,
            price=0.5,
            created_at="2026-08-09T00:00:00Z",
            global_sell_receipt_closure=closure,
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE command_id = ?",
            ("cmd-global-sell-persisted",),
        ).fetchone()[0] == 1

    def test_closure_rejects_string_post_only_and_accepts_sqlite_zero(self, conn):
        ref = _insert_global_auction_receipt(conn)
        closure = _global_sell_closure(ref, execution_mode="TAKER_LIMIT")
        base = {
            "condition_id": closure.condition_id,
            "selected_outcome_token_id": closure.token_id,
            "side": "SELL",
            "order_type": "FAK",
        }
        with pytest.raises(ValueError, match="GLOBAL_SELL_RECEIPT_POST_ONLY_INVALID"):
            closure.assert_matches_command(
                position_id=closure.position_id,
                token_id=closure.token_id,
                side="SELL",
                envelope={**base, "post_only": "0"},
            )
        closure.assert_matches_command(
            position_id=closure.position_id,
            token_id=closure.token_id,
            side="SELL",
            envelope={**base, "post_only": 0},
        )
        maker = _global_sell_closure(ref, execution_mode="MAKER_REST")
        maker.assert_matches_command(
            position_id=maker.position_id,
            token_id=maker.token_id,
            side="SELL",
            envelope={
                "condition_id": maker.condition_id,
                "selected_outcome_token_id": maker.token_id,
                "side": "SELL",
                "order_type": "GTC",
                "post_only": 1,
            },
        )

    @pytest.mark.parametrize(
        "fault",
        (
            "deleted",
            "mutated",
            "wrong_winner",
            "wrong_position",
            "wrong_token",
            "wrong_condition",
            "wrong_action",
            "wrong_mode",
            "non_exit",
        ),
    )
    def test_closure_rejections_leave_zero_command_event_provenance_envelope(
        self, conn, fault
    ):
        ref = _insert_global_auction_receipt(conn)
        if fault == "deleted":
            conn.execute("DELETE FROM decision_log WHERE id = ?", (ref.decision_log_id,))
        elif fault == "mutated":
            row = conn.execute(
                "SELECT artifact_json FROM decision_log WHERE id = ?",
                (ref.decision_log_id,),
            ).fetchone()
            artifact = json.loads(row[0])
            artifact["summary"]["winner_candidate_id"] = "mutated"
            conn.execute(
                "UPDATE decision_log SET artifact_json = ? WHERE id = ?",
                (json.dumps(artifact), ref.decision_log_id),
            )
        closure = _global_sell_closure(ref)
        kwargs = {"command_id": f"cmd-global-sell-{fault}", "idempotency_key": f"idem-global-sell-{fault}"}
        envelope_id = f"pre-submit:{kwargs['command_id']}"
        envelope = None
        if fault == "wrong_winner":
            with pytest.raises(ValueError, match="GLOBAL_SELL_RECEIPT"):
                _global_sell_closure(ref, winner_candidate_id="wrong")
            self._assert_zero_rows(
                conn,
                command_id=kwargs["command_id"],
                envelope_id=envelope_id,
            )
            return
        elif fault == "wrong_position":
            kwargs["position_id"] = "other-position"
        elif fault == "wrong_token":
            kwargs["token_id"] = "other-token"
        elif fault == "wrong_condition":
            closure = _global_sell_closure(ref, condition_id="other-condition")
        elif fault == "wrong_action":
            with pytest.raises(ValueError, match="GLOBAL_SELL_RECEIPT_ACTION_INVALID"):
                _global_sell_closure(ref, action="BUY")
            self._assert_zero_rows(
                conn,
                command_id=kwargs["command_id"],
                envelope_id=envelope_id,
            )
            return
        elif fault == "wrong_mode":
            closure = _global_sell_closure(ref, execution_mode="TAKER_LIMIT")
        elif fault == "non_exit":
            kwargs["intent_kind"] = "ENTRY"
            envelope = _make_envelope(token_id=closure.token_id, side="BUY")
        with pytest.raises(ValueError, match="GLOBAL_SELL_RECEIPT"):
            self._insert_closure_command(conn, closure=closure, envelope=envelope, **kwargs)
        self._assert_zero_rows(
            conn,
            command_id=kwargs["command_id"],
            envelope_id=envelope_id,
        )


def _signed_envelope(*, order_id="0xorder", token_id="tok-001", side="BUY"):
    from src.contracts.venue_submission_envelope import VenueSubmissionEnvelope

    signed_order = b"canonical-signed-order"
    return VenueSubmissionEnvelope(
        sdk_package="py-clob-client-v2",
        sdk_version="test",
        host="https://clob-v2.polymarket.com",
        chain_id=137,
        funder_address="0xfunder",
        condition_id="condition-test",
        question_id="question-test",
        yes_token_id=token_id,
        no_token_id=f"{token_id}-no",
        selected_outcome_token_id=token_id,
        outcome_label="YES",
        side=side,
        price=Decimal("0.5"),
        size=Decimal("10"),
        order_type="GTC",
        post_only=True,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("0.01"),
        neg_risk=False,
        fee_details={},
        canonical_pre_sign_payload_hash="d" * 64,
        signed_order=signed_order,
        signed_order_hash=hashlib.sha256(signed_order).hexdigest(),
        raw_request_hash="e" * 64,
        raw_response_json=None,
        order_id=order_id,
        trade_ids=(),
        transaction_hashes=(),
        error_code=None,
        error_message=None,
        captured_at=_NOW.isoformat(),
    )


def test_bind_signed_submission_identity_is_durable_before_ack(conn):
    from src.state.venue_command_repo import bind_signed_submission_identity

    _insert(conn)
    conn.execute(
        "UPDATE venue_commands SET state='SUBMITTING' WHERE command_id='cmd-001'"
    )
    envelope = _signed_envelope()

    envelope_id = bind_signed_submission_identity(
        conn,
        command_id="cmd-001",
        envelope=envelope,
    )

    command = conn.execute(
        "SELECT state, venue_order_id FROM venue_commands WHERE command_id='cmd-001'"
    ).fetchone()
    assert dict(command) == {"state": "SUBMITTING", "venue_order_id": "0xorder"}
    persisted = conn.execute(
        """
        SELECT order_id, signed_order_hash
          FROM venue_submission_envelopes
         WHERE envelope_id = ?
        """,
        (envelope_id,),
    ).fetchone()
    assert persisted["order_id"] == "0xorder"
    assert persisted["signed_order_hash"] == envelope.signed_order_hash
    provenance = conn.execute(
        """
        SELECT event_type, payload_json
          FROM provenance_envelope_events
         WHERE subject_type='command' AND subject_id='cmd-001'
         ORDER BY local_sequence DESC
         LIMIT 1
        """
    ).fetchone()
    assert provenance["event_type"] == "SIGNED_IDENTITY_PERSISTED_PRE_POST"
    assert '"side_effect_boundary_crossed":false' in provenance["payload_json"]


def test_executor_pre_post_receipt_is_backed_by_committed_canonical_readback(conn):
    from src.execution.executor import _persist_signed_submission_identity_before_post

    _insert(conn)
    conn.execute(
        "UPDATE venue_commands SET state='SUBMITTING' WHERE command_id='cmd-001'"
    )
    conn.commit()
    envelope = _signed_envelope()

    receipt = _persist_signed_submission_identity_before_post(
        conn,
        envelope,
        command_id="cmd-001",
    )

    assert receipt.command_id == "cmd-001"
    assert receipt.order_id == envelope.order_id
    assert receipt.signed_order_hash == envelope.signed_order_hash
    assert receipt.canonical_pre_sign_payload_hash == (
        envelope.canonical_pre_sign_payload_hash
    )
    assert receipt.raw_request_hash == envelope.raw_request_hash
    persisted = conn.execute(
        """
        SELECT command.venue_order_id, signed.envelope_id
          FROM venue_commands command
          JOIN venue_submission_envelopes signed
            ON signed.envelope_id = ?
           AND signed.order_id = command.venue_order_id
         WHERE command.command_id = ?
        """,
        (receipt.envelope_id, receipt.command_id),
    ).fetchone()
    assert tuple(persisted) == (envelope.order_id, receipt.envelope_id)


def test_bind_signed_submission_identity_rejects_wrong_order_economics(conn):
    from src.state.venue_command_repo import bind_signed_submission_identity

    _insert(conn)
    conn.execute(
        "UPDATE venue_commands SET state='SUBMITTING' WHERE command_id='cmd-001'"
    )
    bad = _signed_envelope().with_updates(price=Decimal("0.49"))

    with pytest.raises(ValueError, match="does not match canonical command"):
        bind_signed_submission_identity(conn, command_id="cmd-001", envelope=bad)

    row = conn.execute(
        "SELECT venue_order_id FROM venue_commands WHERE command_id='cmd-001'"
    ).fetchone()
    assert row[0] is None


def test_bind_signed_submission_identity_retry_is_strict_noop(conn):
    from src.state.venue_command_repo import bind_signed_submission_identity

    _insert(conn)
    conn.execute(
        "UPDATE venue_commands SET state='SUBMITTING' WHERE command_id='cmd-001'"
    )
    envelope = _signed_envelope()
    first_id = bind_signed_submission_identity(
        conn,
        command_id="cmd-001",
        envelope=envelope,
    )
    second_id = bind_signed_submission_identity(
        conn,
        command_id="cmd-001",
        envelope=envelope.with_updates(
            captured_at="2026-04-26T00:00:01+00:00"
        ),
    )

    assert second_id == first_id
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_submission_envelopes WHERE order_id='0xorder'"
    ).fetchone()[0] == 1
    assert conn.execute(
        """
        SELECT COUNT(*)
          FROM provenance_envelope_events
         WHERE subject_type='command'
           AND subject_id='cmd-001'
           AND event_type='SIGNED_IDENTITY_PERSISTED_PRE_POST'
        """
    ).fetchone()[0] == 1


@pytest.mark.parametrize("terminal_state", ["MATCHED", "CANCEL_CONFIRMED", "EXPIRED", "VENUE_WIPED"])
def test_append_order_fact_preserves_prior_terminal_zero_remainder(conn, terminal_state):
    from src.state.venue_command_repo import append_order_fact

    _insert(conn, command_id="cmd-terminal-order", size=181.16)

    first_id = append_order_fact(
        conn,
        venue_order_id="ord-terminal-order",
        command_id="cmd-terminal-order",
        state=terminal_state,
        remaining_size="0",
        matched_size="100",
        source="REST",
        observed_at="2026-05-21T00:00:00Z",
        raw_payload_hash="1" * 64,
        raw_payload_json={"status": terminal_state, "remaining_size": "0", "matched_size": "100"},
    )

    second_id = append_order_fact(
        conn,
        venue_order_id="ord-terminal-order",
        command_id="cmd-terminal-order",
        state="PARTIALLY_MATCHED",
        remaining_size="81.16",
        matched_size="100",
        source="WS_USER",
        observed_at="2026-05-21T00:01:00Z",
        raw_payload_hash="2" * 64,
        raw_payload_json={
            "status": "PARTIALLY_MATCHED",
            "remaining_size": "81.16",
            "matched_size": "100",
        },
    )

    rows = conn.execute(
        """
        SELECT fact_id, state, remaining_size, matched_size, source
          FROM venue_order_facts
         WHERE venue_order_id = ?
         ORDER BY local_sequence, fact_id
        """,
        ("ord-terminal-order",),
    ).fetchall()

    assert second_id == first_id
    assert [dict(row) for row in rows] == [
        {
            "fact_id": first_id,
            "state": terminal_state,
            "remaining_size": "0",
            "matched_size": "100",
            "source": "REST",
        }
    ]


def test_append_order_fact_preserves_prior_cancel_confirmed_partial_remainder(conn):
    from src.state.venue_command_repo import append_order_fact

    _insert(conn, command_id="cmd-cancelled-partial", size=25.07)

    first_id = append_order_fact(
        conn,
        venue_order_id="ord-cancelled-partial",
        command_id="cmd-cancelled-partial",
        state="CANCEL_CONFIRMED",
        remaining_size="15.07",
        matched_size="10",
        source="WS_USER",
        observed_at="2026-07-02T13:47:22.683000+00:00",
        raw_payload_hash="5" * 64,
        raw_payload_json={
            "status": "CANCELED",
            "remaining_size": "15.07",
            "matched_size": "10",
        },
    )

    second_id = append_order_fact(
        conn,
        venue_order_id="ord-cancelled-partial",
        command_id="cmd-cancelled-partial",
        state="PARTIALLY_MATCHED",
        remaining_size="15.07",
        matched_size="10",
        source="REST",
        observed_at="2026-07-02T13:38:59.763701+00:00",
        raw_payload_hash="6" * 64,
        raw_payload_json={
            "reason": "m5_exchange_reconcile_entry_fill_order_fact",
            "remaining_size": "15.07",
            "matched_size": "10",
        },
    )

    rows = conn.execute(
        """
        SELECT fact_id, state, remaining_size, matched_size, source
          FROM venue_order_facts
         WHERE venue_order_id = ?
         ORDER BY local_sequence, fact_id
        """,
        ("ord-cancelled-partial",),
    ).fetchall()

    assert second_id == first_id
    assert [dict(row) for row in rows] == [
        {
            "fact_id": first_id,
            "state": "CANCEL_CONFIRMED",
            "remaining_size": "15.07",
            "matched_size": "10",
            "source": "WS_USER",
        }
    ]


def test_append_order_fact_terminal_preservation_is_command_scoped(conn):
    from src.state.venue_command_repo import append_order_fact

    _insert(conn, command_id="cmd-terminal-order", size=181.16)
    _insert(
        conn,
        command_id="cmd-other-order",
        position_id="pos-other-order",
        decision_id="dec-other-order",
        idempotency_key="idem-other-order",
        token_id="tok-other-order",
        size=181.16,
    )

    first_id = append_order_fact(
        conn,
        venue_order_id="ord-shared-order-id",
        command_id="cmd-terminal-order",
        state="EXPIRED",
        remaining_size="0",
        matched_size="100",
        source="REST",
        observed_at="2026-05-21T00:00:00Z",
        raw_payload_hash="3" * 64,
        raw_payload_json={"status": "EXPIRED", "remaining_size": "0", "matched_size": "100"},
    )

    second_id = append_order_fact(
        conn,
        venue_order_id="ord-shared-order-id",
        command_id="cmd-other-order",
        state="PARTIALLY_MATCHED",
        remaining_size="81.16",
        matched_size="100",
        source="WS_USER",
        observed_at="2026-05-21T00:01:00Z",
        raw_payload_hash="4" * 64,
        raw_payload_json={"status": "PARTIALLY_MATCHED"},
    )

    rows = conn.execute(
        """
        SELECT fact_id, command_id, state, remaining_size, matched_size
          FROM venue_order_facts
         WHERE venue_order_id = ?
         ORDER BY local_sequence, fact_id
        """,
        ("ord-shared-order-id",),
    ).fetchall()

    assert second_id != first_id
    assert [dict(row) for row in rows] == [
        {
            "fact_id": first_id,
            "command_id": "cmd-terminal-order",
            "state": "EXPIRED",
            "remaining_size": "0",
            "matched_size": "100",
        },
        {
            "fact_id": second_id,
            "command_id": "cmd-other-order",
            "state": "PARTIALLY_MATCHED",
            "remaining_size": "81.16",
            "matched_size": "100",
        },
    ]


# ---------------------------------------------------------------------------
# Test 1: insert_command atomicity
# ---------------------------------------------------------------------------

class TestInsertCommandAtomicWithIntentCreatedEvent:
    def test_both_rows_inserted(self, conn):
        from src.state.venue_command_repo import get_command, list_events

        _insert(conn)

        cmd = get_command(conn, "cmd-001")
        assert cmd is not None
        assert cmd["state"] == "INTENT_CREATED"
        assert cmd["command_id"] == "cmd-001"
        assert cmd["idempotency_key"] == "idem-001"

        events = list_events(conn, "cmd-001")
        assert len(events) == 1
        assert events[0]["event_type"] == "INTENT_CREATED"
        assert events[0]["state_after"] == "INTENT_CREATED"
        assert events[0]["sequence_no"] == 1

        # last_event_id must point to the INTENT_CREATED event
        assert cmd["last_event_id"] == events[0]["event_id"]

    def test_rollback_on_mid_transaction_failure(self, conn):
        """If the events INSERT fails, the command INSERT must also roll back."""
        from src.state.venue_command_repo import insert_command

        # Sabotage: drop the events table so the second INSERT raises
        conn.execute("DROP TABLE venue_command_events")
        conn.commit()

        with pytest.raises(Exception):
            snapshot_id = _ensure_snapshot(conn, token_id="tok-001")
            envelope = _make_envelope(token_id="tok-001", price=0.5, size=10.0)
            _ensure_entry_certificate(
                conn, certificate_hash="cert-fail", envelope=envelope
            )
            insert_command(
                conn,
                command_id="cmd-fail",
                snapshot_id=snapshot_id,
                envelope_id="env-fail",
                submission_envelope=envelope,
                position_id="pos-001",
                decision_id="dec-001",
                idempotency_key="idem-fail",
                intent_kind="ENTRY",
                market_id="mkt-001",
                token_id="tok-001",
                side="BUY",
                size=10.0,
                price=0.5,
                created_at="2026-04-26T00:00:00Z",
                decision_certificate_hash="cert-fail",
            )

        # The command row must NOT exist
        row = conn.execute(
            "SELECT command_id FROM venue_commands WHERE command_id = 'cmd-fail'"
        ).fetchone()
        assert row is None, "command row should have been rolled back"


class TestPositionDecisionAttributionAppendHook:
    """LX-E packet (2026-07-13): insert_command appends the permanent
    position -> decision_certificate_hash fact atomically with the command."""

    def test_global_receipt_closes_before_command_and_attribution(self, conn):
        receipt_ref = _insert_global_auction_receipt(conn)

        _insert(
            conn,
            command_id="cmd-global-receipt",
            position_id="pos-global-receipt",
            idempotency_key="idem-global-receipt",
            decision_certificate_hash="cert-global-receipt",
            decision_certificate_payload_extra=_global_certificate_payload(
                receipt_ref
            ),
        )

        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE command_id = ?",
            ("cmd-global-receipt",),
        ).fetchone()[0] == 1
        assert _attribution_row(conn, "pos-global-receipt") is not None

    @pytest.mark.parametrize(
        "fault",
        (
            "missing_ref",
            "deleted",
            "mutated",
            "missing_binding_field",
            "nonbinding_mutation",
        ),
    )
    def test_global_receipt_fault_rolls_back_command_and_attribution(
        self,
        conn,
        fault,
    ):
        receipt_ref = _insert_global_auction_receipt(conn)
        payload_extra = _global_certificate_payload(receipt_ref)
        if fault == "missing_ref":
            payload_extra.pop("global_auction_receipt")
        elif fault == "deleted":
            conn.execute(
                "DELETE FROM decision_log WHERE id = ?",
                (receipt_ref.decision_log_id,),
            )
        else:
            row = conn.execute(
                "SELECT artifact_json FROM decision_log WHERE id = ?",
                (receipt_ref.decision_log_id,),
            ).fetchone()
            artifact = json.loads(row[0])
            if fault == "mutated":
                artifact["summary"]["winner_candidate_id"] = "mutated-candidate"
            elif fault == "missing_binding_field":
                artifact["summary"].pop("wealth_economic_identity")
                artifact["summary"]["execution_binding_hash"] = "f" * 64
            else:
                artifact["summary"]["no_trade_reason"] = "forged-reason"
            conn.execute(
                "UPDATE decision_log SET artifact_json = ? WHERE id = ?",
                (json.dumps(artifact), receipt_ref.decision_log_id),
            )

        with pytest.raises(ValueError, match="global auction receipt"):
            _insert(
                conn,
                command_id=f"cmd-global-{fault}",
                position_id=f"pos-global-{fault}",
                idempotency_key=f"idem-global-{fault}",
                decision_certificate_hash=f"cert-global-{fault}",
                decision_certificate_payload_extra=payload_extra,
            )

        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE command_id = ?",
            (f"cmd-global-{fault}",),
        ).fetchone()[0] == 0
        assert _attribution_row(conn, f"pos-global-{fault}") is None

    def test_hash_given_writes_attribution_row(self, conn):
        _insert(
            conn,
            command_id="cmd-attr-1",
            position_id="pos-attr-1",
            idempotency_key="idem-attr-1",
            decision_certificate_hash="cert-hash-1",
        )
        row = conn.execute(
            """SELECT position_id, command_id, decision_certificate_hash, resolution,
                      source, intent_kind
               FROM position_decision_attribution WHERE position_id = 'pos-attr-1'"""
        ).fetchone()
        assert row is not None
        assert row[0] == "pos-attr-1"
        assert row[1] == "cmd-attr-1"
        assert row[2] == "cert-hash-1"
        assert row[3] == "ATTRIBUTED"
        assert row[4] == "LIVE_DECISION"
        assert row[5] == "ENTRY"

    def test_later_command_inherits_position_entry_certificate(self, conn):
        _insert(
            conn,
            command_id="cmd-attr-2-entry",
            position_id="pos-attr-2",
            idempotency_key="idem-attr-2-entry",
            decision_certificate_hash="cert-entry",
        )
        _insert(
            conn,
            command_id="cmd-attr-2",
            position_id="pos-attr-2",
            idempotency_key="idem-attr-2",
            intent_kind="EXIT",
            decision_certificate_hash=None,
        )
        rows = conn.execute(
            "SELECT command_id, decision_certificate_hash FROM "
            "position_decision_attribution WHERE position_id='pos-attr-2' "
            "ORDER BY command_id"
        ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            ("cmd-attr-2", "cert-entry"),
            ("cmd-attr-2-entry", "cert-entry"),
        ]

    def test_distinct_commands_keep_distinct_exact_attributions(self, conn):
        _insert(
            conn,
            command_id="cmd-attr-3a",
            position_id="pos-attr-3",
            idempotency_key="idem-attr-3a",
            decision_certificate_hash="cert-first",
        )
        _insert(
            conn,
            command_id="cmd-attr-3b",
            position_id="pos-attr-3",
            idempotency_key="idem-attr-3b",
            decision_certificate_hash="cert-second",
        )
        rows = conn.execute(
            "SELECT decision_certificate_hash FROM position_decision_attribution "
            "WHERE position_id = 'pos-attr-3'"
        ).fetchall()
        assert [row[0] for row in rows] == ["cert-first", "cert-second"]

    def test_exit_without_certificate_is_always_journaled_unattributable(
        self, conn, monkeypatch
    ):
        monkeypatch.delenv("ZEUS_ENTRY_Q_VERSION_STRICT", raising=False)
        monkeypatch.delenv("ZEUS_MODE", raising=False)
        monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
        _insert(
            conn,
            command_id="cmd-orphan-exit",
            position_id="pos-orphan-exit",
            idempotency_key="idem-orphan-exit",
            intent_kind="EXIT",
        )
        row = conn.execute(
            "SELECT resolution, resolution_reason FROM position_decision_attribution "
            "WHERE command_id='cmd-orphan-exit'"
        ).fetchone()
        assert tuple(row) == (
            "UNATTRIBUTABLE",
            "legacy_position_certificate_missing_or_ambiguous",
        )

    def test_rollback_on_mid_transaction_failure_also_rolls_back_attribution(self, conn):
        """The attribution write shares the command's SAVEPOINT — if the command
        insert's transaction rolls back, the attribution row must not survive."""
        from src.state.venue_command_repo import insert_command

        conn.execute("DROP TABLE venue_command_events")
        conn.commit()

        with pytest.raises(Exception):
            snapshot_id = _ensure_snapshot(conn, token_id="tok-attr-4")
            envelope = _make_envelope(token_id="tok-attr-4", price=0.5, size=10.0)
            _ensure_entry_certificate(
                conn,
                certificate_hash="cert-should-not-survive",
                envelope=envelope,
            )
            insert_command(
                conn,
                command_id="cmd-attr-4",
                snapshot_id=snapshot_id,
                envelope_id="env-attr-4",
                submission_envelope=envelope,
                position_id="pos-attr-4",
                decision_id="dec-001",
                idempotency_key="idem-attr-4",
                intent_kind="ENTRY",
                market_id="mkt-001",
                token_id="tok-attr-4",
                side="BUY",
                size=10.0,
                price=0.5,
                created_at="2026-04-26T00:00:00Z",
                decision_certificate_hash="cert-should-not-survive",
            )

        assert _attribution_row(conn, "pos-attr-4") is None, (
            "attribution row should have been rolled back with the command"
        )


class TestEntryCertificateReferentialClosure:
    def test_no_token_entry_persists_with_buy_no_certificate(self, conn):
        from src.state.venue_command_repo import insert_command

        yes_token_id = "tok-real-yes"
        no_token_id = "tok-real-no"
        snapshot_id = _ensure_snapshot(
            conn,
            token_id=no_token_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        )
        envelope = _make_envelope(
            token_id=no_token_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        )
        _ensure_entry_certificate(
            conn,
            certificate_hash="cert-real-no",
            envelope=envelope,
        )

        insert_command(
            conn,
            command_id="cmd-real-no",
            snapshot_id=snapshot_id,
            envelope_id="env-real-no",
            submission_envelope=envelope,
            position_id="pos-real-no",
            decision_id="dec-real-no",
            idempotency_key="idem-real-no",
            intent_kind="ENTRY",
            market_id="mkt-real-no",
            token_id=no_token_id,
            side="BUY",
            size=10.0,
            price=0.5,
            created_at="2026-07-29T00:00:00Z",
            decision_certificate_hash="cert-real-no",
        )

        command = conn.execute(
            "SELECT token_id, envelope_id FROM venue_commands "
            "WHERE command_id='cmd-real-no'"
        ).fetchone()
        assert tuple(command) == (no_token_id, "env-real-no")
        attribution = _attribution_row(conn, "pos-real-no")
        assert attribution["decision_certificate_hash"] == "cert-real-no"

    @pytest.mark.parametrize(
        ("selected_token_id", "certificate_direction"),
        (
            ("tok-pair-yes", "buy_no"),
            ("tok-pair-no", "buy_yes"),
        ),
    )
    def test_yes_no_direction_mismatch_rejects(
        self,
        conn,
        selected_token_id,
        certificate_direction,
    ):
        from src.state.venue_command_repo import insert_command

        yes_token_id = "tok-pair-yes"
        no_token_id = "tok-pair-no"
        snapshot_id = _ensure_snapshot(
            conn,
            token_id=selected_token_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        )
        envelope = _make_envelope(
            token_id=selected_token_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        )
        certificate_hash = f"cert-mismatch-{selected_token_id}"
        _ensure_entry_certificate(
            conn,
            certificate_hash=certificate_hash,
            envelope=envelope,
            direction=certificate_direction,
        )

        with pytest.raises(ValueError, match="identity does not match"):
            insert_command(
                conn,
                command_id=f"cmd-mismatch-{selected_token_id}",
                snapshot_id=snapshot_id,
                envelope_id=f"env-mismatch-{selected_token_id}",
                submission_envelope=envelope,
                position_id=f"pos-mismatch-{selected_token_id}",
                decision_id=f"dec-mismatch-{selected_token_id}",
                idempotency_key=f"idem-mismatch-{selected_token_id}",
                intent_kind="ENTRY",
                market_id="mkt-pair",
                token_id=selected_token_id,
                side="BUY",
                size=10.0,
                price=0.5,
                created_at="2026-07-29T00:00:00Z",
                decision_certificate_hash=certificate_hash,
            )

        assert conn.execute(
            "SELECT COUNT(*) FROM venue_submission_envelopes "
            "WHERE envelope_id = ?",
            (f"env-mismatch-{selected_token_id}",),
        ).fetchone()[0] == 0

    def test_dangling_hash_rolls_back_entire_admission(self, conn):
        from src.state.venue_command_repo import insert_command

        snapshot_id = _ensure_snapshot(conn, token_id="tok-dangling")
        envelope = _make_envelope(token_id="tok-dangling")
        with pytest.raises(ValueError, match="exact LIVE VERIFIED"):
            insert_command(
                conn,
                command_id="cmd-dangling",
                snapshot_id=snapshot_id,
                envelope_id="env-dangling",
                submission_envelope=envelope,
                position_id="pos-dangling",
                decision_id="dec-dangling",
                idempotency_key="idem-dangling",
                intent_kind="ENTRY",
                market_id="mkt-dangling",
                token_id="tok-dangling",
                side="BUY",
                size=10.0,
                price=0.5,
                created_at="2026-07-29T00:00:00Z",
                decision_certificate_hash="missing-certificate-hash",
            )

        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE command_id='cmd-dangling'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_submission_envelopes "
            "WHERE envelope_id='env-dangling'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_command_events "
            "WHERE command_id='cmd-dangling'"
        ).fetchone()[0] == 0
        assert _attribution_row(conn, "pos-dangling") is None

    def test_identity_mismatch_rejects_without_command_or_attribution(self, conn):
        from src.state.venue_command_repo import insert_command

        snapshot_id = _ensure_snapshot(conn, token_id="tok-identity")
        envelope = _make_envelope(token_id="tok-identity")
        _ensure_entry_certificate(
            conn,
            certificate_hash="cert-identity-mismatch",
            envelope=envelope,
        )
        conn.execute(
            """
            UPDATE world.decision_certificates
               SET payload_json = ?
             WHERE certificate_hash = 'cert-identity-mismatch'
            """,
            (json.dumps({"condition_id": "other", "token_id": "tok-identity", "direction": "buy_yes"}),),
        )
        with pytest.raises(ValueError, match="identity does not match"):
            insert_command(
                conn,
                command_id="cmd-identity-mismatch",
                snapshot_id=snapshot_id,
                envelope_id="env-identity-mismatch",
                submission_envelope=envelope,
                position_id="pos-identity-mismatch",
                decision_id="dec-identity-mismatch",
                idempotency_key="idem-identity-mismatch",
                intent_kind="ENTRY",
                market_id="mkt-identity-mismatch",
                token_id="tok-identity",
                side="BUY",
                size=10.0,
                price=0.5,
                created_at="2026-07-29T00:00:00Z",
                decision_certificate_hash="cert-identity-mismatch",
            )

        assert conn.execute(
            "SELECT COUNT(*) FROM venue_commands WHERE command_id='cmd-identity-mismatch'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_submission_envelopes "
            "WHERE envelope_id='env-identity-mismatch'"
        ).fetchone()[0] == 0
        assert _attribution_row(conn, "pos-identity-mismatch") is None

    def test_fresh_admission_reads_certificate_committed_after_stale_trade_snapshot(
        self,
        tmp_path,
    ):
        from src.state.db import init_schema, init_schema_trade_only
        from src.state.venue_command_repo import (
            begin_fresh_entry_admission,
            insert_command,
        )

        world_path = tmp_path / "zeus-world.db"
        trade_path = tmp_path / "zeus_trades.db"
        world_writer = sqlite3.connect(world_path)
        world_writer.execute("PRAGMA journal_mode=WAL")
        world_writer.execute(
            """
            CREATE TABLE decision_certificates (
                certificate_hash TEXT PRIMARY KEY,
                certificate_type TEXT NOT NULL,
                mode TEXT NOT NULL,
                verifier_status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        world_writer.commit()

        trade_conn = sqlite3.connect(trade_path)
        trade_conn.row_factory = sqlite3.Row
        init_schema(trade_conn)
        init_schema_trade_only(trade_conn)
        trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
        snapshot_id = _ensure_snapshot(trade_conn, token_id="tok-fresh-entry")
        envelope = _make_envelope(token_id="tok-fresh-entry")
        trade_conn.commit()

        trade_conn.execute("BEGIN")
        assert trade_conn.execute(
            "SELECT COUNT(*) FROM world.decision_certificates"
        ).fetchone()[0] == 0
        world_writer.execute(
            """
            INSERT INTO decision_certificates(
                certificate_hash, certificate_type, mode, verifier_status, payload_json
            ) VALUES (
                'cert-after-snapshot', 'ActionableTradeCertificate',
                'LIVE', 'VERIFIED', ?
            )
            """,
            (
                json.dumps(
                    {
                        "condition_id": envelope.condition_id,
                        "token_id": envelope.selected_outcome_token_id,
                        "direction": "buy_yes",
                    }
                ),
            ),
        )
        world_writer.commit()

        begin_fresh_entry_admission(trade_conn)
        insert_command(
            trade_conn,
            command_id="cmd-after-snapshot",
            snapshot_id=snapshot_id,
            envelope_id="env-after-snapshot",
            submission_envelope=envelope,
            position_id="pos-after-snapshot",
            decision_id="dec-after-snapshot",
            idempotency_key="idem-after-snapshot",
            intent_kind="ENTRY",
            market_id="mkt-after-snapshot",
            token_id="tok-fresh-entry",
            side="BUY",
            size=10.0,
            price=0.5,
            created_at="2026-07-29T00:00:00Z",
            decision_certificate_hash="cert-after-snapshot",
        )
        trade_conn.commit()

        assert trade_conn.execute(
            "SELECT COUNT(*) FROM venue_commands "
            "WHERE command_id='cmd-after-snapshot'"
        ).fetchone()[0] == 1
        trade_conn.close()
        world_writer.close()


class TestInsertCommandQVersionStamp:
    """SCH-W1.2-ORDER-STATE: q_version is write-once at insert_command, nullable,
    stamped beside snapshot_id. Never re-stamped by append_event (no UPDATE path
    touches the column)."""

    def test_q_version_stamped_when_passed(self, conn):
        _insert(conn, command_id="cmd-qv", q_version="posterior-hash-abc123")

        row = conn.execute(
            "SELECT q_version FROM venue_commands WHERE command_id = 'cmd-qv'"
        ).fetchone()
        assert row["q_version"] == "posterior-hash-abc123"

    def test_q_version_null_by_default_when_omitted(self, conn):
        _insert(conn, command_id="cmd-no-qv")

        row = conn.execute(
            "SELECT q_version FROM venue_commands WHERE command_id = 'cmd-no-qv'"
        ).fetchone()
        assert row["q_version"] is None

    def test_live_entry_missing_q_version_rejected_before_insert(self, conn, monkeypatch):
        from src.state.venue_command_repo import insert_command

        monkeypatch.setenv("ZEUS_ENTRY_Q_VERSION_STRICT", "1")
        snapshot_id = _ensure_snapshot(conn, token_id="tok-live-qv")
        envelope = _make_envelope(token_id="tok-live-qv")

        with pytest.raises(ValueError, match="ENTRY venue command requires non-empty q_version"):
            insert_command(
                conn,
                command_id="cmd-live-no-qv",
                snapshot_id=snapshot_id,
                envelope_id="env-live-no-qv",
                submission_envelope=envelope,
                position_id="pos-live-no-qv",
                decision_id="dec-live-no-qv",
                idempotency_key="idem-live-no-qv",
                intent_kind="ENTRY",
                market_id="mkt-live-no-qv",
                token_id="tok-live-qv",
                side="BUY",
                size=10.0,
                price=0.5,
                created_at="2026-04-26T00:00:00Z",
            )

        row = conn.execute(
            "SELECT command_id FROM venue_commands WHERE command_id = 'cmd-live-no-qv'"
        ).fetchone()
        assert row is None

    def test_xpc_live_entry_missing_q_version_rejected_before_insert(self, conn, monkeypatch):
        from src.state.venue_command_repo import insert_command

        monkeypatch.delenv("ZEUS_ENTRY_Q_VERSION_STRICT", raising=False)
        monkeypatch.setenv("XPC_SERVICE_NAME", "com.zeus.live-trading")
        snapshot_id = _ensure_snapshot(conn, token_id="tok-xpc-live-qv")
        envelope = _make_envelope(token_id="tok-xpc-live-qv")

        with pytest.raises(ValueError, match="ENTRY venue command requires non-empty q_version"):
            insert_command(
                conn,
                command_id="cmd-xpc-live-no-qv",
                snapshot_id=snapshot_id,
                envelope_id="env-xpc-live-no-qv",
                submission_envelope=envelope,
                position_id="pos-xpc-live-no-qv",
                decision_id="dec-xpc-live-no-qv",
                idempotency_key="idem-xpc-live-no-qv",
                intent_kind="ENTRY",
                market_id="mkt-xpc-live-no-qv",
                token_id="tok-xpc-live-qv",
                side="BUY",
                size=10.0,
                price=0.5,
                created_at="2026-04-26T00:00:00Z",
            )

        row = conn.execute(
            "SELECT command_id FROM venue_commands WHERE command_id = 'cmd-xpc-live-no-qv'"
        ).fetchone()
        assert row is None

    def test_zeus_mode_live_entry_missing_q_version_rejected_before_insert(self, conn, monkeypatch):
        from src.state.venue_command_repo import insert_command

        monkeypatch.delenv("ZEUS_ENTRY_Q_VERSION_STRICT", raising=False)
        monkeypatch.delenv("XPC_SERVICE_NAME", raising=False)
        monkeypatch.setenv("ZEUS_MODE", "live")
        snapshot_id = _ensure_snapshot(conn, token_id="tok-mode-live-qv")
        envelope = _make_envelope(token_id="tok-mode-live-qv")

        with pytest.raises(ValueError, match="ENTRY venue command requires non-empty q_version"):
            insert_command(
                conn,
                command_id="cmd-mode-live-no-qv",
                snapshot_id=snapshot_id,
                envelope_id="env-mode-live-no-qv",
                submission_envelope=envelope,
                position_id="pos-mode-live-no-qv",
                decision_id="dec-mode-live-no-qv",
                idempotency_key="idem-mode-live-no-qv",
                intent_kind="ENTRY",
                market_id="mkt-mode-live-no-qv",
                token_id="tok-mode-live-qv",
                side="BUY",
                size=10.0,
                price=0.5,
                created_at="2026-04-26T00:00:00Z",
            )

        row = conn.execute(
            "SELECT command_id FROM venue_commands WHERE command_id = 'cmd-mode-live-no-qv'"
        ).fetchone()
        assert row is None

    def test_q_version_whitespace_only_normalizes_to_null(self, conn):
        _insert(conn, command_id="cmd-blank-qv", q_version="   ")

        row = conn.execute(
            "SELECT q_version FROM venue_commands WHERE command_id = 'cmd-blank-qv'"
        ).fetchone()
        assert row["q_version"] is None

    def test_q_version_survives_state_transitions_unchanged(self, conn):
        from src.state.venue_command_repo import append_event

        _insert(conn, command_id="cmd-qv-transition", q_version="posterior-hash-xyz789")
        append_event(
            conn,
            command_id="cmd-qv-transition",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={
                "execution_capability": {
                    "allowed": True,
                    "components": [
                        {
                            "component": "entry_economics",
                            "allowed": True,
                            "details": {
                                "q_live": 0.7,
                                "q_lcb_5pct": 0.6,
                                "expected_edge": 0.1,
                                "min_entry_price": 0.01,
                                "limit_price": 0.5,
                                "submit_edge": 0.1,
                                "expected_profit_usd": 1.0,
                                "min_expected_profit_usd": 0.01,
                                "submit_edge_density": 0.1,
                                "min_submit_edge_density": 0.01,
                                "shares": 10.0,
                                "qkernel_side": "buy_yes",
                            },
                        },
                        {"component": "entry_actionable_certificate", "allowed": True},
                    ],
                },
            },
        )

        row = conn.execute(
            "SELECT state, q_version FROM venue_commands WHERE command_id = 'cmd-qv-transition'"
        ).fetchone()
        assert row["state"] == "SUBMITTING"
        assert row["q_version"] == "posterior-hash-xyz789"


class TestSiblingEntryPortfolioAuthority:
    def test_command_persistence_does_not_impose_single_token_family_veto(self, conn):
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, city, target_date, bin_label, direction,
                strategy_key, condition_id, no_token_id, updated_at,
                temperature_metric
            ) VALUES (
                'held-28c', 'active', 'Seoul', '2026-07-21', '28C',
                'buy_no', 'center_bin_buy', 'condition-28c', 'no-28c',
                '2026-07-20T00:00:00Z', 'high'
            )
            """
        )

        _insert(
            conn,
            command_id="cmd-sibling-29c",
            position_id="new-29c-position",
            token_id="no-29c",
            idempotency_key="idem-sibling-29c",
            price=0.84,
        )

        row = conn.execute(
            "SELECT state, token_id, price FROM venue_commands "
            "WHERE command_id='cmd-sibling-29c'"
        ).fetchone()
        assert tuple(row) == ("INTENT_CREATED", "no-29c", 0.84)


# ---------------------------------------------------------------------------
# Test 2: append_event state transition grammar
# ---------------------------------------------------------------------------

class TestAppendEventStateTransitionIsGrammarChecked:
    # --- legal transitions ---

    def test_intent_created_to_submitting(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z",
                     payload=_valid_execution_capability_payload())
        assert get_command(conn, "cmd-001")["state"] == "SUBMITTING"

    def test_submitting_to_acked(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:02:00Z")
        assert get_command(conn, "cmd-001")["state"] == "ACKED"

    def test_submitting_to_rejected(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REJECTED",
                     occurred_at="2026-04-26T00:02:00Z")
        assert get_command(conn, "cmd-001")["state"] == "REJECTED"

    def test_submitting_to_unknown(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_UNKNOWN",
                     occurred_at="2026-04-26T00:02:00Z")
        assert get_command(conn, "cmd-001")["state"] == "UNKNOWN"

    def test_acked_to_partial(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:02:00Z")
        append_event(conn, command_id="cmd-001", event_type="PARTIAL_FILL_OBSERVED",
                     occurred_at="2026-04-26T00:03:00Z")
        assert get_command(conn, "cmd-001")["state"] == "PARTIAL"

    def test_acked_to_filled(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:02:00Z")
        append_event(conn, command_id="cmd-001", event_type="FILL_CONFIRMED",
                     occurred_at="2026-04-26T00:03:00Z")
        assert get_command(conn, "cmd-001")["state"] == "FILLED"

    def test_cancel_pending_to_cancelled(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-001", event_type="CANCEL_REQUESTED",
                     occurred_at="2026-04-26T00:02:00Z")
        append_event(conn, command_id="cmd-001", event_type="CANCEL_ACKED",
                     occurred_at="2026-04-26T00:03:00Z")
        assert get_command(conn, "cmd-001")["state"] == "CANCELLED"

    def test_intent_created_to_review_required(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="REVIEW_REQUIRED",
                     occurred_at="2026-04-26T00:01:00Z")
        assert get_command(conn, "cmd-001")["state"] == "REVIEW_REQUIRED"

    def test_review_required_cancel_unknown_live_proof_restores_acked(self, conn):
        from src.state.venue_command_repo import append_event, get_command
        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:02:00Z",
                     payload={"venue_order_id": "ord-live"})
        append_event(conn, command_id="cmd-001", event_type="CANCEL_REQUESTED",
                     occurred_at="2026-04-26T00:03:00Z",
                     payload={"venue_order_id": "ord-live"})
        append_event(
            conn,
            command_id="cmd-001",
            event_type="CANCEL_REPLACE_BLOCKED",
            occurred_at="2026-04-26T00:04:00Z",
            payload={
                "reason": "post_cancel_exception_possible_side_effect: local adapter error",
                "requires_m5_reconcile": True,
                "semantic_cancel_status": "CANCEL_UNKNOWN",
            },
        )
        append_event(
            conn,
            command_id="cmd-001",
            event_type="REVIEW_CLEARED_VENUE_ORDER_LIVE",
            occurred_at="2026-04-26T00:05:00Z",
            payload={
                "schema_version": 1,
                "reason": "review_cleared_venue_order_live",
                "command_id": "cmd-001",
                "venue_order_id": "ord-live",
                "proof_class": "cancel_unknown_venue_order_live",
                "side_effect_boundary_crossed": "unknown",
                "sdk_cancel_attempted": "unknown",
                "required_predicates": {
                    "latest_event_is_cancel_replace_blocked": True,
                    "semantic_cancel_status_cancel_unknown": True,
                    "requires_m5_reconcile": True,
                    "venue_order_id_present": True,
                    "venue_order_id_matches_point_read": True,
                    "point_order_status_live": True,
                    "point_order_matched_size_not_positive": True,
                    "no_trade_facts": True,
                },
                "venue_order_live_proof": {
                    "source": "authenticated_clob_point_order_read",
                    "observed_at": "2026-04-26T00:05:00Z",
                    "venue_order_id": "ord-live",
                    "point_order_status": "LIVE",
                    "matched_size": "0",
                    "point_order": {"orderID": "ord-live", "status": "LIVE", "matched_size": "0"},
                },
                "source_proof": {
                    "source_function": "command_recovery._reconcile_row",
                    "source_reason": "cancel_unknown_venue_order_live",
                },
                "reviewed_by": "command_recovery",
                "cleared_at": "2026-04-26T00:05:00Z",
            },
        )
        assert get_command(conn, "cmd-001")["state"] == "ACKED"

    # --- illegal transitions ---

    @pytest.mark.parametrize("from_state,event_type,setup_events", [
        # From INTENT_CREATED: submit/cancel/provenance-boundary/review events are legal.
        # NOTE: INTENT_CREATED->SUBMIT_REJECTED was legalized by 260f75863
        # ("retire pre-submit orphan commands") and is no longer illegal; see the
        # legal-transition coverage instead (no dedicated positive test today).
        ("INTENT_CREATED", "SUBMIT_UNKNOWN", []),
        ("INTENT_CREATED", "FILL_CONFIRMED", []),
        ("INTENT_CREATED", "CANCEL_ACKED", []),
        ("INTENT_CREATED", "EXPIRED", []),
        ("INTENT_CREATED", "PARTIAL_FILL_OBSERVED", []),
        # From SUBMITTING: SUBMIT_ACKED, SUBMIT_REJECTED, SUBMIT_UNKNOWN,
        # CANCEL_REQUESTED, REVIEW_REQUIRED, EXPIRED are legal; others illegal.
        # NOTE: SUBMITTING->EXPIRED was legalized by bb74e650c
        # ("restore redecision freshness flow") and is no longer illegal.
        ("SUBMITTING", "INTENT_CREATED", ["SUBMIT_REQUESTED"]),
        ("SUBMITTING", "FILL_CONFIRMED", ["SUBMIT_REQUESTED"]),
        ("SUBMITTING", "PARTIAL_FILL_OBSERVED", ["SUBMIT_REQUESTED"]),
        ("SUBMITTING", "CANCEL_ACKED", ["SUBMIT_REQUESTED"]),
        # From ACKED: fill/cancel/expire/review legal; submit events illegal
        ("ACKED", "SUBMIT_REQUESTED", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        ("ACKED", "SUBMIT_ACKED", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        ("ACKED", "SUBMIT_REJECTED", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        ("ACKED", "SUBMIT_UNKNOWN", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        ("ACKED", "CANCEL_ACKED", ["SUBMIT_REQUESTED", "SUBMIT_ACKED"]),
        # From FILLED: only REVIEW_REQUIRED legal
        ("FILLED", "SUBMIT_REQUESTED",
         ["SUBMIT_REQUESTED", "SUBMIT_ACKED", "FILL_CONFIRMED"]),
        ("FILLED", "CANCEL_REQUESTED",
         ["SUBMIT_REQUESTED", "SUBMIT_ACKED", "FILL_CONFIRMED"]),
        ("FILLED", "FILL_CONFIRMED",
         ["SUBMIT_REQUESTED", "SUBMIT_ACKED", "FILL_CONFIRMED"]),
        # From CANCEL_PENDING: only CANCEL_ACKED, EXPIRED, REVIEW_REQUIRED legal
        ("CANCEL_PENDING", "SUBMIT_ACKED",
         ["SUBMIT_REQUESTED", "CANCEL_REQUESTED"]),
        ("CANCEL_PENDING", "FILL_CONFIRMED",
         ["SUBMIT_REQUESTED", "CANCEL_REQUESTED"]),
    ])
    def test_illegal_transition_raises_value_error(
            self, conn, from_state, event_type, setup_events):
        from src.state.venue_command_repo import append_event
        _insert(conn)
        for evt in setup_events:
            append_event(
                conn, command_id="cmd-001", event_type=evt,
                occurred_at="2026-04-26T00:00:00Z",
                payload=_valid_execution_capability_payload() if evt == "SUBMIT_REQUESTED" else None,
            )
        with pytest.raises(ValueError, match="Illegal command-event grammar"):
            append_event(conn, command_id="cmd-001", event_type=event_type,
                         occurred_at="2026-04-26T00:10:00Z")

    def test_unknown_command_id_raises_value_error(self, conn):
        from src.state.venue_command_repo import append_event
        with pytest.raises(ValueError, match="Unknown command_id"):
            append_event(conn, command_id="nonexistent", event_type="SUBMIT_REQUESTED",
                         occurred_at="2026-04-26T00:00:00Z")


# ---------------------------------------------------------------------------
# Test 3: idempotency key uniqueness
# ---------------------------------------------------------------------------

class TestIdempotencyKeyUniquenessEnforced:
    def test_duplicate_key_raises_integrity_error(self, conn):
        from src.state.venue_command_repo import insert_command
        _insert(conn, command_id="cmd-001", idempotency_key="same-key")

        with pytest.raises(sqlite3.IntegrityError):
            snapshot_id = _ensure_snapshot(conn, token_id="tok-001")
            envelope = _make_envelope(token_id="tok-001", price=0.6, size=5.0)
            _ensure_entry_certificate(
                conn, certificate_hash="cert-cmd-002", envelope=envelope
            )
            insert_command(
                conn,
                command_id="cmd-002",
                snapshot_id=snapshot_id,
                envelope_id="env-cmd-002",
                submission_envelope=envelope,
                position_id="pos-002",
                decision_id="dec-002",
                idempotency_key="same-key",  # same key
                intent_kind="ENTRY",
                market_id="mkt-001",
                token_id="tok-001",
                side="BUY",
                size=5.0,
                price=0.6,
                created_at="2026-04-26T00:01:00Z",
                decision_certificate_hash="cert-cmd-002",
            )

    def test_different_keys_succeed(self, conn):
        from src.state.venue_command_repo import insert_command, get_command
        _insert(conn, command_id="cmd-001", idempotency_key="key-A")
        snapshot_id = _ensure_snapshot(conn, token_id="tok-001")
        insert_command(
            conn,
            command_id="cmd-002",
            snapshot_id=snapshot_id,
            envelope_id=_ensure_envelope(
                conn,
                token_id="tok-001",
                side="SELL",
                price=0.6,
                size=5.0,
            ),
            position_id="pos-002",
            decision_id="dec-002",
            idempotency_key="key-B",
            intent_kind="EXIT",
            market_id="mkt-001",
            token_id="tok-001",
            side="SELL",
            size=5.0,
            price=0.6,
            created_at="2026-04-26T00:01:00Z",
        )
        assert get_command(conn, "cmd-001") is not None
        assert get_command(conn, "cmd-002") is not None


# ---------------------------------------------------------------------------
# Test 4: find_unresolved_commands returns only in-flight
# ---------------------------------------------------------------------------

class TestFindUnresolvedCommandsReturnsOnlyInFlight:
    def test_returns_only_submitting_unknown_review(self, conn):
        from src.state.venue_command_repo import append_event, find_unresolved_commands

        # ACKED (terminal-ish, not in unresolved set)
        _insert(conn, command_id="cmd-acked", idempotency_key="key-acked")
        append_event(conn, command_id="cmd-acked", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:00:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-acked", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:01:00Z")

        # SUBMITTING
        _insert(conn, command_id="cmd-submitting", idempotency_key="key-sub")
        append_event(conn, command_id="cmd-submitting", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:00:00Z",
                     payload=_valid_execution_capability_payload())

        # UNKNOWN
        _insert(conn, command_id="cmd-unknown", idempotency_key="key-unk")
        append_event(conn, command_id="cmd-unknown", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:00:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-unknown", event_type="SUBMIT_UNKNOWN",
                     occurred_at="2026-04-26T00:01:00Z")

        # FILLED (resolved, should not appear)
        _insert(conn, command_id="cmd-filled", idempotency_key="key-filled")
        append_event(conn, command_id="cmd-filled", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:00:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-filled", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:01:00Z")
        append_event(conn, command_id="cmd-filled", event_type="FILL_CONFIRMED",
                     occurred_at="2026-04-26T00:02:00Z")

        # REVIEW_REQUIRED
        _insert(conn, command_id="cmd-review", idempotency_key="key-rev")
        append_event(conn, command_id="cmd-review", event_type="REVIEW_REQUIRED",
                     occurred_at="2026-04-26T00:01:00Z")

        unresolved = list(find_unresolved_commands(conn))
        ids = {r["command_id"] for r in unresolved}
        assert ids == {"cmd-submitting", "cmd-unknown", "cmd-review"}
        assert "cmd-acked" not in ids
        assert "cmd-filled" not in ids


# ---------------------------------------------------------------------------
# Test 5: list_events ordered by sequence_no
# ---------------------------------------------------------------------------

class TestListEventsOrderedBySequenceNo:
    def test_three_events_in_order(self, conn):
        from src.state.venue_command_repo import append_event, list_events

        _insert(conn)
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_REQUESTED",
                     occurred_at="2026-04-26T00:01:00Z",
                     payload=_valid_execution_capability_payload())
        append_event(conn, command_id="cmd-001", event_type="SUBMIT_ACKED",
                     occurred_at="2026-04-26T00:02:00Z")

        events = list_events(conn, "cmd-001")
        # Should have: INTENT_CREATED (1), SUBMIT_REQUESTED (2), SUBMIT_ACKED (3)
        assert len(events) == 3
        assert events[0]["sequence_no"] == 1
        assert events[0]["event_type"] == "INTENT_CREATED"
        assert events[1]["sequence_no"] == 2
        assert events[1]["event_type"] == "SUBMIT_REQUESTED"
        assert events[2]["sequence_no"] == 3
        assert events[2]["event_type"] == "SUBMIT_ACKED"

    def test_empty_for_unknown_command(self, conn):
        from src.state.venue_command_repo import list_events
        assert list_events(conn, "nonexistent") == []


# ---------------------------------------------------------------------------
# Test 6: NC-18 — no module outside repo writes events (AST walk)
# ---------------------------------------------------------------------------

class TestNoModuleOutsideRepoWritesEvents:
    """NC-18 enforcement (post-critic MAJOR-2 fix): real AST walk that catches
    SQL string literals containing forbidden mutation verbs against the
    venue_commands / venue_command_events tables, even when:
      - the SQL is built via f-string/`.format()`/concatenation
      - the table name is quoted (`"venue_command_events"` or backticks)
      - whitespace varies (`UPDATE  venue_command_events`)
      - the verb is uppercase, lowercase, or mixed case

    Strategy: walk every Constant node in src/**/*.py whose value is a string
    matching the forbidden-mutation regex. Substring matching is bypassable;
    AST-level inspection of every string literal is not. Comments and
    docstrings count too — if a docstring documents a forbidden statement,
    that is itself a leak signal worth flagging (allowlist below covers the
    legitimate documentation case).
    """

    # Regex catches:
    #  - INSERT INTO  / UPDATE  / DELETE FROM
    #  - target = venue_command_events  OR  venue_commands
    #  - allows quoting (", ', `) and arbitrary whitespace
    _FORBIDDEN_MUTATION_RE = __import__("re").compile(
        r"""
        \b
        (?:
            INSERT \s+ INTO          # INSERT INTO ...
          | UPDATE                   # UPDATE ...
          | DELETE \s+ FROM          # DELETE FROM ...
        )
        \s+
        ["'`]?                       # optional quote
        (?:venue_command_events|venue_commands)
        ["'`]?
        \b
        """,
        __import__("re").IGNORECASE | __import__("re").VERBOSE,
    )

    def test_no_direct_venue_command_events_mutation_outside_repo(self):
        """Real AST walk: every string Constant in every src file is scanned.
        Only src/state/venue_command_repo.py is allowed to contain mutation
        SQL against either table. P1.S2/S3 will need to extend the allowlist
        if helpers move; today the seam is single-file.
        """
        repo_rel = "src/state/venue_command_repo.py"
        allowed_files = {str(ROOT / repo_rel)}
        violations: list[str] = []

        for filepath in glob.glob(str(ROOT / "src/**/*.py"), recursive=True):
            if filepath in allowed_files:
                continue
            try:
                source = Path(filepath).read_text()
            except OSError:
                continue
            try:
                tree = ast.parse(source, filename=filepath)
            except SyntaxError as exc:
                violations.append(
                    f"{filepath}:{exc.lineno}: parse error in NC-18 guard "
                    f"(fix the syntax first): {exc.msg}"
                )
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if self._FORBIDDEN_MUTATION_RE.search(node.value):
                        rel = Path(filepath).relative_to(ROOT).as_posix()
                        violations.append(
                            f"{rel}:{node.lineno}: forbidden venue_commands/"
                            f"venue_command_events mutation literal — "
                            f"route through src/state/venue_command_repo.py"
                        )

        assert not violations, (
            "NC-18 violation: direct venue_commands/venue_command_events "
            "mutation SQL outside the repo:\n" + "\n".join(violations)
        )

    def test_regex_catches_known_evasion_shapes(self):
        """Self-test for the AST regex. Pre-fix substring match would have
        missed every shape below; post-fix regex catches all of them.
        """
        evasions = [
            'UPDATE venue_command_events SET state = ?',
            'update venue_command_events set foo = bar',  # lowercase
            'UPDATE  venue_command_events SET ...',         # double space
            'UPDATE "venue_command_events" SET ...',        # quoted ident
            "UPDATE `venue_command_events` SET ...",        # backtick ident
            "DELETE  FROM venue_command_events WHERE 1",
            "INSERT  INTO venue_command_events VALUES",
            "delete from venue_commands where true",
        ]
        for shape in evasions:
            assert self._FORBIDDEN_MUTATION_RE.search(shape), (
                f"AST guard regex failed to catch evasion shape: {shape!r}"
            )

    def test_regex_does_not_false_positive_on_benign_strings(self):
        """Regex must NOT trip on legitimate non-mutation references."""
        benign = [
            "SELECT * FROM venue_command_events",
            "SELECT * FROM venue_commands WHERE state = ?",
            "Note: do not UPDATE venue_command_events directly",  # prose mentions verb but not SQL form
        ]
        # The third one is interesting: it DOES contain "UPDATE venue_command_events"
        # exactly, so the regex correctly flags it. That's a true positive
        # (the prose IS a mutation literal in a string constant), and the
        # allowlist already excludes the only file where this would legitimately
        # appear (the repo module's docstrings). Verify the first two pass.
        for shape in benign[:2]:
            assert not self._FORBIDDEN_MUTATION_RE.search(shape), (
                f"AST guard regex falsely flagged benign string: {shape!r}"
            )


# ---------------------------------------------------------------------------
# Test 7: find_command_by_idempotency_key
# ---------------------------------------------------------------------------

class TestFindCommandByIdempotencyKey:
    def test_finds_existing_command(self, conn):
        from src.state.venue_command_repo import find_command_by_idempotency_key
        _insert(conn, command_id="cmd-001", idempotency_key="find-me")
        result = find_command_by_idempotency_key(conn, "find-me")
        assert result is not None
        assert result["command_id"] == "cmd-001"

    def test_returns_none_for_missing_key(self, conn):
        from src.state.venue_command_repo import find_command_by_idempotency_key
        assert find_command_by_idempotency_key(conn, "no-such-key") is None


# ---------------------------------------------------------------------------
# Test 8: payload_json round-trip
# ---------------------------------------------------------------------------

class TestAppendEventPayloadRoundTrip:
    def test_payload_stored_as_json(self, conn):
        import json
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)
        payload = {"venue_order_id": "ord-abc", "status": "ok"}
        # REVIEW_REQUIRED (not SUBMIT_REQUESTED): this test exercises generic
        # payload_json round-trip, not the ENTRY execution_capability gate.
        append_event(conn, command_id="cmd-001", event_type="REVIEW_REQUIRED",
                     occurred_at="2026-04-26T00:01:00Z", payload=payload)
        events = list_events(conn, "cmd-001")
        evt = events[1]  # sequence_no=2
        assert evt["payload_json"] is not None
        assert json.loads(evt["payload_json"]) == payload

    def test_none_payload_stored_as_null(self, conn):
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)
        # REVIEW_REQUIRED (not SUBMIT_REQUESTED): this test exercises generic
        # payload_json round-trip, not the ENTRY execution_capability gate.
        append_event(conn, command_id="cmd-001", event_type="REVIEW_REQUIRED",
                     occurred_at="2026-04-26T00:01:00Z", payload=None)
        events = list_events(conn, "cmd-001")
        assert events[1]["payload_json"] is None


# ---------------------------------------------------------------------------
# Test 9 (post-critic MAJOR-1): savepoint composability
# Project memory L30: `with conn:` silently RELEASEs an outer SAVEPOINT.
# Repo must use SAVEPOINT-based context so callers can wrap repo calls inside
# their own transaction or savepoint without losing rollback granularity.
# This is the regression guard that protects P1.S3 executor from latent
# atomicity loss when it wraps _live_order in its own transaction context.
# ---------------------------------------------------------------------------

class TestSavepointComposability:
    def test_insert_command_composable_inside_outer_savepoint(self, conn):
        """Outer SAVEPOINT followed by insert_command followed by ROLLBACK TO
        outer must undo BOTH the command row AND the auto-appended event row.
        Pre-fix: `with conn:` would have RELEASEd `outer_test` mid-flight,
        making ROLLBACK TO raise OperationalError.
        """
        from src.state.venue_command_repo import insert_command

        conn.execute("SAVEPOINT outer_test")
        snapshot_id = _ensure_snapshot(conn, token_id="t1")
        envelope = _make_envelope(token_id="t1", price=0.5, size=10.0)
        _ensure_entry_certificate(conn, certificate_hash="cert-cmp", envelope=envelope)
        insert_command(
            conn,
            command_id="cmp-001",
            snapshot_id=snapshot_id,
            envelope_id="env-cmp",
            submission_envelope=envelope,
            position_id="pos-1",
            decision_id="dec-1",
            idempotency_key="idem-cmp-001",
            intent_kind="ENTRY",
            market_id="m1",
            token_id="t1",
            side="BUY",
            size=10.0,
            price=0.5,
            created_at="2026-04-26T00:00:00Z",
            decision_certificate_hash="cert-cmp",
        )
        # Outer rollback must succeed (SAVEPOINT still exists).
        conn.execute("ROLLBACK TO SAVEPOINT outer_test")
        conn.execute("RELEASE SAVEPOINT outer_test")

        # And both rows must be gone.
        commands = conn.execute(
            "SELECT * FROM venue_commands WHERE command_id = 'cmp-001'"
        ).fetchall()
        assert len(commands) == 0
        events = conn.execute(
            "SELECT * FROM venue_command_events WHERE command_id = 'cmp-001'"
        ).fetchall()
        assert len(events) == 0

    def test_append_event_composable_inside_outer_savepoint(self, conn):
        """Same pattern for append_event."""
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)  # standard cmd-001 helper

        conn.execute("SAVEPOINT outer_evt")
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:00:30Z",
            payload=_valid_execution_capability_payload(),
        )
        events_during = list_events(conn, "cmd-001")
        assert len(events_during) == 2

        conn.execute("ROLLBACK TO SAVEPOINT outer_evt")
        conn.execute("RELEASE SAVEPOINT outer_evt")

        events_after = list_events(conn, "cmd-001")
        assert len(events_after) == 1
        cmd = conn.execute(
            "SELECT state FROM venue_commands WHERE command_id = 'cmd-001'"
        ).fetchone()
        state_val = cmd["state"] if hasattr(cmd, "keys") else cmd[0]
        assert state_val == "INTENT_CREATED"


# ---------------------------------------------------------------------------
# Test 10 (post-critic MEDIUM-1): payload datetime / bytes round-trip
# P1.S4 recovery loop will routinely attach datetime payloads. Pre-fix
# json.dumps raised TypeError on datetime; post-fix coerces to ISO string.
# ---------------------------------------------------------------------------

class TestAppendEventPayloadCoercion:
    def test_payload_datetime_coerces_to_iso(self, conn):
        import json
        import datetime
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)

        ts = datetime.datetime(2026, 4, 26, 12, 30, 45, tzinfo=datetime.timezone.utc)
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"observed_at": ts, **_valid_execution_capability_payload()},
        )
        evt = list_events(conn, "cmd-001")[1]
        decoded = json.loads(evt["payload_json"])
        assert decoded["observed_at"] == ts.isoformat()

    def test_payload_bytes_coerces_to_hex(self, conn):
        import json
        from src.state.venue_command_repo import append_event, list_events
        _insert(conn)

        raw = b"\xde\xad\xbe\xef"
        append_event(
            conn,
            command_id="cmd-001",
            event_type="SUBMIT_REQUESTED",
            occurred_at="2026-04-26T00:01:00Z",
            payload={"raw": raw, **_valid_execution_capability_payload()},
        )
        evt = list_events(conn, "cmd-001")[1]
        decoded = json.loads(evt["payload_json"])
        assert decoded["raw"] == raw.hex()

    def test_payload_unserializable_raises_clean_typeerror(self, conn):
        from src.state.venue_command_repo import append_event
        _insert(conn)

        class Opaque:
            pass

        with pytest.raises(TypeError, match="not JSON serializable"):
            append_event(
                conn,
                command_id="cmd-001",
                event_type="SUBMIT_REQUESTED",
                occurred_at="2026-04-26T00:01:00Z",
                payload={"x": Opaque(), **_valid_execution_capability_payload()},
            )
