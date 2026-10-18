"""Offline BTSnoop/H4 ACL reassembly. Output contains private device traffic."""
import argparse
import json
import struct
from pathlib import Path


def extract(data):
    if len(data) < 16 or data[:8] != b"btsnoop\0" or struct.unpack_from(">II", data, 8) != (1, 1002):
        raise ValueError("Expected BTSnoop v1, H4 datalink 1002")
    pos = 16
    epochs = {}
    pending = {}
    records = []
    while pos < len(data):
        if pos + 24 > len(data):
            raise ValueError("Truncated BTSnoop record header")
        original, size, flags, drops, timestamp = struct.unpack_from(">IIIIQ", data, pos)
        pos += 24
        if size > original or pos + size > len(data):
            raise ValueError("Truncated or invalid BTSnoop record")
        packet = data[pos:pos + size]
        pos += size
        if original != size:
            raise ValueError("Capture contains truncated HCI packet")
        if len(packet) >= 6 and packet[:2] == b"\x04\x05":
            handle = int.from_bytes(packet[4:6], "little")
            epochs[handle] = epochs.get(handle, 0) + 1
            for key in list(pending):
                if key[0] == handle:
                    del pending[key]
        if not packet or packet[0] != 2:
            continue
        if len(packet) < 5:
            raise ValueError("Short ACL header")
        word, length = struct.unpack_from("<HH", packet, 1)
        if len(packet) != length + 5:
            raise ValueError("ACL length mismatch")
        handle, boundary = word & 0xFFF, (word >> 12) & 3
        key = (handle, flags & 1)
        payload = packet[5:]
        if boundary in (0, 2):
            if key in pending:
                raise ValueError("New ACL start before previous message completed")
            pending[key] = bytearray(payload)
        elif boundary == 1:
            if key not in pending:
                # Capture can start halfway through a connection.
                continue
            pending[key].extend(payload)
        else:
            raise ValueError("Unsupported ACL boundary")
        buffer = pending[key]
        if len(buffer) < 4:
            continue
        total, cid = struct.unpack_from("<HH", buffer)
        if len(buffer) > total + 4:
            raise ValueError("L2CAP length exceeded")
        if len(buffer) < total + 4:
            continue
        if cid == 4:
            records.append({"connection": f"{handle:03x}:{epochs.get(handle, 0)}",
                            "direction": flags & 1, "timestamp": timestamp,
                            "att": bytes(buffer[4:]).hex()})
        del pending[key]
    return {"att_records": records, "incomplete_acl_messages": len(pending)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = extract(args.input.read_bytes())
    with args.output.open("x", encoding="utf8") as stream:
        json.dump(result, stream, indent=2)
    print(f"Extracted {len(result['att_records'])} ATT records; incomplete ACL messages: {result['incomplete_acl_messages']}")


if __name__ == "__main__":
    main()
