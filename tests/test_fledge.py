"""Fledge: setting a thermostat up, its cards, and what its commands do."""

import json

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.fledge import art, cards
from custom_components.fledge.const import DOMAIN

SERIAL = "09AB01AC36230301"
CARDS = f"nestlocal/{SERIAL}/set/ui"
COMMANDS = f"nestlocal/{SERIAL}/ui/command"


class State:
    def __init__(self, entity_id, state, **attributes):
        self.entity_id, self.state, self.attributes = entity_id, state, attributes


def test_a_light_as_the_thermostat_lists_it():
    lamp = State("light.lamp", "on", friendly_name="Lamp", icon="m3s:table-lamp", brightness=153,
                 supported_color_modes=["color_temp"], rgb_color=(255, 169, 87))
    assert cards.light(lamp) == {"id": "light.lamp", "name": "Lamp", "icon": "m3s:table-lamp", "on": True,
                                 "dimmable": True, "brightness": 60, "rgb": [255, 169, 87]}
    off = State("light.lamp", "off", supported_color_modes=["onoff"])
    assert cards.light(off)["brightness"] is None and not cards.light(off)["dimmable"]
    faint = State("light.lamp", "on", brightness=1, supported_color_modes=["brightness"])
    assert cards.light(faint)["brightness"] == 1, "on is at least 1%"


def test_each_device_is_a_card_in_the_order_chosen():
    states = {"switch.fan": State("switch.fan", "off", friendly_name="Fan"),
              "light.lamp": State("light.lamp", "on", friendly_name="Lamp", brightness=255,
                                  supported_color_modes=["brightness"]),
              "climate.utility": State("climate.utility", "heat", temperature=21, hvac_action="idle"),
              "media_player.speaker": State("media_player.speaker", "playing", volume_level=0.35)}
    out = cards.cards(["climate.utility", "light.gone", "light.lamp", "media_player.speaker", "switch.fan"],
                      states.get)["cards"]
    assert [c["kind"] for c in out] == ["climate", "lights", "media", "lights"], "a device Home Assistant lost: no card"
    assert out[0]["target"] == 21 and out[2]["volume"] == 35
    assert out[1]["name"] == "Lamp" and [l["brightness"] for l in out[1]["lights"]] == [100]
    assert out[3]["lights"][0]["dimmable"] is False


def test_a_light_s_warmth_and_colour_and_a_track_s_progress():
    white = State("light.white", "on", supported_color_modes=["color_temp"], color_mode="color_temp",
                  color_temp_kelvin=2702.6, min_color_temp_kelvin=2202, max_color_temp_kelvin=6535, hs_color=(30, 60))
    assert {k: cards.light(white).get(k) for k in ("kelvin", "kelvins", "hues", "hue")} == \
        {"kelvin": 2703, "kelvins": [2202, 6535], "hues": None, "hue": None}
    colour = State("light.strip", "on", supported_color_modes=["color_temp", "hs"], color_mode="hs", hs_color=(359.6, 80),
                   min_color_temp_kelvin=2000, max_color_temp_kelvin=6500)
    assert (cards.light(colour)["hues"], cards.light(colour)["hue"]) == (True, 0)
    assert "kelvins" not in cards.light(State("light.plain", "on", supported_color_modes=["brightness"]))
    from datetime import datetime, timezone
    at = datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc)
    player = State("media_player.speaker", "playing", media_position=42, media_duration=180,
                   media_position_updated_at=at)
    assert {k: cards.media(player)[k] for k in ("position", "position_at", "duration")} == \
        {"position": 42, "position_at": at.timestamp(), "duration": 180}


def test_a_camera_s_picture_is_left_alone_when_small_else_shrunk():
    from io import BytesIO
    from PIL import Image
    small, large = BytesIO(), BytesIO()
    Image.new("RGB", (640, 360), (10, 20, 30)).save(small, "JPEG")
    Image.new("RGB", (3840, 2160), (10, 20, 30)).save(large, "JPEG")
    assert art.screen(small.getvalue()) == small.getvalue()
    with Image.open(BytesIO(art.screen(large.getvalue()))) as out:
        assert (out.format, out.size) == ("JPEG", (480, 480))
    assert art.screen(b"nope") is None


def test_scenes_scripts_buttons_and_cameras_are_cards():
    states = {e: State(e, "on", friendly_name=n) for e, n in [("scene.goodnight", "Goodnight"),
              ("script.movie", "Movie"), ("button.doorbell_chime", "Chime"), ("camera.front_door", "Front door")]}
    out = cards.cards(list(states), states.get)["cards"]
    assert [(c["kind"], c["name"]) for c in out] == [("action", "Goodnight"), ("action", "Movie"),
                                                     ("action", "Chime"), ("camera", "Front door")]
    chosen = list(states)
    command = lambda payload: cards.command(payload, chosen)
    assert command({"id": "scene.goodnight", "action": "run"}) == cards.Call("scene", "turn_on", "scene.goodnight", {})
    assert command({"id": "button.doorbell_chime", "action": "run"}).service == "press"
    assert command({"id": "camera.front_door", "action": "snapshot"}) == \
        cards.Call("fledge", "snapshot", "camera.front_door", {})
    assert command({"id": "door", "action": "alert_pressed"}) == cards.Call("fledge", "notification", "door",
                                                                          {"action": "pressed"})
    assert command({"id": "scene.other", "action": "run"}) is None, "only chosen ones"
    assert command({"id": "camera.front_door", "action": "run"}) is None


def test_automatic_wakes_to_what_s_going_on():
    states = {"light.lamp": State("light.lamp", "on"), "media_player.speaker": State("media_player.speaker", "idle"),
              "climate.utility": State("climate.utility", "heat")}
    wake = lambda night: cards.cards(["climate.utility", "light.lamp", "media_player.speaker"], states.get, "auto",
                                     None, night)["wake"]
    assert wake(False) == "chooser", "daytime, nothing playing"
    assert wake(True) == 1, "after sunset, the light that's on"
    states["media_player.speaker"] = State("media_player.speaker", "playing")
    assert wake(True) == 2, "the music first"


def test_options_covers_presets_and_groups():
    states = {
        "select.boost": State("select.boost", "Off", friendly_name="Boost", options=["Off", "1 hour"]),
        "cover.blind": State("cover.blind", "open", friendly_name="Blind", current_position=60, supported_features=15),
        "climate.kitchen": State("climate.kitchen", "heat", preset_modes=["none", "eco", "away"], preset_mode="none"),
        "light.bedroom": State("light.bedroom", "on", friendly_name="Bedroom"),
        "light.lamp": State("light.lamp", "off", friendly_name="Lamp"),
    }
    states["light.bedroom"].attributes["entity_id"] = ["light.lamp", "light.gone"]
    out = cards.cards(list(states)[:4], states.get)["cards"]
    assert out[0] == {"kind": "options", "id": "select.boost", "name": "Boost", "icon": "", "options": ["Off", "1 hour"],
                      "current": "Off"}
    assert (out[1]["kind"], out[1]["position"], out[1]["positionable"]) == ("cover", 60, True)
    assert out[2]["presets"] == ["eco", "away"], "none isn't a preset to choose"
    assert [m["id"] for m in out[3]["members"]] == ["light.lamp"], "a group's own lights it knows"
    allowed = [*list(states)[:4], *cards.members(states["light.bedroom"])]
    command = lambda payload: cards.command(payload, allowed)
    assert command({"id": "select.boost", "action": "select", "option": "1 hour"}).data == {"option": "1 hour"}
    assert command({"id": "cover.blind", "action": "position", "value": 30}).data == {"position": 30}
    assert command({"id": "cover.blind", "action": "close"}).service == "close_cover"
    assert command({"id": "climate.kitchen", "action": "preset", "option": "eco"}).data == {"preset_mode": "eco"}
    assert command({"id": "light.lamp", "action": "on"}).entity_id == "light.lamp", "a group's own light"
    assert command({"id": "select.boost", "action": "select", "option": 5}) is None
    assert command({"id": "cover.blind", "action": "position", "value": 300}) is None


def test_the_glance_has_the_time_zone_and_the_weather():
    weather = State("weather.home", "partlycloudy", temperature=8.4, temperature_unit="°C")
    assert cards.glance(weather, 3600.4) == {"offset": 3600, "weather": {"condition": "partlycloudy",
                                                                        "temperature": 8.4, "unit": "°C"}}
    assert cards.glance(None, 0) == {"offset": 0}
    assert cards.glance(State("weather.home", "unavailable"), 0) == {"offset": 0}


def test_the_thermostat_wakes_to_what_was_chosen():
    states = {"light.lamp": State("light.lamp", "on"), "climate.utility": State("climate.utility", "heat")}
    wake = lambda w, device=None: cards.cards(["light.lamp", "climate.utility"], states.get, w, device)["wake"]
    assert wake("chooser") == "chooser" and wake("thermostat") == "thermostat"
    assert wake("chooser", "climate.utility") == 1 and wake("thermostat", "light.lamp") == 0, "by its place"
    assert wake("thermostat", "light.hall") == "thermostat", "a device that isn't a card: as chosen"


def test_commands_reach_only_what_was_chosen_only_as_allowed():
    chosen = ["light.lamp", "switch.fan", "climate.utility", "media_player.speaker"]
    command = lambda payload: cards.command(payload, chosen)
    assert command({"id": "light.lamp", "action": "brightness", "value": 40}) == \
        cards.Call("light", "turn_on", "light.lamp", {"brightness_pct": 40})
    assert command({"id": "switch.fan", "action": "on"}) == cards.Call("homeassistant", "turn_on", "switch.fan", {})
    assert command({"id": "climate.utility", "action": "temperature", "value": 20.5}).data == {"temperature": 20.5}
    assert command({"id": "light.lamp", "action": "color_temp", "value": 2700}).data == {"color_temp_kelvin": 2700}
    assert command({"id": "light.lamp", "action": "hue", "value": 200}).data == {"hs_color": [200, 100]}
    assert command({"id": "media_player.speaker", "action": "volume", "value": 35}).data == {"volume_level": 0.35}
    assert command({"id": "media_player.speaker", "action": "next"}).service == "media_next_track"
    refused = [
        {"id": "light.other", "action": "on"},
        {"id": "light.lamp", "action": "temperature", "value": 20},
        {"id": "switch.fan", "action": "brightness", "value": 50},
        {"id": "switch.fan", "action": "hue", "value": 50},
        {"id": "light.lamp", "action": "color_temp", "value": 99999},
        {"id": "climate.utility", "action": "on"},
        {"id": "climate.utility", "action": "temperature", "value": 90},
        {"id": "media_player.speaker", "action": "off"},
        {"id": "light.lamp", "action": "brightness", "value": "lots"},
        {"id": ["light.lamp"], "action": "on"},
        {"action": "on"},
    ]
    assert [command(p) for p in refused] == [None] * len(refused)


def test_album_art_is_cropped_square_and_small():
    from io import BytesIO
    from PIL import Image
    wide = BytesIO()
    Image.new("RGBA", (400, 300), (200, 30, 40, 255)).save(wide, "PNG")
    png = art.thumbnail(wide.getvalue())
    with Image.open(BytesIO(png)) as out:
        assert (out.size, out.mode, out.getpixel((80, 80))) == ((art.SIZE, art.SIZE), "RGB", (200, 30, 40))
    assert art.thumbnail(b"not a picture") is None


def _thermostat(hass: HomeAssistant) -> dr.DeviceEntry:
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", f"nestlocal_{SERIAL.lower()}")},
        name="Nest Thermostat", manufacturer="Google Nest", serial_number=SERIAL)


async def test_choosing_the_thermostat_and_its_cards(hass: HomeAssistant, mqtt_mock) -> None:
    thermostat = _thermostat(hass)
    # Other MQTT devices, one with an identifier of three parts.
    dr.async_get(hass).async_get_or_create(config_entry_id=next(iter(thermostat.config_entries)),
                                           identifiers={("mqtt", "zigbee2mqtt_0x1", "extra")}, name="Other")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"serial": SERIAL})
    assert result["step_id"] == "cards"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"entities": ["light.lamp", "climate.utility"], "wake": "thermostat"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Nest Thermostat ({SERIAL})"
    assert result["data"] == {"serial": SERIAL}
    assert result["options"] == {"entities": ["light.lamp", "climate.utility"], "wake": "thermostat"}

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init", "one page"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entities": ["climate.utility", "light.lamp"], "wake": "chooser",
                            "wake_device": "light.lamp"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"entities": ["climate.utility", "light.lamp"], "wake": "chooser",
                             "wake_device": "light.lamp"}


async def test_set_up_by_hand_while_discovered(hass: HomeAssistant, mqtt_mock) -> None:
    _thermostat(hass)
    info = MqttServiceInfo(topic=f"nestlocal/{SERIAL}/availability", payload="online", qos=0, retain=True,
                           subscribed_topic="nestlocal/+/availability", timestamp=0)
    discovered = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_MQTT},
                                                           data=info)
    assert discovered["step_id"] == "confirm"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"serial": SERIAL})
    assert result["step_id"] == "cards", "not refused as already in progress"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"entities": [], "wake": "chooser"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN), "the discovery goes with it"


async def test_without_a_thermostat_there_is_nothing_to_set_up(hass: HomeAssistant, mqtt_mock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "no_thermostats"


async def test_a_thermostat_coming_online_is_offered(hass: HomeAssistant, mqtt_mock) -> None:
    info = MqttServiceInfo(topic=f"nestlocal/{SERIAL}/availability", payload="online", qos=0, retain=True,
                           subscribed_topic="nestlocal/+/availability", timestamp=0)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_MQTT},
                                                       data=info)
    assert result["step_id"] == "confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"] == {"serial": SERIAL}
    assert result["options"]["wake"] == "chooser", "the chooser unless chosen"
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_MQTT},
                                                      data=info)
    assert again["type"] is FlowResultType.ABORT and again["reason"] == "already_configured"
    other = MqttServiceInfo(topic="something/else", payload="online", qos=0, retain=True,
                            subscribed_topic="nestlocal/+/availability", timestamp=0)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_MQTT},
                                                       data=other)
    assert result["reason"] == "not_fledge"


def _published(mqtt_mock) -> list:
    return [c.args for c in mqtt_mock.async_publish.call_args_list if c.args[0] == CARDS]


async def test_the_chosen_devices_are_kept_on_the_thermostat(hass: HomeAssistant, mqtt_mock) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp", "brightness": 255,
                                               "supported_color_modes": ["brightness"]})
    hass.states.async_set("switch.fan", "off", {"friendly_name": "Fan"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["switch.fan", "light.lamp"], "wake": "chooser",
                                     "wake_device": "light.lamp"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    topic, payload, qos, retain = _published(mqtt_mock)[-1]
    published = json.loads(payload)
    assert retain and published["wake"] == 1
    assert [(c["name"], c["lights"][0]["on"]) for c in published["cards"]] == [("Fan", False), ("Lamp", True)]

    hass.states.async_set("switch.fan", "on", {"friendly_name": "Fan"})
    await hass.async_block_till_done()
    card = json.loads(_published(mqtt_mock)[-1][1])["cards"][0]
    assert card["lights"][0]["on"], "switched elsewhere: shows on the thermostat"

    turn_on = async_mock_service(hass, "light", "turn_on")
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "light.lamp", "action": "brightness", "value": 30}))
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "light.kitchen", "action": "brightness", "value": 30}))
    async_fire_mqtt_message(hass, COMMANDS, "not json")
    await hass.async_block_till_done()
    assert [c.data for c in turn_on] == [{"entity_id": "light.lamp", "brightness_pct": 30}]

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _published(mqtt_mock)[-1] == (CARDS, "", 0, True), "removed: the thermostat back as it was"


async def test_a_media_player_s_art_follows_its_track(hass: HomeAssistant, mqtt_mock) -> None:
    from homeassistant.components.media_player import DATA_COMPONENT
    from io import BytesIO
    from PIL import Image
    picture = BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 30)).save(picture, "PNG")

    class Player:
        async def async_get_media_image(self):
            return picture.getvalue(), "image/png"

    class Players:
        def get_entity(self, entity_id):
            return Player() if entity_id == "media_player.speaker" else None

    hass.data[DATA_COMPONENT] = Players()
    hass.states.async_set("media_player.speaker", "playing", {"entity_picture": "/art?1"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["media_player.speaker"], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    topic = f"nestlocal/{SERIAL}/set/art/media_player.speaker"
    sent = lambda: [c.args[1] for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic]
    assert len(sent()) == 1 and sent()[0].startswith(b"\x89PNG")
    async def later():  # past the publisher's cooldown
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
        await hass.async_block_till_done()

    hass.states.async_set("media_player.speaker", "playing", {"entity_picture": "/art?1", "volume_level": 0.5})
    await later()
    assert len(sent()) == 1, "the same track: not sent again"
    hass.states.async_set("media_player.speaker", "off", {})
    await later()
    assert sent()[-1] == b"", "off: none"
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert sent()[-1] == "", "removed: cleared"


async def test_on_the_thermostat_s_device_and_its_build_checked(hass: HomeAssistant, mqtt_mock) -> None:
    thermostat = _thermostat(hass)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    cards = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SERIAL)})
    assert dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id) == [cards]
    assert cards.name == "Nest Thermostat cards"
    assert cards.via_device_id == thermostat.id, "it leads to the thermostat and its entities"
    assert dr.async_get(hass).async_get(thermostat.id).config_entries == thermostat.config_entries, \
        "the thermostat's device stays MQTT's alone"

    issue = lambda: ir.async_get(hass).async_get_issue(DOMAIN, f"outdated_{SERIAL}")
    assert issue() is None, "not yet: the thermostat may still say"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert issue() is not None, "a build that never says is too old"
    async_fire_mqtt_message(hass, f"nestlocal/{SERIAL}/ui/protocol", "4")
    await hass.async_block_till_done()
    assert issue() is None, "updated: the repair goes"
    async_fire_mqtt_message(hass, f"nestlocal/{SERIAL}/ui/protocol", "3")
    await hass.async_block_till_done()
    assert issue() is not None


async def test_an_updated_integration_has_the_thermostat_check_for_its_own(hass: HomeAssistant, mqtt_mock,
                                                                           hass_storage) -> None:
    check = f"nestlocal/{SERIAL}/set/check_update"
    checks = lambda: [c for c in mqtt_mock.async_publish.call_args_list if c.args[0] == check]
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(checks()) == 1, "new: it checks once"
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(checks()) == 1, "restarted, the same version: no check"
    hass_storage[f"{DOMAIN}.versions"]["data"][SERIAL] = "0.0.1"
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(checks()) == 2, "updated: the thermostat checks for its own"


async def test_wake_to_and_the_screen_from_home_assistant(hass: HomeAssistant, mqtt_mock) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    hass.states.async_set("media_player.speaker", "playing", {"friendly_name": "Speaker"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["light.lamp", "media_player.speaker"], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    select = er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{SERIAL}_wake_to")
    state = hass.states.get(select)
    assert state.state == "Device chooser" and state.attributes["options"] == \
        ["Device chooser", "Automatic", "Thermostat", "Lamp", "Speaker"]
    thermostat = entry.runtime_data
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Speaker"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.runtime_data is thermostat, "only what it wakes to changed: no reload"
    assert json.loads(_published(mqtt_mock)[-1][1])["wake"] == 1
    assert hass.states.get(select).state == "Speaker"
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Thermostat"},
                                   blocking=True)
    await hass.async_block_till_done()
    assert json.loads(_published(mqtt_mock)[-1][1])["wake"] == "thermostat"
    assert entry.options == {"entities": ["light.lamp", "media_player.speaker"], "wake": "thermostat"}

    switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{SERIAL}_keep_screen_on")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    mqtt_mock.async_publish.assert_any_call(f"nestlocal/{SERIAL}/set/screen_on", "ON", 0, True)
    assert hass.states.get(switch).state == "on"
    button = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{SERIAL}_wake_screen")
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    mqtt_mock.async_publish.assert_any_call(f"nestlocal/{SERIAL}/set/screen_wake", "PRESS", 0, False)


async def test_notifications_and_their_answers(hass: HomeAssistant, mqtt_mock) -> None:
    from unittest.mock import patch
    from io import BytesIO
    from PIL import Image
    from homeassistant.components.camera import Image as CameraImage
    picture = BytesIO()
    Image.new("RGB", (640, 360), (30, 60, 90)).save(picture, "JPEG")
    hass.states.async_set("camera.front_door", "idle", {"friendly_name": "Front door", "entity_picture": "/c?1"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sent = lambda topic: [c.args[1] for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic]
    alerts = f"nestlocal/{SERIAL}/set/alert"

    response = await hass.services.async_call(DOMAIN, "notify", {"message": "Done", "title": "Washing"},
                                              blocking=True, return_response=True)
    shown = json.loads(sent(alerts)[-1])
    assert shown == {"id": response["tag"], "title": "Washing", "message": "Done", "icon": "", "timeout": 30,
                     "picture": None}

    with patch("homeassistant.components.camera.async_get_image",
               return_value=CameraImage("image/jpeg", picture.getvalue())):
        await hass.services.async_call(DOMAIN, "notify", {"message": "Ring", "camera": "camera.front_door",
                                                          "tag": "door"}, blocking=True)
    shown = json.loads(sent(alerts)[-1])
    assert shown["id"] == "door" and shown["picture"] == "alert:door"
    still = sent(f"nestlocal/{SERIAL}/set/art/alert:door")[-1]
    assert still == picture.getvalue(), "a small JPEG goes as the camera made it, not even decoded"

    events = []
    hass.bus.async_listen("fledge_notification", lambda event: events.append(event.data))
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "door", "action": "alert_pressed"}))
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "door", "action": "alert_pressed"}))
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "never_shown", "action": "alert_pressed"}))
    await hass.async_block_till_done()
    assert events == [{"tag": "door", "action": "pressed", "serial": SERIAL}], \
        "one answer to a notification shown: not twice, nor to one never shown"

    await hass.services.async_call(DOMAIN, "dismiss", {"tag": "door"}, blocking=True)
    assert json.loads(sent(alerts)[-1]) == {"id": "door", "dismiss": True}


async def test_rooms_become_a_card_a_light(hass: HomeAssistant, mqtt_mock) -> None:
    bedroom = ar.async_get(hass).async_create("Bedroom", icon="mdi:bed")
    entities = er.async_get(hass)
    for name in ("lamp", "ceiling", "screen"):
        entities.async_get_or_create("light", "test", name, suggested_object_id=name)
        entities.async_update_entity(f"light.{name}", area_id=bedroom.id)
    # A light group hidden from dashboards is still a room's light.
    entities.async_update_entity("light.ceiling", hidden_by=er.RegistryEntryHider.USER)
    for name in ("lamp", "ceiling"):
        hass.states.async_set(f"light.{name}", "off", {"friendly_name": name.title()})
    hass.states.async_set("climate.utility", "heat", {"temperature": 21})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"areas": [bedroom.id], "exclude": ["light.screen"],
                                     "climates": ["climate.utility"], "media": [], "wake": f"area:{bedroom.id}"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.options == {"entities": ["light.ceiling", "light.lamp", "climate.utility"], "wake": "chooser",
                             "wake_device": "light.ceiling"}
    published = json.loads(_published(mqtt_mock)[-1][1])
    assert [c["name"] for c in published["cards"]] == ["Ceiling", "Lamp", "climate.utility"] and published["wake"] == 0


def test_a_card_can_have_its_own_name_on_the_nest():
    lamp = State("light.lamp", "on", friendly_name="Lamp", supported_color_modes=["brightness"], brightness=128)
    fan = State("switch.fan", "off", friendly_name="Fan")
    shown = cards.cards(["light.lamp", "switch.fan"], {s.entity_id: s for s in (lamp, fan)}.get,
                        names={"light.lamp": "Reading"})
    assert [c["name"] for c in shown["cards"]] == ["Reading", "Fan"]
    assert shown["cards"][0]["lights"][0]["name"] == "Reading", "its one light too"


async def test_the_panel_lays_out_the_cards_and_saves_them(hass: HomeAssistant, mqtt_mock, hass_ws_client) -> None:
    from homeassistant.components.frontend import DATA_PANELS

    thermostat = _thermostat(hass)
    er.async_get(hass).async_get_or_create("climate", "mqtt", "nestlocal_thermostat", device_id=thermostat.id,
                                           suggested_object_id="nest_thermostat")
    hass.states.async_set("climate.nest_thermostat", "heat", {"temperature": 21, "current_temperature": 20.4,
                                                              "hvac_action": "heating"})
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp", "supported_color_modes": ["brightness"],
                                               "brightness": 255})
    hass.states.async_set("switch.fan", "off", {"friendly_name": "Fan"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["light.lamp"], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DATA_PANELS]["fledge"].require_admin, "in the sidebar, for admins"

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "fledge/thermostats"})
    found = (await client.receive_json())["result"]["thermostats"]
    assert len(found) == 1
    shown = found[0]
    assert (shown["entry_id"], shown["serial"], shown["climate"], shown["loaded"]) == \
        (entry.entry_id, SERIAL, "climate.nest_thermostat", True)
    assert set(shown["entities"]) == {"keep_screen_on", "wake_to", "wake_screen"}

    await client.send_json_auto_id({"type": "fledge/preview", "entities": ["light.lamp", "light.gone"],
                                    "climate": "climate.nest_thermostat"})
    preview = (await client.receive_json())["result"]
    assert list(preview["cards"]) == ["light.lamp"], "unknown to Home Assistant: no card"
    assert preview["cards"]["light.lamp"]["lights"][0]["brightness"] == 100
    assert preview["thermostat"]["target"] == 21

    await client.send_json_auto_id({"type": "fledge/save", "entry_id": entry.entry_id,
                                    "entities": ["switch.fan", "light.lamp"], "wake": "auto",
                                    "wake_device": "light.lamp", "glance": False,
                                    "names": {"light.lamp": " Reading ", "switch.fan": "", "light.other": "X"}})
    response = await client.receive_json()
    assert response["success"], response
    await hass.async_block_till_done()
    assert entry.options == {"entities": ["switch.fan", "light.lamp"], "wake": "auto", "wake_device": "light.lamp",
                             "names": {"light.lamp": "Reading"}}
    published = json.loads(_published(mqtt_mock)[-1][1])
    assert [c["name"] for c in published["cards"]] == ["Fan", "Reading"] and published["wake"] == 1
    select = er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{SERIAL}_wake_to")
    assert hass.states.get(select).state == "Reading", "Wake to calls it so too"

    await client.send_json_auto_id({"type": "fledge/save", "entry_id": entry.entry_id,
                                    "entities": ["climate.nest_thermostat"], "wake": "chooser"})
    response = await client.receive_json()
    assert not response["success"], "the Nest's own thermostat isn't a card"
    await client.send_json_auto_id({"type": "fledge/save", "entry_id": "nope", "entities": [], "wake": "chooser"})
    assert (await client.receive_json())["error"]["code"] == "not_found"

    # The options flow keeps the names of the cards still shown.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {"entities": ["light.lamp"],
                                                                          "wake": "chooser"})
    await hass.async_block_till_done()
    assert entry.options["names"] == {"light.lamp": "Reading"}

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert "fledge" not in hass.data[DATA_PANELS], "no thermostat: out of the sidebar"
