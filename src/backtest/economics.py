"""ECONOMICS purpose tombstone and readiness contract.

Per packet 2026-04-27 §01 §3.C: the ECONOMICS lane is structurally
impossible until upstream data unblocks (market_events_v2 populated +
parity contracts pass). Rather than emit `pnl_available: False`
limitation flags from a loop that runs anyway, we refuse to run.

When data-layer P4.A unblocks, this module's body fills in. Until
then, callers see the unblock pointer in the error itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Connection
from typing import NoReturn

from src.backtest.purpose import PurposeContractViolation


REQUIRED_ECONOMICS_TABLES: tuple[str, ...] = (
    "market_events_v2",
    "market_price_history",
    "executable_market_snapshots",
    "venue_trade_facts",
    "position_lots",
    "probability_trace_fact",
    "trade_decisions",
    "selection_family_fact",
    "selection_hypothesis_fact",
    "settlements_v2",
    "outcome_fact",
)

_FULL_MARKET_PRICE_LINKAGE_COLUMNS = frozenset({
    "market_price_linkage",
    "source",
    "best_bid",
    "best_ask",
    "raw_orderbook_hash",
})

_FULL_MARKET_PRICE_LINKAGE_SOURCES = (
    "CLOB_WS_MARKET",
    "CLOB_BEST_BID_ASK",
    "CLOB_ORDERBOOK",
)


@dataclass(frozen=True)
class EconomicsReadiness:
    ready: bool
    blockers: tuple[str, ...]
    table_counts: tuple[tuple[str, int | None], ...]

    def count_for(self, table: str) -> int | None:
        for name, count in self.table_counts:
            if name == table:
                return count
        return None

    def blocker_summary(self) -> str:
        if not self.blockers:
            return "no readiness blockers"
        return ", ".join(self.blockers)


def _table_exists(conn: Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _count_rows(conn: Connection, table: str, where_sql: str | None = None) -> int:
    query = f"SELECT COUNT(*) FROM {table}"
    if where_sql:
        query += f" WHERE {where_sql}"
    return int(conn.execute(query).fetchone()[0])


def check_economics_readiness(conn: Connection | None) -> EconomicsReadiness:
    """Return the structural unblock state for promotion-grade economics.

    This is a read-only preflight, not an economics engine. A ready result
    means the minimum substrate exists; it does not authorize live promotion,
    staged-live execution, or PnL computation by itself.
    """
    if conn is None:
        return EconomicsReadiness(
            ready=False,
            blockers=("missing_connection",),
            table_counts=(),
        )

    blockers: list[str] = []
    counts: list[tuple[str, int | None]] = []
    existing_tables: set[str] = set()
    for table in REQUIRED_ECONOMICS_TABLES:
        if not _table_exists(conn, table):
            blockers.append(f"missing_table:{table}")
            counts.append((table, None))
            continue
        existing_tables.add(table)
        count = _count_rows(conn, table)
        counts.append((table, count))
        if count <= 0:
            blockers.append(f"empty_table:{table}")

    if "venue_trade_facts" in existing_tables:
        try:
            if _count_rows(conn, "venue_trade_facts", "state = 'CONFIRMED'") <= 0:
                blockers.append("no_confirmed_venue_trade_facts")
        except Exception:
            blockers.append("invalid_schema:venue_trade_facts.state")

    if "position_lots" in existing_tables:
        try:
            confirmed_lots = _count_rows(
                conn,
                "position_lots",
                "state IN ('CONFIRMED_EXPOSURE','ECONOMICALLY_CLOSED','SETTLED')",
            )
            if confirmed_lots <= 0:
                blockers.append("no_confirmed_position_lots")
        except Exception:
            blockers.append("invalid_schema:position_lots.state")

    if "executable_market_snapshots" in existing_tables:
        try:
            executable_facts = _count_rows(
                conn,
                "executable_market_snapshots",
                "COALESCE(min_tick_size, '') <> '' "
                "AND COALESCE(min_order_size, '') <> '' "
                "AND COALESCE(fee_details_json, '') <> '' "
                "AND neg_risk IS NOT NULL "
                "AND COALESCE(raw_orderbook_hash, '') <> ''",
            )
            if executable_facts <= 0:
                blockers.append("no_fee_tick_min_order_neg_risk_orderbook_snapshot_facts")
        except Exception:
            blockers.append("invalid_schema:executable_market_snapshots.fee_tick_min_order_neg_risk_orderbook")

    if "market_price_history" in existing_tables:
        try:
            columns = _table_columns(conn, "market_price_history")
            if not _FULL_MARKET_PRICE_LINKAGE_COLUMNS.issubset(columns):
                blockers.append("market_price_history_lacks_full_linkage_contract")
            else:
                sources_sql = ",".join(repr(source) for source in _FULL_MARKET_PRICE_LINKAGE_SOURCES)
                full_linkage_rows = _count_rows(
                    conn,
                    "market_price_history",
                    "LOWER(COALESCE(market_price_linkage, '')) = 'full' "
                    f"AND UPPER(COALESCE(source, '')) IN ({sources_sql}) "
                    "AND best_bid IS NOT NULL "
                    "AND best_ask IS NOT NULL "
                    "AND best_bid >= 0.0 AND best_bid <= 1.0 "
                    "AND best_ask >= 0.0 AND best_ask <= 1.0 "
                    "AND best_bid <= best_ask "
                    "AND COALESCE(raw_orderbook_hash, '') <> ''",
                )
                if full_linkage_rows <= 0:
                    blockers.append("no_full_market_price_linkage_rows")
        except Exception:
            blockers.append("invalid_schema:market_price_history.full_linkage")

    if "trade_decisions" in existing_tables:
        try:
            if _count_rows(conn, "trade_decisions", "COALESCE(decision_snapshot_id, '') <> ''") <= 0:
                blockers.append("no_trade_decision_snapshot_linkage")
        except Exception:
            blockers.append("invalid_schema:trade_decisions.decision_snapshot_id")

    if "probability_trace_fact" in existing_tables:
        try:
            if _count_rows(conn, "probability_trace_fact", "COALESCE(decision_snapshot_id, '') <> ''") <= 0:
                blockers.append("no_probability_trace_snapshot_linkage")
        except Exception:
            blockers.append("invalid_schema:probability_trace_fact.decision_snapshot_id")

    if "selection_hypothesis_fact" in existing_tables:
        try:
            if _count_rows(conn, "selection_hypothesis_fact", "selected_post_fdr = 1") <= 0:
                blockers.append("no_selected_fdr_hypothesis_facts")
        except Exception:
            blockers.append("invalid_schema:selection_hypothesis_fact.selected_post_fdr")

    if "market_events_v2" in existing_tables:
        try:
            if _count_rows(conn, "market_events_v2", "COALESCE(outcome, '') <> ''") <= 0:
                blockers.append("no_market_event_outcomes")
        except Exception:
            blockers.append("invalid_schema:market_events_v2.outcome")

    if "outcome_fact" in existing_tables:
        try:
            if _count_rows(conn, "outcome_fact", "outcome IS NOT NULL AND COALESCE(decision_snapshot_id, '') <> ''") <= 0:
                blockers.append("no_resolution_matched_outcome_facts")
        except Exception:
            blockers.append("invalid_schema:outcome_fact.outcome_snapshot")

    blockers.append("economics_engine_not_implemented")

    return EconomicsReadiness(
        ready=not blockers,
        blockers=tuple(blockers),
        table_counts=tuple(counts),
    )


def _tombstone_message(readiness: EconomicsReadiness) -> str:
    return (
        "ECONOMICS purpose is tombstoned. It requires populated "
        "market_events_v2 + market_price_history + parity contracts "
        "(market_price_linkage='full', Sizing.KELLY_BOOTSTRAP, "
        "Selection.BH_FDR). Readiness blockers: "
        f"{readiness.blocker_summary()}. See unblock plan at "
        "docs/operations/task_2026-04-27_backtest_first_principles_review/"
        "02_blocker_handling_plan.md §3.B."
    )


def run_economics(*_, conn: Connection | None = None, **__) -> NoReturn:
    readiness = check_economics_readiness(conn)
    raise PurposeContractViolation(_tombstone_message(readiness))
