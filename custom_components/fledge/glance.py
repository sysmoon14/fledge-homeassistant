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
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# The layouts, and how many lines each has room for (nestlocal's
# ui::model::Layout::slots).
SLOTS = {"clock": 3, "temp": 2, "weather": 2, "analog": 1, "number": 2, "agenda": 1}
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

LINE = vol.Schema({
    vol.Required("source"): vol.In(SOURCES),
    vol.Optional("entity"): vol.Any(None, cv.entity_id),
    vol.Optional("label"): vol.Any(None, vol.All(str, vol.Length(max=40))),
    vol.Optional("template"): vol.Any(None, vol.All(str, vol.Length(max=TEMPLATE_LENGTH))),
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
    vol.Optional("calendar"): vol.Any(None, cv.entity_domain("calendar")),
    vol.Optional("colour", default=True): bool,
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
        return state.state.replace("_", " ").capitalize() if state.state not in ("unknown", "unavailable") else "–"
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


# An icon for a sensor without its own, by device class.
CLASS_ICONS = {
    "battery": "mdi:battery", "temperature": "mdi:thermometer", "humidity": "mdi:water-percent",
    "power": "mdi:flash", "energy": "mdi:lightning-bolt", "monetary": "mdi:currency-gbp", "carbon_dioxide": "mdi:molecule-co2",
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
                 now: datetime | None = None) -> None:
        self.hass = hass
        self.twelve = twelve
        self.cards = cards
        self.hourly = hourly or []
        self.daily = daily or []
        self.weather = weather
        self.calendar = calendar
        self.now = dt_util.as_local(now or dt_util.now())

    def line(self, spec: dict[str, Any]) -> dict[str, Any]:
        """One line as the Nest takes it: a kind, or words and an icon
        (empty words: the line left empty, keeping its place)."""
        source = spec.get("source", "none")
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
        out = self.line(spec)
        kind = out.get("kind")
        if kind is None:
            return out
        weather = self.hass.states.get(self.weather) if self.weather else None
        attrs = climate.attributes if climate is not None else {}
        text, icon = "", None
        if kind == "date":
            text = f"{self.now.strftime('%A')} {self.now.day} {self.now.strftime('%B')}"
        elif kind == "date_short":
            text = f"{self.now.strftime('%a')} {self.now.day} {self.now.strftime('%b')}"
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

    def _playing(self, _spec: dict) -> tuple[str, str] | None:
        players = [self.hass.states.get(e) for e in self.cards if e.startswith("media_player.")]
        players += [s for s in self.hass.states.async_all("media_player") if s.entity_id not in self.cards]
        playing = next((s for s in players if s is not None and s.state == "playing"), None)
        if playing is None:
            return None
        title, artist = playing.attributes.get("media_title"), playing.attributes.get("media_artist")
        text = " · ".join(str(p) for p in (title, artist) if p) or playing.attributes.get("friendly_name") or playing.entity_id
        return text, "mdi:play"

    def event(self) -> dict[str, Any] | None:
        """The chosen calendar's next (or current) event, as the Nest's Up
        next takes it."""
        state = self.hass.states.get(self.calendar) if self.calendar else None
        if state is None or not state.attributes.get("message"):
            return None
        start = _local(state.attributes.get("start_time"))
        if start is None:
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
        home = [p.attributes.get("friendly_name") or p.entity_id for p in people if p.state == "home"]
        if not home:
            return "Nobody home", "mdi:home-export-outline"
        if len(home) <= 2:
            return f"{' and '.join(home)} home", "mdi:account" if len(home) == 1 else "mdi:account-multiple"
        return f"{len(home)} people home", "mdi:account-multiple"

    def _entity(self, spec: dict) -> tuple[str, str | None] | None:
        state = self.hass.states.get(spec.get("entity") or "")
        if state is None:
            return None
        label = (spec.get("label") or "").strip() or state.attributes.get("friendly_name") or state.entity_id
        unit = state.attributes.get("unit_of_measurement") or ""
        value = number(self.hass, state)
        text = f"{label} {value}{'' if unit in ('%', '°C', '°F', '°') else ' '}{unit}".strip()
        icon = state.attributes.get("icon") or CLASS_ICONS.get(state.attributes.get("device_class") or "")
        return text, icon

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


def build(sources: Sources, chosen: dict[str, Any], night: bool, base: dict[str, Any]) -> dict[str, Any]:
    """The glance as the Nest takes it: `base` (its offset and weather)
    with the day's or the night's layout and lines."""
    nights = chosen["night"]
    layout = nights["layout"] if night else chosen["layout"]
    lines = lines_for(nights["lines"] if night else chosen["lines"], layout, night)
    out = dict(base)
    out["layout"] = layout
    out["clock"] = {"hours": 12 if sources.twelve else 24, "ampm": chosen["ampm"]}
    sent = [sources.line(spec) for spec in lines]
    while sent and sent[-1] == {"text": ""}:
        sent.pop()
    out["lines"] = sent
    if layout == "number":
        big = sources.big(chosen.get("big"), chosen.get("big_label"))
        if big:
            out["big"] = big
    if layout == "agenda" or any(spec.get("source") == "calendar" for spec in lines):
        event = sources.event()
        if event:
            out["event"] = event
    if layout == "temp":
        out["colour"] = chosen["colour"]
    if night:
        out["night"] = True
        out["dim"] = nights["dim"]
    return out


def watched(chosen: dict[str, Any]) -> list[str]:
    """The entities the glance follows: its lines', the big number's, the
    calendar, the night's sensor, people and media for their lines."""
    entities: set[str] = set()
    for lines in (*chosen["lines"].values(), *chosen["night"]["lines"].values()):
        for spec in lines:
            if spec.get("source") == "entity" and spec.get("entity"):
                entities.add(spec["entity"])
    for key in ("big", "calendar"):
        if chosen.get(key):
            entities.add(chosen[key])
    if chosen["night"].get("mode") == "sensor" and chosen["night"].get("entity"):
        entities.add(chosen["night"]["entity"])
    return sorted(entities)


def templates(chosen: dict[str, Any]) -> list[str]:
    """The glance's template lines, day and night."""
    found = []
    for lines in (*chosen["lines"].values(), *chosen["night"]["lines"].values()):
        found += [spec["template"] for spec in lines if spec.get("source") == "template" and spec.get("template")]
    return found


def uses(chosen: dict[str, Any], source: str) -> bool:
    """Whether any of the glance's lines, day or night, come from `source`."""
    return any(spec.get("source") == source
               for lines in (*chosen["lines"].values(), *chosen["night"]["lines"].values()) for spec in lines)


def customised(raw: dict[str, Any] | None) -> bool:
    """Whether the glance is anything but the glance as it was (needing
    protocol 11 to show)."""
    return bool(raw) and settings(raw) != settings({})

