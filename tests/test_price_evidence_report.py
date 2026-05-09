# Created: 2026-05-09
# Last reused/audited: 2026-05-09
# Authority basis: S4 price/orderbook evidence report packet; TASK.md safe implementation queue.
"""Relationship tests for derived price/orderbook evidence visibility."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
from src.state.schema.v2_schema import apply_v2_schema
from src.state.snapshot_repo import init_snapshot_schema, insert_snapshot

UTC = timezone.utc


def _conn(*, include_snapshot_table: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_v2_schema(conn)
    if include_snapshot_table:
        init_snapshot_schema(conn)
    return conn


def _insert_price_history(
    conn: sqlite3.Connection,
    *,
    token_id: str = "yes-token",
    recorded_at: str = "2026-05-09T09:00:00+00:00",
    linkage: str = "price_only",
    source: str = "GAMMA_SCANNER",
    best_bid: float | None = None,
    best_ask: float | None = None,
    raw_orderbook_hash: str | None = None,
    snapshot_id: str | None = None,
    condition_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO market_price_history (
            market_slug, token_id, price, recorded_at, hours_since_open,
            hours_to_resolution, market_price_linkage, source, best_bid,
            best_ask, raw_orderbook_hash, snapshot_id, condition_id
        ) VALUES (
            'weather-market', :token_id, 0.42, :recorded_at, 2.0,
            18.0, :linkage, :source, :best_bid,
            :best_ask, :raw_orderbook_hash, :snapshot_id, :condition_id
        )
        """,
        {
            "token_id": token_id,
            "recorded_at": recorded_at,
            "linkage": linkage,
            "source": source,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "raw_orderbook_hash": raw_orderbook_hash,
            "snapshot_id": snapshot_id,
            "condition_id": condition_id,
        },
    )


def _insert_snapshot(conn: sqlite3.Connection, *, snapshot_id: str = "snap-1") -> None:
    captured_at = datetime(2026, 5, 9, 9, 0, tzinfo=UTC)
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-1",
            event_id="event-1",
            event_slug="weather-market",
            condition_id="condition-1",
            question_id="question-1",
            yes_token_id="yes-token",
            no_token_id="no-token",
            selected_outcome_token_id="yes-token",
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            fee_details={"source": "test"},
            token_map_raw={"YES": "yes-token", "NO": "no-token"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.41"),
            orderbook_top_ask=Decimal("0.43"),
            orderbook_depth_jsonb='{"asks":[{"price":"0.43","size":"100"}],"bids":[{"price":"0.41","size":"100"}]}',
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=captured_at,
            freshness_deadline=captured_at + timedelta(seconds=30),
        ),
    )


def test_price_only_rows_do_not_count_as_executable_snapshot_backed() -> None:
    from src.observability.price_evidence_report import build_price_evidence_report

    conn = _conn()
    _insert_price_history(conn, linkage="price_only", source="GAMMA_SCANNER")

    report = build_price_evidence_report(conn)

    assert report["authority"] == "derived_operator_visibility"
    assert report["status"] == "observed"
    assert report["modes"]["price_only"]["row_count"] == 1
    assert report["modes"]["executable_snapshot_backed"]["row_count"] == 0
    assert "no_executable_snapshot_backed_price_rows" in report["blockers"]


def test_full_linkage_without_snapshot_row_is_not_snapshot_backed() -> None:
    from src.observability.price_evidence_report import build_price_evidence_report

    conn = _conn()
    _insert_price_history(
        conn,
        linkage="full",
        source="CLOB_ORDERBOOK",
        best_bid=0.41,
        best_ask=0.43,
        raw_orderbook_hash="c" * 64,
        snapshot_id="missing-snapshot",
        condition_id="condition-1",
    )

    report = build_price_evidence_report(conn)

    assert report["modes"]["full_linkage_rows"]["row_count"] == 1
    assert report["modes"]["executable_snapshot_backed"]["row_count"] == 0
    assert report["counts"]["full_linkage_without_snapshot_rows"] == 1
    assert "full_linkage_without_executable_snapshot" in report["blockers"]


def test_full_linkage_with_snapshot_row_is_executable_snapshot_backed() -> None:
    from src.observability.price_evidence_report import build_price_evidence_report

    conn = _conn()
    _insert_snapshot(conn, snapshot_id="snap-1")
    _insert_price_history(
        conn,
        linkage="full",
        source="CLOB_ORDERBOOK",
        best_bid=0.41,
        best_ask=0.43,
        raw_orderbook_hash="c" * 64,
        snapshot_id="snap-1",
        condition_id="condition-1",
    )

    report = build_price_evidence_report(conn)

    assert report["modes"]["price_only"]["row_count"] == 0
    assert report["modes"]["full_linkage_rows"]["row_count"] == 1
    assert report["modes"]["executable_snapshot_backed"]["row_count"] == 1
    assert report["counts"]["full_linkage_without_snapshot_rows"] == 0
    assert report["blockers"] == []


def test_mixed_price_only_and_snapshot_backed_rows_keep_modes_separate() -> None:
    from src.observability.price_evidence_report import build_price_evidence_report

    conn = _conn()
    _insert_snapshot(conn, snapshot_id="snap-1")
    _insert_price_history(
        conn,
        token_id="price-only-token",
        recorded_at="2026-05-09T09:00:00+00:00",
        linkage="price_only",
        source="GAMMA_SCANNER",
    )
    _insert_price_history(
        conn,
        token_id="snapshot-backed-token",
        recorded_at="2026-05-09T09:01:00+00:00",
        linkage="full",
        source="CLOB_ORDERBOOK",
        best_bid=0.41,
        best_ask=0.43,
        raw_orderbook_hash="c" * 64,
        snapshot_id="snap-1",
        condition_id="condition-1",
    )

    report = build_price_evidence_report(conn)

    assert report["modes"]["price_only"]["row_count"] == 1
    assert report["modes"]["executable_snapshot_backed"]["row_count"] == 1
    assert "no_executable_snapshot_backed_price_rows" not in report["blockers"]


def test_invalid_full_linkage_rows_do_not_count_as_executable_backed() -> None:
    from src.observability.price_evidence_report import build_price_evidence_report

    conn = _conn()
    _insert_snapshot(conn, snapshot_id="snap-1")
    _insert_price_history(
        conn,
        linkage="full",
        source="CLOB_ORDERBOOK",
        best_bid=0.44,
        best_ask=0.42,
        raw_orderbook_hash="c" * 64,
        snapshot_id="snap-1",
        condition_id="condition-1",
    )

    report = build_price_evidence_report(conn)

    assert report["modes"]["full_linkage_rows"]["row_count"] == 0
    assert report["modes"]["executable_snapshot_backed"]["row_count"] == 0
    assert report["counts"]["invalid_full_linkage_rows"] == 1
    assert "invalid_full_linkage_rows" in report["blockers"]


def test_missing_snapshot_orderbook_columns_return_partial() -> None:
    from src.observability.price_evidence_report import build_price_evidence_report

    conn = _conn(include_snapshot_table=False)
    conn.execute("CREATE TABLE executable_market_snapshots (snapshot_id TEXT PRIMARY KEY)")

    report = build_price_evidence_report(conn)

    assert report["status"] == "partial"
    assert report["counts"]["executable_orderbook_snapshot_rows"] is None
    assert "snapshot_orderbook_columns_unavailable" in report["blockers"]
    assert report["source_errors"] == [
        {
            "source": "executable_market_snapshots",
            "error": "missing_orderbook_columns",
            "columns": ["orderbook_top_ask", "orderbook_top_bid", "raw_orderbook_hash"],
        }
    ]


def test_empty_price_evidence_tables_return_certified_empty() -> None:
    from src.observability.price_evidence_report import build_price_evidence_report

    conn = _conn()

    report = build_price_evidence_report(conn)

    assert report["status"] == "certified_empty"
    assert report["counts"]["market_price_history_rows"] == 0
    assert report["counts"]["executable_market_snapshots_rows"] == 0
    assert report["source_errors"] == []


def test_missing_price_evidence_tables_return_query_error() -> None:
    from src.observability.price_evidence_report import build_price_evidence_report

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    report = build_price_evidence_report(conn)

    assert report["status"] == "query_error"
    assert report["authority"] == "derived_operator_visibility"
    assert {error["source"] for error in report["source_errors"]} == {
        "market_price_history",
        "executable_market_snapshots",
    }
