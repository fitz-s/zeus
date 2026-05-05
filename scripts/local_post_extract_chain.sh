#!/usr/bin/env bash
# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_tigge_ingest_resilience/POSTDOWNLOAD_CHAIN.md
#                  + plan /Users/leofitz/.claude/plans/golden-knitting-wand.md
#                  Pairs with cloud-side scripts/cloud_tigge_autochain.sh.
#
# local_post_extract_chain.sh — runs ON operator laptop. Polls cloud
# tigge-runner for autochain+extract completion, pulls JSONs, runs ingest,
# preflight, and Platt v2 refit. Emits verdict JSON. Stops at refit
# verified; does NOT touch arm_live_mode.sh.
#
# Usage (operator):
#   tmux new-session -d -s post-extract-chain \
#     "bash $(pwd)/scripts/local_post_extract_chain.sh"

set -uo pipefail
ZEUS="$(cd "$(dirname "$0")/.." && pwd)"
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="$ZEUS/logs/post_extract_chain_${TS}.log"
VERDICT="$ZEUS/state/post_extract_pipeline_${TS}.json"
GCP=(gcloud compute ssh tigge-runner --project snappy-frame-468105-h0 --zone=europe-west4-a --command)
POLL=${POLL_SECONDS:-600}
STAGE1_MAX=${STAGE1_MAX_POLLS:-18}
STAGE2_MAX=${STAGE2_MAX_POLLS:-36}
RAW="$ZEUS/raw"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }

emit() {
    python3 -c "import json,sys; json.dump({'verdict':sys.argv[1],'ts':sys.argv[2],'log':sys.argv[3]},open(sys.argv[4],'w'),indent=2)" \
        "$1" "$TS" "$LOG" "$VERDICT"
    log "verdict=$1 → $VERDICT"
}

wait_gone() {
    local pat="$1" max="$2" n=0
    while "${GCP[@]}" "tmux ls 2>/dev/null | grep -q '^$pat'"; do
        n=$((n + 1))
        if [[ $n -gt $max ]]; then
            emit "aborted_${pat}_timeout"
            exit 2
        fi
        log "waiting for $pat (poll $n/$max)"
        sleep "$POLL"
    done
}

log "stage1: wait tigge-autochain (Phase A done + extract launched)"
wait_gone tigge-autochain "$STAGE1_MAX"

log "stage2: wait extract- (cycle-aware extract done)"
wait_gone extract- "$STAGE2_MAX"

log "stage3: scp JSONs"
for i in 1 2 3; do
    if gcloud compute scp --recurse --project snappy-frame-468105-h0 --zone=europe-west4-a \
        "tigge-runner:/data/tigge/workspace-venus/51 source data/raw/tigge_ecmwf_ens_mn2t6_localday_min" \
        "tigge-runner:/data/tigge/workspace-venus/51 source data/raw/tigge_ecmwf_ens_mx2t6_localday_max" \
        "$RAW/" >>"$LOG" 2>&1; then
        break
    fi
    log "scp attempt $i failed"
    if [[ $i -eq 3 ]]; then
        emit aborted_pull_failed
        exit 3
    fi
    sleep 60
done

cd "$ZEUS"

log "activating zeus venv"
# shellcheck disable=SC1091
source "$ZEUS/.venv/bin/activate" 2>>"$LOG" || {
    emit aborted_venv_unavailable
    exit 4
}

log "stage5: ingest mn2t6_low"
if ! python scripts/ingest_grib_to_snapshots.py --track mn2t6_low --json-root raw --no-require-files >>"$LOG" 2>&1; then
    emit aborted_ingest_errors
    exit 5
fi

log "stage5: ingest mx2t6_high"
if ! python scripts/ingest_grib_to_snapshots.py --track mx2t6_high --json-root raw --no-require-files >>"$LOG" 2>&1; then
    emit aborted_ingest_errors
    exit 5
fi

log "stage6: preflight (verify_truth_surfaces)"
if ! python scripts/verify_truth_surfaces.py --mode platt-refit-preflight --json >>"$LOG" 2>&1; then
    emit aborted_preflight_blocked
    exit 6
fi

log "stage7: refit_platt_v2 --no-dry-run --force"
if ! python scripts/refit_platt_v2.py --no-dry-run --force --temperature-metric all >>"$LOG" 2>&1; then
    emit aborted_refit_partial
    exit 7
fi

emit ready_for_operator_promotion_review
log "DONE"
