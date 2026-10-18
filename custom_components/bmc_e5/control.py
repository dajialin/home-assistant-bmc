"""Bounded control transactions; no radio access or automatic write retries."""
import struct

from .protocol import ProtocolError, parse_realtime
from .values import therapy_settings

SETTING_OFFSETS = {"reslex": 134, "auto_start": 140, "auto_stop": 141,
                   "mask_type": 142, "tube_type": 143, "delayed_off": 144}
REMINDER_OFFSETS = {"filter_reminder": 8, "tube_reminder": 12, "mask_reminder": 16, "maintenance_reminder": 20}


def validate_action(key, value):
    if key in ("therapy", "preheat", "auto_start", "auto_stop", "delayed_off", "water_reminder"):
        if type(value) is not bool:
            raise ProtocolError("Expected a boolean")
    elif key in REMINDER_OFFSETS:
        if type(value) is not int or not 0 <= value <= 1500 or value % 100:
            raise ProtocolError("Reminder must be off (0) or 100 to 1500 hours in 100 hour steps")
    elif key in ("humidifier", "heated_tube"):
        if type(value) is not int or value not in range(-1, 6):
            raise ProtocolError("Expected automatic (-1), off (0), or level 1 to 5")
    elif key == "tube_type":
        if type(value) is not int or value not in (2, 3):
            raise ProtocolError("Unsupported tube type")
    elif key in ("reslex", "mask_type"):
        if type(value) is not int or value not in range(4):
            raise ProtocolError("Unsupported relief level")
    elif key == "ramp_minutes":
        if type(value) is not int or not 0 <= value <= 60 or value % 5:
            raise ProtocolError("Ramp must be 0 to 60 minutes in 5 minute steps")
    else:
        raise ProtocolError("Unsupported control")


def validate_structure(data, prefix):
    if len(data) != 256 or data[:6] != prefix:
        raise ProtocolError("Unsupported settings structure")
    if sum(data[:-2]) & 0xFFFF != int.from_bytes(data[-2:], "little"):
        raise ProtocolError("Settings checksum mismatch")


def update_setting(payload, key, value):
    """Patch one field in a fresh complete snapshot; preserve every other byte."""
    validate_action(key, value)
    mode, _ = therapy_settings(payload)
    data = bytearray(payload[4:])
    validate_structure(data, b"TS\x00\x01\x01\x01")
    if mode != 6:
        raise ProtocolError("Controls currently support AutoS only")
    if key == "ramp_minutes":
        maximum = struct.unpack_from("<H", data, 128)[0]
        if value > maximum:
            raise ProtocolError("Ramp exceeds device maximum")
        struct.pack_into("<H", data, 130, value)
    elif key in ("humidifier", "heated_tube"):
        offset, mask = (135, 4) if key == "humidifier" else (136, 8)
        if data[offset] not in range(6):
            raise ProtocolError("Device does not expose a supported comfort level")
        # Auto uses a separate bit and retains the previous manual level.
        if value == -1:
            data[138] |= mask
        else:
            data[138] &= ~mask
            data[offset] = value
    elif key in SETTING_OFFSETS:
        supported = range(4) if key in ("reslex", "mask_type") else (2, 3) if key == "tube_type" else (0, 1)
        if data[SETTING_OFFSETS[key]] not in supported:
            raise ProtocolError("Device does not expose this setting")
        data[SETTING_OFFSETS[key]] = int(value)
    else:
        raise ProtocolError("Unsupported setting")
    data[-2:] = (sum(data[:-2]) & 0xFFFF).to_bytes(2, "little")
    return payload[:4] + bytes(data)


def update_system(payload, key, value):
    validate_action(key, value)
    validate_structure(payload, b"SS\x00\x01\x01\x01")
    if payload[28] != 6:
        raise ProtocolError("Unsupported system setting")
    data = bytearray(payload)
    if key == "water_reminder" and payload[43] in (0, 1):
        data[43] = int(value)
    elif key in REMINDER_OFFSETS and struct.unpack_from("<I", data, REMINDER_OFFSETS[key])[0] <= 15:
        struct.pack_into("<I", data, REMINDER_OFFSETS[key], value // 100)
    else:
        raise ProtocolError("Unsupported system setting")
    data[-2:] = (sum(data[:-2]) & 0xFFFF).to_bytes(2, "little")
    return bytes(data)


class ControlTransaction:
    """Fresh read -> one write -> matching ACK -> fresh readback.

    Setting writes follow the App's therapy then unchanged-system sequence.
    Errors are terminal: the caller disconnects and never replays the operation.
    """

    def __init__(self, key, value):
        validate_action(key, value)
        self.key, self.value = key, value
        self.phase = "snapshot"
        self.system = self.therapy = self.realtime = None
        self.expected = None
        self.done = False

    def begin(self):
        return (0x81D0, b"")

    def receive(self, message):
        if message.total_blocks != 1 or message.block_number != 1:
            raise ProtocolError("Segmented control response unsupported")
        if message.command == 0x06EA:
            self.realtime = parse_realtime(message)
            if self.realtime["mode"] != 6:
                raise ProtocolError("Therapy mode changed during control")
            if self.key not in ("therapy", "preheat") and self.realtime["work_state"] != 0:
                raise ProtocolError("Change settings only while the device is in standby")
        if self.phase in ("snapshot", "readback"):
            if message.command == 0x06A8:
                validate_structure(message.payload, b"SS\x00\x01\x01\x01")
                if message.payload[28] != 6:
                    raise ProtocolError("Unsupported system mode")
                self.system = message.payload
            elif message.command == 0x06AA:
                mode, _ = therapy_settings(message.payload)
                if mode == 6:
                    validate_structure(message.payload[4:], b"TS\x00\x01\x01\x01")
                    self.therapy = message.payload
        if self.phase == "snapshot" and all(x is not None for x in (self.system, self.therapy, self.realtime)):
            allowed_states = (0, 6) if self.key == "preheat" else (0, 1)
            if self.realtime["work_state"] not in allowed_states:
                raise ProtocolError("Unsupported current running state")
            if self.key == "therapy":
                if self.realtime["work_state"] == int(self.value):
                    self.done = True
                    return []
                self.expected = struct.pack("<II", int(self.value), 6)
                self.phase = "work_ack"
                return [(0x8194, self.expected)]
            if self.key == "preheat":
                if self.realtime["work_state"] == (6 if self.value else 0):
                    self.done = True
                    return []
                self.expected = bytes([int(self.value)])
                self.phase = "heat_ack"
                return [(0x81C0, self.expected)]
            if self.realtime["work_state"] != 0:
                raise ProtocolError("Change settings only in standby")
            system_key = self.key == "water_reminder" or self.key in REMINDER_OFFSETS
            self.expected = self.therapy if system_key else update_setting(self.therapy, self.key, self.value)
            self.expected_system = update_system(self.system, self.key, self.value) if system_key else self.system
            if self.expected == self.therapy and self.expected_system == self.system:
                self.done = True
                return []
            self.original_system = self.system
            if self.key in REMINDER_OFFSETS:
                self.phase = "system_ack"
                return [(0x82B8, self.expected_system)]
            self.phase = "therapy_ack"
            return [(0x82BA, self.expected + bytes(8))]
        if self.phase == "work_ack" and message.command == 0x8195:
            self._ack(message, self.expected)
            self.phase = "work_readback"
        elif self.phase == "heat_ack" and message.command == 0x81C1:
            self._ack(message, self.expected)
            self.phase = "heat_readback"
        elif self.phase == "heat_readback" and message.command == 0x06EA:
            self.done = self.realtime["work_state"] == (6 if self.value else 0)
        elif self.phase == "work_readback" and message.command == 0x06EA:
            self.done = self.realtime["work_state"] == int(self.value)
        elif self.phase == "therapy_ack" and message.command == 0x82BB:
            self._ack(message, self.expected)
            self.phase = "system_ack"
            return [(0x82B8, self.expected_system)]
        elif self.phase == "system_ack" and message.command == 0x82B9:
            self._ack(message, self.expected_system)
            self.phase = "readback"
            self.system = self.therapy = self.realtime = None
            return [(0x81D0, b"")]
        elif self.phase == "readback" and all(x is not None for x in (self.system, self.therapy, self.realtime)):
            if self.system != self.expected_system or self.therapy != self.expected:
                raise ProtocolError("Device readback differs; inspect device settings")
            self.done = True
        return []

    @staticmethod
    def _ack(message, expected):
        if message.param1 != 1 or message.param2 != 0 or message.payload != expected:
            raise ProtocolError("Device rejected control or returned different settings")
