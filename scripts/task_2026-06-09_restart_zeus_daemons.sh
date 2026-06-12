#!/bin/zsh
# Created: 2026-06-09
# Purpose: SAFETY NET — bootstrap (restart) the Zeus DB-touching launchd daemons after a
#          maintenance pause for the dead-table drop + world.db VACUUM. Run this if the
#          maintenance session is interrupted and the daemons are still down.
# delete_by: 2026-06-16
set -u
UID_N=$(id -u)
PLDIR="$HOME/Library/LaunchAgents"
SERVICES=(data-ingest forecast-live live-trading riskguard-live venue-heartbeat heartbeat-sensor)
echo "Bootstrapping Zeus daemons into gui/$UID_N ..."
for s in $SERVICES; do
  p="$PLDIR/com.zeus.$s.plist"
  if [ -e "$p" ]; then
    launchctl bootstrap gui/$UID_N "$p" 2>/dev/null && echo "  started com.zeus.$s" || echo "  (already loaded or failed) com.zeus.$s"
  else
    echo "  MISSING plist: $p"
  fi
done
echo "--- launchctl zeus services now ---"
launchctl list | grep -i zeus
