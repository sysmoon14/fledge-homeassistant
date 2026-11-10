"""Heating today and Hot water today: how long the Nest has called for
each since midnight, in hours. Kept as long-term statistics (total
increasing, back to 0 at midnight), so they chart by day, week and month.

They follow the Nest's own Heating and Hot water binary sensors (nestlocal,
over MQTT), and carry on across restarts the same day.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorStateClass
from homeassistant.const import STATE_ON, UnitOfTime
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from .config_flow import thermostats
from .const import CONF_SERIAL
from .entity import FledgeEntity

# The Nest's binary sensors, by the end of their unique ids (bridge.rs), and
# what each counts.
CALLS = {"heating": "heating_today", "hot_water_active": "hot_water_today"}
# How often a running count moves on screen.
TICK = timedelta(minutes=1)


def sources(hass: HomeAssistant, serial: str) -> dict[str, str]:
    """The Nest's Heating and Hot water binary sensors it has, by key."""
    device = thermostats(hass).get(serial)
    if device is None:
        return {}
    found = {}
    for e in er.async_entries_for_device(er.async_get(hass), device.id):
        if e.domain != "binary_sensor":
            continue
        for end, key in CALLS.items():
            if (e.unique_id or "").endswith(f"_{end}"):
                found[key] = e.entity_id
    return found


async def async_setup_entry(hass: HomeAssistant, entry, add: AddEntitiesCallback) -> None:
    found = sources(hass, entry.data[CONF_SERIAL])
    add([RunTime(entry, key, source) for key, source in found.items()])


class RunTime(FledgeEntity, RestoreSensor):
    """Hours a call has been on since midnight."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 1

    def __init__(self, entry, key: str, source: str) -> None:
        super().__init__(entry, key)
        self.source = source
        # Seconds counted today before the current run, and when that run
        # started (None while off).
        self._done = 0.0
        self._since: datetime | None = None
        self._tick: Any = None

    @property
    def icon(self) -> str:
        return "mdi:radiator" if self._attr_translation_key == "heating_today" else "mdi:water-boiler"

    @property
    def native_value(self) -> float:
        running = (dt_util.utcnow() - self._since).total_seconds() if self._since else 0.0
        return round((self._done + running) / 3600, 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"source": self.source}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        data = await self.async_get_last_sensor_data()
        if last is not None and data is not None and isinstance(data.native_value, (int, float)):
            if dt_util.as_local(last.last_updated).date() == dt_util.now().date():
                self._done = float(data.native_value) * 3600
        state = self.hass.states.get(self.source)
        if state is not None and state.state == STATE_ON:
            self._start(dt_util.utcnow())
        self.async_on_remove(async_track_state_change_event(self.hass, [self.source], self._changed))
        self.async_on_remove(async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=0))
        self.async_on_remove(self._stop_tick)

    def _start(self, now: datetime) -> None:
        self._since = now
        if self._tick is None:
            self._tick = async_track_time_interval(self.hass, self._moved_on, TICK)

    def _stop_tick(self) -> None:
        if self._tick is not None:
            self._tick()
            self._tick = None

    @callback
    def _changed(self, event: Event) -> None:
        new = event.data.get("new_state")
        now = dt_util.utcnow()
        on = new is not None and new.state == STATE_ON
        if on and self._since is None:
            self._start(now)
        elif not on and self._since is not None:
            self._done += (now - self._since).total_seconds()
            self._since = None
            self._stop_tick()
        else:
            return
        self.async_write_ha_state()

    @callback
    def _moved_on(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @callback
    def _midnight(self, now: datetime) -> None:
        """A new day: back to 0, a run going on counted from now."""
        self._done = 0.0
        if self._since is not None:
            self._since = dt_util.utcnow()
        self.async_write_ha_state()
