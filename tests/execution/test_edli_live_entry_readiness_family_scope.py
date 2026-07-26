# Created: 2026-07-25
# Last reused or audited: 2026-07-25
# Authority basis: 7-day production block-event audit (32,763 blocking
#   instances, 20.97h/7d global entry admission blocked, worst episode
#   7h17m) -- the composite live-block string
#   "entry_readiness:EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:N,
#   EDLI_STAGE_LIVE_CAP_RESERVED:N" is a coupled pair (one stuck order holds
#   both an unresolved projection row and its cap reservation) and was 100%
#   of observed blocking instances. Both whole-universe COUNT(*) gates are
#   narrowed to per-family together in this commit.
"""Per-family narrowing of the EDLI live entry-readiness BUY gate.

Before this change, ``src.main._edli_live_entry_readiness_block`` returned one
block-reason string built from whole-universe ``COUNT(*)`` queries over
``edli_live_order_projection`` (pending_reconcile) and ``edli_live_cap_usage``
(RESERVED), so one stuck order blocked new-entry BUY admission for every
family. These tests assert the narrowed contract: a stuck order blocks only
its own resolvable family_id; an order whose family_id cannot be resolved
still fails closed to a global block (never fails open).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import src.main as main_mod
from src.state.schema.edli_live_cap_usage_schema import ensure_table as _ensure_cap_usage_table
from src.state.schema.edli_live_order_events_schema import ensure_tables as _ensure_live_order_tables

FIXED_NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _make_world_db(tmp_path) -> str:
    path = tmp_path / "world.db"
    conn = sqlite3.connect(str(path))
    try:
        _ensure_live_order_tables(conn)
        _ensure_cap_usage_table(conn)
        conn.commit()
    finally:
        conn.close()
    return str(path)


def _insert_submit_plan(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    event_id: str,
    final_intent_id: str,
    family_id: str | None,
    sequence: int = 1,
) -> None:
    payload = {
        "event_id": event_id,
        "final_intent_id": final_intent_id,
        "condition_id": "cond-1",
        "token_id": "token-1",
        "direction": "BUY",
        "city": "Dallas",
        "target_date": "2026-07-25",
        "metric": "high",
        "order_type": "limit",
        "time_in_force": "GTC",
        "post_only": True,
        "limit_price": 0.5,
        "size": 10,
    }
    if family_id is not None:
        payload["family_id"] = family_id
    conn.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, 'SubmitPlanBuilt', NULL, ?, ?, ?, 'engine_adapter', ?, ?, 1)
        """,
        (
            f"{aggregate_id}:{sequence}",
            aggregate_id,
            sequence,
            f"event-hash-{aggregate_id}-{sequence}",
            json.dumps(payload),
            f"payload-hash-{aggregate_id}-{sequence}",
            FIXED_NOW.isoformat(),
            FIXED_NOW.isoformat(),
        ),
    )


def _insert_projection(
    conn: sqlite3.Connection,
    *,
    aggregate_id: str,
    event_id: str,
    final_intent_id: str,
    pending_reconcile: bool,
    current_state: str = "SUBMIT_PLAN_BUILT",
) -> None:
    conn.execute(
        """
        INSERT INTO edli_live_order_projection (
            aggregate_id, event_id, final_intent_id, current_state,
            last_sequence, last_event_type, last_event_hash, pending_reconcile,
            venue_order_id, updated_at, schema_version
        ) VALUES (?, ?, ?, ?, 1, 'SubmitPlanBuilt', 'projection-hash', ?, NULL, ?, 1)
        """,
        (
            aggregate_id,
            event_id,
            final_intent_id,
            current_state,
            int(pending_reconcile),
            FIXED_NOW.isoformat(),
        ),
    )


def _insert_cap_reservation(
    conn: sqlite3.Connection,
    *,
    usage_id: str,
    event_id: str,
    final_intent_id: str,
    reservation_status: str = "RESERVED",
) -> None:
    conn.execute(
        """
        INSERT INTO edli_live_cap_usage (
            usage_id, event_id, decision_time, cap_scope, max_notional_usd,
            max_orders_per_day, reserved_notional_usd, order_count,
            reservation_status, final_intent_id, execution_command_id,
            created_at, schema_version
        ) VALUES (?, ?, ?, 'live_execution_reservation', 10, 1, 10, 1, ?, ?, NULL, ?, 1)
        """,
        (
            usage_id,
            event_id,
            FIXED_NOW.isoformat(),
            reservation_status,
            final_intent_id,
            FIXED_NOW.isoformat(),
        ),
    )


# ---------------------------------------------------------------------------
# _edli_stage_pending_reconcile_families
# ---------------------------------------------------------------------------


def test_pending_reconcile_families_resolves_stuck_row_to_its_family(tmp_path):
    world_db = _make_world_db(tmp_path)
    conn = sqlite3.connect(world_db)
    conn.row_factory = sqlite3.Row
    try:
        _insert_submit_plan(
            conn,
            aggregate_id="agg-a",
            event_id="evt-a",
            final_intent_id="fi-a",
            family_id="family_a",
        )
        _insert_projection(
            conn,
            aggregate_id="agg-a",
            event_id="evt-a",
            final_intent_id="fi-a",
            pending_reconcile=True,
        )
        conn.commit()

        family_reasons, unresolved = main_mod._edli_stage_pending_reconcile_families(conn)
    finally:
        conn.close()

    assert family_reasons == {"family_a": "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1"}
    assert unresolved == 0


def test_pending_reconcile_families_falls_back_when_family_unresolvable(tmp_path):
    world_db = _make_world_db(tmp_path)
    conn = sqlite3.connect(world_db)
    conn.row_factory = sqlite3.Row
    try:
        # Pre-submit orphan: a projection row with no persisted SubmitPlanBuilt
        # plan to resolve family_id from.
        _insert_projection(
            conn,
            aggregate_id="agg-orphan",
            event_id="evt-orphan",
            final_intent_id="fi-orphan",
            pending_reconcile=True,
            current_state="DECISION_PROOF_ACCEPTED",
        )
        conn.commit()

        family_reasons, unresolved = main_mod._edli_stage_pending_reconcile_families(conn)
    finally:
        conn.close()

    assert family_reasons == {}
    assert unresolved == 1


# ---------------------------------------------------------------------------
# _edli_stage_open_cap_reservation_families
# ---------------------------------------------------------------------------


def test_cap_reservation_families_resolves_via_projection_join(tmp_path):
    world_db = _make_world_db(tmp_path)
    conn = sqlite3.connect(world_db)
    conn.row_factory = sqlite3.Row
    try:
        _insert_submit_plan(
            conn,
            aggregate_id="agg-b",
            event_id="evt-b",
            final_intent_id="fi-b",
            family_id="family_b",
        )
        _insert_projection(
            conn,
            aggregate_id="agg-b",
            event_id="evt-b",
            final_intent_id="fi-b",
            pending_reconcile=False,
        )
        _insert_cap_reservation(
            conn,
            usage_id="usage-b",
            event_id="evt-b",
            final_intent_id="fi-b",
        )
        conn.commit()

        family_reasons, unresolved = main_mod._edli_stage_open_cap_reservation_families(conn)
    finally:
        conn.close()

    assert family_reasons == {"family_b": "EDLI_STAGE_LIVE_CAP_RESERVED:1"}
    assert unresolved == 0


def test_cap_reservation_families_falls_back_when_unjoinable(tmp_path):
    world_db = _make_world_db(tmp_path)
    conn = sqlite3.connect(world_db)
    conn.row_factory = sqlite3.Row
    try:
        # RESERVED cap row with no matching projection row at all (join misses).
        _insert_cap_reservation(
            conn,
            usage_id="usage-orphan",
            event_id="evt-unmatched",
            final_intent_id="fi-unmatched",
        )
        conn.commit()

        family_reasons, unresolved = main_mod._edli_stage_open_cap_reservation_families(conn)
    finally:
        conn.close()

    assert family_reasons == {}
    assert unresolved == 1


# ---------------------------------------------------------------------------
# _edli_live_entry_readiness_block (the per-cycle BUY gate)
# ---------------------------------------------------------------------------


@pytest.fixture
def _fresh_readiness_files(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    source_health = tmp_path / "source_health.json"
    source_health.write_text(json.dumps({"generated_at": now}))
    status_summary = tmp_path / "status_summary.json"
    status_summary.write_text(json.dumps({"generated_at": now}))
    return str(source_health), str(status_summary)


def _edli_cfg(source_health_path: str, status_summary_path: str) -> dict:
    return {
        "edli_stage_source_health_json": source_health_path,
        "edli_stage_status_json": status_summary_path,
    }


def test_readiness_block_composite_narrows_to_stuck_family_only(
    tmp_path, monkeypatch, _fresh_readiness_files
):
    """THE acceptance criterion: one stuck order producing BOTH
    EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN and EDLI_STAGE_LIVE_CAP_RESERVED for
    family A blocks A only; an unrelated family B keeps trading (absent from
    the block map entirely) and there is no global block reason.
    """
    world_db = _make_world_db(tmp_path)
    conn = sqlite3.connect(world_db)
    try:
        _insert_submit_plan(
            conn,
            aggregate_id="agg-stuck",
            event_id="evt-stuck",
            final_intent_id="fi-stuck",
            family_id="family_a",
        )
        _insert_projection(
            conn,
            aggregate_id="agg-stuck",
            event_id="evt-stuck",
            final_intent_id="fi-stuck",
            pending_reconcile=True,
        )
        _insert_cap_reservation(
            conn,
            usage_id="usage-stuck",
            event_id="evt-stuck",
            final_intent_id="fi-stuck",
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        main_mod,
        "_settings_section",
        lambda name, default=None: {"world_db": world_db} if name == "state" else default,
    )

    source_health_path, status_summary_path = _fresh_readiness_files
    global_reason, family_reasons = main_mod._edli_live_entry_readiness_block(
        _edli_cfg(source_health_path, status_summary_path)
    )

    assert global_reason is None
    assert family_reasons == {
        "family_a": "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1,EDLI_STAGE_LIVE_CAP_RESERVED:1"
    }
    assert "family_b" not in family_reasons


def test_readiness_block_falls_back_globally_when_family_unresolvable(
    tmp_path, monkeypatch, _fresh_readiness_files
):
    world_db = _make_world_db(tmp_path)
    conn = sqlite3.connect(world_db)
    try:
        _insert_projection(
            conn,
            aggregate_id="agg-orphan",
            event_id="evt-orphan",
            final_intent_id="fi-orphan",
            pending_reconcile=True,
            current_state="DECISION_PROOF_ACCEPTED",
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        main_mod,
        "_settings_section",
        lambda name, default=None: {"world_db": world_db} if name == "state" else default,
    )

    source_health_path, status_summary_path = _fresh_readiness_files
    global_reason, family_reasons = main_mod._edli_live_entry_readiness_block(
        _edli_cfg(source_health_path, status_summary_path)
    )

    assert global_reason == "entry_readiness:EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1"
    assert family_reasons == {}


def test_readiness_block_preserves_genuinely_global_freshness_reasons(
    tmp_path, monkeypatch, _fresh_readiness_files
):
    """Source-health/status-summary staleness are genuinely bankroll-wide and
    must stay global -- untouched by the per-family narrowing."""

    world_db = _make_world_db(tmp_path)  # no stuck rows at all

    monkeypatch.setattr(
        main_mod,
        "_settings_section",
        lambda name, default=None: {"world_db": world_db} if name == "state" else default,
    )

    _, status_summary_path = _fresh_readiness_files
    missing_source_health = str(tmp_path / "does_not_exist.json")
    global_reason, family_reasons = main_mod._edli_live_entry_readiness_block(
        _edli_cfg(missing_source_health, status_summary_path)
    )

    assert global_reason is not None
    assert "EDLI_STAGE_SOURCE_HEALTH_MISSING" in global_reason
    assert family_reasons == {}
