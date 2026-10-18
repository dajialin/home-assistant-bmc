import binascii
import struct
import unittest

from custom_components.bmc_e5.protocol import FrameBuffer, Message, ProtocolError, SessionCodec, parse_realtime
from custom_components.bmc_e5.values import entity_values
from tools.hci_extract import extract


def message_bytes(command, payload=b""):
    body = struct.pack(">BBHHHHHI", 1, 0, command, 0, 0, 1, 1, 1) + payload
    return body + binascii.crc_hqx(body, 0).to_bytes(2, "big")


class ProtocolTests(unittest.TestCase):
    def test_every_split_boundary_and_coalescing(self):
        frame = b"\x02\x05\x00\x20" + bytes(32)
        for split in range(1, len(frame)):
            parser = FrameBuffer()
            self.assertEqual(parser.feed(frame[:split]), [])
            self.assertEqual(parser.feed(frame[split:] + frame), [frame, frame])
            self.assertEqual(parser.pending, 0)

    def test_invalid_frames_reset_buffer(self):
        for frame in (b"\x03\x05\x00\x20", b"\x02\x06\x00\x20", b"\x02\x05\xff\xff", b"\x02\x05\x00\x01"):
            parser = FrameBuffer()
            with self.subTest(frame=frame), self.assertRaises(ProtocolError):
                parser.feed(frame)
            self.assertEqual(parser.pending, 0)

    def test_crc_and_truncation(self):
        good = message_bytes(8)
        self.assertEqual(Message.parse(good).command, 8)
        for bad in (good[:-1], bytes(10), good[:-2] + bytes(2)):
            with self.assertRaises(ProtocolError):
                Message.parse(bad)

    def test_unknown_version_and_blocks(self):
        for values in ((2, 0, 8, 0, 0, 1, 1, 1), (1, 0, 8, 0, 0, 0, 1, 1)):
            body = struct.pack(">BBHHHHHI", *values)
            with self.assertRaises(ProtocolError):
                Message.parse(body + binascii.crc_hqx(body, 0).to_bytes(2, "big"))

    def test_missing_key_and_bad_direction(self):
        codec = SessionCodec(1 << 1023, 3)
        with self.assertRaisesRegex(ProtocolError, "key missing"):
            codec.decode(b"\x02\x05\x00\x10" + bytes(16), 1)
        with self.assertRaises(ProtocolError):
            codec.decode(b"", 2)

    def test_unknown_realtime_version(self):
        with self.assertRaises(ProtocolError):
            parse_realtime(Message.parse(message_bytes(0x6ea, bytes(70))))

    def test_unknown_mode_or_unit_does_not_guess_pressure(self):
        settings = {"minimum_epap_raw": 400, "maximum_ipap_raw": 1500}
        value = entity_values({"realtime": {"mode": 6, "pressure_unit": 1}, "settings": {6: settings}})
        self.assertEqual((value["minimum_epap"], value["maximum_ipap"]), (4.0, 15.0))
        value = entity_values({"realtime": {"mode": 6, "pressure_unit": 255}, "settings": {6: settings}})
        self.assertNotIn("minimum_epap", value)

    def test_hci_acl_fragment_reassembly(self):
        att = b"\x1b\x18\x00example"
        payload = struct.pack("<HH", len(att), 4) + att
        packets = [struct.pack("<BHH", 2, 0x2201, 6) + payload[:6],
                   struct.pack("<BHH", 2, 0x1201, len(payload) - 6) + payload[6:]]
        capture = b"btsnoop\0" + struct.pack(">II", 1, 1002)
        for packet in packets:
            capture += struct.pack(">IIIIQ", len(packet), len(packet), 1, 0, 1) + packet
        result = extract(capture)
        self.assertEqual(result["att_records"][0]["att"], att.hex())
        self.assertEqual(result["incomplete_acl_messages"], 0)
        with self.assertRaises(ValueError):
            extract(capture[:-1])

if __name__ == "__main__":
    unittest.main()
