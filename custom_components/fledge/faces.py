"""Glance faces as cards (protocol 13): something to look at among the
cards, with actions round the ring.

A face card has an id of its own in the cards' order (FACE_DOMAIN.<key>,
never a real entity) and its own layout and lines, drawn as the glance is.
The glance's other settings (the time format, the calendars, the date's
words) are shared with it, and it has no night of its own: someone is
standing at it. Its actions are entities run or switched from its ring of
actions, and one of them may also be what a hold does.
"""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol

from homeassistant.core import State
from homeassistant.helpers import config_validation as cv

from . import glance
from .const import FACE_DOMAIN, RUN, SWITCHED

# What a face's action can be: something run, or switched on and off.
ACTION_DOMAINS = (*SWITCHED, *RUN)
# The most actions round a face's ring, after Back.
MAX_ACTIONS = 4
# A face's name until it's given one: its layout's.
LAYOUT_NAMES = {"clock": "Clock", "temp": "Temperature", "weather": "Weather", "analog": "Clock",
                "number": "Number", "agenda": "Up next", "battery": "Battery"}
LAYOUT_ICONS = {"clock": "mdi:clock-outline", "temp": "mdi:thermometer", "weather": "mdi:weather-partly-cloudy",
                "analog": "mdi:clock-time-four-outline", "number": "mdi:numeric", "agenda": "mdi:calendar", "battery": "mdi:battery"}
# The glance's settings a face shares rather than has its own.
SHARED = ("hours", "ampm", "calendar", "calendars", "skip_all_day", "date", "words", "language")


def action_entity(value: Any) -> str:
    """An entity a face's action can be."""
    entity = cv.entity_id(value)
    if entity.partition(".")[0] not in ACTION_DOMAINS:
        raise vol.Invalid(f"{entity} can't be a face's action")
    return entity


SCHEMA = vol.Schema({
    vol.Optional("layout", default="clock"): vol.In(glance.LAYOUTS),
    vol.Optional("lines", default={}): glance.LINES,
    vol.Optional("big"): vol.Any(None, cv.entity_id),
    vol.Optional("big_label"): vol.Any(None, vol.All(str, vol.Length(max=40))),
    vol.Optional("colour", default=True): bool,
    vol.Optional("battery"): vol.Any(None, glance.BATTERY),
    vol.Optional("actions", default=[]): vol.All([action_entity], vol.Length(max=MAX_ACTIONS)),
    # What a hold does: one of its actions, or nothing.
    vol.Optional("hold"): vol.Any(None, action_entity),
})


def is_face(entity_id: Any) -> bool:
    """Whether an id in the cards' order is a face card's."""
    return isinstance(entity_id, str) and entity_id.startswith(f"{FACE_DOMAIN}.")


def new_id() -> str:
    """A new face card's id."""
    return f"{FACE_DOMAIN}.{secrets.token_hex(3)}"


def settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """A face's settings with every default filled in: its actions without
    repeats, and its hold only if it's one of them."""
    try:
        face = SCHEMA(dict(raw or {}))
    except vol.Invalid:
        face = SCHEMA({})
    face["actions"] = list(dict.fromkeys(face["actions"]))
    if face.get("hold") not in face["actions"]:
        face["hold"] = None
    return face


def kept(faces: dict[str, Any] | None, entities: list[str]) -> dict[str, dict[str, Any]]:
    """The faces among `entities` (the cards' order), their settings filled in."""
    return {face_id: settings((faces or {}).get(face_id)) for face_id in entities if is_face(face_id)}


def glance_raw(face: dict[str, Any], shared: dict[str, Any] | None) -> dict[str, Any]:
    """The glance settings a face is drawn from: its own layout and lines
    with the glance's shared settings, and no night."""
    out = {k: v for k, v in (shared or {}).items() if k in SHARED}
    out.update({k: face[k] for k in ("layout", "lines", "big", "big_label", "colour", "battery") if face.get(k) is not None})
    out["night"] = {"mode": "off"}
    return out


def name(face: dict[str, Any], given: str | None = None) -> str:
    """What a face card is called on the Nest."""
    return (given or "").strip() or LAYOUT_NAMES.get(face["layout"], "Glance")


def action(state: State) -> dict[str, Any]:
    """One of a face's actions as the Nest takes it: run, or switched (with
    whether it's on now)."""
    domain = state.entity_id.partition(".")[0]
    out: dict[str, Any] = {"id": state.entity_id, "name": str(state.attributes.get("friendly_name") or state.entity_id),
                           "icon": state.attributes.get("icon") or ""}
    if domain in RUN:
        out["kind"] = "run"
    else:
        out["kind"] = "switch"
        out["on"] = state.state == "on"
    return out


def card(face_id: str, face: dict[str, Any], shown: dict[str, Any], get: Any, given: str | None = None) -> dict[str, Any]:
    """A face's card: its glance as built (`shown`), and its actions that
    Home Assistant knows (`get`), the hold's by its place among them."""
    actions = [action(state) for state in map(get, face["actions"]) if state is not None]
    out: dict[str, Any] = {"kind": "face", "id": face_id, "name": name(face, given),
                           "icon": LAYOUT_ICONS.get(face["layout"], "mdi:clock-outline"), "glance": shown, "actions": actions}
    ids = [a["id"] for a in actions]
    if face.get("hold") in ids:
        out["hold"] = ids.index(face["hold"])
    return out


def entities(faces: dict[str, dict[str, Any]]) -> list[str]:
    """Every face's actions: what the Nest may run or switch for them."""
    return sorted({e for face in faces.values() for e in face["actions"]})
