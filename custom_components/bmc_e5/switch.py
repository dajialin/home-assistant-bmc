"""Explicit user controls; state always comes from device readback."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory
from .entity import BmcEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BmcSwitch(entry.runtime_data, key, name) for key, name in (
        ("therapy", "治疗启停"), ("preheat", "预热"), ("auto_start", "自动启动设置"),
        ("auto_stop", "自动停机设置"), ("delayed_off", "延时关机设置"),
        ("water_reminder", "水量提示"))])


class BmcSwitch(BmcEntity, SwitchEntity):
    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key + "_control", name)
        self.control_key = key
        if key not in ("therapy", "preheat"):
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self):
        raw = self.coordinator.data.get("realtime", {})
        states = (0, 1) if self.control_key == "therapy" else (0, 6) if self.control_key == "preheat" else (0,)
        return super().available and raw.get("mode") == 6 and raw.get("work_state") in states

    @property
    def is_on(self):
        if self.control_key in ("therapy", "preheat"):
            state = self.coordinator.data.get("realtime", {}).get("work_state")
            on_state = 1 if self.control_key == "therapy" else 6
            return state == on_state if state in (0, on_state) else None
        from .values import entity_values
        value = entity_values(self.coordinator.data).get(self.control_key)
        return bool(value) if value in (0, 1) else None

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_control(self.control_key, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_control(self.control_key, False)
