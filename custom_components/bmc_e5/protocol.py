"""E5 new-protocol primitives verified against LightTrip 1.02.04 captures.

No network access or device commands. RSA material is supplied by the caller;
this module contains no vendor key or user credential.
"""
from __future__ import annotations

import binascii
import struct
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

SERVICE_UUID = "0000fe60-0000-1000-8000-00805f9b34fb"
DATA_UUID = "0000fe64-0000-1000-8000-00805f9b34fb"
REALTIME_COMMAND = 0x06EA


class ProtocolError(ValueError):
    """Malformed, unsupported or unauthenticated data."""


class FrameBuffer:
    """Reassemble characteristic values; instantiate per connection/direction."""

    def __init__(self, max_payload: int = 4096) -> None:
        self._buffer = bytearray()
        self.max_payload = max_payload

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        frames = []
        try:
            while len(self._buffer) >= 4:
                magic, algorithm, length = struct.unpack_from(">BBH", self._buffer)
                if magic != 2 or algorithm not in (1, 5):
                    raise ProtocolError("Unsupported frame header")
                if length == 0 or length > self.max_payload:
                    raise ProtocolError("Invalid frame length")
                if algorithm == 1 and length != 128:
                    raise ProtocolError("Unexpected RSA block length")
                if algorithm == 5 and length % 16:
                    raise ProtocolError("Invalid AES block length")
                if len(self._buffer) < length + 4:
                    break
                frames.append(bytes(self._buffer[:length + 4]))
                del self._buffer[:length + 4]
        except ProtocolError:
            self.reset()
            raise
        return frames


@dataclass(frozen=True)
class Message:
    major: int
    minor: int
    command: int
    param1: int
    param2: int
    total_blocks: int
    block_number: int
    message_id: int
    payload: bytes

    @classmethod
    def parse(cls, data: bytes) -> Message:
        if len(data) < 18:
            raise ProtocolError("Short message")
        if binascii.crc_hqx(data[:-2], 0) != int.from_bytes(data[-2:], "big"):
            raise ProtocolError("CRC mismatch")
        values = struct.unpack_from(">BBHHHHHI", data)
        if values[0:2] != (1, 0):
            raise ProtocolError("Unsupported message version")
        if values[5] < 1 or not 1 <= values[6] <= values[5]:
            raise ProtocolError("Invalid block numbering")
        return cls(*values, data[16:-2])


class SessionCodec:
    """Decode a capture using the RSA key and per-connection AES session key.

    Direction 0 is App -> device (RSA type-1 signature), 1 is device -> App
    (RSA type-2 ciphertext). This is an offline decoder, not a pairing bypass.
    """

    def __init__(self, modulus: int, private_exponent: int) -> None:
        self.modulus = modulus
        self.private_exponent = private_exponent
        self._aes_key: bytes | None = None
        self._message_id = secrets.randbelow(65536)

    def reset(self) -> None:
        self._aes_key = None

    def _encode(self, command: int, param1: int, param2: int, payload: bytes, *, rsa: bool = False) -> bytes:
        """Internal encoder; callers use ReadOnlySession's fixed operations."""
        self._message_id = (self._message_id + 1) & 0xFFFFFFFF
        clear = struct.pack(">BBHHHHHI", 1, 0, command, param1, param2, 1, 1, self._message_id) + payload
        clear += binascii.crc_hqx(clear, 0).to_bytes(2, "big")
        if rsa:
            if len(clear) > 117:
                raise ProtocolError("RSA message too large")
            padded = b"\0\x01" + b"\xff" * (125 - len(clear)) + b"\0" + clear
            encrypted = pow(int.from_bytes(padded, "big"), self.private_exponent, self.modulus).to_bytes(128, "big")
        else:
            if self._aes_key is None:
                raise ProtocolError("Session not initialized")
            padder = PKCS7(128).padder()
            padded = padder.update(clear) + padder.finalize()
            encryptor = Cipher(algorithms.AES(self._aes_key), modes.CBC(bytes(16))).encryptor()
            encrypted = encryptor.update(padded) + encryptor.finalize()
        return struct.pack(">BBH", 2, 1 if rsa else 5, len(encrypted)) + encrypted

    def decode(self, frame: bytes, direction: int) -> Message:
        if direction not in (0, 1):
            raise ProtocolError("Invalid direction")
        if len(frame) < 4 or frame[0] != 2 or len(frame) != int.from_bytes(frame[2:4], "big") + 4:
            raise ProtocolError("Frame length mismatch")
        encrypted = frame[4:]
        if frame[1] == 1:
            if len(encrypted) != 128 or int.from_bytes(encrypted, "big") >= self.modulus:
                raise ProtocolError("Invalid RSA block")
            exponent = 65537 if direction == 0 else self.private_exponent
            padded = pow(int.from_bytes(encrypted, "big"), exponent, self.modulus).to_bytes(128, "big")
            padding_type = 1 if direction == 0 else 2
            end = padded.find(b"\0", 2)
            if padded[:2] != bytes((0, padding_type)) or end < 10:
                raise ProtocolError("RSA padding mismatch")
            if padding_type == 1 and padded[2:end] != b"\xff" * (end - 2):
                raise ProtocolError("Invalid RSA signature padding")
            clear = padded[end + 1:]
        elif frame[1] == 5:
            if self._aes_key is None:
                raise ProtocolError("Session key missing; capture the handshake")
            if not encrypted or len(encrypted) % 16:
                raise ProtocolError("Invalid AES block")
            decryptor = Cipher(algorithms.AES(self._aes_key), modes.CBC(bytes(16))).decryptor()
            padded = decryptor.update(encrypted) + decryptor.finalize()
            try:
                unpadder = PKCS7(128).unpadder()
                clear = unpadder.update(padded) + unpadder.finalize()
            except ValueError as err:
                raise ProtocolError("AES padding mismatch") from err
        else:
            raise ProtocolError("Unsupported cipher")
        message = Message.parse(clear)
        if direction == 0 and message.command == 0:
            self.reset()
        if direction == 0 and message.command == 2:
            if frame[1] != 1 or len(message.payload) != 24:
                raise ProtocolError("Invalid session key exchange")
            self._aes_key = message.payload
        return message


def parse_realtime(message: Message) -> dict[str, int]:
    """Return raw device values; no unverified unit conversions or state guesses."""
    payload = message.payload
    if message.command != REALTIME_COMMAND or len(payload) != 70:
        raise ProtocolError("Unsupported realtime payload")
    if payload[:4] != b"FL\x00\x01":
        raise ProtocolError("Unsupported realtime structure version")
    if message.total_blocks != 1 or message.block_number != 1:
        raise ProtocolError("Segmented realtime structure not supported")
    result = {"tick": int.from_bytes(payload[4:8], "little")}
    shorts = ("spo2", "pulse_rate", "leak", "total_leak", "tidal_volume",
              "minute_ventilation", "respiratory_rate", "ie", "inspiratory_time",
              "expiratory_time", "mode", "work_state", "target_epap", "target_ipap", "target_volume")
    result.update(zip(shorts, struct.unpack_from("<15H", payload, 8), strict=True))
    byte_names = ("pressure_unit", "gas_standard", "sd_state", "heated_tube_state",
                  "module_type", "module_state", "module_network_state", "module_quality",
                  "spo2_state", "phone_app_state", "maintenance_date", "home_key_state")
    result.update(zip(byte_names, payload[38:50], strict=True))
    result.update(zip(("display_pressure", "mute_remaining", "ramp_remaining",
                       "ramp_down_remaining", "heat_up_remaining", "cool_down_remaining"),
                      struct.unpack_from("<6H", payload, 50), strict=True))
    result.update(auto_brightness=payload[62], heater_state=payload[63],
                  flow=int.from_bytes(payload[64:66], "little"),
                  spo2_connected=payload[66], is_release=payload[67])
    return result


class ReadOnlySession:
    """Fixed handshake, read-all, heartbeat and receipt acknowledgements only.

    Does not replay captured requests. No public arbitrary-command interface.
    """

    _ACK = {0x06A4: 0x06A5, 0x06A6: 0x06A7, 0x06A8: 0x06A9,
            0x06AA: 0x06AB, 0x06B8: 0x06B9, 0x057E: 0x057F,
            0x0584: 0x0585, 0x0588: 0x0589}

    def __init__(self, modulus: int, private_exponent: int) -> None:
        self.codec = SessionCodec(modulus, private_exponent)
        self.state = "new"
        self._challenge = (secrets.randbelow(98) + 1, secrets.randbelow(98) + 1)
        self._session_key = secrets.token_hex(12).encode("ascii")

    def begin(self) -> bytes:
        if self.state != "new":
            raise ProtocolError("Handshake already started")
        self.state = "challenge"
        return self.codec._encode(0, 0, 0, struct.pack(">II", *self._challenge), rsa=True)

    def receive(self, frame: bytes) -> tuple[Message, list[bytes]]:
        message = self.codec.decode(frame, 1)
        commands = []
        if self.state == "challenge":
            a, b = self._challenge
            if message.command != 1 or message.param1 != 1 or message.payload != struct.pack(">II", a + 1, b - 1):
                raise ProtocolError("Device challenge failed")
            commands.append(self.codec._encode(2, 24, 1, self._session_key, rsa=True))
            self.codec._aes_key = self._session_key
            self.state = "key_confirmation"
        elif self.state == "key_confirmation":
            if frame[1] != 5 or message.command != 3 or message.param1 != 1:
                raise ProtocolError("Session key not confirmed")
            self.state = "reading"
            commands.append(self.codec._encode(0x81D0, 0, 0, b""))
        elif self.state in ("reading", "ready"):
            if frame[1] != 5:
                raise ProtocolError("Unexpected RSA data after handshake")
            if message.command == 0x06A4:
                if len(message.payload) != 256 or message.payload[:24].rstrip(b"\0") != b"E5 B25A Plus":
                    raise ProtocolError("Unsupported device model")
                self.state = "ready"
            if message.command in self._ACK:
                payload = b""
                param2 = 1 if message.command == 0x057E else 0
                if message.command == 0x06AA:
                    if len(message.payload) != 260:
                        raise ProtocolError("Invalid therapy structure")
                    payload = message.payload[:4]
                commands.append(self.codec._encode(self._ACK[message.command], 1, param2, payload))
        else:
            raise ProtocolError("Handshake not started")
        return message, commands

    def heartbeat(self) -> bytes:
        if self.state not in ("reading", "ready"):
            raise ProtocolError("Handshake incomplete")
        return self.codec._encode(8, 0, 0, b"")
