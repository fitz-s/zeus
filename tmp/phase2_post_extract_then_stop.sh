#!/usr/bin/env bash
set -uo pipefail

PROJECT="${PROJECT:-snappy-frame-468105-h0}"
ZONE="${ZONE:-europe-west4-a}"
INSTANCE="${INSTANCE:-tigge-runner}"
REMOTE_ROOT="${REMOTE_ROOT:-/data/tigge/workspace-venus/51 source data}"
ZEUS="${ZEUS:-/Users/leofitz/.openclaw/workspace-venus/zeus}"
POLL_SECONDS="${POLL_SECONDS:-600}"
MAX_AUTOCHAIN_POLLS="${MAX_AUTOCHAIN_POLLS:-2500}"
MAX_EXTRACT_POLLS="${MAX_EXTRACT_POLLS:-720}"
PYTHON_BIN="${PYTHON_BIN:-$ZEUS/.venv/bin/python}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$ZEUS/logs/phase2_post_extract_then_stop_${TS}.log"
VERDICT="$ZEUS/state/phase2_post_extract_then_stop_${TS}.json"

mkdir -p "$ZEUS/logs" "$ZEUS/state" "$ZEUS/raw"

log() {
    printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"
}

emit() {
    local verdict="$1"
    printf '{"verdict":"%s","ts":"%s","log":"%s"}\n' "$verdict" "$TS" "$LOG" >"$VERDICT"
    log "verdict=$verdict -> $VERDICT"
}

cloud() {
    gcloud compute ssh "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --command "$1"
}

wait_remote_tmux_gone() {
    local pattern="$1"
    local max_polls="$2"
    local poll=0
    while cloud "tmux ls 2>/dev/null | grep -q '^${pattern}'" >/dev/null 2>&1; do
        poll=$((poll + 1))
        if [[ "$poll" -gt "$max_polls" ]]; then
            emit "aborted_${pattern}_timeout"
            return 1
        fi
        log "waiting for remote tmux pattern ${pattern} poll=${poll}/${max_polls}"
        sleep "$POLL_SECONDS"
    done
}

log "phase2 watcher start: project=$PROJECT zone=$ZONE instance=$INSTANCE"
log "waiting for cloud autochain to finish"
wait_remote_tmux_gone "tigge-autochain" "$MAX_AUTOCHAIN_POLLS" || exit 2

log "waiting for cloud extract sessions to finish"
wait_remote_tmux_gone "extract-" "$MAX_EXTRACT_POLLS" || exit 3

log "checking remote JSON output directories"
if ! cloud "test -d '$REMOTE_ROOT/raw/tigge_ecmwf_ens_mn2t6_localday_min' && test -d '$REMOTE_ROOT/raw/tigge_ecmwf_ens_mx2t6_localday_max' && du -sh '$REMOTE_ROOT/raw/tigge_ecmwf_ens_mn2t6_localday_min' '$REMOTE_ROOT/raw/tigge_ecmwf_ens_mx2t6_localday_max'" >>"$LOG" 2>&1; then
    emit "aborted_remote_json_missing"
    exit 4
fi

log "transferring compact JSON outputs locally"
if ! gcloud compute scp --recurse --project "$PROJECT" --zone "$ZONE" \
    "$INSTANCE:$REMOTE_ROOT/raw/tigge_ecmwf_ens_mn2t6_localday_min" \
    "$INSTANCE:$REMOTE_ROOT/raw/tigge_ecmwf_ens_mx2t6_localday_max" \
    "$ZEUS/raw/" >>"$LOG" 2>&1; then
    emit "aborted_json_transfer_failed"
    exit 5
fi

log "JSON transfer complete; stopping VM to halt compute billing"
if ! gcloud compute instances stop "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --quiet >>"$LOG" 2>&1; then
    emit "aborted_vm_stop_failed_after_transfer"
    exit 6
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="/usr/local/bin/python3"
fi

cd "$ZEUS" || exit 7

log "local ingest mn2t6_low"
if ! "$PYTHON_BIN" scripts/ingest_grib_to_snapshots.py --track mn2t6_low --json-root raw --no-require-files >>"$LOG" 2>&1; then
    emit "aborted_local_ingest_mn2t6_low"
    exit 8
fi

log "local ingest mx2t6_high"
if ! "$PYTHON_BIN" scripts/ingest_grib_to_snapshots.py --track mx2t6_high --json-root raw --no-require-files >>"$LOG" 2>&1; then
    emit "aborted_local_ingest_mx2t6_high"
    exit 9
fi

log "local preflight"
if ! "$PYTHON_BIN" scripts/verify_truth_surfaces.py --mode platt-refit-preflight --json >>"$LOG" 2>&1; then
    emit "aborted_local_preflight"
    exit 10
fi

log "local refit_platt_v2"
if ! "$PYTHON_BIN" scripts/refit_platt_v2.py --no-dry-run --force --temperature-metric all >>"$LOG" 2>&1; then
    emit "aborted_local_refit"
    exit 11
fi

emit "ready_for_operator_promotion_review"
log "DONE"