from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.helpers.entity import EntityCategory
from .entity import BmcEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([ConnectionSensor(coordinator, "connection", "蓝牙连接"),
                        TherapySensor(coordinator, "therapy", "治疗中"),
                        *[SettingSensor(coordinator, key, name) for key, name in (
                            ("auto_start", "自动启动"), ("auto_stop", "自动停机"), ("delayed_off", "延时关机"))]])


class ConnectionSensor(BmcEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    @property
    def available(self):
        return True
    @property
    def is_on(self):
        return bool(self.coordinator.data.get("connected"))


class TherapySensor(BmcEntity, BinarySensorEntity):
    @property
    def is_on(self):
        code = self.coordinator.data.get("realtime", {}).get("work_state")
        # Observed states: standby 0, therapy 1, preheat 6. Unknown stays unknown.
        return code == 1 if code in (0, 1, 6) else None


class SettingSensor(BmcEntity, BinarySensorEntity):
    @property
    def is_on(self):
        return bool(self.device_value) if self.device_value in (0, 1) else None
