#!/usr/bin/env bash
# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: round2_verdict.md §4.1 #5 + AGENTS.md root §4 (Planning lock)
#
# pre-edit-architecture.sh — refuses Edit/Write to architecture/** unless
# plan-evidence is declared via env var ARCH_PLAN_EVIDENCE=<path-to-plan>
# OR the changed-file list is empty (no architecture/** touched).
#
# Wired as a PreToolUse hook for Edit/Write tools in .claude/settings.json.
# Receives a JSON payload on stdin: {tool_name, tool_input{file_path,...}, ...}
# Exit 0 = allow; exit 2 = block (Claude sees stderr).

set -euo pipefail

HOOK_DIR=$(cd "$(dirname "$0")" && pwd)
HOOK_COMMON="${HOOK_DIR}/hook_common.py"

INPUT=$(cat)

# Extract file_path (Edit/Write) or notebook_path (NotebookEdit) from tool_input
if ! FILE_PATH=$(printf '%s' "$INPUT" | python3 "$HOOK_COMMON" extract-json-field file_path 2>/tmp/pre-edit-json.err); then
    echo "[pre-edit-architecture] BLOCKED: malformed Claude hook JSON ($(cat /tmp/pre-edit-json.err 2>/dev/null || echo parse failure))" >&2
    exit 2
fi
if [ -z "$FILE_PATH" ]; then
    if ! FILE_PATH=$(printf '%s' "$INPUT" | python3 "$HOOK_COMMON" extract-json-field notebook_path 2>/tmp/pre-edit-json.err); then
        echo "[pre-edit-architecture] BLOCKED: malformed Claude hook JSON ($(cat /tmp/pre-edit-json.err 2>/dev/null || echo parse failure))" >&2
        exit 2
    fi
fi

# If no file_path, allow (other tool, not relevant to this gate)
if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# Only gate edits inside this repository's architecture/ directory. Do not
# block unrelated paths whose names happen to contain `/architecture/`.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
if REL_PATH=$(python3 "$HOOK_COMMON" repo-relative "$REPO_ROOT" "$FILE_PATH"); then
    :
else
    REL_STATUS=$?
    if [ "$REL_STATUS" -eq 10 ]; then
        exit 0
    fi
    echo "[pre-edit-architecture] BLOCKED: could not resolve path relative to repo: $FILE_PATH" >&2
    exit 2
fi

case "$REL_PATH" in
    architecture/*)
        ;;
    *)
        exit 0
        ;;
esac

# Architecture path detected. Require plan-evidence.
if [ -n "${ARCH_PLAN_EVIDENCE:-}" ] && [ -f "${ARCH_PLAN_EVIDENCE}" ]; then
    # Plan-evidence declared and exists. Allow.
    exit 0
fi

# Allow read-only verification commands (no, this is pre-edit/pre-write only)
# Block.
cat >&2 <<EOF
[pre-edit-architecture] BLOCKED: edit to architecture/** path "$FILE_PATH"
without plan-evidence.

To proceed:
  export ARCH_PLAN_EVIDENCE=docs/operations/task_<...>/<plan>.md
  (or set inline for this command)

Then re-run the edit. Per AGENTS.md root §4 Planning lock and round2_verdict.md
§4.1 #5. Bypass requires operator override (delete this hook line in
.claude/settings.json hooks block).
EOF
exit 2
