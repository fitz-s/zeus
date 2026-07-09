#!/usr/bin/env bash
# Created: 2026-07-08
# Last reused/audited: 2026-07-08
# Authority: docs/operations/current/plans/allday_improvement_loop_design_2026-07-06.md
#   §6 (enable/disable) + docs/rebuild/EXECUTION_MASTER_2026-07-07.md §C1
#   (deploy is operator-only).
#
# WHAT: preflight-checks the loop v2 machinery and PRINTS (never runs) the
#   exact launchctl bootstrap commands the operator would type to enable
#   loop/tick.sh (L1 hourly) and loop/daily.sh (L2 daily). This script never
#   calls launchctl itself — enabling the loop is an operator action, full
#   stop, per the packet's hard boundary ("nothing in this packet may
#   schedule/launch anything itself").
#
# WHO WRITES: nothing (read-only). WHO READS: the operator, once, when
#   deciding whether to enable the loop. WHAT BREAKS IF THIS SILENTLY STOPS
#   WORKING: nothing at runtime — this is a one-shot preflight+print tool,
#   not part of the loop's own execution path.
#
# PREFLIGHT CHECKS (all must pass before the printed commands are safe to run):
#   1. loop/HALT does not exist (a fresh enable should start un-halted).
#   2. loop/JOURNAL.md exists and is writable.
#   3. `claude` CLI is on PATH.
#   4. loop/allowlist_auto.txt parses (scripts/ops/loop_guard.py load path).
#   5. deploy/launchd/com.zeus.loop-tick.plist and
#      com.zeus.loop-daily.plist exist under this repo.
#
# SAFETY: read-only. Does not touch ~/Library/LaunchAgents, does not run
#   launchctl, does not modify loop/** or deploy/**.
#
# USAGE:
#   scripts/ops/loop_enable.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOOP_DIR="$REPO_ROOT/loop"
LAUNCHD_DIR="$REPO_ROOT/deploy/launchd"
PY="$REPO_ROOT/.venv/bin/python"

fail=0

echo "== loop_enable.sh preflight =="
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

# 3. claude CLI present
if command -v claude >/dev/null 2>&1; then
  echo "OK    claude CLI on PATH ($(command -v claude))"
else
  echo "FAIL  claude CLI not found on PATH — tick.sh/daily.sh cannot run without it"
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

# 5. plist templates present
for plist in com.zeus.loop-tick.plist com.zeus.loop-daily.plist; do
  if [ -f "$LAUNCHD_DIR/$plist" ]; then
    echo "OK    deploy/launchd/$plist present"
  else
    echo "FAIL  deploy/launchd/$plist missing"
    fail=1
  fi
done

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
echo "  cp '$LAUNCHD_DIR/com.zeus.loop-daily.plist' ~/Library/LaunchAgents/"
echo "  launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.zeus.loop-tick.plist"
echo "  launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.zeus.loop-daily.plist"
echo ""
echo "To disable at any time (either stops all future ticks immediately):"
echo "  touch '$LOOP_DIR/HALT'                                    # soft: leaves daemons loaded, next tick no-ops"
echo "  launchctl bootout gui/\$(id -u)/com.zeus.loop-tick        # hard: unloads the L1 daemon"
echo "  launchctl bootout gui/\$(id -u)/com.zeus.loop-daily       # hard: unloads the L2 daemon"
