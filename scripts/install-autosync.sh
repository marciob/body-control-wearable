#!/bin/zsh
# Install or update both jobs. Run again after code changes.
#   com.bodycontrol.daemon : always connected (live IMU, history drain, battery log)
#   com.bodycontrol.sync   : hourly push to the VPS + archive to S3
set -e
# The daemon needs the strap's CoreBluetooth id (run `bodycontrol scan`): BODYCONTROL_ADDRESS=... ./scripts/install-autosync.sh
: "${BODYCONTROL_ADDRESS:?set BODYCONTROL_ADDRESS to the strap id from bodycontrol scan}"
cd "$(dirname "$0")/.."
APP="$HOME/Library/Application Support/bodycontrol"
LA="$HOME/Library/LaunchAgents"
mkdir -p "$APP"
uv tool install --force --reinstall . >/dev/null
cp scripts/autosync.sh "$APP/autosync.sh"
chmod +x "$APP/autosync.sh"
for name in sync daemon; do
  label="com.bodycontrol.$name"
  sed -e "s#__APP__#$APP#g" -e "s#__HOME__#$HOME#g" -e "s#__ADDRESS__#$BODYCONTROL_ADDRESS#g" "scripts/$label.plist" > "$LA/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  # bootout returns before the old process is gone; bootstrap fails (I/O error) if it is still there.
  while launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; do sleep 1; done
  launchctl bootstrap "gui/$(id -u)" "$LA/$label.plist"
  echo "installed: $label"
done
