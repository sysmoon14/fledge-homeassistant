"""The Nest's own weekly schedules, heating and hot water, from Home
Assistant: what the schedule cards (frontend/fledge-schedule-card.js) draw,
and fledge.set_schedule, which writes any of the days at once.

nestlocal publishes each schedule as a sensor with the days as attributes
(heating: [{time, temp}], hot water: [{on, off}]) and replaces the days a
message on set/heating_times or set/hot_water_times names, keeping the rest.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt, websocket_api
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import dt as dt_util

from .config_flow import thermostats
from .const import DOMAIN, TOPIC_PREFIX

_LOGGER = logging.getLogger(__name__)

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
HEATING, HOT_WATER = "heating", "hot_water"
TOPICS = {HEATING: "{prefix}/{serial}/set/heating_times", HOT_WATER: "{prefix}/{serial}/set/hot_water_times"}
# What nestlocal takes (schedule.rs SETPOINT).
SETPOINT = (4.5, 35.0)
# More a day than anyone sets by hand; the Nest's own app stops well short.
MOST = 12
# The thermostat's entities, by the end of their unique ids (nestlocal_<serial>_...).
OWN = {"climate": "climate", "heating": "heating_times", "hot_water": "hot_water_times",
       "hot_water_switch": "hot_water_schedule", "auto_schedule": "learning_mode",
       "heat_control": "heat_control_phase"}
# Heat control phases (nestlocal demand.rs) in which the Nest's schedule is
# kept aside: Home Assistant decides, or the dial holds until its next change.
SCHEDULE_ASIDE = ("heating", "idle", "dial")

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _time(value: Any) -> str:
    value = str(value).strip()
    if len(value) == 8 and value.endswith(":00"):
        value = value[:5]  # HH:MM:SS, as a time selector gives it
    if not _TIME.match(value):
        raise vol.Invalid(f"expected a time HH:MM, got {value!r}")
    return value


def _setpoints(day: list[Any]) -> list[dict[str, Any]]:
    out = vol.Schema([{vol.Required("time"): _time,
                       vol.Required("temp"): vol.All(vol.Coerce(float), vol.Range(*SETPOINT))}])(day)
    if len(out) > MOST:
        raise vol.Invalid(f"at most {MOST} changes a day")
    times = [p["time"] for p in out]
    if len(set(times)) != len(times):
        raise vol.Invalid("two changes at the same time")
    return sorted(({"time": p["time"], "temp": round(p["temp"] * 2) / 2} for p in out), key=lambda p: p["time"])


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _periods(day: list[Any]) -> list[dict[str, str]]:
    out = vol.Schema([{vol.Required("on"): _time, vol.Required("off"): _time}])(day)
    if len(out) > MOST:
        raise vol.Invalid(f"at most {MOST} periods a day")
    # An off at or before its on is the next day's (24:00 is 00:00).
    spans = sorted((_minutes(p["on"]), _minutes(p["off"]) if _minutes(p["off"]) > _minutes(p["on"])
                    else _minutes(p["off"]) + 1440) for p in out)
    if any(b[0] < a[1] for a, b in zip(spans, spans[1:])):
        raise vol.Invalid("periods overlap")
    if spans and spans[-1][1] > 1440 + spans[0][0]:
        raise vol.Invalid("a period runs past the next day's first")
    return sorted(({"on": p["on"], "off": p["off"]} for p in out), key=lambda p: p["on"])


SET_SCHEDULE_SCHEMA = vol.All(vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Optional(HEATING): {vol.In(DAYS): _setpoints},
    vol.Optional(HOT_WATER): {vol.In(DAYS): _periods},
}), cv.has_at_least_one_key(HEATING, HOT_WATER))


def _serial(hass: HomeAssistant, entity_id: str) -> str:
    """The serial of the Nest an entity belongs to (any of its own)."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None and entry.device_id:
        for serial, device in thermostats(hass).items():
            if device.id == entry.device_id:
                return serial
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_a_nest",
                                 translation_placeholders={"entity_id": entity_id})


def entities(hass: HomeAssistant, serial: str) -> dict[str, str | None]:
    """The thermostat's own entities the schedules use, by role."""
    device = thermostats(hass).get(serial)
    found: dict[str, str | None] = dict.fromkeys(OWN)
    if device is None:
        return found
    for e in er.async_entries_for_device(er.async_get(hass), device.id):
        for role, end in OWN.items():
            if role == "climate":
                if e.domain == "climate" and found[role] is None:
                    found[role] = e.entity_id
            elif (e.unique_id or "").endswith(f"_{end}"):
                found[role] = e.entity_id
    return found


def current(week: dict[str, list[dict[str, Any]]], weekday: int, now: str) -> float | None:
    """The setpoint a heating week has now: the day's last change at or
    before now, else the last one before it, however many days back."""
    past = [p for p in week.get(DAYS[weekday]) or [] if p["time"] <= now]
    if past:
        return float(past[-1]["temp"])
    for back in range(1, 8):
        day = week.get(DAYS[(weekday - back) % 7]) or []
        if day:
            return float(day[-1]["temp"])
    return None


async def async_set_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    serial = _serial(hass, call.data["entity_id"])
    own = entities(hass, serial)
    for kind in (HEATING, HOT_WATER):
        if kind not in call.data:
            continue
        if kind == HOT_WATER and own[HOT_WATER] is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_hot_water",
                                         translation_placeholders={"serial": serial})
        topic = TOPICS[kind].format(prefix=TOPIC_PREFIX, serial=serial)
        await mqtt.async_publish(hass, topic, json.dumps(call.data[kind], separators=(",", ":")))
    if HEATING in call.data:
        await _apply_now(hass, own, call.data[HEATING])


async def _apply_now(hass: HomeAssistant, own: dict[str, str | None], changed: dict[str, Any]) -> None:
    """The Nest reads a changed schedule only at its next scheduled change:
    the setpoint that applies now, set now. Not while in Eco (away, or a
    holiday) or off, and not while heat control has the schedule aside: the
    Nest takes it up again itself when Home Assistant stops asking, and a
    target set now would be a turn of the dial that shuts Home Assistant out
    until the schedule's next change."""
    phase = hass.states.get(own["heat_control"]) if own["heat_control"] else None
    if phase is not None and phase.state in SCHEDULE_ASIDE:
        return
    climate = hass.states.get(own["climate"]) if own["climate"] else None
    sensor = hass.states.get(own[HEATING]) if own[HEATING] else None
    if climate is None or climate.state != "heat" or climate.attributes.get("preset_mode") not in (None, "none"):
        return
    week = {d: (sensor.attributes.get(d) if sensor else None) or [] for d in DAYS}
    week.update(changed)
    local = dt_util.now()
    temp = current(week, local.weekday(), local.strftime("%H:%M"))
    target = climate.attributes.get("temperature")
    if temp is None or (isinstance(target, (int, float)) and abs(float(target) - temp) < 0.25):
        return
    await hass.services.async_call("climate", "set_temperature", {"entity_id": climate.entity_id, "temperature": temp},
                                   blocking=True)


@websocket_api.websocket_command({vol.Required("type"): "fledge/schedule", vol.Required("entity_id"): cv.entity_id})
@callback
def ws_schedule(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """For a schedule card: the Nest an entity belongs to, and its entities
    the schedules use. Any user: it's what the dashboard shows anyway."""
    try:
        serial = _serial(hass, msg["entity_id"])
    except ServiceValidationError:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, f"{msg['entity_id']} isn't a Nest running Fledge")
        return
    connection.send_result(msg["id"], {"serial": serial, **entities(hass, serial)})


@callback
def async_setup(hass: HomeAssistant) -> None:
    async def set_schedule(call: ServiceCall) -> None:
        await async_set_schedule(hass, call)

    hass.services.async_register(DOMAIN, "set_schedule", set_schedule, schema=SET_SCHEDULE_SCHEMA)
    websocket_api.async_register_command(hass, ws_schedule)

