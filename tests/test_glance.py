"""The glance (glance.py): its settings, lines, night, and what reaches the Nest."""

import json
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.fledge import glance
from custom_components.fledge.const import DOMAIN

SERIAL = "09AB01AC36230301"
CARDS = f"nestlocal/{SERIAL}/set/ui"
TRY = f"nestlocal/{SERIAL}/set/glance_try"


def _published(mqtt_mock, topic=CARDS):
    return [call.args for call in mqtt_mock.async_publish.call_args_list if call.args[0] == topic]


def _at(hass: HomeAssistant, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 11, hour, minute, tzinfo=dt_util.get_default_time_zone())


def test_settings_fill_in_the_glance_as_it_was():
    chosen = glance.settings(None)
    assert (chosen["layout"], chosen["hours"], chosen["night"]["mode"]) == ("clock", "auto", "off")
    assert [s["source"] for s in glance.lines_for(chosen["lines"], "clock")] == ["date", "weather", "whats_on"]
    assert [s["source"] for s in glance.lines_for({}, "analog", night=True)] == ["date"]
    assert [s["source"] for s in glance.lines_for({}, "temp", night=True)] == ["heating", "none"], "the night's: quieter"
    assert glance.lines_for({"analog": [{"source": "time"}, {"source": "date"}]}, "analog") == [{"source": "time"}], \
        "as many as the layout has room for"
    assert not glance.customised({}) and not glance.customised({"layout": "clock"})
    assert glance.customised({"hours": "12"})
    assert glance.settings({"layout": "hologram"}) == glance.settings({}), "settings that don't read: the defaults"


async def test_the_clock_reads_twelve_hours_where_people_do(hass: HomeAssistant):
    assert glance.twelve_hour(hass, "12") and not glance.twelve_hour(hass, "24")
    hass.config.country = "US"
    assert glance.twelve_hour(hass, "auto")
    hass.config.country = "GB"
    assert not glance.twelve_hour(hass, "auto")
    moment = datetime(2026, 11, 11, 16, 30)
    assert (glance.time_text(moment, False), glance.time_text(moment, True)) == ("16:30", "4:30 PM")
    assert glance.time_text(moment.replace(hour=0), True) == "12:30 AM"


async def test_night_by_set_times_a_sensor_or_the_sun(hass: HomeAssistant):
    hours = {"mode": "hours", "from": "22:00", "until": "07:00"}
    assert glance.is_night(hass, hours, _at(hass, 23)) and glance.is_night(hass, hours, _at(hass, 6, 59))
    assert not glance.is_night(hass, hours, _at(hass, 7)) and not glance.is_night(hass, hours, _at(hass, 12))
    afternoon = {"mode": "hours", "from": "13:00", "until": "15:00"}
    assert glance.is_night(hass, afternoon, _at(hass, 14)) and not glance.is_night(hass, afternoon, _at(hass, 16))
    bedtime = {"mode": "sensor", "entity": "binary_sensor.bedtime", "state": "on"}
    assert not glance.is_night(hass, bedtime), "no sensor yet: day"
    hass.states.async_set("binary_sensor.bedtime", "on")
    assert glance.is_night(hass, bedtime)
    assert not glance.is_night(hass, {**bedtime, "state": "off"})
    hass.states.async_set("binary_sensor.bedtime", "unavailable")
    assert not glance.is_night(hass, bedtime) and not glance.is_night(hass, {**bedtime, "state": "off"}), \
        "unavailable: day, whichever state means night"
    sun = {"mode": "sun", "after": 30, "before": 0}
    assert glance.is_night(hass, sun, _at(hass, 2)) and not glance.is_night(hass, sun, _at(hass, 12))
    assert not glance.is_night(hass, {"mode": "off"}, _at(hass, 2))


async def test_lines_worked_out_in_home_assistant(hass: HomeAssistant):
    now = _at(hass, 13, 10)
    hourly = [{"datetime": (now + timedelta(hours=h)).isoformat(), "condition": "rainy" if h >= 2 else "cloudy",
               "precipitation_probability": 80 if h >= 2 else 10} for h in range(1, 12)]
    daily = [{"datetime": now.isoformat(), "temperature": 12.4, "templow": 4.6}]
    hass.states.async_set("weather.home", "cloudy", {"temperature": 9})
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    hass.states.async_set("person.sam", "not_home", {"friendly_name": "Sam"})
    hass.states.async_set("sensor.price", "24.31", {"friendly_name": "Electricity", "unit_of_measurement": "p/kWh",
                                                    "device_class": "monetary"})
    hass.states.async_set("calendar.home", "off", {"message": "Dentist", "all_day": False,
                                                   "start_time": (now + timedelta(days=1)).strftime("%Y-%m-%d 09:00:00")})
    hass.states.async_set("media_player.kitchen", "playing", {"media_title": "Dreams", "media_artist": "Fleetwood Mac"})
    sources = glance.Sources(hass, False, ["media_player.kitchen"], hourly, daily, "weather.home", "calendar.home", now)
    line = lambda source, **more: sources.line({"source": source, **more})  # noqa: E731
    assert line("date") == {"kind": "date"}, "the Nest's own: as a kind"
    assert line("forecast") == {"text": "High 12° · Low 5°", "icon": "mdi:thermometer"}
    assert line("rain") == {"text": "Rain from 15:10", "icon": "mdi:weather-rainy"}
    assert line("people") == {"text": "Alex home", "icon": "mdi:account"}
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam Example-Smith"})
    assert line("people")["text"] == "Alex and Sam home", "first names: full ones take the room"
    hass.states.async_set("person.sam", "not_home", {"friendly_name": "Sam"})
    assert line("entity", entity="sensor.price") == {"text": "Electricity 24.3 p/kWh"}
    hass.states.async_set("sensor.cost", "1.5", {"friendly_name": "Today", "unit_of_measurement": "GBP", "device_class": "monetary"})
    assert line("entity", entity="sensor.cost") == {"text": "Today 1.50 GBP", "icon": "mdi:currency-gbp"} or \
        line("entity", entity="sensor.cost") == {"text": "Today 1.5 GBP", "icon": "mdi:currency-gbp"}
    hass.states.async_set("binary_sensor.door", "on", {"friendly_name": "Front door", "device_class": "door"})
    assert line("entity", entity="binary_sensor.door")["text"] in ("Front door Open", "Front door On"), \
        "in Home Assistant's own words where it has them loaded"
    hass.states.async_set("sensor.alarm", (now + timedelta(hours=18)).isoformat(), {"friendly_name": "Alarm", "device_class": "timestamp"})
    assert line("entity", entity="sensor.alarm") == {"text": "Alarm Tomorrow 07:10"}
    assert line("entity", entity="sensor.price", label="Power")["text"] == "Power 24.3 p/kWh"
    assert line("calendar") == {"text": "Tomorrow 09:00 Dentist", "icon": "mdi:calendar"}
    assert line("playing") == {"text": "Dreams · Fleetwood Mac", "icon": "mdi:play"}, "no speaker chosen: the cards'"
    hass.states.async_set("media_player.study", "playing", {"media_title": "Podcast"})
    assert line("playing", entity="media_player.study") == {"text": "Podcast", "icon": "mdi:play"}, "the speaker chosen"
    hass.states.async_set("media_player.study", "paused", {"media_title": "Podcast"})
    assert line("playing", entity="media_player.study") == {"text": ""}, "not the kitchen's instead"
    assert line("template", template="{{ 2 + 2 }} things") == {"text": "4 things"}
    assert line("template", template="{{ nonsense(") == {"text": ""}, "a broken template: an empty line"
    assert line("entity", entity="sensor.gone") == {"text": ""}, "an empty line keeps its place"
    assert sources.big("sensor.price", None) == {"label": "Electricity", "value": "24.3", "unit": "p/kWh"}
    twelve = glance.Sources(hass, True, [], hourly, daily, "weather.home", None, now)
    assert twelve.line({"source": "rain"})["text"] == "Rain from 3:10 PM"
    dry = glance.Sources(hass, False, [], [{**f, "condition": "sunny", "precipitation_probability": 0} for f in hourly],
                         daily, "weather.home", None, now)
    assert dry.line({"source": "rain"})["text"] == "No rain expected"


async def test_the_glance_as_the_nest_takes_it(hass: HomeAssistant):
    chosen = glance.settings({"layout": "number", "hours": "12", "big": "sensor.price",
                              "lines": {"number": [{"source": "time"}]},
                              "night": {"mode": "hours", "layout": "analog", "dim": True}})
    hass.states.async_set("sensor.price", "24.31", {"friendly_name": "Electricity", "unit_of_measurement": "p/kWh"})
    sources = glance.Sources(hass, True, [])
    day = glance.build(sources, chosen, False, {"offset": 0})
    assert day == {"offset": 0, "layout": "number", "clock": {"hours": 12, "ampm": True}, "lines": [{"kind": "time"}],
                   "big": {"label": "Electricity", "value": "24.3", "unit": "p/kWh"}}
    night = glance.build(sources, chosen, True, {"offset": 0})
    assert (night["layout"], night["lines"], night["night"], night["dim"]) == ("analog", [{"kind": "date"}], True, True)


async def _set_up(hass: HomeAssistant, settings: dict, **options) -> MockConfigEntry:
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    thermostat = dr.async_get(hass).async_get_or_create(
        config_entry_id=mqtt_entry.entry_id, identifiers={("mqtt", f"nestlocal_{SERIAL.lower()}")},
        name="Nest Thermostat", manufacturer="Google Nest", serial_number=SERIAL)
    er.async_get(hass).async_get_or_create("climate", "mqtt", "nestlocal_thermostat", device_id=thermostat.id,
                                           suggested_object_id="nest_thermostat")
    hass.states.async_set("climate.nest_thermostat", "heat", {"temperature": 21, "current_temperature": 20.4,
                                                              "current_humidity": 48, "hvac_action": "heating"})
    hass.states.async_set("light.lamp", "on", {"friendly_name": "Lamp"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data={"serial": SERIAL},
                            options={"entities": ["light.lamp"], "wake": "chooser", "far": "glance",
                                     "glance_settings": settings, **options})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_bedtime_sensor_brings_the_night_s_glance(hass: HomeAssistant, mqtt_mock) -> None:
    hass.states.async_set("binary_sensor.bedtime", "off")
    hass.states.async_set("sensor.price", "24.31", {"friendly_name": "Electricity"})
    entry = await _set_up(hass, {"hours": "12", "lines": {"clock": [{"source": "entity", "entity": "sensor.price"}]},
                                 "night": {"mode": "sensor", "entity": "binary_sensor.bedtime", "layout": "analog"}})
    sent = json.loads(_published(mqtt_mock)[-1][1])["glance"]
    assert (sent["layout"], sent["clock"], sent["lines"]) == ("clock", {"hours": 12, "ampm": True},
                                                              [{"text": "Electricity 24.3"}])
    night = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"{SERIAL}_night_glance")
    assert hass.states.get(night).state == "off"
    hass.states.async_set("binary_sensor.bedtime", "on")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    sent = json.loads(_published(mqtt_mock)[-1][1])["glance"]
    assert (sent["layout"], sent["night"], sent["dim"]) == ("analog", True, True)
    assert hass.states.get(night).state == "on"
    hass.states.async_set("sensor.price", "30", {"friendly_name": "Electricity"})
    await hass.async_block_till_done()
    assert hass.states.get(night).state == "on"
    hass.states.async_set("binary_sensor.bedtime", "off")
    hass.states.async_set("sensor.price", "31", {"friendly_name": "Electricity"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert json.loads(_published(mqtt_mock)[-1][1])["glance"]["lines"] == [{"text": "Electricity 31"}], \
        "a line's sensor changing reaches the Nest"
    assert entry.runtime_data.night is False


async def test_set_times_turn_night_on_as_the_clock_passes(hass: HomeAssistant, mqtt_mock, freezer) -> None:
    freezer.move_to(_at(hass, 21, 58))
    await _set_up(hass, {"night": {"mode": "hours", "from": "22:00", "until": "07:00"}})
    assert "night" not in json.loads(_published(mqtt_mock)[-1][1])["glance"]
    freezer.move_to(_at(hass, 22, 0) + timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert json.loads(_published(mqtt_mock)[-1][1])["glance"]["night"] is True


async def test_the_glance_layout_select_and_the_older_nest_s_repair(hass: HomeAssistant, mqtt_mock) -> None:
    from homeassistant.helpers import issue_registry as ir
    from pytest_homeassistant_custom_component.common import async_fire_mqtt_message

    entry = await _set_up(hass, {})
    select = er.async_get(hass).async_get_entity_id("select", DOMAIN, f"{SERIAL}_glance_layout")
    assert hass.states.get(select).state == "Clock"
    thermostat = entry.runtime_data
    await hass.services.async_call("select", "select_option", {"entity_id": select, "option": "Weather"}, blocking=True)
    await hass.async_block_till_done()
    assert entry.runtime_data is thermostat, "no reload"
    assert entry.options["glance_settings"]["layout"] == "weather"
    assert json.loads(_published(mqtt_mock)[-1][1])["glance"]["layout"] == "weather"
    assert hass.states.get(select).state == "Weather"
    async_fire_mqtt_message(hass, f"nestlocal/{SERIAL}/ui/protocol", "10")
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"outdated_{SERIAL}"), "a glance of its own needs protocol 11"
    async_fire_mqtt_message(hass, f"nestlocal/{SERIAL}/ui/protocol", "11")
    await hass.async_block_till_done()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, f"outdated_{SERIAL}")


async def test_the_panel_previews_tries_and_saves_the_glance(hass: HomeAssistant, mqtt_mock, hass_ws_client) -> None:
    entry = await _set_up(hass, {})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "fledge/thermostats"})
    shown = (await client.receive_json())["result"]["thermostats"][0]
    assert shown["glance"]["layout"] == "clock" and shown["night"] is False

    draft = {"layout": "temp", "hours": "24", "lines": {"temp": [{"source": "heating"}, {"source": "inside"}]}}
    await client.send_json_auto_id({"type": "fledge/glance_preview", "entry_id": entry.entry_id, "glance": draft})
    preview = (await client.receive_json())["result"]
    assert preview["glance"]["layout"] == "temp"
    assert preview["lines"] == [{"kind": "heating", "text": "Heating to 21°", "icon": "mdi:fire"},
                                {"kind": "inside", "text": "Inside 20.4° · 48%", "icon": "mdi:thermometer"}]

    await client.send_json_auto_id({"type": "fledge/glance_try", "entry_id": entry.entry_id, "glance": draft})
    assert (await client.receive_json())["success"]
    tried = _published(mqtt_mock, TRY)
    assert len(tried) == 1 and json.loads(tried[0][1])["layout"] == "temp"
    assert entry.options["glance_settings"] == {}, "trying saves nothing"

    await client.send_json_auto_id({"type": "fledge/glance_try", "entry_id": entry.entry_id,
                                    "glance": {"layout": "hologram"}})
    assert not (await client.receive_json())["success"], "settings that don't read are refused"

    await client.send_json_auto_id({"type": "fledge/save", "entry_id": entry.entry_id, "entities": ["light.lamp"],
                                    "wake": "chooser", "far": "glance", "glance_settings": draft})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert entry.options["glance_settings"]["layout"] == "temp"
    await client.send_json_auto_id({"type": "fledge/save", "entry_id": entry.entry_id, "entities": ["light.lamp"],
                                    "wake": "chooser", "far": "glance"})
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert entry.options["glance_settings"]["layout"] == "temp", "an older panel's save keeps it"


async def test_a_chatty_sensor_reaches_the_nest_now_and_then(hass: HomeAssistant, mqtt_mock) -> None:
    from custom_components.fledge import LINES_EVERY

    hass.states.async_set("sensor.power", "100", {"friendly_name": "Power"})
    await _set_up(hass, {"lines": {"clock": [{"source": "entity", "entity": "sensor.power"}]}})
    sent = lambda: json.loads(_published(mqtt_mock)[-1][1])["glance"]["lines"][0]["text"]  # noqa: E731
    assert sent() == "Power 100"
    hass.states.async_set("sensor.power", "101", {"friendly_name": "Power"})
    await hass.async_block_till_done()
    assert sent() == "Power 101", "the first change at once"
    for watts in ("102", "103", "104"):
        hass.states.async_set("sensor.power", watts, {"friendly_name": "Power"})
        await hass.async_block_till_done()
    assert sent() == "Power 101", "the rest held"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=LINES_EVERY + 1))
    await hass.async_block_till_done()
    assert sent() == "Power 104"


async def test_a_glance_nobody_is_shown_needs_no_newer_nest(hass: HomeAssistant, mqtt_mock) -> None:
    from homeassistant.helpers import issue_registry as ir
    from pytest_homeassistant_custom_component.common import async_fire_mqtt_message

    await _set_up(hass, {"layout": "weather"}, far="wake", near="wake")
    async_fire_mqtt_message(hass, f"nestlocal/{SERIAL}/ui/protocol", "10")
    await hass.async_block_till_done()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, f"outdated_{SERIAL}")


async def test_a_template_that_doesn_t_read_leaves_its_line_empty(hass: HomeAssistant, mqtt_mock) -> None:
    hass.states.async_set("sensor.bins", "Recycling")
    await _set_up(hass, {"lines": {"clock": [{"source": "template", "template": "{{ nonsense("},
                                             {"source": "template", "template": "Bins: {{ states('sensor.bins') }}"}]}})
    assert json.loads(_published(mqtt_mock)[-1][1])["glance"]["lines"] == [{"text": "Bins: Recycling"}], \
        "the empty line isn't sent: the next closes up"
    hass.states.async_set("sensor.bins", "Garden")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert json.loads(_published(mqtt_mock)[-1][1])["glance"]["lines"][0] == {"text": "Bins: Garden"}, "a template follows its entities"


async def test_a_line_with_nothing_to_say_shows_something_else_or_closes_up(hass: HomeAssistant):
    hass.states.async_set("media_player.kitchen", "idle", {})
    hass.states.async_set("binary_sensor.door", "off", {"friendly_name": "Front door", "device_class": "door"})
    hass.states.async_set("sensor.price", "31", {"friendly_name": "Price"})
    sources = glance.Sources(hass, False, ["media_player.kitchen"], now=_at(hass, 12))
    line = lambda **spec: sources.line(spec)  # noqa: E731
    assert line(source="playing") == {"text": ""}
    assert line(**{"source": "playing", "else": "whats_on"}) == {"kind": "whats_on"}, "nothing playing: what's on instead"
    assert line(**{"source": "weather", "else": "date"}) == {"kind": "date"}, "no weather chosen: nothing to say either"
    door = {"source": "entity", "entity": "binary_sensor.door", "when": "on", "else": "date_short"}
    assert line(**door) == {"kind": "date_short"}, "the door shut: its line gives way"
    hass.states.async_set("binary_sensor.door", "on", {"friendly_name": "Front door", "device_class": "door"})
    assert line(**door)["text"].startswith("Front door ")
    price = {"source": "entity", "entity": "sensor.price"}
    assert line(**price, when="above", value="30")["text"] == "Price 31"
    assert line(**price, when="below", value="30") == {"text": ""}
    assert line(**price, when="is", value="31")["text"] == "Price 31" and line(**price, when="is_not", value="31") == {"text": ""}
    hass.states.async_set("sensor.price", "unavailable", {"friendly_name": "Price"})
    assert line(**price, when="is_not", value="31") == {"text": ""}, "not reporting: not shown when asked for a state"
    chosen = glance.settings({"lines": {"clock": [{"source": "playing"}, {"source": "date"}, {"source": "rain"}]}})
    assert glance.build(sources, chosen, False, {"offset": 0})["lines"] == [{"kind": "date"}], "empty lines aren't sent"


async def test_the_date_s_words_order_and_the_night_s_level(hass: HomeAssistant):
    words = {"days": ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"],
             "days_short": ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."],
             "months": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
                        "novembre", "décembre"],
             "months_short": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]}
    moment = datetime(2026, 11, 11, 9, 0)
    assert glance.date_text(moment, None, False, False) == "Wednesday 11 November"
    assert glance.date_text(moment, None, True, True) == "Wed, Nov 11"
    assert glance.date_text(moment, words, False, False) == "mercredi 11 novembre"
    hass.config.country = "US"
    assert glance.month_first(hass, "auto") and not glance.month_first(hass, "dmy")
    chosen = glance.settings({"words": words, "date": "mdy", "night": {"mode": "hours", "level": 35}})
    sources = glance.Sources(hass, False, [], words=chosen["words"], month_first=glance.month_first(hass, chosen["date"]))
    night = glance.build(sources, chosen, True, {"offset": 0})
    assert (night["words"], night["date_order"], night["dim"], night["dim_level"]) == (words, "mdy", True, 0.35)
    assert glance.settings({"words": {"days": ["x"]}}) == glance.settings({}), "half the words: none"


async def test_up_next_is_the_next_event_not_yet_over(hass: HomeAssistant):
    now = _at(hass, 13)
    tz = dt_util.get_default_time_zone()
    events = glance.parse_events([
        {"start": "2026-11-11", "end": "2026-11-12", "summary": "Bin day"},
        {"start": datetime(2026, 11, 11, 12, 0, tzinfo=tz).isoformat(), "end": datetime(2026, 11, 11, 14, 0, tzinfo=tz).isoformat(), "summary": "Lunch"},
        {"start": datetime(2026, 11, 11, 16, 30, tzinfo=tz).isoformat(), "end": datetime(2026, 11, 11, 17, 0, tzinfo=tz).isoformat(), "summary": "Dentist"},
        {"start": "2026-11-12", "end": "2026-11-13", "summary": "Birthday"},
    ])
    title = lambda at, skip=False: (glance.next_event(events, at, skip) or {}).get("title")  # noqa: E731
    assert title(now) == "Dentist", "lunch began an hour ago: the one after it, which the calendar entity alone would hide"
    assert title(_at(hass, 12, 5)) == "Lunch", "just begun: still it"
    assert title(_at(hass, 17, 30)) == "Bin day", "nothing more today but the all-day one"
    assert title(_at(hass, 17, 30), True) is None and title(now, True) == "Dentist", "all-day ones left out"
    sources = glance.Sources(hass, False, [], now=now, events=events)
    assert sources.line({"source": "calendar"}) == {"text": "16:30 Dentist", "icon": "mdi:calendar"}
    assert sources.event() == {"title": "Dentist", "start": int(datetime(2026, 11, 11, 16, 30, tzinfo=tz).timestamp()), "all_day": False}


async def test_the_glance_asks_its_calendars_for_their_events(hass: HomeAssistant, mqtt_mock) -> None:
    from homeassistant.core import SupportsResponse

    start = dt_util.now() + timedelta(hours=3)
    asked = []

    async def get_events(call):
        asked.append(call.data["entity_id"])
        entity = call.data["entity_id"]
        return {entity: {"events": [{"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
                                     "summary": f"From {entity}"}]}}

    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    hass.states.async_set("calendar.home", "off", {})
    hass.states.async_set("calendar.work", "off", {})
    await _set_up(hass, {"layout": "agenda", "calendars": ["calendar.home", "calendar.work"]})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert sorted(set(asked)) == ["calendar.home", "calendar.work"]
    sent = json.loads(_published(mqtt_mock)[-1][1])["glance"]
    assert sent["event"]["title"] == "From calendar.home" and sent["event"]["start"] == int(start.timestamp())
