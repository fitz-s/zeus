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
#     2. mktemp -d a per-invocation snapshot dir OUTSIDE the repo tree
#        entirely ($SNAPDIR, e.g. /tmp/zeus-loop-XXXXXX) and trap it for
#        cleanup on exit. This is the load-bearing fix for a real escape: an
#        earlier version froze the pre-tick allowlist/DB-sentinel snapshots
#        INSIDE loop/ (gitignored but still repo-relative and readable/
#        writable by the tick's own Bash tool) — a tick could `echo
#        'src/**' >> loop/.pre_tick_allowlist.l1` mid-run and enforce would
#        trust the tampered copy with zero trace. $SNAPDIR's exact path is
#        random per invocation, lives outside the repo working tree, is
#        never passed to the claude subprocess via argv or environment, and
#        is never mentioned in loop/prompts/*.md — nothing the tick reads as
#        part of its normal orientation discloses it, and no repo-relative
#        glob (loop/**, docs/**, anything) can reach it. This defeats a tick
#        guessing a plausible repo-relative path (the realistic case — the
#        old naming convention was documented in comments the tick would
#        naturally read); it is NOT a hard sandbox boundary — a tick whose
#        Bash tool actively enumerates $TMPDIR still finds it, since the
#        claude subprocess runs as the same OS user with no process
#        isolation (see SAFETY's KNOWN LIMITATION note below).
#        GUARD_IMMUTABLE_PATTERNS on loop/allowlist_auto.txt/tick.sh/
#        daily.sh/prompts/** (see scripts/ops/loop_guard.py) stays as
#        belt-and-suspenders; $SNAPDIR is what actually closes the
#        repo-relative-guess escape.
#     3. Snapshot BEFORE invoking claude, all written into $SNAPDIR: (a) the
#        current git-dirty/untracked path set, so the post-run check only
#        ever evaluates paths that changed DURING this tick — operator-dirty
#        files present before the tick started are never touched, restored,
#        or reported; (b) a copy of loop/allowlist_auto.txt; (c) mtime+size
#        of every state/**.db* file (see step 5b — *.db is globally
#        gitignored, so git-status-based enforcement below is structurally
#        blind to DB writes and needs this separate check).
#     4. Acquire a non-blocking single-flight lock (scripts/ops/loop_guard.py
#        flock-run — NOT the `flock` CLI, which is not reliably present on
#        stock macOS) and invoke `claude -p` with loop/prompts/l1.md under a
#        wall-clock timeout, with output captured to loop/logs/.
#     5a. Post-run allowlist diff check: any file that changed during this
#        tick is hard-restored to HEAD + logged VIOLATION if (i) it touches
#        one of the guard's own immutable files (loop/allowlist_auto.txt,
#        this script, loop/daily.sh, loop/prompts/**) or (ii) either side of
#        it (both old and new path, for a rename — laundering a file out of
#        scope by renaming it into an allowed dir is checked both ways) does
#        not match the $SNAPDIR allowlist snapshot from step 3b. A diff over
#        20 files or 600 lines trips the circuit breaker first and
#        hard-restores EVERYTHING new-this-tick (ESCALATION) regardless.
#        See scripts/ops/loop_guard.py::cmd_enforce.
#     5b. DB sentinel check: any state/**.db* mtime/size delta vs step 3c
#        logs ESCALATION and self-halts (touches loop/HALT) — a DB write
#        cannot be hard-restored byte-for-byte the way git restore undoes a
#        tracked file, so the safe response is to stop, not repair.
#     6. If claude exited non-zero, append a mechanical FALLBACK journal
#        entry so loop/JOURNAL.md never silently stops growing on a crash.
#     7. $SNAPDIR is removed via the EXIT trap set in step 2 — fires on
#        every exit path (HALT no-op, LOCK_BUSY no-op, normal completion),
#        so nothing is left behind in /tmp across invocations.
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
#     a hard sandbox). The pre-tick snapshot calls in step 3 run WITHOUT
#     `|| true` (still under `set -e`): if any of them fail, the script
#     aborts before claude is ever invoked — fail CLOSED on setup.
#   - KNOWN LIMITATION (accepted, not fixed, same class as the DB sentinel's
#     forged-mtime gap): $SNAPDIR defeats a tick that guesses a plausible
#     REPO-RELATIVE path for the trusted snapshot (the realistic threat —
#     the old in-repo naming convention was documented in comments the tick
#     naturally reads). It does NOT defeat a tick whose Bash tool actively
#     enumerates $TMPDIR (e.g. `for d in "$TMPDIR"/zeus-loop-*`) — the
#     claude subprocess runs as the SAME OS user as this wrapper, so there
#     is no permission boundary, only path-secrecy, and mktemp's path is
#     discoverable by anyone willing to search $TMPDIR. Closing that
#     requires real OS-level process isolation (a restricted UID or a
#     Seatbelt/sandbox-exec profile around the claude subprocess), which is
#     a materially different and heavier fix than "move the file" — out of
#     scope here per the same "wrapper, not a hard sandbox" design stance
#     applied throughout, not attempted.
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
ALLOWLIST="$LOOP_DIR/allowlist_auto.txt"
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

# 2. Per-invocation snapshot dir OUTSIDE the repo tree (see header). Trap
#    fires on every exit path so nothing is left behind in /tmp.
SNAPDIR="$(mktemp -d "${TMPDIR:-/tmp}/zeus-loop-XXXXXX")"
trap 'rm -rf "$SNAPDIR"' EXIT

# 3. Pre-tick snapshots into $SNAPDIR — all three run under `set -e` (no
#    `|| true`): if any fails, the script aborts before claude is ever
#    invoked. This is deliberate fail-closed setup, distinct from the
#    best-effort post-run guard calls below.
# 3a. git-dirty snapshot (operator-dirty files are frozen out of scope).
"$PY" "$GUARD_PY" snapshot --repo-root "$REPO_ROOT" --out "$SNAPDIR/dirty_paths"
# 3b. Allowlist copy, frozen outside the repo — enforce reads ONLY this
#     copy, never the live loop/allowlist_auto.txt and never a repo-relative
#     path the tick could reach (self-widening fix; see loop_guard.py
#     cmd_enforce --allowlist-snapshot help).
cp "$ALLOWLIST" "$SNAPDIR/allowlist"
# 3c. DB mtime/size sentinel (git-independent — *.db is gitignored).
"$PY" "$GUARD_PY" db-sentinel-snapshot --repo-root "$REPO_ROOT" --out "$SNAPDIR/db_sentinel"

LOG_FILE="$LOOP_DIR/logs/tick-l1-$(date -u +%Y%m%dT%H%M%SZ).log"
TIMEOUT_BIN="$(command -v timeout || command -v gtimeout || true)"

# 4. Single-flight lock + invocation. `flock-run` prints LOCK_BUSY and exits
#    75 without running the command if another tick already holds the lock.
#    $SNAPDIR is never passed as an argument or environment variable to the
#    claude subprocess below, and is never mentioned in the prompt file.
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

# rc=75 = LOCK_BUSY (another tick already running): quiet no-op, not a
# failure. $SNAPDIR cleanup happens via the trap regardless of exit point.
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

# 5a. Post-run allowlist diff check + hard restore of anything out of scope.
#    Best-effort: a bug in the guard must never block a legitimate journal
#    append that already happened inside the claude run. Uses the $SNAPDIR
#    allowlist snapshot from step 3b — outside the repo, never reachable by
#    the tick, never the (possibly tampered) live file.
"$PY" "$GUARD_PY" enforce --repo-root "$REPO_ROOT" --allowlist-snapshot "$SNAPDIR/allowlist" \
  --pre-snapshot "$SNAPDIR/dirty_paths" --journal "$JOURNAL" --tier l1 || true

# 5b. DB sentinel check — git-independent backstop for state/**.db* writes.
#    Self-halts (writes loop/HALT) on any delta; best-effort like 5a.
"$PY" "$GUARD_PY" db-sentinel-check --repo-root "$REPO_ROOT" \
  --pre-snapshot "$SNAPDIR/db_sentinel" --journal "$JOURNAL" \
  --loop-dir "$LOOP_DIR" --tier l1 || true

# 6. If claude itself failed/crashed, leave a mechanical trace so the
#    journal never silently stops growing.
if [ "$RC" -ne 0 ]; then
  "$PY" "$GUARD_PY" fallback-entry --journal "$JOURNAL" --tier l1 \
    --reason "claude exit=$RC (see $LOG_FILE)"
fi

# 7. $SNAPDIR removed by the EXIT trap set in step 2.
exit 0
