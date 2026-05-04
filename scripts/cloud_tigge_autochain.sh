#!/usr/bin/env bash
# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: PR #55 follow-up — auto-chain post-12z TIGGE backfill
#
# cloud_tigge_autochain.sh — runs ON the GCE tigge-runner instance.
#
# Watches the 10 download lanes (5 accounts × 2 metrics × cycle=12z) and,
# when ALL lanes report status="completed" in their status JSON, kicks off
# the next phase (default: extend backwards to 2023-01-01 → 2023-12-31).
#
# Usage (on tigge-runner):
#   tmux new-session -d -s tigge-autochain "bash /data/tigge/workspace-venus/51\ source\ data/scripts/cloud_tigge_autochain.sh"
#
# The script polls every POLL_SECONDS (default 600 = 10 min), tolerant of
# transient JSON read failures.  It does NOT kill in-flight lanes; it
# only starts NEW lanes once existing ones report done.

set -uo pipefail

ROOT="${TIGGE_ROOT:-/data/tigge/workspace-venus/51 source data}"
POLL_SECONDS="${POLL_SECONDS:-600}"
LOG="${ROOT}/logs/tigge_autochain_$(date -u +%Y%m%dT%H%M%SZ).log"
PHASE_B_DATE_FROM="${PHASE_B_DATE_FROM:-2023-01-01}"
PHASE_B_DATE_TO="${PHASE_B_DATE_TO:-2023-12-31}"
PHASE_B_CYCLE="${PHASE_B_CYCLE:-12}"

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
        if [[ "$status" != "completed" ]]; then
            incomplete=$((incomplete + 1))
        fi
    done
    return $incomplete
}

kick_phase_b() {
    log "Phase B start: cycle=${PHASE_B_CYCLE} from=${PHASE_B_DATE_FROM} to=${PHASE_B_DATE_TO}"
    local sessions=(
        "tigge-mx2t6-b1 ensure_tigge_mx2t6_sessions.sh"
        "tigge-mn2t6-b1 ensure_tigge_mn2t6_sessions.sh"
    )
    cd "$ROOT" || { log "ERROR: cannot cd to $ROOT"; return 1; }
    for entry in "${sessions[@]}"; do
        local session_name="${entry%% *}"
        local script_name="${entry##* }"
        if [[ -x "$ROOT/$script_name" ]]; then
            DATE_FROM="$PHASE_B_DATE_FROM" DATE_TO="$PHASE_B_DATE_TO" CYCLE="$PHASE_B_CYCLE" \
                bash "$ROOT/$script_name" >> "$LOG" 2>&1
            log "Phase B: kicked $script_name"
        else
            log "Phase B: $script_name not executable, skipping"
        fi
    done
}

log "autochain start; ROOT=$ROOT poll=${POLL_SECONDS}s"
log "Phase A is the in-flight 2024-01-01..2026-05-02 12z download"
log "Phase B will start when all 10 lanes report 'completed'"

while true; do
    if all_lanes_complete; then
        log "all 10 lanes complete; triggering Phase B"
        kick_phase_b
        log "autochain done; exiting"
        exit 0
    fi
    sleep "$POLL_SECONDS"
done
