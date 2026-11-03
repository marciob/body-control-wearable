# bodycontrol

Read your own WHOOP 5.0 strap over Bluetooth LE and compute your own metrics. You do not need a
WHOOP account, a WHOOP subscription or the WHOOP cloud. Your data stays on your computer, unless
you set up the optional `push` or `archive` commands.

> **Disclaimer.** This is an independent, unofficial project. It is **not affiliated with,
> endorsed by, or sponsored by WHOOP, Inc.** "WHOOP" is a trademark of its owner. This project
> uses the name only to identify the hardware that it works with.
>
> **Not a medical device.** Heart rate, HRV, sleep and zone values are approximations. They are
> not clinically validated and they are not medical advice.
>
> **Your own strap, your own data, your own risk.** The protocol is undocumented and can change
> with a firmware update. This tool takes the Bluetooth bond from the WHOOP app. After this tool
> downloads history from the strap, the strap deletes that history. No warranty of any kind.
>
> Read [DISCLAIMER.md](DISCLAIMER.md) for the full notice.

## Requirements

- A WHOOP 5.0 that you own. The code also identifies the WHOOP MG, which uses the same Bluetooth
  service, but nobody has tested this tool on an MG.
- A Mac with Bluetooth. The Bluetooth library (`bleak`) also supports Linux with BlueZ, but
  nobody has tested this tool on Linux. The automatic jobs (`install-autosync.sh`) are for macOS
  only.
- Python 3.12 or newer, and `uv`.

## Install

```sh
uv sync
```

## Where your data goes

All commands use one SQLite file:

```
~/Library/Application Support/bodycontrol/bodycontrol.sqlite
```

The first command that writes data makes the folder. To use a different file, add `--db <path>`
to a command.

Only two commands send data to other computers, and only when you configure them:

- `push` copies decoded rows to a server that you control, over SSH.
- `archive` uploads raw frames to an S3 bucket that you own.

## Commands

| Command | What it does |
|---|---|
| `scan` | List WHOOP straps in range. |
| `live` | Stream heart rate and R-R intervals, print rolling HRV, save to SQLite. No bond needed. |
| `probe` | Bond test: send CLIENT_HELLO and list the vendor frames. Reads no history. |
| `sync` | Download the strap's history one time. The strap deletes each part after the download. |
| `daemon` | Stay connected. Download the history every 15 min and log the battery. |
| `enable-deep` | Write 16 feature flags to the strap. Then the strap also records extra data (see below). |
| `imu` | Record the 100 Hz six-axis motion sensor for a fixed time. |
| `night` | One line per night: sleep window, RHR (median HR) and HRV (RMSSD). |
| `zones` | Minutes per heart-rate zone per day. |
| `push` | Optional. Copy new decoded rows to your server over SSH. |
| `archive` | Optional. Upload new raw frames to your S3 bucket (compressed), then delete the local copies. |

Run `uv run bodycontrol <command> --help` for the options.

## First use

1. Close the WHOOP app on the phone. Keep the strap on your wrist.
2. Find the strap:
   ```sh
   uv run bodycontrol scan
   ```
3. Stream heart rate (no bond):
   ```sh
   uv run bodycontrol live
   ```
   One line per second: time, HR, R-R intervals in ms, RMSSD over the last 60 s, wrist contact.
   Press Ctrl-C to stop.
4. Download the history (this makes a bond; read "What needs a bond" first):
   ```sh
   uv run bodycontrol sync --address <id from scan>
   ```

## Run it all the time (macOS)

`scripts/install-autosync.sh` installs two launchd jobs:

- `com.bodycontrol.daemon` stays connected and downloads the history every 15 min. It runs under
  `caffeinate -s`, so the Mac does not sleep while it is on AC power. On battery the Mac sleeps
  as usual, and the daemon downloads the missing history after the Mac wakes.
- `com.bodycontrol.sync` runs every hour. It runs `push` if `BODYCONTROL_PUSH_HOST` is set, and
  `archive` if `BODYCONTROL_BUCKET` is set. If neither is set, it does nothing.

```sh
BODYCONTROL_ADDRESS=<id from scan> ./scripts/install-autosync.sh
```

launchd jobs do not read your shell profile. Set the variables below in the same command, or
export them before you run the script. The script copies them into the job.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `BODYCONTROL_ADDRESS` | `install-autosync.sh` | The strap id from `scan`. Required. |
| `BODYCONTROL_PUSH_HOST` | `push` | SSH target, for example `user@server`. |
| `BODYCONTROL_REMOTE_DIR` | `push` | Folder on the server that has a copy of this repository. Default: `body-control`. |
| `BODYCONTROL_BUCKET` | `archive` | Your S3 bucket for the raw frames. |
| `BODYCONTROL_AWS_PROFILE` | `archive` | AWS CLI profile. Default: `default`. |

If you run `push` or `archive` without its target, the command stops with an error. The other
commands need no configuration.

## Metrics

Nightly numbers from the downloaded history:
```sh
uv run bodycontrol night
```
One line per night: sleep window, hours asleep, RHR (median heart rate while asleep) and
HRV (RMSSD, ms; 5-min windows, Malik artifact rule, no difference across a rejected beat).
Nights under 3 h give no numbers. If the records stop while you are still asleep, the night is
marked `incomplete`: part of the history can still be on the strap. Then the line shows no wake
time and no duration. See `bodycontrol/night.py` for the method.

Minutes per heart-rate zone per day:
```sh
uv run bodycontrol zones --max-hr 183
```
Replace 183 with your own maximum heart rate. Zone 1 is 50-60 % of your maximum heart rate,
zone 2 is 60-70 %, up to zone 5 at 90-100 %. Below 50 % is rest. Each record is one second of
wear, so the minutes are counts, not estimates. The last column is the sum of zones 2 to 5.
With `--json`, each day also gives `data_end`, the time of the newest record.

## What the strap gives without a bond

The strap has the standard Bluetooth Heart Rate service (0x180D / 0x2A37). It sends HR and R-R
intervals about one time per second to any device, with no bond. This is the only data that a
WHOOP 5.0 gives to another app before a bond.

## What needs a bond

Everything on the WHOOP service `fd4b0001-...` needs an encrypted link: battery details, clock,
the stored history (HR, R-R, motion and skin temperature, one record per second), haptics.

- The strap holds one bond at a time. When this tool makes a bond, the WHOOP app loses its bond.
  To use the WHOOP app again, pair it again.
- During a history download, the tool confirms each part. The strap then deletes that part.
  Data that this tool downloads does not go to the WHOOP app or the WHOOP cloud.
- This tool cannot get SpO2 from the data that a 5.0 sends. Sleep stages are approximate.
- A firmware update can change the record layout.

## Extra data (`enable-deep`)

By default the strap records only a basic record (version 18). The WHOOP app writes 16 feature
flags to the strap on each connection. Most of their names start with `enable_r22_`, so this
project calls them the "R22 flags". With these flags set, the strap also records more detailed
data, for example the raw optical sensor samples and the motion sensor.

`enable-deep` writes the same 16 flags, with values from captures of the WHOOP app. `sync` also
writes them before each download, unless you add `--no-deep`. The strap keeps the flags: they
are persistent. The full effect of each flag is not known.

## Known issue: the strap stops recording while you wear it

The strap records no history while its wear detector says "off-body". The strap's log (packet
type 50) shows the state: `SIGPROC-WEAR-DETECT V5: moving from state X to state Y`. State 3 is
off-body.

On one strap, the detector went to state 3 while the sensor touched the skin (`opt_ir_near_m`
6 to 9). It stayed in state 3 for a full night, until the wearer moved. When the strap is really
off the wrist, this value is 0.1 to 0.8. The record numbers continue without a gap, so no data
was lost during the download: the strap did not record that data.

Other 5.0 owners report the same "off-body" problem with the WHOOP app. Some say that a restart
of the strap (command 29, `REBOOT_STRAP`) stops it. This tool does not restart the strap.

Two captures of the WHOOP app at night write `enable_sig12 = '2'` (off). One capture during a
workout writes `'1'`. This tool uses `'2'`. The effect of this flag is not known.

## Run the tests

```sh
uv run pytest
```

## Sources

The protocol facts come from public community projects and from the Bluetooth traffic of a
strap that the author owns. The code in this repository is original.

- NOOP: https://github.com/ryanbr/noop (docs/PROTOCOL.md, docs/BLE_REVERSE_ENGINEERING.md, issues #103 and #423)
- NOOP fork with 5.0 deep-data work: https://github.com/vishk23/noop
- wearable5.0, WHOOP 5.0 protocol schema: https://github.com/LeonardSEO/wearable5.0
- OpenStrap protocol decoder (Dart): https://github.com/OpenStrap/protocol
- Goose, first 5.0 client: https://github.com/b-nnett/goose
- judes.club, "Cracking the WHOOP 5 Bluetooth Protocol": https://judes.club/writing/cracking-the-whoop-5-bluetooth-protocol/

## License

MIT. See [LICENSE](LICENSE).
