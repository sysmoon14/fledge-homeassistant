"""What the Nest hears from Home Assistant besides its cards: the weather
outside, for its own weather screen, and a room's temperature to regulate
from. They replace the Nest weather and external temperature blueprints.

nestlocal takes °C (commands.rs): outdoor_temperature and outdoor_humidity
as numbers, weather as Home Assistant's forecasts in JSON, and
external_temperature as a number while it runs with -external-temperature.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import timedelta
from typing import Any

from homeassistant.components import mqtt
from homeassistant.const import UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .config_flow import thermostats
from .const import (CONF_OUTDOOR, CONF_ROOM_SENSOR, CONF_WEATHER, DOMAIN, HUMIDITY_TOPIC, OUTDOOR_TOPIC, ROOM_TOPIC,
                    TOPIC_PREFIX, WEATHER_TOPIC)

_LOGGER = logging.getLogger(__name__)

# How often the forecast goes again, and an unchanged room reading (one
# counts for 30 minutes on the Nest: -external-temperature-timeout).
WEATHER_EVERY = timedelta(minutes=15)
ROOM_EVERY = timedelta(minutes=10)
# As many forecasts as the weather blueprint sent.
HOURS, DAYS = 48, 14
# WeatherEntityFeature: FORECAST_DAILY, FORECAST_HOURLY, FORECAST_TWICE_DAILY.
DAILY, HOURLY = 1, 2


def celsius(value: Any, unit: str | None) -> float | None:
    """A temperature in °C, from a reading in `unit`; None if not a number."""
    try:
        reading = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(reading):
        return None
    if unit == UnitOfTemperature.FAHRENHEIT:
        reading = (reading - 32) * 5 / 9
    elif unit == UnitOfTemperature.KELVIN:
        reading -= 273.15
    return round(reading, 2)


def _number(value: Any) -> float | None:
    try:
        reading = float(value)
    except (TypeError, ValueError):
        return None
    return reading if math.isfinite(reading) else None


def outdoor(weather: State | None, thermometer: State | None) -> tuple[float | None, float | None]:
    """Outside now, °C and %: the thermometer's reading if one is chosen,
    else the weather entity's."""
    temperature = None
    if thermometer is not None:
        temperature = celsius(thermometer.state, thermometer.attributes.get("unit_of_measurement"))
    elif weather is not None:
        temperature = celsius(weather.attributes.get("temperature"), weather.attributes.get("temperature_unit"))
    humidity = _number(weather.attributes.get("humidity")) if weather is not None else None
    return temperature, humidity


def forecast(weather: State, sun: State | None, hourly: list[dict], daily: list[dict]) -> dict[str, Any]:
    """The Nest's weather: conditions now, and forecasts in °C."""
    unit = weather.attributes.get("temperature_unit")

    def converted(f: dict) -> dict:
        f = dict(f)
        for key in ("temperature", "templow"):
            if key in f:
                f[key] = celsius(f[key], unit)
        return f

    return {
        "condition": weather.state,
        "next_rising": sun.attributes.get("next_rising") if sun else None,
        "next_setting": sun.attributes.get("next_setting") if sun else None,
        "hourly": [converted(f) for f in hourly[:HOURS]],
        "daily": [converted(f) for f in daily[:DAYS]],
    }


def regulates_from_a_sensor(hass: HomeAssistant, serial: str) -> bool:
    """Whether the Nest runs nestlocal's stand-in sensor: only then does it
    announce its Temperature source."""
    device = thermostats(hass).get(serial)
    if device is None:
        return False
    return any((e.unique_id or "").endswith("_temperature_source")
               for e in er.async_entries_for_device(er.async_get(hass), device.id))


def option_switch(hass: HomeAssistant, serial: str) -> str | None:
    """The Nest's External temperature switch (nestlocal 0.5.5 and later):
    turns the option on or off, rebooting the Nest into it."""
    device = thermostats(hass).get(serial)
    if device is None:
        return None
    return next((e.entity_id for e in er.async_entries_for_device(er.async_get(hass), device.id)
                 if e.domain == "switch" and (e.unique_id or "").endswith("_external_temperature_option")), None)


class Feeds:
    """One Nest's weather and room readings, kept sent."""

    def __init__(self, hass: HomeAssistant, serial: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.serial = serial
        self.weather: str | None = options.get(CONF_WEATHER)
        self.thermometer: str | None = options.get(CONF_OUTDOOR)
        self.room: str | None = options.get(CONF_ROOM_SENSOR)
        self._unsubscribe: list[Any] = []
        # What was last sent, so a state change that moves nothing sends nothing.
        self._sent: dict[str, str] = {}

    def _topic(self, topic: str) -> str:
        return topic.format(prefix=TOPIC_PREFIX, serial=self.serial)

    @property
    def issue(self) -> str:
        return f"room_sensor_{self.serial}"

    async def async_start(self) -> None:
        if self.weather or self.thermometer:
            watched = [e for e in (self.weather, self.thermometer) if e]
            self._unsubscribe.append(async_track_state_change_event(self.hass, watched, self._outside_changed))
            self._unsubscribe.append(async_track_time_interval(self.hass, self._every_quarter, WEATHER_EVERY))
            self.hass.async_create_task(self.async_send_weather())
        if self.room:
            self._unsubscribe.append(async_track_state_change_event(self.hass, [self.room], self._room_changed))
            self._unsubscribe.append(async_track_time_interval(self.hass, self._room_again, ROOM_EVERY))
            await self.async_send_room()
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue)

    @callback
    def async_stop(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        ir.async_delete_issue(self.hass, DOMAIN, self.issue)

    async def _publish(self, topic: str, payload: str, retain: bool, always: bool = False) -> None:
        if not always and self._sent.get(topic) == payload:
            return
        self._sent[topic] = payload
        await mqtt.async_publish(self.hass, topic, payload, retain=retain)

    # Outside.

    @callback
    def _outside_changed(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_send_outside())

    async def _every_quarter(self, _now: Any) -> None:
        await self.async_send_weather()

    async def async_send_outside(self) -> None:
        """The temperature and humidity now; a missing one leaves the last."""
        weather = self.hass.states.get(self.weather) if self.weather else None
        thermometer = self.hass.states.get(self.thermometer) if self.thermometer else None
        temperature, humidity = outdoor(weather, thermometer)
        if temperature is not None:
            await self._publish(self._topic(OUTDOOR_TOPIC), str(temperature), retain=True)
        if humidity is not None:
            await self._publish(self._topic(HUMIDITY_TOPIC), str(humidity), retain=True)

    async def async_send_weather(self) -> None:
        """Now, and the forecasts, from the weather entity."""
        await self.async_send_outside()
        weather = self.hass.states.get(self.weather) if self.weather else None
        if weather is None or weather.state in ("unavailable", "unknown"):
            return
        features = int(weather.attributes.get("supported_features") or 0)
        hourly = await self._forecasts("hourly") if features & HOURLY else []
        daily = await self._forecasts("daily" if features & DAILY else "twice_daily")
        payload = json.dumps(forecast(weather, self.hass.states.get("sun.sun"), hourly, daily),
                             separators=(",", ":"), default=str)
        await self._publish(self._topic(WEATHER_TOPIC), payload, retain=True)

    async def _forecasts(self, kind: str) -> list[dict]:
        try:
            response = await self.hass.services.async_call(
                "weather", "get_forecasts", {"entity_id": self.weather, "type": kind},
                blocking=True, return_response=True)
        except Exception:  # noqa: BLE001 - a weather entity without that kind: none
            _LOGGER.debug("No %s forecast from %s", kind, self.weather, exc_info=True)
            return []
        return list(((response or {}).get(self.weather) or {}).get("forecast") or [])

    # The room.

    @callback
    def _room_changed(self, _event: Event) -> None:
        self.hass.async_create_task(self.async_send_room())

    async def _room_again(self, _now: Any) -> None:
        await self.async_send_room(always=True)

    async def async_send_room(self, always: bool = False) -> None:
        """The room's reading, if the Nest can regulate from it; else a
        repair saying how it can."""
        if not regulates_from_a_sensor(self.hass, self.serial):
            # With the Nest's switch, the repair turns the option on; an older
            # build says how instead.
            switch = option_switch(self.hass, self.serial)
            ir.async_create_issue(self.hass, DOMAIN, self.issue, is_fixable=switch is not None,
                                  severity=ir.IssueSeverity.WARNING,
                                  translation_key="room_sensor_switch" if switch else "room_sensor_unsupported",
                                  translation_placeholders={"serial": self.serial, "sensor": self.room or ""},
                                  data={"serial": self.serial})
            return
        ir.async_delete_issue(self.hass, DOMAIN, self.issue)
        state = self.hass.states.get(self.room) if self.room else None
        reading = celsius(state.state, state.attributes.get("unit_of_measurement")) if state else None
        if reading is None:
            return  # unavailable: the last reading counts until it times out
        await self._publish(self._topic(ROOM_TOPIC), str(reading), retain=False, always=always)
