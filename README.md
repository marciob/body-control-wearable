# bodycontrol

Read your own WHOOP 5.0 strap over Bluetooth LE. Compute your own metrics. No cloud.

> **Disclaimer.** This is an independent, unofficial project. It is **not affiliated with,
> endorsed by, or sponsored by WHOOP, Inc.** "WHOOP" is a trademark of its owner. This project
> uses the name only to identify the hardware that it works with.
>
> **Not a medical device.** Heart rate, HRV, sleep and zone values are approximations. They are
> not clinically validated and they are not medical advice.
>
> **Your own strap, your own data, your own risk.** The protocol is undocumented and can change
> with a firmware update. The tool can take the bond from the WHOOP app and can trim history from
> the strap. No warranty of any kind.
>
> Read [DISCLAIMER.md](DISCLAIMER.md) for the full notice.

## Requirements

- macOS with Bluetooth, or Linux with BlueZ.
- Python 3.12 or newer, and `uv`.
- A WHOOP 5.0 or MG that you own.

## Install

```sh
uv sync
```

## Commands

| Command | What it does |
|---|---|
| `scan` | List WHOOP straps in range. |
| `live` | Stream heart rate and R-R intervals, print rolling HRV, save to SQLite. No bond needed. |
| `probe` | Bond test: send CLIENT_HELLO and list the vendor frames. Reads no history. |
| `sync` | Offload the strap's history once. The strap trims each chunk after the ack. |
| `daemon` | Stay connected. Drain the history every 15 min and log the battery. |
| `enable-deep` | Write the 16 R22 feature flags that unlock the deep data streams. |
| `imu` | Capture the 100 Hz six-axis IMU for a bounded session. |
| `night` | One line per night: sleep window, RHR (median HR) and HRV (RMSSD). |
| `zones` | Minutes per heart-rate zone per day. |
| `push` | Copy new decoded rows to your server over SSH. |
| `archive` | Upload new raw frames to your S3 bucket (compressed), then prune the local copies. |

Run `uv run bodycontrol <command> --help` for the options.

## First use

1. Quit the WHOOP app on the phone. Keep the strap on your wrist.
2. Find the strap:
   ```sh
   uv run bodycontrol scan
   ```
3. Stream heart rate (no bond):
   ```sh
   uv run bodycontrol live
   ```
   One line per second: time, HR, R-R intervals in ms, RMSSD over the last 60 s, wrist contact.
   Press Ctrl-C to stop. Samples go to `bodycontrol.sqlite`.
4. Offload the history (bond; see "What needs a bond"):
   ```sh
   uv run bodycontrol sync --address <id from scan>
   ```

## Run it all the time (macOS)

`scripts/install-autosync.sh` installs two launchd jobs:

- `com.bodycontrol.daemon` stays connected and drains the history every 15 min. It runs under
  `caffeinate -s`, so the Mac does not sleep on AC power. On battery the Mac sleeps as usual and
  the daemon catches up after wake.
- `com.bodycontrol.sync` runs every hour: `push`, then `archive`.

```sh
BODYCONTROL_ADDRESS=<id from scan> ./scripts/install-autosync.sh
```

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `BODYCONTROL_ADDRESS` | `install-autosync.sh` | The strap id from `scan`. |
| `BODYCONTROL_PUSH_HOST` | `push` | SSH target, for example `user@server`. |
| `BODYCONTROL_REMOTE_DIR` | `push` | Checkout of this repository on the server. Default: `body-control`. |
| `BODYCONTROL_BUCKET` | `archive` | Your S3 bucket for the raw frames. |
| `BODYCONTROL_AWS_PROFILE` | `archive` | AWS CLI profile. Default: `default`. |

`push` and `archive` stop with an error if their target is not set. The other commands need no
configuration.

## Metrics

Nightly numbers from the collected history:
```sh
bodycontrol night
```
One line per night: sleep window, hours asleep, RHR (median heart rate while asleep) and
HRV (RMSSD, ms; 5-min windows, Malik artifact rule, no difference across a rejected beat).
Nights under 3 h give no numbers. A night whose records stop while still asleep is marked
`incomplete`: the history may still be on the strap, so it shows no wake time and no duration.
See `bodycontrol/night.py` for the method.

Minutes per heart-rate zone per day:
```sh
bodycontrol zones --max-hr 183
```
Zone 1 is 50-60 % of your maximum heart rate, zone 2 is 60-70 %, up to zone 5 at 90-100 %.
Below 50 % is rest. Each record is one second worn, so the minutes are counts, not estimates.
The last column is the sum of zones 2 to 5. With `--json`, each day also gives `data_end`, the
time of the newest record.

## What the strap gives without a bond

The strap exposes the standard Bluetooth Heart Rate service (0x180D / 0x2A37). It streams HR and
R-R intervals at about 1 Hz to any central, with no bond. This is the only data that a WHOOP 5.0
gives to a third party before a bond.

## What needs a bond

Everything on the vendor service `fd4b0001-...` needs an encrypted link: battery details,
clock, the historical records (1 Hz HR, R-R, motion, skin temperature), haptics.

- The strap holds one bond at a time. A bond with this tool takes the bond from the WHOOP app.
  The app must pair again later.
- The history offload acknowledges each chunk. The strap then trims that chunk from its store.
  Data that this tool pulls does not reach the WHOOP app or the WHOOP cloud.
- SpO2 is not recoverable from the 5.0 wire data. Sleep stages are approximate.
- Firmware updates can change record layouts.

## Known issue: the strap stops recording while worn

The strap writes no history while its wear detector says "off-body". Its console log (packet
type 50) shows the state: `SIGPROC-WEAR-DETECT V5: moving from state X to state Y`. State 3 is
off-body. On one strap, the detector went to state 3 with the sensor on the skin
(`opt_ir_near_m` 6 to 9) and stayed there for a full night, until the wearer moved. A strap that
is really off the wrist reads 0.1 to 0.8. The record index stays continuous across the gap, so
the sync loses nothing: the strap did not record.

Owners of the 5.0 report the same "off-body" problem with the official app. Some say that a
reboot of the strap (command 29, `REBOOT_STRAP`) clears it. This tool does not reboot the strap
by itself.

Two captures of the official app at night write `enable_sig12 = '2'` (off). One capture during a
workout writes `'1'`. `enable-deep` uses `'2'`. The effect of this flag is not known.

## Run the tests

```sh
uv run pytest
```

## Sources

Protocol facts come from public community work and from Bluetooth traffic of a strap that the
author owns. The code here is original.

- NOOP: https://github.com/ryanbr/noop (docs/PROTOCOL.md, docs/BLE_REVERSE_ENGINEERING.md, issues #103 and #423)
- NOOP fork with 5.0 deep-data work: https://github.com/vishk23/noop
- wearable5.0, WHOOP 5.0 protocol schema: https://github.com/LeonardSEO/wearable5.0
- OpenStrap protocol decoder (Dart): https://github.com/OpenStrap/protocol
- Goose, first 5.0 client: https://github.com/b-nnett/goose
- judes.club, "Cracking the WHOOP 5 Bluetooth Protocol": https://judes.club/writing/cracking-the-whoop-5-bluetooth-protocol/

## License

MIT. See [LICENSE](LICENSE).
