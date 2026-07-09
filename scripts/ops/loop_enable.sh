#!/usr/bin/env bash
# Zeus improvement loop v3 — enable preflight (print-only).
# Authority: docs/operations/current/plans/allday_improvement_loop_v3_codex_2026-07-09.md §7.
#
# WHAT: preflight-checks the loop v3 machinery and PRINTS (never runs) the
#   exact launchctl bootstrap commands the operator would type to enable
#   loop/tick.sh. Enabling the loop is an operator action, full stop.
#
# WHO WRITES: nothing (read-only). WHO READS: the operator, when deciding
#   whether to enable the loop. WHAT BREAKS IF THIS SILENTLY STOPS WORKING:
#   nothing at runtime — one-shot preflight+print, not on the loop's path.
#
# PREFLIGHT CHECKS:
#   1. loop/HALT does not exist.
#   2. loop/JOURNAL.md exists and is writable.
#   3. `codex` CLI on PATH (the v3 engine).
#   4. loop/allowlist_auto.txt parses.
#   5. deploy/launchd/com.zeus.loop-tick.plist exists.
#
# SAFETY: read-only. Does not touch ~/Library/LaunchAgents, does not run
#   launchctl, does not modify loop/** or deploy/**.
#
# USAGE: scripts/ops/loop_enable.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOOP_DIR="$REPO_ROOT/loop"
LAUNCHD_DIR="$REPO_ROOT/deploy/launchd"
PY="$REPO_ROOT/.venv/bin/python"

fail=0

echo "== loop_enable.sh preflight (v3) =="
echo ""

# 1. HALT absent
if [ -f "$LOOP_DIR/HALT" ]; then
  echo "FAIL  loop/HALT exists — loop would start halted. Remove it first if that's not intended: rm '$LOOP_DIR/HALT'"
  fail=1
else
  echo "OK    loop/HALT absent"
fi

# 2. journal writable
if [ -f "$LOOP_DIR/JOURNAL.md" ] && [ -w "$LOOP_DIR/JOURNAL.md" ]; then
  echo "OK    loop/JOURNAL.md exists and is writable"
elif [ -w "$LOOP_DIR" ]; then
  echo "OK    loop/JOURNAL.md missing but loop/ is writable (will be created on first tick)"
else
  echo "FAIL  loop/JOURNAL.md missing/unwritable and loop/ is not writable"
  fail=1
fi

# 3. codex CLI present (v3 engine)
if command -v codex >/dev/null 2>&1; then
  echo "OK    codex CLI on PATH ($(command -v codex))"
else
  echo "FAIL  codex CLI not found on PATH — tick.sh cannot run without it"
  fail=1
fi

# 4. allowlist parses
if [ -f "$LOOP_DIR/allowlist_auto.txt" ]; then
  if "$PY" -c "
import sys
sys.path.insert(0, '$REPO_ROOT')
from scripts.ops import loop_guard
patterns = loop_guard.load_allowlist('$LOOP_DIR/allowlist_auto.txt')
assert patterns, 'allowlist parsed to zero patterns'
print(f'{len(patterns)} pattern(s)')
" >/tmp/loop_enable_allowlist_check.$$ 2>&1; then
    echo "OK    loop/allowlist_auto.txt parses ($(cat /tmp/loop_enable_allowlist_check.$$))"
  else
    echo "FAIL  loop/allowlist_auto.txt failed to parse:"
    sed 's/^/      /' /tmp/loop_enable_allowlist_check.$$
    fail=1
  fi
  rm -f /tmp/loop_enable_allowlist_check.$$
else
  echo "FAIL  loop/allowlist_auto.txt missing"
  fail=1
fi

# 5. plist template present
if [ -f "$LAUNCHD_DIR/com.zeus.loop-tick.plist" ]; then
  echo "OK    deploy/launchd/com.zeus.loop-tick.plist present"
else
  echo "FAIL  deploy/launchd/com.zeus.loop-tick.plist missing"
  fail=1
fi

# Cadence info (not a gate — missing INTERVAL means default 1h)
if [ -f "$LOOP_DIR/INTERVAL" ]; then
  echo "INFO  loop/INTERVAL = $(cat "$LOOP_DIR/INTERVAL") hour(s)"
else
  echo "INFO  loop/INTERVAL absent — default cadence 1 hour (echo N > loop/INTERVAL to change)"
fi

echo ""
if [ "$fail" -ne 0 ]; then
  echo "PREFLIGHT FAILED — fix the FAIL line(s) above before enabling. No commands printed."
  exit 1
fi

echo "PREFLIGHT PASSED."
echo ""
echo "This script does NOT run these commands — copy/paste them yourself once ready:"
echo ""
echo "  cp '$LAUNCHD_DIR/com.zeus.loop-tick.plist' ~/Library/LaunchAgents/"
echo "  launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.zeus.loop-tick.plist"
echo ""
echo "Cadence (anytime, no plist edit): echo <hours> > '$LOOP_DIR/INTERVAL'   # e.g. 1..6"
echo ""
echo "To disable at any time:"
echo "  touch '$LOOP_DIR/HALT'                                   # soft: daemon stays loaded, next tick no-ops"
echo "  launchctl bootout gui/\$(id -u)/com.zeus.loop-tick       # hard: unloads the daemon"
