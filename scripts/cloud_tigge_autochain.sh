#!/usr/bin/env bash
# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: PR #55 follow-up — operator directive 2026-05-04 (no 2023
#                  backfill; chain extract on cloud after Phase A completes).
#
# cloud_tigge_autochain.sh — runs ON the GCE tigge-runner instance.
#
# Watches the 10 download lanes (5 accounts × 2 metrics × cycle=12z) and,
# when ALL lanes report status="completed" in their status JSON, kicks off
# cycle-aware extract (mn2t6 + mx2t6) directly on cloud — no rsync, no
# Phase B 2023 download.  Reuses the documented extract entrypoints from
# POSTDOWNLOAD_CHAIN.md §2.
#
# Usage (on tigge-runner):
#   tmux new-session -d -s tigge-autochain "bash /data/tigge/workspace-venus/51\ source\ data/scripts/cloud_tigge_autochain.sh"
#
# The script polls every POLL_SECONDS (default 600 = 10 min), tolerant of
# transient JSON read failures.  It does NOT kill in-flight lanes.

set -uo pipefail

ROOT="${TIGGE_ROOT:-/data/tigge/workspace-venus/51 source data}"
POLL_SECONDS="${POLL_SECONDS:-600}"
LOG="${ROOT}/logs/tigge_autochain_$(date -u +%Y%m%dT%H%M%SZ).log"
PYTHON_BIN="${PYTHON_BIN:-/data/tigge/venv/bin/python}"
# Phase 1 extract = 90-day live-window backfill (2026-02-01..2026-05-02)
EXTRACT_DATE_FROM="${EXTRACT_DATE_FROM:-2026-02-01}"
EXTRACT_DATE_TO="${EXTRACT_DATE_TO:-2026-05-02}"
EXTRACT_CYCLE="${EXTRACT_CYCLE:-12}"
# Phase 2 backfill = older 760 days, fired in parallel with extract when phase 1 completes.
# Set PHASE2_ENABLED=0 to skip the older backfill chain.
PHASE2_ENABLED="${PHASE2_ENABLED:-1}"
PHASE2_DATE_FROM="${PHASE2_DATE_FROM:-2024-01-01}"
PHASE2_DATE_TO="${PHASE2_DATE_TO:-2026-01-31}"

mkdir -p "$(dirname "$LOG")"

log() {
    printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"
}

all_lanes_complete() {
    local incomplete=0
    for f in "$ROOT"/tmp/tigge_*_download_status_a*_cycle12z.json; do
        if [[ ! -f "$f" ]]; then
            incomplete=$((incomplete + 1))
            continue
        fi
        local status
        status=$(python3 -c "
import json, sys
try:
    with open(sys.argv[1]) as fp:
        print(json.load(fp).get('status', 'unknown'))
except Exception as exc:
    print('error', file=sys.stderr)
" "$f" 2>/dev/null || echo "unknown")
        # 2026-05-05 fix: lane writers emit status="complete" (no -d).
        # Accept both forms; treat anything else as incomplete. Pre-fix, this
        # check rejected "complete" → autochain stalled 28h despite all 10
        # lanes done. See post-mortem in PR #61.
        if [[ "$status" != "complete" && "$status" != "completed" ]]; then
            incomplete=$((incomplete + 1))
        fi
    done
    return $incomplete
}

kick_extract() {
    log "extract start: cycle=${EXTRACT_CYCLE} from=${EXTRACT_DATE_FROM} to=${EXTRACT_DATE_TO}"
    cd "$ROOT" || { log "ERROR: cannot cd to $ROOT"; return 1; }
    local extract_args=(
        --cycle "$EXTRACT_CYCLE"
        --date-from "$EXTRACT_DATE_FROM"
        --date-to "$EXTRACT_DATE_TO"
        --raw-root "$ROOT/raw"
        --output-root "$ROOT/raw"
    )
    local scripts=(
        "scripts/extract_tigge_mn2t6_localday_min.py"
        "scripts/extract_tigge_mx2t6_localday_max.py"
    )
    for script in "${scripts[@]}"; do
        if [[ -f "$ROOT/$script" ]]; then
            local sess
            sess="extract-$(basename "$script" .py)"
            # Build a safely-quoted arg string so paths with spaces (e.g. the
            # default ROOT containing "51 source data") survive tmux expansion.
            local quoted_args
            quoted_args=$(printf '%q ' "${extract_args[@]}")
            tmux new-session -d -s "$sess" \
                "cd $(printf '%q' "$ROOT") && $(printf '%q' "$PYTHON_BIN") $(printf '%q' "$script") ${quoted_args}2>&1 | tee -a $(printf '%q' "$LOG")"
            log "extract: launched $sess"
        else
            log "extract: $script not found, skipping"
        fi
    done
}

kick_phase2() {
    [[ "$PHASE2_ENABLED" != "1" ]] && { log "phase2 disabled, skipping"; return 0; }
    log "phase2 start: full backfill ${PHASE2_DATE_FROM}..${PHASE2_DATE_TO} cycle=${EXTRACT_CYCLE}"
    cd "$ROOT" || { log "ERROR: cannot cd to $ROOT"; return 1; }
    local ensure_scripts=(
        "scripts/ensure_tigge_mn2t6_sessions.sh"
        "scripts/ensure_tigge_mx2t6_sessions.sh"
    )
    for s in "${ensure_scripts[@]}"; do
        if [[ -f "$ROOT/$s" ]]; then
            DATE_FROM="$PHASE2_DATE_FROM" DATE_TO="$PHASE2_DATE_TO" CYCLE="$EXTRACT_CYCLE" \
                bash "$ROOT/$s" >>"$LOG" 2>&1
            log "phase2: kicked $s"
        else
            log "phase2: $s not found, skipping"
        fi
    done
}

log "autochain start; ROOT=$ROOT poll=${POLL_SECONDS}s"
log "Phase 1 in flight: ${EXTRACT_DATE_FROM}..${EXTRACT_DATE_TO} cycle=${EXTRACT_CYCLE} (90-day live window)"
log "On phase1 complete: trigger cloud extract + phase2 backfill (${PHASE2_DATE_FROM}..${PHASE2_DATE_TO}) in parallel"

while true; do
    if all_lanes_complete; then
        log "all phase1 lanes complete; triggering cloud extract + phase2"
        kick_extract
        kick_phase2
        log "autochain done; exiting (extract + phase2 run in their own tmux sessions)"
        exit 0
    fi
    sleep "$POLL_SECONDS"
done
