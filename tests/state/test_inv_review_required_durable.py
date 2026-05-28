# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Part-3 audit Finding 4 (PR #352) — unresolved size mismatch
#   must be a durable REVIEW_REQUIRED position_events row + persisted quarantined
#   projection, surviving daemon restart.
"""Antibody invariants: size-mismatch-no-baseline persists a durable review.

Finding 4 (Part-3 audit): when chain reconciliation detects a chain/local size
mismatch but has no canonical baseline to correct against, the pre-fix code only
mutated the in-memory Position (state=QUARANTINED, chain_state=
size_mismatch_unresolved) and bumped a stats counter. position_current stayed
'active' on disk, so on the next daemon restart the loader rebuilt the position
as active and the review requirement vanished — live exposure risk.

PR #352 emits a durable REVIEW_REQUIRED event and persists the quarantined
projection via append_many_and_project, so the review survives restart.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.engine.lifecycle_events import build_review_required_canonical_write
from src.contracts.semantic_types import LifecycleState
from src.state.ledger import (
    _ensure_review_required_event_type,
    apply_architecture_kernel_schema,
)


def _quarantined_position(**overrides: Any) -> Any:
    from src.state.portfolio import Position

    defaults: dict[str, Any] = dict(
        trade_id="f4-001",
        market_id="mkt-f4",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-06-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        edge=0.15,
        shares=25.0,
        cost_basis_usd=10.0,
        state=LifecycleState.QUARANTINED.value,
        chain_state="size_mismatch_unresolved",
        chain_shares=40.0,
        token_id="tok_f4",
        no_token_id="tok_no_f4",
        unit="F",
        env="live",
        entered_at="2026-05-27T12:00:00Z",
        chain_verified_at="2026-05-27T13:00:00Z",
        quarantined_at="2026-05-27T13:00:00Z",
        condition_id="cond-f4",
        strategy_key="settlement_capture",
        strategy="settlement_capture",
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_review_required_builder_emits_quarantined_projection() -> None:
    pos = _quarantined_position()
    events, projection = build_review_required_canonical_write(
        pos, reason="size_mismatch_unresolved_no_canonical_baseline", sequence_no=2
    )
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "REVIEW_REQUIRED"
    assert ev["phase_after"] == "quarantined"
    assert projection["phase"] == "quarantined"
    assert projection["chain_state"] == "size_mismatch_unresolved"


def test_fresh_db_check_accepts_review_required(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "f4_fresh.db"))
    try:
        apply_architecture_kernel_schema(conn)
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at,
                phase_before, phase_after, strategy_key, env, source_module, payload_json
            ) VALUES (
                'f4-fresh-1', 'f4-pos-1', 1, 'REVIEW_REQUIRED', '2026-05-27T12:00:00Z',
                NULL, 'quarantined', 'settlement_capture', 'test',
                'tests.test_inv_review_required_durable', '{}'
            )
            """
        )
        row = conn.execute(
            "SELECT event_type FROM position_events WHERE event_id='f4-fresh-1'"
        ).fetchone()
        assert row[0] == "REVIEW_REQUIRED"
    finally:
        conn.close()


def test_review_required_survives_restart_via_position_current(tmp_path: Path) -> None:
    """The core durability invariant: after the durable write, re-reading
    position_current (a restart proxy) shows phase=quarantined, not active."""
    from src.state.db import append_many_and_project

    db_path = tmp_path / "f4_durable.db"
    conn = sqlite3.connect(str(db_path))
    try:
        apply_architecture_kernel_schema(conn)
        pos = _quarantined_position()
        events, projection = build_review_required_canonical_write(
            pos, reason="size_mismatch_unresolved_no_canonical_baseline", sequence_no=1
        )
        append_many_and_project(conn, events, projection)
        conn.commit()
    finally:
        conn.close()

    # Reopen — simulates daemon restart reading durable truth.
    conn2 = sqlite3.connect(str(db_path))
    try:
        phase = conn2.execute(
            "SELECT phase, chain_state FROM position_current WHERE position_id='f4-001'"
        ).fetchone()
        assert phase is not None, "position_current row not persisted — review lost on restart"
        assert phase[0] == "quarantined"
        assert phase[1] == "size_mismatch_unresolved"
        ev = conn2.execute(
            "SELECT event_type FROM position_events WHERE position_id='f4-001' "
            "AND event_type='REVIEW_REQUIRED'"
        ).fetchone()
        assert ev is not None, "durable REVIEW_REQUIRED audit event missing"
    finally:
        conn2.close()


def test_legacy_check_migrated_to_accept_review_required(tmp_path: Path) -> None:
    """Legacy position_events whose CHECK omits REVIEW_REQUIRED is rebuilt."""
    db_path = tmp_path / "f4_legacy.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # Legacy CHECK without REVIEW_REQUIRED.
        conn.executescript(
            """
            CREATE TABLE position_events (
                event_id TEXT PRIMARY KEY,
                position_id TEXT NOT NULL,
                event_version INTEGER NOT NULL DEFAULT 1,
                sequence_no INTEGER NOT NULL,
                event_type TEXT NOT NULL CHECK (event_type IN (
                    'POSITION_OPEN_INTENT','CHAIN_SYNCED','VENUE_POSITION_OBSERVED'
                )),
                occurred_at TEXT NOT NULL,
                phase_before TEXT, phase_after TEXT,
                strategy_key TEXT NOT NULL, decision_id TEXT, snapshot_id TEXT,
                order_id TEXT, command_id TEXT, caused_by TEXT,
                idempotency_key TEXT UNIQUE, venue_status TEXT,
                source_module TEXT NOT NULL, env TEXT NOT NULL, payload_json TEXT NOT NULL,
                UNIQUE(position_id, sequence_no)
            );
            """
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO position_events (event_id, position_id, sequence_no, "
                "event_type, occurred_at, strategy_key, source_module, env, payload_json) "
                "VALUES ('x',  'p', 1, 'REVIEW_REQUIRED', '2026-05-27T00:00:00Z', 'settlement_capture', 'm', 'test', '{}')"
            )
        conn.rollback()
        _ensure_review_required_event_type(conn)
        # Now accepted.
        conn.execute(
            "INSERT INTO position_events (event_id, position_id, sequence_no, "
            "event_type, occurred_at, strategy_key, source_module, env, payload_json) "
            "VALUES ('x', 'p', 1, 'REVIEW_REQUIRED', '2026-05-27T00:00:00Z', 'settlement_capture', 'm', 'test', '{}')"
        )
        row = conn.execute("SELECT event_type FROM position_events WHERE event_id='x'").fetchone()
        assert row[0] == "REVIEW_REQUIRED"
    finally:
        conn.close()
