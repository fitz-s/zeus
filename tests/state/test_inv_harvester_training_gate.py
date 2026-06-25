# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/archive/2026-Q2/plans_historical/2026-05-27-chain-local-refactor-part2-findings.md (Finding D2-wire)
"""Antibody invariants: harvester learning writer joins position_current.fill_authority.

Finding D2-wire (P2, Part-2 audit 2026-05-27): PR D2 in PR #347 shipped
the typed `is_training_eligible_position` policy boundary but did NOT
wire it into the harvester learning write site (which is snapshot-keyed
and previously had no per-position context). Result: training-gate
policy could be bypassed by future code paths.

PR D2 (this commit, on top of D0b's durable fill_authority projection):

  src/execution/harvester.py
    _snapshot_position_training_eligible(conn, snapshot_id) joins
    ensemble snapshot lineage with position_current.decision_snapshot_id
    and applies is_training_eligible_position to every joined position.
    maybe_write_learning_pair calls it before delegating to
    harvest_settlement; failure emits
    harvester_learning_write_blocked_total{reason='position_fill_authority_not_training_eligible'}.

Failure modes covered (all fail-closed):
  - snapshot_id empty/missing
  - no joined position row
  - position fill_authority = venue_position_observed (PR C3 degraded recovery)
  - position fill_authority = optimistic_submitted (no venue confirm)
  - position fill_authority = legacy_unknown / NULL / unknown string
  - DB query failure
"""
from __future__ import annotations

import sqlite3

import pytest

from src.execution.harvester import _snapshot_position_training_eligible
from src.state.ledger import apply_architecture_kernel_schema


def _setup_db(tmp_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "d2.db"))
    apply_architecture_kernel_schema(conn)
    return conn


def _insert_position_current(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    decision_snapshot_id: str,
    fill_authority: str | None,
    phase: str = "active",
    strategy_key: str = "settlement_capture",
    temperature_metric: str = "high",
    updated_at: str = "2026-05-27T12:00:00Z",
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, decision_snapshot_id, strategy_key,
            temperature_metric, updated_at, fill_authority
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            position_id, phase, decision_snapshot_id, strategy_key,
            temperature_metric, updated_at, fill_authority,
        ),
    )
    conn.commit()


def test_eligible_when_position_fill_authority_is_venue_confirmed_full(tmp_path) -> None:
    conn = _setup_db(tmp_path)
    try:
        _insert_position_current(
            conn,
            position_id="p1",
            decision_snapshot_id="snap-1",
            fill_authority="venue_confirmed_full",
        )
        assert _snapshot_position_training_eligible(conn, "snap-1") is True
    finally:
        conn.close()


def test_eligible_when_position_fill_authority_is_venue_confirmed_partial(tmp_path) -> None:
    conn = _setup_db(tmp_path)
    try:
        _insert_position_current(
            conn,
            position_id="p2",
            decision_snapshot_id="snap-2",
            fill_authority="venue_confirmed_partial",
        )
        assert _snapshot_position_training_eligible(conn, "snap-2") is True
    finally:
        conn.close()


def test_blocked_when_position_fill_authority_is_venue_position_observed(tmp_path) -> None:
    """The PR C3 degraded-recovery slot must block training writes."""
    conn = _setup_db(tmp_path)
    try:
        _insert_position_current(
            conn,
            position_id="p3",
            decision_snapshot_id="snap-3",
            fill_authority="venue_position_observed",
        )
        assert _snapshot_position_training_eligible(conn, "snap-3") is False
    finally:
        conn.close()


def test_blocked_when_position_fill_authority_is_optimistic_submitted(tmp_path) -> None:
    conn = _setup_db(tmp_path)
    try:
        _insert_position_current(
            conn,
            position_id="p4",
            decision_snapshot_id="snap-4",
            fill_authority="optimistic_submitted",
        )
        assert _snapshot_position_training_eligible(conn, "snap-4") is False
    finally:
        conn.close()


def test_blocked_when_position_fill_authority_is_null(tmp_path) -> None:
    """Legacy rows pre-D0b had NULL fill_authority; must fail closed."""
    conn = _setup_db(tmp_path)
    try:
        _insert_position_current(
            conn,
            position_id="p5",
            decision_snapshot_id="snap-5",
            fill_authority=None,
        )
        assert _snapshot_position_training_eligible(conn, "snap-5") is False
    finally:
        conn.close()


def test_blocked_when_position_fill_authority_is_unknown_string(tmp_path) -> None:
    conn = _setup_db(tmp_path)
    try:
        _insert_position_current(
            conn,
            position_id="p6",
            decision_snapshot_id="snap-6",
            fill_authority="some_future_authority",
        )
        assert _snapshot_position_training_eligible(conn, "snap-6") is False
    finally:
        conn.close()


def test_blocked_when_no_position_joined_to_snapshot(tmp_path) -> None:
    """A snapshot context reaching the learning writer should have at least
    one position joined on decision_snapshot_id. Missing row = fail closed."""
    conn = _setup_db(tmp_path)
    try:
        assert _snapshot_position_training_eligible(conn, "snap-orphan") is False
    finally:
        conn.close()


def test_blocked_when_snapshot_id_empty(tmp_path) -> None:
    conn = _setup_db(tmp_path)
    try:
        assert _snapshot_position_training_eligible(conn, "") is False
    finally:
        conn.close()


def test_blocked_when_any_position_on_snapshot_is_ineligible(tmp_path) -> None:
    """Multiple positions per snapshot — ANY degraded-authority row blocks."""
    conn = _setup_db(tmp_path)
    try:
        _insert_position_current(
            conn,
            position_id="p-eligible",
            decision_snapshot_id="snap-mixed",
            fill_authority="venue_confirmed_full",
        )
        _insert_position_current(
            conn,
            position_id="p-degraded",
            decision_snapshot_id="snap-mixed",
            fill_authority="venue_position_observed",
        )
        assert _snapshot_position_training_eligible(conn, "snap-mixed") is False
    finally:
        conn.close()


def test_blocked_when_db_query_raises(tmp_path, monkeypatch) -> None:
    """DB query failure is fail-closed (no calibration row written)."""
    conn = _setup_db(tmp_path)
    conn.close()  # force closed connection — subsequent execute() raises
    assert _snapshot_position_training_eligible(conn, "snap-x") is False
