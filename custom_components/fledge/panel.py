"""Fledge in Home Assistant's sidebar: each thermostat's cards laid out in
the ring's order, previewed as the Nest shows them, and saved to the
entry's options (the same ones the options flow edits).

One JS module (frontend/fledge-panel.js, no build step) and a few
websocket commands; admins only, as the options are.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er

from . import cards
from .config_flow import options_from, thermostats
from .feeds import acts_as_thermostat, option_switch, regulates_from_a_sensor, role_switch, wwn_switch
from .const import (CONF_ALARMS, needs, CONF_ENTITIES, CONF_GLANCE, CONF_OUTDOOR, CONF_ROOM_SENSOR, CONF_NAMES, CONF_SERIAL, CONF_THERMOSTAT, CONF_WAKE, CONF_WAKE_DEVICE, CONF_WEATHER, DOMAIN,
                    DOMAINS, WAKE_AUTO, WAKE_CHOOSER, WAKE_THERMOSTAT, CONF_CARD_IDLE, CONF_DIM_OFF, CONF_SHOW_THERMOSTAT, CONF_FAR, CONF_NEAR,
                    FAR_DARK, FAR_GLANCE, FAR_WAKE, NEAR_GLANCE, NEAR_NONE, NEAR_WAKE, presence)

FRONTEND = Path(__file__).parent / "frontend"
STATIC_URL = "/fledge_static"
PANEL = "fledge"
# The static path can't be taken back: registered once a run.
_STATIC = f"{DOMAIN}_static"
# The schedule cards' module, and the url it's loaded from while loaded.
CARDS_JS = "fledge-schedule-card.js"
_CARDS = f"{DOMAIN}_cards"
# Longest name a card takes on the Nest.
NAME_LENGTH = 40
# An alarm's settings as the panel sends them.
ALARM_SETTINGS = vol.Schema({
    vol.Optional("names", default={}): {vol.In([m for m, _ in cards.ALARM_MODES]): str},
    vol.Optional("hidden", default=[]): [vol.In([m for m, _ in cards.ALARM_MODES])],
    vol.Optional("code_length"): vol.Any(None, vol.All(int, vol.Range(min=4, max=12))),
    vol.Optional("takeover", default=True): bool,
})


def alarm_settings(alarms: dict[str, Any]) -> dict[str, Any]:
    """Alarms' settings as kept: names trimmed, only what isn't the default."""
    kept = {}
    for entity, settings in alarms.items():
        names = {m: n.strip()[:cards.MODE_NAME] for m, n in settings.get("names", {}).items() if n.strip()}
        one = {"names": names, "hidden": sorted(set(settings.get("hidden", []))),
               "code_length": settings.get("code_length")}
        one = {k: v for k, v in one.items() if v}
        if settings.get("takeover", True) is False:
            one["takeover"] = False
        if one:
            kept[entity] = one
    return kept


@callback
def async_setup(hass: HomeAssistant) -> None:
    """The panel's websocket commands."""
    websocket_api.async_register_command(hass, ws_thermostats)
    websocket_api.async_register_command(hass, ws_preview)
    websocket_api.async_register_command(hass, ws_save)


async def async_show(hass: HomeAssistant, version: str) -> None:
    """The sidebar entry and the schedule cards, while any thermostat is
    set up. The cards' module loads on every page, as a card's resource
    would, without touching the dashboards' own resources."""
    if hass.http is None:
        return
    if not hass.data.get(_STATIC):
        hass.data[_STATIC] = True
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(FRONTEND), cache_headers=False)])
    if not hass.data.get(_CARDS) and frontend.DATA_EXTRA_MODULE_URL in hass.data:
        hass.data[_CARDS] = f"{STATIC_URL}/{CARDS_JS}?v={version}"
        frontend.add_extra_js_url(hass, hass.data[_CARDS])
    if PANEL in hass.data.get(frontend.DATA_PANELS, {}):
        return
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL, webcomponent_name="fledge-panel", sidebar_title="Fledge",
        sidebar_icon="mdi:thermostat-box", module_url=f"{STATIC_URL}/fledge-panel.js?v={version}",
        require_admin=True, config={})


@callback
def async_hide(hass: HomeAssistant, leaving: ConfigEntry) -> None:
    """No sidebar entry once the last thermostat is unloaded."""
    if not [e for e in hass.config_entries.async_loaded_entries(DOMAIN) if e.entry_id != leaving.entry_id]:
        frontend.async_remove_panel(hass, PANEL, warn_if_unknown=False)
        if url := hass.data.pop(_CARDS, None):
            frontend.remove_extra_js_url(hass, url)


def _thermostat(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """One thermostat as the panel shows it: its options, its own entities
    (the Nest's, over MQTT) and Fledge's, and what its build understands."""
    serial = entry.data[CONF_SERIAL]
    device = thermostats(hass).get(serial)
    registry = er.async_get(hass)
    own = er.async_entries_for_device(registry, device.id) if device else []
    mine = er.async_entries_for_config_entry(registry, entry.entry_id)
    running = getattr(entry, "runtime_data", None) if entry.state is ConfigEntryState.LOADED else None

    def first(domain: str) -> str | None:
        return next((e.entity_id for e in own if e.domain == domain), None)

    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "serial": serial,
        "loaded": running is not None,
        "options": dict(entry.options),
        "device_id": device.id if device else None,
        "name": (device.name_by_user or device.name) if device else None,
        "name_by_user": device.name_by_user if device else None,
        "default_name": device.name if device else None,
        "sw_version": device.sw_version if device else None,
        "climate": first("climate"),
        "update": first("update"),
        "own": [e.entity_id for e in own],
        "entities": {(e.unique_id or "").removeprefix(f"{serial}_"): e.entity_id for e in mine},
        "protocol": running.protocol if running else None,
        # Whether it regulates from a room thermometer it's sent (nestlocal's
        # -external-temperature): only then does the room sensor do anything.
        "room_sensor": regulates_from_a_sensor(hass, serial),
        # The switch that turns that option on (and reboots the Nest), on a
        # build that has it.
        "room_sensor_switch": option_switch(hass, serial),
        "needs": needs(entry.options.get(CONF_ENTITIES), entry.options.get(CONF_SHOW_THERMOSTAT, True)),
        # The Nest's Act as a thermostat switch (protocol 10), and whether it is.
        "role_switch": role_switch(hass, serial),
        "acting": acts_as_thermostat(hass, serial),
        # Its Works With Nest boiler switch, on a Heat Link model.
        "wwn_switch": wwn_switch(hass, serial),
        "version": running.version if running else None,
    }


@websocket_api.websocket_command({vol.Required("type"): "fledge/thermostats"})
@websocket_api.require_admin
@callback
def ws_thermostats(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The thermostats set up, and what can be a card."""
    connection.send_result(msg["id"], {
        "thermostats": [_thermostat(hass, entry) for entry in hass.config_entries.async_entries(DOMAIN)],
        "domains": DOMAINS,
    })


@websocket_api.websocket_command({
    vol.Required("type"): "fledge/preview",
    vol.Required("entities"): [cv.entity_id],
    vol.Optional("names", default={}): {cv.entity_id: str},
    vol.Optional("climate"): vol.Any(None, cv.entity_id),
    vol.Optional(CONF_ALARMS, default={}): {cv.entity_id: dict},
})
@websocket_api.require_admin
@callback
def ws_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The cards as the thermostat would get them now, by entity: what the
    panel draws. Entities Home Assistant doesn't know have none."""
    get = hass.states.get
    names = {k: v.strip()[:NAME_LENGTH] for k, v in msg["names"].items() if v.strip()}
    shown = {}
    for entity_id in msg["entities"]:
        state = get(entity_id)
        if state is not None:
            shown[entity_id] = cards.named(cards.card(state, get, msg[CONF_ALARMS]), names.get(entity_id))
    climate = get(msg["climate"]) if msg.get("climate") else None
    connection.send_result(msg["id"], {
        "cards": shown,
        "thermostat": cards.climate(climate) if climate is not None and climate.domain == "climate" else None,
    })


@websocket_api.websocket_command({
    vol.Required("type"): "fledge/save",
    vol.Required("entry_id"): str,
    vol.Required(CONF_ENTITIES): [cv.entity_id],
    vol.Required(CONF_WAKE): vol.In([WAKE_CHOOSER, WAKE_AUTO, WAKE_THERMOSTAT]),
    vol.Optional(CONF_WAKE_DEVICE): vol.Any(None, cv.entity_id),
    vol.Optional(CONF_GLANCE, default=False): bool,
    vol.Optional(CONF_FAR): vol.In([FAR_GLANCE, FAR_WAKE, FAR_DARK]),
    vol.Optional(CONF_NEAR): vol.In([NEAR_WAKE, NEAR_GLANCE, NEAR_NONE]),
    vol.Optional(CONF_CARD_IDLE): vol.All(int, vol.Range(min=5, max=600)),
    vol.Optional(CONF_DIM_OFF): bool,
    vol.Optional(CONF_SHOW_THERMOSTAT): bool,
    vol.Optional(CONF_WEATHER): vol.Any(None, cv.entity_id),
    vol.Optional(CONF_OUTDOOR): vol.Any(None, cv.entity_id),
    vol.Optional(CONF_ROOM_SENSOR): vol.Any(None, cv.entity_id),
    vol.Optional(CONF_NAMES, default={}): {cv.entity_id: str},
    vol.Optional(CONF_THERMOSTAT, default=0): vol.All(int, vol.Range(min=0)),
    # An older panel doesn't send it: kept.
    vol.Optional(CONF_ALARMS): {cv.entity_id: ALARM_SETTINGS},
})
@websocket_api.require_admin
@callback
def ws_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """A thermostat's cards and wake, as the options flow would save them,
    the cards' names, and where the thermostat's own card goes."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No such thermostat")
        return
    own = set(_thermostat(hass, entry)["own"])
    entities = list(dict.fromkeys(msg[CONF_ENTITIES]))
    wrong = [e for e in entities if e.partition(".")[0] not in DOMAINS or e in own]
    if wrong:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, f"Can't be a card: {', '.join(wrong)}")
        return
    if msg.get(CONF_WEATHER) and not msg[CONF_WEATHER].startswith("weather."):
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "Not a weather entity")
        return
    for key in (CONF_OUTDOOR, CONF_ROOM_SENSOR):
        if msg.get(key) and not msg[key].startswith("sensor."):
            connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "Not a sensor")
            return
    device = msg.get(CONF_WAKE_DEVICE)
    options = options_from({
        CONF_ENTITIES: entities,
        CONF_WAKE: msg[CONF_WAKE],
        CONF_WAKE_DEVICE: device if device in entities else None,
        CONF_GLANCE: msg[CONF_GLANCE],
        # An older panel says only whether to glance: the rest kept.
        CONF_FAR: msg.get(CONF_FAR) or (FAR_GLANCE if msg[CONF_GLANCE] else
                                        FAR_DARK if presence(entry.options)[0] == FAR_DARK else FAR_WAKE),
        CONF_NEAR: msg.get(CONF_NEAR) or presence(entry.options)[1],
        CONF_CARD_IDLE: msg.get(CONF_CARD_IDLE) or entry.options.get(CONF_CARD_IDLE),
        # An older panel doesn't send it: kept.
        CONF_DIM_OFF: msg[CONF_DIM_OFF] if CONF_DIM_OFF in msg else entry.options.get(CONF_DIM_OFF, True),
        CONF_SHOW_THERMOSTAT: (msg[CONF_SHOW_THERMOSTAT] if CONF_SHOW_THERMOSTAT in msg
                               else entry.options.get(CONF_SHOW_THERMOSTAT, True)),
        CONF_WEATHER: msg.get(CONF_WEATHER),
        # An older panel doesn't send these: kept.
        CONF_OUTDOOR: msg[CONF_OUTDOOR] if CONF_OUTDOOR in msg else entry.options.get(CONF_OUTDOOR),
        CONF_ROOM_SENSOR: msg[CONF_ROOM_SENSOR] if CONF_ROOM_SENSOR in msg else entry.options.get(CONF_ROOM_SENSOR),
        CONF_THERMOSTAT: msg[CONF_THERMOSTAT],
    }, {k: v.strip()[:NAME_LENGTH] for k, v in msg[CONF_NAMES].items() if k in entities and v.strip()},
        alarm_settings(msg[CONF_ALARMS]) if CONF_ALARMS in msg else entry.options.get(CONF_ALARMS))
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"options": options})
