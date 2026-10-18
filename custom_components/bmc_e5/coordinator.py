"""Connection lifecycle and push state."""
import asyncio
from contextlib import suppress
import logging

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .protocol import parse_realtime
from .transport import run_connection
from .values import therapy_settings
from .control import REMINDER_OFFSETS, validate_action, validate_structure

LOGGER = logging.getLogger(__name__)


def connection_factory(hass, address):
    async def connect():
        device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
        if device is None:
            raise TimeoutError("Device not reachable by a connectable Bluetooth adapter")
        return await establish_connection(BleakClientWithServiceCache, device, "BMC E5", max_attempts=2)
    return connect


class BmcCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, profile):
        super().__init__(hass, LOGGER, name=DOMAIN, config_entry=entry)
        self.address = entry.data["address"]
        self.profile = profile
        self.data = {"connected": False}
        self._stop = asyncio.Event()
        self._task = None
        self._control_queue = None
        self._control_busy = False

    async def async_control(self, key, value):
        validate_action(key, value)
        if not self.data.get("connected") or self._control_queue is None:
            raise HomeAssistantError("呼吸机未连接，未发送命令")
        if self._control_busy:
            raise HomeAssistantError("上一操作尚未完成，请稍后再试")
        self._control_busy = True
        result = asyncio.get_running_loop().create_future()
        self._control_queue.put_nowait((key, value, result))
        try:
            await asyncio.wait_for(result, timeout=30)
        except (TimeoutError, ValueError) as err:
            raise HomeAssistantError("设备未确认操作。请检查设备当前状态；不会自动重试") from err
        finally:
            self._control_busy = False

    def start(self):
        self._task = self.config_entry.async_create_background_task(self.hass, self._run(), "BMC E5 connection")

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _message(self, message, received_at):
        data = dict(self.data)
        if message.command == 0x06EA:
            raw = parse_realtime(message)
            data.update(connected=True, realtime=raw, last_update=received_at)
        elif message.command == 0x06AA:
            mode, settings = therapy_settings(message.payload)
            data["settings"] = {**data.get("settings", {}), mode: settings}
        elif message.command == 0x06A8:
            validate_structure(message.payload, b"SS\x00\x01\x01\x01")
            data["system_settings"] = {"water_reminder": message.payload[43]}
            for key, offset in REMINDER_OFFSETS.items():
                value = int.from_bytes(message.payload[offset:offset + 4], "little")
                data["system_settings"][key] = value * 100 if value <= 15 else None
        self.async_set_updated_data(data)

    async def _run(self):
        delay = 5
        while not self._stop.is_set():
            try:
                # Clear current data each session so old settings never appear fresh.
                self.async_set_updated_data({"connected": False, "last_update": self.data.get("last_update")})
                self._control_queue = asyncio.Queue(maxsize=1)
                await run_connection(connection_factory(self.hass, self.address), self.profile, self._message, self._stop,
                                     control_queue=self._control_queue)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                # Do not log exception text: BLE stacks may include addresses/payloads.
                LOGGER.debug("E5 reconnect needed (%s)", type(err).__name__)
            finally:
                if self._control_queue is not None:
                    while not self._control_queue.empty():
                        _, _, pending = self._control_queue.get_nowait()
                        if not pending.done():
                            pending.set_exception(ValueError("Connection ended"))
                self._control_queue = None
                self.async_set_updated_data({**self.data, "connected": False})
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 60)
