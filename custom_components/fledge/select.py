"""Wake to: what the thermostat shows as it wakes, for automations to set
(the speaker while music plays, the lights in the evening)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ENTITIES, CONF_WAKE, CONF_WAKE_DEVICE, WAKE_AUTO, WAKE_CHOOSER, WAKE_THERMOSTAT
from .entity import FledgeEntity

CHOOSER, THERMOSTAT, AUTO = "Device chooser", "Thermostat", "Automatic"


async def async_setup_entry(hass: HomeAssistant, entry, add: AddEntitiesCallback) -> None:
    add([WakeTo(entry)])


class WakeTo(FledgeEntity, SelectEntity):
    """The chooser, the thermostat, or one of the devices, by name."""

    _attr_icon = "mdi:gesture-tap"

    def __init__(self, entry) -> None:
        super().__init__(entry, "wake_to")

    def _devices(self) -> dict[str, str]:
        """The devices by the names they show as, in the cards' order."""
        names: dict[str, str] = {}
        for entity_id in self.entry.options.get(CONF_ENTITIES, []):
            state = self.hass.states.get(entity_id)
            name = (state.attributes.get("friendly_name") if state else None) or entity_id
            names.setdefault(name, entity_id)
        return names

    @property
    def options(self) -> list[str]:
        return [CHOOSER, AUTO, THERMOSTAT, *self._devices()]

    @property
    def current_option(self) -> str | None:
        device = self.entry.options.get(CONF_WAKE_DEVICE)
        for name, entity_id in self._devices().items():
            if entity_id == device:
                return name
        wake = self.entry.options.get(CONF_WAKE)
        return THERMOSTAT if wake == WAKE_THERMOSTAT else AUTO if wake == WAKE_AUTO else CHOOSER

    async def async_select_option(self, option: str) -> None:
        options = {k: v for k, v in self.entry.options.items() if k != CONF_WAKE_DEVICE}
        if option == THERMOSTAT:
            options[CONF_WAKE] = WAKE_THERMOSTAT
        elif option == AUTO:
            options[CONF_WAKE] = WAKE_AUTO
        elif option == CHOOSER:
            options[CONF_WAKE] = WAKE_CHOOSER
        else:
            options[CONF_WAKE_DEVICE] = self._devices()[option]
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def async_added_to_hass(self) -> None:
        self.thermostat.wake_listeners.append(self._changed)
        self.async_on_remove(lambda: self.thermostat.wake_listeners.remove(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
