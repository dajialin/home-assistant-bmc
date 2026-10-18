"""Synthetic transaction tests; private capture comparison is separate."""
import struct
import unittest

from custom_components.bmc_e5.control import ControlTransaction, update_setting, update_system, validate_action
from custom_components.bmc_e5.protocol import Message, ProtocolError


def checksum(data):
    data[-2:] = (sum(data[:-2]) & 0xFFFF).to_bytes(2, "little")
    return bytes(data)


def snapshot():
    therapy = bytearray(b"\xff" * 256)
    therapy[:6] = b"TS\x00\x01\x01\x01"
    therapy[8] = 6
    struct.pack_into("<HH", therapy, 128, 60, 15)
    therapy[134] = 0
    therapy[135:140] = bytes([2, 5, 0, 12, 0])
    therapy[140:145] = bytes([1, 1, 1, 2, 1])
    system = bytearray(b"\xff" * 256)
    system[:6] = b"SS\x00\x01\x01\x01"
    system[28] = 6
    struct.pack_into("<IIII", system, 8, 15, 15, 15, 15)
    system[43] = 1
    return struct.pack("<I", 6) + checksum(therapy), checksum(system)


def message(command, payload=b"", param1=0):
    return Message(1, 0, command, param1, 0, 1, 1, 19, payload)


def realtime(state=0, mode=6):
    data = bytearray(70)
    data[:4] = b"FL\x00\x01"
    struct.pack_into("<HH", data, 28, mode, state)
    return message(0x06EA, bytes(data))


def prime(tx, state=0):
    therapy, system = snapshot()
    assert tx.begin() == (0x81D0, b"")
    assert not tx.receive(message(0x06AA, therapy))
    assert not tx.receive(message(0x06A8, system))
    return tx.receive(realtime(state))


class ControlTests(unittest.TestCase):
    def test_reminder_is_system_only_and_preserves_usage(self):
        therapy, system = snapshot()
        tx = ControlTransaction("filter_reminder", 1400)
        expected = update_system(system, "filter_reminder", 1400)
        self.assertEqual({i for i, (a, b) in enumerate(zip(system, expected)) if a != b}, {8, 254})
        self.assertEqual(prime(tx), [(0x82B8, expected)])
        self.assertEqual(tx.receive(message(0x82B9, expected, 1)), [(0x81D0, b"")])
        tx.receive(message(0x06AA, therapy))
        tx.receive(message(0x06A8, expected))
        tx.receive(realtime())
        self.assertTrue(tx.done)
        self.assertEqual(update_system(expected, "filter_reminder", 1500), system)

    def test_automatic_bits_preserve_other_flags_and_manual_levels(self):
        old, _ = snapshot()
        for key, offset, bit, manual in [("humidifier", 139, 4, 2), ("heated_tube", 140, 8, 5)]:
            edited = update_setting(old, key, manual)
            self.assertEqual(edited[142], old[142] & ~bit)
            self.assertEqual(edited[offset], manual)
            self.assertEqual(update_setting(edited, key, -1), old)
            off = update_setting(old, key, 0)
            self.assertEqual(off[offset], 0)
            changed = {i for i, (a, b) in enumerate(zip(old, off)) if a != b}
            self.assertTrue(changed <= {offset, 142, 258, 259})

    def test_water_changes_only_reminder_and_checksum(self):
        therapy, system = snapshot()
        edited = update_system(system, "water_reminder", False)
        self.assertEqual({i for i, (a, b) in enumerate(zip(system, edited)) if a != b}, {43, 254})
        self.assertEqual(update_system(edited, "water_reminder", True), system)
        tx = ControlTransaction("water_reminder", False)
        self.assertEqual(prime(tx), [(0x82BA, therapy + bytes(8))])
        self.assertEqual(tx.receive(message(0x82BB, therapy, 1)), [(0x82B8, edited)])
        tx.receive(message(0x82B9, edited, 1))
        tx.receive(message(0x06AA, therapy))
        tx.receive(message(0x06A8, edited))
        tx.receive(realtime())
        self.assertTrue(tx.done)

    def test_patch_preserves_every_other_field(self):
        old, _ = snapshot()
        for key, value, offsets in [("reslex", 3, {138}), ("ramp_minutes", 20, {134, 135}),
                                    ("auto_start", False, {144}), ("auto_stop", False, {145}),
                                    ("delayed_off", False, {148}), ("tube_type", 3, {147}),
                                    ("mask_type", 0, {146})]:
            new = update_setting(old, key, value)
            changed = {i for i, (a, b) in enumerate(zip(old, new)) if a != b}
            self.assertTrue(changed <= offsets | {258, 259}, (key, changed))
            self.assertTrue(changed & offsets)
            self.assertEqual(sum(new[4:258]) & 0xFFFF, int.from_bytes(new[-2:], "little"))

    def test_bad_input_and_snapshot_rejected(self):
        old, _ = snapshot()
        for key, value in [("mode", 2), ("pressure", 25), ("reslex", 4), ("reslex", True),
                           ("therapy", 1), ("ramp_minutes", float("nan")), ("ramp_minutes", 61)]:
            with self.assertRaises(ProtocolError):
                validate_action(key, value)
        bad = bytearray(old)
        bad[10] ^= 1
        with self.assertRaisesRegex(ProtocolError, "checksum"):
            update_setting(bytes(bad), "reslex", 1)
        bad = bytearray(old[4:])
        struct.pack_into("<H", bad, 128, 10)
        with self.assertRaisesRegex(ProtocolError, "maximum"):
            update_setting(old[:4] + checksum(bad), "ramp_minutes", 15)

    def test_setting_requires_ack_and_full_readback(self):
        tx = ControlTransaction("reslex", 3)
        operations = prime(tx)
        self.assertEqual(operations, [(0x82BA, tx.expected + bytes(8))])
        expected, system = tx.expected, tx.original_system
        self.assertFalse(tx.done)
        self.assertEqual(tx.receive(message(0x82BB, expected, 1)), [(0x82B8, system)])
        self.assertEqual(tx.receive(message(0x82B9, system, 1)), [(0x81D0, b"")])
        tx.receive(message(0x06AA, expected))
        tx.receive(message(0x06A8, system))
        self.assertFalse(tx.done)
        tx.receive(realtime())
        self.assertTrue(tx.done)

    def test_rejected_ack_never_writes_system(self):
        tx = ControlTransaction("reslex", 3)
        prime(tx)
        with self.assertRaisesRegex(ProtocolError, "rejected"):
            tx.receive(message(0x82BB, tx.expected, 0))
        self.assertFalse(tx.done)

    def test_readback_mismatch_fails(self):
        tx = ControlTransaction("reslex", 3)
        prime(tx)
        tx.receive(message(0x82BB, tx.expected, 1))
        tx.receive(message(0x82B9, tx.original_system, 1))
        old, system = snapshot()
        tx.receive(message(0x06AA, old))
        tx.receive(message(0x06A8, system))
        with self.assertRaisesRegex(ProtocolError, "readback differs"):
            tx.receive(realtime())

    def test_running_settings_and_mode_change_fail(self):
        with self.assertRaises(ProtocolError):
            prime(ControlTransaction("reslex", 3), 1)
        tx = ControlTransaction("therapy", True)
        prime(tx)
        with self.assertRaisesRegex(ProtocolError, "mode changed"):
            tx.receive(realtime(1, 2))

    def test_therapy_and_heat_wait_for_state_after_ack(self):
        for key, command, reply, state in [("therapy", 0x8194, 0x8195, 1), ("preheat", 0x81C0, 0x81C1, 6)]:
            tx = ControlTransaction(key, True)
            self.assertEqual(prime(tx)[0][0], command)
            tx.receive(realtime(state))
            self.assertFalse(tx.done)
            tx.receive(message(reply, tx.expected, 1))
            self.assertFalse(tx.done)
            tx.receive(realtime(state))
            self.assertTrue(tx.done)

    def test_noop_never_writes(self):
        for key, value in [("therapy", False), ("preheat", False), ("reslex", 0), ("auto_start", True)]:
            tx = ControlTransaction(key, value)
            self.assertEqual(prime(tx), [])
            self.assertTrue(tx.done)


if __name__ == "__main__":
    unittest.main()
