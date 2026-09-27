# Created: 2026-06-01
# Last reused/audited: 2026-09-27
# P3 lift (system_decomposition_plan §8 Step 3): _edli_durable_fill_bridge_scan moved from
#   src.main to src.ingest.price_channel_ingest (it WRITES the durable bridge in the P3
#   reconcile cycle; src.main's boot recovery imports the SAME canonical copy). Logic unchanged.
# Authority basis: MF-1 / DEFECT-1 — durable self-healing EDLI fill -> position_current
#   bridge. Verified defect: the position bridge in src/main.py was driven SOLELY
#   by the transient in-memory set ``_edli_fill_bridge_aggregate_ids`` (populated
#   only from inbox rows that went PENDING->PROCESSED THIS cycle). A daemon death
#   OR a swallowed bridge exception between the world-conn commit (inbox marked
#   PROCESSED) and the separate bridge commit leaves a FILL_CONFIRMED aggregate
#   with NO position_current row; on restart the in-memory set is empty, so the
#   fill is orphaned forever (stuck capital, invisible to chain-reconcile / exit /
#   harvester / redeem). The fix is a DURABLE IDEMPOTENT SCAN that finds every
#   aggregate with a UserTradeObserved FILL_CONFIRMED whose
#   ``edli_bridge_position_id`` has no position_current row, and bridges each —
#   run EACH CYCLE and AT BOOT, independent of the transient set.
"""Relationship test: the durable fill -> position_current bridge scan.

Cross-module invariant under test (NOT a single-function unit test):

    For every aggregate in ``edli_live_order_events`` that carries a
    ``UserTradeObserved`` event with ``fill_authority_state == "FILL_CONFIRMED"``,
    a canonical ``position_current`` row keyed by ``edli_bridge_position_id``
    MUST exist after the bridge step runs — EVEN WHEN the transient in-memory
    set that previously gated the bridge is EMPTY (the post-restart /
    post-swallowed-exception state).

The seed inserts the exact ``edli_live_order_events`` rows the production
aggregate persists (PreSubmitRevalidated identity + UserTradeObserved fill
economics) and asserts the durable scan materialises the position with the in-
memory set never populated. This reproduces the ORPHAN WINDOW: the events are
durable on disk, but no in-cycle trigger fires for them.

Idempotency is asserted directly: running the scan twice yields exactly one
position_current row and no duplicate ENTRY position_events.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Seed helpers — write the exact rows the production EDLI aggregate persists.
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    """A single connection carrying both the trade-owned tables
    (position_current / position_events) and the EDLI events table.

    In production the bridge runs on a trade connection with ``world`` ATTACHed;
    the bridge's ``_edli_events_table`` resolves to ``world.edli_live_order_events``
    when ``world`` is attached and to the unqualified table on a single
    connection. A single ``init_schema`` connection that also owns
    ``edli_live_order_events`` exercises the identical bridge read/write path
    (same SQL, same canonical write helpers).
    """
    from src.state.db import init_schema
    from src.state.schema.edli_live_order_events_schema import ensure_tables as edli_ensure

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    edli_ensure(conn)
    return conn


def _insert_event(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    sequence: int,
    event_type: str,
    payload: dict,
    source_authority: str,
) -> None:
    payload_json = json.dumps(payload, sort_keys=True)
    event_hash = hashlib.sha256(
        f"{aggregate_id}|{sequence}|{event_type}|{payload_json}".encode("utf-8")
    ).hexdigest()
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            f"edli_live_order_event:{event_hash[:32]}",
            aggregate_id,
            sequence,
            event_type,
            None,
            event_hash,
            payload_json,
            payload_hash,
            source_authority,
            "2026-06-01T00:00:00+00:00",
            "2026-06-01T00:00:00+00:00",
        ),
    )


def _seed_confirmed_fill_aggregate(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    direction: str = "buy_no",
    token_id: str = "0xNOTOKEN",
    filled_size: float = 120.0,
    avg_fill_price: float = 0.42,
    execution_command_id: str | None = None,
    venue_order_id: str = "vo-MF1",
) -> None:
    """Persist a FILL_CONFIRMED aggregate WITHOUT a position_current row.

    This is exactly the durable state after a daemon death / swallowed bridge
    exception: the EDLI events are committed on disk, but the position was never
    materialised, and (post-restart) no in-memory trigger remembers it.
    """
    _insert_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=1,
        event_type="PreSubmitRevalidated",
        payload={
            "event_id": aggregate_id.split(":")[0],
            "final_intent_id": aggregate_id.split(":")[-1],
            "condition_id": "0xCONDITION",
            "token_id": token_id,
            "direction": direction,
            "city": "Tokyo",
            "target_date": "2026-06-02",
            "bin_label": "high_29_30",
            "metric": "high",
            "unit": "C",
            "q_live": 0.62,
            "executable_snapshot_id": "snap-MF1",
            "market_id": "mkt-MF1",
            "strategy_key": "opening_inertia",
        },
        source_authority="decision_kernel",
    )
    sequence = 2
    if execution_command_id:
        _insert_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=sequence,
            event_type="ExecutionCommandCreated",
            payload={
                "event_id": aggregate_id.split(":")[0],
                "final_intent_id": aggregate_id.split(":")[-1],
                "execution_command_id": execution_command_id,
            },
            source_authority="engine_adapter",
        )
        sequence += 1
    _insert_event(
        conn,
        aggregate_id=aggregate_id,
        sequence=sequence,
        event_type="UserTradeObserved",
        payload={
            "event_id": aggregate_id.split(":")[0],
            "final_intent_id": aggregate_id.split(":")[-1],
            "fill_authority_state": "FILL_CONFIRMED",
            "trade_status": "CONFIRMED",
            "filled_size": filled_size,
            "avg_fill_price": avg_fill_price,
            "fees": 0.13,
            "venue_order_id": venue_order_id,
        },
        source_authority="user_channel",
    )
    conn.commit()


def _seed_interrupted_command_link(conn: sqlite3.Connection, *, conflict: bool = False) -> tuple[str, str]:
    """Confirmed EDLI fill survived while command-link convergence was interrupted."""
    from src.events.edli_position_bridge import edli_bridge_position_id
    from src.state.collateral_ledger import init_collateral_schema
    from src.state.db import init_schema_trade_only
    from src.state.venue_command_repo import append_event, append_order_fact, append_trade_fact

    init_schema_trade_only(conn)
    init_collateral_schema(conn)
    aggregate_id = "evt-link-retry:intent-link-retry"
    command_id = "cmd-link-retry"
    token_id = "token-link-retry"
    order_id = "order-link-retry"
    _seed_confirmed_fill_aggregate(
        conn, aggregate_id=aggregate_id, direction="buy_yes", token_id=token_id,
        filled_size=7.99, avg_fill_price=0.50,
        execution_command_id=f"edli_exec_cmd:{aggregate_id}", venue_order_id=order_id,
    )
    canonical_id = edli_bridge_position_id(aggregate_id)
    conn.execute(
        """INSERT INTO position_current
           (position_id, phase, trade_id, market_id, city, cluster, target_date,
            bin_label, direction, unit, size_usd, shares, cost_basis_usd,
            entry_price, p_posterior, decision_snapshot_id, entry_method,
            strategy_key, chain_state, token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric, fill_authority,
            chain_shares, chain_avg_price, chain_cost_basis_usd)
           VALUES (?, 'day0_window', ?, '0xCONDITION', 'Tokyo', 'Tokyo',
                   '2026-06-02', 'high_29_30', 'buy_yes', 'C', 3.995, 7.99,
                   3.995, 0.50, 0.62, 'snap-MF1', 'qkernel_spine',
                   'opening_inertia', 'synced', ?, '0xCONDITION', ?, 'filled',
                   '2026-06-01T00:01:00+00:00', 'high', 'venue_confirmed_full',
                   7.99, 0.50, 3.995)""",
        (canonical_id, canonical_id, token_id, order_id),
    )
    if conflict:
        conn.execute(
            """INSERT INTO position_current
               (position_id, phase, trade_id, updated_at, temperature_metric)
               VALUES ('short-link-retry', 'active', 'short-link-retry',
                       '2026-06-01T00:00:00+00:00', 'high')"""
        )
    conn.execute(
        """INSERT INTO venue_commands
           (command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, venue_order_id, state, created_at, updated_at)
           VALUES (?, 'snap-MF1', 'envelope-link-retry', 'short-link-retry', ?,
                   'idem-link-retry', 'ENTRY', '0xCONDITION', ?, 'BUY', 10, 0.50,
                   ?, 'ACKED', '2026-06-01T00:00:00+00:00',
                   '2026-06-01T00:00:00+00:00')""",
        (command_id, f"edli_exec_cmd:{aggregate_id}", token_id, order_id),
    )
    at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    append_event(conn, command_id=command_id, event_type="CANCEL_REQUESTED",
                 occurred_at=at.isoformat(), payload={"cancel_reason": "BOOK_MOVED"})
    append_trade_fact(
        conn, trade_id="trade-link-retry", venue_order_id=order_id,
        command_id=command_id, state="CONFIRMED", filled_size="7.99",
        fill_price="0.50", source="WS_USER", observed_at=at,
        raw_payload_hash=hashlib.sha256(b"trade-link-retry").hexdigest(),
        raw_payload_json={"proof": "authenticated_trade"},
    )
    append_order_fact(
        conn, venue_order_id=order_id, command_id=command_id,
        state="CANCEL_CONFIRMED", remaining_size="2.01", matched_size="7.99",
        source="WS_USER", observed_at=at,
        raw_payload_hash=hashlib.sha256(b"order-link-retry").hexdigest(),
        raw_payload_json={"proof": "venue_cancelled_remainder"},
    )
    append_event(conn, command_id=command_id, event_type="CANCEL_ACKED",
                 occurred_at=at.isoformat(),
                 payload={"venue_order_id": order_id, "cancel_outcome": {"status": "CANCELED"}})
    conn.commit()
    return canonical_id, command_id


# ---------------------------------------------------------------------------
# RED — the orphan reproduction.
# ---------------------------------------------------------------------------


class TestDurableFillBridgeScan:
    def test_interrupted_link_retries_exact_debt_and_sources_terminal_fill(self):
        from src.ingest.price_channel_ingest import (
            _edli_orphaned_command_link_candidates,
            _edli_repair_orphaned_command_link,
        )
        from src.riskguard.riskguard import _portfolio_position_from_loader_row
        from src.state.db import query_portfolio_loader_view

        conn = _make_conn()
        position_id, command_id = _seed_interrupted_command_link(conn)
        assert _edli_orphaned_command_link_candidates(conn, limit=1) == (
            ("evt-link-retry:intent-link-retry", command_id, position_id),
        )
        with pytest.raises(ValueError, match="fill-grade loader row missing"):
            row = next(row for row in query_portfolio_loader_view(
                conn, runtime_exposure_only=True,
            )["positions"] if row["position_id"] == position_id)
            _portfolio_position_from_loader_row(row)

        assert _edli_repair_orphaned_command_link(
            conn, aggregate_id="evt-link-retry:intent-link-retry",
            command_id=command_id, position_id=position_id,
            now=datetime(2026, 6, 1, 0, 2, tzinfo=timezone.utc),
        )
        assert conn.execute(
            "SELECT position_id FROM venue_commands WHERE command_id = ?", (command_id,)
        ).fetchone()[0] == position_id
        fact = conn.execute(
            "SELECT position_id, shares, fill_price FROM execution_fact WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        assert fact is not None and fact["position_id"] == position_id
        assert float(fact["shares"]) == pytest.approx(7.99)
        assert float(fact["fill_price"]) == pytest.approx(0.5)
        row = next(row for row in query_portfolio_loader_view(
            conn, runtime_exposure_only=True,
        )["positions"] if row["position_id"] == position_id)
        assert _portfolio_position_from_loader_row(row).trade_id == position_id

        assert _edli_orphaned_command_link_candidates(conn, limit=1) == ()
        assert not _edli_repair_orphaned_command_link(
            conn, aggregate_id="evt-link-retry:intent-link-retry",
            command_id=command_id, position_id=position_id,
            now=datetime(2026, 6, 1, 0, 3, tzinfo=timezone.utc),
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM execution_fact WHERE command_id = ?", (command_id,)
        ).fetchone()[0] == 1

    def test_orphaned_link_retry_refuses_real_conflicting_position(self):
        from src.ingest.price_channel_ingest import (
            _edli_orphaned_command_link_candidates,
            _edli_repair_orphaned_command_link,
        )

        conn = _make_conn()
        position_id, command_id = _seed_interrupted_command_link(conn, conflict=True)
        assert _edli_orphaned_command_link_candidates(conn, limit=1) == ()
        assert not _edli_repair_orphaned_command_link(
            conn, aggregate_id="evt-link-retry:intent-link-retry",
            command_id=command_id, position_id=position_id,
            now=datetime(2026, 6, 1, 0, 2, tzinfo=timezone.utc),
        )
        assert conn.execute(
            "SELECT position_id FROM venue_commands WHERE command_id = ?", (command_id,)
        ).fetchone()[0] == "short-link-retry"
        assert conn.execute(
            "SELECT COUNT(*) FROM execution_fact WHERE position_id = ?", (position_id,)
        ).fetchone()[0] == 0

    def test_link_repair_failure_rolls_back_and_remains_retryable(self, monkeypatch):
        from src.execution import exchange_reconcile
        from src.ingest.price_channel_ingest import (
            _edli_orphaned_command_link_candidates,
            _edli_repair_orphaned_command_link,
        )

        conn = _make_conn()
        position_id, command_id = _seed_interrupted_command_link(conn)

        def interrupted(*_args, **_kwargs):
            raise RuntimeError("reconcile interrupted")

        monkeypatch.setattr(exchange_reconcile, "reconcile_persisted_terminal_late_entry_fills", interrupted)
        with pytest.raises(RuntimeError, match="reconcile interrupted"):
            _edli_repair_orphaned_command_link(
                conn, aggregate_id="evt-link-retry:intent-link-retry",
                command_id=command_id, position_id=position_id,
                now=datetime(2026, 6, 1, 0, 2, tzinfo=timezone.utc),
            )
        assert conn.execute(
            "SELECT position_id FROM venue_commands WHERE command_id = ?", (command_id,)
        ).fetchone()[0] == "short-link-retry"
        assert _edli_orphaned_command_link_candidates(conn, limit=1) == (
            ("evt-link-retry:intent-link-retry", command_id, position_id),
        )

    def test_periodic_repair_uses_one_bounded_tranche(self, monkeypatch):
        import src.ingest.price_channel_ingest as lane
        import src.events.price_channel_redecision_router as router

        conn = _make_conn()
        position_id, command_id = _seed_interrupted_command_link(conn)
        original_cursor = lane._edli_orphaned_command_link_cursor
        limits = []

        def link_debt(*, limit, after_command_id=None):
            debt = lane._edli_orphaned_command_link_candidates(
                conn, limit=limit, after_command_id=after_command_id,
            )
            return debt or (lane._edli_orphaned_command_link_candidates(conn, limit=limit)
                            if after_command_id else ())

        def fresh_debt(*, limit):
            limits.append(limit)
            return ()

        class Writer:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                self.lease = SimpleNamespace(acquired_at=time.monotonic())
                return self

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(lane, "_edli_trade_fact_bridge_candidates_read_only", lambda: ((), (), ()))
        monkeypatch.setattr(lane, "_edli_orphaned_command_link_candidates_read_only", link_debt)
        monkeypatch.setattr(lane, "_genuine_cancel_reassert_candidates_read_only",
                            lambda *, limit, after_command_id=None: ())
        monkeypatch.setattr(lane, "_edli_durable_fill_bridge_candidate_ids_read_only", fresh_debt)
        monkeypatch.setattr(lane, "_prepare_fill_bridge_write_connection", lambda *_a, **_k: conn)
        monkeypatch.setattr(lane, "_bound_fill_bridge_sqlite_wait_remaining", lambda *_a, **_k: None)
        monkeypatch.setattr(lane, "_PriceChannelWriteGate", Writer)
        monkeypatch.setattr(lane, "_close_fill_bridge_write_connection", lambda *_a: None)
        monkeypatch.setattr(router, "_edli_position_fill_redecision_cycle", lambda: 0)
        try:
            lane._edli_orphaned_command_link_cursor = ""
            result = lane._edli_fill_bridge_repair_cycle()
            assert result["scheduler_failed"] is False, result
            assert limits == [lane.FILL_BRIDGE_WRITE_TRANCHES_PER_TICK - 1]
            assert conn.execute(
                "SELECT position_id FROM venue_commands WHERE command_id = ?", (command_id,)
            ).fetchone()[0] == position_id
            assert conn.execute(
                "SELECT COUNT(*) FROM execution_fact WHERE command_id = ?", (command_id,)
            ).fetchone()[0] == 1
            result = lane._edli_fill_bridge_repair_cycle()
            assert result["scheduler_failed"] is False, result
            assert limits[-1] == lane.FILL_BRIDGE_WRITE_TRANCHES_PER_TICK
        finally:
            lane._edli_orphaned_command_link_cursor = original_cursor

    def test_failed_oldest_link_debt_does_not_starve_next_command(self, monkeypatch):
        import src.ingest.price_channel_ingest as lane
        import src.events.price_channel_redecision_router as router

        conn = _make_conn()
        original_cursor = lane._edli_orphaned_command_link_cursor
        debts = (("aggregate-a", "command-a", "position-a"),
                 ("aggregate-b", "command-b", "position-b"))
        attempts = []
        fresh_limits = []

        def link_debt(*, limit, after_command_id=None):
            assert limit == 1
            later = tuple(row for row in debts if row[1] > (after_command_id or ""))
            return (later or debts)[:limit]

        def repair(_conn, *, aggregate_id, command_id, position_id, now):
            attempts.append(command_id)
            if command_id == "command-a":
                raise RuntimeError("persistently incomplete proof")
            return True

        class Writer:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                self.lease = SimpleNamespace(acquired_at=time.monotonic())
                return self

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(lane, "_edli_trade_fact_bridge_candidates_read_only", lambda: ((), (), ()))
        monkeypatch.setattr(lane, "_edli_orphaned_command_link_candidates_read_only", link_debt)
        monkeypatch.setattr(lane, "_genuine_cancel_reassert_candidates_read_only",
                            lambda *, limit, after_command_id=None: ())
        monkeypatch.setattr(lane, "_edli_durable_fill_bridge_candidate_ids_read_only",
                            lambda *, limit: fresh_limits.append(limit) or ())
        monkeypatch.setattr(lane, "_edli_repair_orphaned_command_link", repair)
        monkeypatch.setattr(lane, "_prepare_fill_bridge_write_connection", lambda *_a, **_k: conn)
        monkeypatch.setattr(lane, "_bound_fill_bridge_sqlite_wait_remaining", lambda *_a, **_k: None)
        monkeypatch.setattr(lane, "_PriceChannelWriteGate", Writer)
        monkeypatch.setattr(lane, "_close_fill_bridge_write_connection", lambda *_a: None)
        monkeypatch.setattr(router, "_edli_position_fill_redecision_cycle", lambda: 0)
        try:
            lane._edli_orphaned_command_link_cursor = ""
            results = [lane._edli_fill_bridge_repair_cycle() for _ in range(3)]
            assert attempts == ["command-a", "command-b", "command-a"]
            assert [result["scheduler_failed"] for result in results] == [True, False, True]
            assert fresh_limits == [lane.FILL_BRIDGE_WRITE_TRANCHES_PER_TICK - 1] * 3
        finally:
            lane._edli_orphaned_command_link_cursor = original_cursor

    @pytest.mark.parametrize("stubborn_link", [False, True])
    def test_periodic_exact_slot_reasserts_prior_genuine_cancel_once(self, monkeypatch, stubborn_link):
        import src.ingest.price_channel_ingest as lane
        import src.events.price_channel_redecision_router as router
        from src.state.collateral_ledger import init_collateral_schema
        from src.state.db import init_schema_trade_only
        from tests.execution.test_genuine_cancel_carrying_a_fill_is_sourced import (
            _seed_legacy_reopened_genuine_cancel,
        )

        conn = _make_conn()
        init_schema_trade_only(conn)
        init_collateral_schema(conn)
        if stubborn_link:
            _seed_interrupted_command_link(conn)
        _seed_legacy_reopened_genuine_cancel(conn)
        conn.commit()
        original_cursor = lane._edli_orphaned_command_link_cursor
        limits = []

        def cancel_debt(*, limit, after_command_id=None):
            rows = lane._genuine_cancel_reassert_candidates(
                conn, limit=limit, after_command_id=after_command_id,
            )
            return rows or (lane._genuine_cancel_reassert_candidates(conn, limit=limit)
                            if after_command_id else ())

        class Writer:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                self.lease = SimpleNamespace(acquired_at=time.monotonic())
                return self

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(lane, "_edli_trade_fact_bridge_candidates_read_only", lambda: ((), (), ()))
        monkeypatch.setattr(lane, "_edli_orphaned_command_link_candidates_read_only",
                            lambda *, limit, after_command_id=None: (
                                lane._edli_orphaned_command_link_candidates(
                                    conn, limit=limit, after_command_id=after_command_id,
                                ) or (lane._edli_orphaned_command_link_candidates(conn, limit=limit)
                                      if after_command_id else ())
                            ) if stubborn_link else ())
        if stubborn_link:
            monkeypatch.setattr(lane, "_edli_repair_orphaned_command_link",
                                lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stubborn link")))
        monkeypatch.setattr(lane, "_genuine_cancel_reassert_candidates_read_only", cancel_debt)
        monkeypatch.setattr(lane, "_edli_durable_fill_bridge_candidate_ids_read_only",
                            lambda *, limit: limits.append(limit) or ())
        monkeypatch.setattr(lane, "_prepare_fill_bridge_write_connection", lambda *_a, **_k: conn)
        monkeypatch.setattr(lane, "_bound_fill_bridge_sqlite_wait_remaining", lambda *_a, **_k: None)
        monkeypatch.setattr(lane, "_PriceChannelWriteGate", Writer)
        monkeypatch.setattr(lane, "_close_fill_bridge_write_connection", lambda *_a: None)
        monkeypatch.setattr(router, "_edli_position_fill_redecision_cycle", lambda: 0)
        try:
            lane._edli_orphaned_command_link_cursor = ""
            assert lane._edli_durable_fill_bridge_work_exists_read_only()
            first = lane._edli_fill_bridge_repair_cycle()
            assert first["scheduler_failed"] is stubborn_link, first
            if stubborn_link:
                assert conn.execute("SELECT state FROM venue_commands WHERE command_id='cmd-m5'").fetchone()[0] == "PARTIAL"
                assert lane._edli_durable_fill_bridge_work_exists_read_only()
                second = lane._edli_fill_bridge_repair_cycle()
                assert second["scheduler_failed"] is False, second
            assert conn.execute("SELECT state FROM venue_commands WHERE command_id='cmd-m5'").fetchone()[0] == "CANCELLED"
            assert not lane._genuine_cancel_reassert_candidates(conn, limit=1)
            if not stubborn_link:
                assert not lane._edli_durable_fill_bridge_work_exists_read_only()
                second = lane._edli_fill_bridge_repair_cycle()
                assert second["scheduler_failed"] is False, second
            else:
                third = lane._edli_fill_bridge_repair_cycle()
                assert third["scheduler_failed"] is True, third
            assert [limit for limit in limits if limit > 1] == (
                [lane.FILL_BRIDGE_WRITE_TRANCHES_PER_TICK - 1] * 3
                if stubborn_link else [lane.FILL_BRIDGE_WRITE_TRANCHES_PER_TICK - 1,
                                      lane.FILL_BRIDGE_WRITE_TRANCHES_PER_TICK]
            )
            assert conn.execute(
                "SELECT COUNT(*) FROM venue_command_events WHERE command_id='cmd-m5' "
                "AND event_type='CANCEL_ACKED'"
            ).fetchone()[0] == 2
        finally:
            lane._edli_orphaned_command_link_cursor = original_cursor

    @pytest.mark.parametrize("expired_at_gate", [False, True])
    def test_exact_link_admission_remains_250ms_but_hold_starts_at_lease(
        self, monkeypatch, expired_at_gate,
    ):
        import src.ingest.price_channel_ingest as lane
        import src.events.price_channel_redecision_router as router

        conn = _make_conn()
        position_id, command_id = _seed_interrupted_command_link(conn)
        original_cursor = lane._edli_orphaned_command_link_cursor
        original_repair = lane._edli_repair_orphaned_command_link
        calls = []

        def prepare(_opener, *, deadline_monotonic):
            conn.set_progress_handler(
                lambda: int(time.monotonic() >= deadline_monotonic), 1_000,
            )
            return conn

        class Writer:
            def __init__(self, *, deadline_monotonic, **_kwargs):
                self.deadline = deadline_monotonic

            def __enter__(self):
                until = self.deadline + (0.002 if expired_at_gate else -0.012)
                time.sleep(max(0.0, until - time.monotonic()))
                self.lease = SimpleNamespace(acquired_at=time.monotonic())
                return self

            def __exit__(self, *_args):
                return False

        def repair(c, **kwargs):
            calls.append(kwargs["command_id"])
            time.sleep(0.025)  # Would exceed the stale admission progress deadline.
            return original_repair(c, **kwargs)

        monkeypatch.setattr(lane, "_edli_trade_fact_bridge_candidates_read_only", lambda: ((), (), ()))
        monkeypatch.setattr(lane, "_edli_orphaned_command_link_candidates_read_only",
                            lambda *, limit, after_command_id=None: (
                                ("evt-link-retry:intent-link-retry", command_id, position_id),
                            ))
        monkeypatch.setattr(lane, "_genuine_cancel_reassert_candidates_read_only",
                            lambda *, limit, after_command_id=None: ())
        monkeypatch.setattr(lane, "_edli_durable_fill_bridge_candidate_ids_read_only",
                            lambda *, limit: ())
        monkeypatch.setattr(lane, "_prepare_fill_bridge_write_connection", prepare)
        monkeypatch.setattr(lane, "_bound_fill_bridge_sqlite_wait_remaining", lambda *_a, **_k: None)
        monkeypatch.setattr(lane, "_PriceChannelWriteGate", Writer)
        monkeypatch.setattr(lane, "_edli_repair_orphaned_command_link", repair)
        monkeypatch.setattr(lane, "_close_fill_bridge_write_connection",
                            lambda c: c.set_progress_handler(None, 0))
        monkeypatch.setattr(router, "_edli_position_fill_redecision_cycle", lambda: 0)
        try:
            lane._edli_orphaned_command_link_cursor = ""
            result = lane._edli_fill_bridge_repair_cycle()
            fact_count = conn.execute(
                "SELECT COUNT(*) FROM execution_fact WHERE command_id=?", (command_id,),
            ).fetchone()[0]
            assert result["scheduler_failed"] is expired_at_gate, result
            assert calls == ([] if expired_at_gate else [command_id])
            assert fact_count == (0 if expired_at_gate else 1)
        finally:
            lane._edli_orphaned_command_link_cursor = original_cursor

    def test_exact_link_hold_timeout_rolls_back_before_commit(self, monkeypatch):
        import src.ingest.price_channel_ingest as lane
        import src.events.price_channel_redecision_router as router

        conn = _make_conn()
        position_id, command_id = _seed_interrupted_command_link(conn)
        original_cursor = lane._edli_orphaned_command_link_cursor

        class Writer:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                self.lease = SimpleNamespace(acquired_at=time.monotonic() - 0.97)
                return self

            def __exit__(self, *_args):
                return False

        def repair(c, **kwargs):
            c.execute("UPDATE venue_commands SET position_id='transient' WHERE command_id=?",
                      (kwargs["command_id"],))
            time.sleep(0.05)
            c.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<3000) "
                      "SELECT SUM(x) FROM n").fetchone()
            return True

        monkeypatch.setattr(lane, "_edli_trade_fact_bridge_candidates_read_only", lambda: ((), (), ()))
        monkeypatch.setattr(lane, "_edli_orphaned_command_link_candidates_read_only",
                            lambda *, limit, after_command_id=None: (
                                ("evt-link-retry:intent-link-retry", command_id, position_id),
                            ))
        monkeypatch.setattr(lane, "_genuine_cancel_reassert_candidates_read_only",
                            lambda *, limit, after_command_id=None: ())
        monkeypatch.setattr(lane, "_edli_durable_fill_bridge_candidate_ids_read_only",
                            lambda *, limit: ())
        monkeypatch.setattr(lane, "_prepare_fill_bridge_write_connection", lambda *_a, **_k: conn)
        monkeypatch.setattr(lane, "_bound_fill_bridge_sqlite_wait_remaining", lambda *_a, **_k: None)
        monkeypatch.setattr(lane, "_PriceChannelWriteGate", Writer)
        monkeypatch.setattr(lane, "_edli_repair_orphaned_command_link", repair)
        monkeypatch.setattr(lane, "_close_fill_bridge_write_connection",
                            lambda c: c.set_progress_handler(None, 0))
        monkeypatch.setattr(router, "_edli_position_fill_redecision_cycle", lambda: 0)
        try:
            lane._edli_orphaned_command_link_cursor = ""
            result = lane._edli_fill_bridge_repair_cycle()
            assert result["scheduler_failed"] is True, result
            assert conn.execute(
                "SELECT position_id FROM venue_commands WHERE command_id=?", (command_id,),
            ).fetchone()[0] == "short-link-retry"
            assert conn.execute(
                "SELECT COUNT(*) FROM execution_fact WHERE command_id=?", (command_id,),
            ).fetchone()[0] == 0
        finally:
            lane._edli_orphaned_command_link_cursor = original_cursor

    def test_orphaned_confirmed_fill_is_bridged_with_empty_in_memory_set(self):
        """ORPHAN WINDOW: a FILL_CONFIRMED aggregate with NO position_current row
        and NO in-memory trigger MUST still be materialised by the durable scan.

        Pre-fix this fails: the only bridge trigger was the transient
        ``_edli_fill_bridge_aggregate_ids`` set, never populated on restart for a
        fill that already passed the inbox PROCESSED commit. There is no durable
        scan, so position_current is never created -> capital orphaned.
        """
        from src.events.edli_position_bridge import edli_bridge_position_id
        from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        aggregate_id = "evtMF1:fiMF1"
        _seed_confirmed_fill_aggregate(conn, aggregate_id=aggregate_id)

        position_id = edli_bridge_position_id(aggregate_id)

        # Precondition: the orphan exists (events durable, position absent).
        before = conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()[0]
        assert before == 0, "precondition: orphan must start with no position_current row"

        # The durable scan is the authoritative bridge trigger. NO in-memory set
        # is passed — this models the post-restart / post-exception state.
        bridged = _edli_durable_fill_bridge_scan(conn, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        conn.commit()

        # THE ORPHAN MUST BE HEALED.
        after = conn.execute(
            "SELECT position_id, phase, shares, cost_basis_usd, direction, "
            "no_token_id, fill_authority FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()
        assert after is not None, (
            "ORPHAN: durable scan did not materialise position_current for a "
            "FILL_CONFIRMED aggregate (capital stuck, invisible to exit/redeem)"
        )
        assert after["phase"] == "active"
        assert after["shares"] == pytest.approx(120.0)
        assert after["cost_basis_usd"] == pytest.approx(120.0 * 0.42)
        assert after["direction"] == "buy_no"
        # buy_no token must land on no_token_id so chain reconciliation matches.
        assert after["no_token_id"] == "0xNOTOKEN"
        assert after["fill_authority"] == "venue_confirmed_full"
        assert bridged >= 1, "scan must report the number of orphans it bridged"

    def test_durable_scan_is_idempotent(self):
        """Running the scan twice yields exactly ONE position_current row and NO
        duplicate ENTRY position_events (the bridge is provably idempotent via
        ON CONFLICT(position_id) + append-only UNIQUE(position_id, sequence_no)).
        """
        from src.events.edli_position_bridge import edli_bridge_position_id
        from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        aggregate_id = "evtMF1b:fiMF1b"
        _seed_confirmed_fill_aggregate(conn, aggregate_id=aggregate_id)
        position_id = edli_bridge_position_id(aggregate_id)

        first = _edli_durable_fill_bridge_scan(conn, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        conn.commit()
        second = _edli_durable_fill_bridge_scan(conn, now=datetime(2026, 6, 1, 0, 5, tzinfo=timezone.utc))
        conn.commit()

        # First pass bridges the orphan; second pass finds nothing to bridge
        # (the position_current row already exists) -> the scan is self-quieting.
        assert first == 1
        assert second == 0

        pc_count = conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()[0]
        assert pc_count == 1, "idempotency: exactly one position_current row"

        entry_events = conn.execute(
            """
            SELECT COUNT(*) FROM position_events
            WHERE position_id = ? AND event_type = 'POSITION_OPEN_INTENT'
            """,
            (position_id,),
        ).fetchone()[0]
        assert entry_events == 1, "idempotency: no duplicate ENTRY position_events"

    def test_bounded_multi_fill_drain_monotonically_reduces_orphan_backlog(self):
        """A bounded repair pass cannot leave later confirmed fills indefinitely pending."""
        from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        aggregate_ids = [f"evt-drain-{index}:fill-drain-{index}" for index in range(3)]
        for index, aggregate_id in enumerate(aggregate_ids):
            _seed_confirmed_fill_aggregate(
                conn,
                aggregate_id=aggregate_id,
                token_id=f"0xNOTOKEN{index}",
                execution_command_id=f"cmd-drain-{index}",
                venue_order_id=f"vo-drain-{index}",
            )

        def _remaining() -> int:
            return conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0]

        remaining_before = len(aggregate_ids) - _remaining()
        first = _edli_durable_fill_bridge_scan(
            conn,
            now=datetime(2026, 6, 1, tzinfo=timezone.utc),
            limit=2,
        )
        conn.commit()
        remaining_after_first = len(aggregate_ids) - _remaining()
        second = _edli_durable_fill_bridge_scan(
            conn,
            now=datetime(2026, 6, 1, 0, 5, tzinfo=timezone.utc),
            limit=2,
        )
        conn.commit()
        remaining_after_second = len(aggregate_ids) - _remaining()

        assert first == 2
        assert second == 1
        assert remaining_before > remaining_after_first > remaining_after_second == 0

    def test_existing_position_scan_repairs_stale_venue_command_position_link(self):
        """Already-bridged EDLI fills still need command-journal convergence.

        Live regression: position_current existed under the canonical EDLI id,
        while venue_commands.position_id still held a pre-bridge short id. The
        durable scan used to skip immediately on the existing position row,
        leaving command/PnL/redecision joins split.
        """
        from src.events.edli_position_bridge import edli_bridge_position_id
        from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        aggregate_id = "evtMF1d:fiMF1d"
        execution_command_id = "edli_exec_cmd:evtMF1d:fiMF1d"
        _seed_confirmed_fill_aggregate(
            conn,
            aggregate_id=aggregate_id,
            execution_command_id=execution_command_id,
        )
        position_id = edli_bridge_position_id(aggregate_id)
        conn.execute(
            """INSERT INTO position_current
               (position_id, phase, trade_id, strategy_key, updated_at, temperature_metric)
               VALUES (?, 'active', ?, 'opening_inertia', '2026-06-01T00:00:00+00:00', 'high')""",
            (position_id, position_id),
        )
        conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, snapshot_id, envelope_id, position_id, decision_id,
                idempotency_key, intent_kind, market_id, token_id, side, size,
                price, venue_order_id, state, last_event_id, created_at, updated_at,
                review_required_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                "cmd-mf1d",
                "snap-mf1d",
                "env-mf1d",
                "short-mf1d",
                execution_command_id,
                "idem-mf1d",
                "ENTRY",
                "mkt-MF1",
                "0xNOTOKEN",
                "BUY",
                120.0,
                0.42,
                "vo-MF1",
                "FILLED",
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
            ),
        )
        conn.commit()

        bridged = _edli_durable_fill_bridge_scan(
            conn,
            now=datetime(2026, 6, 1, 0, 5, tzinfo=timezone.utc),
            already_bridged_repair_limit=1,
        )
        conn.commit()

        assert bridged == 0
        command = conn.execute(
            "SELECT position_id, updated_at FROM venue_commands WHERE command_id = 'cmd-mf1d'"
        ).fetchone()
        assert command["position_id"] == position_id
        assert command["updated_at"] == "2026-06-01T00:05:00+00:00"
        provenance = conn.execute(
            """
            SELECT event_type, payload_json
              FROM provenance_envelope_events
             WHERE subject_type = 'command'
               AND subject_id = 'cmd-mf1d'
               AND event_type = 'POSITION_LINK_REPAIRED'
            """
        ).fetchone()
        assert provenance is not None
        assert "short-mf1d" in provenance["payload_json"]
        assert position_id in provenance["payload_json"]

    def test_default_scan_does_not_repair_already_bridged_historical_projection(self):
        """Live hot path must not rewrite already-materialised historical rows.

        The durable scan's default responsibility is confirmed-fill recoverability:
        bridge FILL_CONFIRMED aggregates that still have no position_current row.
        Historical projection/link repairs remain available as explicit maintenance
        work through ``already_bridged_repair_limit``; they are not allowed to run
        every reconcile cycle and contend with fresh substrate/redecision writes.
        """
        from src.events.edli_position_bridge import edli_bridge_position_id
        from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        aggregate_id = "evtMF1hot:fiMF1hot"
        execution_command_id = "edli_exec_cmd:evtMF1hot:fiMF1hot"
        _seed_confirmed_fill_aggregate(
            conn,
            aggregate_id=aggregate_id,
            execution_command_id=execution_command_id,
        )
        position_id = edli_bridge_position_id(aggregate_id)
        conn.execute(
            """INSERT INTO position_current
               (position_id, phase, trade_id, strategy_key, p_posterior,
                entry_method, updated_at, temperature_metric)
               VALUES (?, 'active', ?, 'opening_inertia', 0.0,
                       'ens_member_counting', '2026-06-01T00:00:00+00:00', 'high')""",
            (position_id, position_id),
        )
        conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, snapshot_id, envelope_id, position_id, decision_id,
                idempotency_key, intent_kind, market_id, token_id, side, size,
                price, venue_order_id, state, last_event_id, created_at, updated_at,
                review_required_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                "cmd-mf1hot",
                "snap-mf1hot",
                "env-mf1hot",
                "legacy-mf1hot",
                execution_command_id,
                "idem-mf1hot",
                "ENTRY",
                "mkt-MF1",
                "0xNOTOKEN",
                "BUY",
                120.0,
                0.42,
                "vo-MF1hot",
                "FILLED",
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
            ),
        )
        conn.commit()

        bridged = _edli_durable_fill_bridge_scan(
            conn,
            now=datetime(2026, 6, 1, 0, 5, tzinfo=timezone.utc),
        )
        conn.commit()

        assert bridged == 0
        position = conn.execute(
            """
            SELECT p_posterior, entry_method, updated_at
              FROM position_current
             WHERE position_id = ?
            """,
            (position_id,),
        ).fetchone()
        assert float(position["p_posterior"]) == 0.0
        assert position["entry_method"] == "ens_member_counting"
        assert position["updated_at"] == "2026-06-01T00:00:00+00:00"
        command = conn.execute(
            "SELECT position_id, updated_at FROM venue_commands WHERE command_id = 'cmd-mf1hot'"
        ).fetchone()
        assert command["position_id"] == "legacy-mf1hot"
        assert command["updated_at"] == "2026-06-01T00:00:00+00:00"

    def test_existing_position_command_link_noop_does_not_require_strategy_identity(self):
        """Already-linked historical aggregates must not re-parse entry strategy.

        Live regression: old confirmed-fill aggregates had enough command identity
        for command-link convergence but lacked strategy_key/event_type in
        PreSubmitRevalidated. The already-bridged scan path re-ran the full
        position identity parser anyway, emitted EDLI_BRIDGE_STRATEGY_MISSING on
        every reconcile cycle, and let that historical repair lane starve fresh
        user-channel liveness.
        """
        from src.events.edli_position_bridge import (
            edli_bridge_position_id,
            sync_venue_command_position_link_for_edli_fill,
        )

        conn = _make_conn()
        aggregate_id = "evtMF1e:fiMF1e"
        execution_command_id = "edli_exec_cmd:evtMF1e:fiMF1e"
        _insert_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="PreSubmitRevalidated",
            payload={
                "event_id": "evtMF1e",
                "final_intent_id": "fiMF1e",
                "condition_id": "0xCONDITION",
                "token_id": "0xNOTOKEN",
                "direction": "buy_no",
                "city": "Tokyo",
                "target_date": "2026-06-02",
                "bin_label": "high_29_30",
                "metric": "high",
                "unit": "C",
                "market_id": "mkt-MF1",
                "executable_snapshot_id": "snap-mf1e",
            },
            source_authority="decision_kernel",
        )
        _insert_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="ExecutionCommandCreated",
            payload={"execution_command_id": execution_command_id},
            source_authority="engine_adapter",
        )
        _insert_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=3,
            event_type="UserTradeObserved",
            payload={
                "fill_authority_state": "FILL_CONFIRMED",
                "trade_status": "CONFIRMED",
                "filled_size": 120.0,
                "avg_fill_price": 0.42,
                "venue_order_id": "vo-MF1e",
            },
            source_authority="user_channel",
        )
        position_id = edli_bridge_position_id(aggregate_id)
        conn.execute(
            """INSERT INTO position_current
               (position_id, phase, trade_id, strategy_key, updated_at, temperature_metric,
                decision_snapshot_id)
               VALUES (?, 'active', ?, 'opening_inertia', '2026-06-01T00:00:00+00:00', 'high', ?)""",
            (position_id, position_id, "snap-mf1e"),
        )
        conn.execute(
            """
            INSERT INTO venue_commands (
                command_id, snapshot_id, envelope_id, position_id, decision_id,
                idempotency_key, intent_kind, market_id, token_id, side, size,
                price, venue_order_id, state, last_event_id, created_at, updated_at,
                review_required_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                "cmd-mf1e",
                "snap-mf1e",
                "env-mf1e",
                position_id,
                execution_command_id,
                "idem-mf1e",
                "ENTRY",
                "mkt-MF1",
                "0xNOTOKEN",
                "BUY",
                120.0,
                0.42,
                "vo-MF1e",
                "FILLED",
                "2026-06-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
            ),
        )
        conn.commit()

        repaired = sync_venue_command_position_link_for_edli_fill(
            conn,
            aggregate_id,
            position_id=position_id,
            now=datetime(2026, 6, 1, 0, 5, tzinfo=timezone.utc),
        )

        assert repaired is False

    def test_non_confirmed_fill_is_not_bridged(self):
        """A MATCHED-only aggregate (no FILL_CONFIRMED) must NOT be bridged — the
        scan keys strictly on FILL_CONFIRMED so pending fills do not leak into
        position_current.
        """
        from src.events.edli_position_bridge import edli_bridge_position_id
        from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        aggregate_id = "evtMF1c:fiMF1c"
        _insert_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=1,
            event_type="PreSubmitRevalidated",
            payload={
                "event_id": "evtMF1c",
                "final_intent_id": "fiMF1c",
                "condition_id": "0xCONDITION",
                "token_id": "0xYESTOKEN",
                "direction": "buy_yes",
                "city": "Paris",
                "target_date": "2026-06-02",
                "bin_label": "high_20_21",
                "metric": "high",
                "unit": "C",
                "q_live": 0.5,
                "executable_snapshot_id": "snap-c",
                "market_id": "mkt-c",
                "strategy_key": "settlement_capture",
            },
            source_authority="decision_kernel",
        )
        _insert_event(
            conn,
            aggregate_id=aggregate_id,
            sequence=2,
            event_type="UserTradeObserved",
            payload={
                "event_id": "evtMF1c",
                "final_intent_id": "fiMF1c",
                "fill_authority_state": "MATCHED_PENDING_FINALITY",
                "trade_status": "MATCHED",
                "filled_size": 50.0,
                "avg_fill_price": 0.30,
                "venue_order_id": "vo-c",
            },
            source_authority="user_channel",
        )
        conn.commit()

        bridged = _edli_durable_fill_bridge_scan(conn, now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        conn.commit()

        position_id = edli_bridge_position_id(aggregate_id)
        row = conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE position_id = ?",
            (position_id,),
        ).fetchone()[0]
        assert row == 0, "MATCHED-only fill must NOT be bridged"
        assert bridged == 0

    def test_legacy_short_id_row_not_rebridged(self):
        """Relationship test (FIX #96 idempotency): a position_current row written
        BEFORE the SHA-256 widening (11-char id, e.g. 'edlid75be65') MUST be
        detected by the durable scan so the same aggregate is NOT re-bridged into
        a second row.

        RED under naive widening: ``_edli_durable_fill_bridge_scan`` probed only
        the new 68-char id, missed the legacy 11-char row, and materialised a
        duplicate — a second position_current row for the same real fill =
        duplicate capital ownership = live-money hazard.

        GREEN after dual-probe fix: scan checks both the wide AND legacy id;
        finds the legacy row; skips bridging; no duplicate created.

        Uses the brute-force collision pair aggregate_id 'agg-1508' (legacy id =
        'edlid75be65') as the known legacy row stand-in.
        """
        from src.events.edli_position_bridge import (
            edli_bridge_position_id,
            edli_bridge_position_id_legacy,
        )
        from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        # 'agg-1508' → legacy short id 'edlid75be65' (brute-force verified)
        aggregate_id = "agg-1508"
        legacy_id = edli_bridge_position_id_legacy(aggregate_id)
        wide_id = edli_bridge_position_id(aggregate_id)
        assert legacy_id == "edlid75be65"
        assert legacy_id != wide_id, "sanity: legacy and wide ids must differ"

        # Simulate a legacy row: EDLI events present on disk (fill confirmed),
        # but position_current was written with the OLD short id.
        _seed_confirmed_fill_aggregate(conn, aggregate_id=aggregate_id)
        conn.execute(
            """INSERT INTO position_current
               (position_id, phase, trade_id, strategy_key, updated_at, temperature_metric)
               VALUES (?, 'active', ?, 'settlement_capture', '2026-06-01T00:00:00', 'high')""",
            (legacy_id, legacy_id),
        )
        conn.commit()

        # Precondition: exactly one row exists, under the legacy short id.
        before = conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0]
        assert before == 1, f"precondition: one legacy row; got {before}"
        assert conn.execute(
            "SELECT COUNT(*) FROM position_current WHERE position_id = ?", (legacy_id,)
        ).fetchone()[0] == 1

        bridged = _edli_durable_fill_bridge_scan(
            conn, now=__import__("datetime").datetime(2026, 6, 3, tzinfo=__import__("datetime").timezone.utc)
        )
        conn.commit()

        # The scan MUST detect the legacy row and skip re-bridging.
        total = conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0]
        assert total == 1, (
            f"Duplicate row created: scan re-bridged a legacy-id aggregate "
            f"(expected 1 row, got {total}). Live-money hazard: duplicate capital ownership."
        )
        assert bridged == 0, (
            f"scan reported bridged={bridged} but the legacy row should have been found"
        )

    def test_already_bridged_positions_are_loaded_once_per_scan(self):
        """A healthy minute must not issue one position lookup per historical fill."""
        from src.events.edli_position_bridge import edli_bridge_position_id
        from src.ingest.price_channel_ingest import _edli_durable_fill_bridge_scan

        conn = _make_conn()
        for index in range(40):
            aggregate_id = f"already-bridged-{index}"
            _seed_confirmed_fill_aggregate(conn, aggregate_id=aggregate_id)
            position_id = edli_bridge_position_id(aggregate_id)
            conn.execute(
                """
                INSERT INTO position_current
                    (position_id, phase, trade_id, strategy_key, updated_at,
                     temperature_metric, p_posterior, entry_method)
                VALUES (?, 'active', ?, 'settlement_capture',
                        '2026-06-01T00:00:00', 'high', 0.8, 'qkernel')
                """,
                (position_id, position_id),
            )
        conn.commit()

        statements = []
        conn.set_trace_callback(statements.append)
        bridged = _edli_durable_fill_bridge_scan(
            conn,
            now=datetime(2026, 6, 3, tzinfo=timezone.utc),
        )
        conn.set_trace_callback(None)

        projection_reads = [
            statement
            for statement in statements
            if "SELECT position_id, p_posterior, entry_method, phase"
            in statement
            and "FROM position_current" in statement
        ]
        point_reads = [
            statement
            for statement in statements
            if "FROM position_current" in statement
            and ("WHERE position_id IN" in statement or "WHERE position_id =" in statement)
        ]
        assert bridged == 0
        assert len(projection_reads) == 1
        assert point_reads == []
