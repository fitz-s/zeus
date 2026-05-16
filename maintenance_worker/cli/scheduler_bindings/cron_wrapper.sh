#!/bin/sh
# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/scheduler_bindings/
#
# cron_wrapper.sh — thin shell wrapper for cron-based maintenance worker invocation.
#
# Usage (in crontab):
#   0 * * * * /path/to/cron_wrapper.sh /path/to/venv /path/to/repo_root >> /path/to/mw.log 2>&1
#
# Arguments:
#   $1: path to Python virtual environment (directory containing bin/python)
#   $2: path to repository root (working directory for the tick)
#
# Environment:
#   MAINTENANCE_SCHEDULER=1 is set before invoking the CLI so that
#   scheduler_detect.detect() returns InvocationMode.SCHEDULED.
#
# Exit codes mirror the maintenance_worker CLI exit codes.
# Cron does not support launchd's KeepAlive; this wrapper is a one-shot.
#
# Zero Zeus identifiers — all paths are caller-provided.

set -eu

VENV_DIR="${1:-}"
REPO_ROOT="${2:-}"

if [ -z "$VENV_DIR" ] || [ -z "$REPO_ROOT" ]; then
    echo "[cron_wrapper] ERROR: usage: cron_wrapper.sh <venv_dir> <repo_root>" >&2
    exit 2
fi

PYTHON="${VENV_DIR}/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "[cron_wrapper] ERROR: Python not found at ${PYTHON}" >&2
    exit 2
fi

# Signal scheduler detection
export MAINTENANCE_SCHEDULER=1

cd "$REPO_ROOT"

exec "$PYTHON" -m maintenance_worker.cli.entry run
