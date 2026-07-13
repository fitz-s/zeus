#!/usr/bin/env python3
# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T2-a
#   ("Populate from the four durable sources: scan venue_commands token_id,
#   position_current token/no_token, executable snapshot topology join where
#   cheap"); Attack F (absence never proves zero).
"""One-time backfill for ctf_token_registry from Zeus's existing durable sources.

Scans, in priority order (a token already registered by an earlier source in
THIS pass keeps that first_source — see src.state.ctf_token_registry.record_token_seen):

  1. venue_commands.token_id            -> first_source=zeus_command
     (joined to executable_market_snapshots via snapshot_id for condition_id;
     venue_commands itself has no condition_id column)
  2. position_current.token_id/no_token_id -> first_source=attributed_fill
     (condition_id is a direct column)
  3. executable_market_snapshots (yes_token_id, no_token_id) -> first_source=market_topology

The live discovery hook for the fifth vocabulary member (positions_api_discovery)
runs continuously in src.state.chain_mirror_reconciler.run_cycle — this script
does not touch it. transfer_observation has no ingester yet (LX-1R territory)
and is not populated by this backfill.

LAW (Attack F): this script only INSERTs/advances last_confirmed_at. It never
deletes a row — a token missing from Zeus's current tables was NOT this
script's concern to begin with (a prior run's registry entry is durable
regardless of whether the source table still carries that token today).

Usage:
    python scripts/backfill_ctf_token_registry.py            # dry-run (default)
    python scripts/backfill_ctf_token_registry.py --apply     # write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.ctf_token_registry import record_token_seen
from src.state.db import get_trade_connection
from src.state.schema.ctf_token_registry_schema import ensure_table


def _scan_venue_commands(conn) -> list[tuple[str, str, str]]:
    """Return (token_id, condition_id, seen_at) from Zeus's own submitted commands."""

    rows = conn.execute(
        """
        SELECT vc.token_id, ems.condition_id, vc.created_at
        FROM venue_commands vc
        JOIN executable_market_snapshots ems ON ems.snapshot_id = vc.snapshot_id
        WHERE vc.token_id IS NOT NULL AND vc.token_id != ''
          AND ems.condition_id IS NOT NULL AND ems.condition_id != ''
        """
    ).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def _scan_position_current(conn) -> list[tuple[str, str, str]]:
    """Return (token_id, condition_id, seen_at) from Zeus's tracked positions (both legs)."""

    rows = conn.execute(
        """
        SELECT token_id, no_token_id, condition_id, updated_at
        FROM position_current
        WHERE condition_id IS NOT NULL AND condition_id != ''
          AND (
            (token_id IS NOT NULL AND token_id != '')
            OR (no_token_id IS NOT NULL AND no_token_id != '')
          )
        """
    ).fetchall()
    out: list[tuple[str, str, str]] = []
    for token_id, no_token_id, condition_id, updated_at in rows:
        seen_at = str(updated_at)
        if token_id:
            out.append((str(token_id), str(condition_id), seen_at))
        if no_token_id:
            out.append((str(no_token_id), str(condition_id), seen_at))
    return out


def _scan_executable_market_snapshots(conn) -> list[tuple[str, str, str]]:
    """Return (token_id, condition_id, seen_at) from discovered market topology."""

    rows = conn.execute(
        """
        SELECT yes_token_id, no_token_id, condition_id, captured_at
        FROM executable_market_snapshots
        WHERE condition_id IS NOT NULL AND condition_id != ''
        """
    ).fetchall()
    out: list[tuple[str, str, str]] = []
    for yes_token_id, no_token_id, condition_id, captured_at in rows:
        seen_at = str(captured_at)
        if yes_token_id:
            out.append((str(yes_token_id), str(condition_id), seen_at))
        if no_token_id:
            out.append((str(no_token_id), str(condition_id), seen_at))
    return out


def run_backfill(conn, *, apply: bool) -> dict:
    """Scan the four durable sources and (optionally) upsert ctf_token_registry.

    Returns a summary dict; when ``apply`` is False no writes happen (pure
    scan+count, safe to run against a live trade DB at any time).
    """

    sources = (
        ("zeus_command", _scan_venue_commands(conn)),
        ("attributed_fill", _scan_position_current(conn)),
        ("market_topology", _scan_executable_market_snapshots(conn)),
    )

    by_source_scanned: dict[str, int] = {}
    inserted = 0
    confirmed = 0
    seen_token_ids: set[str] = set()

    if apply:
        ensure_table(conn)

    for source_name, rows in sources:
        by_source_scanned[source_name] = len(rows)
        for token_id, condition_id, seen_at in rows:
            if not apply:
                continue
            existed = token_id in seen_token_ids or (
                conn.execute(
                    "SELECT 1 FROM ctf_token_registry WHERE token_id = ?", (token_id,)
                ).fetchone()
                is not None
            )
            record_token_seen(
                conn,
                token_id=token_id,
                condition_id=condition_id,
                source=source_name,
                seen_at=seen_at,
            )
            seen_token_ids.add(token_id)
            if existed:
                confirmed += 1
            else:
                inserted += 1

    if apply:
        conn.commit()

    distinct_tokens = len(
        {token_id for _source, rows in sources for token_id, _cond, _seen in rows}
    )
    return {
        "apply": apply,
        "scanned_by_source": by_source_scanned,
        "distinct_tokens_scanned": distinct_tokens,
        "rows_inserted": inserted if apply else None,
        "rows_confirmed": confirmed if apply else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to ctf_token_registry. Without this flag, only scans and reports counts.",
    )
    args = parser.parse_args()

    conn = get_trade_connection(write_class="bulk" if args.apply else None)
    try:
        summary = run_backfill(conn, apply=args.apply)
    finally:
        conn.close()

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
