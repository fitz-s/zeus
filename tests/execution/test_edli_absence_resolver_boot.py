# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: boot crash-loop incidents 2026-06-12 (3x same day — each
#   needed a manual operator run of the absence resolver before the daemon
#   could boot; launchd respawned a failing boot in a loop meanwhile).
"""ANTIBODY: boot auto-resolution fires ONLY for the stuck-unknown class and
fail-closes on everything else (refusal, mixed reasons, venue failure)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

import src.execution.edli_absence_resolver as resolver_mod
from src.execution.edli_absence_resolver import boot_auto_resolve_stuck_unknowns
from src.events.live_cap import LiveCapLedger
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.state.db import init_schema


NOW = datetime(2026, 6, 18, 3, 9, tzinfo=timezone.utc)


def test_pre_submit_orphan_resolution_can_clear_reserved_cap_without_venue_read(monkeypatch):
    calls = []

    monkeypatch.setattr(
        resolver_mod,
        "resolve_pre_submit_orphans",
        lambda **kw: calls.append("pre_submit") or 0,
    )
    monkeypatch.setattr(
        resolver_mod,
        "resolve",
        lambda **kw: pytest.fail("absence resolver must not run after pre-submit orphan clears"),
    )

    ok = boot_auto_resolve_stuck_unknowns(["EDLI_STAGE_LIVE_CAP_RESERVED:2"])

    assert ok is True
    assert calls == ["pre_submit"]


def test_fires_and_succeeds_for_pure_stuck_unknown_reasons(monkeypatch):
    calls = {}

    monkeypatch.setattr(resolver_mod, "resolve_pre_submit_orphans", lambda **kw: 1)

    def fake_resolve(*, aggregate_id, apply, log):
        calls["apply"] = apply
        return 0

    monkeypatch.setattr(resolver_mod, "resolve", fake_resolve)
    ok = boot_auto_resolve_stuck_unknowns(
        ["EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:4", "EDLI_STAGE_LIVE_CAP_RESERVED:4"]
    )
    assert ok is True
    assert calls["apply"] is True


def test_never_fires_for_mixed_reasons(monkeypatch):
    monkeypatch.setattr(
        resolver_mod,
        "resolve_pre_submit_orphans",
        lambda **kw: pytest.fail("must not attempt pre-submit resolution with out-of-class blockers"),
    )
    monkeypatch.setattr(
        resolver_mod, "resolve",
        lambda **kw: pytest.fail("must not attempt resolution with out-of-class blockers"),
    )
    ok = boot_auto_resolve_stuck_unknowns(
        ["EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1", "EDLI_STAGE_LOADED_SHA_MISMATCH:x"]
    )
    assert ok is False


def test_never_fires_for_empty_reasons(monkeypatch):
    monkeypatch.setattr(
        resolver_mod,
        "resolve_pre_submit_orphans",
        lambda **kw: pytest.fail("must not attempt pre-submit resolution with no blockers"),
    )
    monkeypatch.setattr(
        resolver_mod, "resolve",
        lambda **kw: pytest.fail("must not attempt resolution with no blockers"),
    )
    assert boot_auto_resolve_stuck_unknowns([]) is False


def test_absence_refusal_can_fall_through_to_later_resolver(monkeypatch):
    calls = []

    monkeypatch.setattr(
        resolver_mod,
        "resolve_pre_submit_orphans",
        lambda **kw: calls.append("pre_submit") or 1,
    )

    def raising_resolve(**kw):
        calls.append("absence")
        raise RuntimeError("authenticated venue read found matching exposure; do not release cap")

    monkeypatch.setattr(resolver_mod, "resolve", raising_resolve)
    import src.execution.edli_presence_resolver as presence_mod
    import src.execution.edli_resting_absorbed_resolver as resting_mod

    monkeypatch.setattr(
        presence_mod,
        "resolve_presence",
        lambda **kw: calls.append("presence") or 1,
    )
    monkeypatch.setattr(
        resting_mod,
        "resolve_resting_or_absorbed",
        lambda **kw: calls.append("resting") or 0,
    )

    assert boot_auto_resolve_stuck_unknowns(["EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1"]) is True
    assert calls == ["pre_submit", "absence", "presence", "resting"]


def test_incomplete_resolution_fails_closed(monkeypatch):
    monkeypatch.setattr(resolver_mod, "resolve_pre_submit_orphans", lambda **kw: 1)
    monkeypatch.setattr(resolver_mod, "resolve", lambda **kw: 1)
    assert boot_auto_resolve_stuck_unknowns(["EDLI_STAGE_LIVE_CAP_RESERVED:2"]) is False


def test_pre_submit_orphan_resolver_releases_legacy_certificate_build_orphan(tmp_path, monkeypatch):
    db_path = tmp_path / "world.db"
    conn = _connect(db_path)
    try:
        init_schema(conn)
        ledger = LiveOrderAggregateLedger(conn)
        cap_ledger = LiveCapLedger(conn)
        event_id = "event-pre-submit-orphan"
        final_intent_id = "intent-pre-submit-orphan"
        aggregate_id = f"{event_id}:{final_intent_id}"
        execution_command_id = "cmd-pre-submit-orphan"
        ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="DecisionProofAccepted",
            payload={"event_id": event_id, "final_intent_id": final_intent_id},
            occurred_at=NOW,
            source_authority="decision_kernel",
        )
        ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="SubmitPlanBuilt",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "condition_id": "condition-1",
                "token_id": "token-yes",
                "direction": "buy_no",
                "limit_price": 0.44,
                "size": 25,
            },
            occurred_at=NOW,
            source_authority="engine_adapter",
        )
        pre_submit = ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(event_id=event_id, final_intent_id=final_intent_id),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )
        reservation = cap_ledger.reserve(
            event_id=event_id,
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=11.0,
            final_intent_id=final_intent_id,
            execution_command_id=execution_command_id,
        )
        live_cap = ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="LiveCapReserved",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "usage_id": reservation.usage_id,
            },
            occurred_at=NOW,
            source_authority="live_cap_ledger",
        )
        ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="ExecutionCommandCreated",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "execution_command_id": execution_command_id,
                "pre_submit_event_hash": pre_submit.event_hash,
                "live_cap_reserved_event_hash": live_cap.event_hash,
            },
            occurred_at=NOW,
            source_authority="engine_adapter",
        )
        conn.execute(
            """
            INSERT INTO no_trade_regret_events (
                regret_event_id, event_id, rejection_stage, rejection_reason,
                regret_bucket, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (
                "regret-1",
                event_id,
                "EXECUTOR_EXPRESSIBILITY",
                "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:SubmitRejected requires preceding VenueSubmitAttempted",
                "UNKNOWN_REVIEW_REQUIRED",
                NOW.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(resolver_mod, "get_world_connection_read_only", lambda: _connect(db_path))
    monkeypatch.setattr(resolver_mod, "get_world_connection", lambda **kw: _connect(db_path))
    monkeypatch.setattr(resolver_mod, "world_write_lock", _no_world_lock)

    rc = resolver_mod.resolve_pre_submit_orphans(aggregate_id=None, apply=True, log=lambda msg: None)

    assert rc == 0
    check = _connect(db_path)
    try:
        assert check.execute(
            "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = ?",
            (reservation.usage_id,),
        ).fetchone()["reservation_status"] == "RELEASED"
        projection = check.execute(
            "SELECT current_state, pending_reconcile FROM edli_live_order_projection WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        assert projection["current_state"] == "CAP_TRANSITIONED"
        assert bool(projection["pending_reconcile"]) is False
        rejected = check.execute(
            """
            SELECT payload_json
            FROM edli_live_order_events
            WHERE aggregate_id = ? AND event_type = 'SubmitRejected'
            """,
            (aggregate_id,),
        ).fetchone()
        assert rejected is not None
        assert '"pre_submit_rejection":true' in rejected["payload_json"]
        assert check.execute(
            """
            SELECT COUNT(*) c
            FROM edli_live_order_events
            WHERE aggregate_id = ? AND event_type = 'VenueSubmitAttempted'
            """,
            (aggregate_id,),
        ).fetchone()["c"] == 0
    finally:
        check.close()


def test_pre_submit_orphan_resolver_releases_command_created_without_side_effect(tmp_path, monkeypatch):
    seeded = _seed_pre_submit_orphan(tmp_path / "world.db", include_regret=False)

    monkeypatch.setattr(resolver_mod, "get_world_connection_read_only", lambda: _connect(seeded["db_path"]))
    monkeypatch.setattr(resolver_mod, "get_world_connection", lambda **kw: _connect(seeded["db_path"]))
    monkeypatch.setattr(resolver_mod, "world_write_lock", _no_world_lock)

    rc = resolver_mod.resolve_pre_submit_orphans(aggregate_id=None, apply=True, log=lambda msg: None)

    assert rc == 0
    check = _connect(seeded["db_path"])
    try:
        assert check.execute(
            "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = ?",
            (seeded["usage_id"],),
        ).fetchone()["reservation_status"] == "RELEASED"
        rejected = check.execute(
            """
            SELECT payload_json
            FROM edli_live_order_events
            WHERE aggregate_id = ? AND event_type = 'SubmitRejected'
            """,
            (seeded["aggregate_id"],),
        ).fetchone()
        assert rejected is not None
        assert '"orphan_class":"pre_submit_command_created_without_side_effect"' in rejected["payload_json"]
    finally:
        check.close()


def test_pre_submit_orphan_resolver_refuses_when_venue_command_exists(tmp_path, monkeypatch):
    seeded = _seed_pre_submit_orphan(tmp_path / "world.db", include_regret=False)
    conn = _connect(seeded["db_path"])
    try:
        conn.execute(
            """
            INSERT INTO venue_commands (
                snapshot_id, envelope_id,
                command_id, position_id, decision_id, idempotency_key, intent_kind,
                market_id, token_id, side, size, price, state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "snapshot-1",
                "envelope-1",
                seeded["execution_command_id"],
                "position-1",
                "decision-1",
                "idem-1",
                "ENTRY",
                "market-1",
                "token-yes",
                "BUY",
                5.0,
                0.40,
                "CREATED",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(resolver_mod, "get_world_connection_read_only", lambda: _connect(seeded["db_path"]))
    monkeypatch.setattr(resolver_mod, "get_world_connection", lambda **kw: _connect(seeded["db_path"]))
    monkeypatch.setattr(resolver_mod, "world_write_lock", _no_world_lock)

    rc = resolver_mod.resolve_pre_submit_orphans(aggregate_id=None, apply=True, log=lambda msg: None)

    assert rc == 1
    check = _connect(seeded["db_path"])
    try:
        assert check.execute(
            "SELECT reservation_status FROM edli_live_cap_usage WHERE usage_id = ?",
            (seeded["usage_id"],),
        ).fetchone()["reservation_status"] == "RESERVED"
    finally:
        check.close()


def _seed_pre_submit_orphan(db_path, *, include_regret: bool) -> dict[str, str]:
    conn = _connect(db_path)
    try:
        init_schema(conn)
        ledger = LiveOrderAggregateLedger(conn)
        cap_ledger = LiveCapLedger(conn)
        event_id = "event-pre-submit-orphan"
        final_intent_id = "intent-pre-submit-orphan"
        aggregate_id = f"{event_id}:{final_intent_id}"
        execution_command_id = "cmd-pre-submit-orphan"
        ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="DecisionProofAccepted",
            payload={"event_id": event_id, "final_intent_id": final_intent_id},
            occurred_at=NOW,
            source_authority="decision_kernel",
        )
        ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="SubmitPlanBuilt",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "condition_id": "condition-1",
                "token_id": "token-yes",
                "direction": "buy_no",
                "limit_price": 0.44,
                "size": 25,
            },
            occurred_at=NOW,
            source_authority="engine_adapter",
        )
        pre_submit = ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(event_id=event_id, final_intent_id=final_intent_id),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )
        reservation = cap_ledger.reserve(
            event_id=event_id,
            decision_time=NOW,
            cap_scope="tiny_live_canary",
            requested_notional_usd=11.0,
            final_intent_id=final_intent_id,
            execution_command_id=execution_command_id,
        )
        live_cap = ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="LiveCapReserved",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "usage_id": reservation.usage_id,
            },
            occurred_at=NOW,
            source_authority="live_cap_ledger",
        )
        ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="ExecutionCommandCreated",
            payload={
                "event_id": event_id,
                "final_intent_id": final_intent_id,
                "execution_command_id": execution_command_id,
                "pre_submit_event_hash": pre_submit.event_hash,
                "live_cap_reserved_event_hash": live_cap.event_hash,
            },
            occurred_at=NOW,
            source_authority="engine_adapter",
        )
        if include_regret:
            conn.execute(
                """
                INSERT INTO no_trade_regret_events (
                    regret_event_id, event_id, rejection_stage, rejection_reason,
                    regret_bucket, created_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    "regret-1",
                    event_id,
                    "EXECUTOR_EXPRESSIBILITY",
                    "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:SubmitRejected requires preceding VenueSubmitAttempted",
                    "UNKNOWN_REVIEW_REQUIRED",
                    NOW.isoformat(),
                ),
            )
        conn.commit()
        return {
            "db_path": str(db_path),
            "aggregate_id": aggregate_id,
            "event_id": event_id,
            "final_intent_id": final_intent_id,
            "execution_command_id": execution_command_id,
            "usage_id": reservation.usage_id,
        }
    finally:
        conn.close()


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _no_world_lock(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _pre_submit_payload(*, event_id: str, final_intent_id: str):
    return {
        "event_id": event_id,
        "final_intent_id": final_intent_id,
        "condition_id": "condition-1",
        "token_id": "token-yes",
        "side": "BUY",
        "direction": "buy_no",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": "2026-06-18T03:09:00+00:00",
        "quote_seen_at": "2026-06-18T03:08:59.950000+00:00",
        "quote_age_ms": 50,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash-1",
        "current_best_bid": 0.41,
        "current_best_ask": 0.43,
        "limit_price": 0.40,
        "would_cross_book": False,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 5.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "book_authority_id": "execution_feasibility_evidence",
        "book_captured_at": "2026-06-18T03:08:59.950000+00:00",
        "heartbeat_authority_id": "heartbeat_supervisor",
        "heartbeat_checked_at": "2026-06-18T03:09:00+00:00",
        "user_ws_authority_id": "ws_gap_guard",
        "user_ws_checked_at": "2026-06-18T03:09:00+00:00",
        "venue_connectivity_authority_id": "polymarket_public_orderbook",
        "venue_connectivity_checked_at": "2026-06-18T03:09:00+00:00",
        "balance_allowance_authority_id": "polymarket_wallet_readonly",
        "balance_allowance_checked_at": "2026-06-18T03:09:00+00:00",
        "expected_edge_source_certificate_hash": "actionable-hash-1",
        "cost_basis_source_certificate_hash": "cost-hash-1",
    }
