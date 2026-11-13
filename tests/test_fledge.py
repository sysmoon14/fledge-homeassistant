"""Fledge: setting a thermostat up, its cards, and what its commands do."""

import json

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
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

from custom_components.fledge import art, cards, const
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


def test_an_air_conditioner_s_modes_fan_speeds_swings_and_range():
    ac = State("climate.ac", "cool", hvac_modes=["off", "cool", "heat", "dry", "fan_only", "heat_cool"],
               fan_modes=["auto", "low", "high"], fan_mode="auto", temperature=22,
               preset_modes=["none", "away", "boost"], preset_mode="none",
               swing_modes=["off", "swing"], swing_mode="off",
               swing_horizontal_modes=["off", "swing", "left"], swing_horizontal_mode="swing")
    card = cards.climate(ac)
    assert (card["modes"], card["fan_modes"], card["fan_mode"], card["target"]) == \
        (["off", "cool", "heat", "dry", "fan_only", "heat_cool"], ["auto", "low", "high"], "auto", 22)
    assert (card["presets"], card["preset_modes"]) == (["away", "boost"], ["none", "away", "boost"]), \
        "its whole list too, none to leave a preset"
    assert (card["swing_modes"], card["swing_mode"], card["swing_horizontal_modes"], card["swing_horizontal_mode"]) == \
        (["off", "swing"], "off", ["off", "swing", "left"], "swing")
    states = {"climate.ac": ac}
    command = lambda payload: cards.command(payload, ["climate.ac"], states.get)
    assert command({"id": "climate.ac", "action": "hvac_mode", "option": "dry"}) == \
        cards.Call("climate", "set_hvac_mode", "climate.ac", {"hvac_mode": "dry"})
    assert command({"id": "climate.ac", "action": "fan_mode", "option": "high"}).data == {"fan_mode": "high"}
    assert command({"id": "climate.ac", "action": "fan_mode", "option": "turbo"}) is None, "only its own speeds"
    assert command({"id": "climate.ac", "action": "hvac_mode", "option": "auto"}) is None, "only its own modes"
    assert command({"id": "climate.ac", "action": "swing_mode", "option": "swing"}) == \
        cards.Call("climate", "set_swing_mode", "climate.ac", {"swing_mode": "swing"})
    assert command({"id": "climate.ac", "action": "swing_horizontal_mode", "option": "left"}) == \
        cards.Call("climate", "set_swing_horizontal_mode", "climate.ac", {"swing_horizontal_mode": "left"})
    assert command({"id": "climate.ac", "action": "swing_mode", "option": "top"}) is None, "only its own swings"
    assert command({"id": "climate.ac", "action": "preset", "option": "none"}).data == {"preset_mode": "none"}
    assert cards.command({"id": "climate.ac", "action": "fan_mode", "option": "low"}, ["climate.ac"]) is None, \
        "not without its state"
    assert command({"id": "climate.ac", "action": "temperature", "value": 21}).data == {"temperature": 21}

    # heat_cool: the end it's working towards, the other kept clear of it.
    ranged = lambda action, now=None: State("climate.ac", "heat_cool", target_temp_low=19, target_temp_high=24,
                                            target_temp_step=1, hvac_action=action, current_temperature=now)
    assert cards.climate(ranged("cooling"))["target"] == 24 and cards.climate(ranged("heating"))["target"] == 19
    assert cards.climate(ranged("idle", 23))["target"] == 24, "idle: the nearer end"
    states["climate.ac"] = ranged("cooling")
    assert command({"id": "climate.ac", "action": "temperature", "value": 18}).data == \
        {"target_temp_low": 17, "target_temp_high": 18}
    states["climate.ac"] = ranged("heating")
    assert command({"id": "climate.ac", "action": "temperature", "value": 20}).data == \
        {"target_temp_low": 20, "target_temp_high": 24}


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
    assert command({"id": "light.lamp", "action": "on"}) == cards.Call("light", "turn_on", "light.lamp", {})
    assert command({"id": "light.lamp", "action": "off"}) == cards.Call("light", "turn_off", "light.lamp", {})
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
    assert result["options"] == {"entities": ["light.lamp", "climate.utility"], "wake": "thermostat",
                                 "far": "wake", "near": "wake", "card_idle": 20}

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init", "one page"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"entities": ["climate.utility", "light.lamp"], "wake": "chooser",
                            "wake_device": "light.lamp", "far": "glance", "near": "none", "card_idle": 45,
                            "dim_off": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"entities": ["climate.utility", "light.lamp"], "wake": "chooser",
                             "wake_device": "light.lamp", "far": "glance", "near": "none", "glance": True,
                             "card_idle": 45, "dim_off": False}


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
    async_fire_mqtt_message(hass, f"nestlocal/{SERIAL}/ui/protocol", "6")
    await hass.async_block_till_done()
    assert issue() is None, "updated: the repair goes"
    async_fire_mqtt_message(hass, f"nestlocal/{SERIAL}/ui/protocol", "5")
    await hass.async_block_till_done()
    assert issue() is not None


async def test_renaming_the_thermostat_names_its_own_card_straight_away(hass: HomeAssistant, mqtt_mock) -> None:
    thermostat = _thermostat(hass)
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["light.lamp"], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "thermostat_name" not in json.loads(_published(mqtt_mock)[-1][1]), "not renamed: the Nest says Thermostat"

    dr.async_get(hass).async_update_device(thermostat.id, name_by_user="Hallway")
    await hass.async_block_till_done()
    assert json.loads(_published(mqtt_mock)[-1][1])["thermostat_name"] == "Hallway"
    cards = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, SERIAL)})
    assert cards.name == "Hallway cards"

    dr.async_get(hass).async_update_device(thermostat.id, name_by_user=None)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert "thermostat_name" not in json.loads(_published(mqtt_mock)[-1][1])


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
    group = cards.named({"kind": "lights", "name": "Bedroom", "lights": [{"id": "light.bedroom", "name": "Bedroom"}],
                         "members": [{"id": "light.a", "name": "A"}]}, "Upstairs")
    assert (group["lights"][0]["name"], group["members"][0]["name"]) == ("Upstairs", "A"), "a group's own light; not its members"


def test_the_thermostat_goes_where_it_is_put_among_the_cards() -> None:
    states = {e: State(e, "on", friendly_name=e) for e in ("light.a", "light.b")}
    shown = cards.cards(["light.a", "light.gone", "light.b"], states.get, thermostat=2)
    assert shown["thermostat"] == 1, "among the cards shown: one Home Assistant doesn't know is left out"
    assert "thermostat" not in cards.cards(["light.a"], states.get), "first: as before, nothing sent"


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
    assert set(shown["entities"]) == {"keep_screen_on", "call_for_heat", "wake_to", "wake_screen", "across_the_room", "close_up",
                                      "glance_layout", "night_glance"}

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
                             "far": "wake", "near": "wake", "names": {"light.lamp": "Reading"}}, \
        "an older panel's save: who's about as it was"
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

    # The thermostat's own card anywhere among them: after the fan.
    await client.send_json_auto_id({"type": "fledge/save", "entry_id": entry.entry_id,
                                    "entities": ["switch.fan", "light.lamp"], "wake": "chooser", "thermostat": 1,
                                    "names": {"light.lamp": "Reading"}})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert entry.options["thermostat"] == 1
    published = json.loads(_published(mqtt_mock)[-1][1])
    assert published["thermostat"] == 1 and len(published["cards"]) == 2

    # The options flow keeps the names of the cards still shown, and the
    # thermostat's place within them.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {"entities": ["light.lamp"],
                                                                          "wake": "chooser"})
    await hass.async_block_till_done()
    assert entry.options["names"] == {"light.lamp": "Reading"}
    assert entry.options["thermostat"] == 1

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert "fledge" not in hass.data[DATA_PANELS], "no thermostat: out of the sidebar"


# ---- The Nest's own schedules: fledge.set_schedule and the cards' lookup.

HEATING_WEEK = {"mon": [{"time": "06:30", "temp": 22}, {"time": "22:30", "temp": 16}], "tue": [], "wed": [], "thu": [],
                "fri": [], "sat": [{"time": "07:30", "temp": 21}], "sun": []}


def _nest_entities(hass: HomeAssistant, thermostat: dr.DeviceEntry, hot_water: bool = True) -> None:
    registry = er.async_get(hass)
    own = [("climate", "climate", "nest_thermostat"), ("sensor", "heating_times", "nest_thermostat_heating_times")]
    if hot_water:
        own += [("sensor", "hot_water_times", "nest_thermostat_hot_water_times"),
                ("switch", "hot_water_schedule", "nest_thermostat_hot_water_schedule")]
    for domain, end, object_id in own:
        registry.async_get_or_create(domain, "mqtt", f"nestlocal_{SERIAL.lower()}_{end}", device_id=thermostat.id,
                                     suggested_object_id=object_id)
    hass.states.async_set("sensor.nest_thermostat_heating_times", "3", HEATING_WEEK)


async def _with_schedules(hass: HomeAssistant, hot_water: bool = True) -> None:
    _nest_entities(hass, _thermostat(hass), hot_water)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


def _sent(mqtt_mock, topic: str) -> list:
    return [json.loads(c.args[1]) for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic]


def test_the_setpoint_now_carries_on_from_days_before():
    from custom_components.fledge.schedule import current
    assert current(HEATING_WEEK, 0, "12:00") == 22
    assert current(HEATING_WEEK, 0, "06:00") == 21, "Monday before its first: Saturday's last"
    assert current(HEATING_WEEK, 3, "09:00") == 16, "a day with none: Monday's last"
    assert current({}, 0, "09:00") is None


async def test_copying_a_day_is_one_write(hass: HomeAssistant, mqtt_mock) -> None:
    await _with_schedules(hass)
    hass.states.async_set("climate.nest_thermostat", "heat", {"temperature": 16, "preset_mode": "none"})
    set_temperature = async_mock_service(hass, "climate", "set_temperature")
    monday = [{"time": "06:30", "temp": 22}, {"time": "22:30", "temp": 16}]
    await hass.services.async_call(DOMAIN, "set_schedule", {
        "entity_id": "climate.nest_thermostat",
        "heating": {d: monday for d in ("tue", "wed", "thu", "fri")}}, blocking=True)
    sent = _sent(mqtt_mock, f"nestlocal/{SERIAL}/set/heating_times")
    assert sent == [{d: [{"time": "06:30", "temp": 22.0}, {"time": "22:30", "temp": 16.0}] for d in ("tue", "wed", "thu", "fri")}]
    # What applies now, from the week as written, set on the Nest.
    from custom_components.fledge.schedule import current
    week = {**HEATING_WEEK, **{d: monday for d in ("tue", "wed", "thu", "fri")}}
    now = dt_util.now()
    expected = current(week, now.weekday(), now.strftime("%H:%M"))
    assert [c.data["temperature"] for c in set_temperature] == ([] if expected == 16 else [expected])

    await hass.services.async_call(DOMAIN, "set_schedule", {
        "entity_id": "sensor.nest_thermostat_hot_water_times",
        "hot_water": {"sat": [{"on": "22:00", "off": "00:30"}, {"on": "06:00:00", "off": "07:00"}], "sun": []}},
        blocking=True)
    assert _sent(mqtt_mock, f"nestlocal/{SERIAL}/set/hot_water_times") == [
        {"sat": [{"on": "06:00", "off": "07:00"}, {"on": "22:00", "off": "00:30"}], "sun": []}], "any of the Nest's entities"


async def test_eco_keeps_its_temperature(hass: HomeAssistant, mqtt_mock) -> None:
    await _with_schedules(hass)
    hass.states.async_set("climate.nest_thermostat", "heat", {"temperature": 9.5, "preset_mode": "eco"})
    set_temperature = async_mock_service(hass, "climate", "set_temperature")
    await hass.services.async_call(DOMAIN, "set_schedule", {
        "entity_id": "climate.nest_thermostat", "heating": {"mon": [{"time": "00:00", "temp": 25}]}}, blocking=True)
    assert not set_temperature


async def test_a_schedule_the_nest_would_refuse_is_refused_here(hass: HomeAssistant, mqtt_mock) -> None:
    import pytest
    import voluptuous as vol
    from homeassistant.exceptions import ServiceValidationError
    await _with_schedules(hass, hot_water=False)
    hass.states.async_set("light.lamp", "on")
    bad = [
        {"heating": {"mon": [{"time": "6:30", "temp": 20}]}},
        {"heating": {"mon": [{"time": "06:30", "temp": 40}]}},
        {"heating": {"mon": [{"time": "06:30", "temp": 20}, {"time": "06:30", "temp": 21}]}},
        {"heating": {"someday": []}},
        {"hot_water": {"mon": [{"on": "06:00", "off": "08:00"}, {"on": "07:00", "off": "09:00"}]}},
        {"hot_water": {"mon": [{"on": "06:00", "off": "07:00"}, {"on": "23:00", "off": "06:30"}]}},
        {},
    ]
    for data in bad:
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(DOMAIN, "set_schedule", {"entity_id": "climate.nest_thermostat", **data},
                                           blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_schedule", {"entity_id": "climate.nest_thermostat",
                                                                "hot_water": {"mon": []}}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_schedule", {"entity_id": "light.lamp", "heating": {"mon": []}},
                                       blocking=True)
    assert not _sent(mqtt_mock, f"nestlocal/{SERIAL}/set/heating_times")


async def test_the_cards_find_the_nest_s_schedules(hass: HomeAssistant, mqtt_mock, hass_ws_client) -> None:
    await _with_schedules(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "fledge/schedule", "entity_id": "climate.nest_thermostat"})
    assert (await client.receive_json())["result"] == {
        "serial": SERIAL, "climate": "climate.nest_thermostat", "heating": "sensor.nest_thermostat_heating_times",
        "hot_water": "sensor.nest_thermostat_hot_water_times",
        "hot_water_switch": "switch.nest_thermostat_hot_water_schedule", "auto_schedule": None}
    await client.send_json_auto_id({"type": "fledge/schedule", "entity_id": "climate.elsewhere"})
    assert (await client.receive_json())["error"]["code"] == "not_found"


async def test_the_schedule_cards_load_while_a_thermostat_is_set_up(hass: HomeAssistant, mqtt_mock) -> None:
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
    # The frontend, as far as extra modules go (its build isn't installed here).
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda *_: None, [])
    _thermostat(hass)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    loaded = [u for u in hass.data[DATA_EXTRA_MODULE_URL].urls if "fledge-schedule-card.js" in u]
    assert len(loaded) == 1 and loaded[0].startswith("/fledge_static/fledge-schedule-card.js?v=")
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not [u for u in hass.data[DATA_EXTRA_MODULE_URL].urls if "fledge-schedule-card.js" in u]


async def test_across_the_room_and_close_up_reach_the_thermostat(hass: HomeAssistant, mqtt_mock) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["light.lamp"], "wake": "chooser", "glance": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    published = json.loads(_published(mqtt_mock)[-1][1])
    assert published["presence"] == {"far": "glance", "near": "wake"}, "a glance from before: across the room"
    assert published["card_idle"] == 20 and "glance" in published
    assert published["dim_off"] is True, "turning down past 1% turns a light off unless chosen otherwise"
    ids = {key: er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{SERIAL}_{key}")
           for key in ("across_the_room", "close_up")}
    assert hass.states.get(ids["across_the_room"]).state == "Glance"
    assert hass.states.get(ids["close_up"]).state == "What it wakes to"
    thermostat = entry.runtime_data
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": ids["across_the_room"], "option": "Stay dark"}, blocking=True)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": ids["close_up"], "option": "Glance"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.runtime_data is thermostat, "no reload"
    published = json.loads(_published(mqtt_mock)[-1][1])
    assert published["presence"] == {"far": "dark", "near": "glance"}
    assert "glance" in published, "close up still glances"
    assert "glance" not in entry.options
    assert hass.states.get(ids["across_the_room"]).state == "Stay dark"
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": ids["close_up"], "option": "Nothing"}, blocking=True)
    await hass.async_block_till_done()
    assert "glance" not in json.loads(_published(mqtt_mock)[-1][1])


# The weather and a room's temperature, for the Nest itself (feeds.py).

def test_temperatures_reach_the_nest_in_celsius():
    from custom_components.fledge.feeds import celsius, forecast, outdoor
    assert celsius("68", "°F") == 20.0 and celsius(21.5, "°C") == 21.5 and celsius("293.15", "K") == 20.0
    assert celsius("unavailable", "°C") is None and celsius("nan", "°C") is None
    weather = State("weather.home", "rainy", temperature=50, temperature_unit="°F", humidity=81)
    assert outdoor(weather, None) == (10.0, 81.0)
    assert outdoor(weather, State("sensor.garden", "7.25", unit_of_measurement="°C")) == (7.25, 81.0), \
        "the thermometer's temperature, the forecast's humidity"
    assert outdoor(weather, State("sensor.garden", "unavailable")) == (None, 81.0), "not the forecast's instead"
    shown = forecast(weather, State("sun.sun", "above_horizon", next_rising="r", next_setting="s"),
                     [{"datetime": "h", "temperature": 59}] * 60, [{"datetime": "d", "temperature": 68, "templow": 41}])
    assert shown["condition"] == "rainy" and (shown["next_rising"], shown["next_setting"]) == ("r", "s")
    assert len(shown["hourly"]) == 48 and shown["hourly"][0]["temperature"] == 15.0
    assert shown["daily"] == [{"datetime": "d", "temperature": 20.0, "templow": 5.0}]


def _source(hass: HomeAssistant, thermostat: dr.DeviceEntry, domain: str, end: str, object_id: str) -> str:
    return er.async_get(hass).async_get_or_create(domain, "mqtt", f"nestlocal_{SERIAL.lower()}_{end}",
                                                  device_id=thermostat.id, suggested_object_id=object_id).entity_id


async def test_the_weather_reaches_the_nest_s_own_screen(hass: HomeAssistant, mqtt_mock) -> None:
    from homeassistant.core import SupportsResponse
    _thermostat(hass)
    hass.states.async_set("weather.home", "cloudy", {"temperature": 12.5, "temperature_unit": "°C", "humidity": 70,
                                                     "supported_features": 3})
    asked = []

    async def forecasts(call):
        asked.append(call.data["type"])
        return {"weather.home": {"forecast": [{"datetime": call.data["type"], "temperature": 11}]}}
    hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser", "weather": "weather.home"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sent = lambda end: [(c.args[1], c.args[3]) for c in mqtt_mock.async_publish.call_args_list
                        if c.args[0] == f"nestlocal/{SERIAL}/set/{end}"]
    assert sent("outdoor_temperature") == [("12.5", True)] and sent("outdoor_humidity") == [("70.0", True)]
    assert sorted(asked) == ["daily", "hourly"]
    weather = json.loads(sent("weather")[-1][0])
    assert weather["condition"] == "cloudy" and weather["hourly"] == [{"datetime": "hourly", "temperature": 11.0}]

    hass.states.async_set("weather.home", "cloudy", {"temperature": 13, "temperature_unit": "°C", "humidity": 70,
                                                     "supported_features": 3})
    await hass.async_block_till_done()
    assert sent("outdoor_temperature")[-1] == ("13.0", True)
    assert len(sent("outdoor_humidity")) == 1, "unchanged: not sent again"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=16))
    await hass.async_block_till_done()
    assert len(asked) == 4, "the forecast every quarter of an hour"


async def test_a_room_thermometer_reaches_a_nest_that_can_use_it(hass: HomeAssistant, mqtt_mock) -> None:
    thermostat = _thermostat(hass)
    hass.states.async_set("sensor.lounge", "70.7", {"unit_of_measurement": "°F", "device_class": "temperature"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser", "room_sensor": "sensor.lounge"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    topic = f"nestlocal/{SERIAL}/set/external_temperature"
    sent = lambda: [(c.args[1], c.args[3]) for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic]
    issues = ir.async_get(hass)
    assert sent() == [] and issues.async_get_issue(DOMAIN, f"room_sensor_{SERIAL}"), \
        "a Nest without the option: nothing sent, a repair says why"

    _source(hass, thermostat, "sensor", "temperature_source", "nest_thermostat_temperature_source")
    hass.states.async_set("sensor.lounge", "71.6", {"unit_of_measurement": "°F", "device_class": "temperature"})
    await hass.async_block_till_done()
    assert sent() == [("22.0", False)], "°C, and never retained"
    assert issues.async_get_issue(DOMAIN, f"room_sensor_{SERIAL}") is None
    hass.states.async_set("sensor.lounge", "unavailable")
    await hass.async_block_till_done()
    assert len(sent()) == 1
    hass.states.async_set("sensor.lounge", "71.6", {"unit_of_measurement": "°F", "device_class": "temperature"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert sent()[-1] == ("22.0", False) and len(sent()) == 2, "the same reading again, before it times out"

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, f"room_sensor_{SERIAL}") is None


async def test_a_nest_that_isn_t_a_thermostat_has_no_room_thermometer_repair(hass: HomeAssistant, mqtt_mock) -> None:
    thermostat = _thermostat(hass)
    role = _source(hass, thermostat, "switch", "act_as_thermostat", "nest_thermostat_act_as_a_thermostat")
    hass.states.async_set(role, "off")
    hass.states.async_set("sensor.lounge", "21", {"unit_of_measurement": "°C", "device_class": "temperature"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser", "room_sensor": "sensor.lounge"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"room_sensor_{SERIAL}") is None, "it doesn't regulate at all"
    hass.states.async_set(role, "on")
    hass.states.async_set("sensor.lounge", "21.5", {"unit_of_measurement": "°C", "device_class": "temperature"})
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"room_sensor_{SERIAL}"), "a thermostat again: the usual repair"


def test_hiding_the_thermostat_s_card_needs_a_build_that_can():
    from custom_components.fledge.const import needs
    assert needs(["light.a"]) == 6 and needs(["alarm_control_panel.a"]) == 9
    assert needs(["light.a"], False) == 10 and needs(["alarm_control_panel.a"], False) == 10
    from custom_components.fledge.config_flow import options_from
    assert "show_thermostat" not in options_from({"entities": [], "show_thermostat": True})
    assert options_from({"entities": [], "show_thermostat": False})["show_thermostat"] is False


async def test_the_thermostat_s_card_can_be_hidden(hass: HomeAssistant, mqtt_mock) -> None:
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["light.lamp"], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "show_thermostat" not in json.loads(_published(mqtt_mock)[-1][1]), "shown, as before"
    hass.config_entries.async_update_entry(entry, options={**entry.options, "show_thermostat": False})
    await hass.async_block_till_done()
    assert json.loads(_published(mqtt_mock)[-1][1])["show_thermostat"] is False


async def test_heating_and_hot_water_hours_today(hass: HomeAssistant, mqtt_mock, freezer) -> None:
    freezer.move_to("2026-11-09 10:00:00+00:00")
    thermostat = _thermostat(hass)
    heating = _source(hass, thermostat, "binary_sensor", "heating", "nest_thermostat_heating")
    hot_water = _source(hass, thermostat, "binary_sensor", "hot_water_active", "nest_thermostat_hot_water")
    hass.states.async_set(heating, "off")
    hass.states.async_set(hot_water, "on")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ids = {key: er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_{key}")
           for key in ("heating_today", "hot_water_today")}
    state = hass.states.get(ids["heating_today"])
    assert state.state == "0.0" and state.attributes["unit_of_measurement"] == "h"
    assert state.attributes["state_class"] == "total_increasing" and state.attributes["device_class"] == "duration"

    hass.states.async_set(heating, "on")
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=90))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get(ids["heating_today"]).state) == 1.5, "a run going on counts as it goes"
    hass.states.async_set(heating, "off")
    await hass.async_block_till_done()
    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get(ids["heating_today"]).state) == 1.5
    assert float(hass.states.get(ids["hot_water_today"]).state) == 3.5, "on since it started"

    freezer.move_to(dt_util.start_of_local_day() + timedelta(days=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get(ids["heating_today"]).state) == 0
    freezer.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert float(hass.states.get(ids["hot_water_today"]).state) == 0.5, "a new day, from midnight"


async def test_a_notification_for_one_nest(hass: HomeAssistant, mqtt_mock) -> None:
    thermostat = _thermostat(hass)
    other = "02AA01AB12340001"
    for serial in (SERIAL, other):
        entry = MockConfigEntry(domain=DOMAIN, unique_id=serial, data={"serial": serial},
                                options={"entities": [], "wake": "chooser"})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    alerts = lambda serial: [c for c in mqtt_mock.async_publish.call_args_list
                             if c.args[0] == f"nestlocal/{serial}/set/alert"]
    await hass.services.async_call(DOMAIN, "notify", {"message": "Hi", "thermostat": thermostat.id}, blocking=True)
    assert (len(alerts(SERIAL)), len(alerts(other))) == (1, 0), "the Nest's own device"
    cards_device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, other)})
    await hass.services.async_call(DOMAIN, "dismiss", {"tag": "x", "thermostat": [cards_device.id]}, blocking=True)
    assert (len(alerts(SERIAL)), len(alerts(other))) == (1, 1), "or its cards' device"
    await hass.services.async_call(DOMAIN, "notify", {"message": "All"}, blocking=True)
    assert (len(alerts(SERIAL)), len(alerts(other))) == (2, 2), "none chosen: every Nest"


async def test_the_temperatures_are_options(hass: HomeAssistant, mqtt_mock) -> None:
    from custom_components.fledge.config_flow import options_from
    options = options_from({"entities": [], "wake": "chooser", "weather": "weather.home",
                            "outdoor_temperature": "sensor.garden", "room_sensor": "sensor.lounge"})
    assert (options["weather"], options["outdoor_temperature"], options["room_sensor"]) == \
        ("weather.home", "sensor.garden", "sensor.lounge")
    assert "room_sensor" not in options_from({"entities": [], "wake": "chooser", "room_sensor": None})


async def test_the_repair_turns_the_nest_s_option_on(hass: HomeAssistant, mqtt_mock) -> None:
    from custom_components.fledge import repairs
    thermostat = _thermostat(hass)
    switch = _source(hass, thermostat, "switch", "external_temperature_option", "nest_thermostat_external_temperature")
    hass.states.async_set("sensor.lounge", "21", {"unit_of_measurement": "°C", "device_class": "temperature"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser", "room_sensor": "sensor.lounge"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"room_sensor_{SERIAL}")
    assert issue.is_fixable and issue.translation_key == "room_sensor_switch" and issue.data == {"serial": SERIAL}

    turn_on = async_mock_service(hass, "switch", "turn_on")
    flow = await repairs.async_create_fix_flow(hass, issue.issue_id, issue.data)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm"
    assert turn_on == [], "nothing until confirmed"
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [c.data["entity_id"] for c in turn_on] == [switch]


async def test_an_older_nest_s_repair_only_says_how(hass: HomeAssistant, mqtt_mock) -> None:
    _thermostat(hass)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser", "room_sensor": "sensor.lounge"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"room_sensor_{SERIAL}")
    assert not issue.is_fixable and issue.translation_key == "room_sensor_unsupported"


def test_any_alarm_is_a_card_and_alarmo_s_says_more():
    plain = State("alarm_control_panel.house", "armed_away", friendly_name="House", supported_features=1 | 2 | 4,
                  code_format="number", code_arm_required=False)
    card = cards.card(plain, alarms={"alarm_control_panel.house": {"names": {"away": " Going out "}, "hidden": ["night"],
                                                                   "code_length": 4}})
    assert card == {"kind": "alarm", "id": "alarm_control_panel.house", "name": "House", "icon": "", "state": "armed_away",
                    "modes": [{"mode": "home", "name": ""}, {"mode": "away", "name": "Going out"}], "code": "number",
                    "code_length": 4, "delay": None, "sensors": [], "can_force": False, "takeover": True}
    assert cards.card(State("alarm_control_panel.house", "disarmed", supported_features=32 | 16))["modes"] == \
        [{"mode": "vacation", "name": ""}, {"mode": "custom_bypass", "name": ""}], "in the ring's order"
    assert cards.card(State("alarm_control_panel.x", "disarmed", code_format="something"))["code"] is None
    manual = State("alarm_control_panel.x", "disarmed", code_format="number", code_arm_required=False)
    assert cards.card(manual)["code"] is None, "its code's format, but none needed to arm"

    from datetime import datetime, timezone
    began = datetime(2026, 11, 10, 21, 0, tzinfo=timezone.utc)
    door = State("binary_sensor.front_door", "on", friendly_name="Front door")
    alarmo = State("alarm_control_panel.alarmo", "pending", supported_features=15, code_format="number", arm_mode="armed_away",
                   next_state="triggered", open_sensors={"binary_sensor.front_door": "on"}, delay=30)
    alarmo.last_changed = began
    card = cards.card(alarmo, {"binary_sensor.front_door": door}.get)
    assert card["delay"] == {"ends": began.timestamp() + 30, "seconds": 30}, "its delay, from when the state began"
    assert card["sensors"] == ["Front door"] and card["can_force"]
    disarmed = State("alarm_control_panel.alarmo", "disarmed", supported_features=15, code_format=None, arm_mode=None,
                     open_sensors=None, delay=None)
    assert cards.card(disarmed)["code"] is None, "Alarmo asks no code to arm when it needs none"


def test_an_alarm_is_armed_and_disarmed_only_as_allowed():
    alarm = ["alarm_control_panel.house"]
    command = lambda **p: cards.command({"id": "alarm_control_panel.house", **p}, alarm)
    assert command(action="arm", mode="away") == cards.Call("alarm_control_panel", "alarm_arm_away",
                                                            "alarm_control_panel.house", {})
    assert command(action="arm", mode="home", code="1234", force=True).data == {"code": "1234", "force": True}
    assert command(action="disarm", code="1234") == cards.Call("alarm_control_panel", "alarm_disarm",
                                                               "alarm_control_panel.house", {"code": "1234"})
    assert command(action="arm", mode="party") is None
    assert command(action="disarm", code=1234) is None and command(action="disarm", code="1" * 40) is None
    assert cards.command({"id": "alarm_control_panel.other", "action": "disarm"}, alarm) is None
    assert cards.redacted({"id": "x", "code": "1234"}) == {"id": "x", "code": "…"}


def test_three_wrong_codes_lock_it_out_twice_as_long_each_time():
    tries = cards.Tries()
    assert [tries.failed(0), tries.failed(1), tries.failed(2)] == [(2, 0), (1, 0), (0, 30)]
    assert tries.locked(12) == 20
    assert [tries.failed(40), tries.failed(41), tries.failed(42)] == [(2, 0), (1, 0), (0, 60)]
    tries.passed()
    assert [tries.failed(200), tries.failed(201), tries.failed(202)][-1] == (0, 30), "a code taken starts it again"


async def test_the_thermostat_arms_and_disarms_an_alarm_and_hears_how_it_went(hass: HomeAssistant, mqtt_mock,
                                                                              monkeypatch) -> None:
    import custom_components.fledge as fledge
    monkeypatch.setattr(fledge, "ALARM_ANSWER", 0.2)
    assert await async_setup_component(hass, "alarm_control_panel", {"alarm_control_panel": {
        "platform": "manual", "name": "House", "code": "1234", "code_arm_required": False,
        "arming_time": 0, "delay_time": 0}})
    await hass.async_block_till_done()
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["alarm_control_panel.house"], "wake": "chooser",
                                     "alarms": {"alarm_control_panel.house": {"names": {"away": "Going out"},
                                                                              "code_length": 4}}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    card = json.loads(_published(mqtt_mock)[-1][1])["cards"][0]
    assert (card["kind"], card["state"], card["code"], card["code_length"]) == ("alarm", "disarmed", None, 4)
    assert {"mode": "away", "name": "Going out"} in card["modes"]

    results = f"nestlocal/{SERIAL}/set/alarm_result"
    answers = lambda: [json.loads(c.args[1]) for c in mqtt_mock.async_publish.call_args_list if c.args[0] == results]
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "alarm_control_panel.house", "action": "arm", "mode": "away"}))
    await hass.async_block_till_done()
    assert hass.states.get("alarm_control_panel.house").state == "armed_away"
    assert answers()[-1] == {"alarm": "alarm_control_panel.house", "ok": True}

    for _ in range(2):
        async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "alarm_control_panel.house", "action": "disarm",
                                                            "code": "9999"}))
        await hass.async_block_till_done()
    assert answers()[-2:] == [{"alarm": "alarm_control_panel.house", "ok": False, "error": "rejected", "tries": 2},
                              {"alarm": "alarm_control_panel.house", "ok": False, "error": "rejected", "tries": 1}]
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "alarm_control_panel.house", "action": "disarm", "code": "9999"}))
    await hass.async_block_till_done()
    assert answers()[-1] == {"alarm": "alarm_control_panel.house", "ok": False, "error": "locked", "locked_for": 30}
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "alarm_control_panel.house", "action": "disarm", "code": "1234"}))
    await hass.async_block_till_done()
    assert answers()[-1]["error"] == "locked", "locked out: not even the right code"
    assert hass.states.get("alarm_control_panel.house").state == "armed_away"
    async_fire_mqtt_message(hass, f"nestlocal/{SERIAL}/ui/protocol", "8")
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"outdated_{SERIAL}") is not None, \
        "with an alarm among its cards, a Nest older than protocol 9 is out of date"

    entry.runtime_data.tries["alarm_control_panel.house"].until = 0
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "alarm_control_panel.house", "action": "disarm", "code": "1234"}))
    await hass.async_block_till_done()
    assert answers()[-1] == {"alarm": "alarm_control_panel.house", "ok": True}
    assert hass.states.get("alarm_control_panel.house").state == "disarmed"



async def test_the_panel_saves_an_alarm_s_names_and_code_length(hass: HomeAssistant, mqtt_mock, hass_ws_client) -> None:
    hass.states.async_set("alarm_control_panel.house", "disarmed", {"friendly_name": "House", "supported_features": 7})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["alarm_control_panel.house"], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "fledge/save", "entry_id": entry.entry_id, "wake": "chooser",
                            "entities": ["alarm_control_panel.house"],
                            "alarms": {"alarm_control_panel.house": {"names": {"away": "  Going out ", "home": " "},
                                                                     "hidden": ["night"], "code_length": 4,
                                                                     "takeover": False}}})
    assert (await client.receive_json())["success"]
    assert entry.options["alarms"] == {"alarm_control_panel.house": {"names": {"away": "Going out"}, "hidden": ["night"],
                                                                     "code_length": 4, "takeover": False}}
    await hass.async_block_till_done()
    card = json.loads(_published(mqtt_mock)[-1][1])["cards"][0]
    assert card["modes"] == [{"mode": "home", "name": ""}, {"mode": "away", "name": "Going out"}]
    assert card["takeover"] is False, "it waits on its card"

    await client.send_json({"id": 2, "type": "fledge/save", "entry_id": entry.entry_id, "wake": "chooser",
                            "entities": ["alarm_control_panel.house"]})
    assert (await client.receive_json())["success"]
    assert entry.options["alarms"]["alarm_control_panel.house"]["code_length"] == 4, "an older panel: kept"
    await client.send_json({"id": 3, "type": "fledge/save", "entry_id": entry.entry_id, "wake": "chooser",
                            "entities": ["alarm_control_panel.house"],
                            "alarms": {"alarm_control_panel.house": {"code_length": 2}}})
    assert not (await client.receive_json())["success"], "a code of two digits isn't one"


def test_a_ring_s_limits_are_its_ends_and_its_commands_keep_within_them():
    limits = cards.limit_settings({
        "climate.kitchen": {"temperature": [18, 25]},
        "media_player.speaker": {"volume": [10, 50], "brightness": [1, 2]},
        "light.lamp": {"brightness": [20, None], "kelvin": [2700, 4000]},
        "cover.blind": {"position": [30, 31]},
        "light.wrong": {"brightness": [60, 40]},
        "light.blank": {"brightness": [None, None]},
        "switch.fan": {"brightness": [10, 20]},
        "light.far": {"brightness": [0, 101]},
    })
    assert limits == {"climate.kitchen": {"temperature": [18, 25]}, "media_player.speaker": {"volume": [10, 50]},
                      "light.lamp": {"brightness": [20, None], "kelvin": [2700, 4000]},
                      "cover.blind": {"position": [30, 31]}}, \
        "only what each ring sets, a lowest under the highest, within what it can be"
    assert cards.limit_settings(limits, ["light.lamp"]) == {"light.lamp": limits["light.lamp"]}, "only cards shown"

    kitchen = State("climate.kitchen", "heat", temperature=16, min_temp=7, max_temp=30, target_temp_step=0.5)
    speaker = State("media_player.speaker", "playing", volume_level=0.8)
    lamp = State("light.lamp", "on", brightness=13, supported_color_modes=["color_temp"], color_temp_kelvin=2200,
                 min_color_temp_kelvin=2000, max_color_temp_kelvin=6500)
    blind = State("cover.blind", "open", current_position=80, supported_features=4)
    group = State("light.room", "on", supported_color_modes=["brightness"])
    group.attributes["entity_id"] = ["light.lamp"]
    states = {s.entity_id: s for s in (kitchen, speaker, lamp, blind, group)}
    shown = cards.cards(list(states), states.get, limits={**limits, "light.room": {"brightness": [40, 90]}})["cards"]
    climate, media, lights, cover, room = shown
    assert (climate["min"], climate["max"], climate["target"]) == (18, 25, 16), \
        "the ring's ends are the limits; the target as it is, past one"
    assert (media["volume_limits"], media["volume"]) == ([10, 50], 80)
    one = lights["lights"][0]
    assert (one["brightness_limits"], one["kelvins"], one["kelvin"]) == ([20, 100], [2700, 4000], 2200), \
        "no highest: as far as it goes"
    assert cover["position_limits"] == [30, 31]
    assert room["lights"][0]["brightness_limits"] == [40, 90] and "brightness_limits" not in room["members"][0]
    plain = cards.cards(["climate.kitchen"], states.get)["cards"][0]
    assert (plain["min"], plain["max"]) == (7, 30) and "volume_limits" not in cards.media(speaker)

    every = cards.with_members({**limits, "light.room": {"brightness": [40, 90]}}, states.get)
    command = lambda id, action, value: cards.command({"id": id, "action": action, "value": value}, list(states), states.get, every)
    assert command("climate.kitchen", "temperature", 16).data == {"temperature": 18}, "an older Nest's turn kept within"
    assert command("climate.kitchen", "temperature", 21).data == {"temperature": 21}
    assert command("media_player.speaker", "volume", 80).data == {"volume_level": 0.5}
    assert command("media_player.speaker", "volume", 5).data == {"volume_level": 0.1}
    assert command("light.lamp", "brightness", 5).data == {"brightness_pct": 20}
    assert command("light.lamp", "color_temp", 6500).data == {"color_temp_kelvin": 4000}
    assert command("cover.blind", "position", 100).data == {"position": 31}
    assert command("light.room", "brightness", 100).data == {"brightness_pct": 90}
    assert every["light.lamp"] == limits["light.lamp"], "a group's own light keeps its own limits"

    # heat_cool: the other end kept within them too, clear of the one set.
    states["climate.kitchen"] = State("climate.kitchen", "heat_cool", target_temp_low=19, target_temp_high=27,
                                      target_temp_step=1, hvac_action="heating")
    assert command("climate.kitchen", "temperature", 17).data == {"target_temp_low": 18, "target_temp_high": 25}


def test_limits_need_a_build_that_parks_the_ring():
    from custom_components.fledge.const import needs
    from custom_components.fledge.config_flow import options_from
    assert needs(["light.a"], limits=True) == 13
    options = options_from({"entities": ["light.a"]}, limits={"light.a": {"brightness": [10, 90]},
                                                               "light.gone": {"brightness": [10, 90]}})
    assert options["limits"] == {"light.a": {"brightness": [10, 90]}}
    assert "limits" not in options_from({"entities": ["light.a"]}, limits={})


def test_a_limit_that_gives_way_lets_the_nest_past_that_end_only():
    limits = cards.limit_settings({
        "media_player.speaker": {"volume": [10, 50], "volume_push": [False, True]},
        "climate.kitchen": {"temperature": [18, 25], "temperature_push": [True, True]},
        "light.lamp": {"brightness": [20, None], "brightness_push": [False, True], "kelvin": [2700, 4000],
                       "kelvin_push": [1.0, 0.0]},
        "cover.blind": {"position": [30, 60], "position_push": [False, False]},
    })
    assert limits["media_player.speaker"]["volume_push"] == [False, True]
    assert "brightness_push" not in limits["light.lamp"], "only an end with a limit gives way"
    assert limits["light.lamp"]["kelvin_push"] == [True, False], "as the panel's numbers say it"
    assert "position_push" not in limits["cover.blind"], "kept only where one does"
    assert (cards.limits_level(None), cards.limits_level({"cover.blind": limits["cover.blind"]}), cards.limits_level(limits)) == (0, 1, 2)
    assert const.needs([], limits=1) == const.PROTOCOL_LIMITS and const.needs([], limits=2) == const.PROTOCOL_PUSH

    kitchen = State("climate.kitchen", "heat", temperature=21, min_temp=7, max_temp=30, target_temp_step=0.5)
    speaker = State("media_player.speaker", "playing", volume_level=0.4)
    lamp = State("light.lamp", "on", brightness=128, supported_color_modes=["color_temp"], color_temp_kelvin=3000,
                 min_color_temp_kelvin=2000, max_color_temp_kelvin=6500)
    states = {s.entity_id: s for s in (kitchen, speaker, lamp)}
    climate, media, lights = cards.cards(list(states), states.get, limits=limits)["cards"]
    assert (climate["min"], climate["max"], climate["temperature_whole"], climate["temperature_push"]) == (18, 25, [7, 30], [True, True])
    assert media["volume_push"] == [False, True]
    one = lights["lights"][0]
    assert (one["kelvins"], one["kelvin_whole"], one["kelvin_push"]) == ([2700, 4000], [2000, 6500], [True, False])

    def sent(entity, action, value):
        return cards.command({"id": entity, "action": action, "value": value}, list(states), states.get, limits).data

    assert sent("media_player.speaker", "volume", 70) == {"volume_level": 0.7}, "past its highest, which gives way"
    assert sent("media_player.speaker", "volume", 3) == {"volume_level": 0.1}, "its lowest holds"
    assert sent("climate.kitchen", "temperature", 28) == {"temperature": 28}
    assert sent("light.lamp", "color_temp", 2200) == {"color_temp_kelvin": 2200}
    assert sent("light.lamp", "color_temp", 5000) == {"color_temp_kelvin": 4000}


async def test_the_battery_face_says_what_the_battery_is_doing(hass: HomeAssistant):
    from custom_components.fledge import faces, glance
    hass.states.async_set("sensor.car_battery", "64", {"friendly_name": "Family car", "unit_of_measurement": "%", "icon": "mdi:car-electric"})
    hass.states.async_set("binary_sensor.car_charging", "on")
    hass.states.async_set("binary_sensor.car_plugged", "on")
    hass.states.async_set("sensor.wallbox_power", "7.2", {"unit_of_measurement": "kW"})
    hass.states.async_set("number.car_limit", "80")
    spec = {"level": "sensor.car_battery", "charging": "binary_sensor.car_charging", "plugged": "binary_sensor.car_plugged",
            "power": "sensor.wallbox_power", "limit": "number.car_limit"}
    chosen = glance.settings({"layout": "battery", "battery": spec})
    assert glance.watched(chosen) == sorted(spec.values())
    shown = glance.battery(hass, chosen["battery"])
    assert shown == {"label": "Family car", "level": 64.0, "status": "charging", "text": "Charging · 7.2 kW", "limit": 80.0,
                     "low": 20, "critical": 10, "icon": "mdi:car-electric"}
    hass.states.async_set("binary_sensor.car_charging", "off")
    assert glance.battery(hass, chosen["battery"])["text"] == "Plugged in"
    hass.states.async_set("binary_sensor.car_plugged", "off")
    hass.states.async_set("sensor.wallbox_power", "0", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.car_battery", "8")
    low = glance.battery(hass, chosen["battery"])
    assert (low["status"], low["text"], low["label"]) == ("idle", "Battery very low", "sensor.car_battery")
    # Without a charging entity, power going in counts; going out is a discharge.
    hass.states.async_set("sensor.wallbox_power", "-1.4", {"unit_of_measurement": "kW"})
    home = glance.battery(hass, {"level": "sensor.car_battery", "power": "sensor.wallbox_power", "label": "Home battery"})
    assert (home["status"], home["text"], home["label"]) == ("discharging", "Discharging · 1.4 kW", "Home battery")
    assert glance.battery(hass, {}) is None
    face = faces.settings({"layout": "battery", "battery": spec})
    assert faces.glance_raw(face, {})["battery"]["level"] == "sensor.car_battery"


def test_a_battery_face_s_picture_is_fitted_whole_and_kept_see_through():
    from io import BytesIO
    from PIL import Image
    from custom_components import fledge
    source = Image.new("RGBA", (400, 100), (200, 30, 30, 255))
    source.putpixel((0, 0), (0, 0, 0, 0))
    data = BytesIO()
    source.save(data, "PNG")
    with Image.open(BytesIO(art.cutout(data.getvalue()))) as out:
        assert (out.mode, out.size) == ("RGBA", (168, 168)), "a square, as the Nest takes any picture"
        assert out.getpixel((84, 84)) == (200, 30, 30, 255), "the picture across its middle"
        assert out.getpixel((84, 10))[3] == 0 and out.getpixel((84, 160))[3] == 0, "see-through above and below"
    assert art.cutout(b"not a picture") is None
    shown = {"battery": {"image": "/local/car.png"}}
    fledge._pictured(shown, "battery:glance")
    assert shown["battery"]["picture"] == "battery:glance"
    plain = {"battery": {"icon": "mdi:car"}}
    fledge._pictured(plain, "battery:glance")
    assert "picture" not in plain["battery"], "an icon: nothing to send"


def test_the_nest_s_own_card_takes_limits_too():
    from custom_components.fledge.config_flow import options_from
    own = const.OWN_LIMITS
    options = options_from({"entities": ["light.a"]}, limits={own: {"temperature": [16, 24], "temperature_push": [False, True]}})
    assert options["limits"] == {own: {"temperature": [16, 24], "temperature_push": [False, True]}}, "kept, though it's no entity among the cards"
    assert cards.limits_level(options["limits"]) == 3 and const.needs([], limits=3) == const.PROTOCOL_OWN_LIMITS

    out = cards.cards([], {}.get, limits=options["limits"], unit="°F")
    assert out["thermostat_limits"] == {"temperature": [16, 24], "push": [False, True], "unit": "°F"}
    one = cards.cards([], {}.get, limits={own: {"temperature": [None, 22.5]}})
    assert one["thermostat_limits"] == {"temperature": [None, 22.5], "push": [False, False], "unit": "°C"}
    assert "thermostat_limits" not in cards.cards([], {}.get, limits={"light.a": {"brightness": [10, 90]}})

    # The panel's picture of it: its heating entity's card, its ring between them.
    nest = State("climate.nest", "heat", temperature=20, min_temp=9, max_temp=32, target_temp_step=0.5)
    card = cards.climate(nest, options["limits"][own])
    assert (card["min"], card["max"], card["temperature_whole"]) == (16, 24, [9, 32])


async def test_call_for_heat_asks_while_heating_control_is_home_assistant_s(hass: HomeAssistant, mqtt_mock,
                                                                             freezer) -> None:
    from datetime import timedelta
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": [], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    [switch] = [e for e in hass.states.async_entity_ids("switch") if e.endswith("call_for_heat")]
    topic = f"nestlocal/{SERIAL}/set/call_for_heat"
    asks = lambda: [json.loads(c.args[1]) for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic]

    assert hass.states.get(switch).state == "unavailable", "the Nest decides"
    async_fire_mqtt_message(hass, f"nestlocal/{SERIAL}/state", json.dumps({"heat_control": True,
                                                                             "heat_control_phase": "on_its_own"}))
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "off"
    assert asks()[-1] == {"heat": False, "lease": 600, "rooms": []}, "asked at once"

    await hass.services.async_call(DOMAIN, "call_for_heat", {"entity_id": switch, "heat": True, "rooms": ["Office"]},
                                   blocking=True)
    assert asks()[-1] == {"heat": True, "lease": 600, "rooms": ["Office"]}
    assert hass.states.get(switch).attributes["rooms"] == ["Office"]

    n = len(asks())
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(asks()) == n + 1 and asks()[-1]["heat"], "renewed every minute"

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert asks()[-1] == {"heat": False, "lease": 600, "rooms": []}
