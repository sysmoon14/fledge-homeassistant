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
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import art, cards, panel, schedule
from .feeds import Feeds
from .config_flow import thermostats
from .const import (ALARM_RESULT_TOPIC, CONF_ALARMS, CONF_SHOW_THERMOSTAT, needs, FAR_GLANCE, presence, CONF_DIM_OFF, CONF_GLANCE, CONF_NAMES, CONF_THERMOSTAT, CONF_WEATHER, ALERT_TOPIC, EVENT_NOTIFICATION, PLATFORMS, ART_TOPIC, CARDS_TOPIC, CHECK_TOPIC, COMMAND_TOPIC, DOMAIN, PROTOCOL_TOPIC, CONF_AREAS, CONF_CLIMATES, CONF_ENTITIES, CONF_EXCLUDE, CONF_MEDIA,
                    CONF_SERIAL, CONF_WAKE, CONF_WAKE_DEVICE, TOPIC_PREFIX, WAKE_CHOOSER)

_LOGGER = logging.getLogger(__name__)

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
        # The weather and a room's temperature, for the Nest itself.
        self.feeds = Feeds(hass, serial, dict(entry.options))

    async def async_start(self) -> None:
        if self.entities:
            # The sun too: Automatic wakes to the lights after sunset; and a
            # chosen light group's own lights, listed on its card.
            weather = [self.entry.options[CONF_WEATHER]] if self.entry.options.get(CONF_WEATHER) else []
            self._tracking = async_track_state_change_event(
                self.hass, [*self.entities, *self._members(), *weather, "sun.sun"], self._changed)
        self._unsubscribe.append(await mqtt.async_subscribe(self.hass, self.command_topic, self._command))
        self._unsubscribe.append(await mqtt.async_subscribe(self.hass, self.protocol_topic, self._protocol))
        self._unsubscribe.append(self.hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._device_changed))
        # A build from before it said: older than this integration needs.
        self._unsubscribe.append(async_call_later(self.hass, 60, self._check_protocol))
        self.version = str((await async_get_integration(self.hass, DOMAIN)).version or "")
        await self._updated()
        self._link()
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
        if self.protocol is not None and self.protocol >= needs(self.entities, self.entry.options.get(CONF_SHOW_THERMOSTAT, True)):
            ir.async_delete_issue(self.hass, DOMAIN, issue)
        else:
            ir.async_create_issue(self.hass, DOMAIN, issue, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                                  translation_key="thermostat_outdated",
                                  translation_placeholders={"serial": self.serial})

    @callback
    def async_stop(self) -> None:
        self.feeds.async_stop()
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        if self._tracking:
            self._tracking()
        self._publisher.async_shutdown()

    @callback
    def _changed(self, event: Event) -> None:
        self.hass.async_create_task(self._publisher.async_call())

    def _members(self) -> list[str]:
        """The chosen light groups' own lights."""
        return [m for e in self.entities for m in cards.members(self.hass.states.get(e))]

    async def async_wake_changed(self) -> None:
        """What it wakes to changed: sent on, and Wake to shows it."""
        await self._publish()
        for listener in list(self.wake_listeners):
            listener()

    async def _publish(self) -> None:
        options = self.entry.options
        sun = self.hass.states.get("sun.sun")
        night = sun is not None and sun.state == "below_horizon"
        shown = cards.cards(self.entities, self.hass.states.get, options.get(CONF_WAKE, WAKE_CHOOSER),
                            options.get(CONF_WAKE_DEVICE), night, options.get(CONF_NAMES),
                            options.get(CONF_THERMOSTAT, 0), options.get(CONF_ALARMS))
        far, near, idle = presence(options)
        shown["presence"] = {"far": far, "near": near}
        shown["card_idle"] = idle
        shown["dim_off"] = options.get(CONF_DIM_OFF, True)
        if options.get(CONF_SHOW_THERMOSTAT, True) is False:
            shown["show_thermostat"] = False
        name = self.thermostat_name()
        if name:
            shown["thermostat_name"] = name
        if FAR_GLANCE in (far, near):
            offset = dt_util.now().utcoffset()
            weather = self.hass.states.get(options[CONF_WEATHER]) if options.get(CONF_WEATHER) else None
            shown["glance"] = cards.glance(weather, offset.total_seconds() if offset else 0)
        payload = json.dumps(shown, separators=(",", ":"))
        if payload != self.last:
            self.last = payload
            await mqtt.async_publish(self.hass, self.cards_topic, payload, retain=True)
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
        allowed = [*self.entities, *self._members()]
        call = cards.command(payload, allowed, self.hass.states.get) if isinstance(payload, dict) else None
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
