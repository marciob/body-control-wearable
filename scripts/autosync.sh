#!/bin/zsh
# Hourly job: copy new decoded rows to your server, then archive raw frames to S3.
# Each step runs only if its target is set (see install-autosync.sh).
# The Bluetooth link belongs to the daemon (com.bodycontrol.daemon); this job never touches it.
# Installed by scripts/install-autosync.sh. Runs from ~/Library/Application Support/bodycontrol,
# because launchd jobs cannot read ~/Desktop (macOS privacy protection).
APP="$HOME/Library/Application Support/bodycontrol"
BC="$HOME/.local/bin/bodycontrol"
DB="$APP/bodycontrol.sqlite"
cd "$APP" || exit 1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') autosync start"
if [[ -n "$BODYCONTROL_PUSH_HOST" ]]; then
  "$BC" push --db "$DB" --origin mac-auto
  echo "push exit $?"
else
  echo "push skipped: BODYCONTROL_PUSH_HOST is not set"
fi
if [[ -n "$BODYCONTROL_BUCKET" ]]; then
  "$BC" archive --db "$DB" --origin mac-auto
  echo "archive exit $?"
else
  echo "archive skipped: BODYCONTROL_BUCKET is not set"
fi
