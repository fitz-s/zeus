# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md T-consolidations

"""Characterization tests for the T-consolidations packet (excision prep, no
behavior change): pin the pre-refactor behavior of the redecision-eligibility
predicate call sites and the certificate-revocation checker call sites so the
consolidation in src/engine/cycle_runtime.py, src/state/portfolio.py,
src/ingest/price_channel_ingest.py, src/execution/executor.py,
src/execution/command_recovery.py, and src/state/decision_integrity_quarantine.py
can be verified byte-identical before/after.

Run before the refactor (captures baseline) AND after (regression gate).
"""

from __future__ import annotations

import sqlite3

import pytest

from src.state.portfolio import QUARANTINE_SENTINEL, Position
from src.state.schema.decision_integrity_quarantine_schema import ensure_table


def _make_position(**overrides) -> Position:
    defaults = dict(
        trade_id="test_001",
        market_id="mkt_001",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-04-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        edge=0.15,
        shares=25.0,
        cost_basis_usd=10.0,
        state="holding",
        token_id="tok_yes_001",
        no_token_id="tok_no_001",
        unit="F",
        env="live",
    )
    defaults.update(overrides)
    return Position(**defaults)


# ---------------------------------------------------------------------------
# CONSOLIDATION 1 — redecision-eligibility predicate
# ---------------------------------------------------------------------------


def test_canonical_predicate_true_for_entry_authority_quarantined():
    from src.engine import cycle_runtime

    pos = _make_position(
        direction="buy_yes",
        state="quarantined",
        chain_state="entry_authority_quarantined",
        shares=12.7,
        chain_shares=12.7,
    )
    assert cycle_runtime._quarantined_position_can_redecision(pos) is True


def test_canonical_predicate_false_for_non_quarantine_chain_state():
    from src.engine import cycle_runtime

    # chain_state="synced" is in CURRENT_MONEY_RISK_CHAIN_STATES (used by
    # portfolio._is_runtime_open_position) but NOT in the canonical predicate's
    # single-value REDECISION_ELIGIBLE_QUARANTINE_CHAIN_STATES set.
    pos = _make_position(
        direction="buy_yes",
        state="quarantined",
        chain_state="synced",
        shares=12.7,
        chain_shares=12.7,
    )
    assert cycle_runtime._quarantined_position_can_redecision(pos) is False


def test_canonical_predicate_false_for_placeholder():
    from src.engine import cycle_runtime

    pos = _make_position(
        direction="buy_yes",
        state="quarantined",
        chain_state="entry_authority_quarantined",
        shares=12.7,
        chain_shares=12.7,
        city=QUARANTINE_SENTINEL,
    )
    assert pos.is_quarantine_placeholder is True
    assert cycle_runtime._quarantined_position_can_redecision(pos) is False


def _make_position_current_conn(rows: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            shares REAL,
            chain_shares REAL,
            updated_at TEXT,
            target_date TEXT,
            chain_state TEXT,
            direction TEXT,
            order_status TEXT,
            exit_retry_count INTEGER,
            next_exit_retry_at TEXT,
            exit_reason TEXT,
            last_monitor_market_price_is_fresh INTEGER
        )
        """
    )
    for row in rows:
        conn.execute(
            """
            INSERT INTO position_current
                (position_id, phase, shares, chain_shares, updated_at, target_date,
                 chain_state, direction, order_status, exit_retry_count,
                 next_exit_retry_at, exit_reason, last_monitor_market_price_is_fresh)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("position_id"),
                row.get("phase"),
                row.get("shares", 1.0),
                row.get("chain_shares", 1.0),
                row.get("updated_at", "2026-07-11T00:00:00+00:00"),
                row.get("target_date", "2026-07-11"),
                row.get("chain_state", ""),
                row.get("direction", "buy_yes"),
                row.get("order_status", ""),
                row.get("exit_retry_count", 0),
                row.get("next_exit_retry_at", ""),
                row.get("exit_reason", ""),
                row.get("last_monitor_market_price_is_fresh", 1),
            ),
        )
    conn.commit()
    return conn


def test_canonical_monitor_position_rows_admits_entry_authority_quarantined():
    from src.engine import cycle_runtime

    conn = _make_position_current_conn(
        [
            {
                "position_id": "pos-1",
                "phase": "quarantined",
                "chain_state": "entry_authority_quarantined",
                "direction": "buy_yes",
            }
        ]
    )
    rows = cycle_runtime._canonical_monitor_position_rows(conn)
    assert rows is not None
    ids = [str(r["position_id"]) for r in rows]
    assert ids == ["pos-1"]


def test_canonical_monitor_position_rows_excludes_non_redecision_chain_state():
    from src.engine import cycle_runtime

    # chain_state="synced" is admitted by portfolio._is_runtime_open_position's
    # broader carve-out but must stay excluded here (matches canonical predicate).
    conn = _make_position_current_conn(
        [
            {
                "position_id": "pos-2",
                "phase": "quarantined",
                "chain_state": "synced",
                "direction": "buy_yes",
            }
        ]
    )
    rows = cycle_runtime._canonical_monitor_position_rows(conn)
    assert rows is not None
    assert rows == []


def test_is_runtime_open_position_broader_than_canonical_predicate():
    """Documents the genuine divergence: portfolio.py's carve-out uses the
    4-value CURRENT_MONEY_RISK_CHAIN_STATES set (synced/chain_present/
    exit_pending_missing/entry_authority_quarantined), NOT the canonical
    predicate's 1-value REDECISION_ELIGIBLE_QUARANTINE_CHAIN_STATES set, and
    has no direction or is_quarantine_placeholder gating. This is intentional
    (a different, broader "still exposed" concept than "can redecision now")
    and consolidation must NOT silently narrow it.
    """
    from src.engine import cycle_runtime
    from src.state.portfolio import _is_runtime_open_position

    pos = _make_position(
        # direction is a Position-validated enum (cannot be blank); the point
        # of this case is that _is_runtime_open_position never inspects it.
        direction="buy_no",
        state="quarantined",
        chain_state="synced",
        shares=12.7,
        chain_shares=12.7,
    )
    assert _is_runtime_open_position(pos) is True
    # The canonical redecision predicate disagrees on this exact position —
    # proving the two are NOT the same predicate.
    assert cycle_runtime._quarantined_position_can_redecision(pos) is False


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
        CREATE TABLE IF NOT EXISTS {schema}.decision_integrity_quarantine (
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


def _make_quarantine_conn(*, attach_trade: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    if attach_trade:
        conn.execute("ATTACH DATABASE ':memory:' AS trade")
        _ensure_table_in_schema(conn, "trade")
    else:
        ensure_table(conn)
    return conn


def _insert_quarantine_row(conn, *, schema: str = "main", certificate_hash: str, reason: str):
    ref = "decision_integrity_quarantine" if schema == "main" else f"{schema}.decision_integrity_quarantine"
    conn.execute(
        f"INSERT INTO {ref} (table_name, row_id, reason_code) VALUES (?, ?, ?)",
        ("decision_certificates", certificate_hash, reason),
    )
    conn.commit()


@pytest.mark.parametrize("attach_trade", [False, True])
def test_executor_and_command_recovery_agree_on_quarantined_hash(attach_trade):
    from src.execution.command_recovery import (
        _decision_certificate_is_quarantined as recovery_check,
    )
    from src.execution.executor import (
        _decision_certificate_is_quarantined as executor_check,
    )

    conn = _make_quarantine_conn(attach_trade=attach_trade)
    schema = "trade" if attach_trade else "main"
    _insert_quarantine_row(
        conn,
        schema=schema,
        certificate_hash="cert-hash-1",
        reason="QUARANTINED_INVALID_LIVE_ACTIONABLE_CERTIFICATE",
    )

    assert executor_check(conn, "cert-hash-1") is True
    assert recovery_check(conn, certificate_hash="cert-hash-1") is True


@pytest.mark.parametrize("attach_trade", [False, True])
def test_executor_and_command_recovery_agree_on_clean_hash(attach_trade):
    from src.execution.command_recovery import (
        _decision_certificate_is_quarantined as recovery_check,
    )
    from src.execution.executor import (
        _decision_certificate_is_quarantined as executor_check,
    )

    conn = _make_quarantine_conn(attach_trade=attach_trade)
    schema = "trade" if attach_trade else "main"
    _insert_quarantine_row(
        conn,
        schema=schema,
        certificate_hash="cert-hash-quarantined",
        reason="QUARANTINED_INVALID_LIVE_ACTIONABLE_CERTIFICATE",
    )

    assert executor_check(conn, "cert-hash-clean") is False
    assert recovery_check(conn, certificate_hash="cert-hash-clean") is False


def test_executor_and_command_recovery_agree_on_empty_hash():
    from src.execution.command_recovery import (
        _decision_certificate_is_quarantined as recovery_check,
    )
    from src.execution.executor import (
        _decision_certificate_is_quarantined as executor_check,
    )

    conn = _make_quarantine_conn(attach_trade=False)
    assert executor_check(conn, "") is False
    assert recovery_check(conn, certificate_hash="") is False


def test_executor_and_command_recovery_agree_when_table_absent():
    from src.execution.command_recovery import (
        _decision_certificate_is_quarantined as recovery_check,
    )
    from src.execution.executor import (
        _decision_certificate_is_quarantined as executor_check,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert executor_check(conn, "cert-hash-1") is False
    assert recovery_check(conn, certificate_hash="cert-hash-1") is False
