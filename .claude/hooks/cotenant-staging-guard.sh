#!/usr/bin/env bash
# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: git worktree isolation — main vs linked worktree staging semantics
#
# Blocks `git add -A`, `git add --all`, `git add .` when running in the main
# worktree. Linked worktrees have an isolated index, so broad staging is safe.
#
# Channel A only (agent PreToolUse Bash). No native git hook for `git add`.
#
# Bypass: COTENANT_GUARD_BYPASS=1

set -euo pipefail

HOOK_DIR=$(cd "$(dirname "$0")" && pwd)
HOOK_COMMON="${HOOK_DIR}/hook_common.py"

# Skip git channel (no stdin JSON)
SCRIPT_BASENAME=$(basename "$0")
if [ "$SCRIPT_BASENAME" = "pre-commit" ] || [ -n "${GIT_INDEX_FILE:-}" ]; then
    exit 0
fi

# Parse command from Claude hook JSON
INPUT=$(cat)
[ -z "$INPUT" ] && exit 0

if ! COMMAND=$(printf '%s' "$INPUT" | python3 "$HOOK_COMMON" extract-json-field command 2>/dev/null); then
    exit 0
fi
[ -z "$COMMAND" ] && exit 0

# Only block real broad git add invocations (quote-aware POSIX parsing)
if ! HOOK_COMMAND="$COMMAND" python3 "$HOOK_COMMON" git-add-is-broad 2>/dev/null; then
    exit 0
fi

# Bypass escape hatch
[ "${COTENANT_GUARD_BYPASS:-}" = "1" ] && exit 0

# Determine isolation via git dir path
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null || true)
if [[ "$GIT_DIR" == *"/worktrees/"* ]]; then
    exit 0  # linked worktree — isolated index, safe
fi

cat >&2 <<'MSG'
BLOCKED: broad staging in main worktree

`git add -A`, `--all`, and `.` are blocked here. You are in the main
worktree where a co-tenant agent's uncommitted changes could be absorbed.

Stage specific files:
  git add src/foo.py tests/test_foo.py

Or work in an isolated linked worktree:
  git worktree add ../zeus-<task> -b <branch>

To bypass once (no co-tenant active):
  COTENANT_GUARD_BYPASS=1 git add -A
MSG
exit 2
