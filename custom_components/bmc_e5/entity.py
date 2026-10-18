from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from .values import entity_values


class BmcEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.address)},
                                           manufacturer="BMC", model="E5 B25A Plus", name="BMC E5")

    @property
    def available(self):
        return bool(self.coordinator.data.get("connected"))

    @property
    def device_value(self):
        return entity_values(self.coordinator.data).get(self.key)
