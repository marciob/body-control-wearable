"""Command-line entry point: `bodycontrol scan` and `bodycontrol live`."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

from bleak import BleakClient

from . import uuids
from .ble import find_whoop, scan
from .hrm import parse_heart_rate
from .hrv import RollingHRV
from .store import Store, now_ms


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def cmd_scan(args: argparse.Namespace) -> int:
    _log(f"scanning {args.timeout:.0f}s ...")
    if args.all:
        from bleak import BleakScanner

        seen = await BleakScanner.discover(timeout=args.timeout, return_adv=True)
        for device, adv in sorted(seen.values(), key=lambda t: t[1].rssi or -999, reverse=True):
            name = device.name or adv.local_name or "?"
            svc = ", ".join(u[:8] for u in (adv.service_uuids or []))
            mfr = ",".join(f"{k:04x}" for k in (adv.manufacturer_data or {}))
            print(f"rssi {adv.rssi:4d}  {name!r:28s} {device.address}  services [{svc}] mfr [{mfr}]")
        _log(f"{len(seen)} devices")
        return 0
    found = await scan(args.timeout)
    if not found:
        _log("no WHOOP seen. Quit the WHOOP app on the phone, keep the strap on the wrist, retry.")
        _log("use `bodycontrol scan --all` to list every device in range.")
        return 1
    for f in found:
        name = f.device.name or f.adv.local_name or "?"
        svc = ", ".join(u[:8] for u in (f.adv.service_uuids or []))
        print(f"{f.family:9s} rssi {f.adv.rssi:4d}  {name!r:24s} {f.device.address}  services [{svc}]")
    return 0


async def _read_text(client: BleakClient, uuid: str) -> str | None:
    try:
        raw = await client.read_gatt_char(uuid)
    except Exception:
        return None
    return bytes(raw).decode("utf-8", "replace").strip("\x00 ")


async def _stream_once(dev, store: Store, hrv: RollingHRV, stop: asyncio.Event, deadline: float | None) -> None:
    disconnected = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_disconnect(_client: BleakClient) -> None:
        loop.call_soon_threadsafe(disconnected.set)

    async with BleakClient(dev, disconnected_callback=on_disconnect, timeout=20.0) as client:
        name = dev.name or "WHOOP"
        session = store.start_session(name, dev.address)
        _log(f"connected to {name!r} ({dev.address}); session {session}")

        serial = await _read_text(client, uuids.SERIAL_NUMBER)
        hw = await _read_text(client, uuids.HARDWARE_REVISION)
        fw = await _read_text(client, uuids.FIRMWARE_REVISION)
        if serial or hw or fw:
            _log(f"serial {serial}  hw {hw}  fw {fw}")
        try:
            batt = int(bytes(await client.read_gatt_char(uuids.BATTERY_LEVEL))[0])
            store.add_battery(session, now_ms(), batt)
            _log(f"battery {batt}%")
        except Exception as e:  # battery is optional
            _log(f"battery read failed: {type(e).__name__}")

        def on_hr(_sender, data: bytearray) -> None:
            ts = now_ms()
            m = parse_heart_rate(bytes(data))
            if m is None:
                return
            store.add_hr(session, ts, m.hr, m.sensor_contact)
            t = ts / 1000.0
            for rr in m.rr_ms:
                store.add_rr(session, ts, rr, hrv.add(t, rr))
            store.commit()
            rmssd = hrv.rmssd()
            rr_txt = " ".join(str(r) for r in m.rr_ms) or "-"
            rmssd_txt = f"{rmssd:5.1f} ms" if rmssd is not None else f"   n={hrv.count}"
            contact = "" if m.sensor_contact is None else (" on-wrist" if m.sensor_contact else " OFF-WRIST")
            print(f"{time.strftime('%H:%M:%S')}  HR {m.hr:3d}  RR {rr_txt:<12s} RMSSD({int(hrv.window_s)}s) {rmssd_txt}{contact}", flush=True)

        await client.start_notify(uuids.HEART_RATE_MEASUREMENT, on_hr)
        _log("subscribed to Heart Rate Measurement (0x2A37). Ctrl-C to stop.")

        waits = [asyncio.create_task(stop.wait()), asyncio.create_task(disconnected.wait())]
        timeout = None if deadline is None else max(0.0, deadline - time.time())
        done, pending = await asyncio.wait(waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if disconnected.is_set() and not stop.is_set():
            _log("strap disconnected")
        if not disconnected.is_set():
            try:
                await client.stop_notify(uuids.HEART_RATE_MEASUREMENT)
            except Exception:
                pass


async def cmd_live(args: argparse.Namespace) -> int:
    store = Store(args.db)
    hrv = RollingHRV(window_s=args.window)
    stop = asyncio.Event()
    deadline = None if args.minutes <= 0 else time.time() + args.minutes * 60
    backoff = 3.0
    try:
        while not stop.is_set():
            if deadline is not None and time.time() >= deadline:
                break
            _log("looking for the strap ...")
            dev = await find_whoop(timeout=args.timeout, address=args.address)
            if dev is None:
                _log(f"not found. Retry in {backoff:.0f}s. Quit the WHOOP app on the phone.")
                await asyncio.sleep(backoff)
                continue
            try:
                await _stream_once(dev, store, hrv, stop, deadline)
            except Exception as e:
                _log(f"link error: {type(e).__name__}: {e}")
                await asyncio.sleep(backoff)
    except KeyboardInterrupt:
        pass
    finally:
        store.close()
        _log(f"saved to {args.db}. RR accepted {hrv.accepted}, rejected {hrv.rejected}.")
    return 0


async def cmd_probe(args: argparse.Namespace) -> int:
    from .probe import run_probe

    return await run_probe(args.address, args.listen)


async def cmd_sync(args: argparse.Namespace) -> int:
    from .sync import run_sync

    return await run_sync(args.db, args.address, args.idle, args.max_minutes, deep=not args.no_deep)


async def cmd_push(args: argparse.Namespace) -> int:
    from .push import run_push

    if not args.host:
        sys.exit("push: give --host user@server or set BODYCONTROL_PUSH_HOST")
    return run_push(args.db, args.host, args.remote_dir, args.origin)


async def cmd_archive(args: argparse.Namespace) -> int:
    from .archive import DEFAULT_BUCKET, DEFAULT_PROFILE, run_archive

    bucket = args.bucket or DEFAULT_BUCKET
    if not bucket:
        sys.exit("archive: give --bucket or set BODYCONTROL_BUCKET")
    return run_archive(args.db, args.origin, bucket, args.profile or DEFAULT_PROFILE, args.keep_days)


async def cmd_enable(args: argparse.Namespace) -> int:
    from .deep import run_enable

    return await run_enable(args.address)


async def cmd_imu(args: argparse.Namespace) -> int:
    import os

    from .imu import run_imu

    db = args.db or os.path.expanduser("~/Library/Application Support/bodycontrol/bodycontrol.sqlite")
    return await run_imu(db, args.address, args.minutes, stop_only=args.stop)


async def cmd_daemon(args: argparse.Namespace) -> int:
    import os

    from .daemon import run_daemon

    db = args.db or os.path.expanduser("~/Library/Application Support/bodycontrol/bodycontrol.sqlite")
    return await run_daemon(db, args.address, args.drain_every, args.battery_every, imu=args.imu)


async def cmd_night(args: argparse.Namespace) -> int:
    import os

    from .night import run_night

    db = args.db or os.path.expanduser("~/Library/Application Support/bodycontrol/bodycontrol.sqlite")
    return run_night(db, args.days, args.json)


async def cmd_zones(args: argparse.Namespace) -> int:
    import os

    from .zones import run_zones

    db = args.db or os.path.expanduser("~/Library/Application Support/bodycontrol/bodycontrol.sqlite")
    return run_zones(db, args.max_hr, args.days, args.json)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bodycontrol", description="Read your WHOOP 5.0 over BLE.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="list WHOOP straps in range")
    s.add_argument("--timeout", type=float, default=10.0)
    s.add_argument("--all", action="store_true", help="list every BLE device, not only WHOOP")
    s.set_defaults(fn=cmd_scan)

    l = sub.add_parser("live", help="stream heart rate + R-R, print HRV, save to SQLite")
    l.add_argument("--address", help="device address/UUID from `scan`")
    l.add_argument("--db", default="bodycontrol.sqlite")
    l.add_argument("--minutes", type=float, default=0, help="stop after N minutes (0 = until Ctrl-C)")
    l.add_argument("--window", type=float, default=60.0, help="HRV window in seconds")
    l.add_argument("--timeout", type=float, default=10.0, help="scan timeout per attempt")
    l.set_defaults(fn=cmd_live)

    pr = sub.add_parser("probe", help="bond test: send CLIENT_HELLO, list vendor frames (no history)")
    pr.add_argument("--address", help="device address/UUID from `scan`")
    pr.add_argument("--listen", type=float, default=15.0)
    pr.set_defaults(fn=cmd_probe)

    sy = sub.add_parser("sync", help="offload the strap's history (acked chunks are trimmed from the strap)")
    sy.add_argument("--address")
    sy.add_argument("--db", default="bodycontrol.sqlite")
    sy.add_argument("--idle", type=float, default=30.0)
    sy.add_argument("--max-minutes", type=float, default=30.0)
    sy.add_argument("--no-deep", action="store_true", help="do not write the R22 deep-stream flags before the drain")
    sy.set_defaults(fn=cmd_sync)

    pu = sub.add_parser("push", help="copy new local rows to the VPS over SSH")
    pu.add_argument("--db", default="bodycontrol.sqlite")
    pu.add_argument("--host", default=os.environ.get("BODYCONTROL_PUSH_HOST"), help="SSH target, e.g. user@server (env BODYCONTROL_PUSH_HOST)")
    pu.add_argument("--remote-dir", default=os.environ.get("BODYCONTROL_REMOTE_DIR", "body-control"), help="checkout of this repo on the server (env BODYCONTROL_REMOTE_DIR)")
    pu.add_argument("--origin", default="mac")
    pu.set_defaults(fn=cmd_push)

    ar = sub.add_parser("archive", help="upload new raw frames to S3 (compressed), prune old local copies")
    ar.add_argument("--db", default="bodycontrol.sqlite")
    ar.add_argument("--origin", default="mac")
    ar.add_argument("--bucket", default=None)
    ar.add_argument("--profile", default=None)
    ar.add_argument("--keep-days", type=float, default=0.0, help="0 = delete each frame once S3 confirms the upload")
    ar.set_defaults(fn=cmd_archive)

    en = sub.add_parser("enable-deep", help="write the 16 R22 feature flags that unlock the deep streams")
    en.add_argument("--address")
    en.set_defaults(fn=cmd_enable)

    im = sub.add_parser("imu", help="live 100 Hz six-axis IMU capture for a bounded session")
    im.add_argument("--address")
    im.add_argument("--db", default=None, help="default: the hourly job's database")
    im.add_argument("--minutes", type=float, default=5.0)
    im.add_argument("--stop", action="store_true", help="only send the stop commands")
    im.set_defaults(fn=cmd_imu)

    dm = sub.add_parser("daemon", help="stay connected: history drain every 15 min + battery log")
    dm.add_argument("--address")
    dm.add_argument("--db", default=None, help="default: the hourly job's database")
    dm.add_argument("--drain-every", type=float, default=15.0, help="minutes between history drains (default 15)")
    dm.add_argument("--battery-every", type=float, default=15.0, help="minutes between battery reads")
    dm.add_argument("--imu", action="store_true",
                    help="also stream the live IMU; it duplicates history v21 and costs battery")
    dm.set_defaults(fn=cmd_daemon)

    ni = sub.add_parser("night", help="one line per night: sleep window, RHR (median HR) and HRV (RMSSD)")
    ni.add_argument("--db", help="default: the daemon's database")
    ni.add_argument("--days", type=int, default=30, help="look back this many days (default 30)")
    ni.add_argument("--json", action="store_true", help="one JSON object per night")
    ni.set_defaults(fn=cmd_night)

    zo = sub.add_parser("zones", help="minutes per heart-rate zone per day (WHOOP zones, as a share of max HR)")
    zo.add_argument("--max-hr", type=int, required=True, help="your maximum heart rate, bpm (WHOOP default: 220 - age)")
    zo.add_argument("--db", help="default: the daemon's database")
    zo.add_argument("--days", type=int, default=14, help="whole local days to show, today included (default 14)")
    zo.add_argument("--json", action="store_true", help="one JSON object per day")
    zo.set_defaults(fn=cmd_zones)

    args = p.parse_args(argv)
    try:
        return asyncio.run(args.fn(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
