"""Tests against real HA classes, with simulated transports (no radio use)."""
import asyncio
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

HAS_HA = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(HAS_HA, "Run in the HA 2026.8.3 environment")
class EntityTests(unittest.TestCase):
    def test_controls_are_not_optimistic_and_settings_require_standby(self):
        from custom_components.bmc_e5.switch import BmcSwitch
        from custom_components.bmc_e5.select import ReliefSelect
        from custom_components.bmc_e5.number import RampNumber, ReminderNumber
        coordinator = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", last_update_success=True,
            data={"connected": True, "realtime": {"mode": 6, "work_state": 0},
                  "settings": {6: {"reslex": 0, "ramp_minutes": 15, "maximum_ramp_minutes": 60}},
                  "system_settings": {"filter_reminder": 1500}})
        therapy = BmcSwitch(coordinator, "therapy", "Therapy")
        relief = ReliefSelect(coordinator, "reslex_control", "Relief")
        ramp = RampNumber(coordinator, "ramp_minutes_control", "Ramp")
        self.assertFalse(therapy.is_on)
        self.assertTrue(relief.available)
        self.assertEqual(ramp.native_max_value, 60)
        self.assertEqual(ramp.state, 15)
        reminder = ReminderNumber(coordinator, "filter_reminder", "Filter")
        self.assertEqual(reminder.state, 1500)
        coordinator.data["realtime"]["work_state"] = 1
        self.assertTrue(therapy.is_on)
        self.assertFalse(relief.available)
        self.assertFalse(ramp.available)
        coordinator.data["connected"] = False
        self.assertFalse(therapy.available)

    def test_disconnect_is_not_therapy_off(self):
        from custom_components.bmc_e5.binary_sensor import ConnectionSensor, TherapySensor
        coordinator = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", last_update_success=True,
                                      data={"connected": True, "realtime": {"work_state": 1}})
        therapy = TherapySensor(coordinator, "therapy", "Therapy")
        connection = ConnectionSensor(coordinator, "connection", "Connection")
        self.assertTrue(therapy.available)
        self.assertTrue(therapy.is_on)
        coordinator.data["connected"] = False
        self.assertFalse(therapy.available)
        self.assertTrue(connection.available)
        self.assertFalse(connection.is_on)
        coordinator.data["realtime"]["work_state"] = 255
        self.assertIsNone(therapy.is_on)


@unittest.skipUnless(HAS_HA, "Run in the HA 2026.8.3 environment")
class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def make_flow(self):
        from custom_components.bmc_e5.config_flow import BmcConfigFlow
        flow = BmcConfigFlow()
        flow.hass = MagicMock()
        flow.context = {"source": "user"}
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        return flow

    async def test_invalid_address_and_missing_profile(self):
        flow = await self.make_flow()
        result = await flow.async_step_user({"address": "not-a-mac"})
        self.assertEqual(result["errors"], {"address": "invalid_address"})
        flow.hass.async_add_executor_job = AsyncMock(side_effect=FileNotFoundError())
        result = await flow.async_step_user({"address": "AA:BB:CC:DD:EE:FF"})
        self.assertEqual(result["errors"], {"base": "profile_missing"})

    async def test_connection_failure_and_success(self):
        flow = await self.make_flow()
        flow.hass.async_add_executor_job = AsyncMock(return_value=(123, 456))
        with patch("custom_components.bmc_e5.config_flow.run_connection", side_effect=TimeoutError()):
            result = await flow.async_step_user({"address": "aa:bb:cc:dd:ee:ff"})
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        with patch("custom_components.bmc_e5.config_flow.run_connection", new=AsyncMock()):
            result = await flow.async_step_user({"address": "aa:bb:cc:dd:ee:ff"})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], {"address": "AA:BB:CC:DD:EE:FF"})

    async def test_discovery_nonconnectable(self):
        flow = await self.make_flow()
        result = await flow.async_step_bluetooth(SimpleNamespace(connectable=False))
        self.assertEqual(result["reason"], "not_connectable")


@unittest.skipUnless(HAS_HA, "Run in the HA 2026.8.3 environment")
class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_transaction_is_not_queued_for_reconnect(self):
        from custom_components.bmc_e5.transport import run_connection
        from custom_components.bmc_e5.protocol import ProtocolError
        client = MagicMock()
        client.mtu_size = 103
        characteristic = SimpleNamespace(properties=["notify", "write-without-response"], max_write_without_response_size=100)
        client.services.get_service.return_value.get_characteristic.return_value = characteristic
        callbacks = []
        async def notify(characteristic, callback):
            callbacks.append(callback)
        async def write(characteristic, data, **kwargs):
            if data == b"read-all":
                callbacks[0](None, b"\x02\x05\x00\x10" + bytes(16))
        client.start_notify = AsyncMock(side_effect=notify)
        client.write_gatt_char = AsyncMock(side_effect=write)
        client.disconnect = AsyncMock()
        session = MagicMock()
        session.state = "ready"
        session.begin.return_value = b"handshake"
        session.codec._encode.return_value = b"read-all"
        session.receive.side_effect = ProtocolError("Invalid response")
        queue = asyncio.Queue()
        result = asyncio.get_running_loop().create_future()
        queue.put_nowait(("therapy", True, result))
        with patch("custom_components.bmc_e5.transport.ReadOnlySession", return_value=session), \
             patch("custom_components.bmc_e5.transport.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(ProtocolError):
                await run_connection(AsyncMock(return_value=client), (123, 456), MagicMock(), asyncio.Event(), control_queue=queue)
        self.assertTrue(queue.empty())
        self.assertIsInstance(result.exception(), ProtocolError)
        session.codec._encode.assert_called_once_with(0x81D0, 0, 0, b"")
        client.disconnect.assert_awaited_once()

    async def test_registry_removal_is_scoped_to_entry(self):
        from custom_components.bmc_e5 import async_setup_entry
        for owner in ("this-entry", "other-entry"):
            hass = MagicMock()
            hass.async_add_executor_job = AsyncMock(return_value=(123, 456))
            hass.config_entries.async_forward_entry_setups = AsyncMock()
            entry = SimpleNamespace(data={"address": "AA:BB:CC:DD:EE:FF"}, entry_id="this-entry")
            registry = MagicMock()
            registry.async_get_entity_id.return_value = "sensor.old_timestamp"
            registry.async_get.return_value.config_entry_id = owner
            with patch("homeassistant.helpers.entity_registry.async_get", return_value=registry), \
                 patch("custom_components.bmc_e5.coordinator.BmcCoordinator"):
                self.assertTrue(await async_setup_entry(hass, entry))
            if owner == "this-entry":
                registry.async_remove.assert_called_once_with("sensor.old_timestamp")
            else:
                registry.async_remove.assert_not_called()

    async def test_stale_realtime_closes_connection(self):
        from custom_components.bmc_e5.transport import run_connection
        client = MagicMock()
        client.mtu_size = 103
        characteristic = SimpleNamespace(properties=["notify", "write-without-response"], max_write_without_response_size=100)
        client.services.get_service.return_value.get_characteristic.return_value = characteristic
        client.start_notify = AsyncMock()
        client.write_gatt_char = AsyncMock()
        client.disconnect = AsyncMock()
        session = MagicMock()
        session.begin.return_value = b"fake-handshake"
        session.state = "ready"
        clock = SimpleNamespace(time=MagicMock(side_effect=[0, 0, 0, 16, 16]))
        with patch("custom_components.bmc_e5.transport.ReadOnlySession", return_value=session), \
             patch("custom_components.bmc_e5.transport.asyncio.get_running_loop", return_value=clock), \
             patch("custom_components.bmc_e5.transport.asyncio.sleep", new=AsyncMock()):
            with self.assertRaisesRegex(TimeoutError, "Realtime data expired"):
                await run_connection(AsyncMock(return_value=client), (123, 456), MagicMock(), asyncio.Event())
        client.disconnect.assert_awaited_once()

    async def test_cancel_releases_gatt(self):
        from custom_components.bmc_e5.transport import run_connection
        client = MagicMock()
        client.mtu_size = 103
        characteristic = SimpleNamespace(properties=["notify", "write-without-response"], max_write_without_response_size=100)
        client.services.get_service.return_value.get_characteristic.return_value = characteristic
        client.start_notify = AsyncMock()
        client.write_gatt_char = AsyncMock()
        client.disconnect = AsyncMock()
        session = MagicMock()
        session.begin.return_value = b"fake-handshake"
        session.state = "challenge"
        with patch("custom_components.bmc_e5.transport.ReadOnlySession", return_value=session):
            task = asyncio.create_task(run_connection(AsyncMock(return_value=client), (123, 456), MagicMock(), asyncio.Event()))
            for _ in range(20):
                if client.write_gatt_char.await_count:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(client.write_gatt_char.await_count)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        client.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
