#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/com.openclaw.tigge.mx2t6.resumer.plist"
LOG_PATH="$ROOT/logs/tigge_mx2t6_resumer.launchd.log"
START_INTERVAL_SECONDS="${START_INTERVAL_SECONDS:-120}"

mkdir -p "$(dirname "$PLIST_PATH")" "$ROOT/logs"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.openclaw.tigge.mx2t6.resumer</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>"$ROOT/scripts/ensure_tigge_mx2t6_sessions.sh" &gt;&gt; "$LOG_PATH" 2&gt;&amp;1</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>$START_INTERVAL_SECONDS</integer>
  <key>StandardOutPath</key>
  <string>$LOG_PATH</string>
  <key>StandardErrorPath</key>
  <string>$LOG_PATH</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "installed: $PLIST_PATH"
echo "log: $LOG_PATH"
echo "interval_seconds: $START_INTERVAL_SECONDS"
