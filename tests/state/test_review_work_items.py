# Created: 2026-07-11
# Last reused or audited: 2026-07-11
# Authority basis: docs/rebuild/quarantine_excision_2026-07-11.md "Consult adjudication"
#   (adopted target shape) — excision foundation packet.
# Lifecycle: created=2026-07-11; last_reviewed=2026-07-11; last_reused=never
# Purpose: Unit tests for the owner-local ReviewWorkItem protocol — schema,
#   idempotent open, CAS resolution, supersede, due-work scheduling, family-block
#   read helper, and concurrent-open race safety.
# Reuse: Run when review_work_item.py / review_work_items.py /
#   review_work_items_schema.py change, or before T2/T4/T5 wire consumers to them.

"""Tests for src.contracts.review_work_item + src.state.review_work_items."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from src.contracts.review_work_item import (
    FamilyKey,
    ReviewReasonCode,
    ReviewWorkItem,
    WorkItemStatus,
    evidence_hash_for,
)
from src.state.review_work_items import (
    blocked_family_keys,
    due_work,
    open_items_by_family,
    open_unbounded_count,
    open_work_item,
    resolve_work_item,
    supersede_on_new_revision,
)
from src.state.schema.review_work_items_schema import ensure_table
from src.strategy.family_exclusive_dedup import WeatherFamilyKey


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    return conn


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


class TestReviewWorkItemContract:
    def test_requires_bounded_xor_unbounded(self) -> None:
        with pytest.raises(ValueError):
            ReviewWorkItem(
                work_id="w1",
                owner_domain="trade",
                owner_table="position_current",
                subject_id="pos1",
                reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
                authority_revision=0,
                evidence_refs=(),
                evidence_hash="",
                first_seen_at="2026-07-11T00:00:00+00:00",
                last_seen_at="2026-07-11T00:00:00+00:00",
                exposure_bound_usd=None,
                unbounded=False,
            )

    def test_rejects_both_bounded_and_unbounded(self) -> None:
        with pytest.raises(ValueError):
            ReviewWorkItem(
                work_id="w1",
                owner_domain="trade",
                owner_table="position_current",
                subject_id="pos1",
                reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
                authority_revision=0,
                evidence_refs=(),
                evidence_hash="",
                first_seen_at="2026-07-11T00:00:00+00:00",
                last_seen_at="2026-07-11T00:00:00+00:00",
                exposure_bound_usd=10.0,
                unbounded=True,
            )

    def test_evidence_hash_for_is_deterministic_and_order_independent(self) -> None:
        assert evidence_hash_for(("a", "b")) == evidence_hash_for(("b", "a"))
        assert evidence_hash_for(("a",)) != evidence_hash_for(("b",))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_table_created_on_fresh_fixture_db(self) -> None:
        conn = _make_conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='review_work_items'"
        ).fetchone()
        assert row is not None

    def test_partial_unique_index_blocks_duplicate_open_at_sql_level(self) -> None:
        conn = _make_conn()
        insert_sql = """
            INSERT INTO review_work_items (
                work_id, owner_domain, owner_table, subject_id, reason_code,
                authority_revision, first_seen_at, last_seen_at,
                unbounded, next_attempt_at, status, created_at, updated_at
            ) VALUES (?, 'trade', 'position_current', 'pos1', 'CHAIN_ONLY_UNKNOWN_ASSET',
                0, 't0', 't0', 1, 't0', 'OPEN', 't0', 't0')
        """
        conn.execute(insert_sql, ("w1",))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert_sql, ("w2",))

    def test_bounded_unbounded_check_constraint(self) -> None:
        conn = _make_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO review_work_items (
                    work_id, owner_domain, owner_table, subject_id, reason_code,
                    authority_revision, first_seen_at, last_seen_at,
                    exposure_bound_usd, unbounded, next_attempt_at, status, created_at, updated_at
                ) VALUES ('w1', 'trade', 'position_current', 'pos1', 'CHAIN_ONLY_UNKNOWN_ASSET',
                    0, 't0', 't0', NULL, 0, 't0', 'OPEN', 't0', 't0')
                """
            )

    def test_status_check_constraint(self) -> None:
        conn = _make_conn()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO review_work_items (
                    work_id, owner_domain, owner_table, subject_id, reason_code,
                    authority_revision, first_seen_at, last_seen_at,
                    unbounded, next_attempt_at, status, created_at, updated_at
                ) VALUES ('w1', 'trade', 'position_current', 'pos1', 'CHAIN_ONLY_UNKNOWN_ASSET',
                    0, 't0', 't0', 1, 't0', 'BOGUS', 't0', 't0')
                """
            )


# ---------------------------------------------------------------------------
# open_work_item idempotency
# ---------------------------------------------------------------------------


class TestOpenWorkItem:
    def test_double_open_is_one_row(self) -> None:
        conn = _make_conn()
        first = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            authority_revision=0,
            unbounded=True,
        )
        second = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            authority_revision=0,
            unbounded=True,
        )
        assert first.work_id == second.work_id
        count = conn.execute(
            "SELECT COUNT(*) FROM review_work_items WHERE owner_table='position_current' AND subject_id='pos1'"
        ).fetchone()[0]
        assert count == 1

    def test_different_reason_code_opens_separate_item(self) -> None:
        conn = _make_conn()
        first = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            authority_revision=0,
            unbounded=True,
        )
        second = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.LOCAL_WRITE_FAILURE,
            authority_revision=0,
            exposure_bound_usd=0.0,
        )
        assert first.work_id != second.work_id

    def test_concurrent_open_from_two_connections_partial_index_race(self, tmp_path) -> None:
        db_path = tmp_path / "review_work_items_race.db"
        setup_conn = sqlite3.connect(str(db_path))
        ensure_table(setup_conn)
        setup_conn.commit()
        setup_conn.close()

        results: list[str] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def _worker() -> None:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            conn.execute("PRAGMA busy_timeout = 5000")
            try:
                barrier.wait(timeout=5.0)
                item = open_work_item(
                    conn,
                    owner_domain="trade",
                    owner_table="position_current",
                    subject_id="pos-race-1",
                    reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
                    authority_revision=1,
                    unbounded=True,
                )
                conn.commit()
                results.append(item.work_id)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                conn.close()

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert not errors, errors
        assert len(results) == 2
        assert results[0] == results[1]

        check_conn = sqlite3.connect(str(db_path))
        count = check_conn.execute(
            "SELECT COUNT(*) FROM review_work_items "
            "WHERE owner_table='position_current' AND subject_id='pos-race-1' AND status='OPEN'"
        ).fetchone()[0]
        assert count == 1
        check_conn.close()


# ---------------------------------------------------------------------------
# CAS resolution
# ---------------------------------------------------------------------------


class TestResolveWorkItem:
    def test_correct_revision_resolves(self) -> None:
        conn = _make_conn()
        item = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CERTIFICATE_REVOKED,
            authority_revision=3,
            exposure_bound_usd=0.0,
        )
        ok = resolve_work_item(
            conn,
            work_id=item.work_id,
            authority_revision=3,
            resolver_identity="operator:fitz",
            resolution_evidence="manually verified certificate re-issued",
        )
        assert ok is True
        row = conn.execute(
            "SELECT status FROM review_work_items WHERE work_id=?", (item.work_id,)
        ).fetchone()
        assert row[0] == "RESOLVED"

    def test_stale_revision_refused(self) -> None:
        conn = _make_conn()
        item = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CERTIFICATE_REVOKED,
            authority_revision=3,
            exposure_bound_usd=0.0,
        )
        ok = resolve_work_item(
            conn,
            work_id=item.work_id,
            authority_revision=2,  # stale
            resolver_identity="operator:fitz",
            resolution_evidence="wrong revision",
        )
        assert ok is False
        row = conn.execute(
            "SELECT status FROM review_work_items WHERE work_id=?", (item.work_id,)
        ).fetchone()
        assert row[0] == "OPEN"

    def test_double_resolve_second_call_refused(self) -> None:
        conn = _make_conn()
        item = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CERTIFICATE_REVOKED,
            authority_revision=1,
            exposure_bound_usd=0.0,
        )
        first = resolve_work_item(
            conn,
            work_id=item.work_id,
            authority_revision=1,
            resolver_identity="operator:fitz",
            resolution_evidence="first resolve",
        )
        second = resolve_work_item(
            conn,
            work_id=item.work_id,
            authority_revision=1,
            resolver_identity="operator:fitz",
            resolution_evidence="second resolve",
        )
        assert first is True
        assert second is False


# ---------------------------------------------------------------------------
# Supersede flow
# ---------------------------------------------------------------------------


class TestSupersede:
    def test_supersede_marks_stale_revision_and_allows_new_open(self) -> None:
        conn = _make_conn()
        old = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            authority_revision=1,
            unbounded=True,
        )
        affected = supersede_on_new_revision(
            conn,
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            new_authority_revision=2,
        )
        assert affected == 1
        old_row = conn.execute(
            "SELECT status FROM review_work_items WHERE work_id=?", (old.work_id,)
        ).fetchone()
        assert old_row[0] == "SUPERSEDED"

        new_item = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            authority_revision=2,
            unbounded=True,
        )
        assert new_item.work_id != old.work_id
        assert new_item.status == WorkItemStatus.OPEN

    def test_supersede_does_not_touch_newer_or_equal_revision(self) -> None:
        conn = _make_conn()
        item = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            authority_revision=5,
            unbounded=True,
        )
        affected = supersede_on_new_revision(
            conn,
            owner_table="position_current",
            subject_id="pos1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            new_authority_revision=5,
        )
        assert affected == 0
        row = conn.execute(
            "SELECT status FROM review_work_items WHERE work_id=?", (item.work_id,)
        ).fetchone()
        assert row[0] == "OPEN"


# ---------------------------------------------------------------------------
# due_work ordering + limit
# ---------------------------------------------------------------------------


class TestDueWork:
    def test_ordering_by_priority_then_next_attempt_at(self) -> None:
        conn = _make_conn()
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="low-priority",
            reason_code=ReviewReasonCode.LOCAL_WRITE_FAILURE,
            exposure_bound_usd=0.0,
            priority=200,
            next_attempt_at="2026-07-11T00:00:00+00:00",
        )
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="high-priority",
            reason_code=ReviewReasonCode.LOCAL_WRITE_FAILURE,
            exposure_bound_usd=0.0,
            priority=10,
            next_attempt_at="2026-07-11T00:00:00+00:00",
        )
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="not-due-yet",
            reason_code=ReviewReasonCode.LOCAL_WRITE_FAILURE,
            exposure_bound_usd=0.0,
            priority=1,
            next_attempt_at="2099-01-01T00:00:00+00:00",
        )
        items = due_work(conn, now="2026-07-11T00:00:00+00:00", limit=10)
        subject_ids = [item.subject_id for item in items]
        assert subject_ids == ["high-priority", "low-priority"]

    def test_limit_is_honored(self) -> None:
        conn = _make_conn()
        for i in range(5):
            open_work_item(
                conn,
                owner_domain="trade",
                owner_table="position_current",
                subject_id=f"pos{i}",
                reason_code=ReviewReasonCode.LOCAL_WRITE_FAILURE,
                exposure_bound_usd=0.0,
                next_attempt_at="2026-07-11T00:00:00+00:00",
            )
        items = due_work(conn, now="2026-07-11T00:00:00+00:00", limit=2)
        assert len(items) == 2


# ---------------------------------------------------------------------------
# Unbounded detection
# ---------------------------------------------------------------------------


class TestUnboundedCount:
    def test_counts_only_open_unbounded(self) -> None:
        conn = _make_conn()
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="unbounded1",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            unbounded=True,
        )
        bounded_item = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="bounded1",
            reason_code=ReviewReasonCode.LOCAL_WRITE_FAILURE,
            exposure_bound_usd=5.0,
        )
        resolved_unbounded = open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="unbounded2",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            authority_revision=1,
            unbounded=True,
        )
        resolve_work_item(
            conn,
            work_id=resolved_unbounded.work_id,
            authority_revision=1,
            resolver_identity="operator:fitz",
            resolution_evidence="resolved",
        )
        assert open_unbounded_count(conn) == 1
        assert bounded_item.exposure_bound_usd == 5.0


# ---------------------------------------------------------------------------
# open_items_by_family
# ---------------------------------------------------------------------------


class TestOpenItemsByFamily:
    def test_returns_only_matching_family(self) -> None:
        conn = _make_conn()
        fam = FamilyKey(city="Chicago", target_date="2026-07-15", temperature_metric="high")
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos-in-family",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            unbounded=True,
            family_key=fam,
        )
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos-outside-family",
            reason_code=ReviewReasonCode.CHAIN_ONLY_UNKNOWN_ASSET,
            unbounded=True,
            family_key=FamilyKey(city="Dallas", target_date="2026-07-15", temperature_metric="high"),
        )
        matches = open_items_by_family(
            conn, WeatherFamilyKey("Chicago", "2026-07-15", "high")
        )
        assert [m.subject_id for m in matches] == ["pos-in-family"]


# ---------------------------------------------------------------------------
# blocked_family_keys
# ---------------------------------------------------------------------------


class _FakeChainOnlyFact:
    def __init__(self, condition_id: str, token_id: str = "", blocks_entry: bool = True) -> None:
        self.condition_id = condition_id
        self.token_id = token_id
        self.blocks_entry = blocks_entry


class _FakePortfolio:
    def __init__(self, chain_only_facts) -> None:
        self.chain_only_facts = chain_only_facts


class TestBlockedFamilyKeys:
    def test_includes_family_scoped_open_work_items_only_for_blocking_reasons(self) -> None:
        conn = _make_conn()
        fam = FamilyKey(city="Miami", target_date="2026-08-01", temperature_metric="low")
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos-conflict",
            reason_code=ReviewReasonCode.CONFIRMED_FILL_CHAIN_ABSENCE_CONFLICT,
            exposure_bound_usd=25.0,
            family_key=fam,
        )
        # LOCAL_WRITE_FAILURE is not a family-blocking reason code — must NOT appear.
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id="pos-local-write-failure",
            reason_code=ReviewReasonCode.LOCAL_WRITE_FAILURE,
            exposure_bound_usd=0.0,
            family_key=FamilyKey(city="Denver", target_date="2026-08-01", temperature_metric="low"),
        )
        keys = blocked_family_keys(conn, portfolio=None)
        assert keys == {WeatherFamilyKey("Miami", "2026-08-01", "low", "")}

    def test_includes_blocking_chain_only_facts_from_portfolio(self) -> None:
        conn = _make_conn()
        conn.execute(
            """
            CREATE TABLE market_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                condition_id TEXT,
                token_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO market_events (market_slug, city, target_date, temperature_metric, condition_id, token_id) "
            "VALUES ('slug1', 'Boston', '2026-09-01', 'high', 'cond-1', 'tok-1')"
        )
        portfolio = _FakePortfolio([_FakeChainOnlyFact(condition_id="cond-1")])
        keys = blocked_family_keys(conn, portfolio=portfolio)
        assert WeatherFamilyKey("Boston", "2026-09-01", "high", "") in keys

    def test_excludes_non_blocking_chain_only_facts(self) -> None:
        conn = _make_conn()
        conn.execute(
            """
            CREATE TABLE market_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                condition_id TEXT,
                token_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO market_events (market_slug, city, target_date, temperature_metric, condition_id, token_id) "
            "VALUES ('slug1', 'Boston', '2026-09-01', 'high', 'cond-1', 'tok-1')"
        )
        portfolio = _FakePortfolio([_FakeChainOnlyFact(condition_id="cond-1", blocks_entry=False)])
        keys = blocked_family_keys(conn, portfolio=portfolio)
        assert keys == set()
