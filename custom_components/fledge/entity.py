"""What Fledge's own entities share: the cards' device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_SERIAL, DOMAIN


class FledgeEntity(Entity):
    """An entity of a thermostat's cards, on their device."""

    _attr_has_entity_name = True

    def __init__(self, entry, key: str) -> None:
        self.entry = entry
        self.thermostat = entry.runtime_data
        serial = entry.data[CONF_SERIAL]
        self._attr_unique_id = f"{serial}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, serial)})
