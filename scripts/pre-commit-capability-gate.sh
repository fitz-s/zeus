#!/usr/bin/env bash
# Created: 2026-05-06
# Last reused/audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §5 Gate 3; IMPLEMENTATION_PLAN §6 days 61-64;
#                  phase3_h_decision.md F-7 (non-py path-match enforcement)
#
# pre-commit-capability-gate.sh — Gate 3 commit-time diff verifier.
# Called from git pre-commit hook or .claude/hooks PreToolUse Bash.
#
# Feature flag: ZEUS_ROUTE_GATE_COMMIT=off disables this gate.
# sunset_date: 2026-08-04

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PYTHON="${REPO_ROOT}/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

cd "$REPO_ROOT"
"$PYTHON" -m src.architecture.gate_commit_time
EXIT=$?
if [ $EXIT -ne 0 ]; then
    echo "[pre-commit-capability-gate] Gate 3 rejected commit. See above." >&2
    exit 1
fi
exit 0
