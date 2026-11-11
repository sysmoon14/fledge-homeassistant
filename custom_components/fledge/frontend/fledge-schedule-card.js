// Fledge: the Nest's own weekly schedules, heating and hot water, as cards.
// Loaded on every page by the Fledge integration (no resource to add).
//
//   type: custom:fledge-heating-schedule      entity: climate.nest_thermostat
//   type: custom:fledge-hot-water-schedule    entity: climate.nest_thermostat
//     name:  the card's title (Heating / Hot water)
//     edit:  '#popup' to edit in your own popup holding the editor card below,
//            rather than the card's own dialog
//   type: custom:fledge-heating-schedule-editor / fledge-hot-water-schedule-editor
//     the editor on its own, for such a popup
//
// The week comes from the Nest's Heating times / Hot water times sensors
// (found through the fledge/schedule websocket command); saving and Copy to
// days write with fledge.set_schedule, every changed day in one call.
(() => {
  if (customElements.get('fledge-heating-schedule')) return;

  const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
  const SHORT = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' };
  const LONG = { mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday', sun: 'Sunday' };
  const QUICK = [['Weekdays', DAYS.slice(0, 5)], ['Weekend', DAYS.slice(5)], ['Every day', DAYS]];
  const HEATING = 'heating';
  const WATER = 'hot_water';
  const DAY_MIN = 1440;
  const UNDO_MS = 10000;
  // How long a save waits for the Nest to show it before saying it didn't.
  const CONFIRM_MS = 20000;

  const toMin = (s) => { const [h, m] = String(s).trim().split(':').map((x) => parseInt(x, 10)); return (h || 0) * 60 + (m || 0); };
  const hhmm = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  const dur = (m) => (m >= 60 ? (m % 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m / 60}h`) : `${m}m`);
  const fmtT = (t) => (t == null || isNaN(t) ? '–' : `${(Math.round(t * 2) / 2).toString()}°`);
  const todayIdx = () => (new Date().getDay() + 6) % 7;
  const nowMin = () => { const n = new Date(); return n.getHours() * 60 + n.getMinutes(); };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const listDays = (ds) => {
    const k = DAYS.filter((d) => ds.includes(d));
    if (k.length === 7) return 'every day';
    if (same(k, DAYS.slice(0, 5))) return 'weekdays';
    if (same(k, DAYS.slice(5))) return 'the weekend';
    // Runs of three or more as Tue–Fri.
    const parts = [];
    for (let i = 0; i < k.length;) {
      let j = i;
      while (j + 1 < k.length && DAYS.indexOf(k[j + 1]) === DAYS.indexOf(k[j]) + 1) j++;
      if (j - i >= 2) parts.push(`${SHORT[k[i]]}–${SHORT[k[j]]}`); else for (let x = i; x <= j; x++) parts.push(SHORT[k[x]]);
      i = j + 1;
    }
    return parts.join(', ');
  };

  // ---- The week, from the sensors, and back to what the service takes.

  // Heating: each day's changes [{m, t}]; hot water: on periods [{s, e}],
  // e past 1440 for one running into the next day.
  const KIND = {
    [HEATING]: {
      sensor: HEATING,
      read: (list) => (Array.isArray(list) ? list : []).map((p) => ({ m: toMin(p.time), t: Number(p.temp) }))
        .filter((p) => !isNaN(p.t)).sort((a, b) => a.m - b.m),
      write: (list) => [...list].sort((a, b) => a.m - b.m).map((p) => ({ time: hhmm(p.m), temp: p.t })),
      problem: (list) => {
        const seen = new Set();
        for (const p of list) { if (seen.has(p.m)) return `Two changes at ${hhmm(p.m)}`; seen.add(p.m); }
        return '';
      },
    },
    [WATER]: {
      sensor: WATER,
      read: (list) => (Array.isArray(list) ? list : []).map((p) => {
        const s = toMin(p.on); const e = toMin(p.off);
        return { s, e: e > s ? e : e + DAY_MIN };
      }).sort((a, b) => a.s - b.s),
      write: (list) => [...list].sort((a, b) => a.s - b.s).map((p) => ({ on: hhmm(p.s), off: hhmm(p.e % DAY_MIN) })),
      problem: (list) => {
        const l = [...list].sort((a, b) => a.s - b.s);
        for (let i = 1; i < l.length; i++) if (l[i].s < l[i - 1].e) return `${hhmm(l[i].s)} overlaps the time before it`;
        if (l.length && l[l.length - 1].e > DAY_MIN + l[0].s) return 'The last time runs into the first';
        return '';
      },
    },
  };

  const readWeek = (hass, ids, kind) => {
    const st = ids && ids[KIND[kind].sensor] && hass.states[ids[KIND[kind].sensor]];
    if (!st || !DAYS.some((d) => Array.isArray(st.attributes[d]))) return null;
    return Object.fromEntries(DAYS.map((d) => [d, KIND[kind].read(st.attributes[d])]));
  };
  // A day's list as a key, whatever its order: what's saved and what the
  // sensor then shows compare equal (temperatures to the Nest's half degrees).
  const norm = (list) => JSON.stringify([...(list || [])].sort((a, b) => (a.m ?? a.s) - (b.m ?? b.s))
    .map((x) => ('m' in x ? [x.m, Math.round(x.t * 2) / 2] : [x.s, x.e])));

  // The setpoint at midnight: the last one before, however many days back.
  const carry = (week, i) => {
    for (let b = 1; b <= 7; b++) { const l = week[DAYS[(i + 7 - b) % 7]]; if (l && l.length) return l[l.length - 1].t; }
    return null;
  };
  const heatSlots = (week, d) => {
    const i = DAYS.indexOf(d); const pts = week[d] || [];
    const slots = pts.length && pts[0].m === 0 ? [] : [{ m: 0, t: carry(week, i) }];
    slots.push(...pts);
    return slots.map((x, k) => ({ s: x.m, e: k + 1 < slots.length ? slots[k + 1].m : DAY_MIN, t: x.t }));
  };
  // Hot water on in a day: its own periods, and the end of the day before's.
  const waterSpans = (week, d) => {
    const prev = week[DAYS[(DAYS.indexOf(d) + 6) % 7]] || [];
    const spill = prev.filter((p) => p.e > DAY_MIN).map((p) => ({ s: 0, e: p.e - DAY_MIN, spill: true }));
    return [...spill, ...(week[d] || []).map((p) => ({ s: p.s, e: Math.min(p.e, DAY_MIN) }))];
  };

  const ids = new Map();
  const lookup = (hass, entity) => {
    if (!ids.has(entity)) {
      ids.set(entity, hass.callWS({ type: 'fledge/schedule', entity_id: entity })
        .catch((err) => { setTimeout(() => ids.delete(entity), 30000); throw err; }));
    }
    return ids.get(entity);
  };

  // ---- Look: Home Assistant's theme, Origami's where a dashboard has it.

  // How much of the heat colour a setpoint shows, of 100: 16° nearly none, 21° all.
  const heatShare = (t) => Math.max(12, Math.min(100, ((t - 16) / 5) * 100));
  const heatColour = (t) => `color-mix(in srgb, var(--fs-heat) ${heatShare(t).toFixed(0)}%, var(--fs-inset))`;
  const AXIS = '<div class="axis"><div></div><div class="ticks"><span style="left:0">00</span><span style="left:25%">06</span><span style="left:50%">12</span><span style="left:75%">18</span><span style="left:100%">24</span></div></div>';
  const ICON = {
    edit: 'mdi:pencil-outline', copy: 'mdi:content-copy', add: 'mdi:plus', del: 'mdi:trash-can-outline', close: 'mdi:close',
    minus: 'mdi:minus', plus: 'mdi:plus', check: 'mdi:check',
  };
  const BASE_CSS = `
    :host { display: block;
      --fs-inset: var(--org-inset, var(--secondary-background-color, #eceeeb));
      --fs-inset-shadow: var(--org-inset-shadow, none);
      --fs-heat: var(--fledge-heat-color, var(--temperature-accent, #c9845a));
      --fs-water: var(--fledge-water-color, var(--water-accent, #4f8fd8));
      --fs-accent: var(--fledge-accent-color, var(--accent-color, #2f7d5b));
      --fs-on-accent: var(--text-color-active, var(--text-primary-color, #fff));
      --fs-card: var(--ha-card-background, var(--card-background-color, #fff)); }
    ha-card { padding: 16px; box-sizing: border-box; overflow: hidden; }
    button { font: inherit; -webkit-tap-highlight-color: transparent; cursor: pointer; color: inherit; }
    button:disabled { cursor: default; opacity: .4; }
    button:focus-visible, input:focus-visible { outline: 2px solid var(--fs-accent); outline-offset: 2px; }
    ha-icon { --mdc-icon-size: 18px; }
    .head { display: flex; align-items: center; gap: 12px; }
    .titles { flex: 1; min-width: 0; }
    .title { font-size: var(--ha-font-size-l, 16px); font-weight: var(--ha-font-weight-bold, 700); color: var(--primary-text-color); line-height: 1.25; }
    .line { font-size: var(--ha-font-size-s, 12px); color: var(--secondary-text-color); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
    .pill { flex: none; min-width: 64px; height: 32px; padding: 0 14px; box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center; gap: 6px;
      border-radius: 999px; border: none; font-size: 13px; font-weight: 600; background: var(--fs-inset); box-shadow: var(--fs-inset-shadow); color: var(--primary-text-color);
      transition: background-color .3s, color .3s; font-variant-numeric: tabular-nums; }
    .pill.lit { background: var(--fs-accent); color: var(--fs-on-accent); box-shadow: 0 0 10px color-mix(in srgb, var(--fs-accent) 55%, transparent); }
    .pill ha-icon { --mdc-icon-size: 16px; }
    .week { margin-top: 16px; display: grid; gap: 6px; }
    .row { display: grid; grid-template-columns: 34px 1fr; align-items: center; gap: 8px; border: none; background: none; padding: 0; text-align: left; }
    .day { font-size: 12px; font-weight: 600; color: var(--secondary-text-color); }
    .row.today .day { color: var(--primary-text-color); font-weight: 700; }
    .bar { position: relative; height: 26px; border-radius: 8px; overflow: hidden; background: var(--fs-inset); box-shadow: var(--fs-inset-shadow); }
    .row.today .bar { outline: 1.5px solid color-mix(in srgb, var(--primary-text-color) 22%, transparent); outline-offset: 1px; }
    .seg, .blk { position: absolute; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700;
      color: var(--fs-on-accent); white-space: nowrap; overflow: hidden; font-variant-numeric: tabular-nums; }
    /* A low setpoint is mostly the card's own inset: the theme's text reads on it. */
    .seg.low { color: var(--primary-text-color); }
    .blk { border-radius: 6px; background: color-mix(in srgb, var(--fs-water) 85%, var(--fs-inset)); }
    .blk.paused { filter: saturate(.25); opacity: .55; }
    .now { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--primary-text-color); border-radius: 2px;
      box-shadow: 0 0 0 1px color-mix(in srgb, var(--fs-card) 70%, transparent); }
    .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 11px; color: var(--secondary-text-color); }
    .axis { display: grid; grid-template-columns: 34px 1fr; gap: 8px; margin-top: 4px; }
    .ticks { position: relative; height: 14px; font-size: 10px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
    .ticks span { position: absolute; transform: translateX(-50%); }
    .ticks span:first-child { transform: none; } .ticks span:last-child { transform: translateX(-100%); }
    .btn { height: 40px; border-radius: 12px; border: none; font-size: 14px; font-weight: 600; display: inline-flex; align-items: center; justify-content: center; gap: 8px;
      background: var(--fs-inset); box-shadow: var(--fs-inset-shadow); color: var(--primary-text-color); padding: 0 14px; }
    .btn.primary { background: var(--fs-accent); color: var(--fs-on-accent); box-shadow: none; }
    .btn.ghost { background: transparent; box-shadow: none; }
    .btn.wide { width: 100%; margin-top: 16px; }
    .hint { padding: 16px 4px; text-align: center; font-size: 13px; color: var(--secondary-text-color); }
  `;

  const bars = (kind, week, d, opts = {}) => {
    if (kind === HEATING) {
      return heatSlots(week, d).map((x) => {
        const w = ((x.e - x.s) / DAY_MIN) * 100;
        return `<div class="seg${x.t != null && heatShare(x.t) < 50 ? ' low' : ''}" style="left:${(x.s / 14.4).toFixed(3)}%;width:${w.toFixed(3)}%;background:${x.t == null ? 'transparent' : heatColour(x.t)}"`
          + ` title="${hhmm(x.s)}–${hhmm(x.e)} ${fmtT(x.t)}">${opts.labels !== false && w >= 7 && x.t != null ? fmtT(x.t) : ''}</div>`;
      }).join('');
    }
    const spans = waterSpans(week, d);
    if (!spans.length) return opts.labels === false ? '' : '<span class="empty">Off all day</span>';
    return spans.map((b) => {
      const w = ((b.e - b.s) / DAY_MIN) * 100;
      return `<div class="blk${opts.paused ? ' paused' : ''}" style="left:${(b.s / 14.4).toFixed(3)}%;width:${w.toFixed(3)}%" title="${hhmm(b.s)}–${hhmm(b.e)}">`
        + `${opts.labels !== false && w >= 6 ? dur(b.e - b.s) : ''}</div>`;
    }).join('');
  };

  // A time as two pickers, 24-hour whatever the device's clock: hours, and
  // minutes in quarters (with the minute it has, where that's not one).
  const timeField = (role, m, label) => {
    const h = Math.floor(m / 60) % 24; const mm = m % 60;
    const mins = [0, 15, 30, 45].includes(mm) ? [0, 15, 30, 45] : [0, 15, 30, 45, mm].sort((a, b) => a - b);
    const opt = (v, on) => `<option value="${v}"${on ? ' selected' : ''}>${String(v).padStart(2, '0')}</option>`;
    return `<span class="tf ${role}"><ha-icon icon="mdi:clock-outline"></ha-icon>`
      + `<select class="tsel hh" aria-label="${label}, hour">${Array.from({ length: 24 }, (_, v) => opt(v, v === h)).join('')}</select>`
      + '<span class="colon">:</span>'
      + `<select class="tsel mm" aria-label="${label}, minutes">${mins.map((v) => opt(v, v === mm)).join('')}</select></span>`;
  };
  // Calls back with the minutes of the day as either picker changes.
  const onTime = (field, fn) => {
    const read = () => parseInt(field.querySelector('.hh').value, 10) * 60 + parseInt(field.querySelector('.mm').value, 10);
    field.querySelectorAll('select').forEach((sel) => sel.addEventListener('change', () => fn(read())));
  };

  const navigate = (path) => {
    history.pushState(null, '', path.startsWith('#') ? location.pathname + location.search + path : path);
    window.dispatchEvent(new CustomEvent('location-changed', { detail: { replace: false } }));
  };

  // ---- The week card.

  class FledgeSchedule extends HTMLElement {
    static get kind() { return HEATING; }
    static getConfigForm() {
      return {
        schema: [
          { name: 'entity', required: true, selector: { entity: { domain: 'climate', integration: 'mqtt' } } },
          { name: 'name', selector: { text: {} } },
          { name: 'edit', selector: { text: {} } },
        ],
        computeLabel: (s) => ({ entity: 'Nest thermostat', name: 'Name', edit: 'Edit in this popup instead (optional)' }[s.name]),
        computeHelper: (s) => (s.name === 'edit' ? "A link like #heating_schedule to your own popup with the editor card; left empty, the card's own editor opens." : undefined),
      };
    }
    static getStubConfig(hass) {
      const nest = Object.values(hass.entities || {}).find((e) => e.entity_id.startsWith('climate.') && e.platform === 'mqtt'
        && hass.states[e.entity_id] && /nest/i.test(e.entity_id));
      return { entity: nest ? nest.entity_id : '' };
    }
    get kind() { return this.constructor.kind; }

    setConfig(config) {
      if (!config || !config.entity) throw new Error('Choose the Nest thermostat (entity)');
      this._config = config;
      this._sig = '';
      if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    }
    getCardSize() { return 6; }
    getGridOptions() { return { columns: 12, rows: 'auto', min_columns: 6 }; }
    connectedCallback() { this._tick = setInterval(() => this._render(), 60000); }
    disconnectedCallback() { clearInterval(this._tick); }
    set hass(hass) {
      this._hass = hass;
      if (this._dialog) this._dialog.hass = hass;
      if (!this._ids && !this._looking) {
        this._looking = true;
        lookup(hass, this._config.entity).then((r) => { this._ids = r; this._err = ''; }, (e) => { this._err = (e && e.message) || 'Not found'; })
          .finally(() => { this._looking = false; this._sig = ''; this._render(); });
      }
      this._render();
    }

    _open(day) {
      const detail = { entity: this._config.entity, kind: this.kind, day };
      if (this._config.edit) {
        window.__fledgeScheduleEdit = detail;
        window.dispatchEvent(new CustomEvent('fledge-schedule-edit', { detail }));
        navigate(this._config.edit);
        return;
      }
      if (this._dialog) return;
      const dlg = document.createElement('fledge-schedule-dialog');
      dlg.setup({ entity: this._config.entity, kind: this.kind, day, title: this._title() }, () => { this._dialog = null; });
      dlg.hass = this._hass;
      this._dialog = dlg;
      document.body.appendChild(dlg);
    }

    _title() { return this._config.name || (this.kind === HEATING ? 'Heating' : 'Hot water'); }

    _status(week) {
      const hass = this._hass;
      const ti = todayIdx(); const n = nowMin();
      if (this.kind === HEATING) {
        const clim = hass.states[this._config.entity];
        const target = clim ? parseFloat(clim.attributes.temperature) : NaN;
        const heating = clim && clim.attributes.hvac_action === 'heating';
        const eco = clim && clim.attributes.preset_mode && !['none', 'home', 'comfort'].includes(String(clim.attributes.preset_mode).toLowerCase());
        let next = null;
        for (let off = 0; off <= 7 && !next; off++) {
          const p = (week[DAYS[(ti + off) % 7]] || []).find((x) => off > 0 || x.m > n);
          if (p) next = { ...p, off };
        }
        const when = next && (next.off === 0 ? '' : next.off === 1 ? 'tomorrow ' : `${SHORT[DAYS[(ti + next.off) % 7]]} `);
        let line;
        if (!clim || clim.state === 'off') line = 'Heating off';
        else if (eco) line = `Eco · the schedule's on hold`;
        else line = next ? `Set to ${fmtT(target)} · ${fmtT(next.t)} ${when}at ${hhmm(next.m)}` : `Set to ${fmtT(target)}`;
        return { line, pill: isNaN(target) ? '–' : fmtT(target), lit: !!heating, icon: heating ? 'mdi:fire' : 'mdi:thermostat', paused: false };
      }
      const sw = this._ids.hot_water_switch && hass.states[this._ids.hot_water_switch];
      const on = !sw || sw.state === 'on';
      let cur = null; let next = null;
      for (let off = 0; off <= 7 && !next; off++) {
        const d = DAYS[(ti + off) % 7];
        for (const p of waterSpans(week, d).sort((a, b) => a.s - b.s)) {
          if (off === 0 && p.s <= n && n < p.e) { cur = p; continue; }
          if ((off > 0 || p.s > n) && !p.spill) { next = { ...p, off }; break; }
        }
      }
      const when = (x) => (x.off === 0 ? '' : x.off === 1 ? 'tomorrow ' : `${SHORT[DAYS[(ti + x.off) % 7]]} `);
      let line;
      if (!on) line = 'Schedule paused · hot water off';
      else if (cur) line = `On until ${hhmm(cur.e % DAY_MIN)}${next ? ` · next ${when(next)}${hhmm(next.s)}` : ''}`;
      else line = next ? `Off now · next ${when(next)}${hhmm(next.s)}–${hhmm(next.e % DAY_MIN)}` : 'No hot water times';
      return { line, pill: on ? 'On' : 'Paused', lit: on, icon: on ? (cur ? 'mdi:water-boiler' : 'mdi:calendar-clock') : 'mdi:pause', paused: !on };
    }

    _pill(e) {
      e.stopPropagation();
      if (this.kind === HEATING) {
        this.dispatchEvent(new CustomEvent('hass-more-info', { detail: { entityId: this._config.entity }, bubbles: true, composed: true }));
        return;
      }
      const sw = this._ids && this._ids.hot_water_switch;
      if (!sw) return;
      this._hass.callService('switch', this._hass.states[sw].state === 'on' ? 'turn_off' : 'turn_on', { entity_id: sw });
      if (navigator.vibrate) try { navigator.vibrate(10); } catch (_) { /* no haptics */ }
    }

    _render() {
      if (!this._hass || !this._config || !this.shadowRoot) return;
      const week = this._ids ? readWeek(this._hass, this._ids, this.kind) : null;
      const st = week && this._status(week);
      const n = nowMin();
      const sig = JSON.stringify([n, week, st, this._err, this._config]);
      if (sig === this._sig) return;
      this._sig = sig;
      const today = DAYS[todayIdx()];
      let body;
      if (this._err) body = `<div class="hint">${esc(this._config.entity)} isn't a Nest running Fledge.</div>`;
      else if (!week) body = `<div class="hint">${this._ids ? `No ${this.kind === HEATING ? 'heating' : 'hot water'} schedule from this Nest yet.` : 'Loading…'}</div>`;
      else {
        body = `<div class="week">${DAYS.map((d) => `
          <button class="row${d === today ? ' today' : ''}" data-day="${d}" aria-label="Edit ${LONG[d]}">
            <span class="day">${SHORT[d]}</span>
            <span class="bar">${bars(this.kind, week, d, { paused: st.paused })}${d === today ? `<span class="now" style="left:${(n / 14.4).toFixed(3)}%"></span>` : ''}</span>
          </button>`).join('')}</div>${AXIS}
          <button class="btn wide edit"><ha-icon icon="${ICON.edit}"></ha-icon>Edit schedule</button>`;
      }
      this.shadowRoot.innerHTML = `<style>${BASE_CSS}</style>
        <ha-card>
          <div class="head">
            <div class="titles"><div class="title">${esc(this._title())}</div><div class="line">${st ? esc(st.line) : '&nbsp;'}</div></div>
            ${st ? `<button class="pill${st.lit ? ' lit' : ''}" aria-label="${this.kind === HEATING ? 'Thermostat' : 'Pause or resume the schedule'}"><ha-icon icon="${st.icon}"></ha-icon>${st.pill}</button>` : ''}
          </div>
          ${body}
        </ha-card>`;
      const sr = this.shadowRoot;
      const pill = sr.querySelector('.pill');
      if (pill) pill.addEventListener('click', (e) => this._pill(e));
      sr.querySelectorAll('.row').forEach((r) => r.addEventListener('click', () => this._open(r.dataset.day)));
      const ed = sr.querySelector('.edit');
      if (ed) ed.addEventListener('click', () => this._open(today));
    }
  }
  class FledgeHotWaterSchedule extends FledgeSchedule { static get kind() { return WATER; } }

  // ---- The editor: a day at a time, saving every changed day at once, and
  // Copy to days.

  const EDITOR_CSS = `
    .days { display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px; }
    .dchip { height: 46px; border-radius: 12px; border: none; display: grid; place-items: center; align-content: center; gap: 4px; font-size: 12px; font-weight: 700;
      background: var(--fs-inset); box-shadow: var(--fs-inset-shadow); color: var(--secondary-text-color); position: relative; padding: 0; }
    .dchip.on { background: var(--primary-text-color); color: var(--fs-card); box-shadow: none; }
    .dchip .strip { position: relative; width: 26px; height: 5px; border-radius: 3px; overflow: hidden; background: color-mix(in srgb, var(--secondary-text-color) 25%, transparent); }
    .dchip .strip .seg, .dchip .strip .blk { border-radius: 0; }
    .dchip .dot { position: absolute; top: 5px; right: 6px; width: 6px; height: 6px; border-radius: 50%; background: var(--fs-accent); }
    .dchip .dot.bad { background: var(--error-color, #c0392b); }
    .sub { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--secondary-text-color); margin: 14px 2px 8px; font-variant-numeric: tabular-nums; }
    .sub b { color: var(--primary-text-color); font-weight: 700; }
    .preview { position: relative; height: 22px; border-radius: 7px; overflow: hidden; background: var(--fs-inset); box-shadow: var(--fs-inset-shadow); }
    .preview + .axis { grid-template-columns: 1fr; } .preview + .axis > div:first-child { display: none; }
    .list { display: grid; gap: 8px; margin-top: 10px; }
    .ev { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: 8px; padding: 8px 6px 8px 12px; border-radius: 14px;
      background: var(--fs-inset); box-shadow: var(--fs-inset-shadow); }
    .ev.water { grid-template-columns: auto auto auto auto minmax(0, 1fr) auto; gap: 6px; }
    .lbl { font-size: 12px; color: var(--secondary-text-color); }
    .tf { display: inline-flex; align-items: center; gap: 6px; height: 36px; padding: 0 10px; box-sizing: border-box; border-radius: 10px; cursor: pointer;
      background: var(--fs-card); border: 1px solid color-mix(in srgb, var(--secondary-text-color) 30%, transparent); color: var(--primary-text-color); }
    .tf:hover { border-color: color-mix(in srgb, var(--secondary-text-color) 60%, transparent); }
    .tf:focus-within { border-color: var(--fs-accent); box-shadow: 0 0 0 1px var(--fs-accent); }
    .tf ha-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); flex: none; }
    .tf { gap: 2px; padding: 0 4px 0 10px; }
    .tf .colon { font-size: 15px; font-weight: 700; }
    select.tsel { font: inherit; font-size: 15px; font-weight: 600; border: none; background: transparent; color: inherit; font-variant-numeric: tabular-nums;
      padding: 0 2px; margin: 0; height: 34px; cursor: pointer; outline: none; -webkit-appearance: none; appearance: none; color-scheme: light dark; text-align: center; }
    select.tsel:focus-visible { background: color-mix(in srgb, var(--fs-accent) 15%, transparent); border-radius: 6px; }
    .temp { display: flex; align-items: center; gap: 6px; font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
    .temp .v { min-width: 38px; text-align: center; }
    .rb, .del { width: 34px; height: 34px; border: none; border-radius: 999px; display: inline-flex; align-items: center; justify-content: center;
      background: var(--fs-card); color: var(--primary-text-color); padding: 0; }
    .del { background: transparent; color: var(--secondary-text-color); }
    .sw { width: 10px; height: 28px; border-radius: 4px; }
    .add { width: 100%; margin-top: 8px; height: 40px; border-radius: 12px; border: 1.5px dashed color-mix(in srgb, var(--secondary-text-color) 40%, transparent);
      background: transparent; font-size: 13px; font-weight: 600; color: var(--secondary-text-color); display: flex; align-items: center; justify-content: center; gap: 6px; }
    .problem { margin-top: 10px; font-size: 12px; font-weight: 600; color: var(--error-color, #c0392b); }
    .foot { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 8px; margin-top: 16px; }
    .status { font-size: 12px; color: var(--secondary-text-color); text-align: center; min-width: 0; }
    .status.err { color: var(--error-color, #c0392b); }
    .copy { display: grid; gap: 12px; margin-top: 4px; }
    .copy .ct { font-size: 15px; font-weight: 700; color: var(--primary-text-color); }
    .copy .cs { font-size: 12px; color: var(--secondary-text-color); margin-top: -8px; }
    .quick { display: flex; gap: 6px; flex-wrap: wrap; }
    .chip { height: 30px; padding: 0 12px; border-radius: 999px; border: none; font-size: 12px; font-weight: 600; background: var(--fs-inset); box-shadow: var(--fs-inset-shadow); color: var(--primary-text-color); }
    .chip.on { background: var(--primary-text-color); color: var(--fs-card); box-shadow: none; }
    .targets { display: grid; gap: 4px; }
    .dt { display: grid; grid-template-columns: 22px 34px 1fr 58px; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 12px; border: none;
      background: transparent; text-align: left; }
    .dt:hover:not(:disabled) { background: var(--fs-inset); }
    .dt:disabled { opacity: .5; }
    .box { width: 20px; height: 20px; border-radius: 6px; border: 2px solid color-mix(in srgb, var(--secondary-text-color) 45%, transparent); box-sizing: border-box;
      display: grid; place-items: center; color: var(--fs-on-accent); }
    .box ha-icon { --mdc-icon-size: 14px; }
    .dt[aria-pressed="true"] .box { background: var(--fs-accent); border-color: var(--fs-accent); }
    .dt .bar { height: 18px; border-radius: 6px; }
    .dt .note { font-size: 11px; color: var(--secondary-text-color); text-align: right; }
    .dt[aria-pressed="true"] .note { color: var(--fs-accent); font-weight: 700; }
    .toast { margin-top: 12px; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 8px 10px 14px; border-radius: 12px;
      background: var(--primary-text-color); color: var(--fs-card); font-size: 13px; font-weight: 600; }
    .toast button { border: none; background: transparent; color: inherit; font-weight: 800; padding: 4px 8px; text-decoration: underline; }
  `;

  class FledgeScheduleEditor extends HTMLElement {
    static get kind() { return HEATING; }
    static getConfigForm() { return FledgeSchedule.getConfigForm(); }
    static getStubConfig(hass) { return FledgeSchedule.getStubConfig(hass); }
    get kind() { return this._kind || this.constructor.kind; }

    setConfig(config) {
      if (!config || !config.entity) throw new Error('Choose the Nest thermostat (entity)');
      this._config = config;
      this._kind = config.kind || null;
      this._drafts = {};
      this._sig = '';
      this._mode = 'edit';
      if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    }
    getCardSize() { return 8; }
    getGridOptions() { return { columns: 12, rows: 'auto', min_columns: 6 }; }
    connectedCallback() {
      this._onReq = (e) => {
        const d = e.detail;
        if (!d || d.entity !== this._config.entity || d.kind !== this.kind) return;
        this._day = d.day; this._mode = 'edit'; this._drafts = {}; this._sig = ''; this._render(true);
      };
      window.addEventListener('fledge-schedule-edit', this._onReq);
      const pending = window.__fledgeScheduleEdit;
      if (pending && pending.entity === this._config.entity && pending.kind === this.kind) this._day = pending.day;
    }
    disconnectedCallback() {
      window.removeEventListener('fledge-schedule-edit', this._onReq);
      clearTimeout(this._undoTimer); clearTimeout(this._confirmTimer);
    }
    set day(d) { this._day = d; }
    set hass(hass) {
      this._hass = hass;
      if (!this._ids && !this._looking) {
        this._looking = true;
        lookup(hass, this._config.entity).then((r) => { this._ids = r; this._err = ''; }, (e) => { this._err = (e && e.message) || 'Not found'; })
          .finally(() => { this._looking = false; this._sig = ''; this._render(true); });
      }
      this._render();
    }

    _week() { return this._ids ? readWeek(this._hass, this._ids, this.kind) : null; }
    // A day being edited: the week's list when it was opened (base, as a
    // norm key), the edited list, and once saved, what was saved (pending)
    // until the Nest's sensor shows it.
    _draft(week, d) {
      if (!this._drafts[d]) this._drafts[d] = { base: norm(week[d]), list: week[d].map((x) => ({ ...x })) };
      return this._drafts[d].list;
    }
    _current(week, d) { return this._drafts[d] ? this._drafts[d].list : week[d]; }
    _dirty(week, d) {
      const dr = this._drafts[d];
      if (!dr) return false;
      const now = norm(dr.list);
      return now !== (dr.pending || norm(week[d]));
    }
    _pending() { return DAYS.filter((d) => this._drafts[d] && this._drafts[d].pending); }
    _merged(week) { return Object.fromEntries(DAYS.map((d) => [d, this._current(week, d)])); }
    // The changed days whose times the Nest won't take, with why.
    _problems(week) {
      return DAYS.filter((d) => this._dirty(week, d)).map((d) => [d, KIND[this.kind].problem(this._drafts[d].list)]).filter(([, p]) => p);
    }

    // Drafts the week has caught up with go: saved ones the Nest now shows
    // (kept, if edited again since), and ones never edited, or edited to
    // match, once the day changes under them.
    _settle(week) {
      let confirmed = false;
      Object.keys(this._drafts).forEach((d) => {
        const dr = this._drafts[d]; const now = norm(week[d]);
        if (dr.pending) {
          if (dr.pending !== now) return;
          confirmed = true;
          if (norm(dr.list) === now) delete this._drafts[d]; else { delete dr.pending; dr.base = now; }
          return;
        }
        if (dr.base !== now && (norm(dr.list) === dr.base || norm(dr.list) === now)) delete this._drafts[d];
      });
      if (confirmed && !this._pending().length) {
        clearTimeout(this._confirmTimer);
        this._saved = Date.now();
        setTimeout(() => this._render(true), 2100);
      }
    }

    // One call for every day given: {mon: [...]} in the editor's terms.
    _write(days) {
      const out = Object.fromEntries(Object.entries(days).map(([d, l]) => [d, KIND[this.kind].write(l)]));
      return this._hass.callService('fledge', 'set_schedule', { entity_id: this._config.entity, [this.kind]: out });
    }

    // Writes the days, then keeps them as drafts until the Nest shows them:
    // the service only sends them on, so the editor shows what was saved
    // meanwhile, and says so if the Nest never takes it.
    _commit(week, days, after) {
      this._busy = true; this._error = ''; this._render(true);
      return this._write(days).then(() => {
        Object.entries(days).forEach(([d, l]) => {
          const list = l.map((x) => ({ ...x }));
          this._drafts[d] = { base: this._drafts[d] ? this._drafts[d].base : norm(week[d]), list, pending: norm(list) };
        });
        this._busy = false;
        if (after) after();
        clearTimeout(this._confirmTimer);
        this._confirmTimer = setTimeout(() => {
          const late = this._pending();
          if (!late.length) return;
          late.forEach((d) => delete this._drafts[d].pending);
          this._error = `The Nest hasn't shown ${listDays(late)} yet. Is it online? Save again to retry.`;
          this._render(true);
        }, CONFIRM_MS);
        this._render(true);
      }).catch((err) => { this._busy = false; this._error = (err && err.message) || "Couldn't save"; this._render(true); });
    }

    _save(week) {
      if (this._problems(week).length) return;
      const days = Object.fromEntries(DAYS.filter((d) => this._dirty(week, d)).map((d) => [d, this._drafts[d].list]));
      if (!Object.keys(days).length) return;
      this._commit(week, days);
    }

    _copy(week) {
      const from = this._day;
      const targets = [...this._targets];
      const source = this._current(week, from).map((x) => ({ ...x }));
      const days = Object.fromEntries(targets.map((d) => [d, source]));
      if (this._dirty(week, from)) days[from] = source;
      // Undo puts back only the days copied to: the source day's own
      // changes, saved with the copy, stay.
      const before = Object.fromEntries(targets.map((d) => [d, week[d]]));
      this._commit(week, days, () => {
        this._mode = 'edit';
        this._undo = { text: `Copied ${LONG[from]} to ${listDays(targets)}`, before };
        clearTimeout(this._undoTimer);
        this._undoTimer = setTimeout(() => { this._undo = null; this._render(true); }, UNDO_MS);
      });
    }

    _undoCopy(week) {
      const u = this._undo;
      if (!u) return;
      this._undo = null; clearTimeout(this._undoTimer);
      this._commit(week, u.before);
    }

    _render(force) {
      if (!this._hass || !this._config || !this.shadowRoot) return;
      const week = this._week();
      const sig = JSON.stringify([week, this._err]);
      if (!force && sig === this._sig) return;
      this._sig = sig;
      if (week) this._settle(week);
      if (!this._day) this._day = DAYS[todayIdx()];
      const heat = this.kind === HEATING;
      let body;
      if (this._err) body = `<div class="hint">${esc(this._config.entity)} isn't a Nest running Fledge.</div>`;
      else if (!week) body = `<div class="hint">${this._ids ? 'No schedule from this Nest yet.' : 'Loading…'}</div>`;
      else body = this._mode === 'copy' ? this._copyView(week) : this._editView(week);
      this.shadowRoot.innerHTML = `<style>${BASE_CSS}${EDITOR_CSS}</style><ha-card${this._config.bare ? ' style="box-shadow:none;border:none;background:transparent;padding:0"' : ''}>${body}</ha-card>`;
      if (week) (this._mode === 'copy' ? this._bindCopy(week) : this._bindEdit(week, heat));
    }

    _chips(week) {
      const merged = this._merged(week);
      const bad = new Set(this._problems(week).map(([d]) => d));
      return `<div class="days" role="tablist">${DAYS.map((d) => `
        <button class="dchip${d === this._day ? ' on' : ''}" data-day="${d}" role="tab" aria-selected="${d === this._day}" aria-label="${LONG[d]}${bad.has(d) ? ', needs fixing' : this._dirty(week, d) ? ', changed' : ''}">
          ${SHORT[d]}<span class="strip">${bars(this.kind, merged, d, { labels: false })}</span>${this._dirty(week, d) ? `<span class="dot${bad.has(d) ? ' bad' : ''}"></span>` : ''}
        </button>`).join('')}</div>`;
    }

    _footer(week, problem) {
      const changed = DAYS.filter((d) => this._dirty(week, d));
      const blocked = this._problems(week).length > 0;
      const status = this._busy ? 'Saving…' : this._error ? esc(this._error) : this._pending().length ? 'Sending to the Nest…'
        : this._saved && Date.now() - this._saved < 2000 ? 'Saved' : changed.length > 1 ? `${changed.length} days changed` : '';
      const toast = this._undo ? `<div class="toast"><span>${esc(this._undo.text)}</span><button class="undo-copy">Undo</button></div>` : '';
      return `<div class="foot">
          <button class="btn to-copy"${this._busy || problem ? ' disabled' : ''}><ha-icon icon="${ICON.copy}"></ha-icon>Copy to…</button>
          <div class="status${this._error ? ' err' : ''}">${status}</div>
          <button class="btn${changed.length ? ' primary' : ''} save"${changed.length && !this._busy && !blocked ? '' : ' disabled'}>${changed.length > 1 ? `Save ${changed.length} days` : 'Save'}</button>
        </div>${changed.length ? '<button class="btn ghost wide undo-all" style="margin-top:6px;height:32px;font-size:13px">Discard changes</button>' : ''}${toast}`;
    }

    _editView(week) {
      const d = this._day; const heat = this.kind === HEATING;
      const list = this._draft(week, d);
      const merged = this._merged(week);
      const problem = KIND[this.kind].problem(list);
      // Another changed day the Nest won't take holds up Save too.
      const other = problem ? null : this._problems(week).find(([x]) => x !== d);
      const shown = problem || (other ? `${LONG[other[0]]}: ${other[1]}` : '');
      let rows;
      if (heat) {
        const clim = this._hass.states[this._config.entity];
        const step = (clim && parseFloat(clim.attributes.target_temp_step)) || 0.5;
        const minT = Math.max(4.5, (clim && parseFloat(clim.attributes.min_temp)) || 4.5);
        const maxT = Math.min(35, (clim && parseFloat(clim.attributes.max_temp)) || 30);
        this._limits = { step, minT, maxT };
        rows = list.map((x, i) => `
          <div class="ev" data-i="${i}">
            ${timeField('st', x.m, 'Time')}
            <span></span>
            <div class="temp"><button class="rb dn" aria-label="Cooler"${x.t - step < minT ? ' disabled' : ''}><ha-icon icon="${ICON.minus}"></ha-icon></button>
              <span class="v">${fmtT(x.t)}</span>
              <button class="rb up" aria-label="Warmer"${x.t + step > maxT ? ' disabled' : ''}><ha-icon icon="${ICON.plus}"></ha-icon></button></div>
            <button class="del" aria-label="Remove"><ha-icon icon="${ICON.del}"></ha-icon></button>
          </div>`).join('');
      } else {
        rows = list.map((p, i) => `
          <div class="ev water" data-i="${i}">
            <span class="lbl">From</span>${timeField('st', p.s, 'On at')}
            <span class="lbl">to</span>${timeField('en', p.e % DAY_MIN, 'Off at')}
            <span class="lbl">${p.e > DAY_MIN ? 'next day' : ''}</span>
            <button class="del" aria-label="Remove"><ha-icon icon="${ICON.del}"></ha-icon></button>
          </div>`).join('');
      }
      const c = heat ? carry(merged, DAYS.indexOf(d)) : null;
      const note = heat ? (c == null ? '' : `From midnight: ${fmtT(c)}`) : `${list.length || 'No'} time${list.length === 1 ? '' : 's'}`;
      return `${this._chips(week)}
        <div class="sub"><b>${LONG[d]}</b><span>${note}</span></div>
        <div class="preview">${bars(this.kind, merged, d)}</div>${AXIS}
        <div class="list">${rows || `<div class="hint">${heat ? 'No changes: it stays at the temperature from the day before.' : 'Off all day.'}</div>`}</div>
        <button class="add"><ha-icon icon="${ICON.add}"></ha-icon>${heat ? 'Add a change' : 'Add hot water time'}</button>
        ${shown ? `<div class="problem">${esc(shown)}</div>` : ''}
        ${this._footer(week, problem)}`;
    }

    _bindEdit(week, heat) {
      const sr = this.shadowRoot;
      const list = this._draft(week, this._day);
      const re = () => { this._error = ''; this._render(true); };
      sr.querySelectorAll('.dchip').forEach((b) => b.addEventListener('click', () => { this._day = b.dataset.day; re(); }));
      sr.querySelectorAll('.ev').forEach((row) => {
        const i = parseInt(row.dataset.i, 10);
        const st = row.querySelector('.tf.st');
        if (heat) {
          onTime(st, (m) => { list[i].m = m; list.sort((a, b) => a.m - b.m); re(); });
          const { step, minT, maxT } = this._limits;
          row.querySelector('.dn').addEventListener('click', () => { list[i].t = Math.max(minT, Math.round((list[i].t - step) * 2) / 2); re(); });
          row.querySelector('.up').addEventListener('click', () => { list[i].t = Math.min(maxT, Math.round((list[i].t + step) * 2) / 2); re(); });
        } else {
          onTime(st, (m) => {
            const len = list[i].e - list[i].s;
            list[i].s = m; list[i].e = m + Math.max(15, len);
            list.sort((a, b) => a.s - b.s); re();
          });
          onTime(row.querySelector('.tf.en'), (m) => { list[i].e = m > list[i].s ? m : m + DAY_MIN; re(); });
        }
        row.querySelector('.del').addEventListener('click', () => { list.splice(i, 1); re(); });
      });
      sr.querySelector('.add').addEventListener('click', () => {
        if (heat) {
          const last = list.length ? list[list.length - 1] : null;
          const m = last ? Math.min(last.m + 120, 23 * 60 + 45) : 7 * 60;
          if (list.some((x) => x.m === m)) return;
          const clim = this._hass.states[this._config.entity];
          list.push({ m, t: last ? last.t : (parseFloat(clim && clim.attributes.temperature) || 20) });
          list.sort((a, b) => a.m - b.m);
        } else {
          const end = list.length ? Math.max(...list.map((x) => x.e)) : 6 * 60;
          const s = Math.min(end + 60, 22 * 60);
          list.push({ s, e: s + 60 }); list.sort((a, b) => a.s - b.s);
        }
        re();
      });
      sr.querySelector('.save').addEventListener('click', () => this._save(week));
      sr.querySelector('.to-copy').addEventListener('click', () => {
        this._mode = 'copy';
        const from = this._current(week, this._day);
        const merged = this._merged(week);
        this._targets = new Set(DAYS.filter((d) => d !== this._day && !same(merged[d], from)
          && (DAYS.indexOf(this._day) < 5) === (DAYS.indexOf(d) < 5)));
        re();
      });
      const all = sr.querySelector('.undo-all');
      if (all) all.addEventListener('click', () => { this._drafts = {}; re(); });
      const u = sr.querySelector('.undo-copy');
      if (u) u.addEventListener('click', () => this._undoCopy(week));
    }

    _copyView(week) {
      const from = this._day;
      const source = this._current(week, from);
      const merged = this._merged(week);
      const eligible = DAYS.filter((d) => d !== from && !same(merged[d], source));
      [...this._targets].forEach((d) => { if (!eligible.includes(d)) this._targets.delete(d); });
      const chosen = eligible.filter((d) => this._targets.has(d));
      const heat = this.kind === HEATING;
      const what = heat ? `${source.length} change${source.length === 1 ? '' : 's'}` : `${source.length || 'no'} time${source.length === 1 ? '' : 's'}`;
      const unsaved = this._dirty(week, from) ? ` Also saves ${LONG[from]}'s changes.` : '';
      return `<div class="copy">
          <div class="ct">Copy ${LONG[from]}'s ${heat ? 'heating' : 'hot water'}</div>
          <div class="cs">The days you choose get ${SHORT[from]}'s ${what}, replacing what they have.${unsaved}</div>
          <div class="quick">${QUICK.map(([n, ds]) => {
            const pick = ds.filter((d) => eligible.includes(d));
            const on = pick.length && same(chosen, pick);
            return `<button class="chip${on ? ' on' : ''}" data-q="${n}"${pick.length ? '' : ' disabled'}>${n}</button>`;
          }).join('')}</div>
          <div class="targets">${DAYS.filter((d) => d !== from).map((d) => {
            const ok = eligible.includes(d); const on = ok && this._targets.has(d);
            const shown = on ? { ...merged, [d]: source } : merged;
            return `<button class="dt" data-day="${d}" aria-pressed="${on}"${ok ? '' : ' disabled'} aria-label="${LONG[d]}">
              <span class="box">${on ? `<ha-icon icon="${ICON.check}"></ha-icon>` : ''}</span><span class="day">${SHORT[d]}</span>
              <span class="bar">${bars(this.kind, shown, d, { labels: false })}</span>
              <span class="note">${ok ? (on ? 'Replace' : '') : 'Same'}</span></button>`;
          }).join('')}</div>
          <div class="foot">
            <button class="btn ghost cancel">Cancel</button>
            <div class="status${this._error ? ' err' : ''}">${this._busy ? 'Copying…' : esc(this._error || '')}</div>
            <button class="btn primary go"${chosen.length && !this._busy ? '' : ' disabled'}>${chosen.length ? `Copy to ${chosen.length} day${chosen.length > 1 ? 's' : ''}` : 'Choose days'}</button>
          </div>
        </div>`;
    }

    _bindCopy(week) {
      const sr = this.shadowRoot;
      const re = () => this._render(true);
      sr.querySelectorAll('.dt').forEach((b) => b.addEventListener('click', () => {
        const d = b.dataset.day; if (this._targets.has(d)) this._targets.delete(d); else this._targets.add(d); re();
      }));
      sr.querySelectorAll('.chip').forEach((b) => b.addEventListener('click', () => {
        const ds = QUICK.find((q) => q[0] === b.dataset.q)[1];
        const source = this._current(week, this._day); const merged = this._merged(week);
        this._targets = new Set(ds.filter((d) => d !== this._day && !same(merged[d], source))); re();
      }));
      sr.querySelector('.cancel').addEventListener('click', () => { this._mode = 'edit'; this._error = ''; re(); });
      sr.querySelector('.go').addEventListener('click', () => this._copy(week));
    }
  }
  class FledgeHotWaterScheduleEditor extends FledgeScheduleEditor { static get kind() { return WATER; } }

  // ---- The card's own dialog, when it has no popup of its own.

  class FledgeScheduleDialog extends HTMLElement {
    setup(opts, onClose) {
      this._opts = opts; this._onClose = onClose;
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.innerHTML = `<style>
          :host { position: fixed; inset: 0; z-index: 8; display: grid; place-items: center; }
          .scrim { position: absolute; inset: 0; background: var(--mdc-dialog-scrim-color, rgba(0,0,0,.32)); animation: fade .15s ease-out; }
          .panel { position: relative; box-sizing: border-box; width: min(560px, 100vw); max-height: min(92vh, 900px); overflow: auto; overscroll-behavior: contain;
            background: var(--ha-dialog-surface-background, var(--ha-card-background, var(--card-background-color, #fff))); color: var(--primary-text-color);
            border-radius: var(--ha-dialog-border-radius, 24px); padding: 8px 8px 12px; box-shadow: 0 12px 40px rgba(0,0,0,.3); animation: rise .18s ease-out; }
          .top { display: flex; align-items: center; gap: 8px; padding: 8px 8px 0 16px; }
          h2 { flex: 1; margin: 0; font-size: var(--ha-font-size-xl, 20px); font-weight: var(--ha-font-weight-bold, 700); }
          .x { width: 40px; height: 40px; border: none; border-radius: 999px; background: transparent; color: var(--primary-text-color); cursor: pointer; display: grid; place-items: center; }
          .x:focus-visible { outline: 2px solid var(--accent-color); }
          @media (max-width: 600px) { :host { place-items: end stretch; } .panel { width: 100vw; max-height: 94vh; border-radius: 24px 24px 0 0;
            padding-bottom: calc(12px + env(safe-area-inset-bottom, 0px)); } }
          @keyframes fade { from { opacity: 0; } } @keyframes rise { from { transform: translateY(16px); opacity: 0; } }
          @media (prefers-reduced-motion: reduce) { .scrim, .panel { animation: none; } }
        </style>
        <div class="scrim"></div>
        <div class="panel" role="dialog" aria-modal="true" aria-label="${esc(opts.title)} schedule">
          <div class="top"><h2>${esc(opts.title)}</h2><button class="x" aria-label="Close"><ha-icon icon="${ICON.close}"></ha-icon></button></div>
          <div class="body"></div>
        </div>`;
      const ed = document.createElement(opts.kind === HEATING ? 'fledge-heating-schedule-editor' : 'fledge-hot-water-schedule-editor');
      ed.setConfig({ entity: opts.entity, bare: true });
      ed.day = opts.day;
      this._editor = ed;
      this.shadowRoot.querySelector('.body').appendChild(ed);
      this.shadowRoot.querySelector('.scrim').addEventListener('click', () => this.close());
      this.shadowRoot.querySelector('.x').addEventListener('click', () => this.close());
      this._key = (e) => { if (e.key === 'Escape') this.close(); };
      window.addEventListener('keydown', this._key);
      setTimeout(() => this.shadowRoot.querySelector('.x').focus(), 0);
    }
    set hass(hass) { if (this._editor) this._editor.hass = hass; }
    close() {
      window.removeEventListener('keydown', this._key);
      this.remove();
      if (this._onClose) this._onClose();
    }
  }

  customElements.define('fledge-heating-schedule', FledgeSchedule);
  customElements.define('fledge-hot-water-schedule', FledgeHotWaterSchedule);
  customElements.define('fledge-heating-schedule-editor', FledgeScheduleEditor);
  customElements.define('fledge-hot-water-schedule-editor', FledgeHotWaterScheduleEditor);
  customElements.define('fledge-schedule-dialog', FledgeScheduleDialog);

  window.customCards = window.customCards || [];
  window.customCards.push(
    { type: 'fledge-heating-schedule', name: 'Fledge heating schedule', description: "The Nest's own weekly heating schedule, with an editor and Copy to days.", preview: false },
    { type: 'fledge-hot-water-schedule', name: 'Fledge hot water schedule', description: "The Nest's own weekly hot water schedule, with an editor and Copy to days.", preview: false },
    { type: 'fledge-heating-schedule-editor', name: 'Fledge heating schedule editor', description: 'The heating schedule editor on its own, for a popup.', preview: false },
    { type: 'fledge-hot-water-schedule-editor', name: 'Fledge hot water schedule editor', description: 'The hot water schedule editor on its own, for a popup.', preview: false },
  );
})();
