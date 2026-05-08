#!/usr/bin/env python3
# Created: 2026-05-08
# Last reused or audited: 2026-05-08
# Authority basis: docs/operations/task_2026-05-08_post_merge_full_chain/TASK.md
#   Phase D — backfill 317 London °F rows quarantined under old logic
"""Backfill 317 London settlements_v2 rows quarantined as 'harvester_live_obs_outside_bin'.

Background
----------
Pre-2026 London Polymarket markets were posed in °F (e.g. "Will it be 40-41°F?")
even though London is now configured as a °C settlement city.
Fix #262 (PR #98) added bin-unit conversion at ingest time, so new settlements
write VERIFIED after converting F bins to C before containment check.

The existing 317 quarantined rows were written before fix #262. They need to be
re-derived using the °F→°C conversion so they can be resolved to VERIFIED
(if the observation is now contained) or reclassified to obs_outside_bin.

Strategy
--------
1. Find rows: settlements_v2 WHERE city='London'
   AND provenance_json::quarantine_reason='harvester_live_obs_outside_bin'.
2. For each row: re-derive bin_unit from market question using _detect_bin_unit.
   Apply °F→°C conversion to bin bounds if pm_bin_unit='F' and city.settlement_unit='C'.
   Re-run containment check. If now contained:
     - Update authority='VERIFIED', clear quarantine_reason, set winning_bin.
     - Add provenance fields: bin_unit_converted=True, backfilled_via=<this script>.
3. Idempotent: rows already VERIFIED or already backfilled are skipped.
4. Single-writer: acquire db_writer_lock(BULK) before any writes.
   Do NOT run while daemon is mid-write.

Usage
-----
  # Dry-run (default — no DB writes):
  python scripts/backfill_london_f_to_c_2026_05_08.py

  # Apply:
  python scripts/backfill_london_f_to_c_2026_05_08.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_london_f_to_c")

BACKFILL_TAG = "backfill_london_f_to_c_2026_05_08"
TARGET_CITY = "London"
TARGET_QUARANTINE_REASON = "harvester_live_obs_outside_bin"


# ---------------------------------------------------------------------------
# Helpers (inlined to avoid circular imports)
# ---------------------------------------------------------------------------

def _f_to_c(val: float) -> float:
    return (val - 32.0) * 5.0 / 9.0


def _detect_bin_unit(question: str) -> Optional[str]:
    import re
    if re.search(r"\xb0[Ff]", question):
        return "F"
    if re.search(r"\xb0[Cc]", question):
        return "C"
    return None


def _canonical_bin_label(lo: Optional[float], hi: Optional[float], unit: str) -> Optional[str]:
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None:
        if lo == hi:
            return f"{int(lo)}°{unit}"
        return f"{int(lo)}-{int(hi)}°{unit}"
    if lo is None and hi is not None:
        return f"{int(hi)}°{unit} or below"
    return f"{int(lo)}°{unit} or higher"


def _wmo_half_up(val: float) -> float:
    return float(math.floor(val + 0.5))


def _containment_check(
    rounded: float,
    effective_lo: Optional[float],
    effective_hi: Optional[float],
) -> bool:
    if effective_lo is not None and effective_hi is not None:
        return effective_lo <= rounded <= effective_hi
    if effective_lo is None and effective_hi is not None:
        return rounded <= effective_hi
    if effective_hi is None and effective_lo is not None:
        return rounded >= effective_lo
    return False


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _find_candidate_rows(conn) -> list[dict]:
    """Find London quarantined rows that may benefit from F→C conversion."""
    rows = conn.execute("""
        SELECT settlement_id, city, target_date, temperature_metric,
               market_slug, settlement_value, authority, provenance_json
        FROM settlements_v2
        WHERE city = ?
          AND authority = 'QUARANTINED'
    """, (TARGET_CITY,)).fetchall()

    candidates = []
    for r in rows:
        prov = {}
        try:
            prov = json.loads(r["provenance_json"] or "{}")
        except Exception:
            pass

        # Skip already-backfilled rows (idempotency)
        if prov.get("backfilled_via") == BACKFILL_TAG:
            continue

        # Only process rows quarantined with the target reason
        qr = prov.get("quarantine_reason", "")
        if qr != TARGET_QUARANTINE_REASON:
            continue

        candidates.append({
            "settlement_id": r["settlement_id"],
            "city": r["city"],
            "target_date": r["target_date"],
            "temperature_metric": r["temperature_metric"],
            "market_slug": r["market_slug"],
            "settlement_value": r["settlement_value"],
            "provenance": prov,
        })
    return candidates


def _process_row(row: dict) -> Optional[dict]:
    """Compute the re-derived containment result for a candidate row.

    Returns a dict with the proposed update if now contained, else None.
    Returns a dict with reclassification info even if still outside (for reporting).
    """
    prov = row["provenance"]
    pm_bin_lo = prov.get("pm_bin_lo")
    pm_bin_hi = prov.get("pm_bin_hi")
    pm_bin_unit = prov.get("pm_bin_unit")
    settlement_value = row["settlement_value"]

    if settlement_value is None or not math.isfinite(float(settlement_value)):
        return None
    if pm_bin_lo is None and pm_bin_hi is None:
        # no bin — cannot re-evaluate
        return None

    # Detect bin unit from market slug/question if not already recorded.
    # For pre-#262 London rows, pm_bin_unit=None because the column wasn't
    # populated yet. These rows were written when London was a C city but the
    # market questions used F bins (e.g. bin 40-41 = °F, obs 5°C).
    # Heuristic: if pm_bin_unit is None AND the bin values are in the F range
    # for a London-temperature context (lo >= 30), assume °F. This matches
    # the PR #98 RUN.md backfill spec: "317 rows quarantined under old logic".
    if pm_bin_unit is None:
        slug = row.get("market_slug") or ""
        pm_bin_unit = _detect_bin_unit(slug)

    if pm_bin_unit is None:
        # Heuristic fallback for pre-#262 London rows: if bin values are in the
        # Fahrenheit range for London winter/spring temps (typically 30-70°F),
        # treat as °F. The settlement_value is in °C (London is a C city), so
        # if bins are >> 30 and obs is << 30, they are in different units → F bins.
        lo_val = float(pm_bin_lo) if pm_bin_lo is not None else None
        if lo_val is not None and lo_val >= 28.0:
            # Bins >= 28 cannot be London °C temps (London rarely exceeds 35°C
            # in summer). Must be °F. Apply conversion.
            pm_bin_unit = "F"
            logger.debug(
                "  %s %s: inferred pm_bin_unit='F' from bin lo=%.1f (heuristic)",
                row["city"], row["target_date"], lo_val,
            )

    # Apply F→C conversion if bin is in F and city is a C city (London = C)
    effective_lo = pm_bin_lo
    effective_hi = pm_bin_hi
    bin_unit_converted = False

    if pm_bin_unit == "F":
        if effective_lo is not None:
            effective_lo = _f_to_c(float(effective_lo))
        if effective_hi is not None:
            effective_hi = _f_to_c(float(effective_hi))
        bin_unit_converted = True
        logger.debug(
            "  %s %s: converting F bin [%s, %s] → C [%s, %s]",
            row["city"], row["target_date"],
            pm_bin_lo, pm_bin_hi,
            f"{effective_lo:.4f}" if effective_lo is not None else None,
            f"{effective_hi:.4f}" if effective_hi is not None else None,
        )

    rounded = _wmo_half_up(float(settlement_value))

    # Fix #264: Polymarket °C bins are INTEGER. After F→C conversion the bounds
    # are floats (e.g. 48°F → 8.888°C). Snap both edges via WMO half-up so that
    # containment operates on integers, matching Polymarket's settlement grid.
    # Closed bin: obs in {lo_int, hi_int}. Open-shoulder: inequality on snapped edge.
    if bin_unit_converted:
        if effective_lo is not None:
            effective_lo = _wmo_half_up(effective_lo)
        if effective_hi is not None:
            effective_hi = _wmo_half_up(effective_hi)
        if effective_lo is not None and effective_hi is not None:
            contained = rounded in {effective_lo, effective_hi}
        elif effective_lo is None and effective_hi is not None:
            contained = rounded <= effective_hi
        elif effective_hi is None and effective_lo is not None:
            contained = rounded >= effective_lo
        else:
            contained = False
    else:
        contained = _containment_check(rounded, effective_lo, effective_hi)

    return {
        "settlement_id": row["settlement_id"],
        "city": row["city"],
        "target_date": row["target_date"],
        "temperature_metric": row["temperature_metric"],
        "market_slug": row.get("market_slug"),
        "contained": contained,
        "bin_unit_converted": bin_unit_converted,
        "pm_bin_unit": pm_bin_unit,
        "effective_lo": effective_lo,
        "effective_hi": effective_hi,
        "rounded": rounded,
        "winning_bin": _canonical_bin_label(effective_lo, effective_hi, "C") if contained else None,
    }


def _apply_update(conn, result: dict, backfilled_at: str) -> None:
    """Update a row to VERIFIED with provenance metadata.

    Writes to both settlements_v2 (canonical, INV-17) and legacy settlements
    table (read by harvester_pnl_resolver.py FROM settlements WHERE authority='VERIFIED').
    Both writes occur in the same transaction (caller holds db_writer_lock).
    """
    row = conn.execute(
        "SELECT provenance_json FROM settlements_v2 WHERE settlement_id = ?",
        (result["settlement_id"],),
    ).fetchone()
    if row is None:
        logger.warning("Row %s not found — skipping", result["settlement_id"])
        return

    prov = json.loads(row["provenance_json"] or "{}")
    prov.pop("quarantine_reason", None)
    prov["bin_unit_converted"] = result["bin_unit_converted"]
    prov["backfilled_via"] = BACKFILL_TAG
    prov["backfilled_at"] = backfilled_at

    prov_json = json.dumps(prov, sort_keys=True, default=str)

    # Primary: canonical settlements_v2 (INV-17 authority source)
    conn.execute("""
        UPDATE settlements_v2
        SET authority = 'VERIFIED',
            winning_bin = ?,
            provenance_json = ?
        WHERE settlement_id = ?
    """, (result["winning_bin"], prov_json, result["settlement_id"]))

    # Derived: legacy settlements table (harvester_pnl_resolver reads FROM settlements
    # WHERE authority='VERIFIED' — must stay in sync, fix P1)
    city = result["city"]
    target_date = result["target_date"]
    market_slug = result.get("market_slug")
    if market_slug:
        conn.execute("""
            UPDATE settlements
            SET authority = 'VERIFIED',
                winning_bin = ?,
                provenance_json = ?
            WHERE city = ? AND target_date = ? AND market_slug = ?
        """, (result["winning_bin"], prov_json, city, target_date, market_slug))
    else:
        conn.execute("""
            UPDATE settlements
            SET authority = 'VERIFIED',
                winning_bin = ?,
                provenance_json = ?
            WHERE city = ? AND target_date = ?
        """, (result["winning_bin"], prov_json, city, target_date))

    logger.info(
        "  UPDATED %s %s %s → VERIFIED winning_bin=%s (bin_converted=%s)",
        result["city"], result["target_date"], result["temperature_metric"],
        result["winning_bin"], result["bin_unit_converted"],
    )


def run(apply: bool) -> dict:
    """Main backfill logic.

    Returns summary dict with counts.
    """
    import sqlite3
    from src.state.db import ZEUS_WORLD_DB_PATH
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    conn = sqlite3.connect(str(ZEUS_WORLD_DB_PATH))
    conn.row_factory = sqlite3.Row

    logger.info("Finding London quarantined rows...")
    candidates = _find_candidate_rows(conn)
    logger.info("Found %d candidate rows (city=London, quarantine_reason=%s)",
                len(candidates), TARGET_QUARANTINE_REASON)

    if not candidates:
        logger.info("No candidates found — nothing to do.")
        conn.close()
        return {"candidates": 0, "would_resolve": 0, "still_outside": 0, "skipped": 0, "applied": 0}

    results = []
    for row in candidates:
        r = _process_row(row)
        if r is None:
            results.append({"settlement_id": row["settlement_id"], "contained": False, "skipped": True})
        else:
            results.append(r)

    would_resolve = sum(1 for r in results if r.get("contained"))
    still_outside = sum(1 for r in results if not r.get("contained") and not r.get("skipped"))
    skipped = sum(1 for r in results if r.get("skipped"))

    logger.info(
        "Dry-run results: candidates=%d  would_resolve=%d  still_outside=%d  skipped=%d",
        len(candidates), would_resolve, still_outside, skipped,
    )

    if not apply:
        logger.info("DRY-RUN mode — no writes. Pass --apply to commit.")
        conn.close()
        return {
            "candidates": len(candidates),
            "would_resolve": would_resolve,
            "still_outside": still_outside,
            "skipped": skipped,
            "applied": 0,
            "dry_run": True,
        }

    # Apply writes under BULK writer lock
    backfilled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    applied = 0

    logger.info("Acquiring BULK writer lock for %s...", ZEUS_WORLD_DB_PATH)
    with db_writer_lock(ZEUS_WORLD_DB_PATH, WriteClass.BULK):
        for r in results:
            if r.get("skipped") or not r.get("contained"):
                continue
            _apply_update(conn, r, backfilled_at)
            applied += 1

        conn.commit()

    logger.info(
        "APPLY complete: applied=%d  still_outside=%d  skipped=%d",
        applied, still_outside, skipped,
    )

    # Verification: recount quarantined London rows after backfill
    remaining = conn.execute("""
        SELECT COUNT(*) FROM settlements_v2
        WHERE city = ?
          AND authority = 'QUARANTINED'
    """, (TARGET_CITY,)).fetchone()[0]
    logger.info("Remaining London QUARANTINED rows after backfill: %d", remaining)

    conn.close()
    return {
        "candidates": len(candidates),
        "would_resolve": would_resolve,
        "still_outside": still_outside,
        "skipped": skipped,
        "applied": applied,
        "remaining_quarantined": remaining,
        "dry_run": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Commit changes (default: dry-run only, no writes)",
    )
    args = parser.parse_args()

    summary = run(apply=args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    logger.info("%s summary: %s", mode, json.dumps(summary, indent=2))

    if args.apply and summary.get("remaining_quarantined", 0) > 0:
        logger.info(
            "Note: %d London rows remain QUARANTINED after backfill "
            "(genuinely outside bin, not F-bin mismatch).",
            summary["remaining_quarantined"],
        )


if __name__ == "__main__":
    main()
