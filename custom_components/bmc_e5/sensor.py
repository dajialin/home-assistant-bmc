from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from .entity import BmcEntity

DESCRIPTIONS = (
    ("mode", "治疗模式", None),
    ("pressure_unit", "压力单位", None),
    ("minimum_epap", "最小呼气压力", "cmH₂O"),
    ("maximum_ipap", "最大吸气压力", "cmH₂O"),
    ("ramp_minutes", "延时升压", "min"),
    ("reslex", "呼吸减压档位", None),
    ("work_state", "运行状态代码", None),
)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([BmcSensor(entry.runtime_data, *d) for d in DESCRIPTIONS])


class BmcSensor(BmcEntity, SensorEntity):
    def __init__(self, coordinator, key, name, unit):
        super().__init__(coordinator, key, name)
        self._attr_native_unit_of_measurement = unit
        if key == "work_state":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
    @property
    def native_value(self):
        return self.device_value
