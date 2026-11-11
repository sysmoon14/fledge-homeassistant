"""Glance faces as cards: their settings, what the Nest is sent, the panel,
the options flow and the actions the Nest may give."""

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message, async_mock_service

from custom_components.fledge import cards, faces
from custom_components.fledge.config_flow import with_faces
from custom_components.fledge.const import DOMAIN, needs

SERIAL = "09AB01AC36230301"
CARDS = f"nestlocal/{SERIAL}/set/ui"
COMMANDS = f"nestlocal/{SERIAL}/ui/command"
CLOCK = "fledge_face.a1b2c3"
WEATHER = "fledge_face.d4e5f6"


class State:
    def __init__(self, entity_id, state, **attributes):
        self.entity_id, self.state, self.attributes = entity_id, state, attributes


def test_a_face_s_settings_and_its_glance():
    face = faces.settings({"layout": "weather", "actions": ["scene.movie", "light.hall", "scene.movie"],
                           "hold": "switch.not_an_action"})
    assert face["actions"] == ["scene.movie", "light.hall"], "an action once"
    assert face["hold"] is None, "a hold only does one of its actions"
    assert faces.settings({"actions": ["light.hall"], "hold": "light.hall"})["hold"] == "light.hall"
    assert faces.settings({"actions": ["camera.front"]}) == faces.settings({}), "a camera isn't an action: the defaults"
    assert faces.name(face) == "Weather" and faces.name(face, " Outside ") == "Outside"
    raw = faces.glance_raw(face, {"hours": "12", "calendars": ["calendar.home"], "layout": "clock",
                                  "night": {"mode": "sun", "layout": "analog"}, "colour": False})
    assert raw["layout"] == "weather" and raw["hours"] == "12" and raw["calendars"] == ["calendar.home"]
    assert raw["night"] == {"mode": "off"}, "a face has no night: someone is standing at it"
    assert raw["colour"] is True, "its own colour, not the glance's"
    assert faces.is_face(CLOCK) and not faces.is_face("light.hall") and faces.new_id().startswith("fledge_face.")


def test_a_face_s_card_has_its_actions_and_its_hold():
    states = {"scene.movie": State("scene.movie", "scening", friendly_name="Movie Night"),
              "light.hall": State("light.hall", "on", friendly_name="Hall Light", icon="mdi:ceiling-light")}
    face = faces.settings({"actions": ["scene.movie", "scene.gone", "light.hall"], "hold": "light.hall"})
    card = faces.card(CLOCK, face, {"layout": "clock"}, states.get, "Clock")
    assert card["kind"] == "face" and card["name"] == "Clock" and card["glance"] == {"layout": "clock"}
    assert card["actions"] == [
        {"id": "scene.movie", "name": "Movie Night", "icon": "", "kind": "run"},
        {"id": "light.hall", "name": "Hall Light", "icon": "mdi:ceiling-light", "kind": "switch", "on": True}]
    assert card["hold"] == 1, "by its place among the actions Home Assistant knows"
    assert "hold" not in faces.card(CLOCK, faces.settings({"actions": ["scene.movie"]}), {}, states.get)


def test_faces_take_their_place_among_the_cards():
    states = {"light.lamp": State("light.lamp", "on", supported_color_modes=["onoff"])}
    face = {"kind": "face", "id": CLOCK, "name": "Clock"}
    out = cards.cards([CLOCK, "light.gone", "light.lamp"], states.get, device=CLOCK, thermostat=2, faces={CLOCK: face})
    assert [c["kind"] for c in out["cards"]] == ["face", "lights"]
    assert out["wake"] == 0 and out["thermostat"] == 1, "a face counts among the cards, a lost light doesn't"
    assert cards.cards([CLOCK], states.get)["cards"] == [], "a face with no card built: left out"
    assert needs([CLOCK]) == 13 and needs(["light.lamp"], glance=True) == 11


def test_the_options_flow_keeps_the_faces_where_they_were():
    before = ["light.lamp", CLOCK, "switch.fan", WEATHER]
    assert with_faces(["switch.fan", "light.lamp"], before) == ["switch.fan", CLOCK, "light.lamp", WEATHER]
    assert with_faces([], [CLOCK]) == [CLOCK]


def _thermostat(hass: HomeAssistant) -> None:
    config = MockConfigEntry(domain="mqtt")
    config.add_to_hass(hass)
    thermostat = dr.async_get(hass).async_get_or_create(config_entry_id=config.entry_id, identifiers={("mqtt", SERIAL)},
                                                        name="Hallway", serial_number=SERIAL)
    er.async_get(hass).async_get_or_create("climate", "mqtt", "nestlocal_thermostat", device_id=thermostat.id,
                                           suggested_object_id="nest_thermostat")
    hass.states.async_set("climate.nest_thermostat", "heat", {"temperature": 21, "current_temperature": 20.4})


def _published(mqtt_mock) -> dict:
    return json.loads([c.args for c in mqtt_mock.async_publish.call_args_list if c.args[0] == CARDS][-1][1])


async def test_a_face_reaches_the_nest_and_its_actions_are_allowed(hass: HomeAssistant, mqtt_mock) -> None:
    _thermostat(hass)
    hass.states.async_set("scene.movie", "2026-11-11T20:00:00", {"friendly_name": "Movie Night"})
    hass.states.async_set("light.hall", "off", {"friendly_name": "Hall Light", "supported_color_modes": ["onoff"]})
    hass.states.async_set("sensor.price", "24.3", {"friendly_name": "Electricity", "unit_of_measurement": "p/kWh"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL}, options={
        "entities": [CLOCK, "light.hall"], "wake": "chooser", "names": {CLOCK: "Price"},
        "glance_settings": {"hours": "12"},
        "faces": {CLOCK: {"layout": "number", "big": "sensor.price", "lines": {"number": [{"source": "time"}]},
                          "actions": ["scene.movie", "light.hall"], "hold": "scene.movie"}}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sent = _published(mqtt_mock)
    face = sent["cards"][0]
    assert (face["kind"], face["id"], face["name"], face["hold"]) == ("face", CLOCK, "Price", 0)
    assert face["glance"]["layout"] == "number" and face["glance"]["clock"]["hours"] == 12, "the glance's time format"
    assert face["glance"]["big"] == {"label": "Electricity", "value": "24.3", "unit": "p/kWh"}
    assert "night" not in face["glance"] and "glance" not in sent, "no glance shown: only the face's"
    assert [a["kind"] for a in face["actions"]] == ["run", "switch"] and face["actions"][1]["on"] is False

    hass.states.async_set("sensor.price", "25.1", {"friendly_name": "Electricity", "unit_of_measurement": "p/kWh"})
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall Light", "supported_color_modes": ["onoff"]})
    await hass.async_block_till_done()
    face = _published(mqtt_mock)["cards"][0]
    assert face["actions"][1]["on"] is True, "an action switched elsewhere shows on the Nest"

    scene = async_mock_service(hass, "scene", "turn_on")
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "scene.movie", "action": "run"}))
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": "scene.other", "action": "run"}))
    async_fire_mqtt_message(hass, COMMANDS, json.dumps({"id": CLOCK, "action": "run"}))
    await hass.async_block_till_done()
    assert [c.data for c in scene] == [{"entity_id": "scene.movie"}], "only a face's own actions"


async def test_the_panel_previews_and_saves_a_face(hass: HomeAssistant, mqtt_mock, hass_ws_client) -> None:
    _thermostat(hass)
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp", "supported_color_modes": ["onoff"]})
    hass.states.async_set("scene.movie", "unknown", {"friendly_name": "Movie Night"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["light.lamp"], "wake": "chooser"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "fledge/preview", "entry_id": entry.entry_id, "entities": [CLOCK, "light.lamp"],
                                    "faces": {CLOCK: {"layout": "agenda", "actions": ["scene.movie"]}},
                                    "glance": {"hours": "24"}, "names": {CLOCK: "Next"}})
    preview = (await client.receive_json())["result"]
    assert list(preview["cards"]) == [CLOCK, "light.lamp"]
    assert preview["cards"][CLOCK]["name"] == "Next" and preview["cards"][CLOCK]["glance"]["layout"] == "agenda"

    await client.send_json_auto_id({"type": "fledge/save", "entry_id": entry.entry_id, "entities": [CLOCK, "light.lamp"],
                                    "wake": "chooser", "wake_device": CLOCK, "names": {CLOCK: "Next"},
                                    "faces": {CLOCK: {"layout": "agenda", "actions": ["scene.movie"], "hold": "scene.movie"},
                                              WEATHER: {"layout": "weather"}}})
    response = await client.receive_json()
    assert response["success"], response
    await hass.async_block_till_done()
    assert entry.options["faces"] == {CLOCK: {"layout": "agenda", "lines": {}, "colour": True,
                                              "actions": ["scene.movie"], "hold": "scene.movie"}}, "only faces among the cards"
    assert entry.options["wake_device"] == CLOCK and entry.options["names"] == {CLOCK: "Next"}
    sent = _published(mqtt_mock)
    assert sent["wake"] == 0 and sent["cards"][0]["name"] == "Next"
    select = er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{SERIAL}_wake_to")
    assert hass.states.get(select).state == "Next", "Wake to names a face"

    await client.send_json_auto_id({"type": "fledge/thermostats"})
    shown = (await client.receive_json())["result"]["thermostats"][0]
    assert shown["faces"][CLOCK]["actions"] == ["scene.movie"] and shown["needs"] == 13

    await client.send_json_auto_id({"type": "fledge/save", "entry_id": entry.entry_id, "entities": [CLOCK],
                                    "wake": "chooser", "faces": {CLOCK: {"actions": ["camera.front"]}}})
    assert not (await client.receive_json())["success"], "a camera can't be a face's action"

    # The options flow can't show a face: it keeps it where it was.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {"entities": ["light.lamp"], "wake": "chooser"})
    await hass.async_block_till_done()
    assert entry.options["entities"] == [CLOCK, "light.lamp"] and CLOCK in entry.options["faces"]
