"""Conservative entity values verified on the supplied E5/AutoS device."""
import struct
from .protocol import ProtocolError


def therapy_settings(payload):
    if len(payload) != 260 or payload[4:10] != b"TS\x00\x01\x01\x01":
        raise ProtocolError("Unsupported therapy settings version")
    mode = int.from_bytes(payload[:4], "little")
    data = payload[4:]
    if data[8] != mode:
        raise ProtocolError("Therapy mode mismatch")
    return mode, {"minimum_epap_raw": struct.unpack_from("<H", data, 14)[0],
                  "maximum_ipap_raw": struct.unpack_from("<H", data, 22)[0],
                  "maximum_ramp_minutes": struct.unpack_from("<H", data, 128)[0],
                  "ramp_minutes": struct.unpack_from("<H", data, 130)[0],
                  "reslex": data[134],
                  "humidifier": -1 if data[138] & 4 else data[135],
                  "heated_tube": -1 if data[138] & 8 else data[136],
                  "auto_start": data[140], "auto_stop": data[141],
                  "mask_type": data[142], "tube_type": data[143], "delayed_off": data[144]}


def entity_values(data):
    raw = data.get("realtime", {})
    mode = raw.get("mode")
    values = {"mode": "AutoS" if mode == 6 else None,
              "work_state": raw.get("work_state"),
              "pressure_unit": "cmH₂O" if raw.get("pressure_unit") == 1 else None}
    values.update(data.get("system_settings", {}))
    # Only the AutoS pressure fields and unit code 1 have been cross-checked.
    settings = data.get("settings", {}).get(mode)
    if settings:
        values.update(settings)
        if mode == 6 and raw.get("pressure_unit") == 1:
            values.update(minimum_epap=settings["minimum_epap_raw"] / 100,
                          maximum_ipap=settings["maximum_ipap_raw"] / 100)
    return values
