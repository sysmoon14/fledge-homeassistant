"""Fledge: Home Assistant on a Nest thermostat's own screen.

The thermostat (nestlocal) shows whatever cards are retained on
nestlocal/<serial>/set/ui, and asks for changes on nestlocal/<serial>/ui/
command. This keeps the cards up to date as the chosen devices change, and
carries out the commands: only for those devices, and only the ways
cards.command allows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.components.media_player import DATA_COMPONENT as MEDIA_PLAYERS
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (TrackTemplate, async_call_later, async_track_state_change_event,
                                         async_track_template_result, async_track_time_interval)
from homeassistant.helpers.template import Template
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import art, cards, faces, glance, panel, schedule
from .feeds import Feeds
from .config_flow import thermostats
from .const import (DOCS, CONF_FACES, CONF_LIMITS, CONF_GLANCE_SETTINGS, GLANCE_TRY_TOPIC, ALARM_RESULT_TOPIC, CONF_ALARMS, CONF_SHOW_THERMOSTAT, needs, FAR_GLANCE, presence, CONF_DIM_OFF, CONF_GLANCE, CONF_NAMES, CONF_THERMOSTAT, CONF_WEATHER, ALERT_TOPIC, EVENT_NOTIFICATION, PLATFORMS, ART_TOPIC, CARDS_TOPIC, CHECK_TOPIC, COMMAND_TOPIC, DOMAIN, PROTOCOL_TOPIC, CONF_AREAS, CONF_CLIMATES, CONF_ENTITIES, CONF_EXCLUDE, CONF_MEDIA,
                    CONF_SERIAL, CONF_WAKE, CONF_WAKE_DEVICE, TOPIC_PREFIX, WAKE_CHOOSER)

_LOGGER = logging.getLogger(__name__)
# The largest picture read for a battery face.
PICTURE_BYTES = 8 * 1024 * 1024


def _pictured(shown: dict[str, Any], key: str) -> None:
    """A glance as built, its battery's picture (if it has one) named by
    the key the Nest is sent it on."""
    cell = shown.get("battery") or {}
    if cell.get("image"):
        cell["picture"] = key

type FledgeConfigEntry = ConfigEntry["Thermostat"]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

NOTIFY_SCHEMA = vol.Schema({
    vol.Required("message"): cv.string,
    vol.Optional("title", default=""): cv.string,
    vol.Optional("icon"): cv.icon,
    vol.Optional("camera"): cv.entity_domain("camera"),
    vol.Optional("timeout", default=30): vol.All(vol.Coerce(int), vol.Range(min=3, max=3600)),
    vol.Optional("tag"): cv.string,
    vol.Optional("thermostat"): vol.All(cv.ensure_list, [cv.string]),
})
DISMISS_SCHEMA = vol.Schema({vol.Required("tag"): cv.string,
                             vol.Optional("thermostat"): vol.All(cv.ensure_list, [cv.string])})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """fledge.notify and fledge.dismiss: notifications on the thermostats'
    screens. A press or a dismissal fires fledge_notification with the tag."""

    def running(call: ServiceCall) -> list[Thermostat]:
        """The thermostats a call is for: the devices chosen (the Nest's own,
        or its cards'), else all of them."""
        everyone = [entry.runtime_data for entry in hass.config_entries.async_loaded_entries(DOMAIN)]
        chosen = call.data.get("thermostat")
        if not chosen:
            return everyone
        devices = dr.async_get(hass)
        serials = {d.serial_number for d in (devices.async_get(i) for i in chosen) if d is not None}
        return [t for t in everyone if t.serial in serials]

    async def notify(call: ServiceCall) -> ServiceResponse:
        tag = call.data.get("tag") or uuid.uuid4().hex[:8]
        for thermostat in running(call):
            await thermostat.async_notify(tag, call.data)
        return {"tag": tag}

    async def dismiss(call: ServiceCall) -> None:
        for thermostat in running(call):
            await thermostat.async_dismiss(call.data["tag"])

    hass.services.async_register(DOMAIN, "notify", notify, schema=NOTIFY_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "dismiss", dismiss, schema=DISMISS_SCHEMA)
    panel.async_setup(hass)
    schedule.async_setup(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FledgeConfigEntry) -> bool:
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT is not ready")
    if CONF_ENTITIES not in entry.options:
        _migrate(hass, entry)
    thermostat = Thermostat(hass, entry)
    await thermostat.async_start()
    entry.runtime_data = thermostat
    entry.async_on_unload(thermostat.async_stop)
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await panel.async_show(hass, thermostat.version)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FledgeConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    panel.async_hide(hass, entry)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """No more cards: the thermostat goes back to how it was."""
    serial = entry.data[CONF_SERIAL]
    ir.async_delete_issue(hass, DOMAIN, f"outdated_{serial}")
    await mqtt.async_publish(hass, CARDS_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial), "", retain=True)
    for entity in entry.options.get(CONF_ENTITIES, []):
        if entity.startswith(("media_player.", "camera.")):
            await mqtt.async_publish(hass, ART_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial, entity=entity), "",
                                     retain=True)


async def _reload(hass: HomeAssistant, entry: FledgeConfigEntry) -> None:
    """Changed options: other devices start afresh; only what it wakes to
    (an automation's Wake to, say) is just sent on."""
    thermostat = getattr(entry, "runtime_data", None)
    if (thermostat is not None and list(entry.options.get(CONF_ENTITIES, [])) == thermostat.entities
            and entry.options.get(CONF_WEATHER) == thermostat.weather):
        await thermostat.async_wake_changed()
        return
    await hass.config_entries.async_reload(entry.entry_id)


def _area_lights(hass: HomeAssistant, area_id: str, left_out: set[str]) -> list[str]:
    """An area's lights by name: its own, and its devices' that are not
    moved elsewhere. Hidden is about dashboards (a light group often is):
    still listed."""
    devices, entities = dr.async_get(hass), er.async_get(hass)
    found = {e.entity_id: e for e in er.async_entries_for_area(entities, area_id)}
    for device in dr.async_entries_for_area(devices, area_id):
        for e in er.async_entries_for_device(entities, device.id):
            if e.area_id in (None, area_id):
                found.setdefault(e.entity_id, e)
    lights = [e for e in found.values() if e.domain == "light" and not e.disabled_by and e.entity_id not in left_out]
    lights.sort(key=lambda e: (e.name or e.original_name or e.entity_id).lower())
    return [e.entity_id for e in lights]


def _migrate(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """From rooms of lights (before 0.4.4) to a card each: the rooms'
    lights as they are now, then the thermostats and media players."""
    old = entry.options
    left_out = set(old.get(CONF_EXCLUDE, []))
    entities: list[str] = []
    for area_id in old.get(CONF_AREAS, []):
        if ar.async_get(hass).async_get_area(area_id) is not None:
            entities += [e for e in _area_lights(hass, area_id, left_out) if e not in entities]
    entities += [*old.get(CONF_CLIMATES, []), *old.get(CONF_MEDIA, [])]
    wake = old.get(CONF_WAKE, WAKE_CHOOSER)
    options: dict[str, Any] = {CONF_ENTITIES: entities, CONF_WAKE: wake if wake in ("chooser", "thermostat") else WAKE_CHOOSER}
    if wake.startswith("entity:"):
        options[CONF_WAKE_DEVICE] = wake.removeprefix("entity:")
    elif wake.startswith("area:"):
        lights = _area_lights(hass, wake.removeprefix("area:"), left_out)
        if lights:
            options[CONF_WAKE_DEVICE] = lights[0]
    _LOGGER.info("Fledge: the cards are now a device each: %s", ", ".join(entities) or "none")
    hass.config_entries.async_update_entry(entry, options=options)


# How often, at most, a change of one of the glance's own sensors (a line's,
# the big number's) reaches the Nest, in seconds.
LINES_EVERY = 15
# How often the glance's calendars are asked for their coming events.
EVENTS_EVERY = timedelta(minutes=5)

# How long an alarm has to change state after a command before it's taken
# that it didn't (Alarmo, given a wrong code, says nothing).
ALARM_ANSWER = 3


class Thermostat:
    """One Nest's cards: kept published, and its commands carried out."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        serial = entry.data[CONF_SERIAL]
        self.cards_topic = CARDS_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial)
        self.command_topic = COMMAND_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial)
        self.protocol_topic = PROTOCOL_TOPIC.format(prefix=TOPIC_PREFIX, serial=serial)
        # What the thermostat's build understands; None until it says.
        self.protocol: int | None = None
        self.version = ""
        # Called as what it wakes to changes (the Wake to select).
        self.wake_listeners: list[Any] = []
        # The notifications shown and not yet answered, by tag, until when an
        # answer is taken (monotonic seconds): an answer to any other, from
        # whoever else can publish to the broker, fires nothing.
        self.notifications: dict[str, float] = {}
        self.entities: list[str] = list(entry.options.get(CONF_ENTITIES, []))
        self.weather: str | None = entry.options.get(CONF_WEATHER)
        self.serial = serial
        # Wrong alarm codes from this thermostat, by alarm.
        self.tries: dict[str, cards.Tries] = {}
        # Each media player's picture as last sent: its entity_picture, or None.
        self.art: dict[str, str | None] = {}
        self.last: str | None = None
        self._unsubscribe: list[Any] = []
        self._tracking: Any = None
        self._publisher = Debouncer(hass, _LOGGER, cooldown=0.2, immediate=True, function=self._publish)
        # The glance's own sensors: the first change at once, the rest held.
        self._lines = Debouncer(hass, _LOGGER, cooldown=LINES_EVERY, immediate=True, function=self._publish)
        # The weather and a room's temperature, for the Nest itself.
        self.feeds = Feeds(hass, serial, dict(entry.options), self._forecasts_changed)
        # Whether the Nest has the night's glance, and who's told as it turns
        # (the Night glance sensor).
        self.night = False
        self.night_listeners: list[Any] = []
        # What the glance follows, beyond the cards (glance.watched).
        self._glance_tracking: list[Any] = []
        # The glance's calendars' coming events, and the calendars they are
        # of; None until fetched, or where a calendar can't list them.
        self.events: list[dict[str, Any]] | None = None
        self._events_of: list[str] = []

    async def async_start(self) -> None:
        if self.entities:
            # The sun too: Automatic wakes to the lights after sunset; and a
            # chosen light group's own lights, listed on its card.
            weather = [self.entry.options[CONF_WEATHER]] if self.entry.options.get(CONF_WEATHER) else []
            # A face's actions too: a switched one shows whether it's on.
            followed = [e for e in self.entities if not faces.is_face(e)]
            self._tracking = async_track_state_change_event(
                self.hass, [*followed, *self._members(), *faces.entities(self.faces), *weather, "sun.sun"], self._changed)
        self._unsubscribe.append(await mqtt.async_subscribe(self.hass, self.command_topic, self._command))
        self._unsubscribe.append(await mqtt.async_subscribe(self.hass, self.protocol_topic, self._protocol))
        self._unsubscribe.append(self.hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._device_changed))
        # A build from before it said: older than this integration needs.
        self._unsubscribe.append(async_call_later(self.hass, 60, self._check_protocol))
        self.version = str((await async_get_integration(self.hass, DOMAIN)).version or "")
        await self._updated()
        self._link()
        self._watch_glance()
        await self._publish()
        await self.feeds.async_start()

    async def _updated(self) -> None:
        """This integration just updated: the thermostat checks for its own
        update now, rather than at its daily check, as the two often come
        out together. The version each thermostat last saw is kept."""
        store: Store[dict[str, str]] = Store(self.hass, 1, f"{DOMAIN}.versions")
        seen = await store.async_load() or {}
        if seen.get(self.serial) == self.version:
            return
        _LOGGER.info("Fledge %s: asking thermostat %s to check for its update", self.version, self.serial)
        await mqtt.async_publish(self.hass, CHECK_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial), "PRESS")
        await store.async_save({**seen, self.serial: self.version})

    @callback
    def _link(self) -> None:
        """The cards as a device of their own, connected via the thermostat's
        (nestlocal's, over MQTT): the integration's page leads to it, and it
        to the thermostat and all its entities. A device belongs to one
        integration, so this can't be the thermostat's own."""
        thermostat = thermostats(self.hass).get(self.serial)
        name = (thermostat.name_by_user or thermostat.name) if thermostat else None
        devices = dr.async_get(self.hass)
        device = devices.async_get_or_create(
            config_entry_id=self.entry.entry_id, identifiers={(DOMAIN, self.serial)},
            name=f"{name or 'Nest Thermostat'} cards", manufacturer="Fledge", model="Home screen cards",
            serial_number=self.serial, sw_version=self.version)
        if thermostat is not None and device.via_device_id != thermostat.id:
            devices.async_update_device(device.id, via_device_id=thermostat.id, area_id=device.area_id or thermostat.area_id)

    @callback
    def _device_changed(self, event: Event) -> None:
        """The thermostat renamed: its own card on the Nest, and the cards'
        device, say so straight away."""
        thermostat = thermostats(self.hass).get(self.serial)
        if thermostat is None or event.data.get("device_id") != thermostat.id:
            return
        if event.data.get("action") == "update" and "name_by_user" not in (event.data.get("changes") or {}):
            return
        self._link()
        self.hass.async_create_task(self._publisher.async_call())

    @property
    def faces(self) -> dict[str, dict[str, Any]]:
        """The glance faces among the cards, by id, their settings filled in."""
        return faces.kept(self.entry.options.get(CONF_FACES), self.entities)

    def face_card(self, face_id: str, face: dict[str, Any], shared: dict[str, Any] | None = None,
                  given: str | None = None, weather: Any = False, entities: list[str] | None = None) -> dict[str, Any]:
        """A glance face's card as the Nest takes it: drawn as the glance
        is, with the glance's shared settings (`shared`: the panel's unsaved
        ones, else the saved)."""
        if shared is None:
            shared = self.entry.options.get(CONF_GLANCE_SETTINGS)
        shown, _ = self.glance_for(faces.glance_raw(face, shared), False, weather, entities)
        _pictured(shown, f"battery:{face_id}")
        return faces.card(face_id, face, shown, self.hass.states.get, given)

    def _glances(self) -> list[dict[str, Any]]:
        """Every glance the Nest is sent, its settings filled in: the glance
        itself (if anything shows it) and each face's."""
        far, near, _ = presence(self.entry.options)
        shared = self.entry.options.get(CONF_GLANCE_SETTINGS)
        out = [glance.settings(shared)] if FAR_GLANCE in (far, near) else []
        return out + [glance.settings(faces.glance_raw(face, shared)) for face in self.faces.values()]

    def own_glance(self) -> bool:
        """Whether the Nest is sent a glance that isn't the glance as it was
        (so its build must know protocol 11): one is shown, and customised."""
        far, near, _ = presence(self.entry.options)
        return FAR_GLANCE in (far, near) and glance.customised(self.entry.options.get(CONF_GLANCE_SETTINGS))

    def thermostat_name(self) -> str | None:
        """What the thermostat was called in Home Assistant, if renamed."""
        thermostat = thermostats(self.hass).get(self.serial)
        name = thermostat.name_by_user.strip() if thermostat and thermostat.name_by_user else ""
        return name or None

    @callback
    def _protocol(self, message: mqtt.ReceiveMessage) -> None:
        try:
            self.protocol = int(message.payload)
        except (TypeError, ValueError):
            return
        self._link()
        self._check_protocol()

    @callback
    def _check_protocol(self, _now: Any = None) -> None:
        """A repair to make while the thermostat's build is older than this
        integration needs."""
        issue = f"outdated_{self.serial}"
        if self.protocol is not None and self.protocol >= needs(self.entities, self.entry.options.get(CONF_SHOW_THERMOSTAT, True),
                                                                self.own_glance(), cards.limits_level(self.entry.options.get(CONF_LIMITS))):
            ir.async_delete_issue(self.hass, DOMAIN, issue)
        else:
            ir.async_create_issue(self.hass, DOMAIN, issue, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                                  translation_key="thermostat_outdated",
                                  learn_more_url=f"{DOCS}updates.html",
                                  translation_placeholders={"serial": self.serial})

    @callback
    def _watch_glance(self) -> None:
        """Follows what the glance shows besides the cards: its sensors, the
        calendar, the night's sensor, its templates; and each minute (the
        night by the clock or the sun, rain coming, a template's "ago")."""
        for unsubscribe in self._glance_tracking:
            unsubscribe()
        self._glance_tracking = []
        shown = self._glances()
        if not shown:
            return
        # The night's sensor (a bedtime switch) and who's home reach the Nest
        # at once; a line's sensor, which may change every second, at most
        # every LINES_EVERY, so a chatty one doesn't keep the Nest awake.
        prompt = [glance.night_sensor(chosen) for chosen in shown if glance.night_sensor(chosen)]
        if any(glance.uses(chosen, "people") for chosen in shown):
            prompt += self.hass.states.async_entity_ids("person")
        if prompt:
            self._glance_tracking.append(async_track_state_change_event(self.hass, sorted(set(prompt)), self._changed))
        watched = sorted({e for chosen in shown for e in glance.watched(chosen) if e not in self.entities})
        if watched:
            self._glance_tracking.append(async_track_state_change_event(self.hass, watched, self._line_changed))
        if any(glance.wants_events(chosen) for chosen in shown):
            # The next event: as a calendar changes, and every few minutes.
            # The calendars are shared by the glance and its faces.
            self._glance_tracking.append(async_track_state_change_event(self.hass, glance.calendars(shown[0]), self._events_changed))
            self._glance_tracking.append(async_track_time_interval(self.hass, self._events_changed, EVENTS_EVERY))
            self._events_changed()
        else:
            self.events, self._events_of = None, []
        sources = [source for chosen in shown for source in glance.templates(chosen)]
        tracked = []
        for source in sources:
            template = Template(source, self.hass)
            try:
                template.ensure_valid()
            except Exception:  # noqa: BLE001 - a template that doesn't read: its line stays empty
                continue
            tracked.append(TrackTemplate(template, None))
        if tracked:
            info = async_track_template_result(self.hass, tracked, self._glance_changed)
            self._glance_tracking.append(info.async_remove)
        self._glance_tracking.append(async_track_time_interval(self.hass, self._glance_changed, timedelta(minutes=1)))

    @callback
    def _glance_changed(self, *_: Any) -> None:
        """A template's result, or a minute gone: the glance again (sent only
        if it changed)."""
        self.hass.async_create_task(self._publisher.async_call())

    @callback
    def _events_changed(self, *_: Any) -> None:
        self.hass.async_create_task(self._fetch_events())

    async def _fetch_events(self) -> None:
        """The glance's calendars' events for the days ahead, then the
        glance again. A calendar that can't list them leaves events None:
        the glance falls back on what the calendar entity says."""
        chosen = glance.settings(self.entry.options.get(CONF_GLANCE_SETTINGS))
        wanted = glance.calendars(chosen)
        now = dt_util.now()
        found: list[dict[str, Any]] | None = []
        for calendar in wanted:
            try:
                response = await self.hass.services.async_call(
                    "calendar", "get_events",
                    {"entity_id": calendar, "start_date_time": now, "end_date_time": now + glance.EVENTS_AHEAD},
                    blocking=True, return_response=True)
            except Exception:  # noqa: BLE001 - a calendar that can't: what its entity says instead
                _LOGGER.debug("No events from %s", calendar, exc_info=True)
                found = None
                break
            found += glance.parse_events(((response or {}).get(calendar) or {}).get("events") or [])
        self.events, self._events_of = found, wanted
        await self._publisher.async_call()

    @callback
    def _line_changed(self, _event: Event) -> None:
        self.hass.async_create_task(self._lines.async_call())

    @callback
    def _forecasts_changed(self) -> None:
        """New forecasts (Feeds): the glance's rain and high and low."""
        self._glance_changed()

    def glance_sources(self, chosen: dict[str, Any], weather: str | None, entities: list[str] | None = None) -> glance.Sources:
        """What the glance's lines are worked out from, for `chosen` settings."""
        wanted = glance.calendars(chosen)
        return glance.Sources(self.hass, glance.twelve_hour(self.hass, chosen["hours"]), entities or self.entities,
                              self.feeds.hourly, self.feeds.daily, weather, wanted[0] if wanted else None,
                              events=self.events if wanted == self._events_of else None,
                              skip_all_day=chosen["skip_all_day"], words=chosen.get("words"),
                              month_first=glance.month_first(self.hass, chosen["date"]))

    def glance_for(self, raw: dict[str, Any] | None, night: bool | None = None, weather: Any = False,
                   entities: list[str] | None = None) -> tuple[dict[str, Any], bool]:
        """The glance as the Nest takes it, from glance settings `raw` (the
        saved ones, or the panel's to try), at night as the settings say
        unless `night` says; and whether it's the night's. `weather` and
        `entities`: the panel's unsaved weather entity and cards."""
        chosen = glance.settings(raw)
        if night is None:
            night = chosen["night"]["mode"] != "off" and glance.is_night(self.hass, chosen["night"])
        offset = dt_util.now().utcoffset()
        entity = self.entry.options.get(CONF_WEATHER) if weather is False else weather
        base = cards.glance(self.hass.states.get(entity) if entity else None, offset.total_seconds() if offset else 0)
        return glance.build(self.glance_sources(chosen, entity, entities), chosen, night, base), night

    async def async_try_glance(self, raw: dict[str, Any] | None, night: bool) -> None:
        """Shows the panel's glance (unsaved) on the Nest for a while."""
        shown, _ = self.glance_for(raw, night)
        await mqtt.async_publish(self.hass, GLANCE_TRY_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial),
                                 json.dumps(shown, separators=(",", ":")))

    @callback
    def async_stop(self) -> None:
        self.feeds.async_stop()
        for unsubscribe in self._glance_tracking:
            unsubscribe()
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        if self._tracking:
            self._tracking()
        self._publisher.async_shutdown()
        self._lines.async_shutdown()

    @callback
    def _changed(self, event: Event) -> None:
        self.hass.async_create_task(self._publisher.async_call())

    def _members(self) -> list[str]:
        """The chosen light groups' own lights."""
        return [m for e in self.entities for m in cards.members(self.hass.states.get(e))]

    async def async_wake_changed(self) -> None:
        """What it wakes to (or the glance) changed: sent on, and Wake to
        and the glance's entities show it."""
        self._watch_glance()
        self._check_protocol()
        await self._publish()
        for listener in list(self.wake_listeners):
            listener()

    async def _publish(self) -> None:
        options = self.entry.options
        sun = self.hass.states.get("sun.sun")
        night = sun is not None and sun.state == "below_horizon"
        shown = cards.cards(self.entities, self.hass.states.get, options.get(CONF_WAKE, WAKE_CHOOSER),
                            options.get(CONF_WAKE_DEVICE), night, options.get(CONF_NAMES),
                            options.get(CONF_THERMOSTAT, 0), options.get(CONF_ALARMS),
                            {face_id: self.face_card(face_id, face, given=(options.get(CONF_NAMES) or {}).get(face_id))
                             for face_id, face in self.faces.items()},
                             options.get(CONF_LIMITS))
        far, near, idle = presence(options)
        shown["presence"] = {"far": far, "near": near}
        shown["card_idle"] = idle
        shown["dim_off"] = options.get(CONF_DIM_OFF, True)
        if options.get(CONF_SHOW_THERMOSTAT, True) is False:
            shown["show_thermostat"] = False
        name = self.thermostat_name()
        if name:
            shown["thermostat_name"] = name
        night = False
        if FAR_GLANCE in (far, near):
            shown["glance"], night = self.glance_for(options.get(CONF_GLANCE_SETTINGS))
            _pictured(shown["glance"], "battery:glance")
        if night != self.night:
            self.night = night
            for listener in list(self.night_listeners):
                listener()
        payload = json.dumps(shown, separators=(",", ":"))
        if payload != self.last:
            self.last = payload
            await mqtt.async_publish(self.hass, self.cards_topic, payload, retain=True)
        # The battery faces' pictures, each on its own key: sent as it's
        # chosen or changed, cleared once it's no longer wanted.
        wanted = {cell["picture"]: cell["image"] for cell in (
            (holder.get("glance") or {}).get("battery") or {} for holder in (shown, *shown["cards"])) if cell.get("picture")}
        for key in [k for k in self.art if k.startswith("battery:") and k not in wanted]:
            del self.art[key]
            await mqtt.async_publish(self.hass, ART_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial, entity=key), b"", retain=True)
        for key, image in wanted.items():
            if self.art.get(key) != image:
                self.art[key] = image
                self.hass.async_create_task(self._send_picture(key, image))
        for entity in self.entities:
            state = self.hass.states.get(entity)
            if entity.startswith("camera.") and state is not None:
                # A new still as the camera's picture changes (its token
                # turns over every few minutes), and as it comes back.
                key = None if state.state == "unavailable" else state.attributes.get("entity_picture")
                if key and self.art.get(entity) != key:
                    self.art[entity] = key
                    self.hass.async_create_task(self._send_still(entity))
                continue
            if not entity.startswith("media_player.") or state is None:
                continue
            picture = None if state.state in ("off", "unavailable") else state.attributes.get("entity_picture")
            if entity not in self.art or self.art[entity] != picture:
                self.art[entity] = picture
                self.hass.async_create_task(self._send_art(entity, picture))

    async def _send_art(self, entity: str, picture: str | None) -> None:
        """A media player's album art, shrunk for the thermostat, or none."""
        png: bytes | None = None
        player = self.hass.data[MEDIA_PLAYERS].get_entity(entity) if picture and MEDIA_PLAYERS in self.hass.data else None
        if player is not None:
            try:
                data, _ = await player.async_get_media_image()
            except Exception:  # noqa: BLE001 - a player failing to fetch its art: none shown
                _LOGGER.debug("No album art from %s", entity, exc_info=True)
                data = None
            if data:
                png = await self.hass.async_add_executor_job(art.thumbnail, data)
        if self.art.get(entity) != picture:
            return  # the track changed again meanwhile
        topic = ART_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial, entity=entity)
        await mqtt.async_publish(self.hass, topic, png or b"", retain=True)

    async def _send_picture(self, key: str, image: str) -> None:
        """A battery face's picture, shrunk for the thermostat, or none if
        it can't be read."""
        data = await self._image(image)
        png = await self.hass.async_add_executor_job(art.cutout, data) if data else None
        if not png:
            _LOGGER.warning("Fledge: no picture to show from %s", image)
        if self.art.get(key) != image:
            return  # changed again meanwhile
        topic = ART_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial, entity=key)
        await mqtt.async_publish(self.hass, topic, png or b"", retain=True)

    async def _image(self, image: str) -> bytes | None:
        """A picture's bytes: one uploaded to Home Assistant
        (/api/image/serve/<id>/...), a file in its www folder (/local/...),
        or a web address."""
        from pathlib import Path
        path: Path | None = None
        if image.startswith("/api/image/serve/"):
            image_id = image.split("/")[4] if len(image.split("/")) > 4 else ""
            if image_id.isalnum():
                path = Path(self.hass.config.path("image", image_id, "original"))
        elif image.startswith("/local/"):
            www = Path(self.hass.config.path("www")).resolve()
            candidate = (www / image.removeprefix("/local/").split("?")[0]).resolve()
            if candidate.is_relative_to(www):
                path = candidate
        elif image.startswith(("http://", "https://")):
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            try:
                async with asyncio.timeout(15):
                    response = await async_get_clientsession(self.hass).get(image)
                    if response.status == 200 and (response.content_length or 0) <= PICTURE_BYTES:
                        return await response.content.read(PICTURE_BYTES)
            except Exception:  # noqa: BLE001 - a picture that can't be fetched: none shown
                _LOGGER.debug("No picture from %s", image, exc_info=True)
            return None
        if path is None:
            return None

        def read() -> bytes | None:
            try:
                return path.read_bytes() if path.stat().st_size <= PICTURE_BYTES else None
            except OSError:
                return None
        return await self.hass.async_add_executor_job(read)

    async def _still(self, camera: str) -> bytes | None:
        """A camera's picture now, about display-sized, as JPEG, or None."""
        from homeassistant.components.camera import async_get_image
        try:
            # The camera (Frigate) makes it this small itself, or Home
            # Assistant scales its JPEG cheaply: little left to do here.
            image = await async_get_image(self.hass, camera, timeout=10, width=art.SCREEN, height=art.SCREEN)
        except Exception:  # noqa: BLE001 - a camera that can't: no picture
            _LOGGER.debug("No picture from %s", camera, exc_info=True)
            return None
        return await self.hass.async_add_executor_job(art.screen, image.content)

    async def _send_still(self, camera: str) -> None:
        png = await self._still(camera)
        if png:
            topic = ART_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial, entity=camera)
            await mqtt.async_publish(self.hass, topic, png, retain=True)

    async def async_notify(self, tag: str, data: dict[str, Any]) -> None:
        """A notification on the thermostat's screen, with the camera's
        picture now if one is named."""
        picture = None
        if camera := data.get("camera"):
            png = await self._still(camera)
            if png:
                picture = f"alert:{tag}"
                topic = ART_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial, entity=picture)
                await mqtt.async_publish(self.hass, topic, png)
        payload = {"id": tag, "title": data.get("title", ""), "message": data["message"], "icon": data.get("icon", ""),
                   "timeout": data.get("timeout", 30), "picture": picture}
        now = time.monotonic()
        self.notifications = {t: until for t, until in self.notifications.items() if until > now}
        self.notifications[tag] = now + data.get("timeout", 30) + 30
        await mqtt.async_publish(self.hass, ALERT_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial),
                                 json.dumps(payload, separators=(",", ":")))

    async def async_dismiss(self, tag: str) -> None:
        self.notifications.pop(tag, None)
        await mqtt.async_publish(self.hass, ALERT_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial),
                                 json.dumps({"id": tag, "dismiss": True}, separators=(",", ":")))

    @callback
    def _command(self, message: mqtt.ReceiveMessage) -> None:
        try:
            payload = json.loads(message.payload)
        except (TypeError, ValueError):
            return
        allowed = [*self.entities, *self._members(), *faces.entities(self.faces)]
        limits = cards.with_members(self.entry.options.get(CONF_LIMITS), self.hass.states.get)
        call = cards.command(payload, allowed, self.hass.states.get, limits) if isinstance(payload, dict) else None
        if call is None:
            _LOGGER.warning("Ignoring a command from the thermostat it may not give: %s", cards.redacted(payload))
            return
        if call.domain == "alarm_control_panel":
            self.hass.async_create_task(self._alarm(call))
            return
        if call.domain == DOMAIN and call.service == "snapshot":
            self.hass.async_create_task(self._send_still(call.entity_id))
            return
        if call.domain == DOMAIN and call.service == "notification":
            if self.notifications.pop(call.entity_id, 0) < time.monotonic():
                _LOGGER.warning("Ignoring an answer to a notification not showing: %s", call.entity_id)
                return
            self.hass.bus.async_fire(EVENT_NOTIFICATION, {"tag": call.entity_id, "action": call.data["action"],
                                                          "serial": self.serial})
            return
        self.hass.async_create_task(self.hass.services.async_call(
            call.domain, call.service, {"entity_id": call.entity_id, **call.data}, blocking=False))

    async def _alarm(self, call: cards.Call) -> None:
        """An alarm armed or disarmed from the thermostat, and what came of
        it said back (<base>/set/alarm_result). Alarmo's own arm service is
        used where it's Alarmo's (arming anyway, and why it didn't); a code
        it doesn't take raises nothing, so an alarm whose state hasn't
        changed soon after didn't take the code. The code is never logged."""
        entity = call.entity_id
        tries = self.tries.setdefault(entity, cards.Tries())
        left = tries.locked(time.monotonic())
        if left > 0:
            await self._alarm_result(entity, ok=False, error="locked", locked_for=math.ceil(left))
            return
        state = self.hass.states.get(entity)
        if state is None:
            await self._alarm_result(entity, ok=False, error="failed")
            return
        before = (state.state, state.last_changed)
        registered = er.async_get(self.hass).async_get(entity)
        alarmo = registered is not None and registered.platform == "alarmo"
        changed = asyncio.Event()
        refused: dict[str, Any] = {}

        @callback
        def _changed(_event: Event) -> None:
            changed.set()

        @callback
        def _refused(event: Event) -> None:
            if event.data.get("entity_id") == entity:
                refused.update(event.data)
                changed.set()

        stop = [async_track_state_change_event(self.hass, [entity], _changed)]
        if alarmo:
            stop.append(self.hass.bus.async_listen("alarmo_failed_to_arm", _refused))
        code = {"code": call.data["code"]} if call.data.get("code") else {}
        outcome = "ok"
        try:
            if alarmo and call.service != "alarm_disarm":
                mode = call.service.removeprefix("alarm_arm_")
                force = {"force": True} if call.data.get("force") else {}
                await self.hass.services.async_call(
                    "alarmo", "arm", {"entity_id": entity, "mode": "custom" if mode == "custom_bypass" else mode,
                                      **code, **force}, blocking=True)
            else:
                await self.hass.services.async_call(call.domain, call.service, {"entity_id": entity, **code},
                                                    blocking=True)
        except ServiceValidationError:
            outcome = "rejected"
        except HomeAssistantError:
            outcome = "failed"
        if outcome == "ok":
            try:
                async with asyncio.timeout(ALARM_ANSWER):
                    await changed.wait()
            except TimeoutError:
                pass
            now = self.hass.states.get(entity)
            if refused:
                outcome = {"invalid_code": "rejected", "open_sensors": "open_sensors"}.get(refused.get("reason"), "failed")
            elif now is None or (now.state, now.last_changed) == before:
                outcome = "rejected"
        for unsubscribe in stop:
            unsubscribe()
        _LOGGER.info("Alarm %s: %s from thermostat %s: %s", entity, call.service, self.serial, outcome)
        if outcome == "ok":
            tries.passed()
            await self._alarm_result(entity, ok=True)
        elif outcome == "rejected":
            remaining, locked = tries.failed(time.monotonic())
            if locked:
                await self._alarm_result(entity, ok=False, error="locked", locked_for=locked)
            else:
                await self._alarm_result(entity, ok=False, error="rejected", tries=remaining)
        elif outcome == "open_sensors":
            await self._alarm_result(entity, ok=False, error="open_sensors",
                                     sensors=cards.sensor_names(refused.get("sensors"), self.hass.states.get))
        else:
            await self._alarm_result(entity, ok=False, error="failed")

    async def _alarm_result(self, entity: str, **result: Any) -> None:
        await mqtt.async_publish(self.hass, ALARM_RESULT_TOPIC.format(prefix=TOPIC_PREFIX, serial=self.serial),
                                 json.dumps({"alarm": entity, **result}, separators=(",", ":")))
