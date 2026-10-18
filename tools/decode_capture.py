"""Decode local E5 captures with locally extracted LightTrip native library.

Never sends Bluetooth commands. Output includes private clinical information
and session key exchanges: keep it in evidence/private.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.bmc_e5.protocol import FrameBuffer, ProtocolError, SessionCodec, parse_realtime
from tools.hci_extract import extract


def decode_capture(hci: bytes, native: bytes):
    candidates = [int(m.group(1), 16) for m in re.finditer(rb"N = ([A-F0-9]{256})", native)]
    exponents = [int(m.group(1), 16) for m in re.finditer(rb"D = ([A-F0-9]{256})", native)]
    if not candidates or not exponents:
        raise ProtocolError("RSA material not found in this library version")
    capture = extract(hci)
    # GATT caches may suppress the name read on reconnect. A candidate handshake
    # must subsequently return the verified model before its data is retained.
    connections = {r["connection"] for r in capture["att_records"]
                   if bytes.fromhex(r["att"]).startswith(b"\x52\x18\x00\x02\x01\x00\x80")}
    if not connections:
        raise ProtocolError("No candidate E5 handshake; include connection in capture")
    buffers = {}
    codecs = {}
    messages = []
    for record in capture["att_records"]:
        if record["connection"] not in connections:
            continue
        att = bytes.fromhex(record["att"])
        # This handle was verified in the supplied E5 capture; not a generic GATT assumption.
        if att[:3] not in (b"\x52\x18\x00", b"\x1b\x18\x00"):
            continue
        connection, direction = record["connection"], record["direction"]
        buffer = buffers.setdefault((connection, direction), FrameBuffer())
        for frame in buffer.feed(att[3:]):
            if connection not in codecs:
                if frame[1] != 1 or direction != 0:
                    raise ProtocolError("Missing outgoing RSA handshake")
                matches = []
                for modulus in candidates:
                    for exponent in exponents:
                        candidate = SessionCodec(modulus, exponent)
                        try:
                            message = candidate.decode(frame, direction)
                        except ProtocolError:
                            continue
                        if message.command == 0:
                            matches.append(candidate)
                if len(matches) != 1:
                    raise ProtocolError("No unique verified RSA profile")
                codecs[connection] = matches[0]
            message = codecs[connection].decode(frame, direction)
            item = asdict(message)
            item["payload"] = message.payload.hex()
            item.update(connection=connection, direction=direction, timestamp=record["timestamp"])
            if message.command == 0x06EA:
                item["realtime_raw"] = parse_realtime(message)
            messages.append(item)
    if not messages:
        raise ProtocolError("No verified E5 messages at the expected capture handle")
    verified = {m["connection"] for m in messages if m["command"] == 0x06A4
                and bytes.fromhex(m["payload"])[:24].rstrip(b"\0") == b"E5 B25A Plus"}
    if verified != connections:
        raise ProtocolError("Not every candidate session returned the expected E5 model")
    return {"messages": messages, "incomplete_frames": sum(b.pending > 0 for b in buffers.values()),
            "incomplete_acl_messages": capture["incomplete_acl_messages"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hci", type=Path, required=True)
    parser.add_argument("--native-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new filename")
    result = decode_capture(args.hci.read_bytes(), args.native_library.read_bytes())
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({"messages": len(result["messages"]),
                      "realtime": sum("realtime_raw" in m for m in result["messages"]),
                      "incomplete_frames": result["incomplete_frames"],
                      "incomplete_acl_messages": result["incomplete_acl_messages"]}))


if __name__ == "__main__":
    main()
