#!/usr/bin/env bash
# pre-merge-contamination-check.sh — prints conflict-first merge guidance for
# protected branches. If MERGE_AUDIT_EVIDENCE is provided, validates the critic
# verdict file containing required fields per architecture/worktree_merge_protocol.yaml.
#
# Created: 2026-04-28
# Last reused/audited: 2026-05-01
# Authority basis: contamination remediation verdict.md §6 Stage 4 Gate B + ultrareview-25 F3/F4/F13/F17 fixes
# Plan evidence: docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md §3 P1
# Wired as PreToolUse hook for Bash tool in .claude/settings.json
# Receives JSON payload on stdin: {tool_name, tool_input{command,...}, ...}
# Exit 0 = allow/advisory; exit 2 = block invalid escalated evidence.

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
# F3 fix (ultrareview-25): bash regex handles multi-space `git  merge`,
# absolute `/usr/bin/git merge`, and the `git -C <path> merge` form that
# the prior literal `case` silently bypassed. Backslash escapes inside
# `[...]` are not required for `;`/`&`/`|` in bash ERE.
FIRST_LINE=$(printf '%s' "$COMMAND" | head -1)
if [[ "$FIRST_LINE" =~ (^|[;&|[:space:]])(/[^[:space:]]+/)?git[[:space:]]+(-[A-Za-z][[:space:]]+[^[:space:]]+[[:space:]]+)*(merge|pull|cherry-pick|rebase|am)([[:space:]]|$) ]]; then
    IS_MERGE=1
else
    IS_MERGE=0
fi

if [ "$IS_MERGE" -eq 0 ]; then
    exit 0
fi

# Check current branch is in protected set.
# F4 fix (ultrareview-25): the prior `plan-*` glob over-matched arbitrary
# branches like `plan-pretty` / `plan-prototype`. Restrict to the documented
# protected family `plan-pre<N>` (with optional `/sub-branch` so prior
# behaviour for sub-branch namespacing is preserved), plus `main` and
# `release-<non-empty>`. Empty-suffix `release-` is rejected; case-sensitive.
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [[ "$CURRENT_BRANCH" =~ ^(main|plan-pre[0-9]+(/.*)?|release-[A-Za-z0-9._/-]+)$ ]]; then
    IS_PROTECTED=1
else
    IS_PROTECTED=0
fi

if [ "$IS_PROTECTED" -eq 0 ]; then
    exit 0
fi

# Without MERGE_AUDIT_EVIDENCE, the correct flow is conflict-first:
# allow the merge-class command so the agent/operator can inspect actual
# conflicts, then escalate only broad/high-risk conflict surfaces.
if [ -z "${MERGE_AUDIT_EVIDENCE:-}" ]; then
    cat >&2 <<EOF
[pre-merge-contamination-check] ADVISORY: merge-class command on protected branch '$CURRENT_BRANCH'.

Per architecture/worktree_merge_protocol.yaml + AGENTS.md "Cross-session
merge protocol", use conflict-first handling:

To proceed:
1. Inspect conflict surface: git merge-tree / git merge --no-commit / equivalent.
2. If no conflicts, merge normally and run scoped verification.
3. If conflicts are narrow and mechanical, resolve directly or manually choose the correct side.
4. Escalate to critic evidence only for broad, cross-zone, high-risk, or semantically ambiguous conflicts.

Escalated path:
1. Save critic verdict to a file containing fields:
     critic_verdict: APPROVE
     diff_scope: <files + LOC summary>
     drift_keyword_scan: <bidirectional grep results>
2. Re-run with: MERGE_AUDIT_EVIDENCE=<path> <your git command>

To override (operator emergency only):
  MERGE_AUDIT_EVIDENCE=OVERRIDE_<reason> <your git command>
  (audit trail emitted to stderr AND appended to .claude/logs/merge-overrides.log
   for forensic inspection. Each entry: ISO8601 timestamp, branch, reason,
   cwd, command line.)
EOF
    exit 0
fi

# Check evidence file exists (skip for OVERRIDE)
case "${MERGE_AUDIT_EVIDENCE}" in
    OVERRIDE_*)
        # F17 fix (ultrareview-25): the prior implementation only echoed to
        # stderr while the docstring claimed durable logging — false claim.
        # Now appends a forensic record to .claude/logs/merge-overrides.log.
        # Failures to write the log do NOT block the override (the override
        # is the operator's escape hatch); they emit a warning to stderr.
        REPO_ROOT_FOR_LOG=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
        OVERRIDE_LOG_PATH="${REPO_ROOT_FOR_LOG}/.claude/logs/merge-overrides.log"
        OVERRIDE_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        OVERRIDE_REASON="${MERGE_AUDIT_EVIDENCE#OVERRIDE_}"
        mkdir -p "$(dirname "$OVERRIDE_LOG_PATH")" 2>/dev/null
        if printf '%s\tbranch=%s\treason=%s\tcwd=%s\tcommand=%s\n' \
                "$OVERRIDE_TS" "$CURRENT_BRANCH" "$OVERRIDE_REASON" "$PWD" \
                "$(printf '%s' "$COMMAND" | head -1 | tr '\t\n' '  ')" \
                >> "$OVERRIDE_LOG_PATH" 2>/dev/null; then
            echo "[pre-merge-contamination-check] OVERRIDE: MERGE_AUDIT_EVIDENCE=$MERGE_AUDIT_EVIDENCE; logged to ${OVERRIDE_LOG_PATH}" >&2
        else
            echo "[pre-merge-contamination-check] OVERRIDE: MERGE_AUDIT_EVIDENCE=$MERGE_AUDIT_EVIDENCE; WARNING — could not append to ${OVERRIDE_LOG_PATH} (permissions?); proceeding" >&2
        fi
        exit 0
        ;;
esac

if [ ! -f "$MERGE_AUDIT_EVIDENCE" ]; then
    echo "[pre-merge-contamination-check] BLOCKED: MERGE_AUDIT_EVIDENCE file not found: $MERGE_AUDIT_EVIDENCE" >&2
    exit 2
fi

# Check evidence file contains required fields. F13 fix (ultrareview-25):
# anchor field detection at start-of-line with NO leading whitespace, since
# the worktree_merge_protocol schema is strictly flat (top-level keys only).
# Rejects: commented-out lines (`# critic_verdict: ...`) AND YAML-nested
# spoofs (`some_parent:\n  critic_verdict: APPROVE`). Both paths fail-closed
# with a clear missing-field diagnostic.
for FIELD in "critic_verdict:" "diff_scope:" "drift_keyword_scan:"; do
    if ! grep -qE "^${FIELD}" "$MERGE_AUDIT_EVIDENCE"; then
        echo "[pre-merge-contamination-check] BLOCKED: $MERGE_AUDIT_EVIDENCE missing required field: $FIELD (must appear at column 0 — neither commented-out nor YAML-nested lines satisfy this check)" >&2
        exit 2
    fi
done

# Check critic_verdict is APPROVE or REVISE (not BLOCK).
# F13 fix (ultrareview-25): anchor critic_verdict at column 0 — no leading
# whitespace allowed — since the evidence schema is flat (top-level keys
# only). This rejects both commented spoofs (`# critic_verdict: ...`) AND
# YAML-nested spoofs (`parent:\n  critic_verdict: ...`).
VERDICT=$(grep -E '^critic_verdict:' "$MERGE_AUDIT_EVIDENCE" | head -1 | sed 's/.*critic_verdict:[[:space:]]*//;s/[[:space:]]*$//')
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
