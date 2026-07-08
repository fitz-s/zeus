#!/usr/bin/env bash
# Created: 2026-07-08
# Last reused/audited: 2026-07-08
# Authority: docs/operations/current/plans/allday_improvement_loop_design_2026-07-06.md
#   §3 (wrapper mechanism, adopted consult BLOCKER-1/HIGH items) +
#   docs/rebuild/EXECUTION_MASTER_2026-07-07.md §C/§F.
#
# WHAT: L1 hourly wrapper for the Zeus 24/7 improvement loop v2. Launched by
#   launchd (com.zeus.loop-tick) once the operator loads the plist, or run
#   manually. Sequence, every invocation:
#     1. loop/HALT existence check (existence = full stop).
#     2. Capture guard control-state into THIS SCRIPT's own shell variables
#        BEFORE invoking claude — never a file, never exported, never
#        passed as a claude CLI argument:
#          - BASE_SHA = `git rev-parse HEAD`. enforce reads the allowlist
#            from the git object at "$BASE_SHA:loop/allowlist_auto.txt"
#            after claude exits. This removes the enumerable on-disk path a
#            file snapshot exposed, but is NOT same-UID-proof: git does not
#            verify object hashes on read and .git/objects is owner-writable,
#            so a deliberate tick could overwrite the loose object. Treat as
#            a tripwire for an honest straying tick; the real boundary is OS
#            sandboxing (posture A — Claude Code auto-mode sandbox-exec).
#          - DIRTY_AT_START = `loop_guard.py snapshot` output (current
#            git-dirty/untracked paths) — the post-run check only ever
#            evaluates paths that changed DURING this tick; operator-dirty
#            files present before the tick started are never touched,
#            restored, or reported.
#          - DB_SENTINEL_PRE = `loop_guard.py db-sentinel-snapshot` output
#            (mtime+size of every state/**.db* file) — *.db is globally
#            gitignored, so git-status-based enforcement is structurally
#            blind to DB writes and needs this separate baseline.
#        This is the second hardening round: an earlier design froze these
#        into files under a `mktemp -d` directory OUTSIDE the repo tree,
#        which closed the easy repo-relative-guess escape but NOT a tick
#        whose Bash tool actively enumerated $TMPDIR (same OS user as this
#        wrapper, no process isolation — a file anywhere findable is a file
#        tamperable). Holding DIRTY_AT_START/DB_SENTINEL_PRE only in this
#        process's memory and reading the allowlist from an immutable git
#        object removes the tick-reachable-file class of escape entirely
#        for tracked-file tampering (see SAFETY's KNOWN LIMITATION for what
#        remains genuinely open — full-UID filesystem access outside this
#        repo-scoped mechanism).
#     3. Acquire a non-blocking single-flight lock (scripts/ops/loop_guard.py
#        flock-run — NOT the `flock` CLI, which is not reliably present on
#        stock macOS) and invoke `claude -p` with loop/prompts/l1.md under a
#        wall-clock timeout, with output captured to loop/logs/.
#     4a. Post-run allowlist diff check: any file that changed during this
#        tick is hard-restored to HEAD + logged VIOLATION if (i) it touches
#        one of the guard's own immutable files (loop/allowlist_auto.txt,
#        this script, loop/daily.sh, loop/prompts/**) or (ii) either side of
#        it (both old and new path, for a rename — laundering a file out of
#        scope by renaming it into an allowed dir is checked both ways) does
#        not match the allowlist loaded from BASE_SHA. DIRTY_AT_START is
#        piped in via stdin (`--pre-snapshot -`), never a file. A diff over
#        20 files or 600 lines trips the circuit breaker first and
#        hard-restores EVERYTHING new-this-tick (ESCALATION) regardless.
#        See scripts/ops/loop_guard.py::cmd_enforce.
#     4b. DB sentinel check: DB_SENTINEL_PRE piped in via stdin; any
#        state/**.db* mtime/size delta logs ESCALATION and self-halts
#        (touches loop/HALT) — a DB write cannot be hard-restored
#        byte-for-byte the way git restore undoes a tracked file, so the
#        safe response is to stop, not repair.
#     5. If claude exited non-zero, append a mechanical FALLBACK journal
#        entry so loop/JOURNAL.md never silently stops growing on a crash.
#
# WHO WRITES: launchd (via com.zeus.loop-tick.plist) or the operator running
#   this manually. WHO READS: nothing reads this script itself; it produces
#   loop/JOURNAL.md entries (read by the next tick and the operator) and
#   loop/logs/tick-l1-*.log (read by the operator / loop_status.sh on
#   failure investigation).
# WHAT BREAKS IF THIS SILENTLY STOPS RUNNING: loop/JOURNAL.md stops growing
#   entirely (not even empty-tick lines) — that is the single symptom to
#   check for. scripts/ops/loop_status.sh reports the last journal entry
#   age and last-tick-log age for exactly this reason.
#
# SAFETY:
#   - Nothing in this script schedules or launches anything else; it is a
#     leaf entrypoint. It never edits the launchd plists, never touches
#     config/**, deploy/**.
#   - Non-blocking lock: a second concurrent invocation (e.g. launchd firing
#     while a manual run is still in flight) exits 0 immediately without
#     running claude at all — never queues, never blocks.
#   - `set -euo pipefail` throughout except around the claude invocation
#     itself (its exit code is inspected, not fatal) and the post-run guard
#     calls (best-effort: `|| true`, so a bug in the guard never blocks a
#     legitimate journal append — a crash in enforce/db-sentinel-check fails
#     OPEN, a known, deliberate tradeoff: this is a best-effort wrapper, not
#     a hard sandbox). The pre-tick capture in step 2 runs WITHOUT `|| true`
#     (still under `set -e`): if any of it fails, the script aborts before
#     claude is ever invoked — fail CLOSED on setup.
#   - DIRTY_AT_START and DB_SENTINEL_PRE are plain (non-exported) shell
#     variables: they are NOT in the claude subprocess's environment (bash
#     only exports variables explicitly marked `export`), NOT passed as a
#     CLI argument to it (so not visible via `ps`), and NOT written to disk
#     anywhere. They cross into loop_guard.py via an anonymous stdin pipe
#     after claude has already exited.
#   - KNOWN LIMITATION (accepted, not fixed — operator's accept-or-scope
#     call, same stance as the DB sentinel's forged-mtime gap): this guard
#     is repo-scoped. It detects and reverts stray changes to TRACKED files
#     (via git) and detects (but cannot revert) DB touches under state/. It
#     is NOT an OS sandbox — the claude subprocess runs under the FULL USER
#     UID with no process isolation, so a deliberately rogue or
#     prompt-injected tick can write ANYWHERE else on the filesystem
#     (~/.ssh, /etc, another repo, an absolute-path DB write outside
#     state/) and nothing here would see it, let alone revert it. Closing
#     that requires real OS-level sandboxing (a sandbox-exec/Seatbelt
#     profile jailing the claude subprocess to the worktree, or a
#     restricted UID) — materially heavier than anything in this file, not
#     attempted here. For unattended enablement on a live-money host, wrap
#     the claude subprocess in sandbox-exec first (follow-up, not this
#     packet).
#
# USAGE:
#   loop/tick.sh                    # normal invocation (launchd or manual)
#
# ENV OVERRIDES (all optional):
#   ZEUS_LOOP_L1_MODEL              default: sonnet
#   ZEUS_LOOP_L1_MAX_TURNS           default: 40
#   ZEUS_LOOP_L1_TIMEOUT_SECONDS     default: 1500 (25 min)
#   ZEUS_LOOP_MAX_LOG_BYTES          default: 5000000 (5MB tick-log cap)
#   ZEUS_LOOP_CLAUDE_CMD             default: claude (override for tests /
#                                     a non-default install path)
#
# NOTE for the operator before first enable: --allowedTools below lists the
#   tool names this CLI build exposes in THIS session (Read, Edit, Write,
#   Bash, Grep, Glob, Agent). Tool ID strings can drift between claude CLI
#   versions — verify against `claude --help` / your installed CLI's tool
#   list before the first live tick if this looks stale.

set -euo pipefail

LOOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$LOOP_DIR/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
GUARD_PY="$REPO_ROOT/scripts/ops/loop_guard.py"
JOURNAL="$LOOP_DIR/JOURNAL.md"
PROMPT="$LOOP_DIR/prompts/l1.md"
LOCKFILE="$LOOP_DIR/.lock"

MODEL="${ZEUS_LOOP_L1_MODEL:-sonnet}"
MAX_TURNS="${ZEUS_LOOP_L1_MAX_TURNS:-40}"
TIMEOUT_SECONDS="${ZEUS_LOOP_L1_TIMEOUT_SECONDS:-1500}"
MAX_LOG_BYTES="${ZEUS_LOOP_MAX_LOG_BYTES:-5000000}"
CLAUDE_CMD="${ZEUS_LOOP_CLAUDE_CMD:-claude}"

# 1. HALT: existence = full stop, checked before any other work.
"$PY" "$GUARD_PY" halt-check --loop-dir "$LOOP_DIR" >/dev/null || exit 0

mkdir -p "$LOOP_DIR/logs"

# 2. Guard control-state, captured into THIS SCRIPT's shell variables only
#    (see header) — no `|| true` here: any failure aborts before claude runs.
BASE_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
DIRTY_AT_START="$("$PY" "$GUARD_PY" snapshot --repo-root "$REPO_ROOT")"
DB_SENTINEL_PRE="$("$PY" "$GUARD_PY" db-sentinel-snapshot --repo-root "$REPO_ROOT")"

LOG_FILE="$LOOP_DIR/logs/tick-l1-$(date -u +%Y%m%dT%H%M%SZ).log"
TIMEOUT_BIN="$(command -v timeout || command -v gtimeout || true)"

# 3. Single-flight lock + invocation. `flock-run` prints LOCK_BUSY and exits
#    75 without running the command if another tick already holds the lock.
#    None of BASE_SHA/DIRTY_AT_START/DB_SENTINEL_PRE are exported or passed
#    to the claude subprocess below, and none are mentioned in the prompt.
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

# rc=75 = LOCK_BUSY (another tick already running): quiet no-op, not a failure.
if [ "$RC" -eq 75 ]; then
  exit 0
fi

# Byte cap: truncate an oversized tick log rather than let one runaway tick
# fill the disk.
if [ -f "$LOG_FILE" ]; then
  size=$(stat -f '%z' "$LOG_FILE" 2>/dev/null || stat -c '%s' "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$size" -gt "$MAX_LOG_BYTES" ]; then
    tail -c "$MAX_LOG_BYTES" "$LOG_FILE" > "$LOG_FILE.trunc" && mv "$LOG_FILE.trunc" "$LOG_FILE"
  fi
fi

# 4a. Post-run allowlist diff check + hard restore of anything out of scope.
#    Best-effort: a bug in the guard must never block a legitimate journal
#    append that already happened inside the claude run. Allowlist comes
#    from the IMMUTABLE git object at BASE_SHA; DIRTY_AT_START is piped via
#    stdin, never written to disk.
printf '%s' "$DIRTY_AT_START" | "$PY" "$GUARD_PY" enforce --repo-root "$REPO_ROOT" \
  --allowlist-git-ref "$BASE_SHA:loop/allowlist_auto.txt" \
  --pre-snapshot - --journal "$JOURNAL" --tier l1 || true

# 4b. DB sentinel check — git-independent backstop for state/**.db* writes.
#    DB_SENTINEL_PRE piped via stdin. Self-halts (writes loop/HALT) on any
#    delta; best-effort like 4a.
printf '%s' "$DB_SENTINEL_PRE" | "$PY" "$GUARD_PY" db-sentinel-check --repo-root "$REPO_ROOT" \
  --pre-snapshot - --journal "$JOURNAL" --loop-dir "$LOOP_DIR" --tier l1 || true

# 5. If claude itself failed/crashed, leave a mechanical trace so the
#    journal never silently stops growing.
if [ "$RC" -ne 0 ]; then
  "$PY" "$GUARD_PY" fallback-entry --journal "$JOURNAL" --tier l1 \
    --reason "claude exit=$RC (see $LOG_FILE)"
fi

exit 0
