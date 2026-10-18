"""Collect BLE advertisements; optional explicit-device GATT service discovery.

No characteristic reads, writes, pairing or notification subscriptions.
"""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner


async def capture(seconds, address):
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    records = []
    for device, adv in found.values():
        if address and device.address.casefold() != address.casefold():
            continue
        records.append({"address": device.address, "name": adv.local_name or device.name,
                        "rssi": adv.rssi, "service_uuids": adv.service_uuids,
                        "manufacturer_data": {str(k): v.hex() for k, v in adv.manufacturer_data.items()},
                        "service_data": {k: v.hex() for k, v in adv.service_data.items()}})
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "devices": records}
    if address:
        target = next((d for d, _ in found.values() if d.address.casefold() == address.casefold()), None)
        if target is None:
            raise RuntimeError("Selected device not found; no connection attempted")
        async with BleakClient(target, timeout=20) as client:
            result["services"] = [{"uuid": service.uuid, "handle": service.handle,
                                    "characteristics": [{"uuid": c.uuid, "handle": c.handle,
                                        "properties": c.properties,
                                        "descriptors": [{"uuid": d.uuid, "handle": d.handle} for d in c.descriptors]}
                                        for c in service.characteristics]} for service in client.services]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=15, choices=range(1, 61), metavar="1..60")
    parser.add_argument("--address", help="Explicit known target; also discover GATT services")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new filename")
    data = asyncio.run(capture(args.seconds, args.address))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    print(f"Saved {len(data['devices'])} devices to {args.output}")


if __name__ == "__main__":
    main()
