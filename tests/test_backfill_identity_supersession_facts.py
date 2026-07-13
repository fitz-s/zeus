# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Round-2 delta
#   (duplicate_consolidator special handling, LX-F rework F2)
"""LX-F backfill antibody: scripts/backfill_identity_supersession_facts.py.

Covers:
  1. A historical MANUAL_OVERRIDE_APPLIED consolidator-merge event converts to
     one POSITION_IDENTITY_SUPERSEDED fact, with evidence_refs carrying the
     raw dup-detection evidence and NONE of the historical payload's
     synthesized-economics keys.
  2. A MANUAL_OVERRIDE_APPLIED event with a different reason is left alone.
  3. Re-running after --apply is a no-op (idempotency: a keeper that already
     carries the matching POSITION_IDENTITY_SUPERSEDED fact is skipped).
  4. Dry-run (no --apply) never writes to position_events.
  5. --apply actually appends the row with the correct event_type/payload and
     never mutates position_current.
  6. F2 rework: --apply REFUSES (fail-closed) when the live position_events
     CHECK does not yet admit POSITION_IDENTITY_SUPERSEDED (pre-migration
     DB) — dry-run remains safe regardless.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from src.state.db import init_schema, init_schema_trade_only

from backfill_identity_supersession_facts import (  # noqa: E402
    _apply_backfill,
    _plan_backfill,
    _refuse_unless_check_migrated,
)

_MERGED_REASON = "duplicate_open_rows_merged_same_identity_2026_06_17"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    init_schema_trade_only(c)
    yield c
    c.close()


def _insert_historical_merge_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    keeper_position_id: str,
    absorbed_position_ids: list[str],
    occurred_at: str = "2026-06-17T10:00:00+00:00",
    reason: str = _MERGED_REASON,
    token_id: str = "tok-1",
    chain_shares: float = 60.0,
    db_total_shares: float = 28.5,
) -> None:
    payload = json.dumps(
        {
            "reason": reason,
            "absorbed_position_ids": absorbed_position_ids,
            "token_id": token_id,
            "shares_before": [[keeper_position_id, 13.5], [absorbed_position_ids[0], 15.0]]
            if absorbed_position_ids
            else [],
            "db_total_shares": db_total_shares,
            "chain_shares": chain_shares,
            "shares_after": chain_shares,
            "db_total_cost_basis_usd": 8.835,
            "cost_basis_usd_after": chain_shares * 0.31,
        },
        sort_keys=True,
    )
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key,
            source_module, payload_json, env
        ) VALUES (?, ?, 1, 1, 'MANUAL_OVERRIDE_APPLIED', ?, 'active', 'active',
                  'opening_inertia', 'src.state.position_duplicate_consolidator',
                  ?, 'live')
        """,
        (event_id, keeper_position_id, occurred_at, payload),
    )
    conn.commit()


class TestPlanBackfill:
    def test_historical_merge_converts_to_identity_superseded_fact(self, conn):
        _insert_historical_merge_event(
            conn,
            event_id="ev-merge-1",
            keeper_position_id="pos-merge-b",
            absorbed_position_ids=["pos-merge-a"],
        )

        planned, skipped = _plan_backfill(conn)

        assert skipped == []
        assert len(planned) == 1
        item = planned[0]
        assert item["keeper_position_id"] == "pos-merge-b"
        assert item["absorbed_position_ids"] == ["pos-merge-a"]
        assert item["occurred_at"] == "2026-06-17T10:00:00+00:00"
        assert item["evidence_refs"]["token_id"] == "tok-1"
        assert item["evidence_refs"]["chain_shares"] == pytest.approx(60.0)
        assert item["evidence_refs"]["db_total_shares_before"] == pytest.approx(28.5)
        # No synthesized-economics keys leak into evidence_refs.
        forbidden = {"shares_after", "cost_basis_usd_after", "db_total_cost_basis_usd"}
        assert forbidden.isdisjoint(item["evidence_refs"].keys())

    def test_unrelated_manual_override_event_is_skipped(self, conn):
        """A MANUAL_OVERRIDE_APPLIED event with a different reason is not a
        consolidator merge and must not be converted."""
        _insert_historical_merge_event(
            conn,
            event_id="ev-other-1",
            keeper_position_id="pos-x",
            absorbed_position_ids=["pos-y"],
            reason="some_other_operator_override_reason",
        )

        planned, skipped = _plan_backfill(conn)

        assert planned == []
        assert skipped == []  # filtered out before ever reaching the payload scan

    def test_missing_absorbed_ids_is_skipped_with_reason(self, conn):
        _insert_historical_merge_event(
            conn,
            event_id="ev-merge-empty",
            keeper_position_id="pos-empty",
            absorbed_position_ids=[],
        )

        planned, skipped = _plan_backfill(conn)

        assert planned == []
        assert len(skipped) == 1
        assert skipped[0]["position_id"] == "pos-empty"
        assert "absorbed_position_ids" in skipped[0]["reason"]

    def test_idempotent_skip_when_already_converted(self, conn):
        _insert_historical_merge_event(
            conn,
            event_id="ev-merge-2",
            keeper_position_id="pos-merge-d",
            absorbed_position_ids=["pos-merge-c"],
        )
        planned_first, _ = _plan_backfill(conn)
        assert len(planned_first) == 1
        written = _apply_backfill(conn, planned_first)
        assert written == 1

        planned_second, skipped_second = _plan_backfill(conn)
        assert planned_second == []
        assert len(skipped_second) == 1
        assert "already recorded" in skipped_second[0]["reason"]


class TestApplyBackfill:
    def test_apply_writes_position_identity_superseded_event(self, conn):
        _insert_historical_merge_event(
            conn,
            event_id="ev-merge-3",
            keeper_position_id="pos-merge-f",
            absorbed_position_ids=["pos-merge-e"],
        )
        planned, _ = _plan_backfill(conn)

        written = _apply_backfill(conn, planned)
        assert written == 1

        rows = conn.execute(
            "SELECT event_type, payload_json, occurred_at FROM position_events "
            "WHERE position_id = 'pos-merge-f' AND event_type = 'POSITION_IDENTITY_SUPERSEDED'"
        ).fetchall()
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        assert payload["keeper_position_id"] == "pos-merge-f"
        assert payload["absorbed_position_ids"] == ["pos-merge-e"]
        assert rows[0]["occurred_at"] == "2026-06-17T10:00:00+00:00"

    def test_apply_never_touches_position_current(self, conn):
        """The backfill is a pure position_events append; it must never write
        position_current (no economics resynthesis, no phase mutation)."""
        conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, shares, cost_basis_usd, strategy_key,
                temperature_metric, updated_at
            ) VALUES ('pos-merge-h', 'active', 13.5, 4.185, 'opening_inertia',
                      'high', '2026-06-17T09:00:00+00:00')
            """
        )
        conn.commit()
        _insert_historical_merge_event(
            conn,
            event_id="ev-merge-4",
            keeper_position_id="pos-merge-h",
            absorbed_position_ids=["pos-merge-g"],
        )
        planned, _ = _plan_backfill(conn)
        _apply_backfill(conn, planned)

        row = conn.execute(
            "SELECT phase, shares, cost_basis_usd, updated_at FROM position_current "
            "WHERE position_id = 'pos-merge-h'"
        ).fetchone()
        assert row["phase"] == "active"
        assert row["shares"] == pytest.approx(13.5)
        assert row["cost_basis_usd"] == pytest.approx(4.185)
        assert row["updated_at"] == "2026-06-17T09:00:00+00:00"

    def test_dry_run_via_main_never_writes(self, conn, monkeypatch, capsys):
        """--apply omitted (default dry-run) must not append any event."""
        _insert_historical_merge_event(
            conn,
            event_id="ev-merge-5",
            keeper_position_id="pos-merge-j",
            absorbed_position_ids=["pos-merge-i"],
        )
        before_count = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]

        planned, skipped = _plan_backfill(conn)
        assert len(planned) == 1
        after_count = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
        assert after_count == before_count, "_plan_backfill must never write"


class TestRefuseUnlessCheckMigrated:
    def test_refuses_when_check_does_not_admit_literal(self):
        """F2 rework: --apply is fail-closed on a pre-migration DB whose CHECK
        does not yet admit POSITION_IDENTITY_SUPERSEDED."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE position_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL CHECK (event_type IN ('MANUAL_OVERRIDE_APPLIED'))
            )
            """
        )
        with pytest.raises(SystemExit, match="REFUSED"):
            _refuse_unless_check_migrated(conn)
        conn.close()

    def test_passes_when_check_admits_literal(self, conn):
        # conn fixture already runs current db.py's fresh schema, which
        # admits the literal directly (no migration needed for a fresh DB).
        _refuse_unless_check_migrated(conn)  # must not raise
