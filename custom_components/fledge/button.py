"""Wake screen: lights the thermostat's display onto what it wakes to."""

from __future__ import annotations

from homeassistant.components import mqtt
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_SERIAL, SCREEN_WAKE_TOPIC, TOPIC_PREFIX
from .entity import FledgeEntity


async def async_setup_entry(hass: HomeAssistant, entry, add: AddEntitiesCallback) -> None:
    add([WakeScreen(entry)])


class WakeScreen(FledgeEntity, ButtonEntity):
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, entry) -> None:
        super().__init__(entry, "wake_screen")
        self.topic = SCREEN_WAKE_TOPIC.format(prefix=TOPIC_PREFIX, serial=entry.data[CONF_SERIAL])

    async def async_press(self) -> None:
        await mqtt.async_publish(self.hass, self.topic, "PRESS")
