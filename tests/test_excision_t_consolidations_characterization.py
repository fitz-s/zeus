# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md T-consolidations

"""Characterization tests for the T-consolidations packet (excision prep, no
behavior change): pin the pre-refactor behavior of the redecision-eligibility
predicate call sites and the certificate-revocation checker call sites so the
consolidation in src/engine/cycle_runtime.py, src/state/portfolio.py,
src/ingest/price_channel_ingest.py, src/execution/executor.py,
src/execution/command_recovery.py, and src/state/fact_revocation.py (DIQ
packet, 2026-07-12, supersedes src/state/decision_integrity_quarantine.py)
can be verified byte-identical before/after.

Run before the refactor (captures baseline) AND after (regression gate).
CONSOLIDATION 2 section updated 2026-07-12 for the DIQ reshape (module path
and private alias renamed; behavior/semantics unchanged).
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.schema.fact_revocations_schema import ensure_table


# ---------------------------------------------------------------------------
# CONSOLIDATION 1 — redecision-eligibility predicate [RETIRED]
# ---------------------------------------------------------------------------
#
# T5 BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md,
# post-T5-migration cleanup): this section used to characterize
# src.engine.cycle_runtime._quarantined_position_can_redecision and
# _canonical_monitor_position_rows' phase='quarantined' handling — both gated
# on a raw state/phase literal that could only ever appear on a pre-T5-
# migration legacy row. The T5 schema migration
# (scripts/migrations/2026_07_quarantine_phase_retirement.py) has run against
# the live DBs: the position_current/position_events CHECK constraints no
# longer admit 'quarantined', LifecycleState/PositionPhase have no such
# member (Position construction now raises instead of remapping), and no row
# can ever trigger the bridge again. _quarantined_position_can_redecision and
# its supporting helpers were deleted as provably dead code; these tests
# constructed Position(state="quarantined"), which is no longer constructible
# at all, so they tested ONLY the retired bridge and are removed rather than
# rewritten.


def test_edli_priority_tokens_includes_voided_phase_and_broader_chain_states():
    """Documents the genuine divergence: price_channel_ingest's exposure clause
    admits phase IN ('quarantined', 'voided') against CURRENT_MONEY_RISK_CHAIN_STATES
    (via src.contracts.position_truth), which is broader than and structurally
    different from the canonical predicate (single phase 'quarantined', single
    chain_state 'entry_authority_quarantined', plus direction/placeholder gates
    the SQL clause does not apply). Not consolidated — reported divergence.
    """
    from src.ingest.price_channel_ingest import _edli_held_position_priority_token_ids

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            token_id TEXT,
            no_token_id TEXT,
            chain_shares REAL,
            chain_state TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_current
            (position_id, phase, token_id, no_token_id, chain_shares, chain_state)
        VALUES ('pos-voided', 'voided', 'tok-voided-yes', 'tok-voided-no', 5.0, 'synced')
        """
    )
    conn.commit()
    tokens = _edli_held_position_priority_token_ids(conn)
    assert tokens == {"tok-voided-yes", "tok-voided-no"}


# ---------------------------------------------------------------------------
# CONSOLIDATION 2 — certificate-revocation checker
# ---------------------------------------------------------------------------


def _ensure_table_in_schema(conn: sqlite3.Connection, schema: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.fact_revocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            row_id TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            forecast_snapshot_id TEXT,
            recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
            meta_json TEXT NOT NULL DEFAULT '{{}}',
            UNIQUE(table_name, row_id, reason_code)
        )
        """
    )


def _make_revocation_conn(*, attach_trade: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    if attach_trade:
        conn.execute("ATTACH DATABASE ':memory:' AS trade")
        _ensure_table_in_schema(conn, "trade")
    else:
        ensure_table(conn)
    return conn


def _insert_revocation_row(conn, *, schema: str = "main", certificate_hash: str, reason: str):
    ref = "fact_revocations" if schema == "main" else f"{schema}.fact_revocations"
    conn.execute(
        f"INSERT INTO {ref} (table_name, row_id, reason_code) VALUES (?, ?, ?)",
        ("decision_certificates", certificate_hash, reason),
    )
    conn.commit()


@pytest.mark.parametrize("attach_trade", [False, True])
def test_executor_and_command_recovery_agree_on_revoked_hash(attach_trade):
    from src.execution.command_recovery import (
        _certificate_is_revoked as recovery_check,
    )
    from src.execution.executor import (
        _certificate_is_revoked as executor_check,
    )

    conn = _make_revocation_conn(attach_trade=attach_trade)
    schema = "trade" if attach_trade else "main"
    _insert_revocation_row(
        conn,
        schema=schema,
        certificate_hash="cert-hash-1",
        reason="REVOKED_INVALID_LIVE_ACTIONABLE_CERTIFICATE",
    )

    assert executor_check(conn, "cert-hash-1") is True
    assert recovery_check(conn, certificate_hash="cert-hash-1") is True


@pytest.mark.parametrize("attach_trade", [False, True])
def test_executor_and_command_recovery_agree_on_clean_hash(attach_trade):
    from src.execution.command_recovery import (
        _certificate_is_revoked as recovery_check,
    )
    from src.execution.executor import (
        _certificate_is_revoked as executor_check,
    )

    conn = _make_revocation_conn(attach_trade=attach_trade)
    schema = "trade" if attach_trade else "main"
    _insert_revocation_row(
        conn,
        schema=schema,
        certificate_hash="cert-hash-revoked",
        reason="REVOKED_INVALID_LIVE_ACTIONABLE_CERTIFICATE",
    )

    assert executor_check(conn, "cert-hash-clean") is False
    assert recovery_check(conn, certificate_hash="cert-hash-clean") is False


def test_executor_and_command_recovery_agree_on_empty_hash():
    from src.execution.command_recovery import (
        _certificate_is_revoked as recovery_check,
    )
    from src.execution.executor import (
        _certificate_is_revoked as executor_check,
    )

    conn = _make_revocation_conn(attach_trade=False)
    assert executor_check(conn, "") is False
    assert recovery_check(conn, certificate_hash="") is False


def test_executor_and_command_recovery_agree_when_table_absent():
    from src.execution.command_recovery import (
        _certificate_is_revoked as recovery_check,
    )
    from src.execution.executor import (
        _certificate_is_revoked as executor_check,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert executor_check(conn, "cert-hash-1") is False
    assert recovery_check(conn, certificate_hash="cert-hash-1") is False
