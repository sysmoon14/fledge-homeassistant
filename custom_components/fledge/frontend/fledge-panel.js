// Fledge's panel in Home Assistant's sidebar: a thermostat's cards in the
// ring's order, previewed as the Nest shows them, and its wake, glance and
// notifications. Plain custom element, no build step; it talks to the
// integration over the fledge/* websocket commands (panel.py).

const ICONS = {
  light: "mdi:lightbulb", switch: "mdi:toggle-switch-variant", fan: "mdi:fan", input_boolean: "mdi:toggle-switch-outline",
  climate: "mdi:thermostat", media_player: "mdi:speaker", scene: "mdi:palette", script: "mdi:script-text-play",
  button: "mdi:gesture-tap-button", input_button: "mdi:gesture-tap-button", camera: "mdi:cctv",
  select: "mdi:format-list-bulleted", input_select: "mdi:format-list-bulleted", cover: "mdi:blinds",
  alarm_control_panel: "mdi:shield-home",
};
const KINDS = {
  light: "Light", switch: "Switch", fan: "Fan", input_boolean: "Toggle", climate: "Thermostat", media_player: "Speaker",
  scene: "Scene", script: "Script", button: "Button", input_button: "Button", camera: "Camera", select: "Options",
  input_select: "Options", cover: "Cover", alarm_control_panel: "Alarm",
};
// What the ring does on each kind of card.
const RING = {
  light: [["Turn", "Brightness"], ["Press", "Warmth and colour, where it has them"]],
  switch: [["Press", "On / off"]], fan: [["Press", "On / off"]], input_boolean: [["Press", "On / off"]],
  climate: [["Turn", "Target temperature"], ["Press", "Presets"]],
  media_player: [["Turn", "Volume"], ["Press", "Play / pause"], ["Behind the dial", "Album art"]],
  scene: [["Press", "Opens it: press again for Run"], ["Hold, open", "Runs it"]], script: [["Press", "Opens it: press again for Run"], ["Hold, open", "Runs it"]],
  button: [["Press", "Opens it: press again for Run"], ["Hold, open", "Presses it"]], input_button: [["Press", "Opens it: press again for Run"], ["Hold, open", "Presses it"]],
  camera: [["Press", "Full screen"], ["Full screen", "A new picture every 3 s"]],
  select: [["Turn", "Pick an option"], ["Press", "Choose it"]], input_select: [["Turn", "Pick an option"], ["Press", "Choose it"]],
  cover: [["Turn", "Position"], ["Press", "Open / close"]],
  alarm_control_panel: [["Press", "Opens it: press again to arm or disarm"], ["Code", "Turn to each digit, press to enter it, hold to rub out"],
    ["Entry delay", "Takes the screen, straight to the code"]],
};
// An alarm's modes, in the ring's order, with Home Assistant's names (cards.py ALARM_MODES).
const ALARM_MODES = [["home", "Home"], ["away", "Away"], ["night", "Night"], ["vacation", "Vacation"], ["custom_bypass", "Custom"]];
const ALARM_ICONS = { home: "mdi:shield-home", night: "mdi:shield-moon", vacation: "mdi:airplane", custom_bypass: "mdi:shield-star" };
const ALARM_ARMED = [154, 134, 255];
const ALARM_RED = [255, 59, 59];
const THERMOSTAT_RING = [["Turn", "Target temperature"], ["Press", "Its dial; press again for eco, off and hot water"]];
const WAKES = [
  ["auto", "Automatic", "The speaker that's playing, else after sunset a light that's on, else the chooser"],
  ["chooser", "Device chooser", "The thermostat among your cards, zoomed out"],
  ["thermostat", "The thermostat", "As the Nest always did"],
];
// Someone across the room (the Nest's Farsight) and someone close (its
// approach reading), as Fledge's options name them.
const FARS = [
  ["glance", "The glance", "The time, the weather and what's on"],
  ["wake", "What it wakes to", "As if the ring had turned"],
  ["dark", "Nothing", "The screen stays dark"],
];
const NEARS = [
  ["wake", "What it wakes to", "Without a touch"],
  ["glance", "The glance", "The same as across the room"],
  ["none", "Nothing more", "Only what across the room shows"],
];
const ACTIVE = ["on", "playing", "open", "heat", "cool", "heat_cool", "auto"];

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const domainOf = (id) => id.split(".")[0];

// The Nest's screens as nestlocal draws them (fledge: crates/nestlocal/src/
// ui/screens.rs and paint.rs), at its 480 × 480, as SVG: the same places,
// sizes and colours, in either of its looks (Nest's ticks, Fledge's arc).
const N = {
  WHITE: [255, 255, 255], GREY: [168, 173, 181], DIM: [92, 96, 104], BLACK: [0, 0, 0], HEAT: [241, 90, 0],
  COOL: [26, 111, 216], MEDIA: [63, 209, 195], AMBER: [246, 136, 0], NOW: [255, 246, 214], ACTION: [255, 196, 87],
  COVER: [120, 170, 255], ECO: [76, 175, 80], WARM: [255, 169, 87], TRACK: [27, 35, 48], FACE: [9, 13, 21],
  LIFT: [22, 31, 48], SOFT: [255, 225, 205], UNDER: [163, 173, 184],
};
const TICKS = 147;
const SPAN = 292;
const NUMBER = 299;
const NEST_ICONS = {
  light: "mdi:lightbulb", switch: "mdi:toggle-switch-variant", input_boolean: "mdi:toggle-switch-variant",
  fan: "mdi:fan", climate: "mdi:thermostat", media_player: "mdi:speaker",
};
const sameRgb = (a, b) => a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
const css = (c, a = 1) => (a >= 1 ? `rgb(${c.join(",")})` : `rgba(${c.join(",")},${Math.round(a * 1000) / 1000})`);
const tickAt = (f) => Math.round(Math.max(0, Math.min(1, f)) * (TICKS - 1));
const tickAngle = (i) => -SPAN / 2 + (SPAN * i) / (TICKS - 1);
const onCircle = (deg, r) => {
  const a = (deg * Math.PI) / 180;
  return [240 + r * Math.sin(a), 240 - r * Math.cos(a)];
};
const r1 = (n) => Math.round(n * 10) / 10;
const capitalise = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
let faceIds = 0;

class Face {
  constructor(fledge) {
    this.fledge = fledge;
    this.defs = [];
    this.svg = [];
    this.icons = [];
  }

  id() {
    faceIds += 1;
    return `nf${faceIds}`;
  }

  // paint::face: black (or the Nest's heating orange), or Fledge's dark
  // face lifting to blue, glowing with what's on.
  face(bg) {
    if (!this.fledge) {
      this.svg.push(`<rect width="480" height="480" fill="${css(bg)}"/>`);
      return;
    }
    this.svg.push(`<rect width="480" height="480" fill="${css(N.FACE)}"/>`);
    this.glow(N.LIFT, 240, 210, 330, 0.7);
    if (!sameRgb(bg, N.BLACK)) this.glow(bg, 240, 272, 300, sameRgb(bg, N.HEAT) ? 0.62 : 0.42);
  }

  glow(c, cx, cy, r, strength) {
    const id = this.id();
    const stops = [0, 0.2, 0.4, 0.6, 0.8, 1]
      .map((d) => `<stop offset="${d}" stop-color="${css(c)}" stop-opacity="${r1(strength * (1 - d) * (1 - d) * 100) / 100}"/>`).join("");
    this.defs.push(`<radialGradient id="${id}" cx="${cx}" cy="${cy}" r="${r}" gradientUnits="userSpaceOnUse">${stops}</radialGradient>`);
    this.svg.push(`<circle cx="${cx}" cy="${cy}" r="${r}" fill="url(#${id})"/>`);
  }

  // paint::ring_shaped: `of(i)` gives each tick { c, a, shape }; shape
  // "short" (the ring's), "long" (a set value), "wide" (the temperature
  // now). `gap` ticks are left out (under the temperature now's number).
  ring(of, gap = null) {
    if (this.fledge) {
      this.arc(of);
      return;
    }
    const plain = [];
    const marks = [];
    for (let i = 0; i < TICKS; i++) {
      const t = of(i);
      if (gap && t.shape === "short" && i >= gap[0] && i <= gap[1]) continue;
      const [length, inner, rim] = t.shape === "long" ? [73, 2.3, 4.3] : t.shape === "wide" ? [54, 2.8, 3.3] : [54, 1.2, 1.2];
      (t.shape === "short" ? plain : marks).push(`<path d="${wedge(tickAngle(i), 233 - length, 233, inner, rim)}" fill="${css(t.c, t.a ?? 1)}"/>`);
    }
    this.svg.push(plain.join(""), marks.join(""));
  }

  // paint::arc: one band on a faint track, lit slices glowing, a set value
  // a white knob, the temperature now a dot.
  arc(of) {
    const R = 212;
    const ticks = Array.from({ length: TICKS }, (_, i) => of(i));
    const unlit = (t) => (t.a ?? 1) < 0.03 || sameRgb(t.c, N.DIM);
    const slices = ticks.map((t, i) => {
      let s = t;
      if (t.shape !== "short") s = [ticks[i - 1], ticks[i + 1]].find((n) => n && n.shape === "short") || { c: N.TRACK };
      return unlit(s) ? null : s;
    });
    const blur = this.id();
    this.defs.push(`<filter id="${blur}" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="7"/></filter>`);
    const band = [];
    const halo = [];
    const caps = [];
    slices.forEach((s, i) => {
      if (!s) return;
      const d = arcPath(i - 0.5, i + 0.56, R);
      band.push(`<path d="${d}" stroke="${css(s.c, s.a ?? 1)}" stroke-width="16" fill="none"/>`);
      halo.push(`<path d="${d}" stroke="${css(s.c, (s.a ?? 1) * 0.55)}" stroke-width="30" fill="none"/>`);
      const cap = (at) => {
        const [x, y] = onArc(at, R);
        caps.push(`<circle cx="${r1(x)}" cy="${r1(y)}" r="8" fill="${css(s.c, s.a ?? 1)}"/>`);
      };
      if (!slices[i - 1]) cap(i - 0.5);
      if (!slices[i + 1]) cap(i + 0.5);
    });
    const marks = [];
    ticks.forEach((t, i) => {
      const [x, y] = onArc(i, R).map(r1);
      if (t.shape === "long") {
        const lit = slices[i];
        marks.push(`<circle cx="${x}" cy="${y}" r="22" fill="${css(lit ? lit.c : N.WHITE, 0.22)}"/>`,
          `<circle cx="${x}" cy="${y}" r="13" fill="#fff"/>`);
        if (lit) marks.push(`<circle cx="${x}" cy="${y}" r="4.5" fill="${css(lit.c)}"/>`);
      } else if (t.shape === "wide") {
        marks.push(`<circle cx="${x}" cy="${y}" r="5.5" fill="${css(t.c, t.a ?? 1)}"/>`);
      }
    });
    this.svg.push(`<path d="${arcPath(-0.5, TICKS - 0.5, R)}" stroke="${css(N.TRACK)}" stroke-width="16" fill="none" stroke-linecap="round"/>`,
      `<g filter="url(#${blur})">${halo.join("")}</g>`, band.join(""), caps.join(""), marks.join(""));
  }

  font(kind) {
    if (this.fledge) return kind === "digits" ? "Outfit,'DM Sans',system-ui,sans-serif" : "'DM Sans',system-ui,sans-serif";
    return "'Akkurat','Helvetica Neue',Roboto,system-ui,sans-serif";
  }

  // paint::text, centred on x (or from it), fitted to `max` px.
  text(x, y, s, size, { weight = 400, c = N.WHITE, a = 1, spacing = 0, anchor = "middle", kind = "regular", max = 340 } = {}) {
    const fitted = fit(String(s ?? ""), size, spacing, max);
    const w = kind === "digits" ? (this.fledge ? 200 : 300) : weight;
    this.svg.push(`<text x="${r1(x)}" y="${r1(y)}" text-anchor="${anchor}" font-family="${this.font(kind)}" font-size="${size}" font-weight="${w}"${spacing ? ` letter-spacing="${spacing}"` : ""} fill="${css(c, a)}">${esc(fitted)}</text>`);
  }

  // A card's name: the Nest's capitals, or as written on Fledge's.
  label(y, s, c) {
    if (this.fledge) this.text(240, y, s, 22, { weight: 500, c, max: 300 });
    else this.text(240, y, String(s ?? "").toUpperCase(), 22, { weight: 500, c, spacing: 1.5, max: 300 });
  }

  line(y, s, c) {
    if (s) this.text(240, y, s, 20, { c, max: 320 });
  }

  // Words above or under a dial's number.
  dialWords(y, s, c, icon = null) {
    if (!this.fledge) {
      this.text(240, y, String(s).toUpperCase(), 25, { weight: 500, c, spacing: 1.5, max: 300 });
      return;
    }
    if (y >= NUMBER) {
      this.text(240, y, s, 21, { c: N.UNDER });
      return;
    }
    const mid = y - 9;
    const words = Math.min(estimate(s, 18, 0), 260);
    const room = icon ? 26 : 0;
    const width = words + room + 34;
    const left = 240 - width / 2;
    this.pill(left, mid - 19, width, 38, c, 0.18);
    if (icon) this.icon(icon, left + 27, mid, 20, c);
    this.text(left + 17 + room, mid + 6.5, s, 18, { weight: 500, c, anchor: "start", max: 260 });
  }

  // A dial's number: 21, with the Nest's raised 5 (Fledge's .5) for a half.
  big(value, half, unit, c = N.WHITE) {
    const whole = half ? Math.floor(value) : Math.round(value);
    const small = half && value - Math.floor(value) >= 0.25 ? (this.fledge ? ".5" : "5") : unit;
    const tail = small ? `<tspan font-size="52" dx="4" dy="-82" letter-spacing="0">${esc(small)}</tspan>` : "";
    this.svg.push(`<text x="240" y="${NUMBER}" text-anchor="middle" font-family="${this.font("digits")}" font-size="170" font-weight="${this.fledge ? 200 : 300}" letter-spacing="-4" fill="${css(c)}">${whole}${tail}</text>`);
  }

  icon(name, cx, cy, size, c) {
    if (name) this.icons.push({ name, cx, cy, size, c });
  }

  pill(x, y, w, h, c, a = 1) {
    this.svg.push(`<rect x="${r1(x)}" y="${r1(y)}" width="${r1(w)}" height="${h}" rx="${h / 2}" fill="${css(c, a)}"/>`);
  }

  picture(url, cx, cy, size, radius) {
    const id = this.id();
    const [x, y] = [cx - size / 2, cy - size / 2];
    this.defs.push(`<clipPath id="${id}"><rect x="${x}" y="${y}" width="${size}" height="${size}" rx="${radius}"/></clipPath>`);
    this.svg.push(`<rect x="${x}" y="${y}" width="${size}" height="${size}" rx="${radius}" fill="#222"/>`,
      `<image href="${esc(url)}" x="${x}" y="${y}" width="${size}" height="${size}" preserveAspectRatio="xMidYMid slice" clip-path="url(#${id})"/>`);
  }

  html() {
    const icons = this.icons.map((i) => `<ha-icon class="nicon" icon="${esc(i.name)}" style="left:${r1(i.cx / 4.8)}%;top:${r1(i.cy / 4.8)}%;--s:${r1(i.size / 4.8)};color:${css(i.c)}"></ha-icon>`).join("");
    return `<svg class="nface" viewBox="0 0 480 480" aria-hidden="true"><defs>${this.defs.join("")}</defs>${this.svg.join("")}</svg>${icons}`;
  }
}

function wedge(deg, rIn, rOut, hIn, hOut) {
  const a = (deg * Math.PI) / 180;
  const [px, py] = [Math.cos(a), Math.sin(a)];
  const [ix, iy] = onCircle(deg, rIn);
  const [ox, oy] = onCircle(deg, rOut);
  const pts = [[ix - px * hIn, iy - py * hIn], [ox - px * hOut, oy - py * hOut], [ox + px * hOut, oy + py * hOut], [ix + px * hIn, iy + py * hIn]];
  return `M${pts.map(([x, y]) => `${r1(x)} ${r1(y)}`).join("L")}Z`;
}

function onArc(at, r) {
  return onCircle(-SPAN / 2 + (SPAN * at) / (TICKS - 1), r);
}

function arcPath(from, to, r) {
  const [x0, y0] = onArc(from, r).map(r1);
  const [x1, y1] = onArc(to, r).map(r1);
  const large = ((to - from) * SPAN) / (TICKS - 1) > 180 ? 1 : 0;
  return `M${x0} ${y0}A${r} ${r} 0 ${large} 1 ${x1} ${y1}`;
}

// Roughly how wide text is: the screen's own fonts aren't the browser's.
function estimate(s, size, spacing) {
  let w = 0;
  for (const ch of String(s)) w += (/[ilI.,'·:;|!]/.test(ch) ? 0.28 : /[mwMW@%]/.test(ch) ? 0.82 : /[A-Z0-9]/.test(ch) ? 0.64 : 0.54) * size + spacing;
  return w;
}

function fit(s, size, spacing, max) {
  if (estimate(s, size, spacing) <= max) return s;
  let out = s;
  while (out.length > 1 && estimate(`${out}…`, size, spacing) > max) out = out.slice(0, -1);
  return `${out.trimEnd()}…`;
}

// model::Light::colour: its rgb (or a 2700 K lamp's), lifted to read on black.
function lightColour(light) {
  const [r, g, b] = light.rgb || N.WARM;
  const top = Math.max(r, g, b, 1);
  if (top >= 170) return [r, g, b];
  return [r, g, b].map((c) => Math.min(255, Math.floor((c * 200) / top)));
}

function climateColours(card) {
  if (card.action === "heating" || card.action === "preheating") return [N.HEAT, true];
  if (card.action === "cooling") return [N.COOL, true];
  return [N.BLACK, false];
}

function climateRing(f, card, target) {
  const [, active] = climateColours(card);
  const fraction = (t) => (t - card.min) / Math.max(card.max - card.min, 0.5);
  const mark = target != null ? tickAt(fraction(target)) : null;
  const now = card.current != null ? tickAt(fraction(card.current)) : null;
  const range = active && now != null && mark != null && now !== mark ? [Math.min(now, mark), Math.max(now, mark), now] : null;
  const base = f.fledge || !active ? N.DIM : card.action === "cooling" ? [96, 160, 240] : N.AMBER;
  const label = card.mode !== "off" ? nowPlace(f, card, target) : null;
  f.ring((i) => {
    if (i === mark) return { c: N.WHITE, shape: "long" };
    if (i === now) return { c: active ? N.NOW : N.GREY, shape: "wide" };
    if (range && i >= range[0] && i <= range[1]) {
      const along = Math.abs(i - range[2]) / (range[1] - range[0]);
      if (f.fledge) {
        const [from, to] = card.action === "cooling" ? [[60, 140, 240], [150, 205, 255]] : [[255, 106, 43], [255, 179, 92]];
        const t = 0.35 * (1 - along);
        return { c: from.map((v, k) => Math.round(v + (to[k] - v) * t)), shape: "short" };
      }
      return { c: N.WHITE, a: 0.62, shape: "short" };
    }
    return { c: base, shape: "short" };
  }, label && !f.fledge ? label.gap : null);
  if (label) nowLabel(f, card, label);
  if (target != null && card.mode !== "off") f.big(target, (card.step || 0.5) < 1, "");
}

// The temperature now, small beside its tick, away from the target: shown
// only while the target is another.
function nowPlace(f, card, target) {
  const now = card.current;
  if (now == null || target == null || Math.abs(now - target) < (card.step || 0.5) / 2) return null;
  const tick = tickAt((now - card.min) / Math.max(card.max - card.min, 0.5));
  const away = target < now ? 1 : -1;
  const [x, y] = onCircle(tickAngle(tick) + away * 9, f.fledge ? 178 : 212);
  const centre = tick + away * 4.5;
  return { x, y, gap: [Math.round(centre - 5), Math.round(centre + 5)] };
}

function nowLabel(f, card, place) {
  const now = card.current;
  const half = (card.step || 0.5) < 1 && Math.abs((now - Math.floor(now)) - 0.5) < 0.25;
  const whole = half ? Math.floor(now) : Math.round(now);
  const tail = half ? `<tspan font-size="16" dx="2" dy="-12">5</tspan>` : "";
  f.svg.push(`<text x="${r1(place.x)}" y="${r1(place.y + 10)}" text-anchor="middle" font-family="${f.font("medium")}" font-size="28" font-weight="500" fill="#fff">${whole}${tail}</text>`);
}

const FACES = {
  lights(f, card) {
    if (card.lights?.length === 1) return FACES.light(f, card, card.lights[0]);
    f.face(N.BLACK);
    const lights = card.lights || [];
    const on = lights.filter((l) => l.on).map((l) => [Math.max(l.brightness ?? 100, 1), lightColour(l)]);
    let room = null;
    if (on.length) {
      const weight = on.reduce((s, [b]) => s + b, 0);
      room = [Math.round(weight / on.length), [0, 1, 2].map((k) => Math.floor(on.reduce((s, [b, c]) => s + b * c[k], 0) / weight))];
    }
    const top = room ? tickAt(room[0] / 100) : -1;
    f.ring((i) => ({ c: room && i <= top ? room[1] : N.DIM, shape: "short" }));
    const accent = room ? room[1] : N.GREY;
    f.icon(card.icon || "mdi:lightbulb-group", 240, 132, 72, accent);
    f.label(214, card.name, accent);
    if (!room) {
      f.text(240, 320, "Off", 96);
      f.line(366, `${lights.length} lights`, N.GREY);
    } else {
      f.text(240, 322, String(on.length), 112);
      f.line(366, `of ${lights.length} on · ${room[0]}%`, N.GREY);
    }
  },
  light(f, card, light) {
    const on = light.on;
    const accent = on ? lightColour(light) : N.GREY;
    const value = light.brightness ?? 100;
    f.face(N.BLACK);
    if (light.dimmable) {
      const top = tickAt(value / 100);
      f.ring((i) => (on && i === top ? { c: N.WHITE, shape: "long" } : { c: on && i <= top ? accent : N.DIM, shape: "short" }));
    } else {
      f.ring(() => ({ c: on ? accent : N.DIM, shape: "short" }));
    }
    f.icon(light.icon || card.icon || NEST_ICONS[domainOf(light.id || "")], 240, 98, 40, accent);
    f.label(150, light.name, accent);
    if (!on || !light.dimmable) f.text(240, NUMBER - 8, on ? "On" : "Off", 96);
    f.line(366, light.dimmable ? (on ? "Hold to turn off" : "Hold to turn on") : (on ? "Press to turn off" : "Press to turn on"), N.GREY);
    if (light.dimmable && on) f.big(value, false, "%");
  },
  climate(f, card) {
    const [bg, active] = climateColours(card);
    f.face(bg);
    const target = card.target;
    f.label(150, card.name, active ? N.WHITE : N.GREY);
    if (target == null || card.mode === "off") f.text(240, 300, "OFF", 96, { kind: "digits" });
    const now = card.current != null ? `${card.current.toFixed(1)}° now` : "";
    const state = card.action || String(card.mode || "").replace(/_/g, " ");
    f.line(366, [now, state].filter(Boolean).join(" · "), active ? N.SOFT : N.GREY);
    climateRing(f, card, target);
  },
  media(f, card, ctx) {
    f.face(N.BLACK);
    const playing = card.state === "playing";
    const lit = card.volume != null ? tickAt(card.volume / 100) : -1;
    f.ring((i) => (i <= lit ? { c: N.MEDIA, a: playing ? 1 : 0.45, shape: "short" } : { c: N.DIM, shape: "short" }));
    const accent = playing ? N.MEDIA : N.GREY;
    let top;
    if (ctx.art) {
      const [y, radius] = f.fledge ? [186, 24] : [194, 14];
      f.picture(ctx.art, 240, y, 168, radius);
      top = y + 84;
    } else {
      f.label(124, card.name, accent);
      f.icon(card.icon || "mdi:speaker", 240, 210, 92, accent);
      top = 270;
    }
    f.text(240, top + 42, card.title || capitalise(card.state), 28, { weight: 500, max: 300 });
    let below = top + 76;
    let done = null;
    if (card.duration > 0 && card.position != null) {
      const at = card.position + (playing && card.position_at ? Math.max(0, ctx.now - card.position_at) : 0);
      done = Math.max(0, Math.min(1, at / card.duration));
    }
    if (done != null) {
      f.pill(150, top + 58, 180, 5, N.DIM);
      f.pill(150, top + 58, Math.max(180 * done, 5), 5, playing ? N.MEDIA : N.GREY);
      below += 14;
    }
    f.line(below, card.artist, N.GREY);
  },
  action(f, card) {
    f.face(N.BLACK);
    f.icon(card.icon || "mdi:play-circle-outline", 240, 178, 104, N.WHITE);
    f.text(240, 288, card.name, 40, { max: 330 });
  },
  camera(f, card, ctx) {
    f.face(N.BLACK);
    if (ctx.art) {
      f.picture(ctx.art, 240, 240, 480, 0);
      return;
    }
    f.ring(() => ({ c: N.DIM, shape: "short" }));
    f.icon(card.icon || "mdi:cctv", 240, 178, 96, N.WHITE);
    f.text(240, 286, card.name, 36, { max: 330 });
    f.line(328, "Press for a picture", N.GREY);
  },
  options(f, card) {
    f.face(N.BLACK);
    f.icon(card.icon || "mdi:format-list-bulleted", 240, 150, 84, N.WHITE);
    f.label(232, card.name, N.GREY);
    f.text(240, 292, card.current ?? "—", 40, { max: 330 });
    f.line(334, "Press to choose", N.GREY);
  },
  cover(f, card) {
    const open = card.state === "open" || card.state === "opening";
    const value = card.position ?? (open ? 100 : 0);
    f.face(N.BLACK);
    const top = tickAt(value / 100);
    f.ring((i) => (i === top ? { c: N.WHITE, shape: "long" } : { c: i <= top && value > 0 ? N.COVER : N.DIM, shape: "short" }));
    f.icon(card.icon || (open ? "mdi:window-shutter-open" : "mdi:window-shutter"), 240, 98, 40, N.COVER);
    f.label(150, card.name, N.COVER);
    if (!card.positionable) f.text(240, NUMBER - 12, capitalise(card.state), 80);
    f.line(366, card.positionable ? "How open · press for more" : "Press to open or close", N.GREY);
    if (card.positionable) f.big(value, false, "%");
  },
  // screens.rs alarm_screen, as its card shows it among the others: its
  // state the large word in its colour, the whole ring lit the same.
  alarm(f, card) {
    const name = card.name || "Alarm";
    const modeName = (mode) => card.modes.find((m) => m.mode === mode)?.name || ALARM_MODES.find(([m]) => m === mode)?.[1] || mode;
    const armed = card.state.startsWith("armed_") ? card.state.slice(6) : null;
    const [accent, icon, word, under] = armed ? [ALARM_ARMED, ALARM_ICONS[armed] || "mdi:shield-lock", "Armed", modeName(armed)]
      : card.state === "disarmed" ? [N.ECO, "mdi:shield-lock-open-outline", "Disarmed", ""]
      : card.state === "arming" ? [N.AMBER, "mdi:home-export-outline", "Arming", "Leave now"]
      : card.state === "pending" ? [N.AMBER, "mdi:door-open", "Entry delay", card.code === "number" ? "Enter the code" : ""]
      : card.state === "triggered" ? [ALARM_RED, "mdi:alarm-light", "Alarm", card.sensors?.[0] || ""]
      : card.state === "disarming" ? [N.AMBER, "mdi:shield-lock-open-outline", "Disarming", ""]
      : [N.DIM, "mdi:shield-lock-open-outline", "Unavailable", ""];
    const triggered = card.state === "triggered";
    f.face(f.fledge || triggered ? (sameRgb(accent, N.DIM) ? N.BLACK : accent) : N.BLACK);
    f.ring(() => ({ c: triggered && !f.fledge ? N.WHITE : accent, shape: "short" }));
    const white = triggered;
    f.icon(icon, 240, 136, 76, white ? N.WHITE : accent);
    f.text(240, 216, name, 22, { weight: 500, kind: "medium", c: white && !f.fledge ? N.WHITE : N.GREY, max: 300 });
    f.text(240, 284, word, 52, { weight: 500, kind: "medium", c: white ? N.WHITE : accent, max: 340 });
    if (under) f.text(240, 326, under, 24, { max: 320 });
  },
  // This thermostat's own card (screens.rs nest_card).
  nest(f, nest) {
    const card = nest.card;
    const [bg, active] = climateColours(card);
    f.face(bg);
    const soft = active ? N.SOFT : N.GREY;
    const off = card.mode === "off";
    const target = off ? null : card.target;
    if (off) {
      f.text(240, NUMBER - 18, "OFF", 96, { kind: "digits" });
    } else if (nest.eco) {
      f.icon("mdi:leaf", 240, 148, 40, active ? N.WHITE : N.ECO);
    } else {
      const doing = card.action === "heating" ? "Heating" : card.action === "cooling" ? "Cooling" : "";
      let above;
      if (doing) above = f.fledge ? (nest.eta ? `${doing} · ${nest.eta}` : doing) : (nest.eta ? `In ${nest.eta}` : doing);
      else above = card.mode === "cool" ? "Cool set to" : "Heat set to";
      const tint = !active ? N.GREY : !f.fledge ? N.WHITE : card.action === "cooling" ? [170, 205, 255] : [255, 201, 166];
      f.dialWords(161, above, tint, card.action === "heating" ? "mdi:fire" : card.action === "cooling" ? "mdi:snowflake" : null);
      if (nest.next) f.dialWords(343, `Until ${nest.next}`, soft);
    }
    if (!off && nest.boost) f.line(392, `Hot water boost · ${nest.boost} min`, soft);
    if (off) {
      climateRing(f, card, null);
      return;
    }
    if (nest.eco) {
      const now = card.current != null ? tickAt((card.current - card.min) / Math.max(card.max - card.min, 0.5)) : null;
      f.ring((i) => (i === now ? { c: active ? N.NOW : N.GREY, shape: "wide" } : { c: f.fledge || !active ? N.DIM : N.AMBER, shape: "short" }));
      f.text(240, NUMBER - 8, f.fledge ? "Eco" : "ECO", 120, { kind: "digits" });
      return;
    }
    climateRing(f, card, target);
  },
  settings(f) {
    f.face(N.BLACK);
    f.icon("mdi:cog", 240, 168, 96, N.WHITE);
    f.text(240, 278, "Settings", 40);
    f.line(316, "Eco · Home/Away · Fledge", N.GREY);
  },
  // Not a card on the Nest: Home Assistant doesn't know it.
  missing(f, name, why) {
    f.face(N.BLACK);
    f.ring(() => ({ c: N.DIM, a: 0.35, shape: "short" }));
    f.icon("mdi:help-circle-outline", 240, 178, 80, N.DIM);
    f.text(240, 278, name, 32, { c: N.GREY, max: 320 });
    f.line(318, why, N.DIM);
  },
};

// The thermostat's own card in the ring order, among Home Assistant's.
const THERMO = "@thermostat";
// Where Fledge's own build and its check live, among the Nest's entities.
const own = (t, domain, end) => (t.own || []).find((id) => id.startsWith(`${domain}.`) && id.endsWith(end)) || null;
// The card protocol that puts the thermostat anywhere (nestlocal ui::PROTOCOL).
const ANYWHERE = 5;
const LOOK_KEY = "fledge-panel-look";
const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

class FledgePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._thermostats = null;
    this._domains = [];
    this._index = 0;
    this._draft = null;
    this._saved = "";
    this._sel = 0;
    this._tab = "cards";
    this._preview = { cards: {}, thermostat: null };
    this._adding = false;
    this._roleAsk = false;
    this._query = "";
    this._busy = false;
    this._error = "";
    this._notice = "";
    this._note = { title: "Washing's done", message: "The washing machine has finished.", camera: "", timeout: 30 };
    this._seen = {};
    this._drag = null;
    this._pickers = !!customElements.get("ha-entity-picker");
    try { this._look = localStorage.getItem(LOOK_KEY) === "nest" ? "nest" : "fledge"; } catch (_) { this._look = "fledge"; }
    this.shadowRoot.addEventListener("click", (e) => this._click(e));
    this.shadowRoot.addEventListener("input", (e) => this._input(e));
    this.shadowRoot.addEventListener("change", (e) => this._input(e));
    this.shadowRoot.addEventListener("value-changed", (e) => this._picked(e));
    this.shadowRoot.addEventListener("keydown", (e) => this._key(e));
    this.shadowRoot.addEventListener("dragstart", (e) => this._dragStart(e));
    this.shadowRoot.addEventListener("dragover", (e) => this._dragOver(e));
    this.shadowRoot.addEventListener("drop", (e) => this._dropped(e));
    this.shadowRoot.addEventListener("dragend", () => this._dragEnd());
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._loadPickers();
      this._load();
      return;
    }
    for (const picker of this.shadowRoot.querySelectorAll("ha-entity-picker")) picker.hass = hass;
    if (!this._draft || !this._current) return;
    // Only what the panel shows: the cards' devices, and the Nest's own.
    const t = this._current;
    const cards = [...this._entities(), t.climate].filter(Boolean);
    if (cards.some((id) => hass.states[id] !== this._seen[id])) {
      this._refreshSoon();
      return;
    }
    const mine = [...(t.own || []), ...Object.values(t.entities || {})].filter(Boolean);
    if (mine.some((id) => hass.states[id] !== this._seen[id])) {
      for (const id of mine) this._seen[id] = hass.states[id];
      this._render();
    }
  }

  get hass() {
    return this._hass;
  }

  set narrow(value) {
    this._narrow = value;
    if (this._thermostats) this._render();
  }

  get _current() {
    return this._thermostats?.[this._index];
  }

  // Home Assistant's own entity picker, which it loads with its editors.
  async _loadPickers() {
    if (this._pickers) return;
    try {
      const helpers = await window.loadCardHelpers?.();
      const card = helpers && (await helpers.createCardElement({ type: "entities", entities: [] }));
      await card?.constructor?.getConfigElement?.();
      await Promise.race([customElements.whenDefined("ha-entity-picker"), new Promise((r) => setTimeout(r, 4000))]);
    } catch (_) { /* the plain list then */ }
    if (customElements.get("ha-entity-picker")) {
      this._pickers = true;
      if (this._thermostats) this._render();
    }
  }

  // Loading and saving

  async _load(keepSelection = false) {
    try {
      const result = await this._hass.callWS({ type: "fledge/thermostats" });
      this._thermostats = result.thermostats;
      this._domains = result.domains;
      if (this._index >= this._thermostats.length) this._index = 0;
      this._reset(keepSelection);
      await this._refresh();
    } catch (err) {
      this._error = err?.message || String(err);
      this._thermostats = this._thermostats || [];
      this._render();
    }
  }

  _reset(keepSelection = false) {
    const t = this._current;
    const options = t?.options || {};
    const entities = [...(options.entities || [])];
    const order = [...entities];
    order.splice(Math.min(options.thermostat || 0, entities.length), 0, THERMO);
    this._draft = {
      order,
      wake: options.wake || "chooser",
      wake_device: options.wake_device || null,
      far: options.far || (options.glance ? "glance" : "wake"),
      near: options.near || "wake",
      card_idle: options.card_idle || 20,
      dim_off: options.dim_off !== false,
      show_thermostat: options.show_thermostat !== false,
      weather: options.weather || null,
      outdoor_temperature: options.outdoor_temperature || null,
      room_sensor: options.room_sensor || null,
      names: { ...(options.names || {}) },
      alarms: JSON.parse(JSON.stringify(options.alarms || {})),
      device_name: t?.name_by_user || "",
    };
    this._saved = this._fingerprint(this._draft);
    if (!keepSelection) this._sel = order.indexOf(THERMO);
    this._sel = Math.max(0, Math.min(this._sel, order.length - 1));
  }

  // Whether the Nest acts as a thermostat: unless its switch is off.
  _acting() {
    const id = this._current?.role_switch;
    return !id || this._hass.states[id]?.state !== "off";
  }

  // Whether the Nest shows its own card: it acts as a thermostat and it isn't hidden.
  _thermoShown() {
    return this._acting() && this._draft.show_thermostat;
  }

  _entities(draft = this._draft) {
    return draft.order.filter((id) => id !== THERMO);
  }

  _place(draft = this._draft) {
    return draft.order.indexOf(THERMO);
  }

  _fingerprint(draft) {
    const entities = this._entities(draft);
    const names = Object.fromEntries(Object.entries(draft.names).filter(([k, v]) => v.trim() && entities.includes(k)).sort());
    const alarms = Object.fromEntries(Object.entries(draft.alarms || {}).filter(([k]) => entities.includes(k)).sort());
    return JSON.stringify({ ...draft, device_name: draft.device_name.trim(), names, alarms,
      wake_device: entities.includes(draft.wake_device) ? draft.wake_device : null });
  }

  get _dirty() {
    return !!this._draft && this._fingerprint(this._draft) !== this._saved;
  }

  _refreshSoon() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._refresh(), 400);
  }

  async _refresh() {
    if (!this._draft || !this._current) {
      this._render();
      return;
    }
    const climate = this._current.climate;
    try {
      this._preview = await this._hass.callWS({ type: "fledge/preview", entities: this._entities(), climate: climate || null });
    } catch (err) {
      this._error = err?.message || String(err);
    }
    for (const id of [...this._entities(), climate].filter(Boolean)) this._seen[id] = this._hass.states[id];
    this._render();
  }

  async _save() {
    const t = this._current;
    if (!t || this._busy) return;
    this._busy = true;
    this._error = "";
    this._render();
    const d = this._draft;
    const entities = this._entities();
    try {
      await this._hass.callWS({
        type: "fledge/save", entry_id: t.entry_id, entities, wake: d.wake, thermostat: this._place(),
        wake_device: entities.includes(d.wake_device) ? d.wake_device : null, glance: d.far === "glance",
        far: d.far, near: d.near, card_idle: d.card_idle, dim_off: d.dim_off, show_thermostat: d.show_thermostat,
        weather: d.weather || null, outdoor_temperature: d.outdoor_temperature || null,
        room_sensor: d.room_sensor || null, names: Object.fromEntries(Object.entries(d.names).filter(([, v]) => v.trim())),
        alarms: Object.fromEntries(Object.entries(d.alarms).filter(([k]) => entities.includes(k)).map(([k, v]) => [k, {
          names: Object.fromEntries(Object.entries(v.names || {}).filter(([, n]) => n.trim())),
          hidden: v.hidden || [], code_length: v.code_length || null, takeover: v.takeover !== false,
        }])),
      });
      const name = d.device_name.trim();
      if (t.device_id && name !== (t.name_by_user || "")) {
        await this._hass.callWS({ type: "config/device_registry/update", device_id: t.device_id, name_by_user: name || null });
      }
      this._busy = false;
      this._flash("Saved. The Nest has its new cards.", 3000);
      await this._load(true);
    } catch (err) {
      this._busy = false;
      this._error = err?.message || String(err);
      this._render();
    }
  }

  // What's shown

  _name(id) {
    if (id === THERMO) return this._thermostatName();
    const mine = this._draft.names[id];
    if (mine && mine.trim()) return mine;
    return this._hass.states[id]?.attributes.friendly_name || id;
  }

  _thermostatName() {
    const t = this._current;
    return this._draft.device_name.trim() || t.default_name || t.name || "This thermostat";
  }

  _icon(id) {
    if (id === THERMO) return "mdi:thermostat";
    return this._hass.states[id]?.attributes.icon || ICONS[domainOf(id)] || "mdi:card-outline";
  }

  // The Fledge build on the Nest: its update entity's installed version.
  _build(short = false) {
    const t = this._current;
    const update = t.update && this._hass.states[t.update];
    const version = update?.attributes.installed_version;
    if (!version) return null;
    return short ? version.split("-")[0] : version;
  }

  // What the Nest's own card shows, from its entities in Home Assistant.
  _nest() {
    const t = this._current;
    const card = this._preview.thermostat;
    if (!card) return null;
    const climate = t.climate && this._hass.states[t.climate];
    const state = (end, domain = "sensor") => {
      const id = own(t, domain, end);
      return id ? this._hass.states[id] : null;
    };
    const now = new Date();
    let eta = null;
    const ttt = state("_time_to_temperature");
    const at = ttt && Date.parse(ttt.state);
    if (at && at > now.getTime() && ["heating", "cooling"].includes(card.action)) {
      const minutes = Math.max(1, Math.ceil((at - now.getTime()) / 60000));
      eta = minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
    }
    let next = null;
    const times = state("_heating_times")?.attributes[DAYS[now.getDay()]];
    if (Array.isArray(times)) {
      const clock = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
      next = times.map((x) => x?.time).filter((x) => typeof x === "string" && x > clock).sort()[0] || null;
    }
    const boost = state("_hot_water_boost", "select")?.state;
    // As the Nest names its own card: the name given here, else "Thermostat".
    const named = { ...card, name: this._draft.device_name.trim() || "Thermostat" };
    return { card: named, eco: climate?.attributes.preset_mode === "eco", eta, next, boost: /^\d+$/.test(boost || "") ? Number(boost) : null };
  }

  _card(id) {
    let card = this._preview.cards[id];
    if (!card) return null;
    if (card.kind === "alarm") {
      // As the integration gives it (cards.alarm): modes left off, named.
      const settings = this._draft.alarms[id] || {};
      const modes = card.modes.filter((m) => !(settings.hidden || []).includes(m.mode))
        .map((m) => ({ ...m, name: (settings.names || {})[m.mode]?.trim() || "" }));
      card = { ...card, modes, code_length: settings.code_length || null };
    }
    // As the integration names it for the Nest (cards.named).
    const name = this._draft.names[id]?.trim();
    if (!name) return card;
    const named = { ...card, name };
    if (card.kind === "lights" && card.lights?.length === 1) named.lights = [{ ...card.lights[0], name }];
    return named;
  }

  _screen(sel) {
    const f = new Face(this._look === "fledge");
    const id = this._draft.order[sel];
    if (id === THERMO) {
      const nest = this._nest();
      if (!this._thermoShown()) FACES.missing(f, this._thermostatName(), this._acting() ? "Hidden: not on the Nest" : "Not a thermostat: no card");
      else if (nest) FACES.nest(f, nest);
      else FACES.missing(f, this._thermostatName(), "Not reporting");
      return f.html();
    }
    const card = this._card(id);
    const state = this._hass.states[id];
    if (!card) FACES.missing(f, this._name(id), "Not in Home Assistant, so not on the Nest");
    else if (state?.state === "unavailable") FACES.missing(f, this._name(id), "Unavailable");
    else {
      const playing = ["playing", "paused"].includes(state?.state);
      const art = card.kind === "camera" ? state?.attributes.entity_picture : card.kind === "media" && playing ? state?.attributes.entity_picture : null;
      (FACES[card.kind] || FACES.action)(f, card, { art, now: Date.now() / 1000 });
    }
    return f.html();
  }

  // Rendering

  _render() {
    // Not while a row is dragged: that would drop it. Drawn as it lands.
    if (!this._hass || this._drag != null) return;
    const t = this._current;
    let body;
    if (!this._thermostats) body = `<div class="empty">Loading…</div>`;
    else if (!t) body = `<div class="empty"><h2>No thermostat yet</h2><p>Add the Fledge integration for your Nest first.</p>
      <a class="btn solid" href="/config/integrations/integration/fledge" data-act="nav" data-href="/config/integrations/integration/fledge">Fledge integration</a></div>`;
    else body = this._tabs() + this._banner() + this._tabBody();
    const scroll = this.shadowRoot.querySelector(".content")?.scrollTop || 0;
    const focus = this._focusKey();
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>${this._toolbar()}<div class="content">${body}</div>${this._saveBar()}${this._adding ? this._addDialog() : ""}${this._roleAsk ? this._roleDialog() : ""}`;
    const content = this.shadowRoot.querySelector(".content");
    if (content) content.scrollTop = scroll;
    this._wirePickers();
    this._restoreFocus(focus);
  }

  // A picker's value and domains can't be attributes: set once drawn.
  _wirePickers() {
    for (const picker of this.shadowRoot.querySelectorAll("ha-entity-picker[data-pick]")) {
      picker.hass = this._hass;
      picker.includeDomains = [picker.dataset.domain];
      if (picker.dataset.class) picker.includeDeviceClasses = [picker.dataset.class];
      picker.value = picker.dataset.value || "";
      picker.label = picker.dataset.label;
      picker.disabled = picker.hasAttribute("data-disabled");
    }
  }

  // An entity choice: Home Assistant's picker when it's loaded, else a list.
  _entityField(key, domain, label, value, disabled = false, deviceClass = "") {
    if (this._pickers) {
      return `<ha-entity-picker data-pick="${key}" data-domain="${domain}" data-class="${esc(deviceClass)}" data-label="${esc(label)}" data-value="${esc(value || "")}" ${disabled ? "data-disabled" : ""}></ha-entity-picker>`;
    }
    const ids = Object.keys(this._hass.states).filter((id) => id.startsWith(`${domain}.`)
      && (!deviceClass || this._hass.states[id].attributes.device_class === deviceClass)).sort();
    return `<div class="field"><label for="pick-${key}">${esc(label)}</label><select id="pick-${key}" class="pick wide" data-input="pick" data-key="${key}" ${disabled ? "disabled" : ""}>
      <option value="">None</option>${ids.map((id) => `<option value="${esc(id)}" ${value === id ? "selected" : ""}>${esc(this._hass.states[id].attributes.friendly_name || id)}</option>`).join("")}</select></div>`;
  }

  _focusKey() {
    const el = this.shadowRoot.activeElement;
    if (!el) return null;
    return { id: el.id, act: el.dataset?.act, i: el.dataset?.i, start: el.selectionStart, end: el.selectionEnd };
  }

  _restoreFocus(f) {
    if (!f) return;
    let el = f.id ? this.shadowRoot.getElementById(f.id) : null;
    if (!el && f.act) el = this.shadowRoot.querySelector(`[data-act="${f.act}"]${f.i != null ? `[data-i="${f.i}"]` : ""}`);
    if (!el) return;
    el.focus();
    if (f.start != null && el.setSelectionRange) {
      try { el.setSelectionRange(f.start, f.end); } catch (_) { /* not a text field */ }
    }
  }

  _toolbar() {
    const t = this._current;
    const menu = this._narrow ? `<button class="icon" data-act="menu" aria-label="Sidebar"><ha-icon icon="mdi:menu"></ha-icon></button>` : "";
    let picker = "";
    if (this._thermostats?.length > 1) {
      picker = `<label class="hidden" for="which">Thermostat</label><select id="which" class="pick" data-input="which">${this._thermostats
        .map((x, i) => `<option value="${i}" ${i === this._index ? "selected" : ""}>${esc(x.name || x.title)}</option>`).join("")}</select>`;
    } else if (t) picker = `<span class="muted">${esc(t.name || t.title)}</span>`;
    let status = "";
    if (t) {
      const climate = t.climate && this._hass.states[t.climate];
      const online = climate && climate.state !== "unavailable";
      const build = this._build(true);
      status = `<span class="pill ${online ? "ok" : "bad"}"><span class="dotlet"></span>${online ? "Online" : "Offline"}${build ? ` · Fledge ${esc(build)}` : ""}</span>`;
    }
    const wake = t?.entities?.wake_screen ? `<button class="btn ghost" data-act="wakenow">Wake screen</button>` : "";
    return `<header class="toolbar">${menu}<h1>Fledge</h1>${picker}<span class="grow"></span>${status}${wake}</header>`;
  }

  _tabs() {
    const tabs = [["cards", "Cards"], ["wake", "Wake & presence"], ["notify", "Notifications"], ["thermostat", "Thermostat"]];
    return `<nav class="tabs" role="tablist">${tabs.map(([k, label]) =>
      `<button role="tab" class="tab ${k === this._tab ? "on" : ""}" aria-selected="${k === this._tab}" data-act="tab" data-tab="${k}">${esc(label)}</button>`).join("")}</nav>`;
  }

  _banner() {
    const t = this._current;
    const out = [];
    if (this._error) out.push(`<div class="banner bad">${esc(this._error)}</div>`);
    if (this._notice) out.push(`<div class="banner ok">${esc(this._notice)}</div>`);
    if (!t.loaded) out.push(`<div class="banner bad">Fledge isn't running for this thermostat (its setup failed or it's disabled), so changes won't reach the Nest until it is.</div>`);
    else if (t.protocol != null && t.protocol < t.needs) out.push(`<div class="banner warn">The Nest runs an older Fledge build than this integration needs: some cards may not work until it updates (Thermostat tab).</div>`);
    return out.join("");
  }

  _tabBody() {
    if (this._tab === "wake") return this._wakeTab();
    if (this._tab === "notify") return this._notifyTab();
    if (this._tab === "thermostat") return this._thermostatTab();
    return this._cardsTab();
  }

  _cardsTab() {
    const order = this._draft.order;
    const rows = order.map((id, i) => this._row(i, id)).join("");
    return `<div class="deck">
      <section class="card list" aria-label="Cards in ring order">
        <div class="head"><h2>Ring order</h2><span class="muted small">${order.length} cards · drag to reorder</span></div>
        <div class="rows">${rows}</div>
        <button class="add" data-act="add"><ha-icon icon="mdi:plus"></ha-icon>Add a card</button>
        <p class="muted small foot"><ha-icon icon="mdi:cog"></ha-icon>Settings always comes last on the Nest.</p>
      </section>
      ${this._previewSection()}
      ${this._detail()}
    </div>`;
  }

  _row(i, id) {
    const t = this._current;
    const mine = id === THERMO;
    const state = this._hass.states[mine ? t.climate : id];
    const active = state && ACTIVE.includes(state.state);
    const missing = !mine && !state;
    const unavailable = state?.state === "unavailable";
    const badge = missing ? `<span class="chip bad">Not found</span>` : unavailable ? `<span class="chip">Unavailable</span>`
      : mine && !this._acting() ? `<span class="chip warm">Not a thermostat</span>`
      : mine && !this._draft.show_thermostat ? `<span class="chip">Hidden</span>`
      : mine ? `<span class="chip">This Nest</span>` : this._draft.wake_device === id ? `<span class="chip warm">Wakes here</span>` : "";
    return `<div class="row ${i === this._sel ? "sel" : ""} ${mine && !this._thermoShown() ? "off" : ""}" data-row="${i}" draggable="true">
      <span class="grip" aria-hidden="true"><ha-icon icon="mdi:drag-vertical"></ha-icon></span>
      <button class="pickrow" data-act="select" data-i="${i}" aria-label="${esc(this._name(id))}" aria-current="${i === this._sel}">
        <span class="ico ${active ? "active" : ""}"><ha-icon icon="${esc(this._icon(id))}"></ha-icon></span>
        <span class="text"><span class="name">${esc(this._name(id))}</span><span class="sub">${esc(mine ? "Thermostat" : id)}</span></span>
        ${badge}
      </button>
    </div>`;
  }

  _previewSection() {
    const total = this._draft.order.length;
    const dots = total <= 16 ? Array.from({ length: total }, (_, i) => `<span class="dot ${i === this._sel ? "on" : ""}"></span>`).join("")
      : `<span class="muted small">${this._sel + 1} of ${total}</span>`;
    const look = (k, label) => `<button class="seg ${this._look === k ? "on" : ""}" data-act="look" data-look="${k}" aria-pressed="${this._look === k}">${label}</button>`;
    return `<section class="stage" aria-label="As the Nest shows it">
      <span class="muted caps">On the Nest</span>
      <div class="bezel"><div class="screen">${this._screen(this._sel)}</div></div>
      <div class="dots">${dots}</div>
      <div class="ring">
        <button class="ringbtn" data-act="turn" data-by="-1" aria-label="Turn the ring left"><ha-icon icon="mdi:chevron-left"></ha-icon></button>
        <span class="muted small center">Turn the ring to try your order</span>
        <button class="ringbtn" data-act="turn" data-by="1" aria-label="Turn the ring right"><ha-icon icon="mdi:chevron-right"></ha-icon></button>
      </div>
      <div class="segs" role="group" aria-label="The Nest's look">${look("nest", "Nest look")}${look("fledge", "Fledge look")}</div>
    </section>`;
  }

  _moves() {
    const i = this._sel;
    return `<button class="btn ghost" data-act="move" data-by="-1" ${i === 0 ? "disabled" : ""} aria-label="Move up"><ha-icon icon="mdi:arrow-up"></ha-icon></button>
      <button class="btn ghost" data-act="move" data-by="1" ${i === this._draft.order.length - 1 ? "disabled" : ""} aria-label="Move down"><ha-icon icon="mdi:arrow-down"></ha-icon></button>`;
  }

  _detail() {
    const t = this._current;
    const id = this._draft.order[this._sel];
    if (id === THERMO) {
      const old = t.protocol != null && t.protocol < ANYWHERE && this._place() > 0;
      return `<section class="card detail" aria-label="Selected card">
        <div class="who"><span class="ico big active"><ha-icon icon="mdi:thermostat"></ha-icon></span>
          <span class="text"><span class="name big">${esc(this._thermostatName())}</span><span class="sub">This Nest's own card</span></span></div>
        ${this._acting()
          ? `<div class="kv"><span>Show on the Nest<span class="sub">${this._draft.show_thermostat ? "Turn it to set the temperature, as the Nest always did" : "Hidden: the Nest still heats, set from Home Assistant"}</span></span>${this._switch("showthermo", this._draft.show_thermostat, "Show on the Nest")}</div>`
          : `<div class="banner warn inline">This Nest isn't acting as a thermostat, so it has no card of its own. Turn it back on in the Thermostat tab.</div>`}
        ${this._nameField("cardname")}
        ${this._acting() ? `<div><div class="muted small">On the ring</div>
          ${THERMOSTAT_RING.map(([k, v]) => `<div class="kv"><span class="muted">${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}</div>` : ""}
        ${old ? `<div class="banner warn inline">The Nest's Fledge build keeps it first until it updates (Thermostat tab).</div>` : ""}
        <div class="actions">${this._moves()}
          ${t.climate ? `<button class="btn ghost" data-act="more" data-id="${esc(t.climate)}">Open</button>` : ""}</div>
      </section>`;
    }
    const domain = domainOf(id);
    const friendly = this._hass.states[id]?.attributes.friendly_name || id;
    const wakes = this._draft.wake_device === id;
    return `<section class="card detail" aria-label="Selected card">
      <div class="who"><span class="ico big"><ha-icon icon="${esc(this._icon(id))}"></ha-icon></span>
        <span class="text"><span class="name big">${esc(this._name(id))}</span><span class="sub">${esc(KINDS[domain] || domain)} · ${esc(id)}</span></span></div>
      <div class="field"><label for="cardname">Name on the Nest</label>
        <input id="cardname" type="text" maxlength="40" placeholder="${esc(friendly)}" value="${esc(this._draft.names[id] || "")}" data-input="name"></div>
      <div><div class="muted small">On the ring</div>
        ${(RING[domain] || []).map(([k, v]) => `<div class="kv"><span class="muted">${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}</div>
      ${domain === "alarm_control_panel" ? this._alarmSettings(id) : ""}
      <div class="kv"><span>Wake straight to this card</span>${this._switch("wakehere", wakes, "Wake straight to this card")}</div>
      <div class="actions">${this._moves()}
        <button class="btn ghost" data-act="more" data-id="${esc(id)}">Open</button>
        <span class="grow"></span>
        <button class="btn text bad" data-act="remove">Remove</button>
      </div>
    </section>`;
  }

  // An alarm's settings: its modes on the ring, each named as on the Nest,
  // and how long its code is. Which modes it has, and when it wants a code,
  // are the alarm's.
  _alarmSettings(id) {
    const state = this._hass.states[id];
    const a = state?.attributes || {};
    const settings = this._draft.alarms[id] || {};
    const hidden = settings.hidden || [];
    const has = ALARM_MODES.filter(([mode]) => this._preview.cards[id]?.modes?.some((m) => m.mode === mode));
    const missing = ALARM_MODES.filter(([mode]) => !has.some(([m]) => m === mode)).map(([, name]) => name);
    const alarmo = "arm_mode" in a && "open_sensors" in a;
    const rows = has.map(([mode, name]) => {
      const shown = !hidden.includes(mode);
      return `<div class="moderow ${shown ? "" : "off"}">
        ${this._switch("alarmshow", shown, `Show ${name}`).replace("data-act=", `data-mode="${mode}" data-act=`)}
        <span class="muted small tag">${esc(name)}</span>
        <input id="alarmname-${mode}" type="text" maxlength="24" placeholder="${esc(name)}" aria-label="Name for ${esc(name)} on the Nest"
          value="${esc((settings.names || {})[mode] || "")}" data-input="alarmname" data-mode="${mode}"></div>`;
    }).join("");
    const lengths = [["", "Any length: a ✓ after 0 sends it"], ...[4, 5, 6, 7, 8].map((n) => [String(n), `${n} digits: the last sends it`])];
    return `<div class="group">
        <div class="muted small">Modes on the ring</div>
        <p class="muted small">After Back, in this order. A name shows on the ring and as “Armed · name” on the card; blank keeps Home Assistant's.</p>
        ${rows || `<p class="muted small">This alarm has no modes to arm to.</p>`}
        ${missing.length ? `<p class="muted small">${esc(missing.join(", "))}: not offered by this alarm.</p>` : ""}
        ${alarmo ? `<div><button class="btn ghost" data-act="alarmcopy">Copy names from its Alarmo card</button></div>` : ""}
      </div>
      <div class="group">
        <div class="muted small">Code</div>
        <div class="field"><label for="alarmlength">How long is the code?</label>
          <select id="alarmlength" class="pick wide" data-input="alarmlength">${lengths.map(([v, l]) =>
            `<option value="${v}" ${String(settings.code_length || "") === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>
        <p class="muted small">The Nest asks for a code whenever the alarm wants one, and it checks it; Fledge never keeps it. Three wrong codes lock the code pad for 30 s, longer each time.</p>
        ${a.code_format === "text" ? `<div class="banner warn inline">This alarm's code has letters, which the ring can't enter: disarm it from Home Assistant.</div>` : ""}
      </div>
      <div class="kv"><span class="text"><span class="name">Take over the screen</span>
          <span class="sub">When someone comes in (the entry delay) or it goes off, the Nest lights up on it; the code pad first, if it needs a code</span></span>
        ${this._switch("alarmtakeover", settings.takeover !== false, "Take over the screen")}</div>
      <div class="banner ${alarmo ? "ok" : "warn"} inline">${alarmo
        ? "Alarmo: countdowns in seconds, the sensor that set it off, and “Arm anyway” with a door open."
        : "This alarm doesn't say how long its delays are, so the ring shows time running without the seconds."}</div>`;
  }

  // An Alarmo card for this alarm on a dashboard: its buttons' labels and
  // hidden modes (its `states` option) as the Nest's.
  async _copyAlarmo(id) {
    const found = [];
    const look = (node) => {
      if (Array.isArray(node)) node.forEach(look);
      else if (node && typeof node === "object") {
        if (node.type === "custom:alarmo-card" && node.entity === id) found.push(node);
        Object.values(node).forEach(look);
      }
    };
    try {
      const dashboards = await this._hass.callWS({ type: "lovelace/dashboards/list" });
      for (const url of [null, ...dashboards.filter((d) => d.mode === "storage").map((d) => d.url_path)]) {
        try { look(await this._hass.callWS({ type: "lovelace/config", url_path: url })); } catch (_) { /* not one with cards */ }
        if (found.length) break;
      }
    } catch (err) {
      this._error = err?.message || String(err);
      this._render();
      return;
    }
    const states = found[0]?.states;
    if (!states) {
      this._flash("No Alarmo card for this alarm on a dashboard.");
      return;
    }
    const settings = (this._draft.alarms[id] ||= {});
    settings.names = { ...(settings.names || {}) };
    settings.hidden = [];
    for (const [mode] of ALARM_MODES) {
      const own = states[`armed_${mode}`];
      if (own?.button_label) settings.names[mode] = String(own.button_label).slice(0, 24);
      if (own?.hide) settings.hidden.push(mode);
    }
    this._flash("Copied its Alarmo card's names.");
    this._render();
  }

  // The thermostat's name in Home Assistant (its device's), blank for its own.
  _nameField(key) {
    const t = this._current;
    return `<div class="field"><label for="${key}">Thermostat's name</label>
      <input id="${key}" type="text" maxlength="60" placeholder="${esc(t.default_name || "Nest Thermostat")}" value="${esc(this._draft.device_name)}" data-input="devicename"></div>`;
  }

  _switch(act, on, label) {
    return `<button class="switch ${on ? "on" : ""}" role="switch" aria-checked="${on}" aria-label="${esc(label)}" data-act="${act}"><span class="knob"></span></button>`;
  }

  _presence(group, k, label, sub) {
    const on = this._draft[group] === k;
    return `<label class="opt ${on ? "on" : ""}">
      <input type="radio" name="${group}" value="${k}" ${on ? "checked" : ""} data-input="${group}">
      <span class="text"><span class="name">${esc(label)}</span><span class="sub">${esc(sub)}</span></span></label>`;
  }

  _wakeTab() {
    const d = this._draft;
    const t = this._current;
    const entities = this._entities();
    const keep = t.entities?.keep_screen_on;
    const keepOn = keep && this._hass.states[keep]?.state === "on";
    const device = entities.includes(d.wake_device) ? d.wake_device : null;
    const chosen = device ? "card" : d.wake;
    const option = (k, label, sub, extra = "") => `<label class="opt ${chosen === k ? "on" : ""}">
      <input type="radio" name="wake" value="${k}" ${chosen === k ? "checked" : ""} ${k === "card" && !entities.length ? "disabled" : ""} data-input="wake">
      <span class="text"><span class="name">${esc(label)}</span><span class="sub">${esc(sub)}</span>${extra}</span></label>`;
    const chips = chosen === "card" ? `<span class="chips" role="radiogroup" aria-label="Which card">${entities.map((id) =>
      `<button class="chipbtn ${id === device ? "on" : ""}" role="radio" aria-checked="${id === device}" data-act="wakecard" data-id="${esc(id)}">${esc(this._name(id))}</button>`).join("")}</span>` : "";
    return `<div class="cols">
      <section class="card pad">
        <h2>When the Nest wakes</h2>
        <p class="muted small">You walk up, turn the ring or press it while the screen is dark.</p>
        ${WAKES.map(([k, label, sub]) => option(k, label, sub)).join("")}
        ${option("card", "One of your cards", entities.length ? "Opened, ready to turn" : "Add a card first", chips)}
      </section>
      <section class="card pad">
        <h2>Someone across the room</h2>
        <p class="muted small">Fledge keeps the Nest's Farsight on, so its motion sensor always reports at once. Stay dark keeps the screen fully dark.</p>
        ${FARS.map(([k, label, sub]) => this._presence("far", k, label, sub)).join("")}
        <h2>Someone comes up to it</h2>
        <p class="muted small">Close enough to reach it, without touching it.</p>
        ${NEARS.map(([k, label, sub]) => this._presence("near", k, label, sub)).join("")}
        <div class="field narrowfield"><label for="cardidle">Screen stays on for (s)</label><input id="cardidle" type="number" min="5" max="600" value="${esc(d.card_idle)}" data-input="cardidle"></div>
        <div class="kv"><span>Turning a light down past 1% turns it off<span class="sub">The ring holds at 1% for a moment first. A switch turns off to the left and on to the right.</span></span>${this._switch("dimoff", d.dim_off, "Turning a light down past 1% turns it off")}</div>
        <p class="muted small">The glance's weather is chosen on the Thermostat tab.</p>
        ${keep ? `<div class="kv"><span>Keep screen on<span class="sub">Applies straight away</span></span>${this._switch("keepon", keepOn, "Keep screen on")}</div>` : ""}
      </section>
    </div>`;
  }

  _notifyTab() {
    const n = this._note;
    const yaml = ["action: fledge.notify", "data:", n.title ? `  title: ${n.title}` : null, `  message: ${n.message}`,
      n.camera ? `  camera: ${n.camera}` : null, `  timeout: ${n.timeout}`].filter((l) => l != null).join("\n");
    return `<div class="cols">
      <section class="card pad">
        <h2>Try a notification</h2>
        <p class="muted small">It shows on every Nest running Fledge. A press or a dismissal fires <code>fledge_notification</code>.</p>
        <div class="field"><label for="ntitle">Title</label><input id="ntitle" type="text" value="${esc(n.title)}" data-input="note" data-key="title"></div>
        <div class="field"><label for="nmsg">Message</label><input id="nmsg" type="text" value="${esc(n.message)}" data-input="note" data-key="message"></div>
        <div class="split">
          <div class="field">${this._entityField("camera", "camera", "Camera picture", n.camera)}</div>
          <div class="field narrowfield"><label for="nto">Shows for (s)</label><input id="nto" type="number" min="3" max="3600" value="${esc(n.timeout)}" data-input="note" data-key="timeout"></div>
        </div>
        <div class="actions"><button class="btn solid" data-act="send" ${n.message.trim() ? "" : "disabled"}>Send to the Nest</button>
          <button class="btn ghost" data-act="copy">Copy as automation action</button></div>
      </section>
      <section class="card pad">
        <h2>In an automation</h2>
        <pre><code>${esc(yaml)}</code></pre>
        <p class="muted small">Add <code>tag</code> to dismiss it later with <code>fledge.dismiss</code>.</p>
      </section>
    </div>`;
  }

  _thermostatTab() {
    const t = this._current;
    const update = t.update && this._hass.states[t.update];
    const check = own(t, "button", "check_update");
    const protocol = t.protocol == null ? "Not reported yet" : t.protocol >= t.needs ? `${t.protocol} · matches` : `${t.protocol} · needs ${t.needs}`;
    let updateRow = "";
    if (update) {
      const available = update.state === "on";
      const busy = update.attributes.in_progress;
      updateRow = `<div class="kv"><span>Fledge update<span class="sub">${busy ? "Installing…" : available ? `${esc(update.attributes.latest_version)} available` : "Up to date"}</span></span>
        <span class="actions">${check ? `<button class="btn ghost" data-act="check">Check for updates</button>` : ""}
        ${available ? `<button class="btn solid" data-act="install" ${busy ? "disabled" : ""}>Install</button>` : `<button class="btn ghost" data-act="more" data-id="${esc(t.update)}">Details</button>`}</span></div>`;
    }
    const device = t.device_id ? `/config/devices/device/${t.device_id}` : null;
    return `<div class="cols">
      <section class="card pad">
        <h2>${esc(this._thermostatName())}</h2>
        ${this._nameField("devname")}
        <div class="kv"><span class="muted">Serial</span><span>${esc(t.serial)}</span></div>
        <div class="kv"><span class="muted">Fledge on the Nest<span class="sub">nestlocal</span></span><span class="mono">${esc(this._build() || "–")}</span></div>
        <div class="kv"><span class="muted">Nest software<span class="sub">nlclient</span></span><span>${esc(t.sw_version || "–")}</span></div>
        <div class="kv"><span class="muted">Card protocol</span><span class="${t.protocol != null && t.protocol < t.needs ? "badtext" : ""}">${esc(protocol)}</span></div>
        <div class="kv"><span class="muted">Integration</span><span>${esc(t.version || "–")}</span></div>
        ${updateRow}
        <div class="actions">
          ${device ? `<a class="btn ghost" href="${device}" data-act="nav" data-href="${device}">Heating, hot water and sensors</a>` : ""}
          <a class="btn ghost" href="/config/integrations/integration/fledge" data-act="nav" data-href="/config/integrations/integration/fledge">Integration</a>
        </div>
      </section>
      ${this._role()}
      ${this._temperatures()}
    </div>`;
  }

  // Whether the Nest is a thermostat at all, or a panel on the wall with its
  // heating off.
  _role() {
    const t = this._current;
    const id = t.role_switch;
    const acting = this._acting();
    const body = !id
      ? `<p class="muted small">Update Fledge on the Nest to use it only as a panel, without its thermostat.</p>`
      : acting
        ? `<p class="muted small">Using something else for your heating? Turn this off to keep the Nest as a panel for your cards. Its heating ${this._hotWater() ? "and hot water switch" : "switches"} off, and it stops being a thermostat in Home Assistant.</p>`
        : `<div class="banner warn inline"><span><b>Not a thermostat.</b> Its heating is off (its hot water too, if it has it), and Home Assistant has only its sensors. Turn this back on to heat again as before.</span></div>`;
    return `<section class="card pad">
        <h2>Thermostat</h2>
        ${id ? `<div class="kv"><span>Act as a thermostat<span class="sub">${acting ? "On: it controls your heating" : "Off: heating off, panel only"}</span></span>${this._switch("role", acting, "Act as a thermostat")}</div>` : ""}
        ${body}
        ${this._wwn()}
      </section>`;
  }

  // A Heat Link model heating without its Heat Link (nestlocal's
  // using_wwn_boiler), switched with a reboot.
  _wwn() {
    const id = this._current.wwn_switch;
    if (!id) return "";
    const on = this._hass.states[id]?.state === "on";
    return `<div class="kv"><span>Works With Nest boiler<span class="sub">${on ? "On: heats through its own W1 wire, no Heat Link" : "Off: heats through its Heat Link"}</span></span>${this._switch("wwn", on, "Works With Nest boiler")}</div>
      <p class="muted small">For a Nest with no Heat Link, or a boiler wired straight to the Nest's own base. Without it, a Nest that can't find its Heat Link can stop with error H71 or H74. Changing it reboots the Nest.</p>`;
  }

  _hotWater() {
    return (this._current.own || []).some((e) => e.endsWith("_hot_water_schedule") || e.endsWith("_hot_water"));
  }

  // What turning the switch does, said before it does it.
  _roleDialog() {
    const off = this._acting();
    const water = this._hotWater();
    const name = esc(this._thermostatName());
    const items = off ? [
      `Its heating${water ? " and hot water turn" : " turns"} <b>off</b> now, and nothing can turn ${water ? "them" : "it"} back on while it's off. Its safety temperatures still protect against frost.`,
      "Home Assistant loses its thermostat: the climate entity, schedules, Eco, Auto-Schedule, Home/Away and the other heating controls. Automations and dashboards that use them stop working.",
      "Its card and heating settings go from the Nest's screen. Your other cards stay.",
      "Its temperature and humidity sensors stay, with its motion, light and power sensors.",
    ] : [
      "Its heating goes back to the mode it was in (its hot water schedule too, if it had one), and it heats again if that calls for it.",
      "Home Assistant gets its thermostat and heating controls back, with the same entity IDs.",
      "Its card and heating settings come back on the Nest's screen.",
    ];
    return `<div class="scrim" data-act="closerole"></div>
      <div class="dialog" role="alertdialog" aria-modal="true" aria-labelledby="roletitle">
        <div class="head"><h2 id="roletitle">${off ? `Stop ${name} acting as a thermostat?` : `Make ${name} a thermostat again?`}</h2>
          <button class="icon" data-act="closerole" aria-label="Close"><ha-icon icon="mdi:close"></ha-icon></button></div>
        <ul class="role">${items.map((i) => `<li>${i}</li>`).join("")}</ul>
        <div class="actions"><span class="grow"></span>
          <button class="btn text" data-act="closerole">Cancel</button>
          <button class="btn solid ${off ? "danger" : ""}" data-act="roleconfirm">${off ? "Turn heating off" : "Turn heating back on"}</button></div>
      </div>`;
  }

  // The weather the Nest shows (its own screen and the glance), and a room
  // thermometer it can regulate from.
  _temperatures() {
    const d = this._draft;
    const can = this._current.room_sensor;
    const option = this._current.room_sensor_switch;
    const optionOn = option && this._hass.states[option]?.state === "on";
    return `<section class="card pad">
        <h2>Weather and temperature</h2>
        <p class="muted small">The Nest's own weather screen and the glance show this weather.</p>
        ${this._entityField("weather", "weather", "Weather", d.weather)}
        ${this._entityField("outdoor_temperature", "sensor", "Outside thermometer (optional)", d.outdoor_temperature, false, "temperature")}
        <h2>Room thermometer</h2>
        <p class="muted small">The Nest regulates from this sensor instead of its own, and goes back to its own if the readings stop.
        ${can ? "" : " It needs the Nest's external temperature option, which turns off its Bluetooth: real Nest Temperature Sensors stop working."}</p>
        ${option ? `<div class="kv"><span>External temperature option<span class="sub">${optionOn === can ? "Reboots the Nest when changed" : "Rebooting the Nest into it…"}</span></span>${this._switch("extoption", optionOn, "External temperature option")}</div>` : ""}
        ${this._entityField("room_sensor", "sensor", "Room thermometer (optional)", d.room_sensor, false, "temperature")}
        ${d.room_sensor && !can ? `<p class="badtext small">This Nest runs without the external temperature option, so it ignores the readings for now.${option ? "" : " Update its Fledge to turn the option on from here."}</p>` : ""}
      </section>`;
  }

  _saveBar() {
    if (!this._dirty && !this._busy) return "";
    return `<div class="savebar" role="region" aria-label="Unsaved changes"><span>Unsaved changes</span><span class="grow"></span>
      <button class="btn text" data-act="revert" ${this._busy ? "disabled" : ""}>Revert</button>
      <button class="btn solid" data-act="save" ${this._busy ? "disabled" : ""}>${this._busy ? "Saving…" : "Save"}</button></div>`;
  }

  _candidates() {
    const mine = new Set(this._current.own || []);
    const taken = new Set(this._entities());
    const q = this._query.trim().toLowerCase();
    return Object.values(this._hass.states)
      .filter((s) => this._domains.includes(domainOf(s.entity_id)) && !mine.has(s.entity_id) && !taken.has(s.entity_id))
      .map((s) => ({ id: s.entity_id, name: s.attributes.friendly_name || s.entity_id, icon: s.attributes.icon || ICONS[domainOf(s.entity_id)] }))
      .filter((c) => !q || c.name.toLowerCase().includes(q) || c.id.includes(q))
      .sort((a, b) => a.name.localeCompare(b.name))
      .slice(0, 80);
  }

  _addDialog() {
    const list = this._candidates();
    return `<div class="scrim" data-act="closeadd"></div>
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="addtitle">
        <div class="head"><h2 id="addtitle">Add a card</h2><button class="icon" data-act="closeadd" aria-label="Close"><ha-icon icon="mdi:close"></ha-icon></button></div>
        <label class="hidden" for="search">Search</label>
        <input id="search" type="search" placeholder="Lights, speakers, cameras, scenes…" value="${esc(this._query)}" data-input="search" autocomplete="off">
        <div class="results">${list.length ? list.map((c) => `<button class="pickrow" data-act="addone" data-id="${esc(c.id)}">
            <span class="ico"><ha-icon icon="${esc(c.icon)}"></ha-icon></span>
            <span class="text"><span class="name">${esc(c.name)}</span><span class="sub">${esc(c.id)}</span></span>
            <span class="muted small">${esc(KINDS[domainOf(c.id)] || "")}</span></button>`).join("")
          : `<p class="muted center">Nothing else can be a card.</p>`}</div>
      </div>`;
  }

  // Events

  _click(e) {
    const el = e.target.closest("[data-act]");
    if (!el || el.disabled) return;
    const act = el.dataset.act;
    const d = this._draft;
    switch (act) {
      case "tab": this._tab = el.dataset.tab; break;
      case "select": this._sel = Number(el.dataset.i); break;
      case "turn": {
        const total = d.order.length;
        this._sel = (this._sel + Number(el.dataset.by) + total) % total;
        break;
      }
      case "move": this._move(this._sel, this._sel + Number(el.dataset.by)); break;
      case "remove": {
        const id = d.order[this._sel];
        if (id === THERMO) return;
        d.order.splice(this._sel, 1);
        if (d.wake_device === id) d.wake_device = null;
        this._sel = Math.min(this._sel, d.order.length - 1);
        break;
      }
      case "wakehere": {
        const id = d.order[this._sel];
        d.wake_device = d.wake_device === id ? null : id;
        break;
      }
      case "wakecard": d.wake_device = el.dataset.id; break;
      case "look":
        this._look = el.dataset.look;
        try { localStorage.setItem(LOOK_KEY, this._look); } catch (_) { /* only for now */ }
        break;
      case "add": this._adding = true; this._query = ""; this._render(); this.shadowRoot.getElementById("search")?.focus(); return;
      case "closeadd": this._adding = false; break;
      case "addone": {
        d.order.push(el.dataset.id);
        this._adding = false;
        this._sel = d.order.length - 1;
        this._refresh();
        return;
      }
      case "save": this._save(); return;
      case "revert": this._reset(true); this._refresh(); return;
      case "more":
        this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: el.dataset.id }, bubbles: true, composed: true }));
        return;
      case "nav":
        e.preventDefault();
        history.pushState(null, "", el.dataset.href);
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
        return;
      case "menu": this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })); return;
      case "wakenow": this._hass.callService("button", "press", { entity_id: this._current.entities.wake_screen }); return;
      case "dimoff": this._draft.dim_off = !this._draft.dim_off; this._render(); return;
      case "showthermo": this._draft.show_thermostat = !this._draft.show_thermostat; this._render(); return;
      case "role": this._roleAsk = true; this._render(); this.shadowRoot.querySelector('[data-act="roleconfirm"]')?.focus(); return;
      case "closerole": this._roleAsk = false; break;
      case "roleconfirm": {
        const off = this._acting();
        this._roleAsk = false;
        this._hass.callService("switch", off ? "turn_off" : "turn_on", { entity_id: this._current.role_switch })
          .then(() => this._flash(off ? "The Nest's heating is off: it's a panel now." : "The Nest is a thermostat again."),
            (err) => { this._error = err?.message || String(err); this._render(); });
        break;
      }
      case "alarmshow": {
        const id = d.order[this._sel];
        const settings = (d.alarms[id] ||= {});
        const hidden = new Set(settings.hidden || []);
        if (hidden.has(el.dataset.mode)) hidden.delete(el.dataset.mode);
        else hidden.add(el.dataset.mode);
        settings.hidden = ALARM_MODES.map(([m]) => m).filter((m) => hidden.has(m));
        break;
      }
      case "alarmcopy": this._copyAlarmo(d.order[this._sel]); return;
      case "alarmtakeover": {
        const settings = (d.alarms[d.order[this._sel]] ||= {});
        settings.takeover = settings.takeover === false;
        break;
      }
      case "keepon": this._hass.callService("switch", "toggle", { entity_id: this._current.entities.keep_screen_on }); return;
      case "wwn": {
        const id = this._current.wwn_switch;
        const on = this._hass.states[id]?.state === "on";
        const ask = on
          ? "Turn Works With Nest boiler off?\n\nThe Nest goes back to heating through its Heat Link. If it has no Heat Link it may stop with error H71 or H74.\n\nThe Nest reboots now, and its heating pauses for about a minute."
          : "Turn Works With Nest boiler on?\n\nThe Nest stops using its Heat Link and switches the boiler itself, through the W1 wire on its own base. Only do this if the Nest has no Heat Link, or the boiler is wired to the Nest's base: a boiler wired to a Heat Link won't come on.\n\nThe Nest reboots now, and its heating pauses for about a minute.";
        if (!confirm(ask)) return;
        this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: id })
          .then(() => this._flash("The Nest is rebooting into it."), (err) => { this._error = err?.message || String(err); this._render(); });
        return;
      }
      case "extoption": {
        const id = this._current.room_sensor_switch;
        const on = this._hass.states[id]?.state === "on";
        const ask = on
          ? "Turn the external temperature option off? The Nest reboots (its heating pauses for about a minute) and goes back to its own sensor and Bluetooth."
          : "Turn the external temperature option on? The Nest reboots (its heating pauses for about a minute) and its Bluetooth turns off, so real Nest Temperature Sensors stop working.";
        if (!confirm(ask)) return;
        this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: id })
          .then(() => this._flash("The Nest is rebooting into it."), (err) => { this._error = err?.message || String(err); this._render(); });
        return;
      }
      case "install": this._hass.callService("update", "install", { entity_id: this._current.update }); return;
      case "check":
        this._hass.callService("button", "press", { entity_id: own(this._current, "button", "check_update") })
          .then(() => this._flash("Asked the Nest to look for an update."), (err) => { this._error = err?.message || String(err); this._render(); });
        return;
      case "send": this._send(); return;
      case "copy": navigator.clipboard?.writeText(this.shadowRoot.querySelector("pre code")?.textContent || ""); this._flash("Copied."); return;
      default: return;
    }
    this._render();
  }

  _input(e) {
    const el = e.target;
    const what = el.dataset?.input;
    if (!what) return;
    const d = this._draft;
    if (what === "name") {
      d.names[d.order[this._sel]] = el.value;
      this._render();
    } else if (what === "alarmname") {
      const settings = (d.alarms[d.order[this._sel]] ||= {});
      settings.names = { ...(settings.names || {}), [el.dataset.mode]: el.value };
      this._render();
    } else if (what === "alarmlength" && e.type === "change") {
      const settings = (d.alarms[d.order[this._sel]] ||= {});
      settings.code_length = Number(el.value) || null;
      this._render();
    } else if (what === "devicename") {
      d.device_name = el.value;
      this._render();
    } else if (what === "search") {
      this._query = el.value;
      const results = this.shadowRoot.querySelector(".results");
      if (results) {
        // Just the results: the field keeps its caret.
        const tmp = document.createElement("div");
        tmp.innerHTML = this._addDialog();
        results.replaceWith(tmp.querySelector(".results"));
      }
    } else if (what === "wake" && e.type === "change") {
      if (el.value === "card") d.wake_device = this._entities().includes(d.wake_device) ? d.wake_device : this._entities()[0] || null;
      else {
        d.wake = el.value;
        d.wake_device = null;
      }
      this._render();
    } else if ((what === "far" || what === "near") && e.type === "change") {
      d[what] = el.value;
      this._render();
    } else if (what === "cardidle" && e.type === "change") {
      d.card_idle = Math.max(5, Math.min(600, Math.round(Number(el.value)) || 20));
      this._render();
    } else if (what === "pick" && e.type === "change") {
      this._choose(el.dataset.key, el.value);
    } else if (what === "which" && e.type === "change") {
      if (this._dirty && !confirm("Leave this thermostat's unsaved changes?")) {
        el.value = String(this._index);
        return;
      }
      this._index = Number(el.value);
      this._reset();
      this._refresh();
    } else if (what === "note") {
      const key = el.dataset.key;
      this._note[key] = key === "timeout" ? Number(el.value) || 30 : el.value;
      if (e.type === "change" || key !== "timeout") this._noteChanged();
    }
  }

  _picked(e) {
    const key = e.target?.dataset?.pick;
    if (!key) return;
    e.stopPropagation();
    this._choose(key, e.detail?.value || "");
  }

  _choose(key, value) {
    if (["weather", "outdoor_temperature", "room_sensor"].includes(key)) {
      this._draft[key] = value || null;
      this._render();
    } else if (key === "camera") {
      this._note.camera = value || "";
      this._noteChanged();
    }
  }

  _noteChanged() {
    const pre = this.shadowRoot.querySelector("pre");
    const send = this.shadowRoot.querySelector('[data-act="send"]');
    if (pre) {
      const tmp = document.createElement("div");
      tmp.innerHTML = this._notifyTab();
      pre.replaceWith(tmp.querySelector("pre"));
    }
    if (send) send.disabled = !this._note.message.trim();
  }

  _key(e) {
    if (e.key === "Escape" && (this._adding || this._roleAsk)) {
      this._adding = false;
      this._roleAsk = false;
      this._render();
      return;
    }
    const row = e.target.closest?.('[data-act="select"]');
    if (row && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      const i = Number(row.dataset.i);
      e.preventDefault();
      const to = i + (e.key === "ArrowUp" ? -1 : 1);
      if (this._move(i, to)) {
        this._render();
        this.shadowRoot.querySelector(`[data-act="select"][data-i="${to}"]`)?.focus();
      }
    }
  }

  _move(from, to) {
    const list = this._draft.order;
    if (from < 0 || to < 0 || from >= list.length || to >= list.length || from === to) return false;
    const [id] = list.splice(from, 1);
    list.splice(to, 0, id);
    if (this._sel === from) this._sel = to;
    return true;
  }

  _dragStart(e) {
    const row = e.target.closest?.("[data-row]");
    if (!row) return;
    this._drag = Number(row.dataset.row);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", row.dataset.row);
    row.classList.add("dragging");
  }

  _dragOver(e) {
    if (this._drag == null) return;
    const row = e.target.closest?.("[data-row]");
    if (!row) return;
    e.preventDefault();
    this.shadowRoot.querySelectorAll(".row.over").forEach((r) => r.classList.remove("over"));
    row.classList.add("over");
  }

  _dropped(e) {
    if (this._drag == null) return;
    const row = e.target.closest?.("[data-row]");
    e.preventDefault();
    if (row) this._move(this._drag, Number(row.dataset.row));
    this._dragEnd();
  }

  _dragEnd() {
    if (this._drag == null) return;
    this._drag = null;
    this._render();
  }

  async _send() {
    const n = this._note;
    const data = { message: n.message, title: n.title, timeout: Math.max(3, Math.min(3600, n.timeout || 30)) };
    if (n.camera) data.camera = n.camera;
    try {
      await this._hass.callService("fledge", "notify", data);
      this._flash("Sent.");
    } catch (err) {
      this._error = err?.message || String(err);
      this._render();
    }
  }

  _flash(text, ms = 2500) {
    this._notice = text;
    this._render();
    clearTimeout(this._flashTimer);
    this._flashTimer = setTimeout(() => { this._notice = ""; this._render(); }, ms);
  }
}

const STYLE = `
:host{display:flex;flex-direction:column;height:100%;background:var(--primary-background-color);color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
  --fl-card:var(--card-background-color,#1c1c1c);--fl-line:var(--divider-color,rgba(127,127,127,.25));--fl-muted:var(--secondary-text-color,#9e9e9e);
  --fl-primary:var(--primary-color,#03a9f4);--fl-field:var(--input-fill-color, rgba(127,127,127,.12));--mdc-icon-size:22px;position:relative}
*{box-sizing:border-box}
h1{margin:0;font-size:20px;font-weight:400}
h2{margin:0;font-size:16px;font-weight:500}
p{margin:0}
code{font-family:var(--code-font-family,ui-monospace,monospace);font-size:.9em}
.hidden{position:absolute;left:-9999px}
.toolbar{flex:none;display:flex;align-items:center;gap:12px;min-height:var(--header-height,56px);padding:4px 16px;background:var(--app-header-background-color,var(--fl-card));color:var(--app-header-text-color,var(--primary-text-color));border-bottom:var(--app-header-border-bottom,1px solid var(--fl-line));flex-wrap:wrap;z-index:2}
.grow{flex:1 1 auto}
.muted{color:var(--fl-muted)}
.small{font-size:13px}
.caps{font-size:12px;letter-spacing:.08em;text-transform:uppercase}
.center{text-align:center}
.content{flex:1 1 auto;min-height:0;overflow:auto;padding-bottom:88px}
.tabs{flex:none;display:flex;gap:4px;padding:0 12px;border-bottom:1px solid var(--fl-line);overflow-x:auto;scrollbar-width:none;background:var(--fl-card)}
.tab{flex:none;white-space:nowrap;background:none;border:0;border-bottom:2px solid transparent;color:var(--fl-muted);font:inherit;font-size:14px;font-weight:500;height:48px;padding:0 16px;cursor:pointer}
.tab.on{color:var(--fl-primary);border-bottom-color:var(--fl-primary)}
.tab:focus-visible,.btn:focus-visible,.pickrow:focus-visible,.ringbtn:focus-visible,.switch:focus-visible,.icon:focus-visible{outline:2px solid var(--fl-primary);outline-offset:2px}
.card{background:var(--fl-card);border:1px solid var(--fl-line);border-radius:var(--ha-card-border-radius,12px);box-shadow:var(--ha-card-box-shadow,none)}
.pad{padding:20px;display:flex;flex-direction:column;gap:14px}
.deck{display:flex;flex-wrap:wrap;gap:24px;padding:24px;align-items:flex-start}
.list{flex:1 1 300px;max-width:420px;padding:16px;display:flex;flex-direction:column;gap:4px}
.head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:0 4px 8px}
.rows{display:flex;flex-direction:column;gap:2px}
.row{display:flex;align-items:center;gap:2px;border-radius:10px}
.row.sel{background:rgba(var(--rgb-primary-color,3,169,244),.14);outline:1px solid rgba(var(--rgb-primary-color,3,169,244),.5)}
.row.over{box-shadow:0 -2px 0 var(--fl-primary)}
.row.dragging{opacity:.4}
.grip{width:22px;display:flex;justify-content:center;color:var(--fl-muted);cursor:grab;--mdc-icon-size:18px;flex:none}
.pickrow{display:flex;align-items:center;gap:12px;flex:1 1 auto;min-width:0;min-height:56px;padding:8px 10px;background:transparent;border:0;border-radius:10px;color:inherit;font:inherit;text-align:left;cursor:pointer}
.pickrow:hover{background:rgba(127,127,127,.1)}
.ico{width:38px;height:38px;border-radius:50%;background:rgba(127,127,127,.15);display:flex;align-items:center;justify-content:center;flex:none;color:var(--state-icon-color,#44739e);--mdc-icon-size:20px}
.ico.active{color:var(--state-active-color,#fdd835)}
.ico.big{width:46px;height:46px;--mdc-icon-size:24px}
.text{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1 1 auto}
.name{font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.name.big{font-size:17px;font-weight:500}
.sub{display:block;font-size:12px;color:var(--fl-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip{font-size:11px;font-weight:500;letter-spacing:.04em;text-transform:uppercase;border-radius:10px;padding:3px 8px;flex:none;color:var(--fl-muted);background:rgba(127,127,127,.15)}
.chip.warm{color:#e65100;background:rgba(255,152,0,.18)}
.chip.bad{color:var(--error-color,#db4437);background:rgba(219,68,55,.14)}
.add{margin-top:8px;width:100%;height:48px;border:1px dashed var(--fl-line);background:transparent;color:var(--fl-primary);border-radius:10px;font:inherit;font-weight:500;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:8px}
.stage{flex:1 1 360px;display:flex;flex-direction:column;align-items:center;gap:18px;padding-top:4px}
.bezel{width:340px;height:340px;max-width:86vw;max-height:86vw;border-radius:50%;padding:14px;background:radial-gradient(circle at 30% 25%,#f1f2f4,#b9bcc1 55%,#9a9da2);box-shadow:0 18px 50px rgba(0,0,0,.45)}
.screen{width:100%;height:100%;border-radius:50%;background:#000;position:relative;overflow:hidden;container-type:inline-size}
.nface{position:absolute;inset:0;width:100%;height:100%;display:block}
.nicon{position:absolute;width:calc(var(--s) * 1cqw);height:calc(var(--s) * 1cqw);--mdc-icon-size:calc(var(--s) * 1cqw);transform:translate(-50%,-50%);display:flex}
.segs{display:inline-flex;border:1px solid var(--fl-line);border-radius:18px;padding:2px}
.seg{height:30px;padding:0 14px;border:0;border-radius:16px;background:transparent;color:var(--fl-muted);font:inherit;font-size:13px;font-weight:500;cursor:pointer}
.seg.on{background:rgba(var(--rgb-primary-color,3,169,244),.16);color:var(--fl-primary)}
.foot{display:flex;align-items:center;gap:8px;padding:12px 6px 0;--mdc-icon-size:18px}
.mono{font-family:var(--code-font-family,ui-monospace,monospace);font-size:13px;text-align:right;overflow-wrap:anywhere}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chipbtn{height:32px;padding:0 12px;border-radius:16px;border:1px solid var(--fl-line);background:transparent;color:inherit;font:inherit;font-size:13px;cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chipbtn.on{border-color:var(--fl-primary);background:rgba(var(--rgb-primary-color,3,169,244),.16);color:var(--fl-primary)}
.chipbtn:focus-visible,.seg:focus-visible{outline:2px solid var(--fl-primary);outline-offset:2px}
.opt input[disabled]+.text{opacity:.5}
ha-entity-picker{display:block}
.dots{display:flex;gap:6px;flex-wrap:wrap;justify-content:center}
.dot{width:8px;height:8px;border-radius:50%;background:rgba(127,127,127,.35)}
.dot.on{background:var(--fl-primary)}
.ring{display:flex;align-items:center;gap:16px}
.ring .small{width:150px}
.ringbtn{width:52px;height:52px;border-radius:50%;background:var(--fl-card);border:1px solid var(--fl-line);color:inherit;display:inline-flex;align-items:center;justify-content:center;cursor:pointer}
.ringbtn:hover{border-color:var(--fl-primary)}
.detail{flex:1 1 300px;max-width:400px;padding:20px;display:flex;flex-direction:column;gap:16px}
.who{display:flex;align-items:center;gap:12px}
.field{display:flex;flex-direction:column;gap:6px;min-width:0}
.field label{font-size:12px;color:var(--fl-muted)}
input[type=text],input[type=number],input[type=search],.pick{background:var(--fl-field);border:0;border-bottom:1px solid var(--fl-muted);border-radius:6px 6px 0 0;color:inherit;font:inherit;font-size:15px;padding:11px 12px;min-height:44px}
.pick{font-size:14px;padding:8px 10px}
.pick.wide{width:100%}
input:focus,.pick:focus{outline:none;border-bottom:2px solid var(--fl-primary)}
.kv{display:flex;justify-content:space-between;align-items:center;gap:12px;font-size:14px;padding:10px 0;border-top:1px solid var(--fl-line)}
.kv.flat{border-top:0;padding:0}
.kv .sub{margin-top:2px}
.kv .actions{justify-content:flex-end}
.badtext{color:var(--error-color,#db4437)}
.switch{width:40px;height:22px;border-radius:11px;background:rgba(127,127,127,.5);border:0;position:relative;cursor:pointer;flex:none;padding:0}
.switch.on{background:var(--switch-checked-color,var(--fl-primary))}
.knob{position:absolute;top:3px;left:3px;width:16px;height:16px;border-radius:50%;background:#fff;transition:left .15s}
.switch.on .knob{left:21px}
.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:36px;padding:0 14px;border-radius:18px;font:inherit;font-size:14px;font-weight:500;cursor:pointer;text-decoration:none;color:inherit;--mdc-icon-size:18px}
.btn[disabled]{opacity:.4;cursor:default}
.btn.ghost{background:transparent;border:1px solid var(--fl-line)}
.btn.ghost:hover{border-color:var(--fl-primary)}
.btn.solid{background:var(--fl-primary);border:0;color:var(--text-primary-color,#fff)}
.btn.text{background:transparent;border:0;color:var(--fl-primary);padding:0 8px}
.btn.bad{color:var(--error-color,#db4437)}
.icon{width:40px;height:40px;border-radius:50%;background:transparent;border:0;color:inherit;display:inline-flex;align-items:center;justify-content:center;cursor:pointer}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:13px;border-radius:12px;padding:4px 10px}
.pill.ok{color:var(--success-color,#43a047);background:rgba(67,160,71,.14)}
.pill.bad{color:var(--error-color,#db4437);background:rgba(219,68,55,.14)}
.dotlet{width:8px;height:8px;border-radius:50%;background:currentColor}
.banner{margin:16px 24px 0;padding:12px 16px;border-radius:10px;font-size:14px}
.banner.inline{margin:0;display:flex;align-items:center;gap:8px;justify-content:space-between}
.banner.ok{background:rgba(67,160,71,.14);color:var(--success-color,#43a047)}
.banner.warn{background:rgba(255,152,0,.14);color:var(--warning-color,#ffa600)}
.banner.bad{background:rgba(219,68,55,.14);color:var(--error-color,#db4437)}
.cols{display:flex;flex-wrap:wrap;gap:20px;padding:24px;align-items:flex-start}
.cols>.card{flex:1 1 380px;min-width:0;max-width:640px}
.opt{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--fl-line);border-radius:10px;padding:12px 14px;cursor:pointer}
.opt.on{border-color:var(--fl-primary);background:rgba(var(--rgb-primary-color,3,169,244),.08)}
.opt input{margin:3px 0 0;accent-color:var(--fl-primary);width:16px;height:16px;flex:none}
.opt .name{white-space:normal}
.opt .sub{white-space:normal;font-size:13px}
.split{display:flex;gap:12px;flex-wrap:wrap}
.group{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--fl-line);padding-top:12px}
.moderow{display:grid;grid-template-columns:auto 76px minmax(0,1fr);gap:12px;align-items:center}
.moderow.off input{opacity:.45}
.moderow input{min-width:0}
.split>.field{flex:1 1 200px}
.split>.narrowfield{flex:0 0 120px}
pre{margin:0;background:var(--code-editor-background-color,rgba(127,127,127,.1));border:1px solid var(--fl-line);border-radius:8px;padding:12px;overflow-x:auto}
.empty{max-width:480px;margin:80px auto;text-align:center;display:flex;flex-direction:column;gap:12px;align-items:center;padding:0 16px}
.savebar{position:absolute;left:50%;transform:translateX(-50%);bottom:20px;display:flex;align-items:center;gap:12px;background:var(--fl-card);border:1px solid var(--fl-line);border-radius:28px;padding:8px 8px 8px 20px;box-shadow:0 8px 24px rgba(0,0,0,.35);min-width:min(420px,calc(100% - 32px));z-index:3}
.scrim{position:absolute;inset:0;background:rgba(0,0,0,.5);z-index:4}
.dialog{position:absolute;left:50%;top:72px;transform:translateX(-50%);width:min(520px,calc(100% - 32px));max-height:calc(100% - 120px);display:flex;flex-direction:column;gap:12px;background:var(--fl-card);border-radius:16px;padding:16px;z-index:5;box-shadow:0 12px 40px rgba(0,0,0,.5)}
.dialog .head{padding:0}
.dialog ul.role{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:8px;font-size:14px;line-height:1.45}
.btn.solid.danger{background:var(--error-color,#db4437)}
.row.off .ico,.row.off .name{opacity:.5}
.results{overflow:auto;display:flex;flex-direction:column;gap:2px;min-height:120px}
@media (max-width:600px){.deck,.cols{padding:12px;gap:16px}.list,.detail{max-width:none}.ring .small{width:110px}}
`;

customElements.define("fledge-panel", FledgePanel);
