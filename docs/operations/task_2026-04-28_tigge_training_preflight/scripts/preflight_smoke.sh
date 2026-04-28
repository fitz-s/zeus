#!/usr/bin/env bash
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_tigge_training_preflight/plan.md
# Purpose: reproducible Warsaw HIGH smoke — VM re-extract → local ingest → rebuild dry-run → refit dry-run.
# DOES NOT touch state/zeus-world.db. Writes only to /tmp scratch DB.
set -euo pipefail

ZEUS_ROOT="/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/quizzical-bhabha-8bdc0d"
ZEUS_PY="/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python3"
TMP_DB="/tmp/zeus-tigge-preflight.sqlite"
TMP_JSON_ROOT="/tmp/warsaw_high"
PROJECT="snappy-frame-468105-h0"
ZONE="europe-west4-a"
INSTANCE="tigge-runner"
VM_DATA="/data/tigge/workspace-venus/51 source data"

cd "$ZEUS_ROOT"

echo "=== [1/6] VM re-extract Warsaw HIGH (settlement-aligned 2026-03-09..2026-04-15) ==="
gcloud compute ssh "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --command="\
  cd '$VM_DATA' && \
  /data/tigge/venv/bin/python3 scripts/extract_tigge_mx2t6_localday_max.py \
    --track mx2t6_high --cities Warsaw \
    --date-from 2026-03-09 --date-to 2026-04-15 --overwrite \
    --summary-path /tmp/extract_warsaw_apr2026.json | tail -3"

echo "=== [2/6] Sync re-extracted JSON down ==="
rm -rf "$TMP_JSON_ROOT" && mkdir -p "$TMP_JSON_ROOT"
gcloud compute ssh "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --command="\
  cd '$VM_DATA/raw' && tar c tigge_ecmwf_ens_mx2t6_localday_max/warsaw" \
  > /tmp/warsaw_high.tar
tar xf /tmp/warsaw_high.tar -C "$TMP_JSON_ROOT"
file_count=$(find "$TMP_JSON_ROOT" -name '*.json' | wc -l | tr -d ' ')
echo "  synced $file_count files"

echo "=== [3/6] Reset /tmp DB to canonical clone (NO production write) ==="
cp /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db "$TMP_DB"
rm -f "$TMP_DB-wal" "$TMP_DB-shm"
sqlite3 "$TMP_DB" "DELETE FROM ensemble_snapshots_v2; DELETE FROM calibration_pairs_v2; DELETE FROM platt_models_v2;"

echo "=== [4/6] Stage B: ingest HIGH for target 2026-04-08..2026-04-15 ==="
"$ZEUS_PY" scripts/ingest_grib_to_snapshots.py \
  --track mx2t6_high \
  --json-root "$TMP_JSON_ROOT" \
  --db-path "$TMP_DB" \
  --cities Warsaw \
  --date-from 2026-04-08 --date-to 2026-04-15 | tail -8

high_rows=$(sqlite3 "$TMP_DB" "SELECT COUNT(*) FROM ensemble_snapshots_v2 WHERE temperature_metric='high'")
echo "  HIGH rows in /tmp DB: $high_rows (expect 64)"

echo "=== [5/6] Stage D: rebuild_calibration_pairs_v2 --dry-run ==="
"$ZEUS_PY" scripts/rebuild_calibration_pairs_v2.py \
  --dry-run --city Warsaw --db "$TMP_DB" \
  | grep -E "track|Snapshots|eligible|live-write|Total"

echo "=== [6/6] Stage E: refit_platt_v2 --dry-run ==="
"$ZEUS_PY" scripts/refit_platt_v2.py --dry-run --db "$TMP_DB" \
  | grep -E "Mode|MetricIdentity|Buckets|nothing"

echo ""
echo "=== Summary ==="
echo "  /tmp DB:           $TMP_DB"
echo "  HIGH ingested:     $high_rows rows"
echo "  Production DB:     UNTOUCHED"
echo "  Pipeline mechanism: PASS (B→D→E executed)"
echo "  Live-write status: gated on observation-provenance preflight (next packet)"
