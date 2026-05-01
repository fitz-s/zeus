#!/usr/bin/env bash
# pre-merge-contamination-check.sh — prints conflict-first merge guidance for
# protected branches. If MERGE_AUDIT_EVIDENCE is provided, validates the critic
# verdict file containing required fields per architecture/worktree_merge_protocol.yaml.
#
# Created: 2026-04-28
# Last reused/audited: 2026-05-01
# Authority basis: contamination remediation verdict.md §6 Stage 4 Gate B + ultrareview-25 F3/F4/F13/F17 fixes
#                  + ultrareview25_remediation 2026-05-01 P0-2 (dual-channel)
# Plan evidence: docs/operations/task_2026-05-01_ultrareview25_remediation/PLAN.md §3 P1
#
# Dual-channel merge gate.
#
# Channel A (agent / PreToolUse Bash): wired in .claude/settings.json with
# matcher "Bash". The hook receives a JSON payload on stdin; we parse it,
# detect merge-class commands (merge/pull/cherry-pick/rebase/am), and run
# the protected-branch check.
#
# Channel B (operator / git pre-merge-commit hook): wired by symlinking this
# file to .claude/hooks/pre-merge-commit. git invokes the hook with NO stdin
# and the merge already determined; we run the protected-branch check
# unconditionally.
#
# Exit 0 = allow/advisory; exit 2 = block invalid escalated evidence.

set -euo pipefail

# ---------------------------------------------------------------------------
# Channel detection
# ---------------------------------------------------------------------------
SCRIPT_BASENAME=$(basename "$0")
if [ "$SCRIPT_BASENAME" = "pre-merge-commit" ] || [ -n "${GIT_INDEX_FILE:-}" ]; then
    CHANNEL=git
    COMMAND=""  # git already filtered; no command line to parse
else
    CHANNEL=agent
fi

if [ "$CHANNEL" = "agent" ]; then
    INPUT=$(cat)
    COMMAND=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('')
    sys.exit(0)
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
    # the prior literal `case` silently bypassed.
    FIRST_LINE=$(printf '%s' "$COMMAND" | head -1)
    if [[ "$FIRST_LINE" =~ (^|[;&|[:space:]])(/[^[:space:]]+/)?git[[:space:]]+(-[A-Za-z][[:space:]]+[^[:space:]]+[[:space:]]+)*(merge|pull|cherry-pick|rebase|am)([[:space:]]|$) ]]; then
        IS_MERGE=1
    else
        IS_MERGE=0
    fi

    if [ "$IS_MERGE" -eq 0 ]; then
        exit 0
    fi
fi
# Channel B (git): pre-merge-commit hook only fires on actual merge commits,
# so IS_MERGE is implicitly true.

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
  (audit trail emitted to stderr; capture via shell redirect or terminal log.
   TODO(ultrareview-25 F17 follow-up): durable OVERRIDE log -> drift table)
EOF
    exit 0
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

# Check evidence file contains required fields. F13 fix (ultrareview-25):
# anchor field detection at start-of-line (whitespace allowed, `#` rejected)
# so a commented-out field cannot satisfy the existence check.
for FIELD in "critic_verdict:" "diff_scope:" "drift_keyword_scan:"; do
    if ! grep -qE "^[[:space:]]*${FIELD}" "$MERGE_AUDIT_EVIDENCE"; then
        echo "[pre-merge-contamination-check] BLOCKED: $MERGE_AUDIT_EVIDENCE missing required field: $FIELD (commented-out lines do not satisfy this check)" >&2
        exit 2
    fi
done

# Check critic_verdict is APPROVE or REVISE (not BLOCK).
# F13 fix (ultrareview-25): anchor critic_verdict to start-of-line (allowing
# leading whitespace but rejecting `#` comment lines) so a commented hint
# like `# critic_verdict: APPROVE` cannot spoof the actual field value.
VERDICT=$(grep -E '^[[:space:]]*critic_verdict:' "$MERGE_AUDIT_EVIDENCE" | head -1 | sed 's/.*critic_verdict:[[:space:]]*//;s/[[:space:]]*$//')
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
