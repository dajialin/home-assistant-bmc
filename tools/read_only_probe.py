"""Bounded, explicit-address E5 read-only connection validation."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bleak import BleakClient, BleakScanner
from custom_components.bmc_e5.protocol import DATA_UUID, SERVICE_UUID, FrameBuffer, ReadOnlySession, parse_realtime


async def probe(address, seconds, modulus, exponent, output):
    device = await BleakScanner.find_device_by_address(address, timeout=15)
    if device is None:
        raise RuntimeError("E5 not advertising near this computer")
    received = asyncio.Queue(maxsize=512)
    session = ReadOnlySession(modulus, exponent)
    framer = FrameBuffer()
    records = []
    overflow = asyncio.Event()

    def notify(_, value):
        try:
            received.put_nowait(bytes(value))
        except asyncio.QueueFull:
            overflow.set()

    started = datetime.now(timezone.utc).isoformat()
    try:
        async with BleakClient(device, timeout=20) as client:
            service = client.services.get_service(SERVICE_UUID)
            if service is None:
                raise RuntimeError("Expected E5 service absent")
            characteristic = service.get_characteristic(DATA_UUID)
            if characteristic is None or not {"notify", "write-without-response"}.issubset(characteristic.properties):
                raise RuntimeError("Expected E5 characteristic capabilities absent")

            async def send(frame):
                # App uses MTU-minus-five; cap at 98, matching the observed capture.
                chunk_size = max(1, min(98, client.mtu_size - 5, characteristic.max_write_without_response_size))
                for offset in range(0, len(frame), chunk_size):
                    await client.write_gatt_char(characteristic, frame[offset:offset + chunk_size], response=False)
                    await asyncio.sleep(0.02)

            await client.start_notify(characteristic, notify)
            await send(session.begin())
            loop = asyncio.get_running_loop()
            end = loop.time() + seconds
            next_heartbeat = loop.time() + 10
            while loop.time() < end:
                if overflow.is_set():
                    raise RuntimeError("Notification buffer overflow")
                if not client.is_connected:
                    raise RuntimeError("Device disconnected")
                try:
                    data = await asyncio.wait_for(received.get(), timeout=min(1, max(0.01, end - loop.time())))
                except TimeoutError:
                    data = b""
                for frame in framer.feed(data):
                    message, responses = session.receive(frame)
                    item = asdict(message)
                    item["payload"] = message.payload.hex()
                    item["received_at"] = datetime.now(timezone.utc).isoformat()
                    if message.command == 0x06EA:
                        item["realtime_raw"] = parse_realtime(message)
                    records.append(item)
                    for response in responses:
                        await send(response)
                if loop.time() >= next_heartbeat and session.state in ("reading", "ready"):
                    await send(session.heartbeat())
                    next_heartbeat = loop.time() + 10
            await client.stop_notify(characteristic)
        if session.state != "ready" or not any("realtime_raw" in m for m in records):
            raise RuntimeError("Did not receive verified model and realtime data")
        return len(records), sum("realtime_raw" in m for m in records)
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf8") as stream:
            json.dump({"started_at": started, "state": session.state, "messages": records}, stream, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--native-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=30, choices=range(20, 61), metavar="20..60")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists")
    native = args.native_library.read_bytes()
    moduli = [int(m.group(1), 16) for m in re.finditer(rb"N = ([A-F0-9]{256})", native)]
    exponents = [int(m.group(1), 16) for m in re.finditer(rb"D = ([A-F0-9]{256})", native)]
    # This exact key selection has been verified on both supplied captures.
    if len(moduli) != 2 or len(exponents) != 1:
        raise RuntimeError("Unexpected LightTrip library version")
    import hashlib
    expected = Path(__file__).resolve().parents[1] / "evidence/private/native-profile.sha256"
    if not expected.exists() or hashlib.sha256(native).hexdigest() != expected.read_text().strip():
        raise RuntimeError("Native library is not the locally verified profile")
    count, realtime = asyncio.run(probe(args.address, args.seconds, moduli[1], exponents[0], args.output))
    print(json.dumps({"verified_model": True, "messages": count, "realtime": realtime}))


if __name__ == "__main__":
    main()
