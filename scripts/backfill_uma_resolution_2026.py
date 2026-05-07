#!/usr/bin/env python3
# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: docs/operations/task_2026-05-04_oracle_kelly_evidence_rebuild/PLAN.md §A5
#                  architecture/script_manifest.yaml backfill_uma_resolution_2026.py
"""One-shot 90-day UMA Optimistic Oracle SettlementResolved backfill.

Scans Polygon RPC for SettlementResolved events for all condition_ids tracked
in market_events_v2, from --from-date (default 2026-02-07) to --to-date
(default today). Writes to uma_resolution table via the same INSERT path as
the live listener — idempotent on (condition_id, tx_hash) primary key.

Usage
-----
    python3 scripts/backfill_uma_resolution_2026.py
    python3 scripts/backfill_uma_resolution_2026.py --from-date 2026-01-01
    python3 scripts/backfill_uma_resolution_2026.py --rpc-url https://polygon-rpc.com

Prerequisites
-------------
    settings.json must have uma.polygon_rpc_url and uma.oo_contract_address,
    OR pass --rpc-url / --contract-address on the command line.
    If no RPC config is available, the script exits with a clear error message.

Safety
------
    INSERT OR IGNORE on (condition_id, tx_hash) — repeated runs are harmless.
    Does not touch any other table or touch the trading daemon.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection, ZEUS_WORLD_DB_PATH
from src.state.uma_resolution_listener import (
    UmaRpcClient,
    poll_uma_resolutions,
    init_uma_resolution_schema,
    UMA_OO_SETTLE_EVENT_SIGNATURE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("backfill_uma_resolution")

# ---------------------------------------------------------------------------
# Minimal Polygon JSON-RPC client (same pattern as the live tick)
# ---------------------------------------------------------------------------


class _UmaHttpRpcClient(UmaRpcClient):
    """Polygon eth_getLogs over httpx."""

    def __init__(self, rpc_url: str) -> None:
        self._rpc_url = rpc_url

    def get_logs(
        self,
        *,
        contract_address: str,
        topic0: str,
        condition_ids,
        from_block: int,
        to_block=None,
    ) -> list[dict]:
        try:
            import httpx
        except ImportError:
            logger.error("httpx is required: pip install httpx")
            return []

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getLogs",
            "params": [{
                "address": contract_address,
                "topics": [topic0],
                "fromBlock": hex(from_block),
                **({"toBlock": hex(to_block)} if to_block is not None else {}),
            }],
        }
        try:
            resp = httpx.post(self._rpc_url, json=payload, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                logger.warning("RPC error: %s", data["error"])
                return []
            return data.get("result") or []
        except Exception as exc:
            logger.warning("RPC call failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Block-number estimation from date (approximate via Polygon ~2s block time)
# ---------------------------------------------------------------------------

_POLYGON_GENESIS_BLOCK = 0
_POLYGON_APPROX_BLOCK_TIME_SECS = 2.0

# Reference: block ~65_000_000 was around 2026-01-01 (rough estimate).
# Operator should verify or override via --from-block / --to-block.
_REFERENCE_DATE = date(2026, 1, 1)
_REFERENCE_BLOCK = 67_500_000


def _date_to_approx_block(d: date) -> int:
    """Estimate Polygon block number for a given date (UTC midnight)."""
    dt_utc = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    ref_dt = datetime(_REFERENCE_DATE.year, _REFERENCE_DATE.month, _REFERENCE_DATE.day,
                      tzinfo=timezone.utc)
    delta_secs = (dt_utc - ref_dt).total_seconds()
    block = _REFERENCE_BLOCK + int(delta_secs / _POLYGON_APPROX_BLOCK_TIME_SECS)
    return max(_POLYGON_GENESIS_BLOCK, block)


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def _load_condition_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT condition_id FROM market_events_v2 "
        "WHERE condition_id IS NOT NULL AND condition_id != ''"
    ).fetchall()
    return [str(r[0]) for r in rows]


def run_backfill(
    *,
    rpc_url: str,
    contract_address: str,
    from_block: int,
    to_block: int,
) -> dict:
    conn = get_world_connection()
    try:
        init_uma_resolution_schema(conn)
        condition_ids = _load_condition_ids(conn)
        if not condition_ids:
            logger.warning("No condition_ids found in market_events_v2; nothing to backfill.")
            conn.close()
            return {"status": "no_condition_ids", "resolutions_written": 0}

        logger.info(
            "Backfilling UMA resolutions for %d condition_ids, blocks %d..%d",
            len(condition_ids), from_block, to_block,
        )

        rpc_client = _UmaHttpRpcClient(rpc_url)

        # Poll in chunks to avoid Polygon log-range limits (typically 2000-block max).
        CHUNK_SIZE = 2000
        total_resolutions = 0
        current_block = from_block

        while current_block <= to_block:
            chunk_end = min(current_block + CHUNK_SIZE - 1, to_block)
            logger.info("Scanning blocks %d..%d", current_block, chunk_end)

            resolutions = poll_uma_resolutions(
                condition_ids=condition_ids,
                contract_address=contract_address,
                rpc_client=rpc_client,
                conn=conn,
                from_block=current_block,
                to_block=chunk_end,
            )
            if resolutions:
                conn.commit()
                logger.info(
                    "  blocks %d..%d: %d resolution(s) written",
                    current_block, chunk_end, len(resolutions),
                )
                total_resolutions += len(resolutions)
            else:
                logger.debug("  blocks %d..%d: no resolutions", current_block, chunk_end)

            current_block = chunk_end + 1

        conn.commit()
        logger.info("Backfill complete. Total resolutions written: %d", total_resolutions)
        return {"status": "ok", "resolutions_written": total_resolutions}

    except Exception as exc:
        logger.error("Backfill failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"status": "error", "error": str(exc), "resolutions_written": 0}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify() -> None:
    ro = sqlite3.connect(str(ZEUS_WORLD_DB_PATH), timeout=10)
    try:
        count = ro.execute("SELECT COUNT(*) FROM uma_resolution").fetchone()[0]
        logger.info("uma_resolution row count: %d", count)
        if count > 0:
            sample = ro.execute(
                "SELECT condition_id, tx_hash, resolved_at_utc FROM uma_resolution "
                "ORDER BY block_number DESC LIMIT 3"
            ).fetchall()
            for row in sample:
                logger.info("  sample: condition_id=%s tx=%s resolved_at=%s",
                            str(row[0])[:20] + "...", str(row[1])[:20] + "...", row[2])
    finally:
        ro.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill UMA Optimistic Oracle SettlementResolved events (90-day window)."
    )
    parser.add_argument(
        "--from-date", default="2026-02-07",
        help="Start date (YYYY-MM-DD, inclusive). Default: 2026-02-07.",
    )
    parser.add_argument(
        "--to-date", default=date.today().isoformat(),
        help="End date (YYYY-MM-DD, inclusive). Default: today.",
    )
    parser.add_argument(
        "--from-block", type=int, default=None,
        help="Override from_block (skips date-to-block estimation).",
    )
    parser.add_argument(
        "--to-block", type=int, default=None,
        help="Override to_block (skips date-to-block estimation).",
    )
    parser.add_argument(
        "--rpc-url", default=None,
        help="Polygon RPC URL (overrides settings.json).",
    )
    parser.add_argument(
        "--contract-address", default=None,
        help="UMA OO contract address (overrides settings.json).",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="Skip backfill, just print current uma_resolution count.",
    )
    args = parser.parse_args()

    if args.verify_only:
        verify()
        return

    # Resolve RPC config from settings.json if not passed on CLI.
    rpc_url = args.rpc_url
    contract_address = args.contract_address
    if not rpc_url or not contract_address:
        try:
            from src.config import settings
            uma_cfg = settings.get("uma", {})
            rpc_url = rpc_url or uma_cfg.get("polygon_rpc_url", "")
            contract_address = contract_address or uma_cfg.get("oo_contract_address", "")
        except Exception as exc:
            logger.warning("Could not load settings.json: %s", exc)

    if not rpc_url:
        logger.error(
            "No Polygon RPC URL. Set settings.uma.polygon_rpc_url or pass --rpc-url."
        )
        logger.info("Running --verify-only instead to show current state.")
        verify()
        sys.exit(0)

    if not contract_address:
        logger.error(
            "No UMA OO contract address. Set settings.uma.oo_contract_address or pass --contract-address."
        )
        logger.info(
            "NOTE: Polymarket UMA OO V2 contract is typically "
            "0x5945Bae9c5a6b2a6F5f9b06e9Ee6E0bD3aC3df57 on Polygon — "
            "verify before use."
        )
        sys.exit(1)

    # Resolve block range.
    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)
    from_block = args.from_block if args.from_block is not None else _date_to_approx_block(from_date)
    to_block = args.to_block if args.to_block is not None else _date_to_approx_block(to_date)

    logger.info(
        "UMA backfill: from=%s (block~%d) to=%s (block~%d) rpc=%s contract=%s",
        from_date, from_block, to_date, to_block, rpc_url[:40] + "...", contract_address[:20] + "...",
    )

    result = run_backfill(
        rpc_url=rpc_url,
        contract_address=contract_address,
        from_block=from_block,
        to_block=to_block,
    )
    logger.info("Result: %s", result)
    verify()

    if result.get("status") == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
