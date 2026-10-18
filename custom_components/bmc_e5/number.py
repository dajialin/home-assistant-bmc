from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity import EntityCategory
from .entity import BmcEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([RampNumber(entry.runtime_data, "ramp_minutes_control", "延时升压设置"),
        *[ReminderNumber(entry.runtime_data, key, name) for key, name in (
            ("filter_reminder", "空气滤芯提醒周期"), ("mask_reminder", "面罩提醒周期"),
            ("tube_reminder", "管路提醒周期"), ("maintenance_reminder", "深度保养提醒周期"))]])


class RampNumber(BmcEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self):
        raw = self.coordinator.data.get("realtime", {})
        return super().available and raw.get("mode") == 6 and raw.get("work_state") == 0 and self.native_value is not None

    @property
    def native_max_value(self):
        return min(60, self.coordinator.data.get("settings", {}).get(6, {}).get("maximum_ramp_minutes", 0))

    @property
    def native_value(self):
        return self.coordinator.data.get("settings", {}).get(6, {}).get("ramp_minutes")

    async def async_set_native_value(self, value):
        if not float(value).is_integer():
            raise ValueError("Expected whole minutes")
        await self.coordinator.async_control("ramp_minutes", int(value))


class ReminderNumber(BmcEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 1500
    _attr_native_step = 100
    _attr_native_unit_of_measurement = "h"
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key + "_control", name)
        self.control_key = key

    @property
    def available(self):
        raw = self.coordinator.data.get("realtime", {})
        return super().available and raw.get("mode") == 6 and raw.get("work_state") == 0 and self.native_value is not None

    @property
    def native_value(self):
        return self.coordinator.data.get("system_settings", {}).get(self.control_key)

    async def async_set_native_value(self, value):
        if not float(value).is_integer():
            raise ValueError("Expected whole hours")
        await self.coordinator.async_control(self.control_key, int(value))
