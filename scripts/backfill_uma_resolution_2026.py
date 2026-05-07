#!/usr/bin/env python3
# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: daemon-completeness-2026-05-07 task — UMA chain-only backfill
#                  for settlements_v2 gap 2026-01-01 to 2026-05-07.
#                  Rewrote stub (2026-05-07): original only wrote uma_resolution and
#                  incorrectly filtered by condition_id topic (not indexed in UMA Settle).
"""Backfill UMA Settle events → uma_resolution + settlements_v2, 2026-01-01 to 2026-05-07.

Operator requirement: chain-only truth. No Gamma API, no Polymarket API.

Strategy
--------
1. Scan Polygon UMA OO V2 Settle events in 100k-block chunks.
2. For each log: decode ancillaryData text, parse city + date + metric (high/low),
   derive condition_id via CTF keccak formula (requester + questionId + 2).
3. Match city to cities.json. Look up source-correct observation for (city, date, metric).
4. Write uma_resolution row + call _write_settlement_truth (INV-05 gate via
   SettlementSemantics.assert_settlement_value).
5. Commit per chunk. Print progress and final summary.

Run (foreground — do NOT background-detach):
    source .venv/bin/activate
    python scripts/backfill_uma_resolution_2026.py [--dry-run] [--from-block N]
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_uma")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Verified 2026-05-07: block 81,043,972 = 2025-12-31 18:53 UTC
# block 81,087,000 ≈ 2026-01-01 00:00 UTC
DEFAULT_FROM_BLOCK = 81_087_000

CHUNK_SIZE = 100_000  # 100k blocks per eth_getLogs call (tested on Tenderly 2026-05-07)

UMA_OO_ADDRESS = "0xee3afe347d5c74317041e2618c49534daf887c24"
UMA_SETTLE_SIG = "0x3f384afb4bd9f0aef0298c80399950011420eb33b0e1a750b20966270247b9a0"

# ---------------------------------------------------------------------------
# City name normalisation — ancillaryData text → cities.json name
# ---------------------------------------------------------------------------

CITY_NAME_MAP: dict[str, str] = {
    "new york": "NYC", "nyc": "NYC", "new york city": "NYC",
    "los angeles": "Los Angeles", "miami": "Miami", "chicago": "Chicago",
    "houston": "Houston", "dallas": "Dallas", "atlanta": "Atlanta",
    "austin": "Austin", "denver": "Denver", "seattle": "Seattle",
    "toronto": "Toronto", "montreal": "Montreal",
    "london": "London", "paris": "Paris", "amsterdam": "Amsterdam",
    "berlin": "Berlin", "madrid": "Madrid", "milan": "Milan",
    "munich": "Munich", "rome": "Rome", "stockholm": "Stockholm",
    "helsinki": "Helsinki", "oslo": "Oslo", "warsaw": "Warsaw",
    "vienna": "Vienna", "istanbul": "Istanbul", "moscow": "Moscow",
    "ankara": "Ankara", "dubai": "Dubai", "riyadh": "Riyadh",
    "jeddah": "Jeddah", "cairo": "Cairo", "nairobi": "Nairobi",
    "lagos": "Lagos", "cape town": "Cape Town", "johannesburg": "Johannesburg",
    "mumbai": "Mumbai", "delhi": "Delhi", "new delhi": "Delhi",
    "bangalore": "Bangalore", "bengaluru": "Bangalore",
    "hyderabad": "Hyderabad", "lucknow": "Lucknow",
    "karachi": "Karachi", "lahore": "Lahore", "dhaka": "Dhaka",
    "beijing": "Beijing", "shanghai": "Shanghai", "guangzhou": "Guangzhou",
    "shenzhen": "Shenzhen", "chengdu": "Chengdu", "chongqing": "Chongqing",
    "hong kong": "Hong Kong", "taipei": "Taipei",
    "seoul": "Seoul", "busan": "Busan",
    "tokyo": "Tokyo", "osaka": "Osaka",
    "jakarta": "Jakarta", "manila": "Manila",
    "kuala lumpur": "Kuala Lumpur", "singapore": "Singapore",
    "bangkok": "Bangkok",
    "ho chi minh": "Ho Chi Minh City", "ho chi minh city": "Ho Chi Minh City",
    "hanoi": "Hanoi",
    "sydney": "Sydney", "melbourne": "Melbourne",
    "brisbane": "Brisbane", "perth": "Perth",
    "auckland": "Auckland", "wellington": "Wellington",
    "buenos aires": "Buenos Aires",
    "sao paulo": "São Paulo", "são paulo": "São Paulo",
    "rio de janeiro": "Rio de Janeiro",
    "bogota": "Bogotá", "bogotá": "Bogotá",
    "lima": "Lima", "santiago": "Santiago", "mexico city": "Mexico City",
    "tehran": "Tehran", "baghdad": "Baghdad",
}

_RE_METRIC = re.compile(r"\b(highest|lowest)\s+temperature\s+in\s+(.+?)\s+be\b", re.IGNORECASE)
_RE_DATE = re.compile(
    r"\bon\s+(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:,\s*(\d{4}))?",
    re.IGNORECASE,
)
_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_RE_BIN_HIGHER = re.compile(r"be\s+(-?\d+)°[CF]\s+or\s+higher", re.IGNORECASE)
_RE_BIN_BELOW = re.compile(r"be\s+(-?\d+)°[CF]\s+or\s+(?:below|lower)", re.IGNORECASE)
_RE_BIN_SINGLE = re.compile(r"be\s+(-?\d+)°[CF](?:\s+on\b|\s*\?)", re.IGNORECASE)


def _parse_ancillary_text(text: str) -> tuple[str | None, str | None, str | None]:
    """Return (city_name, 'YYYY-MM-DD', 'high'|'low') or (None, None, None)."""
    m = _RE_METRIC.search(text)
    if not m:
        return None, None, None
    metric = "high" if m.group(1).lower() == "highest" else "low"
    raw_city = m.group(2).strip().rstrip(",?")
    city_key = raw_city.lower().strip()
    city_name = CITY_NAME_MAP.get(city_key)
    if city_name is None:
        for k, v in CITY_NAME_MAP.items():
            if city_key.startswith(k) or k.startswith(city_key):
                city_name = v
                break
    if city_name is None:
        return None, None, None
    dm = _RE_DATE.search(text)
    if not dm:
        return None, None, None
    month = _MONTH_MAP[dm.group(1).lower()]
    day = int(dm.group(2))
    year = int(dm.group(3)) if dm.group(3) else 2026
    try:
        target_date = f"{year:04d}-{month:02d}-{day:02d}"
        datetime(year, month, day)
    except ValueError:
        return None, None, None
    return city_name, target_date, metric


def _parse_bin_bounds(text: str) -> tuple[float | None, float | None]:
    m = _RE_BIN_HIGHER.search(text)
    if m:
        return float(m.group(1)), None
    m = _RE_BIN_BELOW.search(text)
    if m:
        return None, float(m.group(1))
    m = _RE_BIN_SINGLE.search(text)
    if m:
        v = float(m.group(1))
        return v, v
    return None, None


# ---------------------------------------------------------------------------
# RPC helpers
# ---------------------------------------------------------------------------

_block_ts_cache: dict[int, int] = {}


def _rpc_post(rpc_url: str, payload: dict, *, timeout: float = 60.0) -> dict:
    import httpx
    for attempt in range(3):
        try:
            r = httpx.post(rpc_url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("RPC attempt %d failed: %s", attempt + 1, exc)
            time.sleep(2 ** attempt)
    return {}


def _get_current_block(rpc_url: str) -> int:
    data = _rpc_post(rpc_url, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}, timeout=15)
    return int(data.get("result", "0x0"), 16)


def _get_logs_chunk(rpc_url: str, from_block: int, to_block: int) -> list[dict]:
    data = _rpc_post(rpc_url, {
        "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
        "params": [{"address": UMA_OO_ADDRESS, "topics": [UMA_SETTLE_SIG],
                    "fromBlock": hex(from_block), "toBlock": hex(to_block)}],
    })
    if "error" in data:
        logger.warning("eth_getLogs error %d-%d: %s", from_block, to_block, data["error"])
        return []
    return data.get("result") or []


def _get_block_ts(rpc_url: str, block_number: int) -> int:
    if block_number in _block_ts_cache:
        return _block_ts_cache[block_number]
    data = _rpc_post(rpc_url, {"jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber",
                                "params": [hex(block_number), False]}, timeout=15)
    block = data.get("result") or {}
    ts_raw = block.get("timestamp", "0x0")
    ts = int(ts_raw, 16) if isinstance(ts_raw, str) else int(ts_raw)
    _block_ts_cache[block_number] = ts
    return ts


# ---------------------------------------------------------------------------
# Price decoder — determines YES vs NO outcome for a UMA Settle event
# ---------------------------------------------------------------------------

def _decode_price(data_hex: str) -> int:
    """Extract the int256 price from the ABI-encoded Settle event data field.

    Slot layout (each 32 bytes = 64 hex chars):
        [0]  identifier (bytes32)
        [1]  timestamp  (uint256)
        [2]  offset to ancillaryData (uint256)
        [3]  price (int256)   ← slot we want
        [4]  payout (uint256)

    YES outcome: price == 1e18 (10**18)
    NO outcome:  price == 0
    Returns 0 on any decode failure.
    """
    try:
        hex_str = data_hex[2:] if data_hex.startswith("0x") else data_hex
        if len(hex_str) < 256:  # Need at least 4 slots
            return 0
        price_hex = hex_str[192:256]  # Slot [3]
        raw = int(price_hex, 16)
        # int256 sign handling
        if raw >= (1 << 255):
            raw -= (1 << 256)
        return raw
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Main backfill
# ---------------------------------------------------------------------------

def run_backfill(*, rpc_url: str, from_block: int, to_block: int, dry_run: bool = False) -> dict:
    from src.config import cities_by_name
    from src.state.db import get_world_connection
    from src.state.uma_resolution_listener import (
        decode_ancillary_data, derive_condition_id,
        init_uma_resolution_schema, record_resolution, ResolvedMarket,
    )
    from src.ingest.harvester_truth_writer import (
        _write_settlement_truth, _lookup_settlement_obs,
    )

    conn = get_world_connection()
    init_uma_resolution_schema(conn)

    total_logs = weather_logs = uma_written = settlements_written = 0
    skipped_no_obs = skipped_no_city = errors = 0
    chunk_count = chunk_start = from_block

    total_blocks = to_block - from_block
    logger.info("Backfill: blocks %d→%d (%d blocks, ~%d chunks of %d)",
                from_block, to_block, total_blocks, total_blocks // CHUNK_SIZE + 1, CHUNK_SIZE)

    while chunk_start <= to_block:
        chunk_end = min(chunk_start + CHUNK_SIZE - 1, to_block)
        chunk_count += 1
        logs = _get_logs_chunk(rpc_url, chunk_start, chunk_end)
        total_logs += len(logs)

        if chunk_count % 5 == 0 or logs:
            pct = 100 * (chunk_start - from_block) / max(total_blocks, 1)
            logger.info("Chunk %d [%.1f%%]: blocks %d-%d → %d events (total=%d weather=%d written=%d)",
                        chunk_count, pct, chunk_start, chunk_end, len(logs),
                        total_logs, weather_logs, settlements_written)

        for log in logs:
            try:
                data_hex = log.get("data") or "0x"
                ancillary_data = decode_ancillary_data(data_hex)
                ad_text = ancillary_data.decode("utf-8", errors="replace")
                city_name, target_date, temperature_metric = _parse_ancillary_text(ad_text)
                if city_name is None:
                    continue
                weather_logs += 1

                # Only write for YES-settled bins (price == 1e18).
                # Each temperature bin is a separate UMA market; NO bins (price=0)
                # must not create a settlement row — only the winning bin does.
                price = _decode_price(data_hex)
                YES_PRICE = 10 ** 18
                if price != YES_PRICE:
                    continue  # NO outcome — skip, the YES bin will be its own event

                city = cities_by_name.get(city_name)
                if city is None:
                    skipped_no_city += 1
                    continue

                condition_id = derive_condition_id(log["topics"][1], ancillary_data)
                blk = int(log.get("blockNumber", "0x0"), 16)
                block_ts = _get_block_ts(rpc_url, blk)
                resolved_at = (datetime.fromtimestamp(block_ts, tz=timezone.utc)
                               if block_ts else datetime.now(timezone.utc))

                # Always write uma_resolution for every YES event
                resolution = ResolvedMarket(
                    condition_id=condition_id,
                    resolved_value=price,
                    tx_hash=log.get("transactionHash") or "",
                    block_number=blk,
                    resolved_at_utc=resolved_at,
                    raw_log=dict(log),
                )
                if not dry_run:
                    record_resolution(conn, resolution)
                    uma_written += 1

                obs_row = _lookup_settlement_obs(conn, city, target_date,
                                                 temperature_metric=temperature_metric)
                if obs_row is None:
                    skipped_no_obs += 1
                    continue

                pm_bin_lo, pm_bin_hi = _parse_bin_bounds(ad_text)

                # Derive market_slug — required by log_settlement_v2 identity check.
                # Use a stable canonical form: city_slug-date-metric
                city_slug = city_name.lower().replace(" ", "_")
                event_slug = f"uma_backfill_{city_slug}_{target_date}_{temperature_metric}"

                if dry_run:
                    logger.info("DRY-RUN YES: %s %s %s bin=[%s,%s] obs=%s cid=%s",
                                city_name, target_date, temperature_metric,
                                pm_bin_lo, pm_bin_hi,
                                obs_row.get("observed_temp"), condition_id[:12])
                    settlements_written += 1
                    continue

                _write_settlement_truth(
                    conn, city, target_date, pm_bin_lo, pm_bin_hi,
                    event_slug=event_slug, obs_row=obs_row,
                    resolved_market_outcomes=None,
                    temperature_metric=temperature_metric,
                )
                settlements_written += 1

            except Exception as exc:
                logger.warning("log error tx=%s: %s", log.get("transactionHash", "?")[:12], exc)
                errors += 1

        if not dry_run:
            try:
                conn.commit()
            except Exception as exc:
                logger.error("Commit failed chunk %d: %s", chunk_count, exc)

        chunk_start = chunk_end + 1

    if not dry_run:
        try:
            conn.commit()
        except Exception as exc:
            logger.error("Final commit failed: %s", exc)

    # Final distribution query
    dist = []
    try:
        rows = conn.execute(
            "SELECT temperature_metric, COUNT(*), MIN(target_date), MAX(target_date) "
            "FROM settlements_v2 GROUP BY 1"
        ).fetchall()
        dist = [(r[0], r[1], r[2], r[3]) for r in rows]
    except Exception:
        pass
    conn.close()

    return {
        "dry_run": dry_run,
        "chunks": chunk_count,
        "total_settle_events": total_logs,
        "weather_events": weather_logs,
        "uma_rows_written": uma_written,
        "settlements_written": settlements_written,
        "skipped_no_obs": skipped_no_obs,
        "skipped_no_city": skipped_no_city,
        "errors": errors,
        "settlements_v2_distribution": dist,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import json
    parser = argparse.ArgumentParser(description="Backfill UMA settlements → settlements_v2")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-block", type=int, default=DEFAULT_FROM_BLOCK,
                        help=f"Start block (default {DEFAULT_FROM_BLOCK} ≈ 2026-01-01)")
    parser.add_argument("--to-block", type=int, default=None,
                        help="End block (default: current chain tip)")
    args = parser.parse_args()

    settings = json.loads((PROJECT_ROOT / "config" / "settings.json").read_text())
    rpc_url = settings.get("uma", {}).get("polygon_rpc_url", "https://polygon.gateway.tenderly.co")

    to_block = args.to_block or _get_current_block(rpc_url)
    logger.info("Chain tip: %d  from_block: %d", to_block, args.from_block)

    summary = run_backfill(rpc_url=rpc_url, from_block=args.from_block,
                           to_block=to_block, dry_run=args.dry_run)

    print("\n=== Backfill summary ===")
    for k, v in summary.items():
        if k == "settlements_v2_distribution":
            print(f"  {k}:")
            for row in v:
                print(f"    metric={row[0]} count={row[1]} range={row[2]}..{row[3]}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
