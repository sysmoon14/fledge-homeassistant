"""Repairs Fledge can fix: turning on the Nest's external temperature option
for the room thermometer chosen, which reboots the Nest into it."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .feeds import option_switch


class RoomSensorFlow(RepairsFlow):
    """Confirm, then flip the Nest's External temperature switch on."""

    def __init__(self, serial: str) -> None:
        self.serial = serial

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        switch = option_switch(self.hass, self.serial)
        if switch is None:
            return self.async_abort(reason="no_switch")
        if user_input is not None:
            await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}),
                                    description_placeholders={"serial": self.serial})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    return RoomSensorFlow((data or {}).get("serial", ""))
