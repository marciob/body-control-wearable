"""Find a WHOOP strap with bleak."""

from __future__ import annotations

from dataclasses import dataclass

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from . import uuids


@dataclass(frozen=True)
class Found:
    device: BLEDevice
    adv: AdvertisementData
    family: str  # "whoop5", "whoop4", "whoop-1150", "hr-only" or "name-only"


def classify(device: BLEDevice, adv: AdvertisementData) -> str | None:
    svc = [u.lower() for u in (adv.service_uuids or [])]
    name = (device.name or adv.local_name or "").lower()
    if any(u.startswith(uuids.WHOOP5_PREFIX) for u in svc):
        return "whoop5"
    if any(u.startswith(uuids.WHOOP4_PREFIX) for u in svc):
        return "whoop4"
    if any(u.startswith(uuids.WHOOP_1150_PREFIX) for u in svc):
        return "whoop-1150"  # second WHOOP family; probably the battery pack, not the strap
    if "whoop" in name:
        if uuids.HEART_RATE_SERVICE in svc:
            return "hr-only"
        return "name-only"
    return None


async def scan(timeout: float = 10.0) -> list[Found]:
    seen = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out: list[Found] = []
    for device, adv in seen.values():
        fam = classify(device, adv)
        if fam:
            out.append(Found(device, adv, fam))
    out.sort(key=lambda f: f.adv.rssi or -999, reverse=True)
    return out


STRAP_FAMILIES = ("whoop5", "whoop4", "hr-only", "name-only")


async def find_whoop(timeout: float = 10.0, address: str | None = None) -> BLEDevice | None:
    if address:
        return await BleakScanner.find_device_by_address(address, timeout=timeout)
    found = [f for f in await scan(timeout) if f.family in STRAP_FAMILIES]
    return found[0].device if found else None
