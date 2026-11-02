#!/bin/zsh
# Hourly job: copy new decoded rows to the VPS, then archive raw frames to S3.
# The Bluetooth link belongs to the daemon (com.bodycontrol.daemon); this job never touches it.
# Installed by scripts/install-autosync.sh. Runs from ~/Library/Application Support/bodycontrol,
# because launchd jobs cannot read ~/Desktop (macOS privacy protection).
APP="$HOME/Library/Application Support/bodycontrol"
BC="$HOME/.local/bin/bodycontrol"
DB="$APP/bodycontrol.sqlite"
cd "$APP" || exit 1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') autosync start"
"$BC" push --db "$DB" --origin mac-auto
echo "push exit $?"
"$BC" archive --db "$DB" --origin mac-auto
echo "archive exit $?"
