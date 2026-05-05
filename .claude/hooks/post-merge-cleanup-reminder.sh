#!/usr/bin/env bash
# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: AGENTS.md §2 Post-merge cleanup
#
# PostToolUse Bash hook. Fires after a successful `gh pr merge` or `git merge`.
# Prints a soft cleanup checklist — non-blocking (exit 0 always).
# Agent decides whether and how to act on each item.

set -euo pipefail

HOOK_DIR=$(cd "$(dirname "$0")" && pwd)
HOOK_COMMON="${HOOK_DIR}/hook_common.py"

# Only agent channel
INPUT=$(cat 2>/dev/null || true)
[ -z "$INPUT" ] && exit 0

COMMAND=$(printf '%s' "$INPUT" | python3 "$HOOK_COMMON" extract-json-field command 2>/dev/null || true)
[ -z "$COMMAND" ] && exit 0

# Fire only on `gh pr merge` (PR-merge = task-complete boundary).
# Local `git merge` / `git pull` are sync operations, not task-end → noise.
# Word-boundary required so `gh pr mergeforce` etc. don't match.
if ! (echo "$COMMAND" | grep -qE '^\s*gh\s+pr\s+merge([[:space:]]|$)'); then
    exit 0
fi

# Check exit code of the completed tool — only remind on success (exit_code == 0 or absent)
EXIT_CODE=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
# PostToolUse payload has tool_response.exit_code
r = d.get('tool_response') or {}
print(r.get('exit_code', 0))
" 2>/dev/null || echo "0")

[ "$EXIT_CODE" != "0" ] && exit 0

# Active worktrees (excluding main and temp pytest worktrees).
# Use --porcelain so paths containing spaces survive intact, and -Fx exact-match
# exclusion of the MAIN worktree (the bare/primary one listed first by git).
# We must exclude the main worktree, not the current worktree (`--show-toplevel`
# returns the one the merge ran from, which IS the linked worktree the cleanup
# reminder should be listing).
# `git worktree list --porcelain` always lists the main worktree first; we
# extract it with sed+head so paths with spaces survive intact.
MAIN_WORKTREE=$(git worktree list --porcelain 2>/dev/null \
  | sed -n 's/^worktree //p' \
  | head -n1 \
  || true)
WORKTREES=$(git worktree list --porcelain 2>/dev/null \
  | sed -n 's/^worktree //p' \
  | grep -vFx -- "${MAIN_WORKTREE:-/dev/null/never-matches}" \
  | grep -v "/tmp/\|/T/" \
  || true)

echo ""
echo "── Post-merge cleanup (soft) ──────────────────────────────────"
if [ -n "$WORKTREES" ]; then
    while IFS= read -r wt; do
        echo "  worktree: $wt  →  git worktree remove <path>"
    done <<< "$WORKTREES"
else
    echo "  worktrees: only main ✓"
fi
echo "  ops packet: delete by default (git = backup); git mv to docs/archives/"
echo "    only when packet holds evidence git log can't summarize."
echo "  context: /compact long sessions; rm .omc/state/agent-replay-*.jsonl"
echo "    when no recovery active."
echo "───────────────────────────────────────────────────────────────"
echo ""

exit 0
