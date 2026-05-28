# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §F3 (F3 — Legacy NULL fill_authority backfill)
"""F3 antibody invariants: deterministic fill_authority backfill + harvester gate distinction.

Finding F3 (P1/P2, docs/findings_2026_05_28.md §F3): migration added fill_authority as
a nullable additive column; legacy rows stay NULL until backfill runs. The harvester
gates training on NULL (fail-closed) but previously treated NULL and legacy_unknown
identically — masking whether backfill had run at all.

PR2-F3 ships:
  src/state/ledger.py
    backfill_fill_authority(conn) — deterministic 4-way classification of NULL rows
    (venue_confirmed_full, venue_confirmed_partial, venue_position_observed, legacy_unknown)
  src/state/db.py
    query_unclassified_authority_rows(conn) — ops tool for migration verification
    report_authority_distribution(conn) — grouped count by authority value
  src/execution/harvester.py
    _snapshot_position_training_eligible — distinct emit per blocked reason:
      NULL  → "position_fill_authority_unmigrated"
      "legacy_unknown" → "position_fill_authority_legacy_unknown"
      other ineligible → "position_fill_authority_not_training_eligible"

Acceptance tests (6):
  1. Linked ENTRY/BUY trade fact with sum >= shares → venue_confirmed_full
  2. Linked trade facts sum < shares → venue_confirmed_partial
  3. VENUE_POSITION_OBSERVED event, no trade facts → venue_position_observed
  4. No evidence → legacy_unknown
  5. Harvester gate emits distinct reasons for NULL vs legacy_unknown
  6. backfill_fill_authority is idempotent (re-run produces all-zero counts)
"""
from __future__ import annotations

import sqlite3

import pytest

from src.state.db import init_schema
from src.state.ledger import apply_architecture_kernel_schema, backfill_fill_authority
from src.execution.harvester import _snapshot_position_training_eligible


_DUMMY_TS = "2026-05-28T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _insert_position(conn, *, position_id: str, shares: float, fill_authority=None) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, strategy_key, temperature_metric,
            updated_at, shares, fill_authority
        ) VALUES (?, 'active', 'settlement_capture', 'high', ?, ?, ?)
        """,
        (position_id, _DUMMY_TS, shares, fill_authority),
    )
    conn.commit()


def _insert_venue_command(conn, *, command_id: str, position_id: str,
                           intent_kind: str = "ENTRY", side: str = "BUY") -> None:
    conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side,
            size, price, state, created_at, updated_at
        ) VALUES (?, 'snap-cmd', 'env-cmd', ?, 'dec-cmd',
                  ?, ?, 'm-cmd', 'tok-cmd', ?,
                  10.0, 0.50, 'PENDING', ?, ?)
        """,
        (command_id, position_id, f"idem-{command_id}", intent_kind, side,
         _DUMMY_TS, _DUMMY_TS),
    )
    conn.commit()


def _insert_trade_fact(conn, *, command_id: str, filled_size: float,
                        state: str = "CONFIRMED") -> None:
    conn.execute(
        """
        INSERT INTO venue_trade_facts (
            trade_id, venue_order_id, command_id, source, state,
            filled_size, fill_price, observed_at, local_sequence, raw_payload_hash
        ) VALUES (?, ?, ?, 'REST', ?, ?, '0.50', ?, 1, 'hash-tf')
        """,
        (f"tid-{command_id}", f"vord-{command_id}", command_id, state,
         str(filled_size), _DUMMY_TS),
    )
    conn.commit()


def _insert_venue_position_observed_event(conn, *, position_id: str) -> None:
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            decision_id, snapshot_id, order_id, command_id, caused_by,
            idempotency_key, venue_status, source_module, env, payload_json
        ) VALUES (
            'ev-vpo-' || ?, ?, 1, 1, 'VENUE_POSITION_OBSERVED',
            ?, 'pending_entry', 'active', 'settlement_capture',
            'dec-1', 'snap-1', 'ord-1', 'cmd-1', 'test',
            'idem-vpo-' || ?, '', 'tests.f3', 'test', '{}'
        )
        """,
        (position_id, position_id, _DUMMY_TS, position_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Test 1: venue_confirmed_full — trade fact sum >= position shares
# ---------------------------------------------------------------------------


def test_backfill_assigns_venue_confirmed_full_for_linked_trade_fact() -> None:
    """Legacy NULL row with linked ENTRY/BUY trade fact summing to position shares
    must be classified as venue_confirmed_full."""
    conn = _setup_db()
    try:
        _insert_position(conn, position_id="p-full", shares=20.0)
        _insert_venue_command(conn, command_id="cmd-full", position_id="p-full")
        _insert_trade_fact(conn, command_id="cmd-full", filled_size=20.0)

        counts = backfill_fill_authority(conn)

        assert counts["venue_confirmed_full"] == 1
        assert counts["venue_confirmed_partial"] == 0
        assert counts["venue_position_observed"] == 0
        assert counts["legacy_unknown"] == 0

        row = conn.execute(
            "SELECT fill_authority FROM position_current WHERE position_id = 'p-full'"
        ).fetchone()
        assert row["fill_authority"] == "venue_confirmed_full"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 2: venue_confirmed_partial — trade fact sum < position shares
# ---------------------------------------------------------------------------


def test_backfill_assigns_partial_for_undersized_fill_fact() -> None:
    """Linked trade facts summing to less than position shares → venue_confirmed_partial."""
    conn = _setup_db()
    try:
        _insert_position(conn, position_id="p-partial", shares=20.0)
        _insert_venue_command(conn, command_id="cmd-partial", position_id="p-partial")
        _insert_trade_fact(conn, command_id="cmd-partial", filled_size=10.0)

        counts = backfill_fill_authority(conn)

        assert counts["venue_confirmed_partial"] == 1
        assert counts["venue_confirmed_full"] == 0

        row = conn.execute(
            "SELECT fill_authority FROM position_current WHERE position_id = 'p-partial'"
        ).fetchone()
        assert row["fill_authority"] == "venue_confirmed_partial"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 3: venue_position_observed — event present, no trade facts
# ---------------------------------------------------------------------------


def test_backfill_assigns_venue_position_observed_for_event_only_row() -> None:
    """VENUE_POSITION_OBSERVED event with no linked trade facts → venue_position_observed."""
    conn = _setup_db()
    try:
        _insert_position(conn, position_id="p-vpo", shares=15.0)
        _insert_venue_position_observed_event(conn, position_id="p-vpo")

        counts = backfill_fill_authority(conn)

        assert counts["venue_position_observed"] == 1
        assert counts["venue_confirmed_full"] == 0
        assert counts["venue_confirmed_partial"] == 0
        assert counts["legacy_unknown"] == 0

        row = conn.execute(
            "SELECT fill_authority FROM position_current WHERE position_id = 'p-vpo'"
        ).fetchone()
        assert row["fill_authority"] == "venue_position_observed"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 4: legacy_unknown — no evidence of any kind
# ---------------------------------------------------------------------------


def test_backfill_assigns_legacy_unknown_when_no_evidence() -> None:
    """No linked trade facts, no VENUE_POSITION_OBSERVED event → legacy_unknown."""
    conn = _setup_db()
    try:
        _insert_position(conn, position_id="p-unk", shares=10.0)

        counts = backfill_fill_authority(conn)

        assert counts["legacy_unknown"] == 1
        assert counts["venue_confirmed_full"] == 0
        assert counts["venue_confirmed_partial"] == 0
        assert counts["venue_position_observed"] == 0

        row = conn.execute(
            "SELECT fill_authority FROM position_current WHERE position_id = 'p-unk'"
        ).fetchone()
        assert row["fill_authority"] == "legacy_unknown"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test 5: harvester gate emits distinct reasons for NULL vs legacy_unknown
# ---------------------------------------------------------------------------


def test_harvester_gate_distinguishes_unmigrated_from_legacy_unknown(
    tmp_path,
) -> None:
    """_snapshot_position_training_eligible returns False for both NULL and
    legacy_unknown, but the emit reason differs.

    Verify via monkeypatching _emit_learning_write_blocked to capture calls.
    """
    import src.execution.harvester as harvester_mod

    emitted: list[str] = []
    original_emit = harvester_mod._emit_learning_write_blocked

    def _capture_emit(reason: str) -> None:
        emitted.append(reason)

    harvester_mod._emit_learning_write_blocked = _capture_emit
    try:
        # --- NULL case (unmigrated) ---
        conn_null = sqlite3.connect(str(tmp_path / "null.db"))
        init_schema(conn_null)
        conn_null.execute(
            """
            INSERT INTO position_current (
                position_id, phase, strategy_key, temperature_metric,
                updated_at, decision_snapshot_id, fill_authority
            ) VALUES ('p-null', 'active', 'settlement_capture', 'high', ?, 'snap-null', NULL)
            """,
            (_DUMMY_TS,),
        )
        conn_null.commit()
        result_null = _snapshot_position_training_eligible(conn_null, "snap-null")
        conn_null.close()

        assert result_null is False
        assert "position_fill_authority_unmigrated" in emitted, (
            f"NULL should emit 'position_fill_authority_unmigrated'; got {emitted}"
        )

        emitted.clear()

        # --- legacy_unknown case (backfill ran, no evidence) ---
        conn_lu = sqlite3.connect(str(tmp_path / "lu.db"))
        init_schema(conn_lu)
        conn_lu.execute(
            """
            INSERT INTO position_current (
                position_id, phase, strategy_key, temperature_metric,
                updated_at, decision_snapshot_id, fill_authority
            ) VALUES ('p-lu', 'active', 'settlement_capture', 'high', ?, 'snap-lu',
                      'legacy_unknown')
            """,
            (_DUMMY_TS,),
        )
        conn_lu.commit()
        result_lu = _snapshot_position_training_eligible(conn_lu, "snap-lu")
        conn_lu.close()

        assert result_lu is False
        assert "position_fill_authority_legacy_unknown" in emitted, (
            f"legacy_unknown should emit 'position_fill_authority_legacy_unknown'; got {emitted}"
        )
        assert "position_fill_authority_unmigrated" not in emitted, (
            f"legacy_unknown must NOT emit unmigrated reason; got {emitted}"
        )
    finally:
        harvester_mod._emit_learning_write_blocked = original_emit


# ---------------------------------------------------------------------------
# Test 6: idempotency — re-run produces all-zero counts
# ---------------------------------------------------------------------------


def test_backfill_idempotent() -> None:
    """Running backfill_fill_authority twice: second run returns all zeros
    because fill_authority IS NULL filter finds nothing on re-run."""
    conn = _setup_db()
    try:
        _insert_position(conn, position_id="p-idem", shares=10.0)

        first = backfill_fill_authority(conn)
        assert first["legacy_unknown"] == 1  # classified on first run

        second = backfill_fill_authority(conn)
        assert second == {
            "venue_confirmed_full": 0,
            "venue_confirmed_partial": 0,
            "venue_position_observed": 0,
            "legacy_unknown": 0,
        }, f"second run must be all-zero; got {second}"

        # Value must remain as classified by first run (not reset to NULL).
        row = conn.execute(
            "SELECT fill_authority FROM position_current WHERE position_id = 'p-idem'"
        ).fetchone()
        assert row["fill_authority"] == "legacy_unknown"
    finally:
        conn.close()
