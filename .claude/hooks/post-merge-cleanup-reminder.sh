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

# Active worktrees (excluding main and temp pytest worktrees)
WORKTREES=$(git worktree list 2>/dev/null \
  | grep -v "^$(git rev-parse --show-toplevel)" \
  | grep -v "/tmp/\|/T/" \
  | awk '{print $1}' || true)

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
