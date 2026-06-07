#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/com.openclaw.tigge.mn2t6.resumer.plist"
LOG_PATH="$ROOT/logs/tigge_mn2t6_resumer.launchd.log"

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"

cat >"$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.openclaw.tigge.mn2t6.resumer</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>"$ROOT/scripts/ensure_tigge_mn2t6_sessions.sh" &gt;&gt; "$LOG_PATH" 2&gt;&amp;1</string>
  </array>
  <key>StartInterval</key>
  <integer>120</integer>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
EOF

launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl enable "gui/$(id -u)/com.openclaw.tigge.mn2t6.resumer" >/dev/null 2>&1 || true
echo "installed: $PLIST_PATH"
