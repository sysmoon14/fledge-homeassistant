"""Keep screen on: the thermostat's display stays lit while it's on.

Call for heat: while the thermostat's heating control is Home Assistant's,
on asks it to fire the boiler and off asks it not to. The ask is sent again
every minute; if Home Assistant stops, the thermostat goes back to its own
schedule by itself.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (ASK_EVERY, ASK_LEASE, CALL_FOR_HEAT_TOPIC, CONF_SERIAL, SCREEN_ON_TOPIC, STATE_TOPIC,
                    TOPIC_PREFIX)
from .entity import FledgeEntity


async def async_setup_entry(hass: HomeAssistant, entry, add: AddEntitiesCallback) -> None:
    add([KeepScreenOn(entry), CallForHeat(entry)])
    entity_platform.async_get_current_platform().async_register_entity_service(
        "call_for_heat",
        {vol.Required("heat"): cv.boolean, vol.Optional("rooms", default=[]): vol.All(cv.ensure_list, [cv.string])},
        "async_call_for_heat",
    )


class KeepScreenOn(FledgeEntity, SwitchEntity, RestoreEntity):
    """Retained on the thermostat's topic, so it holds as either restarts."""

    _attr_icon = "mdi:monitor-shimmer"
    _attr_is_on = False

    def __init__(self, entry) -> None:
        super().__init__(entry, "keep_screen_on")
        self.topic = SCREEN_ON_TOPIC.format(prefix=TOPIC_PREFIX, serial=entry.data[CONF_SERIAL])

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        self._attr_is_on = last is not None and last.state == "on"

    async def _set(self, on: bool) -> None:
        await mqtt.async_publish(self.hass, self.topic, "ON" if on else "OFF", retain=True)
        self._attr_is_on = on
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class CallForHeat(FledgeEntity, SwitchEntity, RestoreEntity):
    """Available while the thermostat's heating control is Home Assistant's."""

    _attr_icon = "mdi:radiator"
    _attr_is_on = False

    def __init__(self, entry) -> None:
        super().__init__(entry, "call_for_heat")
        serial = entry.data[CONF_SERIAL]
        self.topic = CALL_FOR_HEAT_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial)
        self.state_topic = STATE_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial)
        self.rooms: list[str] = []
        self.heat_control = False
        self.phase: str | None = None

    @property
    def available(self) -> bool:
        return self.heat_control

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"rooms": self.rooms, "thermostat": self.phase}

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state == "on"
            self.rooms = list(last.attributes.get("rooms") or [])
        self.async_on_remove(await mqtt.async_subscribe(self.hass, self.state_topic, self._state))
        self.async_on_remove(async_track_time_interval(self.hass, self._renew, timedelta(seconds=ASK_EVERY)))

    @callback
    def _state(self, msg) -> None:
        try:
            state = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        was = self.heat_control
        self.heat_control = bool(state.get("heat_control"))
        self.phase = state.get("heat_control_phase")
        self.async_write_ha_state()
        if self.heat_control and not was:
            self.hass.async_create_task(self._ask())

    async def _renew(self, _now=None) -> None:
        await self._ask()

    async def _ask(self) -> None:
        if not self.heat_control:
            return
        payload = json.dumps({"heat": self._attr_is_on, "lease": ASK_LEASE, "rooms": self.rooms})
        await mqtt.async_publish(self.hass, self.topic, payload)

    async def async_call_for_heat(self, heat: bool, rooms: list[str] | None = None) -> None:
        self._attr_is_on = heat
        self.rooms = list(rooms or []) if heat else []
        self.async_write_ha_state()
        await self._ask()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_call_for_heat(True, self.rooms)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_call_for_heat(False)
