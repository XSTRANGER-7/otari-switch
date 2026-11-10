/* Otari Switch app. Plain JS, no framework, no build step.
   Every number comes from /api/state, /api/run(s), /api/preview or /api/insights.
   All user and API data is inserted as text (textContent), never as HTML. */
(() => {
  "use strict";

  const TOKEN = document.querySelector('meta[name="switch-token"]').content;
  const main = document.getElementById("main");
  const viewport = document.getElementById("viewport");
  const topbar = document.getElementById("topbar");
  const dock = document.getElementById("dock");
  const shell = document.getElementById("shell");

  let S = null; // /api/state
  let RUNS = null; // cached /api/runs
  let currentTab = "overview";
  const HDR = {};

  /* ================================================================ helpers */
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  };
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const sum = (arr) => arr.reduce((a, b) => a + (Number(b) || 0), 0);

  async function api(path, body, method) {
    const opts = { method: method || (body ? "POST" : "GET"), headers: { "X-Switch-Token": TOKEN } };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(path, opts); } catch {
      throw Object.assign(new Error("Lost connection to Otari Switch."),
        { fix: "Check that `otari-switch ui` is still running, then reload this page." });
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw Object.assign(new Error(data.error || `Request failed (${res.status})`), {
        fix: data.fix || (res.status === 401 ? "Reload the page to start a new session." : ""),
        request_id: data.request_id, status: res.status, data,
      });
    }
    return data;
  }

  function append(el, kids) {
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false || kid === "") continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  /** replaceChildren that skips null/false/"" (DOM would print them as text). */
  function put(el, ...kids) { el.replaceChildren(); return append(el, kids); }
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "style") for (const [sk, sv] of Object.entries(v)) sk.startsWith("--") ? el.style.setProperty(sk, sv) : (el.style[sk] = sv);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    return append(el, kids);
  }
  const SVGNS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "text") el.textContent = v; else el.setAttribute(k, v);
    }
    return append(el, kids);
  }

  // Static, trusted icon markup only. Never put data in here.
  const ICONS = {
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    bell: '<path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 1.5h-15z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>',
    chevDown: '<path d="m6 9 6 6 6-6"/>',
    chevRight: '<path d="m9 6 6 6-6 6"/>',
    calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="3.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    x: '<path d="m6 6 12 12M18 6 6 18"/>',
    dash: '<path d="M6 12h12"/>',
    up: '<path d="M7 17 17 7M9 7h8v8"/>',
    down: '<path d="M7 7l10 10M17 9v8H9"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="3"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    play: '<path d="M8 5.5v13l11-6.5z"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/>',
    branch: '<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="7" r="2.2"/><path d="M6 7.2v9.6M18 9.2c0 5-6 3.8-11 8"/>',
    pr: '<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="19" r="2.2"/><path d="M6 7.2v9.6M18 16.8V9a3 3 0 0 0-3-3h-4M13 3.5 10.5 6 13 8.5"/>',
    plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4"/>',
    scan: '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M7 12h10"/>',
    wave: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
    judge: '<path d="M12 4v16M8 20h8M5 7h14M5 7l-2.5 6a2.8 2.8 0 0 0 5 0zM19 7l-2.5 6a2.8 2.8 0 0 0 5 0z"/>',
    gauge: '<path d="M4 17a8 8 0 1 1 16 0"/><path d="m12 17 4-5"/>',
    doc: '<path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM14 3v5h5M9 13h6M9 17h6"/>',
    shield: '<path d="M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 8-8M16 7l2 2M14 9l2 2"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5z"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M3 3l18 18M10.6 6a10 10 0 0 1 1.4-.1c6 0 9.5 6.1 9.5 6.1a17 17 0 0 1-3 3.6M6.3 7.6A16 16 0 0 0 2.5 12s3.5 6.5 9.5 6.5a9.6 9.6 0 0 0 4.2-1M9.9 10a3 3 0 0 0 4.1 4.1"/>',
    info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
    warn: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2h.01"/>',
    sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    arrowRight: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2.5"/>',
    code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
    folder: '<path d="M3.5 7a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    terminal: '<rect x="3" y="4.5" width="18" height="15" rx="3.5"/><path d="m7 10 3 2.5L7 15M12.5 15H17"/>',
    tag: '<path d="M3.5 12V4.5h7.5l9.5 9.5-7.5 7.5z"/><circle cx="8" cy="9" r="1.4"/>',
  };
  function icon(name, cls) {
    const el = document.createElementNS(SVGNS, "svg");
    el.setAttribute("viewBox", "0 0 24 24");
    el.setAttribute("aria-hidden", "true");
    el.setAttribute("class", "i" + (cls ? " " + cls : ""));
    el.innerHTML = ICONS[name] || "";
    return el;
  }
  const SPARK = "M0 -7 C0.8 -2 2 -0.8 7 0 C2 0.8 0.8 2 0 7 C-0.8 2 -2 0.8 -7 0 C-2 -0.8 -0.8 -2 0 -7Z";

  /* ---------- formatting ---------- */
  const nf = (v, d = 0) => Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  function moneyParts(v, d) {
    if (v == null || Number.isNaN(Number(v))) return ["n/a", ""];
    const a = Math.abs(v);
    // under $1: two significant digits, so $0.32, $0.013 and $0.0039 line up in one list
    const dd = d ?? (a === 0 || a >= 1 ? 2 : clamp(1 - Math.floor(Math.log10(a)), 2, 6));
    const [i, f] = nf(a, dd).split(".");
    return [(v < 0 ? "−$" : "$") + i, f ? "." + f : ""];
  }
  const money = (v, d) => moneyParts(v, d).join("");
  const spendParts = (v) => moneyParts(v, v != null && Math.abs(v) < 1 ? 4 : 2);
  const pctParts = (v) => (v == null ? ["n/a", ""] : [String(Math.round(v * 100)), "%"]);
  const pct = (v) => pctParts(v).join("");
  const intParts = (v) => (v == null ? ["n/a", ""] : [nf(Math.round(v)), ""]);
  function compactParts(v) {
    if (v == null) return ["n/a", ""];
    const a = Math.abs(v);
    if (a >= 1e6) { const [x, y] = (v / 1e6).toFixed(1).split("."); return [x, `.${y}M`]; }
    if (a >= 1e4) return [nf(Math.round(v / 1e3)), "K"];
    if (a >= 1e3) { const [x, y] = (v / 1e3).toFixed(1).split("."); return [x, `.${y}K`]; }
    return [nf(Math.round(v)), ""];
  }
  const compact = (v) => compactParts(v).join("");
  const msParts = (v) => (v == null ? ["n/a", ""] : [nf(Math.round(v)), " ms"]);

  const toDate = (iso) => { if (iso == null) return null; const d = typeof iso === "number" ? new Date(iso) : new Date(iso); return Number.isNaN(d.getTime()) ? null : d; };
  const dShort = (d) => d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  const tShort = (d) => d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  function dateRange(a, b) {
    const da = toDate(a), db = toDate(b ?? a);
    if (!da) return "";
    if (!db || da.toDateString() === db.toDateString()) return dShort(da);
    return `${dShort(da)} – ${dShort(db)}`;
  }
  function rel(iso) {
    const d = toDate(iso);
    if (!d) return "";
    const sec = (Date.now() - d.getTime()) / 1000;
    if (sec < 45) return "just now";
    if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
    if (sec < 86400) { const x = Math.round(sec / 3600); return `${x} hour${x === 1 ? "" : "s"} ago`; }
    const x = Math.round(sec / 86400);
    if (x === 1) return "yesterday";
    if (x < 14) return `${x} days ago`;
    return dShort(d);
  }
  const fnName = (id) => String(id).split(":").slice(-2, -1)[0] || String(id);
  const today = (x) => x.app_model || x.frontier_model || "dynamic model";
  const fileOf = (id) => String(id).split(":")[0];
  const short = (m) => String(m || "").split("/").pop();
  function initials(name) {
    const parts = short(name).replace(/[^a-z0-9]+/gi, " ").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    const a = parts[0][0], b = parts[1] ? parts[1][0] : parts[0][1] || "";
    return (a + b).toUpperCase();
  }
  const BADGE_TONES = ["lime", "lav", "violet", "white"];
  function toneFor(name) { let x = 0; for (const ch of String(name)) x = (x * 31 + ch.charCodeAt(0)) >>> 0; return BADGE_TONES[x % BADGE_TONES.length]; }

  /* ---------- small components ---------- */
  function badge(name, opts = {}) {
    return h("span", { class: `badge ${opts.tone || toneFor(name)}${opts.cls ? " " + opts.cls : ""}`, title: opts.title || name, "aria-label": opts.label || null }, opts.text || initials(name));
  }
  function badgeStack(names, max = 4) {
    const list = names.slice(0, max).map((n) => badge(n));
    if (names.length > max) list.push(h("span", { class: "badge more", title: names.slice(max).join(", ") }, `+${names.length - max}`));
    return h("span", { class: "stack", "aria-label": names.join(", ") }, list);
  }
  function chip(text, tone = "gray", ic) {
    return h("span", { class: `chip ${tone}` }, ic ? icon(ic) : null, h("span", { text }));
  }
  function deltaChip(cur, prev, { unit = "pts", scale = 100, digits = 0 } = {}) {
    if (cur == null || prev == null) return null;
    const d = (cur - prev) * scale;
    if (Math.abs(d) < 0.5 * Math.pow(10, -digits)) return chip("No change vs last run", "gray");
    const txt = `${d > 0 ? "+" : "−"}${nf(Math.abs(d), digits)}${unit === "%" ? "%" : " " + unit}`;
    return h("span", { class: `chip ${d > 0 ? "lime" : "lav"}`, title: "Compared with the previous run" }, h("span", { text: txt }), icon(d > 0 ? "up" : "down"));
  }
  function countTo(to, set, dur = 750) {
    if (to == null || reduced()) { set(to); return; }
    const t0 = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      set(k < 1 ? to * e : to);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  function bigNum(value, fmt, cls) {
    if (fmt === moneyParts && value != null) { const d = moneyParts(value)[1].length - 1; fmt = (v) => moneyParts(v, Math.max(0, d)); }
    const int = h("span", { class: "int" }), dec = h("span", { class: "dec" });
    const el = h("span", { class: `num${cls ? " " + cls : ""}` }, int, dec);
    el.set = (v) => { const [a, b] = fmt(v); int.textContent = a; dec.textContent = b; };
    el.set(value == null ? null : 0);
    countTo(value, el.set);
    return el;
  }
  function kpi(label, value, fmt, { sub, chip: ch, cls, empty: emptyText } = {}) {
    const num = value == null && emptyText ? h("span", { class: "num muted-num", text: emptyText }) : bigNum(value, fmt);
    return h("div", { class: `kpi${cls ? " " + cls : ""}` },
      h("div", { class: "kpi-l" }, h("div", { class: "lbl", text: label }), num, sub ? h("div", { class: "sub", text: sub }) : null),
      ch ? h("div", { class: "kpi-r" }, ch) : null);
  }
  function tile({ span = 4, wide = false, cls = "", title, sub, right, label } = {}, ...kids) {
    const t = h("section", { class: `tile s${span}${wide ? " wide" : ""}${cls ? " " + cls : ""}`, "aria-label": label || title || null });
    if (title || right) {
      t.append(h("div", { class: "tile-h" },
        h("div", { class: "tile-ht" }, title ? h("h2", { text: title }) : null, sub ? h("p", { class: "tile-sub", text: sub }) : null),
        right || null));
    }
    return append(t, kids);
  }
  function screen(...tiles) { return h("div", { class: "screen" }, h("div", { class: "grid" }, tiles)); }
  function head(title, { date, extra, sub } = {}) {
    return h("div", { class: "screen-head" },
      h("h1", { text: title, tabindex: "-1" }),
      (date || extra) ? h("div", { class: "head-meta" },
        date ? h("span", { class: "daterange" }, icon("calendar"), typeof date === "string" ? h("span", { text: date }) : date) : null,
        extra || null) : null,
      sub ? h("p", { class: "lede", text: sub }) : null);
  }
  function demoBadge(long = true) {
    return h("span", { class: "demo-pill", title: "Bundled demo gateway: demo models, no key, no cost" }, h("i"), long ? "Demo gateway" : "Demo");
  }
  function emptyState(img, title, text, ...actions) {
    return h("div", { class: "empty" },
      img ? h("img", { src: `/static/img/${img}.svg`, alt: "", width: "240", height: "180", loading: "lazy" }) : null,
      h("h3", { text: title }), text ? h("p", { text }) : null,
      actions.length ? h("div", { class: "actions center" }, actions) : null);
  }
  function note(kind, title, text, extra) {
    return h("div", { class: `note ${kind}`, role: kind === "err" ? "alert" : null },
      icon(kind === "err" ? "warn" : kind === "ok" ? "check" : "info"),
      h("div", {}, title ? h("b", { text: title }) : null, text ? h("span", { text }) : null, extra || null));
  }
  function errNote(e) {
    return note("err", e.message || String(e), e.fix || "", e.request_id ? h("span", { class: "mono small", text: `Otari request ${e.request_id}` }) : null);
  }
  function skelRows(n = 3) { return h("div", { class: "skel-list", "aria-hidden": "true" }, Array.from({ length: n }, (_, i) => h("div", { class: "skel", style: { width: `${80 - i * 12}%` } }))); }

  function btn(label, onClick, { kind = "primary", ic, cls, aria, disabled } = {}) {
    const b = h("button", { class: `btn ${kind}${cls ? " " + cls : ""}`, type: "button", "aria-label": aria || null, disabled: disabled || null }, ic ? icon(ic) : null, h("span", { text: label }));
    b.addEventListener("click", async () => {
      if (b.disabled || b.classList.contains("busy")) return;
      const old = [...b.childNodes];
      b.classList.add("busy"); b.setAttribute("aria-busy", "true");
      put(b, h("span", { class: "spin" }), h("span", { text: label }));
      try { await onClick(b); } catch (e) { fail(e); } finally {
        if (b.isConnected) { b.classList.remove("busy"); b.removeAttribute("aria-busy"); put(b, ...old); }
      }
    });
    return b;
  }
  const linkBtn = (label, href, { kind = "primary", ic, cls } = {}) => h("a", { class: `btn ${kind}${cls ? " " + cls : ""}`, href }, ic ? icon(ic) : null, h("span", { text: label }));

  function seg(options, value, onChange, { lime = false, label, small } = {}) {
    const wrap = h("div", { class: `seg${lime ? " lime" : ""}${small ? " small" : ""}`, role: "radiogroup", "aria-label": label || null });
    const thumb = h("span", { class: "thumb", "aria-hidden": "true" });
    wrap.append(thumb);
    let cur = String(value);
    const btns = options.map(([v, text]) => {
      const b = h("button", { type: "button", role: "radio", "aria-checked": String(String(v) === cur), tabindex: String(v) === cur ? "0" : "-1", text });
      b.dataset.v = String(v);
      b.addEventListener("click", () => select(String(v)));
      wrap.append(b);
      return b;
    });
    function place() {
      const b = btns.find((x) => x.dataset.v === cur);
      if (!b || !b.offsetWidth) return;
      thumb.style.width = b.offsetWidth + "px";
      thumb.style.transform = `translateX(${b.offsetLeft - 4}px)`;
    }
    function select(v, focus) {
      if (v === cur) return;
      cur = v;
      btns.forEach((b) => { const on = b.dataset.v === v; b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; if (on && focus) b.focus(); });
      place();
      const opt = options.find(([o]) => String(o) === v);
      onChange && onChange(opt ? opt[0] : v);
    }
    wrap.addEventListener("keydown", (e) => {
      const i = btns.findIndex((b) => b.dataset.v === cur);
      if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); select(btns[(i + 1) % btns.length].dataset.v, true); }
      if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); select(btns[(i - 1 + btns.length) % btns.length].dataset.v, true); }
    });
    const ro = new ResizeObserver(place);
    ro.observe(wrap);
    onCleanup(() => ro.disconnect());
    wrap.set = (v) => select(String(v));
    return wrap;
  }
  function toggle(checked, onChange, label) {
    const input = h("input", { type: "checkbox", role: "switch", "aria-label": label });
    input.checked = !!checked;
    input.addEventListener("change", () => onChange(input.checked, input));
    return h("label", { class: "switch" }, input, h("span", { class: "knob", "aria-hidden": "true" }));
  }
  function copyBtn(value, label) {
    return h("button", { class: "copy", type: "button", title: `Copy ${value}`, "aria-label": `Copy ${label || value}`, onclick: () => {
      (navigator.clipboard ? navigator.clipboard.writeText(value) : Promise.reject(new Error("Clipboard not available")))
        .then(() => toast(`${label ? label[0].toUpperCase() + label.slice(1) : "Value"} copied`), () => toast("Could not copy. Select the text instead.", { error: true }));
    } }, icon("copy"), h("span", { class: "mono", text: value.length > 14 ? value.slice(0, 8) + "…" : value }));
  }
  function vpill(v, pop) {
    if (!v) return h("span", { class: "vpill pending" }, h("span", { class: "spin sm" }), "Judging");
    const m = V[v];
    return h("span", { class: `vpill ${m.tone}${pop ? " pop" : ""}` }, icon(m.icon), m.label);
  }
  const V = {
    switch: { label: "Switch", short: "Switch", tone: "lime", icon: "check", color: "#D5F26B" },
    switch_with_fallback: { label: "Switch with fallback", short: "Fallback", tone: "lav", icon: "refresh", color: "#BBAEF6" },
    keep: { label: "Keep frontier", short: "Keep", tone: "gray", icon: "dash", color: "#4A4A4F" },
  };

  /* ---------- toasts ---------- */
  function toast(msg, opts = {}) {
    const root = document.getElementById("toasts");
    let done = false;
    const close = () => { if (done) return; done = true; t.classList.remove("show"); setTimeout(() => t.remove(), 320); };
    const t = h("div", { class: `toast${opts.error ? " err" : ""}`, role: opts.error ? "alert" : "status" },
      h("span", { class: "toast-ic" }, icon(opts.error ? "warn" : "check")),
      h("div", { class: "toast-t" }, h("b", { text: msg }), opts.fix ? h("span", { text: opts.fix }) : null,
        opts.rid ? h("span", { class: "mono", text: `Otari request ${opts.rid}` }) : null),
      h("button", { class: "toast-x", type: "button", "aria-label": "Dismiss", onclick: close }, icon("x")));
    root.append(t);
    while (root.children.length > 3) root.firstChild.remove();
    requestAnimationFrame(() => requestAnimationFrame(() => t.classList.add("show")));
    setTimeout(close, opts.error ? 8000 : 3200);
  }
  const fail = (e) => { console.warn(e); toast(e.message || String(e), { error: true, fix: e.fix, rid: e.request_id }); };

  /* ---------- cleanup registry (observers, live listeners) ---------- */
  const CLEAN = [];
  function onCleanup(fn) { CLEAN.push(fn); }
  function cleanupScreen() { while (CLEAN.length) { try { CLEAN.pop()(); } catch { /* ignore */ } } }

  /* ---------- sheet ---------- */
  let openSheetClose = null;
  function sheet(title, content, { wide } = {}) {
    if (openSheetClose) openSheetClose(true);
    const root = document.getElementById("sheet-root");
    const prevFocus = document.activeElement;
    const scrim = h("div", { class: "scrim" });
    const closeBtn = h("button", { class: "icon-btn ring", type: "button", "aria-label": "Close" }, icon("x"));
    const body = h("div", { class: "sheet-body" }, content);
    const titleId = "sh-" + Math.random().toString(36).slice(2, 8);
    const sh = h("div", { class: `sheet${wide ? " wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
      h("div", { class: "grab", "aria-hidden": "true" }),
      h("div", { class: "sheet-head" }, h("h2", { id: titleId, text: title }), closeBtn), body);
    let closed = false;
    const close = (instant) => {
      if (closed) return;
      closed = true; openSheetClose = null;
      sh.classList.remove("open"); scrim.classList.remove("open");
      document.removeEventListener("keydown", onKey, true);
      shell.removeAttribute("inert");
      setTimeout(() => { sh.remove(); scrim.remove(); }, instant ? 0 : 420);
      if (!instant && prevFocus && prevFocus.isConnected) prevFocus.focus({ preventScroll: true });
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); return; }
      if (e.key === "Tab") {
        const f = [...sh.querySelectorAll('button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter((x) => !x.disabled && x.offsetParent !== null);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    };
    closeBtn.addEventListener("click", () => close());
    scrim.addEventListener("click", () => close());
    let y0 = null;
    sh.addEventListener("touchstart", (e) => { if (body.scrollTop <= 0) y0 = e.touches[0].clientY; }, { passive: true });
    sh.addEventListener("touchmove", (e) => { if (y0 == null) return; const dy = Math.max(0, e.touches[0].clientY - y0); sh.style.setProperty("--drag", dy + "px"); sh.classList.add("dragging"); }, { passive: true });
    sh.addEventListener("touchend", (e) => { if (y0 == null) return; const dy = e.changedTouches[0].clientY - y0; y0 = null; sh.classList.remove("dragging"); sh.style.removeProperty("--drag"); if (dy > 110) close(); });
    root.append(scrim, sh);
    shell.setAttribute("inert", "");
    document.addEventListener("keydown", onKey, true);
    requestAnimationFrame(() => requestAnimationFrame(() => { scrim.classList.add("open"); sh.classList.add("open"); }));
    setTimeout(() => (sh.querySelector("[data-autofocus]") || closeBtn).focus({ preventScroll: true }), 40);
    openSheetClose = close;
    return { close, body, el: sh };
  }

  /* ================================================================ charts */
  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  function monotonePath(pts) {
    const n = pts.length;
    if (!n) return "";
    if (n === 1) return `M${pts[0][0]},${pts[0][1]}`;
    if (n === 2) return `M${pts[0][0]},${pts[0][1]}L${pts[1][0]},${pts[1][1]}`;
    const dx = [], m = [], t = [];
    for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; m[i] = (pts[i + 1][1] - pts[i][1]) / (dx[i] || 1); }
    t[0] = m[0]; t[n - 1] = m[n - 2];
    for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
    for (let i = 0; i < n - 1; i++) {
      if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
      const a = t[i] / m[i], b = t[i + 1] / m[i], q = a * a + b * b;
      if (q > 9) { const k = 3 / Math.sqrt(q); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
    }
    let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
    for (let i = 0; i < n - 1; i++) {
      const [x0, y0] = pts[i], [x1, y1] = pts[i + 1], g = dx[i] / 3;
      d += `C${(x0 + g).toFixed(1)},${(y0 + t[i] * g).toFixed(1)} ${(x1 - g).toFixed(1)},${(y1 - t[i + 1] * g).toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
    }
    return d;
  }

  /** Two-series smooth line chart: a = lime (left axis), b = lavender (right axis). */
  function lineChart(points, { bucket, aName, bName, aFmt, bFmt, aTick, bTick }) {
    const live = h("span", { class: "sr-only", "aria-live": "polite" });
    const wrap = h("div", { class: "lchart", tabindex: "0", role: "group", "aria-roledescription": "chart",
      "aria-label": `${aName} and ${bName} per ${bucket}. Use the arrow keys to read each ${bucket}.` });
    const n = points.length;
    let peak = 0;
    points.forEach((p, i) => { if (p.a > points[peak].a) peak = i; });
    let sel = n - 1;
    const label = (p) => (bucket === "hour" ? `${dShort(p.t)} ${tShort(p.t)}` : dShort(p.t));
    const xLabel = (p) => (bucket === "hour" ? tShort(p.t) : dShort(p.t));
    function draw() {
      const W = Math.max(260, Math.round(wrap.clientWidth || 600));
      const base = W < 520 ? 236 : 300;
      const H = Math.round(clamp(wrap.clientHeight > base ? wrap.clientHeight : base, base, 520));
      const pl = 46, pr = 46, pt = 70, pb = 30;
      const iw = W - pl - pr, ih = H - pt - pb;
      const x = (i) => (n === 1 ? pl + iw / 2 : pl + (iw * i) / (n - 1));
      const maxA = niceMax(Math.max(0, ...points.map((p) => p.a)) * 1.08);
      const maxB = niceMax(Math.max(0, ...points.map((p) => p.b)) * 1.08);
      const ya = (v) => pt + ih - (v / maxA) * ih;
      const yb = (v) => pt + ih - (v / maxB) * ih;
      const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" });
      const defs = s("defs", {}, s("linearGradient", { id: "lcA", x1: "0", y1: "0", x2: "0", y2: "1" },
        s("stop", { offset: "0", "stop-color": "#D5F26B", "stop-opacity": "0.22" }), s("stop", { offset: "1", "stop-color": "#D5F26B", "stop-opacity": "0" })));
      svg.append(defs);
      // highlighted column at the peak of series a
      const step = n > 1 ? iw / (n - 1) : iw;
      const cw = clamp(step * 0.9, 44, 66);
      if (n > 0 && points[peak].a > 0) {
        const cx = x(peak);
        const col = s("g", { class: "col" });
        col.append(s("rect", { x: cx - cw / 2, y: 6, width: cw, height: H - pb - 2, rx: Math.min(22, cw / 2), fill: "#2A2A2C" }));
        if (peak !== sel) {
          col.append(s("path", { d: SPARK, transform: `translate(${cx - cw / 2 + 13} 18) scale(.9)`, fill: "#D5F26B" }));
          col.append(s("path", { d: SPARK, transform: `translate(${cx + cw / 2 - 12} 24) scale(.55)`, fill: "#BBAEF6" }));
          col.append(s("text", { x: cx, y: 40, "text-anchor": "middle", class: "c-lbl", text: "Peak" }));
          col.append(s("text", { x: cx, y: 58, "text-anchor": "middle", class: "c-val", text: aTick(points[peak].a) }));
        }
        svg.append(col);
      }
      // gridlines and x labels
      const maxLabels = W < 520 ? 4 : 7;
      const stride = Math.max(1, Math.ceil(n / maxLabels));
      for (let i = 0; i < n; i += stride) {
        svg.append(s("line", { x1: x(i), x2: x(i), y1: pt - 6, y2: pt + ih, class: "gl" }));
        svg.append(s("text", { x: x(i), y: H - 8, "text-anchor": i === 0 && n > 1 ? "start" : "middle", class: "ax", text: xLabel(points[i]) }));
      }
      // y labels (left: a, right: b)
      [0.5, 1].forEach((f) => {
        svg.append(s("text", { x: pl - 8, y: ya(maxA * f) + 4, "text-anchor": "end", class: "ax a", text: aTick(maxA * f) }));
        svg.append(s("text", { x: W - pr + 8, y: yb(maxB * f) + 4, "text-anchor": "start", class: "ax b", text: bTick(maxB * f) }));
      });
      svg.append(s("line", { x1: pl, x2: W - pr, y1: pt + ih, y2: pt + ih, class: "base" }));
      const pa = points.map((p, i) => [x(i), ya(p.a)]);
      const pb2 = points.map((p, i) => [x(i), yb(p.b)]);
      if (n > 1) svg.append(s("path", { d: `${monotonePath(pa)}L${x(n - 1)},${pt + ih}L${x(0)},${pt + ih}Z`, fill: "url(#lcA)" }));
      svg.append(s("path", { d: monotonePath(pb2), class: "ln b" }));
      svg.append(s("path", { d: monotonePath(pa), class: "ln a" }));
      if (n === 1) { svg.append(s("circle", { cx: pa[0][0], cy: pa[0][1], r: 4, fill: "#D5F26B" })); svg.append(s("circle", { cx: pb2[0][0], cy: pb2[0][1], r: 4, fill: "#BBAEF6" })); }
      // selection: ring dot + lime speech bubble
      if (n) {
        const p = points[sel], sx = x(sel), sy = ya(p.a);
        svg.append(s("circle", { cx: sx, cy: yb(p.b), r: 4.5, fill: "#141415", stroke: "#BBAEF6", "stroke-width": 2.5 }));
        svg.append(s("circle", { cx: sx, cy: sy, r: 11, fill: "#D5F26B", opacity: 0.18 }));
        svg.append(s("circle", { cx: sx, cy: sy, r: 6, fill: "#141415", stroke: "#D5F26B", "stroke-width": 3 }));
        const t1 = aFmt(p.a), t2 = `${bFmt(p.b)} · ${label(p)}`;
        const bw = Math.max(t1.length * 8.6, t2.length * 6.2) + 24, bh = 44;
        let bx = clamp(sx - bw / 2, 2, W - bw - 2);
        let by = sy - bh - 16;
        if (by < 2) by = sy + 16;
        const pointerUp = by > sy;
        const g = s("g", { class: "bubble" });
        g.append(s("rect", { x: bx, y: by, width: bw, height: bh, rx: 14, fill: "#D5F26B" }));
        const px = clamp(sx, bx + 14, bx + bw - 14);
        g.append(s("path", { d: pointerUp ? `M${px - 7},${by + 1} L${px},${by - 7} L${px + 7},${by + 1}Z` : `M${px - 7},${by + bh - 1} L${px},${by + bh + 7} L${px + 7},${by + bh - 1}Z`, fill: "#D5F26B" }));
        g.append(s("text", { x: bx + 12, y: by + 19, class: "b-val", text: t1 }));
        g.append(s("text", { x: bx + 12, y: by + 35, class: "b-sub", text: t2 }));
        svg.append(g);
        live.textContent = `${label(p)}: ${aName} ${aFmt(p.a)}, ${bName} ${bFmt(p.b)}`;
      }
      // pointer hit layer
      const hit = s("rect", { x: pl - 10, y: 0, width: iw + 20, height: H, fill: "transparent" });
      const pick = (clientX) => {
        const r = svg.getBoundingClientRect();
        const px = ((clientX - r.left) / r.width) * W;
        const i = n === 1 ? 0 : clamp(Math.round(((px - pl) / iw) * (n - 1)), 0, n - 1);
        if (i !== sel) { sel = i; draw(); }
      };
      hit.addEventListener("pointermove", (e) => pick(e.clientX));
      hit.addEventListener("pointerdown", (e) => pick(e.clientX));
      svg.append(hit);
      put(wrap, svg, live);
    }
    wrap.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" && sel > 0) { sel--; draw(); e.preventDefault(); }
      if (e.key === "ArrowRight" && sel < n - 1) { sel++; draw(); e.preventDefault(); }
      if (e.key === "Home") { sel = 0; draw(); e.preventDefault(); }
      if (e.key === "End") { sel = n - 1; draw(); e.preventDefault(); }
    });
    let lastW = 0, lastH = 0;
    const ro = new ResizeObserver(() => {
      const w = Math.round(wrap.clientWidth), hh = Math.round(wrap.clientHeight);
      if (w && (Math.abs(w - lastW) > 1 || hh - lastH > 2)) { lastW = w; draw(); lastH = Math.round(wrap.clientHeight); }
    });
    ro.observe(wrap);
    onCleanup(() => ro.disconnect());
    return wrap;
  }

  /** Tall fully-rounded vertical pills, colours cycling lavender / lime / gray. */
  function pillBars(items, { fmt = (v) => String(v), height = 170, labels = true, maxLabels = 8, tones } = {}) {
    const max = Math.max(0, ...items.map((x) => x.value));
    const cyc = tones || ["lav", "lime", "gray"];
    const stride = Math.max(1, Math.ceil(items.length / maxLabels));
    const wrap = h("div", { class: "pbars", role: "list", style: { "--h": height + "px" } });
    items.forEach((it, i) => {
      const f = max > 0 ? it.value / max : 0;
      const bar = h("span", { class: `pb ${it.tone || cyc[i % cyc.length]}${it.value === max && max > 0 ? " top" : ""}`, style: { "--f": "0" } });
      const col = h("div", { class: "pcol", role: "listitem", title: `${it.label}: ${fmt(it.value)}`, "aria-label": `${it.label}: ${fmt(it.value)}` },
        h("span", { class: "pv", text: (it.value === max && max > 0) || (items.length <= 8 && it.value > 0) ? fmt(it.value) : "" }),
        h("span", { class: "ptrack" }, bar),
        labels ? h("span", { class: "pl", text: i % stride === 0 ? it.short ?? it.label : "" }) : null);
      wrap.append(col);
      requestAnimationFrame(() => requestAnimationFrame(() => bar.style.setProperty("--f", String(f))));
    });
    return wrap;
  }

  /** Thick ring split into arcs with round caps and small gaps. parts: [{value, color, label}] */
  function donut(parts, { size = 230, stroke = 28, center, label } = {}) {
    const r = (size - stroke) / 2 - 2, C = 2 * Math.PI * r, c = size / 2;
    const total = sum(parts.map((p) => p.value));
    const svg = s("svg", { viewBox: `0 0 ${size} ${size}`, class: "donut-svg", "aria-hidden": "true" });
    svg.append(s("circle", { cx: c, cy: c, r, fill: "none", stroke: "#1F1F22", "stroke-width": stroke }));
    const live = parts.filter((p) => p.value > 0);
    let start = 0;
    const gap = 9;
    const arcs = [];
    live.forEach((p) => {
      const len = (p.value / total) * C;
      const el = s("circle", { cx: c, cy: c, r, fill: "none", stroke: p.color, "stroke-width": stroke, "stroke-linecap": live.length === 1 ? "butt" : "round", class: "arc" });
      let dash, pos;
      if (live.length === 1) { dash = C; pos = 0; } else {
        dash = len - gap - stroke;
        pos = start + gap / 2 + stroke / 2;
        if (dash < 0.5) { dash = 0.01; pos = start + len / 2; }
      }
      el.setAttribute("transform", `rotate(${-90 + (pos / C) * 360} ${c} ${c})`);
      el.setAttribute("stroke-dasharray", `0 ${C}`);
      arcs.push([el, dash]);
      svg.append(el);
      start += len;
    });
    requestAnimationFrame(() => requestAnimationFrame(() => arcs.forEach(([el, d]) => el.setAttribute("stroke-dasharray", `${d} ${C}`))));
    return h("div", { class: "donut", role: "img", "aria-label": label || null, style: { "--size": size + "px" } }, svg, center ? h("div", { class: "donut-c" }, center) : null);
  }
  const boltImg = (cls = "bolt") => h("img", { class: cls, src: "/static/img/bolt.svg", alt: "", width: "240", height: "256" });

  function progressRing(frac, size = 58, stroke = 6) {
    const r = (size - stroke) / 2, C = 2 * Math.PI * r;
    const bar = s("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "#D5F26B", "stroke-width": stroke, "stroke-linecap": "round", "stroke-dasharray": `${C}`, "stroke-dashoffset": `${C}`, transform: `rotate(-90 ${size / 2} ${size / 2})`, class: "pr-bar" });
    const svg = s("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size, "aria-hidden": "true" },
      s("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "#2A2A2C", "stroke-width": stroke }), bar);
    const txt = h("span", { class: "pr-t" });
    const el = h("div", { class: "pring", style: { "--size": size + "px" } }, svg, txt);
    el.set = (f, label, color) => { bar.setAttribute("stroke-dashoffset", String(C * (1 - clamp(f, 0, 1)))); if (label != null) txt.textContent = label; if (color) bar.setAttribute("stroke", color); };
    requestAnimationFrame(() => requestAnimationFrame(() => el.set(frac, `${Math.round(frac * 100)}%`)));
    return el;
  }
  function hbar(frac, tone = "lime") {
    const fill = h("i", { class: tone, style: { "--f": "0" } });
    requestAnimationFrame(() => requestAnimationFrame(() => fill.style.setProperty("--f", String(clamp(frac || 0, 0, 1)))));
    const el = h("span", { class: "hbar", "aria-hidden": "true" }, fill);
    el.set = (f) => fill.style.setProperty("--f", String(clamp(f || 0, 0, 1)));
    el.fill = fill;
    return el;
  }

  /* ================================================================ state */
  async function refresh() { S = await api("/api/state"); updateHeader(); return S; }
  async function loadRuns() { RUNS = (await api("/api/runs")).runs || []; return RUNS; }
  const seenRun = () => store.get("seenRun");
  const markSeen = (id) => { store.set("seenRun", id); updateHeader(); };
  const hasUnseen = () => !!(S && S.latest && S.latest.id !== seenRun());
  const shouldWelcome = () => S && !S.config.key_set && !S.demo && !store.get("welcomed");

  function flowSteps() {
    const c = S.config;
    const sites = (S.scan?.sites || []).filter((x) => x.evaluable);
    const included = sites.filter((x) => !c.excluded.includes(x.id));
    const nrec = sum(Object.values(S.recordings || {}));
    const modelsReady = c.candidates.length > 0 && !!c.judge_model;
    const where = c.region === "custom" ? c.base_url : (c.region || "").toUpperCase();
    return [
      { key: "connect", title: "Connect Otari", img: "connect", href: "#/connect", cta: "Connect Otari", done: c.key_set,
        detail: S.demo ? "Demo gateway on this machine" : c.key_set ? `${where} · key from $${c.key_env}` : "Add your Otari key" },
      { key: "scan", title: "Scan the repo", img: "scan", href: "#/scan", cta: "Scan repo", done: !!S.scan,
        detail: S.scan ? `${sites.length} AI call site${sites.length === 1 ? "" : "s"} found` : "Find every OpenAI and Anthropic call" },
      { key: "record", title: "Record prompts", img: "record", href: "#/record", cta: "Record prompts", done: nrec > 0,
        detail: nrec ? `${nrec} prompts across ${Object.keys(S.recordings).length} call sites` : "Capture the real requests your app sends" },
      { key: "models", title: "Choose models", img: "judge", href: "#/models", cta: "Choose models", done: modelsReady,
        detail: modelsReady ? `${c.candidates.length} candidate${c.candidates.length === 1 ? "" : "s"} · judge ${c.judge_model}` : "Pick open-weight candidates and a judge" },
      { key: "evaluate", title: "Evaluate", img: "budget", href: "#/evaluate", cta: "Start evaluation", done: !!S.latest,
        detail: S.latest ? `Last run: ${S.latest.summary.moved} of ${S.latest.summary.evaluated} can switch` : `Privacy check, replay and judge${included.length ? ` · ${included.length} call sites` : ""}` },
      { key: "report", title: "Review the report", img: "verdict", href: S.latest ? `#/report/${S.latest.id}` : null, cta: "See report",
        done: !!S.latest && seenRun() === S.latest.id, needs: !!S.latest,
        detail: S.latest ? "Verdicts, answers side by side, savings" : "Available after an evaluation" },
      { key: "pr", title: "Open the pull request", img: "pr", href: S.latest ? `#/changes/${S.latest.id}` : null, cta: "Review changes",
        done: !!S.branch, needs: !!S.latest, detail: S.branch ? `Branch ${S.branch}` : "Review the diff, then create the branch" },
    ];
  }
  const nextStep = (steps) => steps.find((x) => !x.done && x.needs !== false) || null;

  /* ================================================================ live evaluation */
  const LIVE = { job: null, es: null, running: false, done: false, cancelled: false, interrupted: false, error: null,
    stage: null, stagesSeen: new Set(), spend: 0, cap: 0, total: 0, n: 0, masked: null, skipped: null,
    sites: new Map(), order: [], feed: [], runId: null, summary: null, tests: null, sandbox: null, listeners: new Set() };
  let liveRaf = 0;
  function liveNotify() {
    if (liveRaf) return;
    liveRaf = requestAnimationFrame(() => { liveRaf = 0; LIVE.listeners.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } }); updateHeader(); });
  }
  function liveSite(id) {
    if (!LIVE.sites.has(id)) {
      const recs = (S?.recordings || {})[id] || 0;
      LIVE.sites.set(id, { id, done: 0, total: recs * (S?.config.candidates.length || 1) || 1, models: new Map(), verdict: null, chosen: null, reason: "", popped: false });
      LIVE.order.push(id);
    }
    return LIVE.sites.get(id);
  }
  function liveModel(site, m) {
    if (!site.models.has(m)) site.models.set(m, { sum: 0, n: 0, mean: null, skipped: false, total: 0 });
    return site.models.get(m);
  }
  function onLive(e) {
    if (e.spend != null) LIVE.spend = e.spend;
    if (e.done != null && e.total != null) { LIVE.n = e.done; LIVE.total = e.total; }
    switch (e.type) {
      case "stage":
        LIVE.stage = e.stage; LIVE.stagesSeen.add(e.stage);
        if (e.stage === "replay") { LIVE.total = e.total || LIVE.total; (e.sites || []).forEach(liveSite); }
        break;
      case "privacy": LIVE.masked = e.masked; LIVE.skipped = e.skipped; break;
      case "candidate_start": {
        const st = liveSite(e.site); const m = liveModel(st, e.model); m.total = e.total || 0;
        const models = S?.config.candidates.length || 1;
        if (e.total) st.total = Math.max(st.total, e.total * models);
        break;
      }
      case "sample": {
        const st = liveSite(e.site); st.done += 1;
        const m = liveModel(st, e.model); m.sum += e.score; m.n += 1; m.mean = m.sum / m.n;
        break;
      }
      case "candidate_done": {
        const st = liveSite(e.site); const m = liveModel(st, e.model);
        if (e.skipped) { m.skipped = true; st.done += m.total || (S?.recordings || {})[e.site] || 0; }
        if (e.mean != null) m.mean = e.mean;
        break;
      }
      case "verdict": { const st = liveSite(e.site); st.verdict = e.verdict; st.chosen = e.chosen; st.reason = e.reason; st.done = st.total; break; }
      case "tests": LIVE.tests = e; break;
      case "sandbox": LIVE.sandbox = e; break;
      case "error": LIVE.error = e; break;
      case "cancelled": LIVE.cancelled = true; break;
      case "done": LIVE.done = true; LIVE.runId = e.run_id; LIVE.summary = e.summary; LIVE.spend = e.spend ?? LIVE.spend; break;
    }
    LIVE.feed.push(e);
    if (LIVE.feed.length > 400) LIVE.feed.splice(0, LIVE.feed.length - 400);
    liveNotify();
  }
  async function startEval() {
    const { job } = await api("/api/eval", {});
    if (LIVE.es) LIVE.es.close();
    Object.assign(LIVE, { job, es: null, running: true, done: false, cancelled: false, interrupted: false, error: null, stage: "start",
      stagesSeen: new Set(["start"]), spend: 0, cap: Number(S.config.budget_usd) || 0, total: 0, n: 0, masked: null, skipped: null,
      sites: new Map(), order: [], feed: [], runId: null, summary: null, tests: null, sandbox: null, startedAt: Date.now() });
    const es = new EventSource(`/api/events?job=${encodeURIComponent(job)}&token=${encodeURIComponent(TOKEN)}`);
    LIVE.es = es;
    es.onmessage = (m) => { try { onLive(JSON.parse(m.data)); } catch (err) { console.error(err); } };
    es.addEventListener("end", () => {
      es.close(); LIVE.running = false; liveNotify();
      refresh().then(() => { RUNS = null; liveNotify(); }).catch(() => {});
      if (LIVE.done && !location.hash.startsWith("#/evaluate")) toast("Evaluation finished. The report is ready.");
    });
    es.onerror = () => { if (LIVE.running) { LIVE.interrupted = !LIVE.done; LIVE.running = false; es.close(); liveNotify(); } };
    liveNotify();
  }

  /* ================================================================ shell */
  const DOCK = [
    ["overview", "Overview", "#/", '<rect class="f" x="3.5" y="3.5" width="7" height="7" rx="2.2"/><rect class="f" x="13.5" y="3.5" width="7" height="7" rx="2.2"/><rect class="f" x="3.5" y="13.5" width="7" height="7" rx="2.2"/><rect class="f" x="13.5" y="13.5" width="7" height="7" rx="2.2"/>'],
    ["flow", "Switch flow", "#/flow", '<path class="f" d="M13.6 2.8 5.2 13.4h6.1l-1 7.8 8.5-10.8h-6.2z"/>'],
    ["trend", "Trend", "#/trend", '<rect class="f" x="3.5" y="11" width="4.2" height="9.5" rx="2.1"/><rect class="f" x="9.9" y="4" width="4.2" height="16.5" rx="2.1"/><rect class="f" x="16.3" y="8" width="4.2" height="12.5" rx="2.1"/>'],
    ["runs", "Runs", "#/runs", '<path class="f" d="M6.5 3h8l4.5 4.5V19a2 2 0 0 1-2 2H6.5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path class="cut" d="M8.5 12.5h7M8.5 16.5h4.5"/>'],
    ["settings", "Settings", "#/settings", '<path d="M4 7h16M4 12h16M4 17h16"/><circle class="f" cx="9" cy="7" r="2.4"/><circle class="f" cx="15.5" cy="12" r="2.4"/><circle class="f" cx="7.5" cy="17" r="2.4"/>'],
  ];
  function buildDock() {
    put(dock, ...DOCK.map(([key, label, href, paths]) => {
      const sv = document.createElementNS(SVGNS, "svg");
      sv.setAttribute("viewBox", "0 0 24 24"); sv.setAttribute("aria-hidden", "true"); sv.setAttribute("class", "di");
      sv.innerHTML = paths; // static markup
      const a = h("a", { class: "dock-btn", href, title: label }, sv, h("span", { class: "dock-l", text: label }));
      a.dataset.tab = key;
      return a;
    }));
  }
  function setTab(key) {
    currentTab = key;
    dock.querySelectorAll(".dock-btn").forEach((a) => (a.dataset.tab === key ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  }
  function buildTopbar() {
    const search = h("button", { class: "icon-btn ring", type: "button", "aria-label": "Search screens, call sites and runs", title: "Search (Ctrl+K)", onclick: openSearch }, icon("search"));
    const brand = h("a", { class: "brand", href: "#/", "aria-label": "Otari Switch overview" }, h("img", { src: "/static/img/bolt-mini.svg", alt: "", width: "22", height: "30" }), h("span", { text: "Otari Switch" }));
    const demo = h("span", { class: "demo-pill top", hidden: true, title: "Bundled demo gateway: demo models, no key, no cost" }, h("i"), h("span", { class: "long", text: "Demo gateway" }), h("span", { class: "shortl", text: "Demo" }));
    const repo = h("button", { class: "repo-btn", type: "button", onclick: openWorkspace, "aria-label": "Workspace details" }, h("span", { class: "repo-name", text: "Otari Switch" }), icon("chevDown"));
    const avatar = h("button", { class: "avatar", type: "button", onclick: () => go("#/connect") }, h("span", { class: "ini", text: "" }), h("span", { class: "dot", hidden: true }));
    const bell = h("button", { class: "icon-btn bell", type: "button", "aria-label": "Notifications", onclick: openBell }, icon("bell"), h("span", { class: "dot", hidden: true }));
    put(topbar, search, brand, demo, h("span", { class: "grow" }), repo, avatar, bell);
    Object.assign(HDR, { demo, repo, avatar, bell });
  }
  function updateHeader() {
    if (!S || !HDR.repo) return;
    HDR.repo.querySelector(".repo-name").textContent = S.repo;
    HDR.repo.title = S.root;
    HDR.avatar.querySelector(".ini").textContent = initials(S.repo);
    const on = !!S.config.key_set;
    HDR.avatar.querySelector(".dot").hidden = !on;
    HDR.avatar.setAttribute("aria-label", on ? `Connected to Otari at ${S.config.base_url}` : "Not connected to Otari. Connect");
    HDR.avatar.title = on ? "Connected to Otari" : "Not connected";
    HDR.demo.hidden = !S.demo;
    const unseen = hasUnseen();
    HDR.bell.querySelector(".dot").hidden = !unseen;
    HDR.bell.classList.toggle("live", LIVE.running);
    HDR.bell.setAttribute("aria-label", unseen ? "Notifications: a new report is ready" : LIVE.running ? "Notifications: an evaluation is running" : "Notifications");
  }
  const go = (hash) => { if (openSheetClose) openSheetClose(true); if (location.hash === hash) render(); else location.hash = hash; };

  function openWorkspace() {
    const c = S.config;
    const rows = [
      ["Repository", S.repo, "folder"], ["Path", S.root, "folder"],
      ["Otari", c.base_url, "globe"], ["Region", c.region === "custom" ? "Custom URL" : String(c.region).toUpperCase(), "globe"],
      ["Key", c.key_set ? `Set, from $${c.key_env} (memory only)` : `Not set ($${c.key_env})`, "key"],
      ["Branch", S.branch || "No Otari Switch branch yet", "branch"], ["Version", `otari-switch ${S.version}`, "tag"],
    ];
    sheet("Workspace", h("div", {},
      S.demo ? note("info", "Demo gateway", "This workspace talks to the bundled fake Otari gateway: demo models, no key, no cost.") : null,
      h("div", { class: "list" }, rows.map(([k, v, ic]) => h("div", { class: "row" }, h("span", { class: "row-ic" }, icon(ic)), h("div", { class: "t" }, h("small", { text: k }), h("b", { class: "wrap", text: v }))))),
      h("div", { class: "actions" }, linkBtn("Connection", "#/connect", { kind: "secondary", ic: "plug" }), linkBtn("Settings", "#/settings", { kind: "secondary" }))));
  }

  function openBell() {
    const items = [];
    if (LIVE.running) {
      items.push(h("a", { class: "row link-row", href: "#/evaluate" }, h("span", { class: "row-ic lime" }, h("span", { class: "spin sm dark" })),
        h("div", { class: "t" }, h("b", { text: "Evaluation running" }), h("small", { text: LIVE.total ? `${LIVE.n} of ${LIVE.total} answers · ${money(LIVE.spend, 4)} spent` : "Starting…" })), icon("chevRight", "chev")));
    }
    if (LIVE.error && !LIVE.running) {
      items.push(h("a", { class: "row link-row", href: "#/evaluate" }, h("span", { class: "row-ic bad" }, icon("warn")),
        h("div", { class: "t" }, h("b", { text: "The last evaluation stopped" }), h("small", { text: LIVE.error.message })), icon("chevRight", "chev")));
    }
    if (S.latest) {
      const unseen = hasUnseen();
      items.push(h("a", { class: "row link-row", href: `#/report/${S.latest.id}` }, h("span", { class: `row-ic ${unseen ? "lime" : ""}` }, icon("doc")),
        h("div", { class: "t" }, h("b", { text: `${unseen ? "New report: " : "Latest report: "}${S.latest.summary.moved} of ${S.latest.summary.evaluated} can switch` }), h("small", { text: rel(S.latest.started) })),
        unseen ? h("span", { class: "dot-red", "aria-label": "new" }) : null, icon("chevRight", "chev")));
    }
    if (S.branch) {
      items.push(h("a", { class: "row link-row", href: S.latest ? `#/changes/${S.latest.id}` : "#/runs" }, h("span", { class: "row-ic" }, icon("branch")),
        h("div", { class: "t" }, h("b", { text: "Branch ready for a pull request" }), h("small", { class: "mono", text: S.branch })), icon("chevRight", "chev")));
    }
    const sh = sheet("Notifications", items.length ? h("div", { class: "list" }, items) : emptyState("verdict", "You're all caught up", "Finished evaluations and branches show up here."));
    sh.el.addEventListener("click", (e) => { if (e.target.closest("a[href]")) sh.close(true); });
  }

  async function openSearch() {
    const input = h("input", { class: "field search-field", type: "search", placeholder: "Screens, call sites, runs…", "aria-label": "Search", autocomplete: "off", spellcheck: "false", "data-autofocus": "" });
    const results = h("div", { class: "list", role: "listbox", "aria-label": "Results" });
    const sh = sheet("Search", h("div", {}, h("label", { class: "search-wrap" }, icon("search"), input), results));
    const screens = [["Overview", "#/", "Dashboard"], ["Switch flow", "#/flow", "All steps"], ["Connect Otari", "#/connect", "Region, key, models"],
      ["Scan repo", "#/scan", "AI call sites"], ["Record prompts", "#/record", "Tests, script or logs"], ["Choose models", "#/models", "Candidates, judge, budget"],
      ["Evaluate", "#/evaluate", "Live replay and judging"], ["Trend", "#/trend", "Usage and budget from Otari"], ["Runs", "#/runs", "Every evaluation"],
      ["Settings", "#/settings", "Thresholds, privacy, setup check"], ["Welcome", "#/welcome", "What Otari Switch does"]];
    let runs = RUNS;
    if (!runs) { try { runs = await loadRuns(); } catch { runs = []; } }
    const latestSites = new Set();
    const draw = () => {
      const q = input.value.trim().toLowerCase();
      const m = (t) => !q || t.toLowerCase().includes(q);
      const out = [];
      screens.filter(([a, , b]) => m(a + " " + b)).forEach(([a, href, b]) => out.push(["Screen", a, b, href, "arrowRight"]));
      (S.scan?.sites || []).filter((x) => m(`${x.function} ${x.file} ${x.model || ""}`)).forEach((x) => {
        const inRun = S.latest && latestSites.has(x.id);
        out.push(["Call site", x.function, `${x.file}:${x.line} · ${x.model || x.model_source}`, inRun ? `#/report/${S.latest.id}?site=${encodeURIComponent(x.id)}` : "#/scan", "code"]);
      });
      (runs || []).filter((r) => m(`${r.id} ${r.started} run report`)).slice(0, 8).forEach((r) =>
        out.push(["Run", `${r.summary.moved} of ${r.summary.evaluated} can switch`, `${rel(r.started)} · ${money(r.spend_usd, 4)}${r.demo ? " · demo" : ""}`, `#/report/${r.id}`, "doc"]));
      put(results, ...(out.length ? out.slice(0, 30).map(([kind, t, d, href, ic]) =>
        h("a", { class: "row link-row", href, role: "option" }, h("span", { class: "row-ic" }, icon(ic)), h("div", { class: "t" }, h("b", { text: t }), h("small", { text: `${kind} · ${d}` })), icon("chevRight", "chev")))
        : [h("p", { class: "muted pad", text: "Nothing matches. Try a function name, a file or a screen." })]));
    };
    if (S.latest) {
      api(`/api/run?id=${encodeURIComponent(S.latest.id)}`).then((r) => { r.sites.forEach((x) => x.candidates.length && latestSites.add(x.site.id)); draw(); }).catch(() => {});
    }
    input.addEventListener("input", draw);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { const a = results.querySelector("a[href]"); if (a) { e.preventDefault(); go(a.getAttribute("href")); } } });
    sh.el.addEventListener("click", (e) => { const a = e.target.closest("a[href]"); if (a) { e.preventDefault(); go(a.getAttribute("href")); } });
    draw();
  }

  /* ================================================================ router */
  const ROUTES = [
    [/^\/$/, "overview", screenOverview, "Overview"],
    [/^\/welcome$/, null, screenWelcome, "Welcome"],
    [/^\/flow$/, "flow", screenFlow, "Switch flow"],
    [/^\/connect$/, "flow", screenConnect, "Connect"],
    [/^\/models$/, "flow", screenModels, "Models"],
    [/^\/scan$/, "flow", screenScan, "Scan"],
    [/^\/record$/, "flow", screenRecord, "Record"],
    [/^\/evaluate$/, "flow", screenEvaluate, "Evaluate"],
    [/^\/report\/([\w-]+)$/, "keep", screenReport, "Report"],
    [/^\/changes\/([\w-]+)$/, "keep", screenChanges, "Changes"],
    [/^\/trend$/, "trend", screenTrend, "Trend"],
    [/^\/runs$/, "runs", screenRuns, "Runs"],
    [/^\/settings$/, "settings", screenSettings, "Settings"],
  ];
  let seq = 0, firstRender = true;
  async function render() {
    const my = ++seq;
    const raw = location.hash.replace(/^#/, "");
    const [p0, qs] = raw.split("?");
    const path = p0 || "/";
    const params = new URLSearchParams(qs || "");
    let match = null;
    for (const [re, tab, fn, name] of ROUTES) { const m = re.exec(path); if (m) { match = { tab, fn, name, args: m.slice(1) }; break; } }
    if (!match) { location.replace("#/"); return; }
    const skelTimer = setTimeout(() => { if (my === seq) put(main, h("div", { class: "screen" }, h("div", { class: "grid" }, tile({ span: 12, wide: true, cls: "lead" }, h("div", { class: "skel title-skel" }), skelRows(4))))); }, 160);
    try { await refresh(); } catch (e) {
      clearTimeout(skelTimer);
      if (my !== seq) return;
      put(main, screen(tile({ span: 12, wide: true, cls: "lead" }, head("Can't reach Otari Switch"), errNote(e), h("div", { class: "actions" }, btn("Try again", async () => render())))));
      return;
    }
    clearTimeout(skelTimer);
    if (my !== seq) return;
    if (path === "/" && firstRender && !raw && shouldWelcome()) { firstRender = false; location.replace("#/welcome"); return; }
    firstRender = false;
    if (match.tab === "keep") setTab(currentTab && currentTab !== "trend" && currentTab !== "settings" ? currentTab : "runs");
    else setTab(match.tab);
    cleanupScreen();
    const ctx = { alive: () => my === seq, params };
    let node;
    try { node = match.fn(ctx, ...match.args); } catch (e) { console.error(e); node = screen(tile({ span: 12, wide: true, cls: "lead" }, head(match.name), errNote(e))); }
    if (my !== seq) return;
    put(main, node);
    viewport.scrollTop = 0;
    document.title = `${match.name} · Otari Switch`;
    if (navByUser) { const h1 = main.querySelector("h1"); if (h1) h1.focus({ preventScroll: true }); }
    navByUser = true;
  }
  let navByUser = false;
  window.addEventListener("hashchange", () => { if (openSheetClose) openSheetClose(true); render(); });
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
    if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing && !openSheetClose)) { e.preventDefault(); openSearch(); }
  });

  /* ================================================================ screens */

  /* ---------- Welcome ---------- */
  function screenWelcome() {
    const known = S.config.key_set || S.demo;
    const path = [
      ["scan", "Scan", "Finds every OpenAI and Anthropic SDK call by reading your code. Nothing runs."],
      ["record", "Record", "Runs your tests with a thin recorder and keeps the real requests per call site."],
      ["privacy", "Privacy", "Emails, keys, card and phone numbers are masked before anything is replayed."],
      ["judge", "Replay & judge", "Each prompt goes to open-weight candidates through Otari. A separate judge scores the answers."],
      ["verdict", "Verdict", "Switch, switch with fallback, or keep frontier: per call site, with the reasons."],
      ["pr", "Pull request", "One routing policy per call site, one commit each, on a new branch. You review the diff first."],
    ];
    const tour = tile({ span: 6, wide: true, cls: "tour", title: "Watch the tour", sub: "A short walk through the whole flow." });
    tour.hidden = true;
    let safety = null;
    const noTour = () => { if (safety) { safety.classList.remove("s6"); safety.classList.add("s12"); } };
    (async () => {
      try {
        const r = await fetch("/static/media/tour.mp4", { method: "HEAD" });
        if (!r.ok) { noTour(); return; }
        const pr = await fetch("/static/media/tour-poster.jpg", { method: "HEAD" }).catch(() => null);
        tour.append(h("video", { class: "video", controls: true, playsinline: true, preload: "none", poster: pr && pr.ok ? "/static/media/tour-poster.jpg" : null, src: "/static/media/tour.mp4", "aria-label": "Otari Switch tour video" }));
        tour.hidden = false;
      } catch { noTour(); /* no tour video yet */ }
    })();
    return screen(
      tile({ span: 12, wide: true, cls: "lead hero" },
        h("div", { class: "hero-art" }, boltImg("bolt hero-bolt")),
        h("div", { class: "hero-copy" },
          h("p", { class: "eyebrow", text: "Otari Switch" }),
          h("h1", { class: "hero-title", text: "Switch with proof.", tabindex: "-1" }),
          h("p", { class: "lede", text: "Move your app's AI calls from frontier APIs to open-weight models through Otari, one call site at a time, and prove each move on your real prompts before the pull request." }),
          h("div", { class: "actions" },
            known ? linkBtn("Go to overview", "#/", { ic: "arrowRight" }) : btn("Connect Otari", async () => { store.set("welcomed", "1"); go("#/connect"); }, { ic: "plug" }),
            known ? linkBtn("Connection", "#/connect", { kind: "secondary", ic: "plug" }) : h("span", { class: "muted small", text: "No key yet? Try `otari-switch demo` in a terminal." })))),
      tile({ span: 12, wide: true, title: "The path", sub: "Six steps from direct provider calls to a reviewed pull request." },
        h("ol", { class: "path" }, path.map(([img, t, d], i) => h("li", { class: "path-card" },
          h("img", { src: `/static/img/${img}.svg`, alt: "", width: "240", height: "180" }),
          h("span", { class: "step-n", text: String(i + 1).padStart(2, "0") }), h("h3", { text: t }), h("p", { text: d }))))),
      tour,
      safety = tile({ span: 6, wide: true, title: "Safe by default" },
        h("div", { class: "list" },
          [["gauge", "Hard spend cap", "Every evaluation stops before the next call would pass its budget."],
            ["shield", "Your key stays in memory", "A pasted key is kept in this process only and is never written to disk."],
            ["branch", "Nothing changes without a yes", "Code changes land on a new branch, after you review the diff."]].map(([ic, t, d]) =>
            h("div", { class: "row" }, h("span", { class: "row-ic lime" }, icon(ic)), h("div", { class: "t" }, h("b", { text: t }), h("small", { text: d }))))),
        known ? null : h("div", { class: "actions" }, btn("Connect Otari", async () => { store.set("welcomed", "1"); go("#/connect"); }, { ic: "plug" }))));
  }

  /* ---------- Overview ---------- */
  function screenOverview(ctx) {
    const c = S.config;
    const steps = flowSteps();
    const next = nextStep(steps);
    let scope = store.get("ovScope") || "latest";
    const dateEl = h("span", { text: S.latest ? dShort(new Date(S.latest.started)) : "No runs yet" });
    const kpis = h("div", { class: "kpis" }, skelRows(4));
    const cta = next ? linkBtn(next.key === "report" ? "See the latest report" : `Next: ${next.cta}`, next.href, { ic: "arrowRight" })
      : linkBtn("New evaluation", "#/evaluate", { ic: "play" });
    const lead = tile({ span: 5, wide: true, cls: "lead tall" },
      head("Overview", { date: dateEl }),
      h("div", { class: "lead-row" }, seg([["latest", "Latest run"], ["all", "All runs"]], scope, (v) => { scope = v; store.set("ovScope", v); drawKpis(); }, { label: "Numbers for" })),
      kpis, h("div", { class: "actions" }, cta));
    const donutTile = tile({ span: 4, title: "Verdicts", sub: S.latest ? "Latest run, per call site" : "Per call site, after an evaluation" }, skelRows(2));
    const quick = tile({ span: 3, cls: "fill", title: "Quick actions" }, quickActions());
    const runsTile = tile({ span: 7, wide: true, title: "Recent runs", right: h("a", { class: "link", href: "#/runs" }, "All runs", icon("chevRight")) }, skelRows(3));
    const flowTile = tile({ span: 12, wide: true, cls: "flow-wide", title: "Switch flow", right: h("a", { class: "link", href: "#/flow" }, "Open", icon("chevRight")) }, flowMini(steps));

    let run = null, runs = [];
    function drawKpis() {
      const sc = S.scan;
      if (scope === "latest") {
        if (!run) {
          put(kpis, 
            kpi("AI call sites found", sc ? sc.sites.length : null, intParts, { empty: "Not scanned", sub: sc ? `${sc.files_scanned} Python files read` : "Scan the repo to find them" }),
            h("div", { class: "kpi-empty" }, emptyState("verdict", "No evaluation yet", "Record real prompts and evaluate them to see what can move to open-weight models.")));
          return;
        }
        const sm = run.summary;
        const prev = runs.find((r) => r.id !== run.id && new Date(r.started) < new Date(run.started));
        put(kpis, 
          kpi("AI call sites found", sc ? sc.sites.length : sm.call_sites, intParts, { sub: `${sm.evaluated} evaluated in this run`, chip: c.excluded.length ? chip(`${c.excluded.length} excluded`, "gray") : null }),
          kpi("Can move to open-weight", sm.open_share, pctParts, { sub: `${sm.moved} of ${sm.evaluated} call sites`, chip: prev ? deltaChip(sm.open_share, prev.summary.open_share) : chip("First run", "gray") }),
          kpi("Estimated monthly savings", sm.monthly_savings_usd, moneyParts, { empty: "Not priced", sub: sm.monthly_savings_usd == null ? "Otari returned no price for today's models" : `At ${nf(sm.monthly_calls_assumed)} calls a month`,
            chip: prev && prev.summary.monthly_savings_usd > 0 && sm.monthly_savings_usd != null ? deltaChip(sm.monthly_savings_usd / prev.summary.monthly_savings_usd, 1, { unit: "%" }) : null }),
          kpi("Evaluation spend", run.spend_usd, spendParts, { sub: `of a ${money(run.budget_usd)} cap`,
            chip: run.budget_usd ? chip(`${pct(run.spend_usd / run.budget_usd)} of cap`, run.spend_usd / run.budget_usd >= 0.8 ? "lav" : "lime") : null }));
        dateEl.textContent = dShort(new Date(run.started));
      } else {
        if (!runs.length) { put(kpis, h("div", { class: "kpi-empty" }, emptyState("verdict", "No runs yet", "Every evaluation is kept, so you can compare them here."))); dateEl.textContent = "No runs yet"; return; }
        const shares = runs.map((r) => r.summary.open_share);
        const best = runs.reduce((a, b) => (b.summary.open_share > a.summary.open_share ? b : a));
        const demoRuns = runs.filter((r) => r.demo).length;
        put(kpis, 
          kpi("Evaluations run", runs.length, intParts, { sub: `Since ${dShort(new Date(runs[runs.length - 1].started))}`, chip: demoRuns ? chip(`${demoRuns} on demo data`, "lav") : null }),
          kpi("Total evaluation spend", sum(runs.map((r) => r.spend_usd)), spendParts, { sub: "Across all runs" }),
          kpi("Best open-weight share", best.summary.open_share, pctParts, { sub: `${best.summary.moved} of ${best.summary.evaluated} · ${rel(best.started)}` }),
          kpi("Average open-weight share", sum(shares) / shares.length, pctParts, { sub: `Over ${runs.length} run${runs.length === 1 ? "" : "s"}` }));
        dateEl.textContent = dateRange(runs[runs.length - 1].started, runs[0].started);
      }
    }
    drawKpis();

    Promise.all([S.latest ? api(`/api/run?id=${encodeURIComponent(S.latest.id)}`).catch(() => null) : Promise.resolve(null), loadRuns().catch(() => [])]).then(([r, list]) => {
      if (!ctx.alive()) return;
      run = r; runs = list || [];
      drawKpis();
      put(donutTile, donutTile.firstChild, ...verdictDonut(run));
      put(runsTile, runsTile.firstChild, runs.length ? runList(runs.slice(0, 4)) :
        emptyState("record", "No runs yet", "Your evaluations will be listed here with their verdicts.", linkBtn("Start the flow", "#/flow", { kind: "secondary", ic: "arrowRight" })));
    });
    return screen(lead, donutTile, quick, runsTile, flowTile);
  }

  function verdictDonut(run, { size = 220 } = {}) {
    if (!run || !run.summary.evaluated) {
      return [h("div", { class: "donut-wrap" }, donut([], { size, center: boltImg("bolt in-donut"), label: "No verdicts yet" })),
        h("p", { class: "muted center small", text: "Verdicts appear here after your first evaluation." })];
    }
    const sm = run.summary;
    const parts = [
      { value: sm.switch, color: V.switch.color, label: "Switch" },
      { value: sm.switch_with_fallback, color: V.switch_with_fallback.color, label: "Switch with fallback" },
      { value: sm.keep, color: V.keep.color, label: "Keep frontier" },
    ];
    return [
      h("a", { class: "donut-wrap", href: `#/report/${run.id}`, "aria-label": `Open the report: ${sm.moved} of ${sm.evaluated} call sites can switch` },
        donut(parts, { size, center: boltImg("bolt in-donut"), label: `Switch ${sm.switch}, switch with fallback ${sm.switch_with_fallback}, keep frontier ${sm.keep}` })),
      h("div", { class: "legend" }, parts.map((p) => h("span", {}, h("i", { style: { background: p.color } }), `${p.label} `, h("b", { text: String(p.value) })))),
      h("p", { class: "donut-cap" }, h("b", { text: `${sm.moved} of ${sm.evaluated}` }), " call sites can run on open-weight models"),
    ];
  }

  function quickActions() {
    const latest = S.latest;
    const scanNow = h("button", { class: "qa", type: "button" }, h("span", { class: "qa-ic" }, icon("scan")), h("b", { text: "Scan again" }), h("small", { text: S.scan ? `${S.scan.sites.length} sites now` : "Not scanned yet" }));
    scanNow.addEventListener("click", async () => {
      if (scanNow.classList.contains("busy")) return;
      scanNow.classList.add("busy");
      try { const r = await api("/api/scan", {}); await refresh(); toast(`Scanned · ${r.sites.length} AI call site${r.sites.length === 1 ? "" : "s"}`); scanNow.querySelector("small").textContent = `${r.sites.length} sites now`; }
      catch (e) { fail(e); } finally { scanNow.classList.remove("busy"); }
    });
    return h("div", { class: "qa-grid" },
      h("a", { class: "qa primary", href: "#/evaluate" }, h("span", { class: "qa-ic" }, icon("play")), h("b", { text: "New evaluation" }), h("small", { text: `Cap ${money(S.config.budget_usd)}` })),
      latest ? h("a", { class: "qa", href: `#/changes/${latest.id}` }, h("span", { class: "qa-ic" }, icon("branch")), h("b", { text: "Review changes" }), h("small", { text: S.branch ? "Branch ready" : "Diff and PR" }))
        : h("span", { class: "qa disabled", "aria-disabled": "true" }, h("span", { class: "qa-ic" }, icon("branch")), h("b", { text: "Review changes" }), h("small", { text: "After a run" })),
      h("a", { class: "qa", href: "#/trend" }, h("span", { class: "qa-ic" }, icon("gauge")), h("b", { text: "Usage trend" }), h("small", { text: "From Otari" })),
      scanNow);
  }

  function runList(runs) {
    return h("div", { class: "list" }, runs.map((r) => {
      const sm = r.summary;
      const stackEl = h("span", { class: "stack", "aria-hidden": "true" },
        badge("S", { tone: "lime", text: String(sm.switch), title: `Switch: ${sm.switch}` }),
        badge("F", { tone: "lav", text: String(sm.switch_with_fallback), title: `Switch with fallback: ${sm.switch_with_fallback}` }),
        badge("K", { tone: "gray", text: String(sm.keep), title: `Keep frontier: ${sm.keep}` }));
      return h("a", { class: "row link-row", href: `#/report/${r.id}`, "aria-label": `Run of ${dShort(new Date(r.started))}: ${sm.moved} of ${sm.evaluated} can switch` },
        stackEl,
        h("div", { class: "t" }, h("b", { text: `${sm.moved} of ${sm.evaluated} can switch` }),
          h("small", { text: `${rel(r.started)} · ${money(r.spend_usd, 4)} spent${r.demo ? " · demo data" : ""}` })),
        chip(pct(sm.open_share), sm.open_share >= 0.5 ? "lime" : "lav"), icon("chevRight", "chev"));
    }));
  }

  function flowMini(steps) {
    const done = steps.filter((x) => x.done).length;
    const next = nextStep(steps);
    const ring = progressRing(done / steps.length, 76, 9);
    setTimeout(() => ring.set(done / steps.length, `${done}/${steps.length}`), 30);
    return h("div", { class: "flowmini" },
      h("div", { class: "fm-top" }, ring, h("div", {}, h("b", { text: next ? `Next: ${next.title}` : "All steps done" }), h("small", { class: "muted", text: next ? next.detail : "Run a new evaluation any time." }))),
      h("ol", { class: "fm-steps" }, steps.map((x) => h("li", { class: x.done ? "done" : x === next ? "next" : "" },
        h("span", { class: "fm-dot" }, x.done ? icon("check") : null), x.href && x.needs !== false ? h("a", { href: x.href, text: x.title }) : h("span", { text: x.title })))),
      next && next.href ? h("div", { class: "actions" }, linkBtn(next.cta, next.href, { ic: "arrowRight" })) : null);
  }

  /* ---------- Switch flow hub ---------- */
  function screenFlow() {
    const steps = flowSteps();
    const done = steps.filter((x) => x.done).length;
    const next = nextStep(steps);
    const bar = hbar(done / steps.length);
    return screen(
      tile({ span: 12, wide: true, cls: "lead" },
        head("Switch flow", { date: S.latest ? `Last run ${dShort(new Date(S.latest.started))}` : "No runs yet", extra: S.demo ? demoBadge() : null }),
        h("p", { class: "lede", text: "Seven steps from direct provider calls to a reviewed pull request. Nothing touches your code until you create the branch." }),
        h("div", { class: "flow-progress" }, h("span", { class: "fp-n" }, h("b", { text: String(done) }), h("span", { class: "dec", text: ` / ${steps.length} done` })), bar),
        next && next.href ? h("div", { class: "actions" }, linkBtn(next.cta, next.href, { ic: "arrowRight" })) : h("div", { class: "actions" }, linkBtn("Run a new evaluation", "#/evaluate", { ic: "play" }))),
      ...steps.map((x, i) => {
        const locked = x.needs === false;
        const state = x.done ? chip("Done", "lime", "check") : x === next ? chip("Next", "lav", "arrowRight") : locked ? chip("Locked", "gray") : chip("To do", "gray");
        const body = [h("div", { class: "st-art" }, h("img", { src: `/static/img/${x.img}.svg`, alt: "", width: "240", height: "180", loading: "lazy" })),
          h("div", { class: "st-top" }, h("span", { class: "step-n", text: String(i + 1).padStart(2, "0") }), state),
          h("h3", { text: x.title }), h("p", { class: "muted", text: x.detail })];
        return tile({ span: i < 4 ? 3 : 4, cls: `step${x === next ? " is-next" : ""}${locked ? " locked" : ""}`, label: x.title },
          locked ? h("div", { class: "step-in" }, body) : h("a", { class: "step-in", href: x.href }, body));
      }));
  }

  /* ---------- Connect ---------- */
  function screenConnect(ctx) {
    const c = S.config;
    let region = c.region === "eu" ? "eu" : c.region === "custom" ? "custom" : "global";
    const opts = [["global", "Global"], ["eu", "EU"]];
    if (c.region === "custom") opts.push(["custom", "Custom URL"]);
    const urlField = h("input", { class: "field", type: "url", value: c.base_url, spellcheck: "false", "aria-label": "Custom Otari base URL" });
    const urlRow = h("label", { class: "field-row" }, h("span", { class: "lbl", text: "Base URL" }), urlField);
    urlRow.hidden = region !== "custom";
    const key = h("input", { class: "field", type: "password", placeholder: c.key_set ? "Using the key already set" : "otk_…", autocomplete: "off", spellcheck: "false", "aria-label": "Otari API key" });
    const eye = h("button", { class: "icon-btn small", type: "button", "aria-label": "Show the key", "aria-pressed": "false" }, icon("eye"));
    eye.addEventListener("click", () => { const show = key.type === "password"; key.type = show ? "text" : "password"; put(eye, icon(show ? "eyeOff" : "eye")); eye.setAttribute("aria-pressed", String(show)); eye.setAttribute("aria-label", show ? "Hide the key" : "Show the key"); });
    const result = h("div", { class: "result", "aria-live": "polite" });
    const pickerTile = tile({ span: 7, wide: true, title: "Model catalog", sub: "Pick 2 or 3 open-weight candidates and one judge." });
    const budgetT = budgetTile(7);
    const statusRows = h("div", { class: "list" });
    const drawStatus = () => put(statusRows, 
      infoRow("globe", "Gateway", S.config.base_url), infoRow("key", "Key", S.config.key_set ? `Set · read from $${S.config.key_env} (memory only)` : `Not set · paste one, or set $${S.config.key_env}`),
      infoRow("tag", "Region", S.config.region === "custom" ? "Custom URL" : String(S.config.region).toUpperCase()));
    drawStatus();

    async function loadPicker(res) {
      if (!S.config.key_set) { put(pickerTile, pickerTile.firstChild, emptyState("judge", "Connect to see the catalog", "The models your key can reach show up here, with prices and JSON and tool support.")); return; }
      if (!res) {
        put(pickerTile, pickerTile.firstChild, skelRows(3));
        try { res = await api("/api/models"); } catch (e) { if (ctx.alive()) put(pickerTile, pickerTile.firstChild, errNote(e)); return; }
      }
      if (!ctx.alive()) return;
      put(pickerTile, pickerTile.firstChild, modelPicker(res, () => budgetT.update()));
    }
    const connectBtn = btn("Test and connect", async () => {
      const body = { api_key: key.value.trim() || null, region };
      if (region === "custom") body.base_url = urlField.value.trim();
      put(result, h("span", { class: "muted", text: "Testing the key…" }));
      try {
        const res = await api("/api/connect", body);
        key.value = "";
        await refresh();
        drawStatus();
        put(result, note("ok", `Connected · ${res.models.length} model${res.models.length === 1 ? "" : "s"} reachable`, res.base_url));
        toast(`Connected · ${res.models.length} models`);
        store.set("welcomed", "1");
        loadPicker(res);
      } catch (e) { put(result, errNote(e)); throw e; }
    }, { ic: "plug" });

    const lead = tile({ span: 5, wide: true, cls: "lead tall" },
      head("Connect", { extra: S.demo ? demoBadge() : null }),
      h("p", { class: "lede", text: "Otari is the gateway every call goes through: candidates, the judge and the sandbox checks." }),
      h("div", { class: "form" },
        h("div", { class: "field-row" }, h("span", { class: "lbl", id: "rg-l", text: "Region" }),
          seg(opts, region, (v) => { region = v; urlRow.hidden = v !== "custom"; }, { label: "Region" })),
        urlRow,
        h("label", { class: "field-row" }, h("span", { class: "lbl", text: "API key" }), h("span", { class: "with-btn" }, key, eye)),
        h("p", { class: "hint" }, icon("shield"), h("span", { text: `Never stored: a pasted key stays in this process's memory and is never written to disk. You can also set $${c.key_env} before starting.` }))),
      h("div", { class: "actions" }, connectBtn, S.config.key_set ? linkBtn("Continue", S.scan ? "#/models" : "#/scan", { kind: "secondary", ic: "arrowRight" }) : null),
      result,
      h("div", { class: "subsec" }, h("h3", { class: "subh", text: "Current connection" }), statusRows));
    loadPicker();
    return screen(lead, pickerTile, budgetT);
  }
  const infoRow = (ic, k, v) => h("div", { class: "row" }, h("span", { class: "row-ic" }, icon(ic)), h("div", { class: "t" }, h("small", { text: k }), h("b", { class: "wrap", text: v })));

  /** Catalog chips from GET /api/models: open-weight filter, search, price and JSON/tools badges,
      "Use suggested". Candidates (up to 4) in lime, the judge in lavender. Saved only on confirm. */
  function modelPicker(res, onSaved) {
    const catalog = res.catalog || [];
    const rows = new Map(catalog.map((r) => [r.id, r]));
    if (!catalog.length) (res.models || []).forEach((m) => rows.set(m, { id: m, open_weight: null }));
    let cands = [...S.config.candidates], judge = S.config.judge_model || "";
    [...cands, judge].filter(Boolean).forEach((m) => { if (!rows.has(m)) rows.set(m, { id: m, open_weight: null, missing: true }); });
    const saved = () => JSON.stringify([S.config.candidates, S.config.judge_model || ""]);
    const isDirty = () => JSON.stringify([cands, judge]) !== saved();
    const sug = res.suggested || {};
    const sugSet = new Set([...(sug.candidates || []), ...(sug.judge ? [sug.judge] : [])]);
    let mode = "cand", openOnly = true, showAll = false;
    const q = h("input", { class: "field", type: "search", placeholder: `Search ${rows.size} models`, "aria-label": "Search models", spellcheck: "false" });
    const chips = h("div", { class: "mchips", role: "group", "aria-label": "Models" });
    const summary = h("div", { class: "picked" });
    const status = h("span", { class: "save-state", "aria-live": "polite" });
    const modeSeg = seg([["cand", "Candidates"], ["judge", "Judge"]], mode, (v) => { mode = v; draw(); }, { lime: true, label: "Pick for", small: true });
    const openToggle = h("div", { class: "inline-toggle" }, toggle(true, (on) => { openOnly = on; draw(); }, "Open-weight models only"), h("span", { "aria-hidden": "true", text: "Open-weight" }));
    const price = (r) => {
      if (r.input_per_m == null && r.output_per_m == null) return "price unknown";
      const f = (v) => (v == null ? "?" : v >= 10 ? `$${nf(v, 0)}` : `$${Number(v).toFixed(2)}`);
      return `${f(r.input_per_m)} / ${f(r.output_per_m)} per 1M`;
    };
    const cap = (v, label) => (v == null ? null : h("span", { class: `cap ${v ? "yes" : "no"}`, title: `${label} ${v ? "supported" : "not supported"}` }, icon(v ? "check" : "x"), label));
    const ctxLabel = (n) => (n ? (n >= 1e6 ? `${nf(n / 1e6, 1)}M ctx` : `${Math.round(n / 1e3)}K ctx`) : null);
    const saveBtn = btn("Save selection", async () => {
      if (!cands.length || !judge) throw Object.assign(new Error("Pick at least one candidate and a judge."), { fix: "Tap models in Candidates mode, then switch to Judge mode." });
      await api("/api/config", { candidates: cands, judge_model: judge });
      await refresh();
      drawSummary();
      toast(`Saved · ${cands.length} candidate${cands.length === 1 ? "" : "s"} and judge ${short(judge)}`);
      onSaved && onSaved();
    }, { ic: "check" });
    const sugBtn = (sug.candidates || []).length ? btn("Use suggested", async () => {
      cands = [...sug.candidates].slice(0, 4);
      if (sug.judge) judge = sug.judge;
      draw(); drawSummary();
      toast("Suggested models picked. Review them, then save.");
    }, { kind: "secondary", ic: "sparkle" }) : null;
    function drawSummary() {
      const dirty = isDirty();
      status.textContent = dirty ? "Unsaved changes" : "Saved";
      status.className = `save-state${dirty ? " dirty" : ""}`;
      saveBtn.disabled = !dirty;
      put(summary, 
        h("div", { class: "pk" }, h("span", { class: "lbl", text: `Candidates (${cands.length}/4)` }),
          cands.length ? h("div", { class: "pk-list" }, badgeStack(cands), h("span", { class: "wrap", text: cands.map(short).join(", ") })) : h("span", { class: "muted", text: "None yet. Tap models in Candidates mode." })),
        h("div", { class: "pk" }, h("span", { class: "lbl", text: "Judge" }),
          judge ? h("div", { class: "pk-list" }, badge(judge, { tone: "lav" }), h("span", { class: "wrap", text: judge })) : h("span", { class: "muted", text: "Not set. Switch to Judge mode and tap a strong model." })));
    }
    const cost = (r) => (r.input_per_m == null && r.output_per_m == null ? Infinity : (r.input_per_m || 0) + (r.output_per_m || 0));
    function draw() {
      const term = q.value.trim().toLowerCase();
      const picked = new Set([...cands, judge].filter(Boolean));
      const list = [...rows.values()].filter((r) => (!term || r.id.toLowerCase().includes(term)) && (!openOnly || r.open_weight !== false || picked.has(r.id)));
      list.sort((a, b) => (picked.has(b.id) - picked.has(a.id)) || (sugSet.has(b.id) - sugSet.has(a.id)) || ((cost(a) - cost(b)) || 0) || a.id.localeCompare(b.id));
      const shown = showAll || term ? list : list.slice(0, 40);
      put(chips, ...shown.map((r) => {
        const m = r.id, isC = cands.includes(m), isJ = judge === m;
        const b = h("button", { type: "button", class: `mchip${isC ? " cand" : ""}${isJ ? " judge" : ""}`, "aria-pressed": String(mode === "cand" ? isC : isJ), title: m },
          h("span", { class: "mc-top" }, isC ? icon("check") : isJ ? icon("judge") : null, h("span", { class: "mc-id", text: m })),
          h("span", { class: "mc-meta" },
            h("span", { class: "mc-price", text: r.missing ? "not in this catalog" : price(r) }),
            r.open_weight === true ? h("span", { class: "cap ow", text: "open" }) : r.open_weight === false ? h("span", { class: "cap", text: "closed" }) : null,
            cap(r.json, "JSON"), cap(r.tools, "tools"), ctxLabel(r.context) ? h("span", { class: "cap", text: ctxLabel(r.context) }) : null,
            sugSet.has(m) ? h("span", { class: "cap sug" }, icon("sparkle"), "suggested") : null));
        b.addEventListener("click", () => {
          if (mode === "cand") {
            if (!isC && cands.length >= 4) { toast("Pick up to 4 candidates", { error: true, fix: "2 or 3 from different model families works best." }); return; }
            cands = isC ? cands.filter((x) => x !== m) : [...cands, m];
          } else judge = isJ ? "" : m;
          draw(); drawSummary();
        });
        return b;
      }), !shown.length ? h("p", { class: "muted", text: openOnly ? "No open-weight model matches. Turn off the filter to see all models." : "No model matches." }) : null,
      list.length > shown.length ? h("button", { class: "mchip more", type: "button", onclick: () => { showAll = true; draw(); }, text: `Show all ${list.length}` }) : null);
    }
    q.addEventListener("input", draw);
    draw(); drawSummary();
    const el = h("div", { class: "picker" },
      h("div", { class: "picker-bar" }, h("label", { class: "search-wrap" }, icon("search"), q), modeSeg, openToggle),
      chips, summary,
      h("div", { class: "actions" }, saveBtn, sugBtn, status),
      h("p", { class: "hint" }, icon("info"), h("span", { text: "Prices are $ per 1M input / output tokens from the Otari catalog. Pick candidates from different families so one weakness does not hide another, and a strong judge, ideally from another family. Nothing is saved until you press Save selection." })));
    el.isDirty = isDirty;
    el.save = async () => { if (isDirty()) { await api("/api/config", { candidates: cands, judge_model: judge }); await refresh(); drawSummary(); } };
    return el;
  }

  function budgetTile(span = 5) {
    const c = S.config;
    let budget = Number(c.budget_usd) || 1;
    const num = bigNum(budget, moneyParts);
    const est = h("p", { class: "hint" });
    const slider = h("input", { type: "range", min: "0.5", max: "20", step: "0.5", value: String(budget), "aria-label": "Evaluation budget in dollars" });
    let t = null;
    const setFill = () => slider.style.setProperty("--p", `${((budget - 0.5) / 19.5) * 100}%`);
    const update = () => {
      const n = Object.entries(S.recordings || {}).filter(([id]) => !S.config.excluded.includes(id)).reduce((a, [, v]) => a + v, 0);
      const k = S.config.candidates.length;
      put(est, icon("info"), h("span", { text: n && k ? `About ${nf(n * k * 2 + n)} calls through Otari (${n} prompts × ${k} candidate${k === 1 ? "" : "s"}, plus judging). The run stops at the cap, never past it.` : "The run stops at the cap, never past it." }));
    };
    slider.addEventListener("input", () => { budget = Number(slider.value); num.set(budget); setFill(); clearTimeout(t); t = setTimeout(() => api("/api/config", { budget_usd: budget }).then(refresh).catch(fail), 300); });
    setFill(); update();
    const el = tile({ span, wide: true, title: "Evaluation budget", sub: "A hard cap for one evaluation." },
      h("div", { class: "budget-num" }, num, h("span", { class: "muted", text: "cap per run" })), slider, h("div", { class: "range-ends" }, h("span", { text: "$0.50" }), h("span", { text: "$20" })), est);
    el.update = update;
    return el;
  }

  /* ---------- Models ---------- */
  function screenModels(ctx) {
    const pickerTile = tile({ span: 7, wide: true, cls: "tall", title: "Model catalog", sub: "From your Otari key" }, skelRows(4));
    const budgetT = budgetTile();
    let picker = null;
    const startBtn = btn("Start evaluation", async () => {
      if (picker) await picker.save();
      await refresh();
      if (!S.config.candidates.length || !S.config.judge_model) throw Object.assign(new Error("Pick at least one candidate and a judge."), { fix: "Tap models in Candidates mode, then switch to Judge mode." });
      await startEval();
      go("#/evaluate");
    }, { ic: "play" });
    const lead = tile({ span: 5, wide: true, cls: "lead" },
      head("Models", { extra: S.demo ? demoBadge() : null }),
      h("p", { class: "lede", text: "Open-weight candidates answer your recorded prompts. A separate judge scores each answer against today's." }),
      h("div", { class: "actions" }, startBtn, linkBtn("Record more prompts", "#/record", { kind: "secondary" })));
    if (!S.config.key_set) {
      put(pickerTile, pickerTile.firstChild, emptyState("judge", "Connect Otari first", "The catalog comes from the models your key can reach.", linkBtn("Connect Otari", "#/connect", { ic: "plug" })));
    } else {
      api("/api/models").then((r) => { if (!ctx.alive()) return; picker = modelPicker(r, () => budgetT.update()); put(pickerTile, pickerTile.firstChild, picker); })
        .catch((e) => { if (ctx.alive()) put(pickerTile, pickerTile.firstChild, errNote(e)); });
    }
    return screen(lead, pickerTile, budgetT);
  }

  /* ---------- Scan ---------- */
  function screenScan(ctx) {
    const lead = tile({ span: 5, wide: true, cls: "lead sticky" }, head("Scan"), skelRows(3));
    const sitesTile = tile({ span: 7, wide: true, title: "Call sites", sub: "Turn a call off to leave it out of the evaluation and the PR." }, skelRows(5));
    const side = h("div", { class: "subsec" });
    const errs = tile({ span: 12, wide: true, title: "Files that could not be read" });
    errs.hidden = true;
    const draw = (scan) => {
      const ex = new Set(S.config.excluded);
      const sites = scan.sites;
      const providers = {};
      sites.forEach((x) => { providers[x.provider] = (providers[x.provider] || 0) + 1; });
      const evaluable = sites.filter((x) => x.evaluable);
      put(lead, 
        head("Scan", { date: `${scan.files_scanned} Python files read`, extra: S.demo ? demoBadge() : null }),
        h("div", { class: "kpis" },
          kpi("AI call sites found", sites.length, intParts, { sub: `${evaluable.length} can be evaluated · code is parsed, never run`, chip: chip(`${evaluable.filter((x) => !ex.has(x.id)).length} included`, "lime") })),
        h("div", { class: "chips-row" }, Object.entries(providers).map(([p, n]) => chip(`${p} · ${n}`, "gray"))),
        h("div", { class: "actions" },
          sites.length ? linkBtn("Record prompts", "#/record", { ic: "arrowRight" }) : null,
          btn("Scan again", async () => { const r = await api("/api/scan", {}); await refresh(); draw(r); toast(`Scanned · ${r.sites.length} AI call site${r.sites.length === 1 ? "" : "s"}`); }, { kind: "secondary", ic: "refresh" })),
        side);
      if (!sites.length) {
        put(sitesTile, sitesTile.firstChild, emptyState("scan", "No AI calls found", "Otari Switch looks for OpenAI and Anthropic SDK calls (chat.completions, responses, messages) in Python files."));
      } else {
        put(sitesTile, sitesTile.firstChild, h("div", { class: "list sites" }, sites.map((x) => {
          const tags = [chip(`${x.provider} · ${x.endpoint}`, "gray"), chip(x.model || (x.model_env ? `$${x.model_env}` : x.model_source), x.model ? "lav" : "gray"),
            ...(x.flags || []).map((f) => chip(f === "json" ? "JSON" : f, "outline")), x.note ? chip(x.note, "outline") : null];
          const right = x.evaluable ? toggle(!ex.has(x.id), async (on, input) => {
            on ? ex.delete(x.id) : ex.add(x.id);
            try { await api("/api/config", { excluded: [...ex] }); await refresh(); toast(on ? `${x.function} included` : `${x.function} left out`); }
            catch (e) { input.checked = !on; on ? ex.add(x.id) : ex.delete(x.id); fail(e); }
          }, `Include ${x.function}`) : chip("Listed only", "gray");
          return h("div", { class: `row site${x.evaluable ? "" : " off"}`, id: `site-${x.id.replace(/[^\w-]/g, "_")}` },
            h("span", { class: "row-ic" }, icon("code")),
            h("div", { class: "t" }, h("b", { text: x.function }), h("small", { class: "mono", text: `${x.file}:${x.line}` }), h("div", { class: "tags" }, tags)),
            right);
        })));
      }
      const clients = scan.clients || [];
      put(side, h("h3", { class: "subh", text: "Clients pointed at Otari" }), h("p", { class: "tile-sub", text: "Constructors the rewrite updates" }), clients.length ? h("div", { class: "list" }, clients.map((k) =>
        h("div", { class: "row" }, h("span", { class: "row-ic" }, icon("plug")), h("div", { class: "t" }, h("b", { text: `${k.cls}()` }), h("small", { class: "mono", text: `${k.file}:${k.line}` })),
          chip(k.has_base_url ? "has base_url" : "default URL", "gray"))))
        : h("p", { class: "muted", text: "No client constructors found. Calls are still rewritten per call site." }));
      errs.hidden = !(scan.parse_errors || []).length;
      if (!errs.hidden) put(errs, errs.firstChild, h("div", { class: "list" }, scan.parse_errors.map((e) => h("div", { class: "row" }, h("span", { class: "row-ic bad" }, icon("warn")), h("div", { class: "t" }, h("small", { class: "mono wrap", text: e }))))));
    };
    if (S.scan) draw(S.scan);
    else api("/api/scan", {}).then(async (r) => { await refresh(); if (ctx.alive()) draw(r); }).catch((e) => { if (ctx.alive()) put(lead, head("Scan"), errNote(e)); });
    return screen(lead, sitesTile, errs);
  }

  /* ---------- Record ---------- */
  function screenRecord(ctx) {
    let source = "tests", fresh = false, viaOtari = !!S.config.key_set, lastModels = null, lastVia = null;
    const arg = h("input", { class: "field mono-in", value: S.config.tests_command, spellcheck: "false", "aria-label": "Test command" });
    const argLbl = h("span", { class: "lbl", text: "Command" });
    const help = h("p", { class: "hint" });
    const helps = {
      tests: "Runs your test suite with recording on. Otari Switch saves request and answer per call site.",
      script: "Runs a Python script that makes real AI calls, for repos whose tests mock the SDK.",
      import: "Imports a JSONL log of past requests and answers (one per line, with site_id).",
    };
    // record through Otari: the app's own calls go via the gateway, so no direct provider key is needed
    const asModel = h("input", { class: "field mono-in", list: "rec-models", placeholder: "Leave empty to use today's model", spellcheck: "false", autocomplete: "off", "aria-label": "Record on model" });
    const modelList = h("datalist", { id: "rec-models" });
    const viaRow = h("div", { class: "field-row inline" },
      h("div", {}, h("b", { text: "Record through Otari" }), h("small", { class: "muted", text: "Your app's calls go through Otari on today's model, so you don't need a direct OpenAI or Anthropic key." })),
      toggle(viaOtari, (v) => { viaOtari = v; setSource(source); }, "Record through Otari"));
    const adv = h("details", { class: "adv" }, h("summary", { text: "Advanced" }),
      h("label", { class: "field-row" }, h("span", { class: "lbl", text: "Record on model" }), asModel, modelList),
      h("p", { class: "hint" }, icon("info"), h("span", { text: "Records every call on this one Otari model, for gateways that don't serve today's model. Implies Record through Otari." })));
    if (S.config.key_set) {
      api("/api/models").then((r) => { if (ctx.alive()) put(modelList, (r.catalog && r.catalog.length ? r.catalog.map((x) => x.id) : r.models || []).map((m) => h("option", { value: m }))); }).catch(() => {});
    }
    const setSource = (v) => {
      source = v;
      viaRow.hidden = v === "import" || !S.config.key_set;
      adv.hidden = v === "import" || !S.config.key_set;
      const via = v !== "import" && S.config.key_set && (viaOtari || asModel.value.trim());
      put(help, icon("info"), h("span", { text: helps[v] + (v === "import" ? "" : via ? " Calls go through Otari." : " Calls go straight to your current provider; Otari Switch only watches.") }));
      argLbl.textContent = v === "tests" ? "Command" : v === "script" ? "Script path" : "Log file";
      arg.setAttribute("aria-label", argLbl.textContent);
      arg.value = v === "tests" ? S.config.tests_command : "";
      arg.placeholder = v === "script" ? "scripts/demo_calls.py" : v === "import" ? "logs/requests.jsonl" : "pytest -q";
    };
    asModel.addEventListener("input", () => setSource(source));
    setSource("tests");
    const progress = h("div", { class: "rec-progress", hidden: true, role: "status" },
      h("div", { class: "eq", "aria-hidden": "true" }, Array.from({ length: 9 }, () => h("i"))), h("span", { class: "rp-t" }));
    const resultsTile = tile({ span: 7, wide: true, title: "Recorded prompts", sub: "Per call site. Deduplicated, up to the per-site limit." });
    const outTile = tile({ span: 7, wide: true, title: "Recorder output" });
    outTile.hidden = true;
    const privacyTile = tile({ span: 5, wide: true, title: "Privacy check" });

    const drawCounts = (counts) => {
      const entries = Object.entries(counts || {});
      if (!entries.length) { put(resultsTile, resultsTile.firstChild, emptyState("record", "Nothing recorded yet", "Choose a source and press Record. Recordings stay in .otari-switch/, which is git-ignored.")); return; }
      const used = Object.entries(lastModels || {});
      const max = Math.max(...entries.map(([, n]) => n));
      const total = sum(entries.map(([, n]) => n));
      put(resultsTile, resultsTile.firstChild,
        h("div", { class: "kpis" }, kpi("Prompts recorded", total, intParts, { sub: `Across ${entries.length} call site${entries.length === 1 ? "" : "s"}` })),
        h("div", { class: "list" }, entries.map(([id, n], i) => {
          const v = h("b", { class: "num-sm" }); countTo(n, (x) => (v.textContent = String(Math.round(x ?? 0))));
          return h("div", { class: "row" }, h("div", { class: "t" }, h("b", { text: fnName(id) }), h("small", { class: "mono", text: fileOf(id) }), hbar(n / max, i % 2 ? "lav" : "lime")), v);
        })),
        lastVia != null ? h("div", { class: "subsec" },
          h("h3", { class: "subh", text: lastVia ? "Recorded through Otari on" : "Recorded directly with the provider" }),
          used.length ? h("div", { class: "list compact" }, used.map(([id, m]) => h("div", { class: "row" }, h("div", { class: "t" }, h("b", { text: fnName(id) }), h("small", { class: "mono", text: fileOf(id) })), chip(m || "today's model", "lav")))) 
            : h("p", { class: "muted small", text: lastVia ? "Otari reported no model per call site for this recording." : "No Otari models were involved." })) : null);
    };
    const drawPrivacy = (extra) => {
      const mode = S.config.privacy_mode;
      const latestMasked = extra?.masked ?? null;
      put(privacyTile, privacyTile.firstChild,
        h("div", { class: "priv" }, h("img", { src: "/static/img/privacy.svg", alt: "", width: "240", height: "180" }),
          h("div", {},
            latestMasked != null ? h("div", { class: "kpis" }, kpi("Private values masked", latestMasked, intParts, { sub: extra.skipped ? `${extra.skipped} recording(s) skipped` : extra.from || "Before replay" })) : null,
            h("p", { class: "muted", text: mode === "mask" ? "Mode: mask. Emails, API keys, card numbers (Luhn-checked) and phone numbers become consistent placeholders before anything is replayed."
              : mode === "skip" ? "Mode: skip. Recordings that contain private values are left out of the evaluation."
                : "Mode: off. Recordings are replayed as recorded." }),
            latestMasked == null ? h("p", { class: "hint" }, icon("info"), h("span", { text: "The masked count is reported when the evaluation runs its privacy check." })) : null,
            h("a", { class: "link", href: "#/settings" }, "Privacy mode in Settings", icon("chevRight")))));
    };
    drawCounts(S.recordings);
    drawPrivacy(null);
    if (S.latest) {
      api(`/api/run?id=${encodeURIComponent(S.latest.id)}`).then((r) => {
        if (!ctx.alive()) return;
        const f = (r.findings || []).find((x) => /masked \d+ value/.test(x));
        const m = f ? Number(/masked (\d+) value/.exec(f)[1]) : 0;
        drawPrivacy({ masked: m, skipped: 0, from: `In the last evaluation (${rel(r.started)})` });
      }).catch(() => {});
    }
    const hasRecs = Object.keys(S.recordings || {}).length > 0;
    const actions = h("div", { class: "actions" });
    const go2 = async () => {
      progress.hidden = false;
      const t0 = Date.now();
      const label = source === "tests" ? `Running ${arg.value || S.config.tests_command} with recording on${S.config.key_set && (viaOtari || asModel.value.trim()) && source !== "import" ? ", through Otari" : ""}…` : source === "script" ? `Running ${arg.value}…` : `Importing ${arg.value}…`;
      const tick = setInterval(() => { progress.querySelector(".rp-t").textContent = `${label} ${Math.round((Date.now() - t0) / 1000)}s`; }, 500);
      progress.querySelector(".rp-t").textContent = label;
      const before = fresh ? 0 : sum(Object.values(S.recordings || {}));
      try {
        const am = asModel.value.trim();
        const body = { source, arg: arg.value.trim() || null, fresh };
        if (source !== "import" && S.config.key_set && (viaOtari || am)) body.via_otari = true;
        if (source !== "import" && am) body.as_model = am;
        const res = await api("/api/record", body);
        lastModels = res.models || null;
        lastVia = res.via_otari ?? (body.via_otari ? true : null);
        const added = sum(Object.values(res.counts || {})) - before;
        await refresh();
        drawCounts(res.counts);
        if (res.privacy) drawPrivacy({ masked: res.privacy.masked, skipped: res.privacy.skipped, from: "In these recordings" });
        outTile.hidden = !(res.detail || res.hint || (res.problems || []).length || added <= 0);
        put(outTile, outTile.firstChild,
          res.hint ? note("warn", "No calls were recorded", res.hint)
            : added <= 0 ? note("warn", "No new prompts this time", "Identical requests are skipped, and the existing recordings are kept. Check the output below if you expected new ones.") : null,
          res.detail ? h("pre", { class: "out", text: res.detail }) : null,
          (res.problems || []).length ? h("div", { class: "list" }, res.problems.map((x) => h("div", { class: "row" }, h("span", { class: "row-ic bad" }, icon("warn")), h("div", { class: "t" }, h("small", { class: "mono wrap", text: x }))))) : null);
        if (Object.keys(res.counts).length) {
          toast(added > 0 ? `Recorded ${added} new prompt${added === 1 ? "" : "s"}` : "No new prompts recorded", { error: false });
          put(actions, linkBtn("Choose models", "#/models", { ic: "arrowRight" }), btn("Record again", go2, { kind: "secondary", ic: "refresh" }));
        }
      } finally { clearInterval(tick); progress.hidden = true; }
    };
    put(actions, btn("Record", go2, { ic: "wave" }), hasRecs ? linkBtn("Use these recordings", "#/models", { kind: "secondary", ic: "arrowRight" }) : null);
    const lead = tile({ span: 5, wide: true, cls: "lead" },
      head("Record", { date: hasRecs ? `${sum(Object.values(S.recordings))} prompts saved` : "Nothing saved yet" }),
      h("p", { class: "lede", text: "Real prompts make the evaluation honest: the same requests your app sends today." }),
      h("div", { class: "form" },
        h("div", { class: "field-row" }, h("span", { class: "lbl", text: "Source" }), seg([["tests", "Tests"], ["script", "Script"], ["import", "Import log"]], "tests", setSource, { label: "Recording source" })),
        h("label", { class: "field-row" }, argLbl, arg),
        viaRow,
        h("div", { class: "field-row inline" }, h("div", {}, h("b", { text: "Start fresh" }), h("small", { class: "muted", text: "Delete earlier recordings first" })), toggle(false, (v) => (fresh = v), "Start fresh")),
        adv,
        help),
      actions, progress);
    return screen(lead, resultsTile, privacyTile, outTile);
  }

  /* ---------- Evaluate (live) ---------- */
  function screenEvaluate(ctx) {
    const c = S.config;
    if (!LIVE.job) return evalPreflight(ctx);
    const STAGES = [["privacy", "Privacy"], ["replay", "Replay & judge"], ["tests", "Repo tests"], ["sandbox", "Sandbox checks"]];
    const titleEl = h("h1", { tabindex: "-1" });
    const stageRow = h("ol", { class: "stages", "aria-label": "Stages" });
    const spend = bigNum(LIVE.spend, spendParts);
    const capBar = hbar(0);
    const capTxt = h("span", { class: "muted small" });
    const countTxt = h("span", { class: "muted small" });
    const doneBar = hbar(0, "lav");
    const errBox = h("div");
    const actions = h("div", { class: "actions" });
    const sitesGrid = h("div", { class: "egrid" });
    const feedList = h("ol", { class: "feed", "aria-label": "Event feed" });
    const maskedEl = h("div");
    let shownFeed = 0, lastSpend = -1;
    const cards = new Map();

    const lead = tile({ span: 5, wide: true, cls: "lead sticky" },
      h("div", { class: "screen-head" }, titleEl, h("div", { class: "head-meta" }, h("span", { class: "daterange" }, icon("clock"), h("span", { text: `Started ${tShort(new Date(LIVE.startedAt || Date.now()))}` })), S.demo ? demoBadge() : null)),
      stageRow,
      h("div", { class: "kpis" }, h("div", { class: "kpi" }, h("div", { class: "kpi-l" }, h("div", { class: "lbl", text: "Spent so far" }), spend, h("div", { class: "spend-bar" }, capBar, capTxt)))),
      h("div", { class: "prog" }, doneBar, countTxt), maskedEl, errBox, actions);
    const sitesTile = tile({ span: 7, wide: true, title: "Call sites", sub: "Rings fill as answers are judged; verdicts pop in per call site." }, sitesGrid);
    const feedTile = tile({ span: 12, wide: true, title: "Event feed", sub: "Newest first. Request ids link each answer to its Otari trace." }, feedList);

    function card(st) {
      if (cards.has(st.id)) return cards.get(st.id);
      const ring = progressRing(0, 62, 7);
      const models = h("div", { class: "emodels" });
      const pillSlot = h("span", {}, vpill(null));
      const sub = h("small", { class: "mono", text: fileOf(st.id) });
      const el = h("div", { class: "ecard" }, h("div", { class: "ec-top" }, ring, h("div", { class: "t" }, h("b", { text: fnName(st.id) }), sub)), pillSlot, models);
      sitesGrid.append(el);
      const cd = { el, ring, models, pillSlot, sub, rows: new Map(), verdict: null };
      cards.set(st.id, cd);
      return cd;
    }
    function sync() {
      if (!ctx.alive()) return;
      const finished = !LIVE.running;
      titleEl.textContent = LIVE.done ? "Evaluation complete" : LIVE.cancelled ? "Evaluation cancelled" : LIVE.error && finished ? "Evaluation stopped" : LIVE.interrupted ? "Connection lost" : "Evaluating";
      put(stageRow, ...STAGES.map(([k, l]) => {
        const seen = LIVE.stagesSeen.has(k);
        const cur = LIVE.stage === k && LIVE.running;
        const st = cur ? "cur" : seen ? "done" : !LIVE.running ? "skip" : "todo";
        return h("li", { class: st, title: st === "skip" ? "Not run in this evaluation" : null }, h("span", { class: "sd" }, st === "done" ? icon("check") : st === "skip" ? icon("dash") : st === "cur" ? h("span", { class: "spin sm dark" }) : null), l);
      }), LIVE.done ? h("li", { class: "done fin" }, h("span", { class: "sd" }, icon("check")), "Done") : null);
      if (LIVE.spend !== lastSpend) { spend.set(LIVE.spend); lastSpend = LIVE.spend; }
      const cap = LIVE.cap || Number(c.budget_usd) || 0;
      capBar.set(cap ? LIVE.spend / cap : 0);
      capBar.fill.className = cap && LIVE.spend / cap >= 0.8 ? "lav" : "lime";
      capTxt.textContent = cap ? `of a ${money(cap)} cap · ${pct(LIVE.spend / cap)}` : "";
      doneBar.set(LIVE.total ? LIVE.n / LIVE.total : 0);
      countTxt.textContent = LIVE.total ? `${LIVE.n} of ${LIVE.total} answers judged` : LIVE.running ? "Preparing…" : "";
      put(maskedEl, LIVE.masked != null ? h("p", { class: "hint" }, icon("shield"), h("span", { text: `Privacy check: ${LIVE.masked} private value${LIVE.masked === 1 ? "" : "s"} masked${LIVE.skipped ? `, ${LIVE.skipped} recording(s) skipped` : ""} before replay.` })) : null);
      put(errBox, LIVE.error ? errNote({ message: LIVE.error.message, fix: LIVE.error.fix, request_id: LIVE.error.request_id })
        : LIVE.interrupted ? note("warn", "The live connection was interrupted", "The evaluation may still be running on the server. Check Runs in a moment.") : null);
      if (LIVE.running) {
        if (!actions.querySelector(".cancel")) put(actions, btn("Cancel", async () => { await api("/api/cancel", { job: LIVE.job }); toast("Cancelling after the current answers…"); }, { kind: "danger", ic: "stop", cls: "cancel" }));
      } else if (!actions.dataset.final) {
        actions.dataset.final = "1";
        put(actions, 
          LIVE.done && LIVE.runId ? linkBtn("See the report", `#/report/${LIVE.runId}`, { ic: "arrowRight" }) : null,
          btn("Run again", async () => { await startEval(); render(); }, { kind: LIVE.done ? "secondary" : "primary", ic: "refresh" }));
      }
      for (const id of LIVE.order) {
        const st = LIVE.sites.get(id), cd = card(st);
        const f = st.verdict ? 1 : st.total ? st.done / st.total : 0;
        cd.ring.set(f, st.verdict ? (st.verdict === "keep" ? "–" : "✓") : `${Math.round(f * 100)}%`, st.verdict === "keep" ? "#5F5F65" : st.verdict === "switch_with_fallback" ? "#BBAEF6" : "#D5F26B");
        for (const [m, ms] of st.models) {
          let row = cd.rows.get(m);
          if (!row) {
            const bar = hbar(0, "lime"); const sc = h("span", { class: "sc", text: "–" });
            cd.models.append(h("div", { class: "mrow" }, h("span", { class: "mn", text: short(m), title: m }), bar, sc));
            row = { bar, sc }; cd.rows.set(m, row);
          }
          if (ms.skipped) { row.sc.textContent = "skip"; row.bar.set(1); row.bar.fill.className = "gray"; }
          else if (ms.mean != null) { row.bar.set(ms.mean / 5); row.bar.fill.className = ms.mean >= 4 ? "lime" : ms.mean >= 3 ? "lav" : "bad"; row.sc.textContent = ms.mean.toFixed(1); }
        }
        if (st.verdict && cd.verdict !== st.verdict) {
          cd.verdict = st.verdict;
          put(cd.pillSlot, vpill(st.verdict, true));
          cd.el.classList.add(`v-${st.verdict}`);
          cd.sub.textContent = st.chosen ? `→ ${st.chosen}` : "Stays on today's model, through Otari";
          cd.sub.classList.remove("mono");
          cd.sub.title = st.reason || "";
        }
      }
      if (!LIVE.order.length) put(sitesGrid, h("p", { class: "muted", text: LIVE.running ? "Waiting for the first call site…" : "No call sites were evaluated." }));
      else if (sitesGrid.querySelector("p.muted")) sitesGrid.querySelector("p.muted").remove();
      const fresh = LIVE.feed.slice(shownFeed);
      shownFeed = LIVE.feed.length;
      fresh.forEach((e) => { const li = feedItem(e); if (li) feedList.prepend(li); });
      while (feedList.children.length > 120) feedList.lastChild.remove();
    }
    LIVE.listeners.add(sync);
    onCleanup(() => LIVE.listeners.delete(sync));
    sync();
    return screen(lead, sitesTile, feedTile);
  }

  function feedItem(e) {
    const site = e.site ? fnName(e.site) : "";
    const time = e.t ? new Date(e.t * 1000).toLocaleTimeString("en-GB") : "";
    let ic = "info", tone = "", text = "", sub = "";
    switch (e.type) {
      case "stage": ic = "arrowRight"; text = { start: "Started", privacy: "Privacy check", replay: "Replaying prompts on open-weight models", tests: "Running your tests on the chosen models", sandbox: "Contract checks in Otari's sandbox" }[e.stage] || e.stage; if (e.stage === "start") sub = `${e.sites} call sites · ${e.recordings} recordings`; break;
      case "privacy": ic = "shield"; text = `Masked ${e.masked} private value${e.masked === 1 ? "" : "s"}`; sub = e.skipped ? `${e.skipped} skipped` : ""; break;
      case "candidate_start": ic = "play"; text = `${site} · ${short(e.model)}`; sub = `${e.total} prompts`; break;
      case "sample": ic = "judge"; tone = e.score >= 4 ? "good" : e.score >= 3 ? "" : "badt"; text = `${site} · ${short(e.model)} scored ${Number(e.score).toFixed(0)}/5`; sub = e.fallback ? `Otari fallback served ${e.served_model || "another model"}` : ""; break;
      case "candidate_done": ic = e.skipped ? "warn" : "check"; text = `${site} · ${short(e.model)} ${e.skipped ? "skipped: first request failed" : `done, mean ${Number(e.mean || 0).toFixed(1)}`}`; break;
      case "verdict": ic = V[e.verdict]?.icon || "info"; tone = e.verdict === "keep" ? "" : "good"; text = `${site} → ${V[e.verdict]?.label || e.verdict}${e.chosen ? ` · ${short(e.chosen)}` : ""}`; break;
      case "tests": ic = e.ok === false ? "warn" : e.ok ? "check" : "info"; tone = e.ok === false ? "badt" : ""; text = e.ok === true ? "Repo tests passed on the chosen models" : e.ok === false ? "Some repo tests failed on a chosen model" : "Repo tests not run"; sub = e.detail || ""; break;
      case "sandbox": ic = "terminal"; text = e.ok === true ? "Sandbox contract checks ran" : e.ok === false ? "Sandbox checks failed" : "Sandbox checks skipped"; break;
      case "error": ic = "warn"; tone = "badt"; text = e.message; sub = e.fix || ""; break;
      case "cancelled": ic = "stop"; text = "Cancelled"; break;
      case "done": ic = "check"; tone = "good"; text = `Done · ${e.summary?.moved ?? "?"} of ${e.summary?.evaluated ?? "?"} can switch`; sub = `Run ${e.run_id}`; break;
      default: return null;
    }
    return h("li", { class: `ev ${tone}` }, h("span", { class: "ev-ic" }, icon(ic)),
      h("div", { class: "ev-t" }, h("span", { text }), sub ? h("small", { text: sub }) : null),
      e.request_id ? copyBtn(e.request_id, "request id") : null, h("time", { text: time }));
  }

  function evalPreflight() {
    const c = S.config;
    const sites = (S.scan?.sites || []).filter((x) => x.evaluable && !c.excluded.includes(x.id));
    const withRecs = sites.filter((x) => (S.recordings || {})[x.id]);
    const prompts = sum(withRecs.map((x) => S.recordings[x.id]));
    const problems = [];
    if (!c.key_set) problems.push(["Connect Otari", "#/connect"]);
    if (!S.scan) problems.push(["Scan the repo", "#/scan"]);
    if (!prompts) problems.push(["Record prompts", "#/record"]);
    if (!c.candidates.length || !c.judge_model) problems.push(["Choose candidates and a judge", "#/models"]);
    const err = h("div");
    const start = btn("Start evaluation", async () => {
      put(err, );
      try { await startEval(); render(); } catch (e) { put(err, errNote(e)); throw e; }
    }, { ic: "play", disabled: problems.length > 0 });
    return screen(
      tile({ span: 5, wide: true, cls: "lead" },
        head("Evaluate", { extra: S.demo ? demoBadge() : null }),
        h("p", { class: "lede", text: "Privacy check, then every recorded prompt goes to each candidate through Otari, a judge scores the answers, and your tests run on the chosen models." }),
        h("div", { class: "kpis" },
          kpi("Call sites in scope", withRecs.length, intParts, { sub: `${prompts} recorded prompts` }),
          kpi("Spend cap", Number(c.budget_usd), moneyParts, { sub: "The run stops before the next call would pass it", chip: h("a", { class: "chip gray", href: "#/models" }, h("span", { text: "Change" })) })),
        problems.length ? note("warn", "Before you start", "", h("ul", { class: "todo" }, problems.map(([t, href]) => h("li", {}, h("a", { href, text: t }))))) : null,
        err, h("div", { class: "actions" }, start)),
      tile({ span: 7, wide: true, title: "Models in this run" },
        c.candidates.length ? h("div", { class: "list" }, c.candidates.map((m) => h("div", { class: "row" }, badge(m), h("div", { class: "t" }, h("b", { class: "wrap", text: m }), h("small", { text: "Candidate" })))),
          c.judge_model ? h("div", { class: "row" }, badge(c.judge_model, { tone: "lav" }), h("div", { class: "t" }, h("b", { class: "wrap", text: c.judge_model }), h("small", { text: "Judge" }))) : null)
          : emptyState("judge", "No candidates yet", "Pick open-weight models to compare with today's.", linkBtn("Choose models", "#/models", { kind: "secondary" }))),
      tile({ span: 12, wide: true, title: "Call sites", sub: "Only call sites with recordings are evaluated." },
        sites.length ? h("div", { class: "list" }, sites.map((x) => h("div", { class: "row" }, h("span", { class: "row-ic" }, icon("code")),
          h("div", { class: "t" }, h("b", { text: x.function }), h("small", { class: "mono", text: `${x.file}:${x.line} · ${x.model || x.model_source}` })),
          chip(`${(S.recordings || {})[x.id] || 0} prompts`, (S.recordings || {})[x.id] ? "lime" : "gray"))))
          : emptyState("scan", "No call sites in scope", "Scan the repo and include at least one call site.", linkBtn("Scan repo", "#/scan", { kind: "secondary" }))));
  }

  /* ---------- Report ---------- */
  function screenReport(ctx, id) {
    const lead = tile({ span: 5, wide: true, cls: "lead" }, head("Report"), skelRows(4));
    const donutT = tile({ span: 4, title: "Verdicts" }, skelRows(2));
    const qualT = tile({ span: 3, title: "Candidate quality", sub: "Mean judge score, 1 to 5" }, skelRows(2));
    const sitesT = tile({ span: 7, wide: true, title: "Call sites", sub: "Open one to see the answers side by side." }, skelRows(4));
    const costT = tile({ span: 5, wide: true, title: "Cost vs today", sub: "Per 1,000 calls, measured through Otari" }, skelRows(3));
    const findT = tile({ span: 12, wide: true, title: "Findings" });
    findT.hidden = true;
    api(`/api/run?id=${encodeURIComponent(id)}`).then((run) => {
      if (!ctx.alive()) return;
      if (S.latest && S.latest.id === run.id) markSeen(run.id);
      const sm = run.summary;
      const sites = run.sites.filter((x) => x.candidates.length);
      put(lead, 
        head("Report", { date: dateRange(run.started, run.finished), extra: run.demo ? demoBadge() : h("span", { class: "chip gray" }, h("span", { text: `Otari ${String(run.region).toUpperCase()}` })) }),
        h("p", { class: "lede strong" }, h("b", { text: `${sm.moved} of ${sm.evaluated}` }), " call sites can run on open-weight models."),
        h("div", { class: "kpis" },
          kpi("Open-weight share", sm.open_share, pctParts, { sub: `${sm.switch} switch · ${sm.switch_with_fallback} with fallback · ${sm.keep} keep`, chip: chip(`Tests ${sm.tests}`, sm.tests === "passed" ? "lime" : sm.tests === "failed" ? "bad" : "gray") }),
          kpi("Estimated monthly savings", sm.monthly_savings_usd, moneyParts, { empty: "Not priced", sub: sm.monthly_savings_usd == null ? "Otari returned no price for today's models" : `At ${nf(sm.monthly_calls_assumed)} calls a month` }),
          kpi("Spent on this run", run.spend_usd, spendParts, { sub: `of a ${money(run.budget_usd)} cap`, chip: run.budget_usd ? chip(`${pct(run.spend_usd / run.budget_usd)} of cap`, "lime") : null })),
        h("div", { class: "actions" },
          sm.evaluated ? linkBtn("Review changes", `#/changes/${run.id}`, { ic: "branch" }) : null,
          h("a", { class: "btn secondary", href: `/report/${encodeURIComponent(run.id)}?token=${encodeURIComponent(TOKEN)}`, target: "_blank", rel: "noopener" }, icon("external"), h("span", { text: "Full report" }))));
      put(donutT, donutT.firstChild, ...verdictDonut(run, { size: 210 }));
      // candidate quality: mean score per candidate across call sites
      const byModel = new Map();
      sites.forEach((x) => x.candidates.forEach((c) => {
        if (!c.samples.length) return;
        const m = byModel.get(c.model) || { sum: 0, n: 0, chosen: 0 };
        m.sum += c.mean_score; m.n += 1; if (x.chosen === c.model) m.chosen += 1;
        byModel.set(c.model, m);
      }));
      const qual = [...byModel.entries()].map(([model, m]) => ({ label: model, short: short(model), value: m.sum / m.n, chosen: m.chosen }));
      put(qualT, qualT.firstChild, qual.length ? h("div", {},
        pillBars(qual, { fmt: (v) => v.toFixed(1), height: 150 }),
        h("div", { class: "list compact" }, qual.map((q) => h("div", { class: "row" }, badge(q.label), h("div", { class: "t" }, h("b", { class: "wrap", text: short(q.label) }), h("small", { text: q.chosen ? `Chosen for ${q.chosen} call site${q.chosen === 1 ? "" : "s"}` : "Not chosen" })), h("b", { class: "num-sm", text: q.value.toFixed(1) })))))
        : emptyState(null, "No scores", "No candidate produced answers to judge."));
      put(sitesT, sitesT.firstChild, sites.length ? h("div", { class: "list" }, sites.map((x) => {
        const ch = x.candidates.find((c) => c.model === x.chosen);
        const r = h("button", { class: "row link-row", type: "button", "aria-label": `${x.site.function}: ${V[x.verdict].label}. Show details.` },
          badgeStack(x.candidates.map((c) => c.model), 3),
          h("div", { class: "t" }, h("b", { text: x.site.function }),
            h("small", { text: x.chosen ? `${today(x)} → ${short(x.chosen)} · score ${ch ? ch.mean_score.toFixed(1) : "?"}/5` : `${today(x)} · ${x.recordings} prompts` })),
          vpill(x.verdict), icon("chevRight", "chev"));
        r.addEventListener("click", () => siteSheet(x, run));
        return r;
      })) : emptyState("scan", "No call sites evaluated", "The run had no recordings in scope, or the budget was reached first."));
      // cost vs today
      const rows = sites.filter((x) => x.frontier_cost_per_1k != null || x.chosen);
      const maxCost = Math.max(0.0001, ...rows.flatMap((x) => [x.frontier_cost_per_1k || 0, x.candidates.find((c) => c.model === x.chosen)?.cost_per_1k || 0]));
      put(costT, costT.firstChild, rows.length ? h("div", { class: "list cost" }, rows.map((x) => {
        const ch = x.candidates.find((c) => c.model === x.chosen);
        const diff = ch && x.frontier_cost_per_1k ? 1 - ch.cost_per_1k / x.frontier_cost_per_1k : null;
        return h("div", { class: "row col" },
          h("div", { class: "cost-h" }, h("b", { text: x.site.function }), diff != null ? chip(`${diff >= 0 ? "−" : "+"}${Math.abs(Math.round(diff * 100))}%`, diff >= 0 ? "lime" : "lav") : null),
          h("div", { class: "cbar" }, h("span", { class: "cl", text: `Today · ${today(x)}` }), hbar((x.frontier_cost_per_1k || 0) / maxCost, "lav"), h("span", { class: "cv", text: x.frontier_cost_per_1k != null ? money(x.frontier_cost_per_1k) : "not measured" })),
          ch ? h("div", { class: "cbar" }, h("span", { class: "cl", text: short(ch.model) }), hbar(ch.cost_per_1k / maxCost, "lime"), h("span", { class: "cv", text: money(ch.cost_per_1k) })) : h("small", { class: "muted", text: "Keeps today's model" }),
          ch ? h("small", { class: "muted", text: `Latency through Otari: p50 ${nf(ch.p50_ms)} ms · p95 ${nf(ch.p95_ms)} ms` }) : null);
      })) : h("p", { class: "muted", text: "No prices were measured in this run." }));
      findT.hidden = !(run.findings || []).length;
      if (!findT.hidden) put(findT, findT.firstChild, h("div", { class: "list" }, run.findings.map((f) => h("div", { class: "row" }, h("span", { class: "row-ic" }, icon("info")), h("div", { class: "t" }, h("span", { class: "wrap", text: f }))))));
      const want = ctx.params.get("site");
      if (want) { const x = sites.find((y) => y.site.id === want); if (x) siteSheet(x, run); }
    }).catch((e) => { if (ctx.alive()) put(lead, head("Report"), errNote(e), h("div", { class: "actions" }, linkBtn("All runs", "#/runs", { kind: "secondary" }))); });
    return screen(lead, donutT, qualT, sitesT, costT, findT);
  }

  function siteSheet(x, run) {
    const best = x.candidates.find((c) => c.model === x.chosen) || [...x.candidates].sort((a, b) => b.mean_score - a.mean_score)[0];
    let pick = best ? best.model : null;
    const answers = h("div", { class: "answers" });
    const pol = x.policy;
    const drawAnswers = () => {
      const cand = x.candidates.find((c) => c.model === pick);
      if (!cand || !cand.samples.length) { put(answers, h("p", { class: "muted", text: "No answers were judged for this model." })); return; }
      put(answers, h("p", { class: "hint" }, icon("info"), h("span", { text: `Lowest-scoring answers first: what a reviewer needs to see. ${cand.samples.length} judged.` })),
        ...cand.samples.slice(0, 6).map((smp, i) => {
          const tone = smp.score >= 4 ? "lime" : smp.score >= 3 ? "lav" : "bad";
          const checks = Object.entries(smp.checks || {});
          return h("article", { class: "sample" },
            h("div", { class: "s-head" }, chip(`Score ${Number(smp.score).toFixed(1)} / 5`, tone), checks.map(([k, ok]) => chip(k.replace(/_/g, " "), ok ? "outline" : "bad", ok ? "check" : "x")),
              h("span", { class: "grow" }), smp.request_id ? copyBtn(smp.request_id, "answer request id") : null, smp.judge_request_id ? copyBtn(smp.judge_request_id, "judge request id") : null),
            h("p", { class: "s-reason", text: smp.reason }),
            h("div", { class: "cmp" },
              h("div", {}, h("h5", {}, h("i", { class: "dotc lav" }), `Today · ${today(x)}`), h("pre", { text: smp.frontier })),
              h("div", {}, h("h5", {}, h("i", { class: "dotc lime" }), short(cand.model)), h("pre", { text: smp.candidate }))),
            smp.prompt_preview ? h("details", { class: "prompt" }, h("summary", { text: `Prompt ${i + 1} (end of it)` }), h("pre", { text: smp.prompt_preview })) : null);
        }));
    };
    const candRows = h("div", { class: "list" }, x.candidates.map((c) => {
      const t = c.tests_passed === true ? chip("tests passed", "lime") : c.tests_passed === false ? chip("tests failed", "bad") : null;
      return h("div", { class: `row col cand-row${c.model === x.chosen ? " chosen" : ""}` },
        h("div", { class: "cr-h" }, badge(c.model), h("b", { class: "wrap", text: c.model }), c.model === x.chosen ? chip("Chosen", "lime", "check") : null),
        h("div", { class: "cr-stats" },
          stat("Score", c.mean_score.toFixed(1)), stat("Usable", pct(c.share_ok)), stat("Format checks", pct(c.check_pass_rate)),
          stat("Cost / 1k", money(c.cost_per_1k)), stat("p50", `${nf(c.p50_ms)} ms`), stat("p95", `${nf(c.p95_ms)} ms`), stat("Errors", String(c.errors))),
        h("div", { class: "tags" }, t, (c.rejected_params || []).map((p) => chip(`rejects ${p}`, "bad", "x"))),
        c.findings.length ? h("ul", { class: "findings" }, c.findings.slice(0, 4).map((f) => h("li", { text: f }))) : null);
    }));
    const facts = h("div", { class: "facts" },
      fact("Today's model", today(x)), x.app_model && x.frontier_model && x.app_model !== x.frontier_model ? fact("Otari id", x.frontier_model) : null, fact("Chosen", x.chosen || "none, keeps today's model"),
      fact("Recordings", String(x.recordings)), x.frontier_cost_per_1k != null ? fact("Today's cost / 1k", money(x.frontier_cost_per_1k)) : null,
      pol ? fact("Policy", pol.name) : null, pol ? fact("Env var", pol.env_var) : null,
      fact("Fallback", pol?.runtime_guard ? "Runtime JSON guard + policy failover" : x.verdict === "keep" ? "Not needed" : "Policy failover to today's model"));
    const rejected = x.candidates.filter((c) => (c.rejected_params || []).length);
    sheet(x.site.function, h("div", { class: "site-sheet" },
      h("div", { class: "ss-top" }, vpill(x.verdict), h("span", { class: "mono muted", text: `${x.site.file}:${x.site.line}` }), (x.site.flags || []).map((f) => chip(f, "outline"))),
      h("p", { class: "ss-reason", text: x.reason }),
      facts,
      pol ? h("div", { class: "chain-wrap" }, h("h4", { text: "Routing policy" }), chainPills(pol)) : null,
      rejected.length ? note("warn", "Models that rejected a parameter", rejected.map((c) => `${c.model}: ${c.rejected_params.join(", ")}`).join(" · ")) : null,
      h("h4", { text: "Candidates" }), candRows,
      h("div", { class: "ans-head" }, h("h4", { text: "Answers side by side" }),
        x.candidates.length > 1 ? seg(x.candidates.map((c) => [c.model, short(c.model)]), pick, (v) => { pick = v; drawAnswers(); }, { label: "Candidate", small: true }) : null),
      answers), { wide: true });
    drawAnswers();
  }
  const stat = (k, v) => h("div", { class: "stat" }, h("small", { text: k }), h("b", { text: v }));
  const fact = (k, v, cls) => h("div", { class: `fact${cls ? " " + cls : ""}` }, h("small", { text: k }), h("b", { class: "wrap", text: v }));
  function chainPills(pol) {
    const roles = ["Serves traffic", "Backup", "Today's model"];
    const n = pol.candidates.length;
    return h("ol", { class: "chain", "aria-label": `Policy ${pol.name}` }, pol.candidates.map((c, i) => {
      const last = i === n - 1 && n > 1;
      const tone = i === 0 && pol.verdict !== "keep" ? "lime" : last || pol.verdict === "keep" ? "gray" : "lav";
      const role = pol.verdict === "keep" ? "Today's model" : i === 0 ? roles[0] : last ? roles[2] : roles[1];
      return h("li", { class: `cnode ${tone}` }, i > 0 ? h("span", { class: "clink", "aria-hidden": "true" }) : null,
        h("span", { class: "cpill" }, h("span", { class: "cn", text: String(i + 1) }), h("span", { class: "cm", text: c.model, title: c.model })), h("small", { text: role }));
    }));
  }

  /* ---------- Changes / PR ---------- */
  function screenChanges(ctx, id) {
    const lead = tile({ span: 5, wide: true, cls: "lead sticky" }, head("Changes"), skelRows(3));
    const content = tile({ span: 7, wide: true });
    const resultT = tile({ span: 12, wide: true, title: "Result" });
    resultT.hidden = true;
    api(`/api/preview?id=${encodeURIComponent(id)}`).then((pv) => {
      if (!ctx.alive()) return;
      let view = "code";
      const adds = sum(pv.files.map((f) => f.diff.split("\n").filter((l) => l.startsWith("+") && !l.startsWith("+++")).length));
      const dels = sum(pv.files.map((f) => f.diff.split("\n").filter((l) => l.startsWith("-") && !l.startsWith("---")).length));
      const views = {
        code: () => pv.files.length ? pv.files.map((f) => h("div", { class: "dfile" }, h("div", { class: "dfile-h" }, icon("code"), h("b", { class: "mono wrap", text: f.file })), diffBlock(f.diff)))
          : [emptyState("pr", "No code changes needed", "Every call site keeps its code; only routing policies change.")],
        policies: () => pv.policies.length ? [
          ...pv.policies.map((pol) => h("div", { class: "pol" },
            h("div", { class: "pol-h" }, h("b", { class: "mono wrap", text: pol.name }), vpill(pol.verdict), pol.runtime_guard ? chip("runtime JSON guard", "lav", "shield") : null),
            chainPills(pol),
            h("small", { class: "muted" }, "Your code reads the policy name from ", h("code", { text: pol.env_var }), ". The first model serves traffic; the next ones take over only if it fails."))),
          pv.dashboard_steps ? h("details", { class: "steps-md" }, h("summary", { text: "Dashboard steps (used when policies can't be created through the API)" }), h("pre", { text: pv.dashboard_steps })) : null]
          : [emptyState("pr", "No policies", "No call site produced a routing policy.")],
        pr: () => [h("div", { class: "prv" }, h("small", { class: "lbl", text: "Title" }), h("h3", { class: "wrap", text: pv.title }), h("small", { class: "lbl", text: "Body (Markdown)" }), h("pre", { class: "md", text: pv.body }))],
      };
      const showView = (v) => { view = v; put(content, ...views[v]()); };
      const actions = h("div", { class: "actions" });
      const status = h("div");
      const drawActions = () => {
        put(status, S.branch ? note("ok", "Branch ready", S.branch) : null);
        put(actions, 
          S.branch ? null : btn("Create branch", async () => {
            const res = await api("/api/apply", { id });
            await refresh();
            toast(`Branch ${res.branch} · ${res.commits.length} commits`);
            showResult("branch", res);
            drawActions();
          }, { ic: "branch" }),
          btn("Open pull request", async () => {
            if (!S.branch) throw Object.assign(new Error("Create the branch first."), { fix: "Press Create branch, then open the pull request." });
            const res = await api("/api/pr", { id });
            showResult("pr", res);
            toast(res.ok ? (res.method === "demo" ? "Demo: branch ready locally" : "Pull request opened") : "Branch pushed, finish the PR manually", { error: !res.ok && !res.url });
          }, { kind: S.branch ? "primary" : "secondary", ic: "pr", disabled: !S.branch }));
      };
      function showResult(kind, res) {
        resultT.hidden = false;
        if (kind === "branch") {
          const pol = res.policies || {};
          put(resultT, resultT.firstChild, h("div", { class: "res" },
            h("div", { class: "res-h" }, h("span", { class: "big-check" }, icon("check")), h("div", {}, h("h3", { text: "Branch created" }), h("p", { class: "mono wrap", text: res.branch }))),
            h("h4", { text: `${res.commits.length} commit${res.commits.length === 1 ? "" : "s"}` }),
            h("ol", { class: "commits" }, res.commits.map((m) => h("li", {}, icon("branch"), h("span", { class: "wrap", text: m })))),
            (pol.applied || []).length ? h("div", {}, h("h4", { text: "Routing policies in Otari" }), h("div", { class: "chips-row" }, pol.applied.map((a) => chip(a.ok ? a.name : `${a.name}: ${a.error || "failed"}`, a.ok ? "lime" : "bad", a.ok ? "check" : "x")))) : null,
            pol.dashboard_steps ? note("warn", "Create the routing policies in the Otari dashboard", "Steps are in .otari-switch/POLICIES.md. The Policies tab shows each chain.") : null));
        } else {
          put(resultT, resultT.firstChild, h("div", { class: "res" },
            h("div", { class: "res-h" }, h("span", { class: `big-check${res.ok ? "" : " warn"}` }, icon(res.ok ? "check" : "warn")),
              h("div", {}, h("h3", { text: res.method === "demo" ? "Branch ready (demo)" : res.ok ? "Pull request opened" : "Almost there" }),
                h("p", { class: "wrap", text: res.message || (res.ok ? `Opened with ${res.method === "otari-mcp" ? "GitHub's MCP server through Otari" : "the GitHub CLI"}.` : "Open the link to finish the pull request.") }),
                res.note ? h("p", { class: "muted small wrap", text: res.note }) : null,
                res.request_id ? h("p", { class: "muted small mono", text: `Otari request ${res.request_id}` }) : null)),
            res.url ? h("div", { class: "actions" }, h("a", { class: "btn primary", href: res.url, target: "_blank", rel: "noopener" }, icon("external"), h("span", { text: "Open on GitHub" }))) : null));
        }
        resultT.scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "nearest" });
      }
      put(lead, 
        head("Changes", { extra: S.demo ? demoBadge() : null }),
        h("p", { class: "lede", text: "Everything here happens on a new branch: one commit pointing clients at Otari, then one per call site, so reviewers can drop any one." }),
        h("div", { class: "kpis two" },
          kpi("Files changed", pv.files.length, intParts, { sub: `+${adds} −${dels} lines` }),
          kpi("Routing policies", pv.policies.length, intParts, { sub: "One per call site" })),
        h("div", { class: "lead-row" }, seg([["code", "Code"], ["policies", "Policies"], ["pr", "Pull request"]], view, showView, { label: "View" })),
        status, actions);
      drawActions();
      showView("code");
    }).catch((e) => { if (ctx.alive()) put(lead, head("Changes"), errNote(e)); });
    return screen(lead, content, resultT);
  }
  function diffBlock(text) {
    return h("div", { class: "diff", role: "region", "aria-label": "Diff", tabindex: "0" }, text.split("\n").map((l) => h("div", {
      class: l.startsWith("@@") ? "hunk" : l.startsWith("+++") || l.startsWith("---") ? "meta" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : null, text: l || " " })));
  }

  /* ---------- Trend (Otari insights) ---------- */
  function screenTrend(ctx) {
    let days = Number(store.get("trendDays")) || 7;
    if (![1, 7, 30].includes(days)) days = 7;
    const dateEl = h("span", { text: "Loading…" });
    const demoEl = h("span");
    const kpis = h("div", { class: "kpis grid2" }, skelRows(3));
    const lead = tile({ span: 4, wide: true, cls: "lead" },
      head("Trend", { date: dateEl, extra: demoEl }),
      h("div", { class: "lead-row" }, seg([[1, "24h"], [7, "7d"], [30, "30d"]], days, (v) => { days = Number(v); store.set("trendDays", String(days)); load(); }, { label: "Range", lime: true })),
      kpis);
    const lineT = tile({ span: 8, wide: true, cls: "chart-tile", title: "Cost and requests", right: h("div", { class: "legend" }, h("span", {}, h("i", { class: "lime" }), "Cost"), h("span", {}, h("i", { class: "lav" }), "Requests")) }, skelRows(4));
    const budgetT = tile({ span: 4, title: "Key budget", sub: "From Otari's /key/budget" }, skelRows(3));
    const barsT = tile({ span: 4, title: "Requests", sub: "Per bucket" }, skelRows(3));
    const roleT = tile({ span: 4, title: "Spend by role", sub: "Tagged by Otari Switch" }, skelRows(3));
    const modelT = tile({ span: 8, wide: true, title: "Spend by model" }, skelRows(3));
    const provT = tile({ span: 4, title: "By provider" }, skelRows(2));
    let loadSeq = 0;
    async function load() {
      const my = ++loadSeq;
      [lineT, budgetT, barsT, roleT, modelT, provT].forEach((t) => { t.hidden = false; put(t, t.firstChild, skelRows(3)); });
      put(kpis, skelRows(3));
      let ins;
      try { ins = await api(`/api/insights?days=${days}`); } catch (e) {
        if (!ctx.alive() || my !== loadSeq) return;
        put(kpis, errNote(e));
        [lineT, budgetT, barsT, roleT, modelT, provT].forEach((t) => put(t, t.firstChild, h("p", { class: "muted", text: "Not available." })));
        return;
      }
      if (!ctx.alive() || my !== loadSeq) return;
      drawTrend(ins);
    }
    function drawTrend(ins) {
      put(demoEl, ins.source === "demo" ? demoBadge() : ins.source === "otari" ? chip("Live from Otari", "lime") : chip("Offline", "gray"));
      const errs = ins.errors || {};
      if (ins.source === "offline" || errs.key) {
        dateEl.textContent = "Not connected";
        put(kpis, note("info", "Not connected", errs.key || "Usage and budget come from Otari for your key."));
        put(lineT, lineT.firstChild, emptyState("budget", "Connect Otari to see usage", "Spend, requests, latency and your key's budget come straight from Otari for the key you connect. Nothing here is estimated.", linkBtn("Connect Otari", "#/connect", { ic: "plug" })));
        [budgetT, barsT, roleT, modelT, provT].forEach((t) => { t.hidden = true; });
        return;
      }
      const u = ins.usage;
      dateEl.textContent = u ? dateRange(u.start_date, u.end_date) : `Last ${days === 1 ? "24 hours" : `${days} days`}`;
      // budget
      drawBudget(budgetT, ins.budget, errs.budget);
      if (!u) {
        const msg = errs.usage ? `Otari did not return usage: ${errs.usage}` : "Otari returned no usage for this key.";
        put(kpis, emptyState("budget", "No usage data", msg));
        [lineT, barsT, roleT, modelT, provT].forEach((t) => put(t, t.firstChild, h("p", { class: "muted", text: msg })));
        return;
      }
      const tt = u.totals || {};
      const errRate = tt.request_count ? (tt.error_count || 0) / tt.request_count : null;
      put(kpis, 
        kpi("Spend", tt.cost ?? 0, spendParts, { sub: `Last ${days === 1 ? "24 hours" : `${days} days`}`, cls: "wide" }),
        kpi("Requests", tt.request_count ?? 0, intParts, { chip: errRate != null ? chip(`${pct(errRate)} errors`, errRate > 0.05 ? "lav" : "lime") : null }),
        kpi("Avg latency", tt.avg_latency_ms, msParts, { empty: "n/a" }),
        kpi("Tokens", tt.total_tokens ?? 0, compactParts, { sub: tt.prompt_tokens != null ? `${compact(tt.prompt_tokens)} in · ${compact(tt.completion_tokens || 0)} out` : null }));
      const pts = fillSeries(u, ins.bucket || u.bucket || "day");
      if (!pts.length || !pts.some((p) => p.a > 0 || p.b > 0)) {
        put(lineT, lineT.firstChild, emptyState("budget", "No traffic in this range", "Requests made with this key will show up here."));
        put(barsT, barsT.firstChild, h("p", { class: "muted", text: "No requests in this range." }));
      } else {
        put(lineT, lineT.firstChild, lineChart(pts, { bucket: ins.bucket || u.bucket || "day", aName: "Cost", bName: "Requests",
          aFmt: (v) => money(v), bFmt: (v) => `${nf(v)} req`, aTick: (v) => money(v, v < 0.1 && v > 0 ? 3 : 2), bTick: (v) => compact(v) }));
        const bucket = ins.bucket || u.bucket || "day";
        put(barsT, barsT.firstChild, pillBars(pts.map((p) => ({ label: bucket === "hour" ? `${dShort(p.t)} ${tShort(p.t)}` : dShort(p.t), short: bucket === "hour" ? tShort(p.t).slice(0, 2) : String(p.t.getUTCDate()), value: p.b })),
          { fmt: (v) => nf(v), height: 150, maxLabels: 8 }));
      }
      // by role
      const roles = (u.by_role || []).slice().sort((a, b) => (b.cost || 0) - (a.cost || 0));
      const ROLE = { candidate: "Candidates", judge: "Judge", sandbox: "Sandbox checks", summary: "Summaries", frontier: "Today's models (price check)" };
      const totalCost = sum(roles.map((r) => r.cost || 0)) || 1;
      put(roleT, roleT.firstChild, roles.length ? h("div", { class: "list" }, roles.map((r, i) => {
        const name = r.key == null ? "Other traffic on this key" : ROLE[r.key] || r.key;
        return h("div", { class: "row" }, h("div", { class: "t" }, h("b", { text: name }), h("small", { text: `${nf(r.requests || 0)} requests · ${compact(r.tokens || 0)} tokens` }), hbar((r.cost || 0) / totalCost, r.key == null ? "gray" : i % 2 ? "lav" : "lime")),
          h("b", { class: "num-sm", text: money(r.cost || 0) }));
      })) : h("p", { class: "muted", text: "No role tags in this range." }));
      const byModel = (u.by_model || []).slice().sort((a, b) => (b.cost || 0) - (a.cost || 0));
      const mTotal = sum(byModel.map((r) => r.cost || 0)) || 1;
      put(modelT, modelT.firstChild, byModel.length ? h("div", { class: "list" }, byModel.slice(0, 12).map((r, i) =>
        h("div", { class: "row" }, badge(r.label || r.key || "?"), h("div", { class: "t" }, h("b", { class: "wrap", text: r.label || r.key || "unknown" }),
          h("small", { text: `${nf(r.requests || 0)} requests · ${compact(r.tokens || 0)} tokens · ${pct((r.cost || 0) / mTotal)} of spend` }), hbar((r.cost || 0) / mTotal, ["lime", "lav", "gray"][i % 3])),
        h("b", { class: "num-sm", text: money(r.cost || 0) }))))
        : h("p", { class: "muted", text: "No model usage in this range." }));
      const byProv = (u.by_provider || []).slice().sort((a, b) => (b.cost || 0) - (a.cost || 0));
      put(provT, provT.firstChild, byProv.length ? h("div", {}, h("div", { class: "pstack" }, badgeStack(byProv.map((r) => r.label || r.key || "?"), 5)),
        h("div", { class: "list compact" }, byProv.map((r) => h("div", { class: "row" }, h("div", { class: "t" }, h("b", { text: r.label || r.key || "unknown" }), h("small", { text: `${nf(r.requests || 0)} requests` })), h("b", { class: "num-sm", text: money(r.cost || 0) })))))
        : h("p", { class: "muted", text: "No provider usage in this range." }));
    }
    load();
    return screen(lead, lineT, budgetT, barsT, roleT, provT, modelT);
  }

  /** Fill missing buckets with zeros so the x axis is honest about time. */
  function fillSeries(u, bucket) {
    const ser = (u.series || []).map((p) => ({ t: toDate(p.bucket_start), a: Number(p.cost) || 0, b: Number(p.requests) || 0 })).filter((p) => p.t);
    if (!ser.length) return [];
    ser.sort((x, y) => x.t - y.t);
    const stepMs = bucket === "hour" ? 3600e3 : 86400e3;
    const byT = new Map(ser.map((p) => [p.t.getTime(), p]));
    const floor = (d) => { const x = new Date(d); if (bucket === "hour") x.setUTCMinutes(0, 0, 0); else x.setUTCHours(0, 0, 0, 0); return x.getTime(); };
    const startD = toDate(u.start_date), endD = toDate(u.end_date);
    let t0 = startD ? Math.min(floor(startD), ser[0].t.getTime()) : ser[0].t.getTime();
    const t1 = endD ? Math.max(floor(endD), ser[ser.length - 1].t.getTime()) : ser[ser.length - 1].t.getTime();
    // only align when the API's buckets sit on UTC boundaries; otherwise keep its own points
    if (!ser.every((p) => p.t.getTime() === floor(p.t))) return ser;
    const out = [];
    for (let t = t0; t <= t1 && out.length < 800; t += stepMs) out.push(byT.get(t) || { t: new Date(t), a: 0, b: 0 });
    return out;
  }

  function drawBudget(t, b, err) {
    if (!b) { put(t, t.firstChild, emptyState("budget", "No budget data", err ? `Otari did not return the key budget: ${err}` : "Otari returned no budget for this key.")); return; }
    const limits = b.limits || [];
    const usd = limits.filter((l) => l.limit_usd != null || l.remaining_usd != null).sort((x, y) => (x.remaining_usd ?? Infinity) - (y.remaining_usd ?? Infinity));
    const prim = usd[0];
    let ring;
    if (b.remaining_usd == null) {
      ring = h("div", { class: "budget-ring" }, donut([], { size: 180, stroke: 22, label: "No dollar cap" }), h("div", { class: "br-c" }, h("b", { text: "No dollar cap" }), h("small", { text: b.budgets_enforced ? "Other limits may apply" : "Budgets not enforced" })));
    } else if (prim && prim.limit_usd != null && prim.used_usd != null && prim.limit_usd > 0) {
      ring = h("div", { class: "budget-ring" }, donut([{ value: Math.min(prim.used_usd, prim.limit_usd), color: "#D5F26B" }, { value: Math.max(0, prim.limit_usd - prim.used_usd), color: "#BBAEF6" }], { size: 180, stroke: 22, label: `${money(prim.used_usd)} used of ${money(prim.limit_usd)}` }),
        h("div", { class: "br-c" }, h("b", { text: money(b.remaining_usd) }), h("small", { text: `left of ${money(prim.limit_usd)}` })));
    } else {
      ring = h("div", { class: "budget-ring" }, donut([{ value: 1, color: "#BBAEF6" }], { size: 180, stroke: 22, label: `${money(b.remaining_usd)} remaining` }),
        h("div", { class: "br-c" }, h("b", { text: money(b.remaining_usd) }), h("small", { text: "remaining" })));
    }
    put(t, t.firstChild, ring,
      prim && prim.limit_usd != null && prim.used_usd != null ? h("div", { class: "legend center" }, h("span", {}, h("i", { class: "lime" }), "Used ", h("b", { text: money(prim.used_usd) })), h("span", {}, h("i", { class: "lav" }), "Left ", h("b", { text: money(Math.max(0, prim.limit_usd - prim.used_usd)) }))) : null,
      limits.length ? h("div", { class: "list compact" }, limits.map((l) => {
        const what = [l.scope, l.provider].filter(Boolean).join(" · ") || "limit";
        const parts = [];
        if (l.limit_usd != null) parts.push(`${money(l.used_usd ?? 0)} of ${money(l.limit_usd)}`); else if (l.remaining_usd != null) parts.push(`${money(l.remaining_usd)} left`);
        if (l.limit_tokens != null) parts.push(`${compact(l.used_tokens ?? 0)} of ${compact(l.limit_tokens)} tokens`); else if (l.remaining_tokens != null) parts.push(`${compact(l.remaining_tokens)} tokens left`);
        if (l.limit_requests != null) parts.push(`${nf(l.used_requests ?? 0)} of ${nf(l.limit_requests)} requests`); else if (l.remaining_requests != null) parts.push(`${nf(l.remaining_requests)} requests left`);
        const resets = toDate(l.resets_at);
        return h("div", { class: "row" }, h("span", { class: "row-ic" }, icon("gauge")), h("div", { class: "t" }, h("b", { text: what }), h("small", { text: `${parts.join(" · ") || "no amounts reported"}${resets ? ` · resets ${dShort(resets)}` : ""}` })));
      })) : h("p", { class: "muted small center", text: b.budgets_enforced ? "No limits listed for this key." : "Otari does not enforce budgets for this key." }));
  }

  /* ---------- Runs ---------- */
  function screenRuns(ctx) {
    const lead = tile({ span: 5, wide: true, cls: "lead" }, head("Runs"), skelRows(3));
    const histT = tile({ span: 7, wide: true, title: "Open-weight share per run", sub: "Oldest to newest" }, skelRows(3));
    const listT = tile({ span: 12, wide: true, title: "All runs" }, skelRows(4));
    loadRuns().then((runs) => {
      if (!ctx.alive()) return;
      if (!runs.length) {
        put(lead, head("Runs", { date: "No runs yet" }), emptyState("verdict", "No runs yet", "Run an evaluation from the Switch flow. Every run is kept here.", linkBtn("Start the flow", "#/flow", { ic: "arrowRight" })));
        histT.hidden = true; listT.hidden = true;
        return;
      }
      const spend = sum(runs.map((r) => r.spend_usd));
      put(lead, 
        head("Runs", { date: dateRange(runs[runs.length - 1].started, runs[0].started) }),
        h("div", { class: "kpis" },
          kpi("Evaluations", runs.length, intParts, { chip: runs.some((r) => r.demo) ? chip(`${runs.filter((r) => r.demo).length} demo`, "lav") : null }),
          kpi("Total spend", spend, spendParts, { sub: "Evaluation spend across runs" }),
          kpi("Latest open-weight share", runs[0].summary.open_share, pctParts, { sub: `${runs[0].summary.moved} of ${runs[0].summary.evaluated} · ${rel(runs[0].started)}`, chip: runs[1] ? deltaChip(runs[0].summary.open_share, runs[1].summary.open_share) : null })));
      const chron = runs.slice(0, 16).reverse();
      put(histT, histT.firstChild, pillBars(chron.map((r, i) => ({ label: `${dShort(new Date(r.started))} ${tShort(new Date(r.started))}`, short: dShort(new Date(r.started)), value: r.summary.open_share, tone: i === chron.length - 1 ? "lime" : i % 2 ? "gray" : "lav" })), { fmt: pct, height: 160, maxLabels: 6 }));
      put(listT, listT.firstChild, runList(runs));
    }).catch((e) => { if (ctx.alive()) put(lead, head("Runs"), errNote(e)); });
    return screen(lead, histT, listT);
  }

  /* ---------- Settings ---------- */
  function screenSettings() {
    const c = S.config;
    const save = (body, msg) => api("/api/config", body).then(refresh).then(() => msg && toast(msg)).catch(fail);
    const slider = (label, key, min, max, step, fmt, help) => {
      const v = h("b", { class: "num-sm", text: fmt(c.thresholds[key]) });
      const r = h("input", { type: "range", min, max, step, value: String(c.thresholds[key]), "aria-label": label });
      const setFill = () => r.style.setProperty("--p", `${((Number(r.value) - min) / (max - min)) * 100}%`);
      r.addEventListener("input", () => { v.textContent = fmt(Number(r.value)); setFill(); });
      r.addEventListener("change", () => save({ thresholds: { [key]: Number(r.value) } }, `${label}: ${fmt(Number(r.value))}`));
      setFill();
      return h("div", { class: "srow" }, h("div", { class: "srow-h" }, h("div", {}, h("b", { text: label }), h("small", { class: "muted", text: help })), v), r);
    };
    const monthly = h("input", { class: "field num-in", type: "number", min: "0", step: "1000", value: String(c.monthly_calls), "aria-label": "AI calls a month" });
    monthly.addEventListener("change", () => save({ monthly_calls: Math.max(0, Number(monthly.value) || 0) }, "Monthly calls saved"));
    const tcmd = h("input", { class: "field mono-in", value: c.tests_command, spellcheck: "false", "aria-label": "Test command" });
    tcmd.addEventListener("change", () => save({ tests_command: tcmd.value }, "Test command saved"));
    const doctor = h("div", {}, h("p", { class: "muted", text: "Checks Python, the key, the region, your models, the SDKs, git and the GitHub MCP server, and says how to fix anything missing." }));
    const runDoctor = btn("Run setup check", async () => {
      put(doctor, skelRows(5));
      const { checks } = await api("/api/doctor");
      const okN = checks.filter((k) => k.ok === true).length, bad = checks.filter((k) => k.ok === false).length;
      put(doctor, 
        h("div", { class: "chips-row" }, chip(`${okN} passed`, "lime", "check"), bad ? chip(`${bad} to fix`, "bad", "x") : null, chip(`${checks.length - okN - bad} optional`, "gray")),
        h("div", { class: "list" }, checks.map((k) => h("div", { class: "row col doc-row" },
          h("div", { class: "doc-h" }, h("span", { class: `st ${k.ok === true ? "ok" : k.ok === false ? "no" : "na"}` }, icon(k.ok === true ? "check" : k.ok === false ? "x" : "dash")), h("b", { text: k.name }), h("small", { class: "muted wrap", text: k.detail || "" })),
          k.ok === false && k.fix ? h("div", { class: "fix" }, h("span", { class: "lbl", text: "Fix" }), h("code", { class: "wrap", text: k.fix }), copyBtn(k.fix, "fix")) : null))));
    }, { kind: "secondary", ic: "refresh" });
    const priv = h("p", { class: "hint" });
    const privText = { mask: "Mask: emails, keys, card and phone numbers become placeholders before replay.", skip: "Skip: prompts with private values are left out.", off: "Off: prompts are replayed as recorded." };
    put(priv, icon("shield"), h("span", { text: privText[c.privacy_mode] || "" }));
    return screen(
      tile({ span: 5, wide: true, cls: "lead" },
        head("Settings", { extra: S.demo ? demoBadge() : null }),
        h("div", { class: "list" },
          h("a", { class: "row link-row", href: "#/connect" }, h("span", { class: "row-ic lime" }, icon("plug")),
            h("div", { class: "t" }, h("b", { text: "Otari connection" }), h("small", { class: "wrap", text: c.key_set ? `${c.region === "custom" ? "Custom URL" : String(c.region).toUpperCase()} · ${c.base_url}` : "Not connected" })), icon("chevRight", "chev")),
          h("a", { class: "row link-row", href: "#/models" }, h("span", { class: "row-ic" }, icon("judge")),
            h("div", { class: "t" }, h("b", { text: "Models and budget" }), h("small", { text: c.candidates.length ? `${c.candidates.length} candidates · judge ${c.judge_model || "not set"} · cap ${money(c.budget_usd)}` : "Not chosen yet" })), icon("chevRight", "chev")))),
      tile({ span: 7, wide: true, title: "Evaluation" },
        h("div", { class: "form" },
          h("label", { class: "field-row" }, h("span", { class: "lbl", text: "AI calls a month" }), monthly, h("small", { class: "muted", text: "Used only for the savings estimate." })),
          h("label", { class: "field-row" }, h("span", { class: "lbl", text: "Test command" }), tcmd),
          h("div", { class: "field-row" }, h("span", { class: "lbl", text: "Privacy" }), seg([["mask", "Mask"], ["skip", "Skip"], ["off", "Off"]], c.privacy_mode, (v) => {
            put(priv, icon("shield"), h("span", { text: privText[v] })); save({ privacy_mode: v }, `Privacy: ${v}`);
          }, { label: "Privacy mode" }), priv))),
      tile({ span: 7, wide: true, title: "Verdict thresholds", sub: "Scores are 1 to 5 against today's answers." },
        slider("Switch: mean score", "switch_mean", 3, 5, 0.1, (x) => x.toFixed(1), "Minimum mean judge score"),
        slider("Switch: usable answers", "switch_min_share", 0.5, 1, 0.05, pct, "Share of answers scoring 3 or more"),
        slider("Fallback: mean score", "fallback_mean", 2.5, 5, 0.1, (x) => x.toFixed(1), "Minimum for switch with fallback"),
        slider("Fallback: format checks", "fallback_check_rate", 0.5, 1, 0.05, pct, "JSON, schemas, tool calls and labels")),
      tile({ span: 5, wide: true, title: "Setup check", right: runDoctor }, doctor),
      tile({ span: 12, wide: true, title: "About" },
        h("div", { class: "facts" }, fact("Version", `otari-switch ${S.version}`), fact("License", "Apache 2.0"), fact("Built on", "Otari by Mozilla.ai"), fact("Typeface", "Inter (SIL Open Font License)"), fact("Repository", S.root, "full"))));
  }

  /* ================================================================ boot */
  buildTopbar();
  buildDock();
  document.querySelector(".skip")?.addEventListener("click", () => main.focus());
  render();
})();
