# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-refactor-part2-findings.md (Finding D0, PR D0)
"""Antibody invariants: balance-only rescue emits VENUE_POSITION_OBSERVED canonical event.

Finding D0 (P1, Part-2 audit 2026-05-27): chain reconciliation rescue
branch previously emitted `CHAIN_SYNCED` for BOTH trade-verified and
balance-only recovery, with no event-grammar distinction. PR C3 added a
runtime `fill_authority` discriminator on the Position dataclass, but the
canonical event log carried no signal — so downstream consumers reading
`position_events` couldn't tell verified fills from degraded recovery.

PR D0 splits the canonical write into two builders:
  - `build_reconciliation_rescue_canonical_write` (event_type=CHAIN_SYNCED)
    — emitted when `_pending_entry_has_linked_fill_fact(pos)` is True.
  - `build_venue_position_observed_canonical_write` (event_type=VENUE_POSITION_OBSERVED)
    — emitted when no linked trade fact exists; payload carries
    `fill_authority=venue_position_observed`,
    `recovery_authority=balance_only`,
    `causality_status=UNVERIFIED`, `training_eligible=false`.

Schema migration (`_ensure_venue_position_observed_event_type` in
src/state/ledger.py) extends `position_events.event_type` CHECK constraint
on legacy DBs via rebuild-pattern. Fresh DBs get the new constraint from
the kernel SQL directly.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.engine.lifecycle_events import (
    build_reconciliation_rescue_canonical_write,
    build_venue_position_observed_canonical_write,
)
from src.state.ledger import (
    _ensure_venue_position_observed_event_type,
    apply_architecture_kernel_schema,
)


def _make_rescued_position(**overrides: Any) -> Any:
    """Construct a minimum-viable Position for canonical builders."""
    from src.state.portfolio import Position

    defaults: dict[str, Any] = dict(
        trade_id="d0-001",
        market_id="mkt-d0",
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
        state="entered",
        chain_state="synced",
        token_id="tok_yes_d0",
        no_token_id="tok_no_d0",
        unit="F",
        env="live",
        entered_at="2026-05-27T12:00:00Z",
        condition_id="cond-d0",
        strategy_key="settlement_capture",
        strategy="settlement_capture",
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_venue_position_observed_builder_emits_distinct_event_type() -> None:
    pos = _make_rescued_position(fill_authority="venue_position_observed")
    events, projection = build_venue_position_observed_canonical_write(
        pos, venue_observed_at="2026-05-27T12:00:00Z", sequence_no=7, source_module="src.state.chain_reconciliation"
    )
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "VENUE_POSITION_OBSERVED"
    assert ev["caused_by"] == "balance_only_recovery"
    assert ev["event_id"].endswith(":venue_position_observed:7")
    assert ev["idempotency_key"].endswith(":venue_position_observed:7")


def test_venue_position_observed_payload_marks_training_ineligible() -> None:
    pos = _make_rescued_position(fill_authority="venue_position_observed")
    events, _ = build_venue_position_observed_canonical_write(
        pos, venue_observed_at="2026-05-27T12:00:00Z", sequence_no=1, source_module="t"
    )
    payload = json.loads(events[0]["payload_json"])
    assert payload["fill_authority"] == "venue_position_observed"
    assert payload["recovery_authority"] == "balance_only"
    assert payload["causality_status"] == "UNVERIFIED"
    assert payload["training_eligible"] is False


def test_verified_rescue_still_emits_chain_synced() -> None:
    """The trade-verified rescue branch is unchanged: same builder, same event_type."""
    pos = _make_rescued_position(fill_authority="venue_confirmed_full")
    events, _ = build_reconciliation_rescue_canonical_write(
        pos, chain_synced_at="2026-05-27T12:00:00Z", sequence_no=2, source_module="t"
    )
    assert events[0]["event_type"] == "CHAIN_SYNCED"


def test_two_builders_differ_by_event_type_and_caused_by() -> None:
    """Static contract: the two builders MUST produce semantically distinct events
    even from the same Position. Protects against silent unification."""
    pos = _make_rescued_position()
    chain_synced_event = build_reconciliation_rescue_canonical_write(
        pos, chain_synced_at="2026-05-27T12:00:00Z", sequence_no=3, source_module="t"
    )[0][0]
    venue_observed_event = build_venue_position_observed_canonical_write(
        pos, venue_observed_at="2026-05-27T12:00:00Z", sequence_no=3, source_module="t"
    )[0][0]
    assert chain_synced_event["event_type"] != venue_observed_event["event_type"]
    assert chain_synced_event["caused_by"] != venue_observed_event["caused_by"]
    assert chain_synced_event["event_id"] != venue_observed_event["event_id"]


def test_fresh_db_check_constraint_accepts_venue_position_observed(tmp_path: Path) -> None:
    """Fresh DB built from current kernel SQL must accept VENUE_POSITION_OBSERVED rows."""
    db_path = tmp_path / "d0_fresh.db"
    conn = sqlite3.connect(str(db_path))
    try:
        apply_architecture_kernel_schema(conn)
        # Insert a minimal row carrying the new event_type — bypass the
        # builder so the test isolates the CHECK constraint surface.
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at,
                phase_before, phase_after, strategy_key, env, source_module,
                payload_json
            ) VALUES (
                'd0-fresh-001', 'd0-fresh-pos-1', 1, 'VENUE_POSITION_OBSERVED',
                '2026-05-27T12:00:00Z', 'pending_entry', 'active',
                'settlement_capture', 'test', 'tests.test_inv_venue_position_observed_event',
                '{}'
            )
            """
        )
        row = conn.execute(
            "SELECT event_type FROM position_events WHERE event_id = 'd0-fresh-001'"
        ).fetchone()
        assert row is not None
        assert row[0] == "VENUE_POSITION_OBSERVED"
    finally:
        conn.close()


def test_ensure_venue_position_observed_migrates_legacy_check(tmp_path: Path) -> None:
    """Legacy DB whose position_events.event_type CHECK omits VENUE_POSITION_OBSERVED
    must be rebuilt by the migration helper so the new event type is accepted."""
    db_path = tmp_path / "d0_legacy.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # Build a pre-D0 schema manually (omit VENUE_POSITION_OBSERVED from CHECK).
        conn.executescript(
            """
            CREATE TABLE position_events (
                event_id TEXT PRIMARY KEY,
                position_id TEXT NOT NULL,
                sequence_no INTEGER NOT NULL CHECK (sequence_no >= 1),
                event_type TEXT NOT NULL CHECK (event_type IN (
                    'POSITION_OPEN_INTENT',
                    'ENTRY_ORDER_POSTED',
                    'ENTRY_ORDER_FILLED',
                    'ENTRY_ORDER_VOIDED',
                    'ENTRY_ORDER_REJECTED',
                    'DAY0_WINDOW_ENTERED',
                    'CHAIN_SYNCED',
                    'CHAIN_SIZE_CORRECTED',
                    'CHAIN_QUARANTINED',
                    'MONITOR_REFRESHED',
                    'EXIT_INTENT',
                    'EXIT_ORDER_POSTED',
                    'EXIT_ORDER_FILLED',
                    'EXIT_ORDER_VOIDED',
                    'EXIT_ORDER_REJECTED',
                    'SETTLED',
                    'ADMIN_VOIDED',
                    'MANUAL_OVERRIDE_APPLIED'
                )),
                occurred_at TEXT NOT NULL,
                phase_before TEXT,
                phase_after TEXT,
                strategy_key TEXT NOT NULL,
                decision_id TEXT,
                snapshot_id TEXT,
                order_id TEXT,
                command_id TEXT,
                caused_by TEXT,
                idempotency_key TEXT UNIQUE,
                venue_status TEXT,
                source_module TEXT NOT NULL,
                env TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(position_id, sequence_no)
            );
            """
        )
        # Seed a legacy row so migration must preserve data.
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at,
                strategy_key, source_module, env, payload_json
            ) VALUES (
                'legacy-1', 'legacy-pos-1', 1, 'CHAIN_SYNCED',
                '2026-05-27T11:00:00Z', 'settlement_capture',
                'tests.legacy', 'test', '{}'
            )
            """
        )
        conn.commit()

        # Pre-migration: VENUE_POSITION_OBSERVED must fail the CHECK.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO position_events (
                    event_id, position_id, sequence_no, event_type, occurred_at,
                    strategy_key, source_module, env, payload_json
                ) VALUES (
                    'pre-mig-fail', 'legacy-pos-2', 1, 'VENUE_POSITION_OBSERVED',
                    '2026-05-27T11:30:00Z', 'settlement_capture',
                    'tests.legacy', 'test', '{}'
                )
                """
            )
        conn.rollback()

        # Run migration.
        _ensure_venue_position_observed_event_type(conn)

        # Post-migration: legacy row preserved.
        row = conn.execute(
            "SELECT event_type FROM position_events WHERE event_id = 'legacy-1'"
        ).fetchone()
        assert row is not None and row[0] == "CHAIN_SYNCED"

        # Post-migration: VENUE_POSITION_OBSERVED must now be accepted.
        conn.execute(
            """
            INSERT INTO position_events (
                event_id, position_id, sequence_no, event_type, occurred_at,
                strategy_key, source_module, env, payload_json
            ) VALUES (
                'post-mig-ok', 'legacy-pos-3', 1, 'VENUE_POSITION_OBSERVED',
                '2026-05-27T12:00:00Z', 'settlement_capture',
                'tests.legacy', 'test', '{}'
            )
            """
        )
        row = conn.execute(
            "SELECT event_type FROM position_events WHERE event_id = 'post-mig-ok'"
        ).fetchone()
        assert row is not None and row[0] == "VENUE_POSITION_OBSERVED"

        # Idempotency: second call does nothing.
        _ensure_venue_position_observed_event_type(conn)
        row = conn.execute(
            "SELECT event_type FROM position_events WHERE event_id = 'legacy-1'"
        ).fetchone()
        assert row is not None and row[0] == "CHAIN_SYNCED"
    finally:
        conn.close()
