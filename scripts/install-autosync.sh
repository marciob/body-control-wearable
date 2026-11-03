#!/bin/zsh
# Install or update both jobs. Run again after code changes.
#   com.bodycontrol.daemon : always connected (live IMU, history drain, battery log)
#   com.bodycontrol.sync   : hourly push to your server + archive to S3 (each only if configured)
set -e
# The daemon needs the strap's CoreBluetooth id (run `bodycontrol scan`): BODYCONTROL_ADDRESS=... ./scripts/install-autosync.sh
# The hourly job uses BODYCONTROL_PUSH_HOST, BODYCONTROL_REMOTE_DIR, BODYCONTROL_BUCKET and
# BODYCONTROL_AWS_PROFILE. Set them before you run this script. An empty target skips that step.
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
  sed -e "s#__APP__#$APP#g" -e "s#__HOME__#$HOME#g" -e "s#__ADDRESS__#$BODYCONTROL_ADDRESS#g" \
      -e "s#__PUSH_HOST__#${BODYCONTROL_PUSH_HOST:-}#g" -e "s#__REMOTE_DIR__#${BODYCONTROL_REMOTE_DIR:-body-control}#g" \
      -e "s#__BUCKET__#${BODYCONTROL_BUCKET:-}#g" -e "s#__AWS_PROFILE__#${BODYCONTROL_AWS_PROFILE:-default}#g" \
      "scripts/$label.plist" > "$LA/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  # bootout returns before the old process is gone; bootstrap fails (I/O error) if it is still there.
  while launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; do sleep 1; done
  launchctl bootstrap "gui/$(id -u)" "$LA/$label.plist"
  echo "installed: $label"
done
