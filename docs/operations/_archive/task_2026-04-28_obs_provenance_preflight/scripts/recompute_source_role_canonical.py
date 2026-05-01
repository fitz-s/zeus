#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md
#                  + corrective remediation for drift confirmed 2026-04-28T03:55Z
"""Corrective recompute: recompute source_role using tier_resolver as the
single source of truth. NO heuristic fallback.

This corrects the data corruption introduced by `enrich_observation_instants_v2_provenance.py`
v3, whose lazy-import path was wrong (`parents[3]` was `docs/`, not zeus root).
Tier_resolver was silently uninlinkable, fallback heuristic ran instead, and
the heuristic mis-labeled ~815k meteostat + ~50k ogimet rows as
'historical_hourly' when they should have been 'fallback_evidence'.

This script:
  - imports tier_resolver from the verified path
  - REFUSES to fall back to any heuristic — if tier_resolver is unavailable,
    this script aborts non-zero rather than corrupting data further
  - per (city, source) pair, computes the canonical source_role
  - UPDATEs rows where current value disagrees with tier_resolver
  - sets training_allowed = 1 iff source_role == 'historical_hourly'

Behavior: dry-run default. --apply requires snapshot.
"""
from __future__ import annotations

import argparse
import os
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

# ABSOLUTE path to zeus repo root — refuses ambiguity.
# This script is at docs/operations/<packet>/scripts/<file>; zeus root is parents[4]
# of __file__. We assert this rather than guess.
SCRIPT = Path(__file__).resolve()
ZEUS_ROOT = SCRIPT.parents[4]  # scripts → packet → operations → docs → REPO_ROOT

if not (ZEUS_ROOT / "src" / "data" / "tier_resolver.py").exists():
    sys.stderr.write(
        f"FATAL: zeus root not found at {ZEUS_ROOT}. "
        f"src/data/tier_resolver.py is mandatory authority — refusing to run.\n"
    )
    sys.exit(2)

sys.path.insert(0, str(ZEUS_ROOT))

# Hard fail on import error — NO heuristic fallback. The whole point of this
# script is to use canonical truth.
from src.data.tier_resolver import source_role_assessment_for_city_source


def main() -> int:
    p = argparse.ArgumentParser(description="Canonical source_role recompute (no heuristic fallback)")
    p.add_argument("--db-path", required=True)
    p.add_argument("--apply", action="store_true", help="Execute UPDATEs (default: dry-run)")
    args = p.parse_args()
    if args.apply:
        _require_operator_apply_approval()

    db = Path(args.db_path)
    if not db.exists():
        sys.stderr.write(f"db not found: {db}\n")
        return 2

    if args.apply:
        snap = db.parent / f"{db.name}.pre-source-role-recompute-2026-04-28"
        if not snap.exists():
            print(f"[apply] snapshotting {db} → {snap}")
            shutil.copy2(db, snap)
        else:
            print(f"[apply] snapshot already exists: {snap}")

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            conn.execute("BEGIN IMMEDIATE")

        # 1. enumerate distinct (city, source) pairs that exist
        pairs = conn.execute(
            "SELECT DISTINCT city, source FROM observation_instants_v2"
        ).fetchall()
        print(f"distinct (city, source) pairs in obs_v2: {len(pairs)}")

        # 2. for each, ask tier_resolver canonical role
        decisions: list[dict] = []
        unresolvable: list[dict] = []
        for r in pairs:
            try:
                a = source_role_assessment_for_city_source(r["city"], r["source"])
                decisions.append({
                    "city": r["city"],
                    "source": r["source"],
                    "canonical_source_role": a.source_role,
                    "canonical_training_allowed": 1 if a.source_role == "historical_hourly" else 0,
                })
            except Exception as e:
                unresolvable.append({"city": r["city"], "source": r["source"], "error": str(e)})

        if unresolvable:
            print(f"WARNING: {len(unresolvable)} pairs unresolvable by tier_resolver:")
            for u in unresolvable[:5]:
                print(f"  {u}")

        # 3. count rows that disagree with tier_resolver
        n_total_rows = 0
        n_role_mismatch = 0
        n_training_mismatch = 0
        update_groups: list[dict] = []
        for d in decisions:
            row_meta = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN COALESCE(source_role,'') != ? THEN 1 ELSE 0 END) AS role_diff,
                       SUM(CASE WHEN COALESCE(training_allowed, 1) != ? THEN 1 ELSE 0 END) AS train_diff
                FROM observation_instants_v2 WHERE city=? AND source=?
                """,
                (d["canonical_source_role"], d["canonical_training_allowed"], d["city"], d["source"]),
            ).fetchone()
            n_total_rows += row_meta["n"]
            n_role_mismatch += row_meta["role_diff"]
            n_training_mismatch += row_meta["train_diff"]
            if row_meta["role_diff"] > 0 or row_meta["train_diff"] > 0:
                update_groups.append({
                    "city": d["city"],
                    "source": d["source"],
                    "n": row_meta["n"],
                    "to_role": d["canonical_source_role"],
                    "to_training": d["canonical_training_allowed"],
                    "role_diff": row_meta["role_diff"],
                    "train_diff": row_meta["train_diff"],
                })

        print(f"\ntotal rows scanned: {n_total_rows}")
        print(f"rows with source_role mismatch: {n_role_mismatch}")
        print(f"rows with training_allowed mismatch: {n_training_mismatch}")
        print(f"\n(city, source) groups requiring update: {len(update_groups)}")
        for g in update_groups[:10]:
            print(f"  {g['city']:20s} {g['source']:35s} → role={g['to_role']:18s} train={g['to_training']}  ({g['role_diff']} role_diffs, {g['train_diff']} train_diffs)")
        if len(update_groups) > 10:
            print(f"  ... +{len(update_groups)-10} more")

        if not args.apply:
            print("\n[dry-run] no DB changes made.")
            return 0

        # 4. apply per-group UPDATE
        n_updated = 0
        for g in update_groups:
            cur = conn.execute(
                """
                UPDATE observation_instants_v2
                SET source_role = ?, training_allowed = ?
                WHERE city = ? AND source = ?
                  AND (COALESCE(source_role,'') != ? OR COALESCE(training_allowed,1) != ?)
                """,
                (g["to_role"], g["to_training"], g["city"], g["source"],
                 g["to_role"], g["to_training"]),
            )
            n_updated += cur.rowcount

        # 5. post-condition: every row's source_role agrees with tier_resolver
        # (recompute: count remaining mismatches)
        post_role_diff = 0
        for d in decisions:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM observation_instants_v2 "
                "WHERE city=? AND source=? AND COALESCE(source_role,'') != ?",
                (d["city"], d["source"], d["canonical_source_role"]),
            ).fetchone()[0]
            post_role_diff += cnt

        if post_role_diff != 0:
            sys.stderr.write(f"FATAL: {post_role_diff} rows still mismatch after UPDATE — rolling back.\n")
            conn.rollback()
            return 3

        conn.commit()
        print(f"\n[apply] updated {n_updated} rows; post-mismatch=0; snapshot at {snap}")
        return 0
    except Exception as e:
        if args.apply:
            conn.rollback()
        sys.stderr.write(f"ERROR: {type(e).__name__}: {e}\n")
        return 4
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
