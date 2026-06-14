# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: Operator LIVE blocker 2026-06-13 (25h zero live orders). Root cause
#   (measured on the live 15GB zeus_trades.db): reconstruct_weather_market_from_static_topology
#   ran `condition_id IN (...) ORDER BY captured_at DESC` with NO LIMIT over the append-only
#   executable_market_snapshots table (3.18M rows, ~318/condition), materializing median 1078 /
#   max 7002 SELECT* rows per family at median 205ms / p95 536ms even though it keeps only the
#   latest row per (condition_id, side). The 302-family warm sweep cost ~82s; on the ~5s
#   topology-phase budget per 20s cycle only ~18 families/cycle were warmed, so each family's
#   executable book was fresh barely 30s of a ~5min sweep period and the reactor's 30s
#   price-freshness gate found a stale book ~90% of wall-clock -> EXECUTABLE_SNAPSHOT_STALE
#   requeue storm (processed=0, retried=18-23/cycle), zero orders.
"""RELATIONSHIP tests for the warm-lane executable-snapshot freshness bound.

These are Fitz-methodology RELATIONSHIP tests: they assert a cross-module invariant at
the boundary between the append-only snapshot store (which accumulates history forever
under a DELETE/UPDATE-forbidding trigger) and the warm-lane reconstruction that feeds the
reactor's 30s price-freshness gate — not just single-function behavior.

INVARIANTS (the category these make impossible):

  INV-FRESH-1 (reconstruct cost is history-INDEPENDENT): the per-family reconstruct must
    read only the latest-per-side rows via the (condition_id, captured_at DESC) index, NOT
    scan the full accumulated history. Concretely, the number of snapshot rows the
    reconstruct query materializes for a family must stay BOUNDED (≈ a small constant per
    condition_id) no matter how many historical snapshots have accumulated. On revert to
    the no-LIMIT `ORDER BY captured_at DESC` scan this assertion goes RED (the row count
    grows with history depth), which is exactly the warm-lane starvation that zeroed live
    orders for 25h.

  INV-FRESH-2 (correctness is preserved under deep history): the reconstructed market must
    be IDENTICAL whether the family has 2 snapshots or 2000 — same latest YES/NO token map,
    same fetched_at_utc (latest captured_at), same outcome identity. Speed must not change
    the answer.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.contracts.executable_market_snapshot import ExecutableMarketSnapshot
from src.data.market_scanner import reconstruct_weather_market_from_static_topology
from src.state.snapshot_repo import init_snapshot_schema, insert_snapshot


_BASE = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)


def _snapshot_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_snapshot_schema(conn)
    return conn


def _insert_side(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str,
    condition_id: str,
    side: str,
    captured_at: datetime,
) -> None:
    """Insert one append-only snapshot row for a (condition_id, side) at captured_at."""
    yes_token = f"yes-{condition_id}"
    no_token = f"no-{condition_id}"
    selected = yes_token if side == "YES" else no_token
    insert_snapshot(
        conn,
        ExecutableMarketSnapshot(
            snapshot_id=snapshot_id,
            gamma_market_id=f"gamma-{condition_id}",
            event_id="evt-warm-fresh",
            event_slug="highest-temperature-in-warmtown-on-june-15-2026",
            condition_id=condition_id,
            question_id=f"question-{condition_id}",
            yes_token_id=yes_token,
            no_token_id=no_token,
            selected_outcome_token_id=selected,
            outcome_label=side,
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            fee_details={"source": "test"},
            token_map_raw={"YES": yes_token, "NO": no_token},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.42"),
            orderbook_top_ask=Decimal("0.44"),
            orderbook_depth_jsonb='{"asks":[{"price":"0.44","size":"100"}],"bids":[{"price":"0.42","size":"100"}]}',
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=captured_at,
            freshness_deadline=captured_at + timedelta(seconds=30),
        ),
    )


def _build_family_history(
    conn: sqlite3.Connection,
    *,
    condition_ids: list[str],
    captures_per_condition: int,
) -> list[dict]:
    """Populate `captures_per_condition` YES+NO snapshot pairs per condition (append-only,
    oldest..newest) and return the static topology rows the reconstruct consumes."""
    topology_rows: list[dict] = []
    for cond in condition_ids:
        for i in range(captures_per_condition):
            captured_at = _BASE - timedelta(minutes=(captures_per_condition - i))
            _insert_side(
                conn,
                snapshot_id=f"{cond}-YES-{i:04d}",
                condition_id=cond,
                side="YES",
                captured_at=captured_at,
            )
            _insert_side(
                conn,
                snapshot_id=f"{cond}-NO-{i:04d}",
                condition_id=cond,
                side="NO",
                captured_at=captured_at + timedelta(milliseconds=1),
            )
        topology_rows.append(
            {
                "condition_id": cond,
                "market_slug": "highest-temperature-in-warmtown-on-june-15-2026",
                "range_label": f"bin-{cond}",
                "range_low": None,
                "range_high": None,
                "outcome": None,
                "token_id": f"yes-{cond}",
                "city": "Warmtown",
                "target_date": "2026-06-15",
                "temperature_metric": "high",
            }
        )
    return topology_rows


class _RowCountingConn:
    """Wraps a sqlite3.Connection and records, per execute() against
    executable_market_snapshots, how many rows the resulting cursor yields. This is the
    instrument that distinguishes an index-bounded latest-per-side read from a full-history
    scan — the cross-module cost the warm lane's freshness depends on."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.snapshot_rows_materialized = 0
        self.snapshot_query_count = 0

    def execute(self, sql, params=()):
        cur = self._conn.execute(sql, params)
        if "executable_market_snapshots" in sql:
            rows = cur.fetchall()
            if "executable_market_snapshots" in sql:
                self.snapshot_query_count += 1
                self.snapshot_rows_materialized += len(rows)
            return _PrefetchedCursor(rows, cur)
        return cur

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _PrefetchedCursor:
    def __init__(self, rows, cur):
        self._rows = rows
        self._cur = cur

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)

    def __getattr__(self, name):
        return getattr(self._cur, name)


def _reconstruct_with_counting(real_conn, topology_rows):
    counting = _RowCountingConn(real_conn)
    market = reconstruct_weather_market_from_static_topology(
        counting, topology_rows=topology_rows, now_utc=_BASE
    )
    return market, counting


# ---------------------------------------------------------------------------
# INV-FRESH-1: reconstruct cost does not grow with accumulated history
# ---------------------------------------------------------------------------

def test_reconstruct_rows_materialized_independent_of_history_depth():
    """The warm-lane reconstruct must read a BOUNDED number of snapshot rows regardless of
    how deep the append-only history is. A no-LIMIT `ORDER BY captured_at DESC` scan
    materializes O(history) rows and makes this RED — the live warm-lane starvation that
    zeroed orders for 25h."""
    condition_ids = [f"0xcond{idx:02d}" for idx in range(11)]  # a full weather family

    shallow = _snapshot_conn()
    topo_shallow = _build_family_history(
        shallow, condition_ids=condition_ids, captures_per_condition=2
    )
    _, count_shallow = _reconstruct_with_counting(shallow, topo_shallow)

    deep = _snapshot_conn()
    topo_deep = _build_family_history(
        deep, condition_ids=condition_ids, captures_per_condition=500
    )
    _, count_deep = _reconstruct_with_counting(deep, topo_deep)

    # Sanity: the deep family really does have ~1000 rows per condition on disk.
    total_deep_rows = deep.execute(
        "SELECT COUNT(*) FROM executable_market_snapshots"
    ).fetchone()[0]
    assert total_deep_rows >= 11 * 500 * 2, total_deep_rows

    # THE INVARIANT: reconstruct materializes essentially the same (small) number of rows
    # whether the family has 2 captures or 500 captures per condition. We allow a tiny
    # constant per condition (latest YES + latest NO + latest-overall = at most 3/condition).
    bound = len(condition_ids) * 3
    assert count_deep.snapshot_rows_materialized <= bound, (
        f"reconstruct materialized {count_deep.snapshot_rows_materialized} rows on a "
        f"deep-history family (bound={bound}); it is scanning accumulated history instead "
        "of seeking the latest-per-side via the (condition_id, captured_at DESC) index — "
        "the warm-lane starvation regression (no-LIMIT ORDER BY captured_at DESC)."
    )
    # And it must NOT scale with depth: deep ≈ shallow (history-independent).
    assert count_deep.snapshot_rows_materialized <= count_shallow.snapshot_rows_materialized * 2 + len(condition_ids), (
        f"reconstruct rows grew with history depth: shallow="
        f"{count_shallow.snapshot_rows_materialized} deep={count_deep.snapshot_rows_materialized}"
    )


# ---------------------------------------------------------------------------
# INV-FRESH-2: correctness is identical under shallow vs deep history
# ---------------------------------------------------------------------------

def test_reconstruct_identical_under_shallow_and_deep_history():
    """Speed must not change the answer: the reconstructed market (token map, condition set,
    latest captured_at) is byte-identical whether the family has 2 or 500 captures."""
    condition_ids = [f"0xcond{idx:02d}" for idx in range(11)]

    shallow = _snapshot_conn()
    topo = _build_family_history(
        shallow, condition_ids=condition_ids, captures_per_condition=2
    )
    market_shallow = reconstruct_weather_market_from_static_topology(
        shallow, topology_rows=topo, now_utc=_BASE
    )

    deep = _snapshot_conn()
    topo_deep = _build_family_history(
        deep, condition_ids=condition_ids, captures_per_condition=500
    )
    market_deep = reconstruct_weather_market_from_static_topology(
        deep, topology_rows=topo_deep, now_utc=_BASE
    )

    assert market_shallow is not None and market_deep is not None

    def _identity(market):
        return {
            "slug": market["slug"],
            "condition_ids": sorted(market["condition_ids"]),
            "outcomes": sorted(
                (o["condition_id"], o["token_id"], o["no_token_id"], o["question_id"])
                for o in market["outcomes"]
            ),
        }

    assert _identity(market_shallow) == _identity(market_deep)
    # The latest captured_at (fetched_at_utc) is the SAME — both pick the newest pair, which
    # is identical in both fixtures (the LAST inserted pair sits at _BASE - 1min).
    assert market_shallow["fetched_at_utc"] == market_deep["fetched_at_utc"]


def test_reconstruct_picks_latest_when_a_side_has_a_capture_run():
    """A condition with a RUN of same-side captures (latest NO sits many rows behind the
    latest YES) must still recover the latest NO. This is the real-data shape (live worst
    case: 42 consecutive YES captures before the latest NO) that a too-tight per-side LIMIT
    would silently drop — guarding the latest-per-side seek, not a blanket LIMIT."""
    conn = _snapshot_conn()
    cond = "0xrun"
    # Oldest: one NO. Then a long run of YES-only captures (newer). Latest YES is most recent.
    _insert_side(conn, snapshot_id=f"{cond}-NO-old", condition_id=cond, side="NO",
                 captured_at=_BASE - timedelta(hours=5))
    for i in range(60):
        _insert_side(conn, snapshot_id=f"{cond}-YES-{i:04d}", condition_id=cond, side="YES",
                     captured_at=_BASE - timedelta(minutes=(60 - i)))
    topo = [{
        "condition_id": cond,
        "market_slug": "highest-temperature-in-warmtown-on-june-15-2026",
        "range_label": "bin-run",
        "range_low": None, "range_high": None, "outcome": None,
        "token_id": f"yes-{cond}",
        "city": "Warmtown", "target_date": "2026-06-15", "temperature_metric": "high",
    }]
    market = reconstruct_weather_market_from_static_topology(conn, topology_rows=topo, now_utc=_BASE)
    assert market is not None, (
        "reconstruct dropped a condition whose latest NO sits behind a long YES run — a "
        "too-tight per-side LIMIT would do this; the latest-per-side index seek must not."
    )
    assert market["outcomes"][0]["no_token_id"] == f"no-{cond}"
    assert market["outcomes"][0]["token_id"] == f"yes-{cond}"
