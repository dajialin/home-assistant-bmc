from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from .entity import BmcEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ReliefSelect(entry.runtime_data, "reslex_control", "呼吸减压设置"),
        ComfortSelect(entry.runtime_data, "humidifier", "湿化器设置"),
        ComfortSelect(entry.runtime_data, "heated_tube", "加热管路设置"),
        EquipmentSelect(entry.runtime_data, "tube_type", "管路类型", {"15 mm": 3, "19 mm": 2}),
        EquipmentSelect(entry.runtime_data, "mask_type", "面罩类型", {"全脸面罩": 0, "鼻面罩": 1, "鼻垫面罩": 2, "其它": 3})])


class ReliefSelect(BmcEntity, SelectEntity):
    _attr_options = ["关", "1", "2", "3"]
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self):
        raw = self.coordinator.data.get("realtime", {})
        return super().available and raw.get("mode") == 6 and raw.get("work_state") == 0 and self.current_option is not None

    @property
    def current_option(self):
        level = self.coordinator.data.get("settings", {}).get(6, {}).get("reslex")
        return self.options[level] if level in (0, 1, 2, 3) else None

    async def async_select_option(self, option):
        await self.coordinator.async_control("reslex", self.options.index(option))


class ComfortSelect(BmcEntity, SelectEntity):
    _attr_options = ["自动", "关", "1", "2", "3", "4", "5"]
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key + "_control", name)
        self.control_key = key

    @property
    def available(self):
        raw = self.coordinator.data.get("realtime", {})
        return super().available and raw.get("mode") == 6 and raw.get("work_state") == 0 and self.current_option is not None

    @property
    def current_option(self):
        value = self.coordinator.data.get("settings", {}).get(6, {}).get(self.control_key)
        return self.options[value + 1] if value in range(-1, 6) else None

    async def async_select_option(self, option):
        await self.coordinator.async_control(self.control_key, self.options.index(option) - 1)


class EquipmentSelect(ComfortSelect):
    def __init__(self, coordinator, key, name, mapping):
        super().__init__(coordinator, key, name)
        self.mapping = mapping
        self._attr_options = list(mapping)

    @property
    def current_option(self):
        value = self.coordinator.data.get("settings", {}).get(6, {}).get(self.control_key)
        return next((name for name, code in self.mapping.items() if code == value), None)

    async def async_select_option(self, option):
        await self.coordinator.async_control(self.control_key, self.mapping[option])
