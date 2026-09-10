# Created: 2026-09-09
# Last reused or audited: 2026-09-09
# Authority basis: hourly capital gains improvement loop — venue fill cash facts.
"""Offline contract tests for the immutable venue fill cash fact companion."""
from __future__ import annotations

import sqlite3

import pytest

from src.state.schema.venue_fill_cash_facts_schema import ensure_table


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


def _insert(
    conn: sqlite3.Connection,
    *,
    status: str = "UNKNOWN",
    proof_hash: str = "proof-1",
    block_number: int | None = None,
    finalized_number: int | None = None,
    reason: str = "awaiting finality",
) -> None:
    conn.execute(
        """
        INSERT INTO venue_fill_cash_facts (
            chain_id, tx_hash, wallet, status, reason,
            block_number, block_hash, finalized_number, finalized_hash,
            observed_at, proof_hash, proof_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            137, "0xtx", "0xwallet", status, reason,
            block_number, "0x" + "ab" * 32 if block_number is not None else None,
            finalized_number,
            "0x" + "cd" * 32 if finalized_number is not None else None,
            "2026-09-09T12:00:00Z", proof_hash, '{"source":"test"}',
        ),
    )


def test_ensure_table_is_idempotent_and_does_not_commit():
    class NoCommitConnection(sqlite3.Connection):
        def commit(self):  # type: ignore[override]
            raise AssertionError("schema helper must not commit")

    conn = sqlite3.connect(":memory:", factory=NoCommitConnection)
    try:
        ensure_table(conn)
        ensure_table(conn)
        _insert(conn)
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','index','trigger')"
            )
        }
        assert "venue_fill_cash_facts" in names
        assert "idx_venue_fill_cash_facts_chain_tx_wallet_time" in names
        assert "venue_fill_cash_facts_no_update" in names
        assert "venue_fill_cash_facts_no_delete" in names
    finally:
        conn.close()


def test_unknown_allows_missing_chain_block_fields_but_required_fields_have_no_defaults():
    conn = _conn()
    try:
        ensure_table(conn)
        _insert(conn)
        row = conn.execute(
            "SELECT status, block_number, finalized_number FROM venue_fill_cash_facts"
        ).fetchone()
        assert row == ("UNKNOWN", None, None)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO venue_fill_cash_facts (
                    chain_id, tx_hash, wallet, status, block_number,
                    finalized_number, observed_at, proof_hash, proof_json
                ) VALUES (137, '0xmissing-reason', '0xwallet', 'UNKNOWN',
                          NULL, NULL, '2026-09-09T12:00:00Z', 'p', '{}')
                """
            )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "block_number,finalized_number",
    [(None, 2), (1, None), (-1, 1), (2, 1), (1.5, 2)],
)
def test_proven_requires_valid_block_and_finalized_numbers(block_number, finalized_number):
    conn = _conn()
    try:
        ensure_table(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(
                conn,
                status="PROVEN",
                block_number=block_number,
                finalized_number=finalized_number,
            )
    finally:
        conn.close()


def test_duplicate_proof_is_rejected_but_later_proof_appends_without_mutating_old_row():
    conn = _conn()
    try:
        ensure_table(conn)
        _insert(conn, proof_hash="proof-unknown")
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, proof_hash="proof-unknown", reason="different retry")
        _insert(
            conn,
            status="PROVEN",
            proof_hash="proof-proven",
            block_number=100,
            finalized_number=101,
            reason="finalized proof",
        )
        rows = conn.execute(
            "SELECT status, reason, proof_hash, block_number, finalized_number "
            "FROM venue_fill_cash_facts ORDER BY id"
        ).fetchall()
        assert rows == [
            ("UNKNOWN", "awaiting finality", "proof-unknown", None, None),
            ("PROVEN", "finalized proof", "proof-proven", 100, 101),
        ]
    finally:
        conn.close()


def test_update_and_delete_are_rejected_by_immutable_triggers():
    conn = _conn()
    try:
        ensure_table(conn)
        _insert(conn)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE venue_fill_cash_facts SET reason='changed'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM venue_fill_cash_facts")
        assert conn.execute(
            "SELECT reason FROM venue_fill_cash_facts"
        ).fetchone() == ("awaiting finality",)
    finally:
        conn.close()


def test_caller_rollback_removes_uncommitted_fact_atomically():
    conn = _conn()
    try:
        ensure_table(conn)
        conn.execute("BEGIN")
        _insert(conn, proof_hash="rolled-back")
        conn.rollback()
        assert conn.execute(
            "SELECT COUNT(*) FROM venue_fill_cash_facts"
        ).fetchone()[0] == 0
    finally:
        conn.close()
