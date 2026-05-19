# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md v3 §4.3 (Path D backfill, DELETE-by-source THEN INSERT); operator directive 2026-05-19 "paths按main写入"
"""Backfill decision_events from decision_log.artifact_json (Path D).

Reads decision_log from the PRIMARY world DB (never worktree-local), extracts
trade_case entries via from_artifact_json(), resolves city->market_slug via
market_events_v2, and writes to decision_events with source='phase0_backfill'.

DELETE-by-source THEN INSERT per critic round 2 SEV-2:
  DELETE decision_events WHERE source='phase0_backfill' AND <natural-key>
  THEN INSERT (no INSERT OR IGNORE -- IGNORE silently swallows bug-fix re-runs).

Path F honesty: PR-3+6 timing fields NULL for backfill rows (historical artifact_json
did not capture them). polymarket_end_anchor_source defaults to 'gamma_explicit'
(Phase 0 critic B2 verdict -- dominant case, retroactive labeling).

schema_version=12 for backfill rows (PR-T1-A schema at time of backfill).

Usage:
    python scripts/backfill_decision_events_from_artifact_json.py [--dry-run] [--limit N]

--dry-run: parse and resolve but do NOT write; prints would-be rowcount.
--limit N: process at most N decision_log rows (default: all).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from collections import defaultdict
from typing import Any

# Allow running from repo root
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from src.contracts.decision_natural_key import (
    decision_event_id_v1_hash,
    from_artifact_json,
)
from src.state.db_paths import primary_forecasts_db_path, primary_world_db_path
from src.state.db_writer_lock import WriteClass, db_writer_lock

_SCHEMA_VERSION = 12  # PR-T1-A schema at time of backfill
_SOURCE = "phase0_backfill"
_CHUNK_SIZE = 500


def _iter_trade_cases(artifact_json_str: str):
    """Yield individual trade_case dicts from a decision_log artifact_json string."""
    try:
        j = json.loads(artifact_json_str)
    except (json.JSONDecodeError, TypeError):
        return
    trade_cases = j.get("trade_cases")
    if not isinstance(trade_cases, list):
        return
    yield from trade_cases


def _build_slug_map() -> dict[tuple[str, str, str], str]:
    """Build (city, target_date, temperature_metric) -> market_slug lookup.

    Reads from PRIMARY forecasts DB (market_events_v2 lives in forecasts, not world).
    """
    fc_path = primary_forecasts_db_path()
    if not fc_path.exists():
        print(f"WARNING: PRIMARY forecasts DB not found at {fc_path}; slug_map will be empty.", file=sys.stderr)
        return {}
    fc_conn = sqlite3.connect(f"file:{fc_path}?mode=ro", uri=True)
    fc_conn.row_factory = sqlite3.Row
    fc_conn.execute("PRAGMA query_only=ON")
    try:
        rows = fc_conn.execute(
            """
            SELECT city, target_date, temperature_metric, market_slug
            FROM market_events_v2
            WHERE market_slug IS NOT NULL
              AND city IS NOT NULL
            """
        ).fetchall()
    finally:
        fc_conn.close()
    return {
        (r["city"], r["target_date"], r["temperature_metric"]): r["market_slug"]
        for r in rows
    }


def _get_base_seq(conn: sqlite3.Connection, market_slug: str, temperature_metric: str,
                  target_date: str, observation_time: str) -> int:
    """Get next available decision_seq after DELETE of phase0_backfill rows.

    After DELETE WHERE source='phase0_backfill' for this natural key, only
    source='live_decision' rows remain. Base seq is MAX(live seq)+1 or 0.
    """
    row = conn.execute(
        """
        SELECT COALESCE(MAX(decision_seq), -1) + 1
        FROM decision_events
        WHERE market_slug = ?
          AND temperature_metric = ?
          AND target_date = ?
          AND observation_time = ?
        """,
        (market_slug, temperature_metric, target_date, observation_time),
    ).fetchone()
    return int(row[0])


def _float_or_none(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _extract_fields(tc: dict[str, Any]) -> dict[str, Any]:
    """Extract available fields from a trade_case dict."""
    return {
        "outcome": str(tc.get("outcome") or "pending"),
        "side": str(tc.get("direction") or tc.get("side") or ""),
        "strategy_key": str(tc.get("strategy_key") or tc.get("strategy") or ""),
        "decision_time": str(tc.get("timestamp") or tc.get("decision_time") or ""),
        "p_posterior": _float_or_none(tc.get("p_posterior")),
        "edge": _float_or_none(tc.get("edge") or tc.get("decision_edge")),
        "target_size_usd": _float_or_none(tc.get("target_size_usd")),
        "target_price": _float_or_none(tc.get("limit_price") or tc.get("target_price")),
        "forecast_time": tc.get("forecast_time") or tc.get("forecast_available_at"),
        "provider_reported_time": tc.get("provider_reported_time"),
        "observation_available_at": str(tc.get("available_at") or tc.get("timestamp") or ""),
        "condition_id": tc.get("condition_id"),
    }


def run_backfill(*, dry_run: bool = False, limit: int | None = None) -> dict[str, int]:
    """Run the backfill. Returns stats dict."""
    db_path = primary_world_db_path()
    if not db_path.exists():
        print(f"FATAL: PRIMARY world DB not found at {db_path}.", file=sys.stderr)
        sys.exit(2)

    stats: dict[str, int] = {
        "decision_log_rows_read": 0,
        "trade_cases_examined": 0,
        "natural_key_recovered": 0,
        "market_slug_resolved": 0,
        "rows_deleted": 0,
        "rows_inserted": 0,
        "skipped_no_slug": 0,
        "skipped_parse_fail": 0,
    }

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        slug_map = _build_slug_map()

        limit_clause = f"LIMIT {limit}" if limit is not None else ""
        log_rows = conn.execute(
            f"""
            SELECT id, artifact_json, timestamp
            FROM decision_log
            ORDER BY id
            {limit_clause}
            """
        ).fetchall()
        stats["decision_log_rows_read"] = len(log_rows)

        chunks = [log_rows[i:i + _CHUNK_SIZE] for i in range(0, len(log_rows), _CHUNK_SIZE)]

        for chunk_idx, chunk in enumerate(chunks):
            # Collect all work for this chunk before acquiring lock
            pending: list[tuple[str, str, str, str, dict[str, Any]]] = []

            for log_row in chunk:
                for tc in _iter_trade_cases(log_row["artifact_json"] or ""):
                    stats["trade_cases_examined"] += 1
                    partial = from_artifact_json(tc)
                    if partial is None:
                        stats["skipped_parse_fail"] += 1
                        continue
                    stats["natural_key_recovered"] += 1

                    city, temperature_metric, target_date, observation_time, _ = partial
                    market_slug = slug_map.get((city, target_date, temperature_metric))
                    if market_slug is None:
                        stats["skipped_no_slug"] += 1
                        continue
                    stats["market_slug_resolved"] += 1

                    fields = _extract_fields(tc)
                    if not fields["observation_available_at"]:
                        fields["observation_available_at"] = observation_time

                    pending.append((market_slug, temperature_metric, target_date,
                                    observation_time, fields))

            if dry_run:
                # Report would-be inserts (one per resolved trade_case)
                stats["rows_deleted"] += len(pending)
                stats["rows_inserted"] += len(pending)
                print(
                    f"  [dry-run] chunk {chunk_idx+1}/{len(chunks)}: "
                    f"would process {len(pending)} rows"
                )
                continue

            if not pending:
                continue

            # Group by natural key (market_slug, metric, target_date, observation_time)
            # so that DELETE fires once per group and all rows in that group get
            # sequential decision_seq values. Without grouping, the second trade-case
            # for the same natural key would DELETE the first one, leaving only 1 row
            # per (market_slug, metric, target_date, observation_time) tuple.
            NaturalKey = tuple[str, str, str, str]
            grouped: dict[NaturalKey, list[dict[str, Any]]] = defaultdict(list)
            for market_slug, temperature_metric, target_date, observation_time, fields in pending:
                grouped[(market_slug, temperature_metric, target_date, observation_time)].append(fields)

            with db_writer_lock(db_path, WriteClass.BULK):
                for (market_slug, temperature_metric, target_date, observation_time), group_fields in grouped.items():
                    # Step 1: DELETE all prior phase0_backfill rows for this natural key
                    # (one DELETE per group — preserves source='live_decision' rows)
                    result = conn.execute(
                        """
                        DELETE FROM decision_events
                        WHERE market_slug = ?
                          AND temperature_metric = ?
                          AND target_date = ?
                          AND observation_time = ?
                          AND source = 'phase0_backfill'
                        """,
                        (market_slug, temperature_metric, target_date, observation_time),
                    )
                    stats["rows_deleted"] += result.rowcount

                    # Step 2: Base seq from remaining live_decision rows after DELETE
                    base_seq = _get_base_seq(
                        conn, market_slug, temperature_metric, target_date, observation_time
                    )

                    # Step 3: INSERT all rows in this group with sequential seq values
                    for offset, fields in enumerate(group_fields):
                        seq = base_seq + offset
                        deid = decision_event_id_v1_hash(
                            market_slug=market_slug,
                            temperature_metric=temperature_metric,
                            target_date=target_date,
                            observation_time=observation_time,
                            decision_seq=seq,
                        )
                        conn.execute(
                            """
                            INSERT INTO decision_events (
                                market_slug, temperature_metric, target_date,
                                observation_time, decision_seq,
                                condition_id, decision_event_id, decision_time,
                                outcome, side, strategy_key,
                                cycle_id, cycle_iteration,
                                p_posterior, edge, target_size_usd, target_price,
                                forecast_time, provider_reported_time,
                                observation_available_at, polymarket_end_anchor_source,
                                first_member_observed_time, run_complete_time,
                                zeus_submit_intent_time, venue_ack_time,
                                first_inclusion_block_time, finality_confirmed_time,
                                clock_skew_estimate_ms_at_submit,
                                raw_orderbook_hash_transition_delta_ms,
                                schema_version, source
                            ) VALUES (
                                ?,?,?,?,?,
                                ?,?,?,
                                ?,?,?,
                                ?,?,
                                ?,?,?,?,
                                ?,?,
                                ?,?,
                                ?,?,
                                ?,?,
                                ?,?,
                                ?,
                                ?,
                                ?,?
                            )
                            """,
                            (
                                market_slug, temperature_metric, target_date,
                                observation_time, seq,
                                fields["condition_id"], deid, fields["decision_time"],
                                fields["outcome"], fields["side"], fields["strategy_key"],
                                None, None,  # cycle_id, cycle_iteration (Phase 2+)
                                fields["p_posterior"], fields["edge"],
                                fields["target_size_usd"], fields["target_price"],
                                fields["forecast_time"], fields["provider_reported_time"],
                                fields["observation_available_at"],
                                "gamma_explicit",  # polymarket_end_anchor_source: Phase 0 critic B2 default
                                # PR-6 timing fields NULL for backfill (Path F honesty)
                                None,  # first_member_observed_time
                                None,  # run_complete_time
                                None,  # zeus_submit_intent_time
                                None,  # venue_ack_time
                                None,  # first_inclusion_block_time
                                None,  # finality_confirmed_time
                                None,  # clock_skew_estimate_ms_at_submit
                                None,  # raw_orderbook_hash_transition_delta_ms
                                _SCHEMA_VERSION, _SOURCE,
                            ),
                        )
                        stats["rows_inserted"] += 1

                conn.commit()

            print(
                f"  chunk {chunk_idx+1}/{len(chunks)}: "
                f"total inserted so far: {stats['rows_inserted']}"
            )

    finally:
        conn.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill decision_events from decision_log.artifact_json (Path D)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Parse and resolve but do NOT write to DB.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum decision_log rows to process (default: all).",
    )
    args = parser.parse_args()

    print(
        f"{'[DRY-RUN] ' if args.dry_run else ''}Backfilling decision_events from "
        f"PRIMARY world DB: {primary_world_db_path()}"
    )
    if args.limit:
        print(f"  limit: {args.limit} decision_log rows")

    stats = run_backfill(dry_run=args.dry_run, limit=args.limit)

    print("\nBackfill complete:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    if args.dry_run:
        print(f"\n[dry-run] would insert {stats['rows_inserted']} rows (no writes made)")


if __name__ == "__main__":
    main()
