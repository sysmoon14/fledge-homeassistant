"""The glance: what the Nest shows someone across the room (protocol 11).

A layout (the time, the room's temperature, the weather, a clock face, one
sensor's value, or the next event) and lines under it. Some lines the Nest
works out itself (the date, the time, its own temperature, what's on): those
go as a kind. The rest are worked out here and go as words with an icon, so
a new kind of line never needs a thermostat update.

At night (by the sun, set times or a sensor such as a bedtime switch) the
Nest gets the night's glance instead, which has its own layout and lines and
can be drawn dimmer and warmer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.template import Template
from homeassistant.helpers.translation import async_translate_state
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# The layouts, and how many lines each has room for (nestlocal's
# ui::model::Layout::slots).
SLOTS = {"clock": 3, "temp": 2, "weather": 2, "analog": 1, "number": 2, "agenda": 1, "battery": 1}
LAYOUTS = list(SLOTS)
# Lines the Nest works out itself, sent as their kind.
NEST_KINDS = ("date", "date_short", "time", "inside", "heating", "whats_on", "weather")
# Lines worked out here, sent as words.
HA_SOURCES = ("forecast", "rain", "sun", "playing", "calendar", "people", "entity", "template")
SOURCES = ("none", *NEST_KINDS, *HA_SOURCES)
# Each layout's lines until chosen: the clock's are the glance as it was.
DEFAULT_LINES = {
    "clock": ["date", "weather", "whats_on"],
    "temp": ["heating", "weather"],
    "weather": ["forecast", "rain"],
    "analog": ["date"],
    "number": ["time", "none"],
    "agenda": ["weather"],
    "battery": ["none"],
}
NIGHT_MODES = ("off", "sun", "hours", "sensor")
# Countries whose clocks read 12 hours, for Automatic.
TWELVE_HOUR = {"US", "CA", "AU", "NZ", "IN", "PH", "PK", "BD", "EG", "SA", "MY", "CO", "JO"}
# Weather conditions that are rain (or worse).
WET = {"rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail"}
# How far ahead "rain from" looks.
RAIN_AHEAD = timedelta(hours=12)
TEMPLATE_LENGTH = 255
TEXT_LENGTH = 80

# What a line can show instead while it has nothing to say: the sources
# that need nothing more chosen.
FALLBACKS = ("none", *NEST_KINDS, "forecast", "rain", "sun", "playing", "calendar", "people")
# When an entity's line shows: always, or only while its state is on, off,
# a given state (or not), or a number above or below a given one.
WHENS = ("always", "on", "off", "is", "is_not", "above", "below")
# Countries that write the month before the day, for Automatic.
MONTH_FIRST = {"US", "PH", "BZ", "FM", "PW"}
# How far ahead the next event is looked for, and a started one still counts.
EVENTS_AHEAD = timedelta(days=8)
EVENT_STARTED = timedelta(minutes=10)

LINE = vol.Schema({
    vol.Required("source"): vol.In(SOURCES),
    vol.Optional("entity"): vol.Any(None, cv.entity_id),
    vol.Optional("label"): vol.Any(None, vol.All(str, vol.Length(max=40))),
    vol.Optional("template"): vol.Any(None, vol.All(str, vol.Length(max=TEMPLATE_LENGTH))),
    vol.Optional("when"): vol.In(WHENS),
    vol.Optional("value"): vol.Any(None, vol.All(str, vol.Length(max=40))),
    vol.Optional("else"): vol.In(FALLBACKS),
})
# The battery face: the entity with its level in percent, and optionally
# whether it's charging and plugged in, the power going in or out, and its
# charge limit; under what it counts as low and very low; its picture (an
# icon; the level's own without) and name; the line under its status
# while it charges (its layout's line shows otherwise).
BATTERY = vol.Schema({
    vol.Optional("level"): vol.Any(None, cv.entity_id),
    vol.Optional("charging"): vol.Any(None, cv.entity_id),
    vol.Optional("plugged"): vol.Any(None, cv.entity_id),
    vol.Optional("power"): vol.Any(None, cv.entity_id),
    vol.Optional("limit"): vol.Any(None, cv.entity_id),
    vol.Optional("low", default=20): vol.All(int, vol.Range(min=0, max=100)),
    vol.Optional("critical", default=10): vol.All(int, vol.Range(min=0, max=100)),
    vol.Optional("icon"): vol.Any(None, vol.All(str, vol.Length(max=60))),
    vol.Optional("label"): vol.Any(None, vol.All(str, vol.Length(max=40))),
    vol.Optional("charging_line"): vol.Any(None, LINE),
})
# States that say a battery is charging, or plugged in.
CHARGING = ("on", "charging", "true")
PLUGGED = ("on", "plugged", "plugged_in", "connected", "true", "home")
NAME = vol.All(str, vol.Length(min=1, max=24))
# The date's words in another language, as the panel (the browser) names
# them: days from Sunday, months from January.
WORDS = vol.Schema({
    vol.Required("days"): vol.All([NAME], vol.Length(min=7, max=7)),
    vol.Required("days_short"): vol.All([NAME], vol.Length(min=7, max=7)),
    vol.Required("months"): vol.All([NAME], vol.Length(min=12, max=12)),
    vol.Required("months_short"): vol.All([NAME], vol.Length(min=12, max=12)),
})
LINES = {vol.In(LAYOUTS): [LINE]}
TIME = vol.Match(r"^([01]\d|2[0-3]):[0-5]\d$")
SCHEMA = vol.Schema({
    vol.Optional("layout", default="clock"): vol.In(LAYOUTS),
    vol.Optional("hours", default="auto"): vol.In(["auto", "12", "24"]),
    vol.Optional("ampm", default=True): bool,
    vol.Optional("lines", default={}): LINES,
    vol.Optional("big"): vol.Any(None, cv.entity_id),
    vol.Optional("big_label"): vol.Any(None, vol.All(str, vol.Length(max=40))),
    vol.Optional("battery"): vol.Any(None, BATTERY),
    vol.Optional("calendar"): vol.Any(None, cv.entity_domain("calendar")),
    # Up next and the next event's line look through all of these (with
    # none, `calendar`, from before there could be several).
    vol.Optional("calendars"): vol.All([cv.entity_domain("calendar")], vol.Length(max=6)),
    vol.Optional("skip_all_day", default=False): bool,
    vol.Optional("colour", default=True): bool,
    # The date: the month first or the day (Automatic: from where Home
    # Assistant is), and its words where not English.
    vol.Optional("date", default="auto"): vol.In(["auto", "dmy", "mdy"]),
    vol.Optional("words"): vol.Any(None, WORDS),
    vol.Optional("language"): vol.Any(None, vol.All(str, vol.Length(max=20))),
    vol.Optional("night", default={}): vol.Schema({
        vol.Optional("mode", default="off"): vol.In(NIGHT_MODES),
        vol.Optional("after", default=30): vol.All(int, vol.Range(min=-180, max=180)),
        vol.Optional("before", default=0): vol.All(int, vol.Range(min=-180, max=180)),
        vol.Optional("from", default="22:00"): TIME,
        vol.Optional("until", default="07:00"): TIME,
        vol.Optional("entity"): vol.Any(None, cv.entity_id),
        vol.Optional("state", default="on"): vol.In(["on", "off"]),
        vol.Optional("layout", default="analog"): vol.In(LAYOUTS),
        vol.Optional("dim", default=True): bool,
        # How bright the dimmed glance stays, in percent.
        vol.Optional("level", default=60): vol.All(int, vol.Range(min=20, max=100)),
        vol.Optional("lines", default={}): LINES,
    }),
})


def settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """An entry's glance settings with every default filled in."""
    try:
        return SCHEMA(dict(raw or {}))
    except vol.Invalid:
        _LOGGER.warning("Fledge: the glance's settings don't read, using the defaults")
        return SCHEMA({})


def lines_for(chosen: dict[str, list[dict]], layout: str, night: bool = False) -> list[dict]:
    """A layout's lines, as many as it has room for: as chosen, else its
    defaults (the night's, only the first)."""
    lines = chosen.get(layout)
    if lines is None:
        defaults = DEFAULT_LINES[layout]
        lines = [{"source": s if i == 0 or not night else "none"} for i, s in enumerate(defaults)]
    lines = list(lines)[:SLOTS[layout]]
    return lines + [{"source": "none"}] * (SLOTS[layout] - len(lines))


def twelve_hour(hass: HomeAssistant, hours: str) -> bool:
    """Whether the clock reads 12 hours: as chosen, or Automatic from where
    Home Assistant is."""
    if hours in ("12", "24"):
        return hours == "12"
    country = (hass.config.country or "").upper()
    if country:
        return country in TWELVE_HOUR
    return (hass.config.language or "").lower() in ("en-us", "en-ca", "en-au")


def time_text(moment: datetime, twelve: bool) -> str:
    """"16:30", or "4:30 PM"."""
    if not twelve:
        return moment.strftime("%H:%M")
    return f"{(moment.hour + 11) % 12 + 1}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def date_text(moment: datetime, words: dict[str, list[str]] | None, month_first: bool, short: bool) -> str:
    """The date as the Nest says it (screens::glance_date): "Sunday 8
    November", "Sun 8 Nov"; the month first, "Sunday, November 8"."""
    weekday, month = (moment.weekday() + 1) % 7, moment.month - 1
    if words:
        day_name, month_name = (words["days_short"][weekday], words["months_short"][month]) if short \
            else (words["days"][weekday], words["months"][month])
    else:
        day_name, month_name = (DAYS[weekday][:3], MONTHS[month][:3]) if short else (DAYS[weekday], MONTHS[month])
    return f"{day_name}, {month_name} {moment.day}" if month_first else f"{day_name} {moment.day} {month_name}"


def month_first(hass: HomeAssistant, order: str) -> bool:
    """Whether the date reads November 8: as chosen, or Automatic from
    where Home Assistant is."""
    if order in ("dmy", "mdy"):
        return order == "mdy"
    return (hass.config.country or "").upper() in MONTH_FIRST


def calendars(chosen: dict[str, Any]) -> list[str]:
    """The calendars the next event comes from."""
    return list(chosen.get("calendars") or ([chosen["calendar"]] if chosen.get("calendar") else []))


def next_event(events: list[dict[str, Any]], now: datetime, skip_all_day: bool) -> dict[str, Any] | None:
    """What's up next among `events` ({title, start, end, all_day}, times
    aware): the first not yet over that started no more than ten minutes
    ago; today's all-day events after today's others."""
    midnight = dt_util.start_of_local_day(now) + timedelta(days=1)
    best, best_key = None, None
    for event in events:
        if event["end"] <= now:
            continue
        if event["all_day"]:
            if skip_all_day:
                continue
            key = event["start"] if event["start"] > now else midnight - timedelta(seconds=1)
        elif event["start"] < now - EVENT_STARTED:
            continue
        else:
            key = event["start"]
        if best_key is None or key < best_key:
            best, best_key = event, key
    return best


def parse_events(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A calendar's events (calendar.get_events) as next_event takes them."""
    events = []
    for event in found:
        start_raw, end_raw = str(event.get("start") or ""), str(event.get("end") or "")
        all_day = len(start_raw) == 10
        start, end = _local(start_raw), _local(end_raw)
        if start is None or not event.get("summary"):
            continue
        events.append({"title": str(event["summary"]), "start": start, "end": end or start + timedelta(hours=1),
                       "all_day": all_day})
    return events


def degrees(value: Any) -> str | None:
    """A temperature as the Nest shows it: 12°."""
    try:
        return f"{round(float(value))}°"
    except (TypeError, ValueError):
        return None


# Night.


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def is_night(hass: HomeAssistant, night: dict[str, Any], now: datetime | None = None) -> bool:
    """Whether it's night as the settings say: between sunset and sunrise
    (shifted), between two times (across midnight too), or while a sensor
    is on (or off). A sensor that's unavailable leaves it day."""
    mode = night.get("mode", "off")
    now = dt_util.as_local(now or dt_util.now())
    if mode == "hours":
        start, end, at = _minutes(night["from"]), _minutes(night["until"]), now.hour * 60 + now.minute
        if start == end:
            return False
        return start <= at < end if start < end else at >= start or at < end
    if mode == "sensor":
        state = hass.states.get(night.get("entity") or "")
        return state is not None and state.state == night.get("state", "on")
    if mode == "sun":
        today = now.date()
        sunset = get_astral_event_date(hass, "sunset", today)
        sunrise = get_astral_event_date(hass, "sunrise", today)
        if sunset is None or sunrise is None:
            sun = hass.states.get("sun.sun")
            return sun is not None and sun.state == "below_horizon"
        dark = sunset + timedelta(minutes=night.get("after", 0))
        light = sunrise - timedelta(minutes=night.get("before", 0))
        return now >= dark or now < light
    return False


# Lines worked out here.


def _local(value: Any) -> datetime | None:
    """A time from a state's attribute: aware, or Home Assistant's local."""
    if isinstance(value, datetime):
        moment = value
    else:
        moment = dt_util.parse_datetime(str(value)) if value else None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_local(moment)


def _day(moment: datetime, now: datetime) -> str:
    """"" today, else "Tomorrow" or the weekday's short name."""
    days = (moment.date() - now.date()).days
    if days <= 0:
        return ""
    return "Tomorrow" if days == 1 else moment.strftime("%a")


def number(hass: HomeAssistant, state: State) -> str:
    """A sensor's value as Home Assistant shows it: its display precision,
    else no more than it needs."""
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        if state.state in ("unknown", "unavailable"):
            return "–"
        return said_state(hass, state)
    entry = er.async_get(hass).async_get(state.entity_id)
    precision = None
    if entry is not None:
        sensor = entry.options.get("sensor") or {}
        precision = sensor.get("display_precision", sensor.get("suggested_display_precision"))
    if precision is None:
        precision = 0 if abs(value) >= 100 else 1 if abs(value) >= 10 else 2
        text = f"{value:.{precision}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text
    return f"{value:.{int(precision)}f}"


def said_state(hass: HomeAssistant, state: State) -> str:
    """A state in words as Home Assistant's own pages say it: a door sensor's
    "on" is Open, a person's "not_home" is Away."""
    try:
        entry = er.async_get(hass).async_get(state.entity_id)
        said = async_translate_state(hass, state.state, state.domain, entry.platform if entry else None,
                                     entry.translation_key if entry else None, state.attributes.get("device_class"))
    except Exception:  # noqa: BLE001 - the state as it is, tidied
        said = state.state
    return state.state.replace("_", " ").capitalize() if said == state.state else said


# An icon for a sensor without its own, by device class.
CLASS_ICONS = {
    "battery": "mdi:battery", "temperature": "mdi:thermometer", "humidity": "mdi:water-percent",
    "power": "mdi:flash", "energy": "mdi:lightning-bolt", "carbon_dioxide": "mdi:molecule-co2",
    "gas": "mdi:fire", "occupancy": "mdi:account", "presence": "mdi:account", "door": "mdi:door", "window": "mdi:window-closed",
}


# Home Assistant's weather conditions in words, and their icons (as the Nest
# names and draws them: nestlocal's screens.rs).
CONDITIONS = {"partlycloudy": "Partly cloudy", "clear-night": "Clear", "snowy-rainy": "Sleet",
              "lightning-rainy": "Thunderstorms", "windy-variant": "Windy", "exceptional": ""}
WEATHER_ICONS = {"sunny": "weather-sunny", "clear-night": "weather-night", "partlycloudy": "weather-partly-cloudy",
                 "rainy": "weather-rainy", "pouring": "weather-pouring", "snowy": "weather-snowy",
                 "snowy-rainy": "weather-snowy", "fog": "weather-fog", "windy": "weather-windy",
                 "windy-variant": "weather-windy", "lightning": "weather-lightning",
                 "lightning-rainy": "weather-lightning", "hail": "weather-hail"}


def whats_on(hass: HomeAssistant, entities: list[str]) -> str:
    """What's on among the cards, as the Nest says it (screens::whats_on)."""
    kinds = {"light": ["light", "lights", 0], "switch": ["switch", "switches", 0], "fan": ["fan", "fans", 0]}
    playing = None
    for entity in entities:
        state = hass.states.get(entity)
        if state is None:
            continue
        domain = entity.partition(".")[0]
        if domain in ("light", "switch", "input_boolean", "fan") and state.state == "on":
            kinds["light" if domain == "light" else "fan" if domain == "fan" else "switch"][2] += 1
        elif domain == "media_player" and state.state == "playing" and playing is None:
            playing = state.attributes.get("media_title") or state.attributes.get("friendly_name")
    parts = [f"{n} {one if n == 1 else many} on" for one, many, n in kinds.values() if n]
    if playing:
        parts.append(f"playing {playing}")
    return " · ".join(parts) or "All quiet"


class Sources:
    """What lines are worked out from: Home Assistant now, the weather's
    forecasts (kept by Feeds), and whether the clock reads 12 hours."""

    def __init__(self, hass: HomeAssistant, twelve: bool, cards: list[str], hourly: list[dict] | None = None,
                 daily: list[dict] | None = None, weather: str | None = None, calendar: str | None = None,
                 now: datetime | None = None, events: list[dict] | None = None, skip_all_day: bool = False,
                 words: dict[str, list[str]] | None = None, month_first: bool = False) -> None:
        # The calendars' coming events (Thermostat keeps them); None: only
        # what the calendar entity itself says.
        self.events = events
        self.skip_all_day = skip_all_day
        self.words = words
        self.month_first = month_first
        self.hass = hass
        self.twelve = twelve
        self.cards = cards
        self.hourly = hourly or []
        self.daily = daily or []
        self.weather = weather
        self.calendar = calendar
        self.now = dt_util.as_local(now or dt_util.now())

    def line(self, spec: dict[str, Any]) -> dict[str, Any]:
        """One line as the Nest takes it: a kind, or words and an icon;
        with nothing to say, what it shows instead (`else`), if anything
        (empty words: nothing)."""
        out = self._line(spec)
        if out == {"text": ""} and spec.get("else") not in (None, "none"):
            out = self._line({"source": spec["else"]})
        return out

    def _line(self, spec: dict[str, Any]) -> dict[str, Any]:
        source = spec.get("source", "none")
        if source == "weather":
            # The Nest's own, but with no weather it has nothing to say.
            weather = self.hass.states.get(self.weather) if self.weather else None
            return {"kind": source} if weather is not None and weather.state not in ("unavailable", "unknown") else {"text": ""}
        if source in NEST_KINDS:
            return {"kind": source}
        try:
            said = getattr(self, f"_{source}")(spec) if source in HA_SOURCES else None
        except Exception:  # noqa: BLE001 - a line that can't be worked out stays empty
            _LOGGER.debug("Fledge: glance line %s", source, exc_info=True)
            said = None
        if not said:
            return {"text": ""}
        text, icon = said
        out = {"text": text[:TEXT_LENGTH]}
        if icon:
            out["icon"] = icon
        return out

    def preview(self, spec: dict[str, Any], climate: State | None) -> dict[str, Any]:
        """A line as the panel's preview draws it: the Nest's own kinds
        worked out here too, from the thermostat's climate entity."""
        out = self._preview(spec, climate)
        if not out.get("text") and spec.get("else") not in (None, "none"):
            out = {**self._preview({"source": spec["else"]}, climate), "instead": True}
        return out

    def _preview(self, spec: dict[str, Any], climate: State | None) -> dict[str, Any]:
        out = self._line(spec)
        kind = out.get("kind")
        if kind is None:
            return out
        weather = self.hass.states.get(self.weather) if self.weather else None
        attrs = climate.attributes if climate is not None else {}
        text, icon = "", None
        if kind in ("date", "date_short"):
            text = date_text(self.now, self.words, self.month_first, kind == "date_short")
        elif kind == "time":
            text, icon = time_text(self.now, self.twelve), "mdi:clock-outline"
        elif kind == "inside" and attrs.get("current_temperature") is not None:
            humidity = attrs.get("current_humidity")
            text = f"Inside {attrs['current_temperature']}°" + (f" · {round(float(humidity))}%" if humidity is not None else "")
            icon = "mdi:thermometer"
        elif kind == "heating" and climate is not None:
            to = attrs.get("temperature")
            action = attrs.get("hvac_action")
            if action in ("heating", "preheating"):
                text, icon = f"Heating to {to}°", "mdi:fire"
            elif action == "cooling":
                text, icon = f"Cooling to {to}°", "mdi:snowflake"
            elif climate.state == "off":
                text, icon = "Heating off", "mdi:thermostat"
            else:
                text, icon = f"Set to {to}°", "mdi:thermostat"
        elif kind == "weather" and weather is not None and weather.state not in ("unavailable", "unknown"):
            t = degrees(weather.attributes.get("temperature"))
            condition = CONDITIONS.get(weather.state, weather.state.replace("-", " ").capitalize())
            text, icon = " · ".join(p for p in (t, condition) if p), f"mdi:{WEATHER_ICONS.get(weather.state, 'weather-cloudy')}"
        elif kind == "whats_on":
            text = whats_on(self.hass, self.cards)
        return {"kind": kind, "text": text, **({"icon": icon} if icon else {})}

    def _forecast(self, _spec: dict) -> tuple[str, str] | None:
        today = next((f for f in self.daily if (_local(f.get("datetime")) or self.now).date() == self.now.date()), None)
        today = today or (self.daily[0] if self.daily else None)
        if today is None:
            return None
        high, low = degrees(today.get("temperature")), degrees(today.get("templow"))
        parts = [f"High {high}" if high else "", f"Low {low}" if low else ""]
        return " · ".join(p for p in parts if p), "mdi:thermometer"

    def _rain(self, _spec: dict) -> tuple[str, str] | None:
        if not self.hourly:
            return None
        weather = self.hass.states.get(self.weather) if self.weather else None

        def wet(f: dict) -> bool:
            return f.get("condition") in WET or (f.get("precipitation_probability") or 0) >= 50

        ahead = [(at, f) for f in self.hourly if (at := _local(f.get("datetime"))) and self.now - timedelta(hours=1) < at <= self.now + RAIN_AHEAD]
        raining = (weather is not None and weather.state in WET) or (bool(ahead) and wet(ahead[0][1]))
        if raining:
            dry = next((at for at, f in ahead if at > self.now and not wet(f)), None)
            return (f"Rain until {time_text(dry, self.twelve)}" if dry else "Rain all day"), "mdi:weather-rainy"
        start = next((at for at, f in ahead if at > self.now and wet(f)), None)
        if start is None:
            return "No rain expected", "mdi:weather-partly-cloudy"
        return f"Rain from {time_text(start, self.twelve)}", "mdi:weather-rainy"

    def _sun(self, _spec: dict) -> tuple[str, str] | None:
        sun = self.hass.states.get("sun.sun")
        if sun is None:
            return None
        up = sun.state == "above_horizon"
        moment = _local(sun.attributes.get("next_setting" if up else "next_rising"))
        if moment is None:
            return None
        day = _day(moment, self.now)
        when = f"{day} {time_text(moment, self.twelve)}".strip() if day and day != "Tomorrow" else time_text(moment, self.twelve)
        return (f"Sunset {when}", "mdi:weather-sunset-down") if up else (f"Sunrise {when}", "mdi:weather-sunset-up")

    def _playing(self, spec: dict) -> tuple[str, str] | None:
        """What the chosen speaker plays; with none chosen, the first of the
        cards' speakers that's playing."""
        if spec.get("entity"):
            players = [self.hass.states.get(spec["entity"])]
        else:
            players = [self.hass.states.get(e) for e in self.cards if e.startswith("media_player.")]
        playing = next((s for s in players if s is not None and s.state == "playing"), None)
        if playing is None:
            return None
        title, artist = playing.attributes.get("media_title"), playing.attributes.get("media_artist")
        text = " · ".join(str(p) for p in (title, artist) if p) or playing.attributes.get("friendly_name") or playing.entity_id
        return text, "mdi:play"

    def event(self) -> dict[str, Any] | None:
        """The chosen calendar's next (or current) event, as the Nest's Up
        next takes it."""
        if self.events is not None:
            event = next_event(self.events, self.now, self.skip_all_day)
            if event is None:
                return None
            return {"title": event["title"][:TEXT_LENGTH], "start": int(event["start"].timestamp()), "all_day": event["all_day"]}
        # Without its events (a calendar that can't list them): what the
        # calendar entity says, its current or next one.
        state = self.hass.states.get(self.calendar) if self.calendar else None
        if state is None or not state.attributes.get("message"):
            return None
        start = _local(state.attributes.get("start_time"))
        if start is None or (self.skip_all_day and state.attributes.get("all_day")):
            return None
        return {"title": str(state.attributes["message"])[:TEXT_LENGTH], "start": int(start.timestamp()),
                "all_day": bool(state.attributes.get("all_day"))}

    def _calendar(self, _spec: dict) -> tuple[str, str] | None:
        event = self.event()
        if event is None:
            return None
        start = dt_util.as_local(dt_util.utc_from_timestamp(event["start"]))
        day = _day(start, self.now)
        if event["all_day"]:
            return f"{day or 'Today'}: {event['title']}", "mdi:calendar"
        when = f"{day} {time_text(start, self.twelve)}".strip()
        return f"{when} {event['title']}", "mdi:calendar"

    def _people(self, _spec: dict) -> tuple[str, str] | None:
        people = self.hass.states.async_all("person")
        if not people:
            return None
        # First names: full ones take the line's room.
        home = [str(p.attributes.get("friendly_name") or p.entity_id).split()[0] for p in people if p.state == "home"]
        if not home:
            return "Nobody home", "mdi:home-export-outline"
        if len(home) <= 2:
            return f"{' and '.join(home)} home", "mdi:account" if len(home) == 1 else "mdi:account-multiple"
        return f"{len(home)} people home", "mdi:account-multiple"

    def _entity(self, spec: dict) -> tuple[str, str | None] | None:
        state = self.hass.states.get(spec.get("entity") or "")
        if state is None or not self._shows(state, spec):
            return None
        label = (spec.get("label") or "").strip() or state.attributes.get("friendly_name") or state.entity_id
        unit = state.attributes.get("unit_of_measurement") or ""
        device_class = state.attributes.get("device_class") or ""
        moment = _local(state.state) if device_class == "timestamp" else None
        if moment is not None:
            # A time (the next alarm, when the washing ends): as a clock reads it.
            value = f"{_day(moment, self.now)} {time_text(moment, self.twelve)}".strip()
        else:
            value = number(self.hass, state)
        text = f"{label} {value}{'' if unit in ('%', '°C', '°F', '°') else ' '}{unit}".strip()
        icon = state.attributes.get("icon") or CLASS_ICONS.get(device_class)
        if icon is None and device_class == "monetary":
            icon = next((f"mdi:currency-{code}" for code, signs in (("gbp", "£GBP"), ("usd", "$USD"), ("eur", "€EUR"))
                         if any(sign in unit for sign in (signs[0], signs[1:]))), None)
        return text, icon

    def _shows(self, state: State, spec: dict) -> bool:
        """Whether an entity's line shows now (`when`): always, or only
        while its state is as asked. Never while it isn't reporting, if asked."""
        when = spec.get("when") or "always"
        if when == "always":
            return True
        if state.state in ("unknown", "unavailable"):
            return False
        value = (spec.get("value") or "").strip().lower()
        if when in ("on", "off"):
            return state.state == when
        if when in ("is", "is_not"):
            same = value in (state.state.lower(), said_state(self.hass, state).lower())
            return same if when == "is" else not same
        try:
            reading, limit = float(state.state), float(value)
        except ValueError:
            return False
        return reading > limit if when == "above" else reading < limit

    def _template(self, spec: dict) -> tuple[str, None] | None:
        source = (spec.get("template") or "").strip()
        if not source:
            return None
        text = Template(source, self.hass).async_render(parse_result=False)
        return " ".join(str(text).split()), None

    def big(self, entity: str | None, label: str | None) -> dict[str, str] | None:
        """The big number's: what it is, its value and unit."""
        state = self.hass.states.get(entity or "")
        if state is None:
            return None
        name = (label or "").strip() or state.attributes.get("friendly_name") or state.entity_id
        return {"label": str(name)[:40], "value": number(self.hass, state),
                "unit": str(state.attributes.get("unit_of_measurement") or "")}


def _reading(state: State | None) -> float | None:
    try:
        return float(state.state) if state is not None else None
    except (TypeError, ValueError):
        return None


def battery(hass: HomeAssistant, spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """The battery face's: its level, what it's doing (charging, plugged
    in, discharging, idle) in a word and in a line, its charge limit, and
    its picture and name. Without a charging entity, power going in counts
    as charging."""
    spec = spec or {}
    get = lambda key: hass.states.get(spec.get(key) or "")  # noqa: E731
    level_state = get("level")
    if level_state is None:
        return None
    level = _reading(level_state)
    low, critical = spec.get("low", 20), spec.get("critical", 10)
    power_state = get("power")
    power = _reading(power_state)
    flow = ""
    if power_state is not None and power:
        shown = number(hass, power_state).lstrip("-−")
        flow = f"{shown} {power_state.attributes.get('unit_of_measurement') or ''}".strip()
    charging_state, plugged_state = get("charging"), get("plugged")
    if charging_state is not None:
        charging = charging_state.state.lower() in CHARGING
    else:
        charging = bool(power and power > 0)
    plugged = plugged_state is not None and plugged_state.state.lower() in PLUGGED
    if charging:
        status, text = "charging", " · ".join(filter(None, ("Charging", flow)))
    elif plugged:
        status, text = "plugged", "Plugged in"
    elif flow:
        status, text = "discharging", f"Discharging · {flow}"
    elif level is not None and level <= critical:
        status, text = "idle", "Battery very low"
    elif level is not None and level <= low:
        status, text = "idle", "Battery low"
    else:
        status, text = "idle", ""
    name = (spec.get("label") or "").strip() or level_state.attributes.get("friendly_name") or level_state.entity_id
    return {"label": str(name)[:40], "level": None if level is None else max(0.0, min(100.0, level)), "status": status,
            "text": text, "limit": _reading(get("limit")), "low": low, "critical": critical,
            "icon": spec.get("icon") or level_state.attributes.get("icon") or "mdi:battery"}


def build(sources: Sources, chosen: dict[str, Any], night: bool, base: dict[str, Any]) -> dict[str, Any]:
    """The glance as the Nest takes it: `base` (its offset and weather)
    with the day's or the night's layout and lines."""
    nights = chosen["night"]
    layout = nights["layout"] if night else chosen["layout"]
    lines = lines_for(nights["lines"] if night else chosen["lines"], layout, night)
    out = dict(base)
    out["layout"] = layout
    out["clock"] = {"hours": 12 if sources.twelve else 24, "ampm": chosen["ampm"]}
    # A line with nothing to say isn't sent: those after it close up.
    out["lines"] = [said for said in (sources.line(spec) for spec in lines) if said != {"text": ""}]
    if sources.words:
        out["words"] = sources.words
    if sources.month_first:
        out["date_order"] = "mdy"
    if layout == "number":
        big = sources.big(chosen.get("big"), chosen.get("big_label"))
        if big:
            out["big"] = big
    if layout == "battery":
        shown = battery(sources.hass, chosen.get("battery"))
        if shown:
            out["battery"] = shown
            other = (chosen.get("battery") or {}).get("charging_line")
            if shown["status"] == "charging" and other:
                # While it charges: its own line (when it'll be full).
                out["lines"] = [said for said in (sources.line(other),) if said != {"text": ""}]
    if layout == "agenda":
        event = sources.event()
        if event:
            out["event"] = event
    if layout == "temp":
        out["colour"] = chosen["colour"]
    if night:
        out["night"] = True
        out["dim"] = nights["dim"]
        if nights["dim"]:
            out["dim_level"] = nights["level"] / 100
    return out


def watched(chosen: dict[str, Any]) -> list[str]:
    """The entities the glance's words come from: its lines' (a sensor, a
    speaker), the big number's and the calendar. They may change often."""
    entities: set[str] = set()
    for lines in (*chosen["lines"].values(), *chosen["night"]["lines"].values()):
        for spec in lines:
            if spec.get("source") in ("entity", "playing") and spec.get("entity"):
                entities.add(spec["entity"])
    if chosen.get("big"):
        entities.add(chosen["big"])
    cell = chosen.get("battery") or {}
    entities.update(cell[key] for key in ("level", "charging", "plugged", "power", "limit") if cell.get(key))
    other = cell.get("charging_line") or {}
    if other.get("source") in ("entity", "playing") and other.get("entity"):
        entities.add(other["entity"])
    return sorted(entities)


def wants_events(chosen: dict[str, Any]) -> bool:
    """Whether the glance, day or night, shows the next event."""
    night = chosen["night"]
    return bool(calendars(chosen)) and (chosen["layout"] == "agenda" or (night["mode"] != "off" and night["layout"] == "agenda")
                                        or uses(chosen, "calendar"))


def night_sensor(chosen: dict[str, Any]) -> str | None:
    """The sensor that says when it's night, if one does."""
    night = chosen["night"]
    return night.get("entity") if night.get("mode") == "sensor" else None


def templates(chosen: dict[str, Any]) -> list[str]:
    """The glance's template lines, day and night."""
    found = []
    for lines in (*chosen["lines"].values(), *chosen["night"]["lines"].values()):
        found += [spec["template"] for spec in lines if spec.get("source") == "template" and spec.get("template")]
    return found


def uses(chosen: dict[str, Any], source: str) -> bool:
    """Whether any of the glance's lines, day or night, come from `source`."""
    return any(source in (spec.get("source"), spec.get("else"))
               for lines in (*chosen["lines"].values(), *chosen["night"]["lines"].values()) for spec in lines)


def customised(raw: dict[str, Any] | None) -> bool:
    """Whether the glance is anything but the glance as it was (needing
    protocol 11 to show)."""
    return bool(raw) and settings(raw) != settings({})

