#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_settlements_low_backfill/plan.md
"""Cross-validate scraped LOW Polymarket markets against observations.low_temp,
then optionally insert into state/zeus-world.db::settlements.

Workflow
--------
1. --plan (default): read manifest, JOIN observations, classify each pair as
   VERIFIED (obs in winning bin) or QUARANTINED (with reason). Print plan +
   write plan JSON. NO DB writes.
2. --apply: take filesystem snapshot, BEGIN IMMEDIATE TXN, insert all VERIFIED
   + QUARANTINED rows under writer signature
   `p_e_reconstruction_low_2026-04-28`. UNIQUE(city, target_date,
   temperature_metric='low') prevents collision with existing HIGH rows.

Usage
-----
    python3 backfill_low_settlements.py \\
        --manifest evidence/pm_settlement_truth_low.json \\
        --db-path  /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db \\
        --plan-out evidence/low_backfill_plan.json
    # then to apply:
    python3 backfill_low_settlements.py ... --apply

Stdlib-only: sqlite3, argparse, json, shutil, sys, pathlib, datetime, typing.
No imports from src/. The MetricIdentity values are hardcoded as strings to
match the canonical typed identity (mirroring what the harvester uses).
"""
from __future__ import annotations

import argparse
import os
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

OPERATOR_APPLY_APPROVAL_ENV = "ZEUS_OPERATOR_APPROVED_DB_MUTATION"


def _require_operator_apply_approval() -> None:
    if os.environ.get(OPERATOR_APPLY_APPROVAL_ENV) != "YES":
        raise SystemExit(
            "REFUSING --apply: this packet script can mutate zeus DB state or call "
            "external data sources. Set ZEUS_OPERATOR_APPROVED_DB_MUTATION=YES "
            "only after the active packet/current_state authorizes the mutation."
        )

# Canonical typed identity strings (mirror src/types/metric_identity.py::LOW_LOCALDAY_MIN).
# Hardcoded here intentionally — script is stdlib-only.
LOW_TEMPERATURE_METRIC = "low"
LOW_PHYSICAL_QUANTITY = "mn2t6_local_calendar_day_min"
LOW_OBSERVATION_FIELD = "low_temp"
# data_version is per-source (mirror HIGH p_e_reconstruction pattern):
# wu_icao → wu_icao_history_v1, hko → hko_daily_api_v1, etc.
DATA_VERSION_MAP = {
    "wu_icao": "wu_icao_history_v1",
    "hko":     "hko_daily_api_v1",
    "noaa":    "ogimet_metar_v1",
}

WRITER_SIGNATURE = "p_e_reconstruction_low_2026-04-28"


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text())


def fetch_obs_index(db_path: Path) -> dict[tuple[str, str], dict]:
    """Build (city_display, target_date) -> {low_temp, unit, source, station_id}."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT city, target_date, low_temp, unit, source, station_id, fetched_at "
        "FROM observations WHERE low_temp IS NOT NULL"
    ).fetchall()
    conn.close()
    idx: dict[tuple[str, str], dict] = {}
    for r in rows:
        # Some sources may write multiple rows per (city, target_date); keep the latest.
        key = (r["city"], r["target_date"])
        existing = idx.get(key)
        if existing is None:
            idx[key] = dict(r)
        else:
            # Prefer wu_icao_history > hko > ogimet
            rank = {"wu_icao_history": 3, "hko_daily_api": 2}
            if rank.get(r["source"], 1) > rank.get(existing["source"], 1):
                idx[key] = dict(r)
    return idx


def winning_bin_contains(obs_low: float, kind: str, lo: float | None, hi: float | None) -> bool:
    """Polymarket bin semantics: integer-rounded WU value falls in bin.

    Bin grammar per zeus_market_settlement_reference.md:
      point          obs_int == lo == hi
      finite_range   lo <= obs_int <= hi  (e.g. "68-69°F" → {68, 69})
      lower_shoulder obs_int <= hi        (e.g. "9°C or below")
      upper_shoulder obs_int >= lo        (e.g. "19°C or higher")
    """
    obs_int = int(round(obs_low))
    if kind == "point":
        return lo is not None and obs_int == int(lo)
    if kind == "finite_range":
        return lo is not None and hi is not None and int(lo) <= obs_int <= int(hi)
    if kind == "lower_shoulder":
        return hi is not None and obs_int <= int(hi)
    if kind == "upper_shoulder":
        return lo is not None and obs_int >= int(lo)
    return False


def build_plan(manifest: dict, obs_idx: dict[tuple[str, str], dict]) -> dict:
    plan_rows: list[dict] = []
    for rec in manifest.get("records", []):
        city = rec["city"]
        td = rec["target_date"]
        sst = rec["settlement_source_type"]
        unit = rec["unit"]
        kind = rec["winning_bin_kind"]
        lo = rec.get("pm_bin_lo")
        hi = rec.get("pm_bin_hi")

        obs = obs_idx.get((city, td))
        if obs is None:
            plan_rows.append({
                **rec,
                "obs_present": False,
                "authority": "QUARANTINED",
                "quarantine_reason": "no_observation_for_target_date",
                "obs_low": None,
                "obs_unit": None,
                "obs_source": None,
            })
            continue

        # Unit consistency
        if obs["unit"] != unit:
            plan_rows.append({
                **rec,
                "obs_present": True,
                "obs_low": obs["low_temp"],
                "obs_unit": obs["unit"],
                "obs_source": obs["source"],
                "authority": "QUARANTINED",
                "quarantine_reason": f"unit_mismatch obs={obs['unit']} pm={unit}",
            })
            continue

        in_bin = winning_bin_contains(obs["low_temp"], kind, lo, hi)
        plan_rows.append({
            **rec,
            "obs_present": True,
            "obs_low": obs["low_temp"],
            "obs_unit": obs["unit"],
            "obs_source": obs["source"],
            "obs_station_id": obs["station_id"],
            "obs_fetched_at": obs["fetched_at"],
            "authority": "VERIFIED" if in_bin else "QUARANTINED",
            "quarantine_reason": None if in_bin else "obs_outside_winning_bin",
        })

    # Summary
    n_total = len(plan_rows)
    n_verified = sum(1 for r in plan_rows if r["authority"] == "VERIFIED")
    n_quarantined = n_total - n_verified
    by_reason: dict[str, int] = {}
    for r in plan_rows:
        if r["authority"] == "QUARANTINED":
            by_reason[r["quarantine_reason"] or "?"] = by_reason.get(r["quarantine_reason"] or "?", 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": str(manifest.get("source")),
        "writer_signature": WRITER_SIGNATURE,
        "n_total": n_total,
        "n_verified": n_verified,
        "n_quarantined": n_quarantined,
        "quarantine_by_reason": by_reason,
        "rows": plan_rows,
    }


def insert_settlements(conn: sqlite3.Connection, plan: dict) -> int:
    """INSERT one row per plan row. UNIQUE(city, target_date, temperature_metric)
    will raise IntegrityError if a LOW row already exists."""
    inserted = 0
    for r in plan["rows"]:
        provenance = {
            "writer": plan["writer_signature"],
            "source_manifest": plan["source_manifest"],
            "polymarket_event_id": r.get("event_id"),
            "polymarket_market_slug": r.get("slug"),
            "polymarket_winner_market_id": r.get("winner_market_id"),
            "uma_resolved_at": r.get("uma_resolved_at"),
            "winning_bin_kind": r.get("winning_bin_kind"),
            "obs_source": r.get("obs_source"),
            "obs_station_id": r.get("obs_station_id"),
            "obs_fetched_at": r.get("obs_fetched_at"),
            "decision_time_snapshot_id": r.get("obs_fetched_at"),
        }
        if r["authority"] == "QUARANTINED":
            provenance["quarantine_reason"] = r["quarantine_reason"]

        sst = r["settlement_source_type"]
        data_version = DATA_VERSION_MAP.get(sst, "unknown_v0")

        try:
            conn.execute(
                """
                INSERT INTO settlements (
                    city, target_date, market_slug, winning_bin, settlement_value,
                    settlement_source, settled_at, authority,
                    pm_bin_lo, pm_bin_hi, unit, settlement_source_type,
                    temperature_metric, physical_quantity, observation_field,
                    data_version, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["city"],
                    r["target_date"],
                    r["slug"],
                    r["winning_bin_label"],
                    r.get("obs_low"),
                    r.get("obs_source"),
                    r.get("uma_resolved_at"),
                    r["authority"],
                    r.get("pm_bin_lo"),
                    r.get("pm_bin_hi"),
                    r.get("unit"),
                    r["settlement_source_type"],
                    LOW_TEMPERATURE_METRIC,
                    LOW_PHYSICAL_QUANTITY,
                    LOW_OBSERVATION_FIELD,
                    data_version,
                    json.dumps(provenance, default=str),
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError as e:
            print(f"  SKIP duplicate or trigger fail: {r['city']} {r['target_date']}: {e}", file=sys.stderr)
    return inserted


def main() -> int:
    p = argparse.ArgumentParser(description="Backfill LOW settlements from Polymarket manifest")
    p.add_argument("--manifest", required=True, help="JSON manifest from scrape_low_markets.py")
    p.add_argument("--db-path", required=True, help="path to zeus-world.db")
    p.add_argument("--plan-out", default=None, help="optional: write plan JSON to this path")
    p.add_argument("--apply", action="store_true", help="apply DB writes (default: plan only)")
    args = p.parse_args()
    if args.apply:
        _require_operator_apply_approval()

    manifest = load_manifest(Path(args.manifest))
    print(f"manifest n_records: {manifest.get('n_records')}")

    obs_idx = fetch_obs_index(Path(args.db_path))
    print(f"obs index size: {len(obs_idx)} rows")

    plan = build_plan(manifest, obs_idx)
    print(f"\n=== PLAN ===")
    print(f"  total      : {plan['n_total']}")
    print(f"  VERIFIED   : {plan['n_verified']}")
    print(f"  QUARANTINED: {plan['n_quarantined']}")
    if plan["quarantine_by_reason"]:
        print(f"  quarantine reasons:")
        for reason, n in plan["quarantine_by_reason"].items():
            print(f"    {n:3d}  {reason}")

    if args.plan_out:
        Path(args.plan_out).write_text(json.dumps(plan, indent=2, default=str))
        print(f"\n[ok] plan → {args.plan_out}")

    if not args.apply:
        print("\n--apply not set: no DB writes performed.")
        return 0

    # Apply path: snapshot DB, then INSERT
    db = Path(args.db_path)
    snap = db.parent / f"{db.name}.pre-low-backfill-2026-04-28"
    print(f"\n[apply] taking snapshot → {snap}")
    shutil.copy2(db, snap)
    print(f"[apply] snapshot size: {snap.stat().st_size} bytes")

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Idempotency: refuse if any LOW row already exists from this writer
        existing = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE temperature_metric='low'"
        ).fetchone()[0]
        if existing > 0:
            print(f"[apply] ABORT: {existing} LOW rows already exist; backfill not idempotent yet")
            conn.rollback()
            return 1

        n = insert_settlements(conn, plan)
        # Post-condition: count LOW rows now equals plan total
        post = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE temperature_metric='low'"
        ).fetchone()[0]
        if post != plan["n_total"]:
            print(f"[apply] ABORT: post-count={post} != expected={plan['n_total']}")
            conn.rollback()
            return 2

        conn.commit()
        print(f"[apply] inserted={n}, post_count={post}, snapshot={snap}")
    except Exception as e:
        conn.rollback()
        print(f"[apply] EXCEPTION: {e}", file=sys.stderr)
        return 3
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
