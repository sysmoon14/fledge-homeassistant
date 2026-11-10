"""The thermostat's cards, and what its commands do: no Home Assistant
imports, so it can be tested on its own.

The cards are what nestlocal reads on <base>/set/ui (crates/nestlocal/src/ui/
model.rs); a command is what it sends on <base>/ui/command:

    {"id": "light.x", "action": "on" | "off" | "brightness" | "color_temp" | "hue"
                                | "temperature" | "volume" | "play_pause", "value": <number>}
    {"id": "alarm_control_panel.x", "action": "arm", "mode": "away", "code": "1234", "force": false}
    {"id": "alarm_control_panel.x", "action": "disarm", "code": "1234"}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .const import CHOICES, RUN, SWITCHED

# Colour modes with colours to choose, beyond a white's warmth.
COLOUR_MODES = ("hs", "rgb", "rgbw", "rgbww", "xy")


class State(Protocol):
    """What cards need of a Home Assistant state."""

    entity_id: str
    state: str
    attributes: Mapping[str, Any]


def _name(state: State) -> str:
    return state.attributes.get("friendly_name") or state.entity_id


def light(state: State) -> dict[str, Any]:
    """One light as the thermostat lists it: dimmable if it supports any
    colour mode beyond on/off; brightness in percent, at least 1 while on;
    its colour Home Assistant's rgb_color, which colour-temperature lights
    have too."""
    on = state.state == "on"
    modes = state.attributes.get("supported_color_modes") or []
    level = state.attributes.get("brightness")
    rgb = state.attributes.get("rgb_color")
    return {
        "id": state.entity_id,
        "name": _name(state),
        "icon": state.attributes.get("icon") or "",
        "on": on,
        "dimmable": state.entity_id.startswith("light.")
        and any(mode not in ("onoff", "unknown") for mode in modes),
        "brightness": max(round(level * 100 / 255), 1) if on and isinstance(level, (int, float)) else None,
        "rgb": list(rgb) if rgb else None,
        **_shades(state, modes),
    }


def _shades(state: State, modes: Sequence[str]) -> dict[str, Any]:
    """A light's white warmth (kelvin, and the range it takes) and colour
    (hue), where it has them, for their dials on the thermostat."""
    out: dict[str, Any] = {}
    attributes = state.attributes
    if "color_temp" in modes:
        low, high = attributes.get("min_color_temp_kelvin"), attributes.get("max_color_temp_kelvin")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low < high:
            out["kelvins"] = [round(low), round(high)]
            kelvin = attributes.get("color_temp_kelvin")
            out["kelvin"] = round(kelvin) if isinstance(kelvin, (int, float)) else None
    if any(mode in COLOUR_MODES for mode in modes):
        out["hues"] = True
        hs = attributes.get("hs_color")
        coloured = attributes.get("color_mode") != "color_temp"
        out["hue"] = round(hs[0]) % 360 if coloured and hs and isinstance(hs[0], (int, float)) else None
    return out


def _seconds(value: Any) -> float | None:
    """A time Home Assistant gives as a datetime (or its ISO text), in Unix
    seconds."""
    if hasattr(value, "timestamp"):
        return value.timestamp()
    if isinstance(value, str):
        from datetime import datetime
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def climate(state: State) -> dict[str, Any]:
    attributes = state.attributes
    return {
        "kind": "climate",
        "id": state.entity_id,
        "name": _name(state),
        "icon": attributes.get("icon") or "",
        "mode": state.state,
        "action": attributes.get("hvac_action") or "",
        "target": attributes.get("temperature"),
        "current": attributes.get("current_temperature"),
        "min": attributes.get("min_temp") or 7,
        "max": attributes.get("max_temp") or 30,
        "step": attributes.get("target_temp_step") or 0.5,
        "presets": [p for p in attributes.get("preset_modes") or [] if p != "none"][:4],
        "preset": attributes.get("preset_mode"),
    }


def media(state: State) -> dict[str, Any]:
    volume = state.attributes.get("volume_level")
    return {
        "kind": "media",
        "id": state.entity_id,
        "name": _name(state),
        "icon": state.attributes.get("icon") or "",
        "state": state.state,
        "title": state.attributes.get("media_title") or "",
        "artist": state.attributes.get("media_artist") or "",
        "volume": round(volume * 100) if isinstance(volume, (int, float)) else None,
        "position": state.attributes.get("media_position"),
        "position_at": _seconds(state.attributes.get("media_position_updated_at")),
        "duration": state.attributes.get("media_duration"),
    }


def _domain(entity_id: str) -> str:
    return entity_id.partition(".")[0]


def members(state: State | None) -> list[str]:
    """A light group's own lights (its entity_id attribute), if it is one."""
    if state is None or _domain(state.entity_id) != "light":
        return []
    found = state.attributes.get("entity_id")
    return [e for e in found if isinstance(e, str) and e != state.entity_id] if isinstance(found, (list, tuple)) else []


# An alarm's modes in the ring's order, each by its supported_features bit
# (AlarmControlPanelEntityFeature).
ALARM_MODES = (("home", 1), ("away", 2), ("night", 4), ("vacation", 32), ("custom_bypass", 16))
# The longest name a mode takes on the Nest, and the code lengths it takes.
MODE_NAME = 24
CODE_LENGTHS = range(4, 13)


def alarmo(state: State) -> bool:
    """Whether an alarm is Alarmo's, by the attributes only it gives."""
    return "arm_mode" in state.attributes and "open_sensors" in state.attributes


def alarm(state: State, get: Any = lambda _: None, settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """An alarm panel's card, from what any alarm in Home Assistant gives:
    its state, the modes it has (less those `settings` hide, named as they
    say), and the code it wants for what would come next (code_format, but
    none to arm where code_arm_required says so). Alarmo's add
    its delay's end (its `delay` from when the state began), the sensors
    that set it off, and arming anyway with one open."""
    attributes = state.attributes
    settings = settings or {}
    features = attributes.get("supported_features") or 0
    hidden = set(settings.get("hidden") or [])
    names = settings.get("names") or {}
    modes = [{"mode": mode, "name": str(names.get(mode) or "").strip()[:MODE_NAME]}
             for mode, bit in ALARM_MODES if features & bit and mode not in hidden]
    code = attributes.get("code_format")
    if state.state == "disarmed" and attributes.get("code_arm_required") is False:
        # Most panels give their code's format whatever the state: arming
        # wants it only if it says so.
        code = None
    length = settings.get("code_length")
    out: dict[str, Any] = {
        "kind": "alarm", "id": state.entity_id, "name": _name(state), "icon": attributes.get("icon") or "",
        "state": state.state, "modes": modes, "code": code if code in ("number", "text") else None,
        "code_length": length if isinstance(length, int) and length in CODE_LENGTHS else None,
        "delay": None, "sensors": [], "can_force": alarmo(state), "takeover": settings.get("takeover", True) is not False,
    }
    if not alarmo(state):
        return out
    seconds = attributes.get("delay")
    began = _seconds(getattr(state, "last_changed", None))
    if state.state in ("arming", "pending") and isinstance(seconds, (int, float)) and seconds > 0 and began:
        out["delay"] = {"ends": round(began + seconds, 1), "seconds": seconds}
    if state.state in ("pending", "triggered"):
        out["sensors"] = sensor_names(attributes.get("open_sensors"), get)
    return out


def sensor_names(sensors: Any, get: Any) -> list[str]:
    """Sensors (Alarmo's open_sensors, a mapping or a list of entities) by
    their names in Home Assistant."""
    ids = list(sensors) if isinstance(sensors, (dict, list, tuple)) else []
    found = [get(e) for e in ids if isinstance(e, str)]
    return [_name(s) if s is not None else e for s, e in zip(found, ids)][:4]


def card(state: State, get: Any = lambda _: None, alarms: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One device's card: a thermostat's, a media player's, a scene's, a
    camera's, a select's, a cover's, an alarm's (with its settings from
    `alarms`), or one light's (or switch's), which the thermostat dims or
    switches straight away; a light group's with its own lights, from
    `get`, to switch each."""
    domain = _domain(state.entity_id)
    if domain == "alarm_control_panel":
        return alarm(state, get, (alarms or {}).get(state.entity_id))
    if domain in CHOICES:
        return {"kind": "options", "id": state.entity_id, "name": _name(state),
                "icon": state.attributes.get("icon") or "",
                "options": [str(o) for o in state.attributes.get("options") or []][:12], "current": state.state}
    if domain == "cover":
        position = state.attributes.get("current_position")
        return {"kind": "cover", "id": state.entity_id, "name": _name(state), "icon": state.attributes.get("icon") or "",
                "state": state.state, "position": round(position) if isinstance(position, (int, float)) else None,
                "positionable": bool((state.attributes.get("supported_features") or 0) & 4)}
    if domain == "climate":
        return climate(state)
    if domain == "media_player":
        return media(state)
    if domain in RUN or domain == "camera":
        return {"kind": "action" if domain in RUN else "camera", "id": state.entity_id, "name": _name(state),
                "icon": state.attributes.get("icon") or ""}
    one = light(state)
    own = [light(s) for s in map(get, members(state)) if s is not None][:MEMBERS]
    return {"kind": "lights", "name": one["name"], "icon": one["icon"], "lights": [one], "members": own}


# A group's own lights the thermostat lists, at most.
MEMBERS = 8


def glance(weather: State | None, offset: float) -> dict[str, Any]:
    """The glance's: Home Assistant's time zone (seconds east of UTC) and,
    from a weather entity, the condition and temperature."""
    out: dict[str, Any] = {"offset": round(offset)}
    if weather is not None and weather.state not in ("unavailable", "unknown"):
        temperature = weather.attributes.get("temperature")
        out["weather"] = {"condition": weather.state,
                          "temperature": temperature if isinstance(temperature, (int, float)) else None,
                          "unit": weather.attributes.get("temperature_unit") or ""}
    return out


def named(card: dict[str, Any], name: str | None) -> dict[str, Any]:
    """A card called something else on the Nest: a light's card and its
    one light both (a group's own light: its card shows that light's name;
    its members keep theirs)."""
    if not name:
        return card
    card = {**card, "name": name}
    if card.get("kind") == "lights" and len(card.get("lights") or []) == 1:
        card["lights"] = [{**card["lights"][0], "name": name}, *card["lights"][1:]]
    return card


def cards(entities: Sequence[str], get: Any, wake: str = "chooser", device: str | None = None,
          night: bool = False, names: Mapping[str, str] | None = None, thermostat: int = 0,
          alarms: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The cards, a device each in the order chosen, each entity's state
    from `get` (None: unknown to Home Assistant, left out), and what the
    thermostat wakes to: `device`'s place among the cards, else "chooser"
    or "thermostat" as `wake` says; "auto" picks: the first media player
    playing, else at `night` the first light that's on, else the chooser.
    `names` renames cards, by entity. `thermostat`: the Nest's own card's
    place, before entities[thermostat]; sent where it isn't first."""
    shown = [state for state in map(get, entities) if state is not None]
    ids = [state.entity_id for state in shown]
    woken: Any = "thermostat" if wake == "thermostat" else "chooser"
    if wake == "auto":
        playing = [i for i, s in enumerate(shown) if _domain(s.entity_id) == "media_player" and s.state == "playing"]
        lit = [i for i, s in enumerate(shown) if night and _domain(s.entity_id) == "light" and s.state == "on"]
        woken = (playing or lit or ["chooser"])[0]
    if device in ids:
        woken = ids.index(device)
    names = names or {}
    out: dict[str, Any] = {"cards": [named(card(state, get, alarms), names.get(state.entity_id)) for state in shown],
                           "wake": woken}
    # Among the cards shown: those Home Assistant doesn't know are left out.
    place = sum(1 for e in entities[:max(thermostat, 0)] if get(e) is not None)
    if place:
        out["thermostat"] = place
    return out


@dataclass
class Call:
    """A Home Assistant action to carry out."""

    domain: str
    service: str
    entity_id: str
    data: dict[str, Any]


def command(payload: Mapping[str, Any], entities: Sequence[str]) -> Call | None:
    """What a command from the thermostat does, or None if it isn't one it
    may give: only the chosen entities (a chosen group's own lights among
    them), only the ways each kind allows."""
    entity = payload.get("id")
    what = payload.get("action")
    # A notification's answer: its tag, not an entity, for an event.
    if what in ("alert_pressed", "alert_dismissed") and isinstance(entity, str) and entity:
        return Call("fledge", "notification", entity, {"action": what.removeprefix("alert_")})
    try:
        value = float(payload.get("value", 0))
    except (TypeError, ValueError):
        return None
    if not isinstance(entity, str) or entity not in entities:
        return None
    domain = _domain(entity)
    if domain in SWITCHED:
        if what == "off":
            return Call("homeassistant", "turn_off", entity, {})
        if what == "on":
            return Call("homeassistant", "turn_on", entity, {})
        if what == "brightness" and domain == "light" and 1 <= value <= 100:
            return Call("light", "turn_on", entity, {"brightness_pct": round(value)})
        if what == "color_temp" and domain == "light" and 1000 <= value <= 12000:
            return Call("light", "turn_on", entity, {"color_temp_kelvin": round(value)})
        if what == "hue" and domain == "light" and 0 <= value <= 360:
            return Call("light", "turn_on", entity, {"hs_color": [value % 360, 100]})
    if domain in RUN and what == "run":
        return Call(domain, RUN[domain], entity, {})
    if domain == "camera" and what == "snapshot":
        return Call("fledge", "snapshot", entity, {})
    if domain == "climate" and what == "temperature" and 5 <= value <= 35:
        return Call("climate", "set_temperature", entity, {"temperature": value})
    if domain == "media_player":
        if what == "volume" and 0 <= value <= 100:
            return Call("media_player", "volume_set", entity, {"volume_level": value / 100})
        if what == "play_pause":
            return Call("media_player", "media_play_pause", entity, {})
        if what in ("next", "previous"):
            return Call("media_player", f"media_{what}_track", entity, {})
    option = payload.get("option")
    named = isinstance(option, str) and 0 < len(option) <= 64
    if domain == "climate" and what == "preset" and named:
        return Call("climate", "set_preset_mode", entity, {"preset_mode": option})
    if domain in CHOICES and what == "select" and named:
        return Call(domain, "select_option", entity, {"option": option})
    if domain == "alarm_control_panel" and what in ("arm", "disarm"):
        code = payload.get("code")
        if code is not None and not (isinstance(code, str) and 0 < len(code) <= 32):
            return None
        data: dict[str, Any] = {"code": code} if code else {}
        if what == "disarm":
            return Call(domain, "alarm_disarm", entity, data)
        mode = payload.get("mode")
        if mode not in [m for m, _ in ALARM_MODES]:
            return None
        if payload.get("force") is True:
            data["force"] = True
        return Call(domain, f"alarm_arm_{mode}", entity, data)
    if domain == "cover":
        if what == "position" and 0 <= value <= 100:
            return Call("cover", "set_cover_position", entity, {"position": round(value)})
        if what in ("open", "close"):
            return Call("cover", f"{what}_cover", entity, {})
    return None


def redacted(payload: Any) -> Any:
    """A command as the log may show it: an alarm code hidden."""
    if isinstance(payload, dict) and "code" in payload:
        return {**payload, "code": "…"}
    return payload


class Tries:
    """Wrong alarm codes from one thermostat, for one alarm: three, then
    the code is locked out for 30 s, twice as long each time after (to 15
    minutes); a code taken starts it again. Times are monotonic seconds."""

    FIRST = 30
    LONGEST = 900

    def __init__(self) -> None:
        self.wrong = 0
        self.until = 0.0
        self.lock = self.FIRST

    def locked(self, now: float) -> float:
        """Seconds the code is still locked out for."""
        return max(0.0, self.until - now)

    def failed(self, now: float) -> tuple[int, int]:
        """A wrong code: the tries left, and the seconds now locked out for."""
        self.wrong += 1
        if self.wrong < 3:
            return 3 - self.wrong, 0
        self.wrong = 0
        self.until = now + self.lock
        locked, self.lock = self.lock, min(self.lock * 2, self.LONGEST)
        return 0, locked

    def passed(self) -> None:
        self.wrong = 0
        self.lock = self.FIRST
