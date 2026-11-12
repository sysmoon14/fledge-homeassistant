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

from . import cards, faces, glance
from .config_flow import options_from, thermostats
from .feeds import acts_as_thermostat, option_switch, regulates_from_a_sensor, role_switch, wwn_switch
from .const import (CONF_FACES, CONF_LIMITS, CONF_GLANCE_SETTINGS, CONF_ALARMS, needs, CONF_ENTITIES, CONF_GLANCE, CONF_OUTDOOR, CONF_ROOM_SENSOR, CONF_NAMES, CONF_SERIAL, CONF_THERMOSTAT, CONF_WAKE, CONF_WAKE_DEVICE, CONF_WEATHER, DOMAIN,
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
    websocket_api.async_register_command(hass, ws_glance_preview)
    websocket_api.async_register_command(hass, ws_glance_try)


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
        "needs": needs(entry.options.get(CONF_ENTITIES), entry.options.get(CONF_SHOW_THERMOSTAT, True),
                       FAR_GLANCE in presence(entry.options)[:2] and glance.customised(entry.options.get(CONF_GLANCE_SETTINGS)),
                       bool(entry.options.get(CONF_LIMITS))),
        # The glance's settings, defaults filled in, and whether it's night now.
        "glance": glance.settings(entry.options.get(CONF_GLANCE_SETTINGS)),
        # The glance faces among the cards, by id, defaults filled in.
        "faces": faces.kept(entry.options.get(CONF_FACES), list(entry.options.get(CONF_ENTITIES, []))),
        "night": running.night if running else False,
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
    # The glance faces among them (unsaved), drawn by this thermostat with
    # the glance's shared settings.
    vol.Optional("entry_id"): str,
    vol.Optional(CONF_FACES, default={}): {cv.entity_id: dict},
    vol.Optional("glance"): dict,
    vol.Optional("weather"): vol.Any(None, cv.entity_id),
    vol.Optional(CONF_LIMITS, default={}): {cv.entity_id: dict},
})
@websocket_api.require_admin
@callback
def ws_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The cards as the thermostat would get them now, by entity: what the
    panel draws. Entities Home Assistant doesn't know have none."""
    get = hass.states.get
    names = {k: v.strip()[:NAME_LENGTH] for k, v in msg["names"].items() if v.strip()}
    entry = hass.config_entries.async_get_entry(msg["entry_id"]) if msg.get("entry_id") else None
    running = getattr(entry, "runtime_data", None) if entry and entry.state is ConfigEntryState.LOADED else None
    shown = {}
    devices = [e for e in msg["entities"] if not faces.is_face(e)]
    for entity_id in msg["entities"]:
        if faces.is_face(entity_id):
            if running is not None:
                shown[entity_id] = _face_preview(hass, running, entity_id, msg, names.get(entity_id), devices)
            continue
        state = get(entity_id)
        if state is not None:
            shown[entity_id] = cards.named(cards.card(state, get, msg[CONF_ALARMS], cards.limit_settings(msg[CONF_LIMITS])),
                                           names.get(entity_id))
    climate = get(msg["climate"]) if msg.get("climate") else None
    connection.send_result(msg["id"], {
        "cards": shown,
        "thermostat": cards.climate(climate) if climate is not None and climate.domain == "climate" else None,
    })


def _face_preview(hass: HomeAssistant, running: Any, face_id: str, msg: dict[str, Any], given: str | None,
                  devices: list[str]) -> dict[str, Any]:
    """A glance face's card as the Nest would get it from the panel's
    settings, and its lines in words (`said`) for the panel to draw."""
    face = faces.settings(msg[CONF_FACES].get(face_id))
    shared = msg.get("glance") if msg.get("glance") is not None else running.entry.options.get(CONF_GLANCE_SETTINGS)
    weather = msg["weather"] if "weather" in msg else running.entry.options.get(CONF_WEATHER)
    card = running.face_card(face_id, face, shared, given, weather, devices)
    chosen = glance.settings(faces.glance_raw(face, shared))
    sources = running.glance_sources(chosen, weather, devices)
    climate = _thermostat(hass, running.entry)["climate"]
    said = [sources.preview(spec, hass.states.get(climate) if climate else None)
            for spec in glance.lines_for(chosen["lines"], chosen["layout"])]
    return {**card, "said": said}


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
    # Each [lowest, highest] of what its ring sets; an older panel doesn't
    # send them: kept.
    vol.Optional(CONF_LIMITS): {cv.entity_id: {str: [vol.Any(None, vol.Coerce(float))]}},
    vol.Optional(CONF_GLANCE_SETTINGS): dict,
    # The glance faces among the cards, by id. An older panel doesn't send
    # them: kept.
    vol.Optional(CONF_FACES): {cv.entity_id: dict},
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
    wrong = [e for e in entities if (e.partition(".")[0] not in DOMAINS and not faces.is_face(e)) or e in own]
    if wrong:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, f"Can't be a card: {', '.join(wrong)}")
        return
    if CONF_FACES in msg:
        try:
            face_settings = {k: faces.SCHEMA(v) for k, v in msg[CONF_FACES].items() if k in entities and faces.is_face(k)}
        except vol.Invalid as err:
            connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, f"A glance face's settings: {err}")
            return
    else:
        face_settings = entry.options.get(CONF_FACES)
    if msg.get(CONF_WEATHER) and not msg[CONF_WEATHER].startswith("weather."):
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "Not a weather entity")
        return
    for key in (CONF_OUTDOOR, CONF_ROOM_SENSOR):
        if msg.get(key) and not msg[key].startswith("sensor."):
            connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "Not a sensor")
            return
    if CONF_GLANCE_SETTINGS in msg:
        try:
            glance_settings = glance.SCHEMA(msg[CONF_GLANCE_SETTINGS])
        except vol.Invalid as err:
            connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, f"The glance's settings: {err}")
            return
    else:
        glance_settings = entry.options.get(CONF_GLANCE_SETTINGS)
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
        CONF_GLANCE_SETTINGS: glance_settings,
        CONF_FACES: face_settings,
    }, {k: v.strip()[:NAME_LENGTH] for k, v in msg[CONF_NAMES].items() if k in entities and v.strip()},
        alarm_settings(msg[CONF_ALARMS]) if CONF_ALARMS in msg else entry.options.get(CONF_ALARMS),
        msg[CONF_LIMITS] if CONF_LIMITS in msg else entry.options.get(CONF_LIMITS))
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {"options": options})


def _running(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> Any:
    """The thermostat a glance command is for, loaded; else an error sent."""
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    running = getattr(entry, "runtime_data", None) if entry and entry.state is ConfigEntryState.LOADED else None
    if entry is None or entry.domain != DOMAIN or running is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No such thermostat")
        return None
    try:
        glance.SCHEMA(msg["glance"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, f"The glance's settings: {err}")
        return None
    return running


@websocket_api.websocket_command({
    vol.Required("type"): "fledge/glance_preview",
    vol.Required("entry_id"): str,
    vol.Required("glance"): dict,
    vol.Optional("night", default=False): bool,
    vol.Optional("weather"): vol.Any(None, cv.entity_id),
    vol.Optional("entities"): [cv.entity_id],
})
@websocket_api.require_admin
@callback
def ws_glance_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The glance as the Nest would get it now from the panel's settings
    (unsaved), each line in words for the preview, and whether it's night
    by those settings."""
    running = _running(hass, connection, msg)
    if running is None:
        return
    chosen = glance.settings(msg["glance"])
    weather = msg["weather"] if "weather" in msg else running.entry.options.get(CONF_WEATHER)
    entities = msg.get("entities") or running.entities
    night = msg["night"]
    nights = chosen["night"]
    layout = nights["layout"] if night else chosen["layout"]
    specs = glance.lines_for(nights["lines"] if night else chosen["lines"], layout, night)
    climate = _thermostat(hass, running.entry)["climate"]
    sources = running.glance_sources(chosen, weather, entities)
    shown, _ = running.glance_for(msg["glance"], night, weather, entities)
    connection.send_result(msg["id"], {
        "glance": shown,
        "lines": [sources.preview(spec, hass.states.get(climate) if climate else None) for spec in specs],
        "twelve": sources.twelve,
        "night_now": nights["mode"] != "off" and glance.is_night(hass, nights),
    })


@websocket_api.websocket_command({
    vol.Required("type"): "fledge/glance_try",
    vol.Required("entry_id"): str,
    vol.Required("glance"): dict,
    vol.Optional("night", default=False): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_glance_try(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Shows the panel's glance (unsaved) on the Nest for 20 seconds."""
    running = _running(hass, connection, msg)
    if running is None:
        return
    await running.async_try_glance(msg["glance"], msg["night"])
    connection.send_result(msg["id"], {"protocol": running.protocol})
