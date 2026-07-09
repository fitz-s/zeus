#!/usr/bin/env bash
# Created: 2026-07-08
# Last reused/audited: 2026-07-08
# Authority: docs/operations/current/plans/allday_improvement_loop_design_2026-07-06.md
#   §2/§3 (three-file state, wrapper mechanism).
#
# WHAT: one-screen liveness report for the loop v2 machinery — last tick
#   time, last journal entry, HALT state, quarantine (VIOLATION/ESCALATION)
#   count. Read-only; does not start, stop, or touch anything.
#
# WHO WRITES: nothing (read-only). WHO READS: the operator, ad hoc, to
#   answer "is the loop alive and behaving." WHAT BREAKS IF THIS SILENTLY
#   STOPS WORKING: nothing at runtime — it is not on the loop's own
#   execution path, only an operator diagnostic.
#
# USAGE:
#   scripts/ops/loop_status.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOOP_DIR="$REPO_ROOT/loop"
JOURNAL="$LOOP_DIR/JOURNAL.md"

now_epoch() { date -u +%s; }

age_human() {
  # $1 = epoch seconds of the event; prints "<N>s"/"<N>m"/"<N>h"/"<N>d" ago.
  local then=$1 now delta
  now=$(now_epoch)
  delta=$((now - then))
  if [ "$delta" -lt 60 ]; then
    echo "${delta}s ago"
  elif [ "$delta" -lt 3600 ]; then
    echo "$((delta / 60))m ago"
  elif [ "$delta" -lt 86400 ]; then
    echo "$((delta / 3600))h ago"
  else
    echo "$((delta / 86400))d ago"
  fi
}

file_mtime_epoch() {
  stat -f '%m' "$1" 2>/dev/null || stat -c '%Y' "$1" 2>/dev/null
}

echo "== loop_status.sh ($(date -u +%Y-%m-%dT%H:%M:%SZ)) =="
echo ""

# HALT state
if [ -f "$LOOP_DIR/HALT" ]; then
  echo "HALT        : PRESENT — loop is halted (touch/rm '$LOOP_DIR/HALT')"
else
  echo "HALT        : absent — loop is not halted"
fi

# Cadence knob (v3): loop/INTERVAL hours, default 1 when absent.
if [ -f "$LOOP_DIR/INTERVAL" ]; then
  echo "INTERVAL    : $(cat "$LOOP_DIR/INTERVAL") hour(s) (operator knob)"
else
  echo "INTERVAL    : absent — default 1 hour"
fi

# launchd daemon state (best-effort; loop may not be enabled at all)
for label in com.zeus.loop-tick; do
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    state="$(launchctl print "gui/$(id -u)/$label" 2>/dev/null | awk -F'= ' '/state = /{print $2; exit}')"
    echo "daemon      : $label loaded (state=${state:-unknown})"
  else
    echo "daemon      : $label not loaded"
  fi
done

echo ""

# Last tick log per tier
for tier in l1; do
  last_log=""
  last_log_mtime=0
  while IFS= read -r -d '' candidate; do
    m="$(file_mtime_epoch "$candidate")"
    if [ "${m:-0}" -gt "$last_log_mtime" ]; then
      last_log="$candidate"
      last_log_mtime="$m"
    fi
  done < <(find "$LOOP_DIR/logs" -maxdepth 1 -name "tick-$tier-*.log" -type f -print0 2>/dev/null)
  if [ -n "$last_log" ]; then
    mtime="$(file_mtime_epoch "$last_log")"
    echo "last $tier tick log : $(basename "$last_log") ($(age_human "$mtime"))"
  else
    echo "last $tier tick log : none found"
  fi
done

echo ""

# Journal liveness
if [ -f "$JOURNAL" ]; then
  jmtime="$(file_mtime_epoch "$JOURNAL")"
  echo "JOURNAL.md  : last modified $(age_human "$jmtime")"
  echo ""
  echo "last journal entry:"
  # Print from the last '## ' heading to EOF.
  awk '/^## /{buf=""} {buf=buf $0 "\n"} END{printf "%s", buf}' "$JOURNAL" | sed 's/^/  /'
  echo ""
  violations="$(grep -c '^VIOLATION:' "$JOURNAL" 2>/dev/null || true)"
  escalations="$(grep -c '^ESCALATION:' "$JOURNAL" 2>/dev/null || true)"
  echo "quarantine counts (all-time, this journal file): VIOLATION=${violations:-0} ESCALATION=${escalations:-0}"
else
  echo "JOURNAL.md  : does not exist yet (loop has never ticked)"
fi
