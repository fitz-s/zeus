#!/usr/bin/env python3
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: operator instruction 2026-04-28T04:05Z — remove any synthetic
#                  provenance data introduced by this packet's earlier scripts.
"""Remove synthetic provenance fields injected by my earlier OBS work this session.

Synthetic fields to remove:
  observation_instants_v2.provenance_json keys:
    - payload_hash         (was: sha256 of canonical row digest, NOT real payload)
    - parser_version       (was: "legacy:enrich_2026-04-28")
    - source_url           (was: "legacy://obs_v2/...")
    - source_file          (was: "legacy://obs_v2/...")
  observations.provenance_metadata:
    - SET to NULL where the value carries our synth signature
      `synthesized_by="legacy:backfill_obs_prov_2026-04-28"`

What is KEPT (because it is canonical, not synthetic):
  - observation_instants_v2.source_role (computed by canonical tier_resolver)
  - observation_instants_v2.training_allowed (computed from canonical source_role)

After this script runs, Gates 3/4/5 will fire again. That is intentional and
correct — synthetic data should never block real preflight.

stdlib-only. dry-run default. Snapshot before apply.
"""
from __future__ import annotations

import argparse
import os
import json
import shutil
import sqlite3
import sys
from pathlib import Path

OPERATOR_APPLY_APPROVAL_ENV = "ZEUS_OPERATOR_APPROVED_DB_MUTATION"


def _require_operator_apply_approval() -> None:
    if os.environ.get(OPERATOR_APPLY_APPROVAL_ENV) != "YES":
        raise SystemExit(
            "REFUSING --apply: this packet script can mutate zeus DB state or call "
            "external data sources. Set ZEUS_OPERATOR_APPROVED_DB_MUTATION=YES "
            "only after the active packet/current_state authorizes the mutation."
        )

SYNTHETIC_PROV_KEYS = ("payload_hash", "parser_version", "source_url", "source_file")
SYNTH_PARSER_VERSION = "legacy:enrich_2026-04-28"
SYNTH_BACKFILL_TAG = "legacy:backfill_obs_prov_2026-04-28"


def main() -> int:
    p = argparse.ArgumentParser(description="Remove synthetic provenance data introduced this session")
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
        snap = db.parent / f"{db.name}.pre-synthetic-removal-2026-04-28"
        if snap.exists():
            print(f"[apply] snapshot already exists: {snap}")
        else:
            print(f"[apply] snapshotting {db} → {snap}")
            shutil.copy2(db, snap)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            conn.execute("BEGIN IMMEDIATE")

        # Pre-counts. json_valid guards against malformed JSON in unrelated rows.
        n_obs_v2_with_synth = conn.execute(
            f"""
            SELECT COUNT(*) FROM observation_instants_v2
            WHERE provenance_json IS NOT NULL
              AND json_valid(provenance_json) = 1
              AND json_extract(provenance_json, '$.parser_version') = ?
            """,
            (SYNTH_PARSER_VERSION,),
        ).fetchone()[0]
        n_observations_with_synth = conn.execute(
            f"""
            SELECT COUNT(*) FROM observations
            WHERE provenance_metadata IS NOT NULL
              AND provenance_metadata != ''
              AND provenance_metadata != '{{}}'
              AND json_valid(provenance_metadata) = 1
              AND json_extract(provenance_metadata, '$.synthesized_by') = ?
            """,
            (SYNTH_BACKFILL_TAG,),
        ).fetchone()[0]
        print(f"observation_instants_v2 rows with synthetic provenance: {n_obs_v2_with_synth}")
        print(f"observations rows with synthesized provenance_metadata: {n_observations_with_synth}")

        if not args.apply:
            print("\n[dry-run] no DB changes made.")
            return 0

        # 1. Strip synthetic keys from observation_instants_v2.provenance_json
        cur = conn.execute(
            f"""
            UPDATE observation_instants_v2
            SET provenance_json = json_remove(provenance_json,
                '$.payload_hash',
                '$.parser_version',
                '$.source_url',
                '$.source_file'
            )
            WHERE provenance_json IS NOT NULL
              AND json_valid(provenance_json) = 1
              AND json_extract(provenance_json, '$.parser_version') = ?
            """,
            (SYNTH_PARSER_VERSION,),
        )
        n_obs_v2_updated = cur.rowcount

        # 2. NULL out observations.provenance_metadata where it carries synth tag
        cur = conn.execute(
            f"""
            UPDATE observations
            SET provenance_metadata = NULL
            WHERE provenance_metadata IS NOT NULL
              AND provenance_metadata != ''
              AND provenance_metadata != '{{}}'
              AND json_valid(provenance_metadata) = 1
              AND json_extract(provenance_metadata, '$.synthesized_by') = ?
            """,
            (SYNTH_BACKFILL_TAG,),
        )
        n_observations_updated = cur.rowcount

        # Post-counts (must be 0)
        post_obs_v2 = conn.execute(
            f"""
            SELECT COUNT(*) FROM observation_instants_v2
            WHERE provenance_json IS NOT NULL
              AND json_valid(provenance_json) = 1
              AND json_extract(provenance_json, '$.parser_version') = ?
            """,
            (SYNTH_PARSER_VERSION,),
        ).fetchone()[0]
        post_observations = conn.execute(
            f"""
            SELECT COUNT(*) FROM observations
            WHERE provenance_metadata IS NOT NULL
              AND json_valid(provenance_metadata) = 1
              AND json_extract(provenance_metadata, '$.synthesized_by') = ?
            """,
            (SYNTH_BACKFILL_TAG,),
        ).fetchone()[0]

        if post_obs_v2 != 0 or post_observations != 0:
            sys.stderr.write(
                f"FATAL: residual synthetic rows after strip: obs_v2={post_obs_v2}, observations={post_observations}\n"
            )
            conn.rollback()
            return 3

        conn.commit()
        print(f"\n[apply] obs_v2 stripped: {n_obs_v2_updated} rows")
        print(f"[apply] observations nulled: {n_observations_updated} rows")
        print(f"[apply] post-count synthetic remaining: {post_obs_v2 + post_observations}")
        print(f"[apply] snapshot at {snap}")
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
