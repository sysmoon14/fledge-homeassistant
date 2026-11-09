// Fledge's panel in Home Assistant's sidebar: a thermostat's cards in the
// ring's order, previewed as the Nest shows them, and its wake, glance and
// notifications. Plain custom element, no build step; it talks to the
// integration over the fledge/* websocket commands (panel.py).

const ICONS = {
  light: "mdi:lightbulb", switch: "mdi:toggle-switch-variant", fan: "mdi:fan", input_boolean: "mdi:toggle-switch-outline",
  climate: "mdi:thermostat", media_player: "mdi:speaker", scene: "mdi:palette", script: "mdi:script-text-play",
  button: "mdi:gesture-tap-button", input_button: "mdi:gesture-tap-button", camera: "mdi:cctv",
  select: "mdi:format-list-bulleted", input_select: "mdi:format-list-bulleted", cover: "mdi:blinds",
};
const KINDS = {
  light: "Light", switch: "Switch", fan: "Fan", input_boolean: "Toggle", climate: "Thermostat", media_player: "Speaker",
  scene: "Scene", script: "Script", button: "Button", input_button: "Button", camera: "Camera", select: "Options",
  input_select: "Options", cover: "Cover",
};
// What the ring does on each kind of card.
const RING = {
  light: [["Turn", "Brightness"], ["Press", "Warmth and colour, where it has them"]],
  switch: [["Press", "On / off"]], fan: [["Press", "On / off"]], input_boolean: [["Press", "On / off"]],
  climate: [["Turn", "Target temperature"], ["Press", "Presets"]],
  media_player: [["Turn", "Volume"], ["Press", "Play / pause"], ["Behind the dial", "Album art"]],
  scene: [["Press", "Runs it"]], script: [["Press", "Runs it"]], button: [["Press", "Presses it"]], input_button: [["Press", "Presses it"]],
  camera: [["Press", "Full screen"], ["Full screen", "A new picture every 3 s"]],
  select: [["Turn", "Pick an option"], ["Press", "Choose it"]], input_select: [["Turn", "Pick an option"], ["Press", "Choose it"]],
  cover: [["Turn", "Position"], ["Press", "Open / close"]],
};
const THERMOSTAT_RING = [["Turn", "Target temperature"], ["Press", "Presets"], ["Place", "Always first"]];
const WAKES = [
  ["auto", "Automatic", "The speaker that's playing, else after sunset a light that's on, else the chooser"],
  ["chooser", "Device chooser", "The thermostat among your cards, zoomed out"],
  ["thermostat", "The thermostat", "As the Nest always did"],
];
const ACTIVE = ["on", "playing", "open", "heat", "cool", "heat_cool", "auto"];

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const domainOf = (id) => id.split(".")[0];
const num = (v, digits = 1) => (typeof v === "number" ? (Math.round(v * 10 ** digits) / 10 ** digits).toString() : "–");

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
    this._query = "";
    this._busy = false;
    this._error = "";
    this._notice = "";
    this._note = { title: "Washing's done", message: "The washing machine has finished.", camera: "", timeout: 30 };
    this._seen = {};
    this._drag = null;
    this.shadowRoot.addEventListener("click", (e) => this._click(e));
    this.shadowRoot.addEventListener("input", (e) => this._input(e));
    this.shadowRoot.addEventListener("change", (e) => this._input(e));
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
      this._load();
      return;
    }
    if (!this._draft || !this._current) return;
    // Only what the panel shows: the cards' devices, and the Nest's own.
    const t = this._current;
    const cards = [...this._draft.entities, t.climate].filter(Boolean);
    if (cards.some((id) => hass.states[id] !== this._seen[id])) {
      this._refreshSoon();
      return;
    }
    const own = [t.update, ...Object.values(t.entities || {})].filter(Boolean);
    if (own.some((id) => hass.states[id] !== this._seen[id])) {
      for (const id of own) this._seen[id] = hass.states[id];
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
    const options = this._current?.options || {};
    this._draft = {
      entities: [...(options.entities || [])],
      wake: options.wake || "chooser",
      wake_device: options.wake_device || null,
      glance: !!options.glance,
      weather: options.weather || null,
      names: { ...(options.names || {}) },
    };
    this._saved = this._fingerprint(this._draft);
    if (!keepSelection) this._sel = 0;
    this._sel = Math.min(this._sel, this._draft.entities.length);
  }

  _fingerprint(draft) {
    const names = Object.fromEntries(Object.entries(draft.names).filter(([k, v]) => v.trim() && draft.entities.includes(k)).sort());
    return JSON.stringify({ ...draft, names, wake_device: draft.entities.includes(draft.wake_device) ? draft.wake_device : null });
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
      this._preview = await this._hass.callWS({ type: "fledge/preview", entities: this._draft.entities, climate: climate || null });
    } catch (err) {
      this._error = err?.message || String(err);
    }
    for (const id of [...this._draft.entities, climate].filter(Boolean)) this._seen[id] = this._hass.states[id];
    this._render();
  }

  async _save() {
    if (!this._current || this._busy) return;
    this._busy = true;
    this._error = "";
    this._render();
    const d = this._draft;
    try {
      await this._hass.callWS({
        type: "fledge/save", entry_id: this._current.entry_id, entities: d.entities, wake: d.wake,
        wake_device: d.entities.includes(d.wake_device) ? d.wake_device : null, glance: d.glance,
        weather: d.weather || null, names: Object.fromEntries(Object.entries(d.names).filter(([, v]) => v.trim())),
      });
      this._busy = false;
      this._notice = "Saved. The Nest has its new cards.";
      setTimeout(() => { this._notice = ""; this._render(); }, 3000);
      await this._load(true);
    } catch (err) {
      this._busy = false;
      this._error = err?.message || String(err);
      this._render();
    }
  }

  // What's shown

  _name(id) {
    const own = this._draft.names[id];
    if (own && own.trim()) return own;
    return this._hass.states[id]?.attributes.friendly_name || id;
  }

  _icon(id) {
    return this._hass.states[id]?.attributes.icon || ICONS[domainOf(id)] || "mdi:card-outline";
  }

  _face(sel) {
    // The round screen: { label, value, name, sub, arc (0-1), color, image }.
    if (sel === 0) {
      const t = this._preview.thermostat;
      const name = this._current.name || "This thermostat";
      if (!t) return { label: "Thermostat", value: "–", name, sub: "Not reporting", arc: 0, color: "#9e9e9e" };
      const action = t.action || t.mode;
      const color = action === "heating" ? "#ff7a2f" : action === "cooling" ? "#29b6f6" : "#9e9e9e";
      const span = (t.max || 30) - (t.min || 7);
      return {
        label: action ? action.replace(/_/g, " ") : "Thermostat",
        value: t.mode === "off" ? "Off" : `${num(t.target)}°`, name,
        sub: typeof t.current === "number" ? `${num(t.current)}° inside` : "",
        arc: t.mode === "off" || typeof t.target !== "number" ? 0 : Math.max(0, Math.min(1, (t.target - (t.min || 7)) / span)),
        color,
      };
    }
    const id = this._draft.entities[sel - 1];
    const card = this._preview.cards[id];
    const name = this._name(id);
    const kind = KINDS[domainOf(id)] || "Card";
    if (!card) return { label: kind, value: "?", name, sub: "Not in Home Assistant, so not on the Nest", arc: 0, color: "#ef9a9a" };
    const state = this._hass.states[id];
    if (state?.state === "unavailable") return { label: kind, value: "–", name, sub: "Unavailable", arc: 0, color: "#9e9e9e" };
    switch (card.kind) {
      case "climate":
        return { label: card.action || card.mode || kind, value: card.mode === "off" ? "Off" : `${num(card.target)}°`, name,
          sub: typeof card.current === "number" ? `${num(card.current)}° inside` : "", color: card.action === "cooling" ? "#29b6f6" : "#ff7a2f",
          arc: typeof card.target === "number" ? (card.target - card.min) / ((card.max - card.min) || 1) : 0 };
      case "media": {
        const art = ["playing", "paused"].includes(card.state) ? state?.attributes.entity_picture : null;
        const sub = card.title ? `${card.title}${card.artist ? " · " + card.artist : ""}` : card.state;
        return { label: kind, value: card.volume ?? "–", name, sub, arc: (card.volume || 0) / 100, color: "#4fc3f7", image: art };
      }
      case "camera":
        return { label: kind, value: "", name, sub: "Press for full screen", arc: 1, color: "#9ccc65", image: state?.attributes.entity_picture };
      case "action":
        return { label: kind, value: "Run", name, sub: "Press to run", arc: 1, color: "#b39ddb" };
      case "options": {
        const at = card.options.indexOf(card.current);
        return { label: kind, value: card.current, name, sub: `${card.options.length} options`, arc: at < 0 ? 0 : (at + 1) / card.options.length, color: "#4dd0e1" };
      }
      case "cover":
        return { label: kind, value: card.position != null ? `${card.position}%` : card.state, name, sub: card.state,
          arc: (card.position ?? (card.state === "open" ? 100 : 0)) / 100, color: "#80cbc4" };
      case "lights": {
        const one = card.lights?.[0] || {};
        const label = card.members?.length ? `Light group · ${card.members.length}` : kind;
        if (!one.on) return { label, value: "Off", name, sub: one.dimmable ? "Turn to switch on" : "Press to switch on", arc: 0, color: "#9e9e9e" };
        const color = one.rgb ? `rgb(${one.rgb.join(",")})` : "#ffc35a";
        const sub = one.kelvin ? `${one.kelvin} K` : one.hue != null ? "Colour" : "On";
        return { label, value: one.brightness != null ? `${one.brightness}%` : "On", name, sub, arc: one.brightness != null ? one.brightness / 100 : 1, color };
      }
      default:
        return { label: kind, value: "", name, sub: "", arc: 0, color: "#9e9e9e" };
    }
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
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>${this._toolbar()}<div class="content">${body}</div>${this._saveBar()}${this._adding ? this._addDialog() : ""}`;
    const content = this.shadowRoot.querySelector(".content");
    if (content) content.scrollTop = scroll;
    this._restoreFocus(focus);
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
      status = `<span class="pill ${online ? "ok" : "bad"}"><span class="dotlet"></span>${online ? "Online" : "Offline"}${t.sw_version ? " · " + esc(t.sw_version) : ""}</span>`;
    }
    const wake = t?.entities?.wake_screen ? `<button class="btn ghost" data-act="wakenow">Wake screen</button>` : "";
    return `<header class="toolbar">${menu}<h1>Fledge</h1>${picker}<span class="grow"></span>${status}${wake}</header>`;
  }

  _tabs() {
    const tabs = [["cards", "Cards"], ["wake", "Wake & glance"], ["notify", "Notifications"], ["thermostat", "Thermostat"]];
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
    const d = this._draft;
    const t = this._current;
    const rows = [
      this._row(0, t.climate || "climate.", t.name || "This thermostat", "Always first", "mdi:thermostat", false),
      ...d.entities.map((id, i) => this._row(i + 1, id, this._name(id), id, this._icon(id), true)),
    ].join("");
    return `<div class="deck">
      <section class="card list" aria-label="Cards in ring order">
        <div class="head"><h2>Ring order</h2><span class="muted small">${d.entities.length + 1} cards · drag to reorder</span></div>
        <div class="rows">${rows}</div>
        <button class="add" data-act="add"><ha-icon icon="mdi:plus"></ha-icon>Add a card</button>
      </section>
      ${this._previewSection()}
      ${this._detail()}
    </div>`;
  }

  _row(i, id, name, sub, icon, movable) {
    const state = movable ? this._hass.states[id] : this._current.climate && this._hass.states[this._current.climate];
    const active = state && ACTIVE.includes(state.state);
    const missing = movable && !this._hass.states[id];
    const unavailable = state?.state === "unavailable";
    const badge = missing ? `<span class="chip bad">Not found</span>` : unavailable ? `<span class="chip">Unavailable</span>`
      : movable && this._draft.wake_device === id ? `<span class="chip warm">Wakes here</span>` : "";
    return `<div class="row ${i === this._sel ? "sel" : ""} ${this._drag === i ? "dragging" : ""}" data-row="${i}" ${movable ? 'draggable="true"' : ""}>
      ${movable ? `<span class="grip" aria-hidden="true"><ha-icon icon="mdi:drag-vertical"></ha-icon></span>` : `<span class="grip"></span>`}
      <button class="pickrow" data-act="select" data-i="${i}" aria-label="${esc(name)}" aria-current="${i === this._sel}">
        <span class="ico ${active ? "active" : ""}"><ha-icon icon="${esc(icon)}"></ha-icon></span>
        <span class="text"><span class="name">${esc(name)}</span><span class="sub">${esc(sub)}</span></span>
        ${badge}
      </button>
    </div>`;
  }

  _previewSection() {
    const total = this._draft.entities.length + 1;
    const f = this._face(this._sel);
    const r = 140;
    const circ = 2 * Math.PI * r;
    const dash = `${(Math.max(0, Math.min(1, f.arc || 0)) * circ).toFixed(1)} ${circ.toFixed(1)}`;
    const value = String(f.value ?? "");
    const size = value.length > 8 ? 26 : value.length > 4 ? 40 : 64;
    const dots = total <= 16 ? Array.from({ length: total }, (_, i) => `<span class="dot ${i === this._sel ? "on" : ""}"></span>`).join("")
      : `<span class="muted small">${this._sel + 1} of ${total}</span>`;
    return `<section class="stage" aria-label="As the Nest shows it">
      <span class="muted caps">On the Nest</span>
      <div class="bezel"><div class="screen">
        ${f.image ? `<img class="photo" src="${esc(f.image)}" alt="">` : ""}
        <svg class="arc" viewBox="0 0 312 312" aria-hidden="true">
          <circle cx="156" cy="156" r="${r}" fill="none" stroke="#1d1d1d" stroke-width="8"></circle>
          <circle cx="156" cy="156" r="${r}" fill="none" stroke="${esc(f.color)}" stroke-width="8" stroke-linecap="round" stroke-dasharray="${dash}"></circle>
        </svg>
        <span class="label">${esc(f.label)}</span>
        ${value ? `<span class="value" style="font-size:${size}px">${esc(value)}</span>` : ""}
        <span class="fname">${esc(f.name)}</span>
        <span class="fsub" style="color:${esc(f.color)}">${esc(f.sub)}</span>
      </div></div>
      <div class="dots">${dots}</div>
      <div class="ring">
        <button class="ringbtn" data-act="turn" data-by="-1" aria-label="Turn the ring left"><ha-icon icon="mdi:chevron-left"></ha-icon></button>
        <span class="muted small center">Turn the ring to try your order</span>
        <button class="ringbtn" data-act="turn" data-by="1" aria-label="Turn the ring right"><ha-icon icon="mdi:chevron-right"></ha-icon></button>
      </div>
    </section>`;
  }

  _detail() {
    const t = this._current;
    if (this._sel === 0) {
      return `<section class="card detail" aria-label="Selected card">
        <div class="who"><span class="ico big active"><ha-icon icon="mdi:thermostat"></ha-icon></span>
          <span class="text"><span class="name big">${esc(t.name || "This thermostat")}</span><span class="sub">The Nest itself</span></span></div>
        <div>${THERMOSTAT_RING.map(([k, v]) => `<div class="kv"><span class="muted">${esc(k)}</span><span>${esc(v)}</span></div>`).join("")}</div>
        ${t.climate ? `<button class="btn ghost" data-act="more" data-id="${esc(t.climate)}">Open in Home Assistant</button>` : ""}
      </section>`;
    }
    const i = this._sel - 1;
    const id = this._draft.entities[i];
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
      <div class="kv"><span>Wake straight to this card</span>${this._switch("wakehere", wakes, "Wake straight to this card")}</div>
      <div class="actions">
        <button class="btn ghost" data-act="move" data-by="-1" ${i === 0 ? "disabled" : ""} aria-label="Move up"><ha-icon icon="mdi:arrow-up"></ha-icon></button>
        <button class="btn ghost" data-act="move" data-by="1" ${i === this._draft.entities.length - 1 ? "disabled" : ""} aria-label="Move down"><ha-icon icon="mdi:arrow-down"></ha-icon></button>
        <button class="btn ghost" data-act="more" data-id="${esc(id)}">Open</button>
        <span class="grow"></span>
        <button class="btn text bad" data-act="remove">Remove</button>
      </div>
    </section>`;
  }

  _switch(act, on, label) {
    return `<button class="switch ${on ? "on" : ""}" role="switch" aria-checked="${on}" aria-label="${esc(label)}" data-act="${act}"><span class="knob"></span></button>`;
  }

  _wakeTab() {
    const d = this._draft;
    const t = this._current;
    const weathers = Object.keys(this._hass.states).filter((id) => id.startsWith("weather.")).sort();
    const keep = t.entities?.keep_screen_on;
    const keepOn = keep && this._hass.states[keep]?.state === "on";
    const device = d.entities.includes(d.wake_device) ? d.wake_device : null;
    return `<div class="cols">
      <section class="card pad">
        <h2>When the Nest wakes</h2>
        <p class="muted small">You walk up, turn the ring or press it while the screen is dark.</p>
        ${device ? `<div class="banner warn inline">It wakes straight to <strong>${esc(this._name(device))}</strong>, whatever is chosen below.
          <button class="btn text" data-act="clearwake">Stop</button></div>` : ""}
        ${WAKES.map(([k, label, sub]) => `<label class="opt ${d.wake === k ? "on" : ""}"><input type="radio" name="wake" value="${k}" ${d.wake === k ? "checked" : ""} data-input="wake">
          <span><span class="name">${esc(label)}</span><span class="sub">${esc(sub)}</span></span></label>`).join("")}
      </section>
      <section class="card pad">
        <div class="kv flat"><h2>Glance</h2>${this._switch("glance", d.glance, "Glance when someone walks up")}</div>
        <p class="muted small">When someone walks up rather than touches it, the Nest shows the time, the weather and what's playing.</p>
        <div class="field"><label for="weather">Weather</label>
          <select id="weather" class="pick wide" data-input="weather" ${d.glance ? "" : "disabled"}>
            <option value="">None</option>
            ${weathers.map((w) => `<option value="${esc(w)}" ${d.weather === w ? "selected" : ""}>${esc(this._hass.states[w].attributes.friendly_name || w)}</option>`).join("")}
          </select></div>
        ${keep ? `<div class="kv"><span>Keep screen on<span class="sub">Applies straight away</span></span>${this._switch("keepon", keepOn, "Keep screen on")}</div>` : ""}
      </section>
    </div>`;
  }

  _notifyTab() {
    const n = this._note;
    const cameras = Object.keys(this._hass.states).filter((id) => id.startsWith("camera.")).sort();
    const yaml = ["action: fledge.notify", "data:", n.title ? `  title: ${n.title}` : null, `  message: ${n.message}`,
      n.camera ? `  camera: ${n.camera}` : null, `  timeout: ${n.timeout}`].filter((l) => l != null).join("\n");
    return `<div class="cols">
      <section class="card pad">
        <h2>Try a notification</h2>
        <p class="muted small">It shows on every Nest running Fledge. A press or a dismissal fires <code>fledge_notification</code>.</p>
        <div class="field"><label for="ntitle">Title</label><input id="ntitle" type="text" value="${esc(n.title)}" data-input="note" data-key="title"></div>
        <div class="field"><label for="nmsg">Message</label><input id="nmsg" type="text" value="${esc(n.message)}" data-input="note" data-key="message"></div>
        <div class="split">
          <div class="field"><label for="ncam">Camera picture</label><select id="ncam" class="pick wide" data-input="note" data-key="camera">
            <option value="">None</option>${cameras.map((c) => `<option value="${esc(c)}" ${n.camera === c ? "selected" : ""}>${esc(this._hass.states[c].attributes.friendly_name || c)}</option>`).join("")}</select></div>
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
    const protocol = t.protocol == null ? "Not reported yet" : t.protocol >= t.needs ? `${t.protocol} · matches` : `${t.protocol} · needs ${t.needs}`;
    let updateRow = "";
    if (update) {
      const available = update.state === "on";
      updateRow = `<div class="kv"><span>Thermostat update<span class="sub">${available ? `${esc(update.attributes.latest_version)} available` : "Up to date"}</span></span>
        ${available ? `<button class="btn solid" data-act="install">Install</button>` : `<button class="btn ghost" data-act="more" data-id="${esc(t.update)}">Details</button>`}</div>`;
    }
    const device = t.device_id ? `/config/devices/device/${t.device_id}` : null;
    return `<div class="cols">
      <section class="card pad">
        <h2>${esc(t.name || "Nest Thermostat")}</h2>
        <div class="kv"><span class="muted">Serial</span><span>${esc(t.serial)}</span></div>
        <div class="kv"><span class="muted">Thermostat build</span><span>${esc(t.sw_version || "–")}</span></div>
        <div class="kv"><span class="muted">Card protocol</span><span class="${t.protocol != null && t.protocol < t.needs ? "badtext" : ""}">${esc(protocol)}</span></div>
        <div class="kv"><span class="muted">Integration</span><span>${esc(t.version || "–")}</span></div>
        ${updateRow}
        <div class="actions">
          ${device ? `<a class="btn ghost" href="${device}" data-act="nav" data-href="${device}">Heating, hot water and sensors</a>` : ""}
          <a class="btn ghost" href="/config/integrations/integration/fledge" data-act="nav" data-href="/config/integrations/integration/fledge">Integration</a>
        </div>
      </section>
    </div>`;
  }

  _saveBar() {
    if (!this._dirty && !this._busy) return "";
    return `<div class="savebar" role="region" aria-label="Unsaved changes"><span>Unsaved changes</span><span class="grow"></span>
      <button class="btn text" data-act="revert" ${this._busy ? "disabled" : ""}>Revert</button>
      <button class="btn solid" data-act="save" ${this._busy ? "disabled" : ""}>${this._busy ? "Saving…" : "Save"}</button></div>`;
  }

  _candidates() {
    const own = new Set(this._current.own || []);
    const taken = new Set(this._draft.entities);
    const q = this._query.trim().toLowerCase();
    return Object.values(this._hass.states)
      .filter((s) => this._domains.includes(domainOf(s.entity_id)) && !own.has(s.entity_id) && !taken.has(s.entity_id))
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
        const total = d.entities.length + 1;
        this._sel = (this._sel + Number(el.dataset.by) + total) % total;
        break;
      }
      case "move": this._move(this._sel - 1, this._sel - 1 + Number(el.dataset.by)); break;
      case "remove": {
        const id = d.entities[this._sel - 1];
        d.entities.splice(this._sel - 1, 1);
        if (d.wake_device === id) d.wake_device = null;
        this._sel = Math.min(this._sel, d.entities.length);
        break;
      }
      case "wakehere": {
        const id = d.entities[this._sel - 1];
        d.wake_device = d.wake_device === id ? null : id;
        break;
      }
      case "clearwake": d.wake_device = null; break;
      case "glance": d.glance = !d.glance; break;
      case "add": this._adding = true; this._query = ""; this._render(); this.shadowRoot.getElementById("search")?.focus(); return;
      case "closeadd": this._adding = false; break;
      case "addone": {
        d.entities.push(el.dataset.id);
        this._adding = false;
        this._sel = d.entities.length;
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
      case "keepon": this._hass.callService("switch", "toggle", { entity_id: this._current.entities.keep_screen_on }); return;
      case "install": this._hass.callService("update", "install", { entity_id: this._current.update }); return;
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
      d.names[d.entities[this._sel - 1]] = el.value;
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
      d.wake = el.value;
      this._render();
    } else if (what === "weather" && e.type === "change") {
      d.weather = el.value || null;
      this._render();
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
      if (e.type === "change" || key !== "timeout") {
        const pre = this.shadowRoot.querySelector("pre");
        const send = this.shadowRoot.querySelector('[data-act="send"]');
        if (pre) {
          const tmp = document.createElement("div");
          tmp.innerHTML = this._notifyTab();
          pre.replaceWith(tmp.querySelector("pre"));
        }
        if (send) send.disabled = !this._note.message.trim();
      }
    }
  }

  _key(e) {
    if (e.key === "Escape" && this._adding) {
      this._adding = false;
      this._render();
      return;
    }
    const row = e.target.closest?.('[data-act="select"]');
    if (row && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      const i = Number(row.dataset.i);
      if (i === 0) return;
      e.preventDefault();
      const to = i - 1 + (e.key === "ArrowUp" ? -1 : 1);
      if (this._move(i - 1, to)) {
        this._render();
        this.shadowRoot.querySelector(`[data-act="select"][data-i="${to + 1}"]`)?.focus();
      }
    }
  }

  _move(from, to) {
    const list = this._draft.entities;
    if (from < 0 || to < 0 || from >= list.length || to >= list.length || from === to) return false;
    const [id] = list.splice(from, 1);
    list.splice(to, 0, id);
    if (this._sel === from + 1) this._sel = to + 1;
    return true;
  }

  _dragStart(e) {
    const row = e.target.closest?.("[data-row]");
    if (!row || row.dataset.row === "0") return;
    this._drag = Number(row.dataset.row);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", row.dataset.row);
    row.classList.add("dragging");
  }

  _dragOver(e) {
    if (this._drag == null) return;
    const row = e.target.closest?.("[data-row]");
    if (!row || row.dataset.row === "0") return;
    e.preventDefault();
    this.shadowRoot.querySelectorAll(".row.over").forEach((r) => r.classList.remove("over"));
    row.classList.add("over");
  }

  _dropped(e) {
    if (this._drag == null) return;
    const row = e.target.closest?.("[data-row]");
    e.preventDefault();
    if (row && row.dataset.row !== "0") this._move(this._drag - 1, Number(row.dataset.row) - 1);
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

  _flash(text) {
    this._notice = text;
    this._render();
    clearTimeout(this._flashTimer);
    this._flashTimer = setTimeout(() => { this._notice = ""; this._render(); }, 2500);
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
.screen{width:100%;height:100%;border-radius:50%;background:#000;position:relative;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;color:#fff;text-align:center;padding:40px}
.photo{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.55}
.arc{position:absolute;inset:0;width:100%;height:100%;transform:rotate(-90deg)}
.screen>span{position:relative;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.label{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#bdbdbd}
.value{font-weight:300;line-height:1}
.fname{font-size:18px}
.fsub{font-size:13px}
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
.split>.field{flex:1 1 200px}
.split>.narrowfield{flex:0 0 120px}
pre{margin:0;background:var(--code-editor-background-color,rgba(127,127,127,.1));border:1px solid var(--fl-line);border-radius:8px;padding:12px;overflow-x:auto}
.empty{max-width:480px;margin:80px auto;text-align:center;display:flex;flex-direction:column;gap:12px;align-items:center;padding:0 16px}
.savebar{position:absolute;left:50%;transform:translateX(-50%);bottom:20px;display:flex;align-items:center;gap:12px;background:var(--fl-card);border:1px solid var(--fl-line);border-radius:28px;padding:8px 8px 8px 20px;box-shadow:0 8px 24px rgba(0,0,0,.35);min-width:min(420px,calc(100% - 32px));z-index:3}
.scrim{position:absolute;inset:0;background:rgba(0,0,0,.5);z-index:4}
.dialog{position:absolute;left:50%;top:72px;transform:translateX(-50%);width:min(520px,calc(100% - 32px));max-height:calc(100% - 120px);display:flex;flex-direction:column;gap:12px;background:var(--fl-card);border-radius:16px;padding:16px;z-index:5;box-shadow:0 12px 40px rgba(0,0,0,.5)}
.dialog .head{padding:0}
.results{overflow:auto;display:flex;flex-direction:column;gap:2px;min-height:120px}
@media (max-width:600px){.deck,.cols{padding:12px;gap:16px}.list,.detail{max-width:none}.ring .small{width:110px}}
`;

customElements.define("fledge-panel", FledgePanel);
