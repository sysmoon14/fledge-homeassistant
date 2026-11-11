"""Wake to: what the thermostat shows as it wakes, for automations to set
(the speaker while music plays, the lights in the evening); and the
glance's layout (the weather on rainy mornings, the big number while the
car charges)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import glance
from .const import (CONF_GLANCE_SETTINGS, CONF_ENTITIES, CONF_FAR, CONF_GLANCE, CONF_NAMES, CONF_NEAR, CONF_WAKE, CONF_WAKE_DEVICE, FAR_DARK,
                    FAR_GLANCE, FAR_WAKE, NEAR_GLANCE, NEAR_NONE, NEAR_WAKE, WAKE_AUTO, WAKE_CHOOSER, WAKE_THERMOSTAT,
                    presence)
from .entity import FledgeEntity

CHOOSER, THERMOSTAT, AUTO = "Device chooser", "Thermostat", "Automatic"


async def async_setup_entry(hass: HomeAssistant, entry, add: AddEntitiesCallback) -> None:
    add([WakeTo(entry), Presence(entry, "across_the_room"), Presence(entry, "close_up"), GlanceLayout(entry)])


class WakeTo(FledgeEntity, SelectEntity):
    """The chooser, the thermostat, or one of the devices, by name."""

    _attr_icon = "mdi:gesture-tap"

    def __init__(self, entry) -> None:
        super().__init__(entry, "wake_to")

    def _devices(self) -> dict[str, str]:
        """The devices by the names they show as, in the cards' order."""
        names: dict[str, str] = {}
        renamed = self.entry.options.get(CONF_NAMES, {})
        for entity_id in self.entry.options.get(CONF_ENTITIES, []):
            state = self.hass.states.get(entity_id)
            name = renamed.get(entity_id) or (state.attributes.get("friendly_name") if state else None) or entity_id
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


class Presence(FledgeEntity, SelectEntity):
    """What someone across the room (across_the_room) or someone coming up
    to the thermostat (close_up) brings, for automations to set (no glance
    at night, say)."""

    _attr_icon = "mdi:motion-sensor"

    def __init__(self, entry, key: str) -> None:
        super().__init__(entry, key)
        self._far = key == "across_the_room"
        self._choices = ({"Glance": FAR_GLANCE, "What it wakes to": FAR_WAKE, "Stay dark": FAR_DARK} if self._far
                         else {"What it wakes to": NEAR_WAKE, "Glance": NEAR_GLANCE, "Nothing": NEAR_NONE})

    @property
    def options(self) -> list[str]:
        return list(self._choices)

    @property
    def current_option(self) -> str | None:
        value = presence(self.entry.options)[0 if self._far else 1]
        return next((name for name, v in self._choices.items() if v == value), None)

    async def async_select_option(self, option: str) -> None:
        options = dict(self.entry.options)
        value = self._choices[option]
        if self._far:
            options[CONF_FAR] = value
            options[CONF_GLANCE] = value == FAR_GLANCE
            if not options[CONF_GLANCE]:
                del options[CONF_GLANCE]
        else:
            options[CONF_NEAR] = value
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def async_added_to_hass(self) -> None:
        self.thermostat.wake_listeners.append(self._changed)
        self.async_on_remove(lambda: self.thermostat.wake_listeners.remove(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()


# The glance's layouts by name, in the panel's order.
LAYOUT_NAMES = {"Clock": "clock", "Temperature": "temp", "Weather": "weather", "Analog": "analog",
                "Big number": "number", "Up next": "agenda"}


class GlanceLayout(FledgeEntity, SelectEntity):
    """The day's glance layout; its lines are each layout's own, as set in
    the panel."""

    _attr_icon = "mdi:clock-time-four-outline"
    _attr_options = list(LAYOUT_NAMES)

    def __init__(self, entry) -> None:
        super().__init__(entry, "glance_layout")

    @property
    def current_option(self) -> str | None:
        layout = glance.settings(self.entry.options.get(CONF_GLANCE_SETTINGS))["layout"]
        return next((name for name, key in LAYOUT_NAMES.items() if key == layout), None)

    async def async_select_option(self, option: str) -> None:
        raw = dict(self.entry.options.get(CONF_GLANCE_SETTINGS) or {})
        raw["layout"] = LAYOUT_NAMES[option]
        self.hass.config_entries.async_update_entry(self.entry, options={**self.entry.options, CONF_GLANCE_SETTINGS: raw})

    async def async_added_to_hass(self) -> None:
        self.thermostat.wake_listeners.append(self._changed)
        self.async_on_remove(lambda: self.thermostat.wake_listeners.remove(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
