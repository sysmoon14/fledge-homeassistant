"""Night glance: whether the Nest shows the glance's night layout, as the
glance's night settings (the sun, set times or a sensor) say."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import FledgeEntity


async def async_setup_entry(hass: HomeAssistant, entry, add: AddEntitiesCallback) -> None:
    add([NightGlance(entry)])


class NightGlance(FledgeEntity, BinarySensorEntity):
    """On while the night's glance is the one sent."""

    _attr_icon = "mdi:weather-night"

    def __init__(self, entry) -> None:
        super().__init__(entry, "night_glance")

    @property
    def is_on(self) -> bool:
        return self.thermostat.night

    async def async_added_to_hass(self) -> None:
        self.thermostat.night_listeners.append(self._changed)
        self.async_on_remove(lambda: self.thermostat.night_listeners.remove(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
