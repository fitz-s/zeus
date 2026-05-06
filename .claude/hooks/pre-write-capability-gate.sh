#!/usr/bin/env bash
# Created: 2026-05-06
# Last reused/audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §5 Gate 1; IMPLEMENTATION_PLAN §6 days 51-55
#
# pre-write-capability-gate.sh — Gate 1 edit-time capability hook.
# Feature flag: ZEUS_ROUTE_GATE_EDIT=off disables this gate entirely.
# sunset_date: 2026-08-04

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PYTHON="${REPO_ROOT}/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="python3"
fi

INPUT=$(cat)

cd "$REPO_ROOT"
RESULT=$(printf '%s' "$INPUT" | "$PYTHON" -m src.architecture.gate_edit_time 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "$RESULT" >&2
    exit 2
fi

if [ -n "$RESULT" ]; then
    echo "$RESULT" >&2
fi

exit 0
