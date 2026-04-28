#!/usr/bin/env bash
# pre-merge-contamination-check.sh — refuses git merge/pull/cherry-pick/rebase
# on protected branches without MERGE_AUDIT_EVIDENCE env var pointing to a
# critic verdict file containing required fields per architecture/worktree_merge_protocol.yaml
#
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: contamination remediation verdict.md §6 Stage 4 Gate B
# Plan evidence: docs/operations/task_2026-04-28_contamination_remediation/STAGE4_PROCESS_GATES_AE_PLAN.md §2
# Wired as PreToolUse hook for Bash tool in .claude/settings.json
# Receives JSON payload on stdin: {tool_name, tool_input{command,...}, ...}
# Exit 0 = allow; exit 2 = block (Claude sees stderr).

set -euo pipefail

INPUT=$(cat)

# Extract command from tool_input
COMMAND=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
ti = d.get('tool_input', {}) or {}
print(ti.get('command') or '')
" 2>/dev/null || echo "")

if [ -z "$COMMAND" ]; then
    exit 0
fi

# Detect merge-class commands by extracting the FIRST `git <subcmd>` token
# from the FIRST LINE only. This avoids false-positives where a multi-line
# heredoc (e.g. commit message body) mentions merge/pull/etc as text.
# Trade-off: chained `git status && git merge X` on a single line where the
# FIRST git command is non-merge will NOT block (rare edge case).
FIRST_GIT=$(printf '%s' "$COMMAND" | head -1 | grep -oE 'git[[:space:]]+[a-z-]+' | head -1)
case "$FIRST_GIT" in
    "git merge"|"git pull"|"git cherry-pick"|"git rebase"|"git am")
        IS_MERGE=1
        ;;
    *)
        IS_MERGE=0
        ;;
esac

if [ "$IS_MERGE" -eq 0 ]; then
    exit 0
fi

# Check current branch is in protected set
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
case "$CURRENT_BRANCH" in
    main|plan-pre5|plan-*|release-*)
        IS_PROTECTED=1
        ;;
    *)
        IS_PROTECTED=0
        ;;
esac

if [ "$IS_PROTECTED" -eq 0 ]; then
    exit 0
fi

# Check MERGE_AUDIT_EVIDENCE env var
if [ -z "${MERGE_AUDIT_EVIDENCE:-}" ]; then
    cat >&2 <<EOF
[pre-merge-contamination-check] BLOCKED: merge command on protected branch '$CURRENT_BRANCH'
requires MERGE_AUDIT_EVIDENCE env var.

Per architecture/worktree_merge_protocol.yaml + AGENTS.md "Cross-session
merge protocol", merging from another session/worktree into a protected
Zeus branch requires a critic-opus dispatch verdict on the merging diff.

To proceed:
1. Identify diff: git diff $CURRENT_BRANCH...<merging-branch>
2. Dispatch critic-opus per .agents/skills/zeus-ai-handoff/SKILL.md §8.8
3. Save critic verdict to a file containing fields:
     critic_verdict: APPROVE
     diff_scope: <files + LOC summary>
     drift_keyword_scan: <bidirectional grep results>
4. Re-run with: MERGE_AUDIT_EVIDENCE=<path> <your git command>

To override (operator emergency only):
  MERGE_AUDIT_EVIDENCE=OVERRIDE_<reason> <your git command>
  (logged to docs/operations/current_state.md drift table)
EOF
    exit 2
fi

# Check evidence file exists (skip for OVERRIDE)
case "${MERGE_AUDIT_EVIDENCE}" in
    OVERRIDE_*)
        echo "[pre-merge-contamination-check] OVERRIDE: MERGE_AUDIT_EVIDENCE=$MERGE_AUDIT_EVIDENCE; logged" >&2
        exit 0
        ;;
esac

if [ ! -f "$MERGE_AUDIT_EVIDENCE" ]; then
    echo "[pre-merge-contamination-check] BLOCKED: MERGE_AUDIT_EVIDENCE file not found: $MERGE_AUDIT_EVIDENCE" >&2
    exit 2
fi

# Check evidence file contains required fields
for FIELD in "critic_verdict:" "diff_scope:" "drift_keyword_scan:"; do
    if ! grep -q "$FIELD" "$MERGE_AUDIT_EVIDENCE"; then
        echo "[pre-merge-contamination-check] BLOCKED: $MERGE_AUDIT_EVIDENCE missing required field: $FIELD" >&2
        exit 2
    fi
done

# Check critic_verdict is APPROVE or REVISE (not BLOCK)
VERDICT=$(grep "critic_verdict:" "$MERGE_AUDIT_EVIDENCE" | head -1 | sed 's/.*critic_verdict:[[:space:]]*//;s/[[:space:]]*$//')
case "$VERDICT" in
    APPROVE|REVISE)
        echo "[pre-merge-contamination-check] PASS: $MERGE_AUDIT_EVIDENCE verdict=$VERDICT" >&2
        exit 0
        ;;
    BLOCK|*)
        echo "[pre-merge-contamination-check] BLOCKED: $MERGE_AUDIT_EVIDENCE critic_verdict=$VERDICT; address defects + re-dispatch" >&2
        exit 2
        ;;
esac
