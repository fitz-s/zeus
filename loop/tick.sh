#!/usr/bin/env bash
# Zeus 24/7 improvement loop v3 — tick wrapper (codex engine).
# Authority: docs/operations/current/plans/allday_improvement_loop_v3_codex_2026-07-09.md
# (design + sandbox facts) building on allday_improvement_loop_design_2026-07-06.md
# (methodology, permission tiers, ledger law — unchanged).
#
# WHAT: launchd (com.zeus.loop-tick) fires this hourly at :17 — the finest
#   trigger granularity. loop/INTERVAL (hours, operator-owned) is the real
#   cadence dial: interval-check gates each firing, so the operator retunes
#   1h-6h-anything by editing one number, never a plist. Sequence:
#     1. HALT existence check (existence = full stop).
#     2. interval-check: inside the INTERVAL window -> silent exit, no stamp.
#     3. Control state into THIS process's shell variables (never a file,
#        never exported, never in argv): BASE_SHA (allowlist read later from
#        the immutable git object "$BASE_SHA:loop/allowlist_auto.txt"),
#        DIRTY_AT_START (operator-dirty files are never touched/judged),
#        DB_SENTINEL_PRE (state/**.db* mtime+size — git is blind to these),
#        INTERVAL_PRE / STAMP_PRE (cadence knobs are operator-owned; a tick
#        write to either is reverted, see step 8).
#     4. run-queries: execute loop/queries/pending/*.sql read-only into
#        loop/queries/results/*.json (query escrow — the tick's only DB
#        evidence channel; see design §4) and prune orphaned results.
#     5. codex exec under an OS sandbox (Seatbelt, workspace-write):
#        writable = loop/ (cwd) + docs/ + tests/ + $TMPDIR. src/, scripts/,
#        architecture/, .git, the network, and everything outside the repo
#        are DENIED at the OS level — the sandbox is the primary boundary;
#        steps 6-8 are the independent second layer + audit trail.
#     6. enforce: any path changed during the tick that is guard-immutable
#        or outside the BASE_SHA allowlist is hard-restored + VIOLATION;
#        >20 files or >600 lines trips the circuit breaker (ESCALATION,
#        restore everything new).
#     7. Cadence anti-tamper: restore loop/INTERVAL / loop/.last_tick_epoch
#        if the tick rewrote them (both gitignored, so invisible to 6).
#     8. commit-auto: the tick has no .git write access (sandbox), so the
#        wrapper commits its legitimate allowlisted changes — explicit
#        per-path add (never -A; co-tenant work is never swept), one commit.
#     9. codex exited non-zero -> mechanical FALLBACK journal entry.
#
#   The v2 DB sentinel (state/**.db* mtime/size delta -> self-HALT) is
#   RETIRED from this wrapper: on a live host the co-tenant daemons write
#   those DBs every second, so the check would false-ESCALATE and self-HALT
#   on effectively every tick, and its threat model (the tick writing a DB)
#   is now closed at the kernel — state/ is outside the sandbox's writable
#   roots (measured: the tick cannot even OPEN a WAL DB read-only). The
#   loop_guard.py db-sentinel-* subcommands remain for tests/manual audit.
#
# WHO WRITES: launchd or the operator, manually. WHO READS: nothing reads
#   this script; it produces loop/JOURNAL.md entries and loop/logs/*.log.
# WHAT BREAKS IF THIS SILENTLY STOPS: loop/JOURNAL.md stops growing —
#   scripts/ops/loop_status.sh surfaces exactly that.
#
# SAFETY: leaf entrypoint; schedules nothing. Deploy stays operator-only.
#   The codex subprocess runs with --ignore-user-config: auth is kept, but
#   the user config.toml (danger-full-access sandbox default, approval
#   bypass, MCP servers, hooks, notify) never leaks into the tick.
#
# USAGE: loop/tick.sh
#
# ENV OVERRIDES (all optional):
#   ZEUS_LOOP_MODEL             default: gpt-5.5
#   ZEUS_LOOP_EFFORT            default: high
#   ZEUS_LOOP_TIMEOUT_SECONDS   default: 2700 (45 min wall clock = the budget)
#   ZEUS_LOOP_MAX_LOG_BYTES     default: 5000000
#   ZEUS_LOOP_CODEX_CMD         default: codex

set -euo pipefail

LOOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$LOOP_DIR/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
GUARD_PY="$REPO_ROOT/scripts/ops/loop_guard.py"
JOURNAL="$LOOP_DIR/JOURNAL.md"
PROMPT="$LOOP_DIR/prompts/l1.md"
LOCKFILE="$LOOP_DIR/.lock"

MODEL="${ZEUS_LOOP_MODEL:-gpt-5.5}"
EFFORT="${ZEUS_LOOP_EFFORT:-high}"
TIMEOUT_SECONDS="${ZEUS_LOOP_TIMEOUT_SECONDS:-2700}"
MAX_LOG_BYTES="${ZEUS_LOOP_MAX_LOG_BYTES:-5000000}"
CODEX_CMD="${ZEUS_LOOP_CODEX_CMD:-codex}"

# 1. HALT: existence = full stop.
"$PY" "$GUARD_PY" halt-check --loop-dir "$LOOP_DIR" >/dev/null || exit 0

# 2. Cadence gate: loop/INTERVAL hours since the last PROCEED, else exit.
"$PY" "$GUARD_PY" interval-check --loop-dir "$LOOP_DIR" >/dev/null || exit 0

mkdir -p "$LOOP_DIR/logs"

# 3. Control state, captured into this process's memory only.
BASE_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
DIRTY_AT_START="$("$PY" "$GUARD_PY" snapshot --repo-root "$REPO_ROOT")"
INTERVAL_PRE="$(cat "$LOOP_DIR/INTERVAL" 2>/dev/null || true)"
STAMP_PRE="$(cat "$LOOP_DIR/.last_tick_epoch" 2>/dev/null || true)"

# 4. Query escrow: run the tick-authored read-only probes, prune orphans.
"$PY" "$GUARD_PY" run-queries --repo-root "$REPO_ROOT" --loop-dir "$LOOP_DIR" || true

LOG_FILE="$LOOP_DIR/logs/tick-l1-$(date -u +%Y%m%dT%H%M%SZ).log"
TIMEOUT_BIN="$(command -v timeout || command -v gtimeout || true)"

# 5. Single-flight lock + sandboxed engine run. flock-run prints LOCK_BUSY
#    and exits 75 without running if another tick still holds the lock.
CODEX_ARGS=(
  exec
  --ignore-user-config
  --sandbox workspace-write
  -C "$LOOP_DIR"
  -c "sandbox_workspace_write.writable_roots=[\"$REPO_ROOT/docs\",\"$REPO_ROOT/tests\"]"
  -m "$MODEL"
  -c "model_reasoning_effort=\"$EFFORT\""
  --skip-git-repo-check
)
set +e
if [ -n "$TIMEOUT_BIN" ]; then
  "$TIMEOUT_BIN" "$TIMEOUT_SECONDS" \
    "$PY" "$GUARD_PY" flock-run --lock-file "$LOCKFILE" -- \
    "$CODEX_CMD" "${CODEX_ARGS[@]}" "$(cat "$PROMPT")" \
    >"$LOG_FILE" 2>&1
else
  "$PY" "$GUARD_PY" flock-run --lock-file "$LOCKFILE" -- \
    "$CODEX_CMD" "${CODEX_ARGS[@]}" "$(cat "$PROMPT")" \
    >"$LOG_FILE" 2>&1
fi
RC=$?
set -e

# rc=75 = LOCK_BUSY: quiet no-op (this interval slot is lost; self-heals).
if [ "$RC" -eq 75 ]; then
  exit 0
fi

# Log byte cap.
if [ -f "$LOG_FILE" ]; then
  size=$(stat -f '%z' "$LOG_FILE" 2>/dev/null || stat -c '%s' "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$size" -gt "$MAX_LOG_BYTES" ]; then
    tail -c "$MAX_LOG_BYTES" "$LOG_FILE" > "$LOG_FILE.trunc" && mv "$LOG_FILE.trunc" "$LOG_FILE"
  fi
fi

# 6. Allowlist enforcement (immutable git object at BASE_SHA; baseline via
#    stdin). Best-effort: a guard bug must never block a finished tick.
printf '%s' "$DIRTY_AT_START" | "$PY" "$GUARD_PY" enforce --repo-root "$REPO_ROOT" \
  --allowlist-git-ref "$BASE_SHA:loop/allowlist_auto.txt" \
  --pre-snapshot - --journal "$JOURNAL" --tier l1 || true

# 7. Cadence anti-tamper: INTERVAL and .last_tick_epoch are operator knobs,
#    gitignored (invisible to enforce) but inside the sandbox-writable loop/.
INTERVAL_POST="$(cat "$LOOP_DIR/INTERVAL" 2>/dev/null || true)"
if [ "$INTERVAL_POST" != "$INTERVAL_PRE" ]; then
  if [ -n "$INTERVAL_PRE" ]; then printf '%s' "$INTERVAL_PRE" > "$LOOP_DIR/INTERVAL"; else rm -f "$LOOP_DIR/INTERVAL"; fi
  "$PY" "$GUARD_PY" fallback-entry --journal "$JOURNAL" --tier l1 \
    --reason "VIOLATION: tick rewrote loop/INTERVAL (operator-owned cadence knob) — restored" || true
fi
STAMP_POST="$(cat "$LOOP_DIR/.last_tick_epoch" 2>/dev/null || true)"
if [ "$STAMP_POST" != "$STAMP_PRE" ]; then
  if [ -n "$STAMP_PRE" ]; then printf '%s' "$STAMP_PRE" > "$LOOP_DIR/.last_tick_epoch"; fi
fi

# 8. Commit the tick's legitimate output (the sandbox denies it .git access;
#    the wrapper is the only committer). Same allowlist source as enforce.
printf '%s' "$DIRTY_AT_START" | "$PY" "$GUARD_PY" commit-auto --repo-root "$REPO_ROOT" \
  --allowlist-git-ref "$BASE_SHA:loop/allowlist_auto.txt" \
  --pre-snapshot - --journal "$JOURNAL" --tier l1 || true

# 9. Engine crash trace.
if [ "$RC" -ne 0 ]; then
  "$PY" "$GUARD_PY" fallback-entry --journal "$JOURNAL" --tier l1 \
    --reason "codex exit=$RC (see $LOG_FILE)"
fi

exit 0
