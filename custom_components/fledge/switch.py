"""Keep screen on: the thermostat's display stays lit while it's on."""

from __future__ import annotations

from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_SERIAL, SCREEN_ON_TOPIC, TOPIC_PREFIX
from .entity import FledgeEntity


async def async_setup_entry(hass: HomeAssistant, entry, add: AddEntitiesCallback) -> None:
    add([KeepScreenOn(entry)])


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
