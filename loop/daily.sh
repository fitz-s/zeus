#!/usr/bin/env bash
# Created: 2026-07-08
# Last reused/audited: 2026-07-08
# Authority: docs/operations/current/plans/allday_improvement_loop_design_2026-07-06.md
#   §1/§3 (L2 daily settlement-window analysis, wrapper mechanism) +
#   docs/rebuild/EXECUTION_MASTER_2026-07-07.md §C/§F.
#
# WHAT: L2 daily wrapper for the Zeus 24/7 improvement loop v2. Same
#   skeleton as loop/tick.sh (see that file's header for the full mechanism
#   walkthrough — HALT check, out-of-repo mktemp $SNAPDIR + trap cleanup,
#   pre-tick snapshots incl. allowlist copy and DB sentinel written into
#   $SNAPDIR, single-flight lock via scripts/ops/loop_guard.py flock-run,
#   post-run allowlist enforcement + DB sentinel self-halt, FALLBACK-on-
#   crash) with three differences: opus model (settlement-join evidence
#   analysis, not a quick AUTO-packet pick), a longer turn/time budget, and
#   loop/prompts/l2.md as the prompt (settlement-window six-category
#   attribution, ledger update, PREPARE diff prep, morning report — see
#   that file).
#
# WHO WRITES: launchd (com.zeus.loop-daily.plist) or the operator running
#   this manually, once daily after the settlement window. WHO READS:
#   nothing reads this script itself; it produces a `## <date> L2 morning
#   report` block in loop/JOURNAL.md (read by the operator) plus the same
#   mechanical VIOLATION/ESCALATION/FALLBACK lines as tick.sh.
# WHAT BREAKS IF THIS SILENTLY STOPS RUNNING: no daily morning report
#   appears in loop/JOURNAL.md and the `cursor:` line stops advancing —
#   scripts/ops/loop_status.sh surfaces the last journal entry age for
#   exactly this reason.
#
# SAFETY: identical to loop/tick.sh — see that file. Nothing here
#   schedules/launches anything else; leaf entrypoint only.
#
# USAGE:
#   loop/daily.sh                   # normal invocation (launchd or manual)
#
# ENV OVERRIDES (all optional):
#   ZEUS_LOOP_L2_MODEL              default: opus
#   ZEUS_LOOP_L2_MAX_TURNS           default: 120
#   ZEUS_LOOP_L2_TIMEOUT_SECONDS     default: 5400 (90 min)
#   ZEUS_LOOP_MAX_LOG_BYTES          default: 5000000 (5MB tick-log cap)
#   ZEUS_LOOP_CLAUDE_CMD             default: claude (override for tests /
#                                     a non-default install path)
#
# NOTE for the operator before first enable: --allowedTools below lists the
#   tool names this CLI build exposes in THIS session (Read, Edit, Write,
#   Bash, Grep, Glob, Agent). Verify against your installed CLI before the
#   first live run if this looks stale — same caveat as loop/tick.sh.

set -euo pipefail

LOOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$LOOP_DIR/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
GUARD_PY="$REPO_ROOT/scripts/ops/loop_guard.py"
JOURNAL="$LOOP_DIR/JOURNAL.md"
ALLOWLIST="$LOOP_DIR/allowlist_auto.txt"
PROMPT="$LOOP_DIR/prompts/l2.md"
LOCKFILE="$LOOP_DIR/.lock"

MODEL="${ZEUS_LOOP_L2_MODEL:-opus}"
MAX_TURNS="${ZEUS_LOOP_L2_MAX_TURNS:-120}"
TIMEOUT_SECONDS="${ZEUS_LOOP_L2_TIMEOUT_SECONDS:-5400}"
MAX_LOG_BYTES="${ZEUS_LOOP_MAX_LOG_BYTES:-5000000}"
CLAUDE_CMD="${ZEUS_LOOP_CLAUDE_CMD:-claude}"

# 1. HALT: existence = full stop. Same lock file as tick.sh (shared
#    single-flight — an L1 and L2 tick must never run concurrently either).
"$PY" "$GUARD_PY" halt-check --loop-dir "$LOOP_DIR" >/dev/null || exit 0

mkdir -p "$LOOP_DIR/logs"

# 2. Per-invocation snapshot dir OUTSIDE the repo tree (see loop/tick.sh
#    header for the full rationale). Trap fires on every exit path.
SNAPDIR="$(mktemp -d "${TMPDIR:-/tmp}/zeus-loop-XXXXXX")"
trap 'rm -rf "$SNAPDIR"' EXIT

# 3. Pre-tick snapshots into $SNAPDIR — fail-closed setup, no `|| true`
#    (see loop/tick.sh header for the full rationale; identical here).
"$PY" "$GUARD_PY" snapshot --repo-root "$REPO_ROOT" --out "$SNAPDIR/dirty_paths"
cp "$ALLOWLIST" "$SNAPDIR/allowlist"
"$PY" "$GUARD_PY" db-sentinel-snapshot --repo-root "$REPO_ROOT" --out "$SNAPDIR/db_sentinel"

LOG_FILE="$LOOP_DIR/logs/tick-l2-$(date -u +%Y%m%dT%H%M%SZ).log"
TIMEOUT_BIN="$(command -v timeout || command -v gtimeout || true)"

# 4. Single-flight lock + invocation. $SNAPDIR is never passed as an
#    argument or environment variable to the claude subprocess below, and
#    is never mentioned in the prompt file.
set +e
if [ -n "$TIMEOUT_BIN" ]; then
  "$TIMEOUT_BIN" "$TIMEOUT_SECONDS" \
    "$PY" "$GUARD_PY" flock-run --lock-file "$LOCKFILE" -- \
    "$CLAUDE_CMD" --model "$MODEL" -p "$(cat "$PROMPT")" --max-turns "$MAX_TURNS" \
    --allowedTools "Read,Edit,Write,Bash,Grep,Glob,Agent" \
    >"$LOG_FILE" 2>&1
else
  "$PY" "$GUARD_PY" flock-run --lock-file "$LOCKFILE" -- \
    "$CLAUDE_CMD" --model "$MODEL" -p "$(cat "$PROMPT")" --max-turns "$MAX_TURNS" \
    --allowedTools "Read,Edit,Write,Bash,Grep,Glob,Agent" \
    >"$LOG_FILE" 2>&1
fi
RC=$?
set -e

# rc=75 = LOCK_BUSY (an L1 or L2 tick already running): quiet no-op.
# $SNAPDIR cleanup happens via the trap regardless of exit point.
if [ "$RC" -eq 75 ]; then
  exit 0
fi

if [ -f "$LOG_FILE" ]; then
  size=$(stat -f '%z' "$LOG_FILE" 2>/dev/null || stat -c '%s' "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$size" -gt "$MAX_LOG_BYTES" ]; then
    tail -c "$MAX_LOG_BYTES" "$LOG_FILE" > "$LOG_FILE.trunc" && mv "$LOG_FILE.trunc" "$LOG_FILE"
  fi
fi

# 5a. Post-run allowlist diff check + hard restore of anything out of scope.
#    Uses the $SNAPDIR allowlist snapshot from step 3 — outside the repo,
#    never reachable by the tick, never the live file.
"$PY" "$GUARD_PY" enforce --repo-root "$REPO_ROOT" --allowlist-snapshot "$SNAPDIR/allowlist" \
  --pre-snapshot "$SNAPDIR/dirty_paths" --journal "$JOURNAL" --tier l2 || true

# 5b. DB sentinel check — git-independent backstop for state/**.db* writes.
#    Self-halts (writes loop/HALT) on any delta; best-effort like 5a.
"$PY" "$GUARD_PY" db-sentinel-check --repo-root "$REPO_ROOT" \
  --pre-snapshot "$SNAPDIR/db_sentinel" --journal "$JOURNAL" \
  --loop-dir "$LOOP_DIR" --tier l2 || true

# 6. If claude itself failed/crashed, leave a mechanical trace.
if [ "$RC" -ne 0 ]; then
  "$PY" "$GUARD_PY" fallback-entry --journal "$JOURNAL" --tier l2 \
    --reason "claude exit=$RC (see $LOG_FILE)"
fi

# 7. $SNAPDIR removed by the EXIT trap set in step 2.
exit 0
