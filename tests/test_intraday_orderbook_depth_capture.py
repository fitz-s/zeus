# Created: 2026-06-07
# Last reused or audited: 2026-06-07
# Authority basis: docs/the_path/P1_BRIEF.md §2e/§3b + §5 (T7 anti-lookahead price, depth capture) — ThePath P1 ITEM 3
# Purpose: ThePath P1 ITEM 3 antibodies — additive, fail-soft intraday order-book
#   depth capture from already-captured executable_market_snapshots.
#     - ADDITIVE: only new 'full' rows; existing mid-only rows untouched.
#     - FAIL-SOFT: missing EMS / crossed book / missing facts => skip, never raise.
#     - ANTI-LOOKAHEAD: only snapshots with captured_at <= as_of are tapped.
#     - NO NEW POLL: reads only persisted EMS rows (no network).
"""ThePath P1 ITEM 3 antibodies for capture_intraday_orderbook_depth_from_snapshots."""

from __future__ import annotations

import sqlite3

from src.state.db import capture_intraday_orderbook_depth_from_snapshots


# ---------------------------------------------------------------------------
# Fixture: minimal EMS + market_price_history with the depth columns.
# ---------------------------------------------------------------------------
def _conn_with_ems_and_mph() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            event_slug TEXT,
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT,
            orderbook_top_bid TEXT,
            orderbook_top_ask TEXT,
            raw_orderbook_hash TEXT,
            captured_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE market_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            token_id TEXT NOT NULL,
            price REAL NOT NULL CHECK (price >= 0.0 AND price <= 1.0),
            recorded_at TEXT NOT NULL,
            hours_since_open REAL,
            hours_to_resolution REAL,
            market_price_linkage TEXT NOT NULL DEFAULT 'price_only'
                CHECK (market_price_linkage IN ('price_only','full')),
            source TEXT NOT NULL DEFAULT 'GAMMA_SCANNER',
            best_bid REAL CHECK (best_bid IS NULL OR (best_bid >= 0.0 AND best_bid <= 1.0)),
            best_ask REAL CHECK (best_ask IS NULL OR (best_ask >= 0.0 AND best_ask <= 1.0)),
            raw_orderbook_hash TEXT,
            snapshot_id TEXT,
            condition_id TEXT,
            UNIQUE(token_id, recorded_at)
        )
        """
    )
    conn.commit()
    return conn


def _insert_ems(conn, *, snapshot_id, condition_id, captured_at,
                bid="0.40", ask="0.50", token="tok-1", slug="boston-high",
                ohash="hash-abc"):
    conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, event_slug, condition_id, selected_outcome_token_id,
            orderbook_top_bid, orderbook_top_ask, raw_orderbook_hash, captured_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (snapshot_id, slug, condition_id, token, bid, ask, ohash, captured_at),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Happy path: depth row written with best_bid/best_ask/raw_orderbook_hash.
# ---------------------------------------------------------------------------
def test_writes_full_linkage_depth_from_existing_snapshot() -> None:
    conn = _conn_with_ems_and_mph()
    _insert_ems(conn, snapshot_id="s1", condition_id="cond-1",
                captured_at="2026-06-06T13:00:00+00:00")

    result = capture_intraday_orderbook_depth_from_snapshots(
        conn,
        condition_ids=["cond-1"],
        recorded_at="2026-06-06T13:05:00+00:00",
    )
    assert result["status"] in ("ok", "ok_with_conflicts")
    assert result["rows_inserted"] == 1

    row = conn.execute(
        "SELECT market_price_linkage, best_bid, best_ask, raw_orderbook_hash, "
        "snapshot_id, condition_id, source FROM market_price_history"
    ).fetchone()
    assert row[0] == "full"
    assert row[1] == 0.40
    assert row[2] == 0.50
    assert row[3] == "hash-abc"
    assert row[4] == "s1"
    assert row[5] == "cond-1"
    assert row[6] == "CLOB_ORDERBOOK_EMS_TAP"
    conn.close()


# ---------------------------------------------------------------------------
# T7 — ANTI-LOOKAHEAD: a future snapshot (captured_at > as_of) is NOT tapped.
# ---------------------------------------------------------------------------
def test_anti_lookahead_future_snapshot_not_selected() -> None:
    conn = _conn_with_ems_and_mph()
    # Only a FUTURE snapshot exists relative to the scan time.
    _insert_ems(conn, snapshot_id="s-future", condition_id="cond-1",
                captured_at="2026-06-06T14:00:00+00:00")

    result = capture_intraday_orderbook_depth_from_snapshots(
        conn,
        condition_ids=["cond-1"],
        recorded_at="2026-06-06T13:00:00+00:00",  # before the snapshot
    )
    assert result["rows_inserted"] == 0
    assert result["skipped_no_snapshot"] == 1
    n = conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0]
    assert n == 0, "a future snapshot must never produce a depth row"
    conn.close()


def test_anti_lookahead_picks_most_recent_at_or_before_as_of() -> None:
    conn = _conn_with_ems_and_mph()
    _insert_ems(conn, snapshot_id="s-old", condition_id="cond-1",
                captured_at="2026-06-06T12:00:00+00:00", bid="0.30", ask="0.40")
    _insert_ems(conn, snapshot_id="s-recent", condition_id="cond-1",
                captured_at="2026-06-06T12:59:00+00:00", bid="0.41", ask="0.49")
    _insert_ems(conn, snapshot_id="s-future", condition_id="cond-1",
                captured_at="2026-06-06T13:30:00+00:00", bid="0.99", ask="1.00")

    capture_intraday_orderbook_depth_from_snapshots(
        conn, condition_ids=["cond-1"], recorded_at="2026-06-06T13:00:00+00:00"
    )
    row = conn.execute(
        "SELECT snapshot_id, best_bid, best_ask FROM market_price_history"
    ).fetchone()
    assert row[0] == "s-recent", "must pick the most-recent snapshot at-or-before as_of"
    assert row[1] == 0.41 and row[2] == 0.49
    conn.close()


# ---------------------------------------------------------------------------
# FAIL-SOFT: missing EMS table => no-op status, never raises.
# ---------------------------------------------------------------------------
def test_fail_soft_missing_ems_table_is_noop() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE market_price_history (id INTEGER PRIMARY KEY, market_slug TEXT)"
    )
    conn.commit()
    result = capture_intraday_orderbook_depth_from_snapshots(
        conn, condition_ids=["cond-1"], recorded_at="2026-06-06T13:00:00+00:00"
    )
    assert result["status"] == "skipped_missing_tables"
    assert result["rows_inserted"] == 0
    conn.close()


def test_fail_soft_no_connection_is_noop() -> None:
    result = capture_intraday_orderbook_depth_from_snapshots(
        None, condition_ids=["cond-1"], recorded_at="2026-06-06T13:00:00+00:00"
    )
    assert result["status"] == "skipped_no_connection"
    assert result["rows_inserted"] == 0


def test_fail_soft_crossed_book_skipped_not_raised() -> None:
    conn = _conn_with_ems_and_mph()
    # bid > ask => crossed book => _mid_price returns None => skipped.
    _insert_ems(conn, snapshot_id="s-cross", condition_id="cond-1",
                captured_at="2026-06-06T12:00:00+00:00", bid="0.60", ask="0.40")
    result = capture_intraday_orderbook_depth_from_snapshots(
        conn, condition_ids=["cond-1"], recorded_at="2026-06-06T13:00:00+00:00"
    )
    assert result["rows_inserted"] == 0
    assert result["skipped_crossed_book"] == 1
    assert conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0] == 0
    conn.close()


# ---------------------------------------------------------------------------
# ADDITIVE-PROOF: pre-existing mid-only price_only rows are untouched; the
# depth capture only ADDS a 'full' row (different token/time -> no UNIQUE clash).
# ---------------------------------------------------------------------------
def test_additive_existing_mid_only_rows_untouched() -> None:
    conn = _conn_with_ems_and_mph()
    # Pre-existing mid-only row (the GAMMA scanner plane).
    conn.execute(
        """
        INSERT INTO market_price_history
            (market_slug, token_id, price, recorded_at, market_price_linkage, source)
        VALUES ('boston-high','tok-mid',0.47,'2026-06-06T13:05:00+00:00','price_only','GAMMA_SCANNER')
        """
    )
    conn.commit()
    before = conn.execute(
        "SELECT id, price, best_bid, market_price_linkage FROM market_price_history"
    ).fetchall()

    _insert_ems(conn, snapshot_id="s1", condition_id="cond-1",
                captured_at="2026-06-06T13:00:00+00:00", token="tok-depth")
    capture_intraday_orderbook_depth_from_snapshots(
        conn, condition_ids=["cond-1"], recorded_at="2026-06-06T13:05:00+00:00"
    )

    # The original price_only row is byte-identical (unchanged).
    after_mid = conn.execute(
        "SELECT id, price, best_bid, market_price_linkage FROM market_price_history "
        "WHERE token_id='tok-mid'"
    ).fetchall()
    assert after_mid == before, "existing mid-only row must be untouched (additive only)"
    # Exactly one NEW full row was added.
    full_rows = conn.execute(
        "SELECT COUNT(*) FROM market_price_history WHERE market_price_linkage='full'"
    ).fetchone()[0]
    assert full_rows == 1
    conn.close()


def test_no_snapshot_for_condition_writes_nothing() -> None:
    conn = _conn_with_ems_and_mph()
    # EMS has a different condition; the requested one has no snapshot.
    _insert_ems(conn, snapshot_id="s-other", condition_id="cond-OTHER",
                captured_at="2026-06-06T12:00:00+00:00")
    result = capture_intraday_orderbook_depth_from_snapshots(
        conn, condition_ids=["cond-MISSING"], recorded_at="2026-06-06T13:00:00+00:00"
    )
    assert result["rows_inserted"] == 0
    assert result["skipped_no_snapshot"] == 1
    assert conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0] == 0
    conn.close()
