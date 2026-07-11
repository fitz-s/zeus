# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   BLOCKER-1 — excision foundation packet.
# Lifecycle: created=2026-07-11; last_reviewed=2026-07-11; last_reused=never
# Purpose: Unit tests for EntryExposureObligation contract + writer/reader —
#   long-only conservative-bound math, unbounded XOR, upsert-open idempotency,
#   resolve, and the two accounting helpers T2 will consume.
# Reuse: Run when entry_exposure_obligation.py (contract or writer) changes.

"""Tests for src.contracts.entry_exposure_obligation + src.state.entry_exposure_obligation."""

from __future__ import annotations

import sqlite3

import pytest

from src.contracts.entry_exposure_obligation import EntryExposureObligation
from src.contracts.review_work_item import FamilyKey
from src.state.entry_exposure_obligation import (
    has_unbounded_obligation,
    open_entry_exposure_obligation,
    resolve_entry_exposure_obligation,
    total_open_obligation_usd,
)
from src.state.schema.entry_exposure_obligations_schema import ensure_table


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    return conn


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


class TestContract:
    def test_requires_shares_and_cost_together(self) -> None:
        with pytest.raises(ValueError):
            EntryExposureObligation(
                command_id="cmd1",
                owner_domain="trade",
                shares=10.0,
                cost_basis_usd=None,
            )

    def test_rejects_bounded_and_unbounded_together(self) -> None:
        with pytest.raises(ValueError):
            EntryExposureObligation(
                command_id="cmd1",
                owner_domain="trade",
                shares=10.0,
                cost_basis_usd=4.0,
                unbounded=True,
            )

    def test_requires_one_of_bounded_or_unbounded(self) -> None:
        with pytest.raises(ValueError):
            EntryExposureObligation(command_id="cmd1", owner_domain="trade")

    def test_exposure_bound_usd_is_shares_times_one_dollar(self) -> None:
        obligation = EntryExposureObligation(
            command_id="cmd1", owner_domain="trade", shares=42.5, cost_basis_usd=30.0
        )
        assert obligation.exposure_bound_usd == 42.5
        assert obligation.net_cost_usd == 30.0

    def test_unbounded_has_no_exposure_bound(self) -> None:
        obligation = EntryExposureObligation(command_id="cmd1", owner_domain="trade", unbounded=True)
        assert obligation.exposure_bound_usd is None
        assert obligation.net_cost_usd is None

    def test_rejects_negative_shares_or_cost(self) -> None:
        with pytest.raises(ValueError):
            EntryExposureObligation(
                command_id="cmd1", owner_domain="trade", shares=-1.0, cost_basis_usd=1.0
            )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_table_created_on_fresh_fixture_db(self) -> None:
        conn = _make_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entry_exposure_obligations'"
        ).fetchone()
        assert row is not None

    def test_check_constraint_rejects_bounded_and_unbounded_together(self) -> None:
        conn = _make_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO entry_exposure_obligations (
                    command_id, owner_domain, shares, cost_basis_usd, unbounded,
                    status, created_at, updated_at
                ) VALUES ('cmd1', 'trade', 10.0, 5.0, 1, 'OPEN', 't0', 't0')
                """
            )


# ---------------------------------------------------------------------------
# Writer: upsert-open idempotency + resolve
# ---------------------------------------------------------------------------


class TestWriter:
    def test_open_then_reopen_updates_same_row(self) -> None:
        conn = _make_conn()
        first = open_entry_exposure_obligation(
            conn,
            command_id="cmd1",
            owner_domain="trade",
            token_id="tok1",
            shares=10.0,
            cost_basis_usd=6.0,
        )
        second = open_entry_exposure_obligation(
            conn,
            command_id="cmd1",
            owner_domain="trade",
            token_id="tok1",
            shares=12.0,
            cost_basis_usd=7.0,
        )
        assert first.command_id == second.command_id
        assert second.shares == 12.0
        count = conn.execute("SELECT COUNT(*) FROM entry_exposure_obligations").fetchone()[0]
        assert count == 1

    def test_resolve_marks_row_resolved(self) -> None:
        conn = _make_conn()
        open_entry_exposure_obligation(
            conn, command_id="cmd1", owner_domain="trade", shares=1.0, cost_basis_usd=0.5
        )
        ok = resolve_entry_exposure_obligation(conn, command_id="cmd1")
        assert ok is True
        row = conn.execute(
            "SELECT status FROM entry_exposure_obligations WHERE command_id='cmd1'"
        ).fetchone()
        assert row[0] == "RESOLVED"

    def test_resolve_missing_command_returns_false(self) -> None:
        conn = _make_conn()
        assert resolve_entry_exposure_obligation(conn, command_id="never-opened") is False

    def test_family_key_round_trips(self) -> None:
        conn = _make_conn()
        fam = FamilyKey(city="Austin", target_date="2026-07-20", temperature_metric="high")
        obligation = open_entry_exposure_obligation(
            conn,
            command_id="cmd1",
            owner_domain="trade",
            shares=3.0,
            cost_basis_usd=1.5,
            family_key=fam,
        )
        assert obligation.family_key == fam


# ---------------------------------------------------------------------------
# Accounting helpers
# ---------------------------------------------------------------------------


class TestAccounting:
    def test_total_open_obligation_usd_sums_bounded_open_rows_only(self) -> None:
        conn = _make_conn()
        open_entry_exposure_obligation(
            conn, command_id="cmd1", owner_domain="trade", shares=10.0, cost_basis_usd=4.0
        )
        open_entry_exposure_obligation(
            conn, command_id="cmd2", owner_domain="trade", shares=5.0, cost_basis_usd=2.0
        )
        resolved = open_entry_exposure_obligation(
            conn, command_id="cmd3", owner_domain="trade", shares=100.0, cost_basis_usd=50.0
        )
        resolve_entry_exposure_obligation(conn, command_id=resolved.command_id)
        open_entry_exposure_obligation(conn, command_id="cmd4", owner_domain="trade", unbounded=True)

        assert total_open_obligation_usd(conn) == pytest.approx(15.0)

    def test_has_unbounded_obligation_true_only_when_open_unbounded_exists(self) -> None:
        conn = _make_conn()
        assert has_unbounded_obligation(conn) is False
        open_entry_exposure_obligation(
            conn, command_id="cmd1", owner_domain="trade", shares=1.0, cost_basis_usd=1.0
        )
        assert has_unbounded_obligation(conn) is False
        open_entry_exposure_obligation(conn, command_id="cmd2", owner_domain="trade", unbounded=True)
        assert has_unbounded_obligation(conn) is True
        resolve_entry_exposure_obligation(conn, command_id="cmd2")
        assert has_unbounded_obligation(conn) is False
