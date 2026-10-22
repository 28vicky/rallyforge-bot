/* Copyright (c) 2026 Kiravik. All rights reserved. RallyForge Bot was created by Kiravik. */
/* RallyForge Bot - interface core: helpers, layout, live updates, theme. */
"use strict";

// ------------------------------------------------------------------ tiny helpers
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value" || k === "checked" || k === "disabled" || k === "readOnly" || k === "selected") e[k] = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  const add = (c) => {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) c.forEach(add);
    else e.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  };
  kids.forEach(add);
  return e;
}
const $ = (s, r) => (r || document).querySelector(s);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const short = (s) => (s.includes(":") ? s.split(":").pop() : s);
const clone = (o) => JSON.parse(JSON.stringify(o));

async function api(name, body, opts) {
  opts = opts || {};
  let j;
  try {
    const r = await fetch("/api/" + name, {
      method: "POST", headers: { "X-Key": window.KEY, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    j = await r.json();
  } catch (e) {
    if (!opts.quiet) toast("Lost contact with the app. Is it still running?", "error");
    throw e;
  }
  if (!j.ok) {
    if (!opts.quiet) toast(j.error || "Something went wrong.", "error");
    throw new Error(j.error || "error");
  }
  return j;
}
const act = (p) => p.catch(() => null);          // swallow: the error was already shown as a toast

function toast(msg, kind) {
  const t = h("div", { class: "toast " + (kind || "") }, msg);
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 3800);
}

// ------------------------------------------------------------------ state
const S = {
  cfg: {}, devices: [], generals: [], monsters: [], profiles: [], cityInfo: [], bosses: [], meta: {},
  devStatus: [], logs: [], seq: 0, sel: "control", tab: (() => { try { return localStorage.getItem("rf_ctl_tab") || "dashboard"; } catch (e) { return "dashboard"; } })(), tg: { status: "", ready: false },
  ram: {}, genScan: {}, city: false, version: "",
};
// ------------------------------------------------------------------ icon set (one outline style: 24 grid, 1.8 stroke, currentColor)
const ICO = {
  bolt: '<path d="M13 3 4 14h7l-1 7 9-11h-7l1-7z"/>',
  play: '<path d="M8 5v14l11-7z"/>', pause: '<path d="M9 5v14M15 5v14"/>', stop: '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
  chev: '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>',
  users: '<circle cx="9" cy="8.5" r="3.4"/><path d="M2.8 20c0-3.5 2.8-5.8 6.2-5.8s6.2 2.3 6.2 5.8M16 5.3a3.4 3.4 0 0 1 0 6.4M18.3 14.6c1.8.8 2.9 2.6 2.9 5.4"/>',
  scan: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  city: '<path d="M3 21h18M5 21V10l5-3v14M10 21V4l9 3v14M13.5 10h2.5M13.5 14h2.5M7 13h1.5M7 17h1.5"/>',
  pin: '<path d="M12 21s7-6 7-11.5a7 7 0 0 0-14 0C5 15 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
  user: '<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20.5c0-4 3.4-6.3 7.5-6.3s7.5 2.3 7.5 6.3"/>',
  file: '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
  down: '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
  save: '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
  more: '<circle cx="5.5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18.5" cy="12" r="1.2"/>',
  grid: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5zM3 12.5l9 5 9-5M3 16.5l9 5 9-5"/>',
  sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  sword: '<path d="M14 5l6-1-1 6L9 20l-5-5zM4 20l4-4M12 12l-3 3"/>',
  list: '<path d="M10 6h10M10 12h10M10 18h10M3.5 6l1.4 1.4L7 5M3.5 12l1.4 1.4L7 11M3.5 18l1.4 1.4L7 17"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5.2l3.2 2"/>',
  bag: '<path d="M5 8.5h14l-1 11.5H6zM9 8.5V7a3 3 0 0 1 6 0v1.5"/>',
  send: '<path d="M21 4 3 11l7 2.5L12.5 21zM10 13.5 21 4"/>',
  shield: '<path d="M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6z"/><path d="m9 12 2.2 2.2L15 10"/>',
  bell: '<path d="M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 20.5a2 2 0 0 0 4 0"/>',
  palette: '<path d="M12 3a9 9 0 1 0 0 18c1.6 0 2.2-1.2 1.5-2.300-.6-1 0-2.200 1.300-2.200H17a4 4 0 0 0 4-4c0-5-4-9.500-9-9.500z"/><circle cx="8" cy="11" r="1"/><circle cx="12" cy="7.500" r="1"/><circle cx="16" cy="11" r="1"/>',
  key: '<circle cx="8" cy="15.500" r="4"/><path d="m11 12.500 9-9M17 6.500l3 3"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.500-4M4 5v3.500h3.500M4 13a8 8 0 0 0 14.500 4M20 19v-3.500h-3.500"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.500h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  power: '<path d="M12 3v8M6.500 6.500a8 8 0 1 0 11 0"/>',
  eye: '<path d="M2.500 12S6 5.500 12 5.500 21.500 12 21.500 12 18 18.500 12 18.500 2.500 12 2.500 12z"/><circle cx="12" cy="12" r="2.800"/>',
  egg: '<path d="M12 3C8 3 5 10 5 14.500a7 7 0 0 0 14 0C19 10 16 3 12 3z"/>',
};
function ico(name, cls) {
  const e = h("span", { class: "ico" + (cls ? " " + cls : "") });
  e.innerHTML = '<svg viewBox="0 0 24 24">' + (ICO[name] || "") + "</svg>";
  return e;
}
const TAB_ICO = { dashboard: "grid", presets: "layers", settings: "sliders", monsters: "sword", tasks: "list", duties: "clock", market: "bag", logs: "file",
  telegram: "send", reliability: "shield", schedule: "bell", updates: "down", themes: "palette", license: "key" };

const TABS = [
  ["dashboard", "Dashboard"], ["presets", "Presets & generals"], ["settings", "Settings"], ["monsters", "Monsters"],
  ["tasks", "Tasks"], ["duties", "Duties"], ["market", "Market"], ["logs", "Logs"], ["telegram", "Telegram & Discord"], ["reliability", "Reliability"],
  ["schedule", "Schedule & alerts"], ["updates", "Updates"], ["themes", "Themes"], ["license", "Account"],
];
const STATE_TXT = { idle: "Not started", run: "Running", pause: "Paused", stop: "Stopped", error: "Problem - see the log" };

// ------------------------------------------------------------------ theme
const DARK_ON = false;                            // dark mode hidden for now (owner 21 Oct); the saved choice (ui_mode) is kept, only the light look is shown
function resolvedMode() {
  if (!DARK_ON) return "light";
  const m = S.cfg.ui_mode || "light";
  return m === "auto" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : m;
}
const ACCENTS = [["#d9a566", "Amber"], ["#e0c25a", "Gold"], ["#e07a5a", "Coral"], ["#e58a95", "Rose"], ["#a58be0", "Violet"], ["#7aa7e0", "Sky"], ["#6fb5a6", "Sage"], ["#8fc46a", "Lime"]];
const CUSTOM_VARS = ["--accent", "--accent1", "--accent2", "--accfg", "--bd", "--btn", "--r1", "--r2", "--rad", "--glow"];
function hexRgb(hx) { const n = parseInt(hx.replace("#", ""), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function rgbHex(r) { return "#" + r.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join(""); }
function mix(hx, to, t) { const a = hexRgb(hx), b = hexRgb(to); return rgbHex(a.map((v, i) => v + (b[i] - v) * t)); }
function lum(hx) { const [r, g, b] = hexRgb(hx); return (0.299 * r + 0.587 * g + 0.114 * b) / 255; }
// accent colour, button style, border and corner roundness on top of the chosen theme
function applyCustom() {
  const root = document.documentElement, st = root.style;
  CUSTOM_VARS.forEach((v) => st.removeProperty(v));
  const c = S.cfg || {}, light = resolvedMode() === "light";
  const hx = /^#[0-9a-f]{6}$/i.test(c.ui_accent || "") ? c.ui_accent : "";
  const cs = getComputedStyle(root);
  if (hx) {
    const a1 = light ? mix(hx, "#000000", 0.38) : hx;
    st.setProperty("--accent1", a1);
    st.setProperty("--accent2", light ? mix(hx, "#000000", 0.45) : mix(hx, "#ffffff", 0.22));
    st.setProperty("--accent", `linear-gradient(135deg, ${mix(hx, "#ffffff", 0.06)}, ${mix(hx, "#000000", 0.16)})`);
    st.setProperty("--accfg", lum(mix(hx, "#000000", 0.08)) > 0.52 ? "#14161d" : "#ffffff");
  }
  const a = hx || (cs.getPropertyValue("--accent1").trim().startsWith("#") ? cs.getPropertyValue("--accent1").trim() : "#d9a566");
  const [ar, ag, ab] = hexRgb(a);
  if (c.ui_border === "accent") st.setProperty("--bd", `1px solid rgba(${ar},${ag},${ab},${light ? 0.55 : 0.38})`);
  if (c.ui_btn === "tinted") {
    const base = cs.getPropertyValue("--btn").trim();
    st.setProperty("--btn", `linear-gradient(rgba(${ar},${ag},${ab},${light ? 0.2 : 0.17}),rgba(${ar},${ag},${ab},${light ? 0.2 : 0.17})), ${base}`);
  }
  const tz = Math.max(80, Math.min(200, parseInt(c.ui_text, 10) || 100));
  try { root.style.zoom = tz === 100 ? "" : String(tz / 100); } catch (e) {}
  const R = { sharp: [6, 4, 8], round: [28, 18, 28] }[c.ui_round];
  if (R) { st.setProperty("--r1", R[0] + "px"); st.setProperty("--r2", R[1] + "px"); st.setProperty("--rad", R[2] + "px"); }
}
const PALS_ON = ["ivory"];                       // Midnight, Amber and Sage are kept in the code but hidden for now
const curPal = () => (PALS_ON.includes(S.cfg.ui_pal) ? S.cfg.ui_pal : "ivory");
function applyTheme() {
  document.documentElement.dataset.pal = curPal();
  document.documentElement.dataset.mode = resolvedMode();
  try { localStorage.setItem("rf_theme", JSON.stringify({ pal: curPal(), mode: S.cfg.ui_mode || "light" })); } catch (e) {}
  applyCustom();
}
function setLook(o) {
  Object.assign(S.cfg, o);
  applyTheme();
  act(api("save", { cfg: o }, { quiet: true }));
}
function setTheme(pal, mode, save) {
  if (pal) S.cfg.ui_pal = pal;
  if (mode) S.cfg.ui_mode = mode;
  applyTheme();
  if (save !== false) act(api("save", { cfg: { ui_pal: S.cfg.ui_pal, ui_mode: S.cfg.ui_mode } }, { quiet: true }));
}
matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => { if (S.cfg.ui_mode === "auto") applyTheme(); });

// ------------------------------------------------------------------ dropdown menu
function menu(anchor, items) {
  document.querySelectorAll(".pop").forEach((p) => p.remove());
  const r = anchor.getBoundingClientRect();
  const m = h("div", { class: "pop", style: { position: "fixed", left: r.left + "px", top: r.bottom + 6 + "px", zIndex: 70,
    background: "var(--card)", border: "1px solid var(--line)", borderRadius: "12px", boxShadow: "0 10px 30px rgba(0,0,0,.12)", padding: "6px", minWidth: "210px" } },
    items.map((it) => h("div", { class: "li", onclick: () => { m.remove(); it.run(); } }, it.icon ? ico(it.icon) : null, it.label)));
  document.body.appendChild(m);
  // keep the whole menu inside the window: a button near the right edge opens it leftwards, near the bottom upwards
  const mr = m.getBoundingClientRect();
  if (mr.right > innerWidth - 8) m.style.left = Math.max(8, Math.min(r.right, innerWidth - 8) - mr.width) + "px";
  if (mr.bottom > innerHeight - 8) m.style.top = Math.max(8, r.top - 6 - mr.height) + "px";
  const off = (e) => { if (!m.contains(e.target)) { m.remove(); document.removeEventListener("mousedown", off, true); } };
  setTimeout(() => document.addEventListener("mousedown", off, true), 0);
}

// ------------------------------------------------------------------ layout
let actViews = [];                               // Activity screens on view
let logViews = [];                               // {el, serial|null} : every log box currently on screen

function layout() {
  const root = $("#root");
  root.innerHTML = "";
  root.appendChild(h("div", { class: "app" + (S.cfg.sidebar_open === false ? " nosb" : ""), id: "app" },
    h("div", { class: "side" },
      h("div", { class: "brand" }, h("img", { src: "/assets/logo.png", alt: "" }),
        h("div", { class: "bt" }, h("b", {}, "RallyForge"), h("small", {}, "Automation Hub")),
        h("button", { class: "vpill", type: "button", title: "What changed in this version", onclick: () => openWhatsNew() }, "v" + S.version)),
      h("h4", {}, h("span", {}, "Instances"), h("span", { class: "cnt", id: "instCnt" })),
      h("div", { class: "inst-list", id: "instList" }),
      h("div", { class: "sidefoot" }, h("div", { class: "whoami", id: "whoami" }),
        h("div", { class: "sflab" }, "All instances"),
        h("div", { class: "allbtns" },
          h("button", { class: "btn olpause", id: "sidePause", type: "button", title: "Pause every running instance", onclick: pauseOrResumeAll }, "Pause all"),
          h("button", { class: "btn olstop", type: "button", title: "Stop every instance", onclick: () => act(api("stop", { serial: "all" })) }, "Stop all")),
        h("button", { class: "btn ib ctlbtn", id: "ctlBtn", title: "Settings and tools for all instances", onclick: () => { S.sel = "control"; renderSide(); renderTabs(); renderPage(); } }, ico("sliders"), "Control settings"))),
    h("div", { class: "main" },
      h("div", { class: "top" }, sideBtn(), h("img", { src: "/assets/logo.png", alt: "" }),
        h("b", {}, "RallyForge Bot"), h("span", { class: "v", title: "Created by " + (S.author || "Kiravik") }, "v" + S.version),
        h("button", { class: "btn sm newbtn", id: "newBtn", type: "button", title: "What changed in this version", onclick: () => openWhatsNew() }, "What\u2019s new"),
        h("span", { class: "sum", id: "sum" }),
        h("div", { class: "iconbtn", id: "modeBtn", title: "Dark / light", onclick: quickMode })),
      h("div", { class: "ribbon", id: "ribbon" }),
      h("div", { class: "tabs", id: "tabs" }),
      h("div", { class: "pages", id: "pages" })),
    // status bar: live facts on the left (running, memory, Telegram, account), update + credit on the right
    h("div", { class: "status" },
      h("div", { class: "sbl" }, h("span", { class: "sbrun" }, h("span", { class: "sbdot", id: "sbDot" }), h("span", { id: "sbRun" }, "0 running")),
        h("span", { id: "sbRam" }), h("span", { id: "sbTg" }), h("span", { id: "sbLic" })),
      h("div", { class: "sbr" }, h("span", { id: "sbUpd", class: "sb-upd", style: { display: "none" } }),
        h("span", { class: "l", title: S.copyright || "" }, "v" + S.version + " \u00b7 by " + (S.author || "Kiravik"))))));
  buildRibbon();
  paintRunState();
  renderModeIcon();
  renderSide();
  renderTabs();
  renderPage();
}

// the sidebar's show/hide button: top-left of the page header, before the instance name (and on Control settings' top bar)
function sideBtn() {
  return h("button", { class: "sidebtn", type: "button", title: "Hide / show the instance list (Ctrl+B)", "aria-label": "Hide or show the instance list", onclick: toggleSide },
    h("i"), h("i"), h("i"));
}
function toggleSide() {
  $("#app").classList.toggle("nosb");
  S.cfg.sidebar_open = !$("#app").classList.contains("nosb");
  act(api("save", { cfg: { sidebar_open: S.cfg.sidebar_open } }, { quiet: true }));
}
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey && e.key.toLowerCase() === "b") || e.key === "F9") { e.preventDefault(); toggleSide(); }
});

function renderModeIcon() {
  const dark = resolvedMode() === "dark";
  const b = $("#modeBtn");
  if (!b) return;
  b.innerHTML = dark
    ? '<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
    : '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
}
function quickMode() {
  setTheme(null, resolvedMode() === "dark" ? "light" : "dark");
  renderModeIcon();
  if (S.sel === "control" && S.tab === "settings") renderPage();
}

// ------------------------------------------------------------------ ribbon
function target() { return S.sel === "control" ? "all" : S.sel; }
async function collectAndRun(name, extra) {
  return act(api(name, Object.assign({ cfg: S.cfg, devices: S.devices }, extra || {})));
}
function buildRibbon() {
  const r = $("#ribbon");
  r.innerHTML = "";
  const btn = (txt, fn, cls, icon) => {
    const caret = txt.endsWith(" \u25be");
    const run = ["Start", "Pause", "Stop"].includes(txt);        // these act on all instances when Control settings is open
    return h("button", { class: "btn ib " + (cls || ""), onclick: fn, "data-act": run ? txt.toLowerCase() : null }, icon ? ico(icon) : null,
      run ? h("span", { class: "runlbl", "data-base": txt }, txt) : (caret ? txt.slice(0, -2) : txt), caret ? ico("chev", "caret") : null);
  };
  const grp = (cap, ...kids) => h("div", { class: "grp" }, h("div", { class: "row" }, kids), h("div", { class: "cap" }, cap));
  r.append(
    grp("Run",
      btn("Start", () => { if (S.sel === "control" && allPaused()) return act(api("resume")); if (S.sel === "control") { S.startingAt = Date.now(); paintRunState(); } collectAndRun("start", { serial: target() }); }, "pri", "play"),
      btn("Pause", () => act(api("pause", { serial: target() })), "", "pause"),
      btn("Stop", () => { S.startingAt = 0; act(api("stop", { serial: target() })); }, "bad", "stop"), h("span", { class: "ribcount", id: "ribCount" })),
    grp("Scan",
      btn("Generals", () => openGeneralManager(), "", "users"),
      ...(S.dev ? [btn("Scan generals", () => scanGeneralsFlow(), "", "scan")] : []),
      btn("Scan city", () => scanCityFlow(S.sel === "control" ? undefined : S.sel), "", "city"), btn("Map by hand", () => manualMapFlow(S.sel === "control" ? undefined : S.sel), "", "pin"), btn("Map by squares", () => scanCityTapsFlow(), "", "city")),
    grp("Data",
      btn("Profiles ▾", (e) => menu(e.currentTarget, [
        { label: "Save profile...", icon: "save", run: saveProfileFlow }, { label: "Load profile...", icon: "down", run: loadProfileFlow }]), "", "user"),
      btn("Logs", () => { S.sel = "control"; pickTab("logs"); renderSide(); }, "", "file")),
    grp("App",
      btn("Updates", () => { S.sel = "control"; S.tab = "updates"; renderSide(); renderTabs(); renderPage(); }, "", "refresh"),
      btn("Save", saveAll, "", "save"),
      btn("More ▾", (e) => menu(e.currentTarget, [
        { label: "Close window (bots keep running)", icon: "eye", run: () => act(api("hide_window")) },
        { label: "Restart app", icon: "refresh", run: restartFlow }, { label: "Quit and stop everything", icon: "power", run: quitFlow }]), "", "more")));
}
async function saveAll() {
  if (await act(api("save", { cfg: S.cfg, devices: S.devices }))) { AUTO.saved = AUTO.seen = cfgSnap(); toast("Settings saved.", "ok"); await reloadMeta(); }
}
async function copyLog() {
  const j = await act(api("log_text", { lines: 300 }));
  if (!j) return;
  const text = j.text || logsText();
  try { await navigator.clipboard.writeText(text); } catch (e) {
    const t = h("textarea", {}); t.value = text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
  }
  toast("The last lines of the log are copied. Paste them into the chat.", "ok");
}
// Save log: pick the time and the instance right where the log is shown (Control settings > Logs, and each instance's log)
function saveLogRow(fixedName) {
  const spans = [["Last 1 hour", 1], ["Last 6 hours", 6], ["Last 24 hours", 24], ["Last 3 days", 72], ["Last 7 days", 168]];
  const span = h("select", { "aria-label": "Time" }, ...spans.map(([t, v]) => h("option", { value: v, selected: v === 24 }, t)));
  const who = h("select", { "aria-label": "Instance" }, h("option", { value: "" }, "All instances"));
  if (fixedName) { who.innerHTML = ""; who.appendChild(h("option", { value: fixedName }, fixedName)); who.disabled = true; }
  else api("log_names", {}, { quiet: true }).then((r) => { for (const n of (r && r.names) || []) who.appendChild(h("option", { value: n }, n)); }).catch(() => {});
  const save = async () => { const j = await act(api("save_log", { hours: +span.value, name: who.value })); if (j) toast(`Saved ${j.lines} lines as a zip in Downloads: ${j.path}`, "ok"); };
  return h("div", { class: "logsave" }, h("div", { class: "field" }, h("label", {}, "Time"), span), h("div", { class: "field" }, h("label", {}, "Instance"), who),
    h("button", { class: "btn pri", type: "button", onclick: save }, "Save as zip"));
}
async function saveReportFlow() {
  const text = h("textarea", { style: { minHeight: "120px", width: "100%" }, placeholder: "Example: Rory stopped joining rallies after about 10 minutes. It was on the world map and never came back." });
  const shots = h("input", { type: "checkbox", checked: true });
  let saved = ""; try { saved = localStorage.getItem("rf_tester") || ""; } catch (e) {}
  const who = h("input", { type: "text", value: saved, placeholder: "Your name or nickname, so I know who sent it" });
  modal({ title: "Report a problem", focus: true, buttons: [{ label: "Cancel" }, { label: "Create report", cls: "pri", onclick: async () => {
    try { localStorage.setItem("rf_tester", who.value.trim()); } catch (e) {}
    const j = await act(api("save_report", { debug: shots.checked, description: text.value, tester: who.value.trim() }));
    if (!j) return false;
    setTimeout(() => modal({ title: "Report ready", buttons: [
      { label: "Show the file", cls: "pri", onclick: () => { act(api("reveal_file", { path: j.path })); return false; } },
      { label: "Open logs folder", onclick: () => { act(api("open_logs")); return false; } },
      { label: "Close" }],
      body: h("div", { class: "field" }, h("p", {}, "Your report is saved as one file. Please send me this file:"), h("p", { style: { wordBreak: "break-all", fontFamily: "var(--mono)" } }, j.path),
        h("p", { class: "note" }, "It holds the recent log, your settings (your Telegram and Discord keys are left out) and what you wrote above.")) }), 50);
  } }], body: h("div", { class: "field", style: { gap: "9px", minWidth: "420px" } },
    h("label", {}, "Your name"), who,
    h("label", {}, "What went wrong? (a few words are enough)"), text,
    h("label", { class: "tgl", style: { marginTop: "6px" } }, shots, h("span", { class: "sw" }), "Include the last screenshots the bot saved (they can show your account name)"),
    h("p", { class: "note" }, "The app collects the log and settings for you. Nothing is sent automatically.")) });
}
// The update in the status bar: one click installs it, restarts the app and starts the running instances again
async function updateNow() {
  S.updClicked = true;
  if (S.update) S.update.installing = true;
  toast("Installing the update - the status bar at the bottom shows the progress. The app restarts by itself (up to about a minute) and the running instances start again.", "ok");
  await act(api("install_update", { restart: true }));
}
async function restartFlow() { if (await confirmBox("Restart", "Restart the app now? Running bots are stopped.")) act(api("restart")); }
async function quitFlow() {
  if (await confirmBox("Quit", "Stop all bots and close RallyForge Bot completely?", "Quit")) { await act(api("quit")); document.body.innerHTML = "<p style='padding:30px;color:#999'>RallyForge Bot has closed. You can close this window.</p>"; }
}
function logsText() { return S.logs.map((l) => l.t).join("\n"); }

async function saveProfileFlow() {
  const name = await promptBox("Save profile", "Name for this set of settings:", "");
  if (!name) return;
  const j = await act(api("profile_save", { name, cfg: S.cfg, devices: S.devices }));
  if (j) { S.profiles = j.profiles; toast(`Profile '${j.name}' saved (all instances and settings).`, "ok"); }
}
async function loadProfileFlow() {
  const j = await act(api("state"));
  if (!j) return;
  S.profiles = j.profiles;
  if (!S.profiles.length) return toast("No saved profiles yet.");
  let chosen = S.profiles[0];
  const list = h("div", {});
  const draw = () => { list.innerHTML = ""; S.profiles.forEach((n) => list.appendChild(h("div", { class: "li" + (n === chosen ? " sel" : ""), onclick: () => { chosen = n; draw(); } }, n))); };
  draw();
  modal({ title: "Load profile", body: list, buttons: [
    { label: "Delete", onclick: async () => { if (await confirmBox("Delete profile", `Delete profile '${chosen}'?`)) { const r = await act(api("profile_delete", { name: chosen })); if (r) { S.profiles = r.profiles; chosen = S.profiles[0]; draw(); } } }, keep: true },
    { label: "Cancel" },
    { label: "Load", cls: "pri", onclick: async () => {
        if (!chosen) return;
        AUTO.busy = true;                                   // no automatic save while the profile is swapped in
        const r = await act(api("profile_load", { name: chosen })).finally(() => {});
        if (!r) AUTO.busy = false;
        if (r) { const j = await api("state").finally(() => (AUTO.busy = false)); S.cfg = j.cfg; S.devices = j.devices; AUTO.saved = AUTO.seen = cfgSnap(); S.generals = j.generals; S.monsters = j.monsters; S.profiles = j.profiles;
          S.cfg.ui_pal = S.cfg.ui_pal || "ivory"; S.cfg.ui_mode = S.cfg.ui_mode || "light"; applyTheme(); layout(); toast(`Profile '${chosen}' loaded.`); }
      } }] });
}

// ------------------------------------------------------------------ sidebar / tabs / pages
function devTag(d) {
  if (S.cityJobs && S.cityJobs[d.id] && S.cityJobs[d.id].running) return "Mapping city";
  const t = { run: "Running", pause: "Paused", stop: "Stopped", error: "Problem" }[d.state] || "";
  if (d.state === "run" && d.kicked_at) return "Kicked";
  if (d.state === "run" && d.no_stamina) return "No stamina";
  return d.queued ? "Queued" : t;
}
function renderSide() {
  const L = $("#instList");
  if (!L || S.dragging) return;          // do not redraw under the mouse while an instance is being dragged
  L.innerHTML = "";
  const item = (key, label, dot, extra) => h("div", { class: "inst st-" + (dot || "run") + (S.sel === key ? " sel" : ""), onclick: () => { S.sel = key; renderSide(); renderTabs(); renderPage(); } },
    dot ? h("span", { class: "dot " + dot }) : h("span", { class: "dot run", style: { background: "var(--accent1)" } }),
    h("span", { class: "nm", title: label }, label), extra ? h("small", {}, extra) : null);
  const cb = $("#ctlBtn"); if (cb) cb.classList.toggle("on", S.sel === "control");
  const ic = $("#instCnt"); if (ic) ic.textContent = S.devStatus.length + " total";
  for (const d of S.devStatus) {
    L.appendChild(item(d.id, (d.name || short(d.id)), (d.state === "run" && (d.no_stamina || d.kicked_at)) ? "pause" : d.state, [(["Running", "Stopped", "Paused", "Problem", ""].includes(devTag(d)) ? "" : devTag(d)), ""].filter(Boolean).join(" · ")));
    // chip timer: counts down (kicked wait, turn in Start all) or up (emulator starting / game loading)
    const tm = d.state === "run" && d.kicked_at ? { "data-at": String(d.kicked_at) } : d.state === "run" && d.loading ? { "data-since": String(d.loading_since) }
      : d.state !== "run" && d.start_at ? { "data-at": String(d.start_at) } : null;
    const chip = d.state === "run" && d.kicked_at ? ["kicked", "warn"] : d.state === "run" && d.loading ? [d.loading, "warn"] : d.state === "run" && d.no_stamina ? ["no stamina", "warn"]
      : d.state === "run" ? (busyTask(d.id) ? [busyTask(d.id), "ok"] : ["idle", ""]) : d.start_at ? ["start", "off"] : d.state === "pause" ? ["paused", "warn"] : d.state === "error" ? ["problem", "bad"] : ["offline", "off"];
    L.lastChild.querySelector(".nm").after(h("span", { class: "ichip " + chip[1], title: d.state === "run" ? (d.loading ? "Emulator / game starting" : ((S.now[d.id] || {}).text || "Idle") + (d.joins ? " \u2022 " + d.joins + " joins" : "")) : d.start_at ? "Waiting for its turn in Start all" : "" },
      chip[0], tm ? h("span", Object.assign({ class: "ctimer" }, tm)) : null));      // status at a glance on the right; Start/Pause/Stop live in the page header
    sideDrag(L.lastChild, d.id);
    if (d.state === "pause" && d.resume_at) { const it = L.lastChild.querySelector("small") || L.lastChild.appendChild(h("small", {})); it.appendChild(h("span", { class: "ptimer", "data-at": String(d.resume_at) })); }   // kicked wait: timer sits in the chip
  }
  tickPauseTimers();
}
// Drag an instance up or down in the left list to change the order (saved at once, kept after a restart)
function sideDrag(el, id) {
  el.draggable = true;
  el.dataset.id = id;
  el.title = "Drag to change the order";
  el.addEventListener("dragstart", (e) => { S.dragging = id; el.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; try { e.dataTransfer.setData("text/plain", id); } catch (x) {} });
  el.addEventListener("dragover", (e) => {
    if (!S.dragging || S.dragging === id) return;
    e.preventDefault();
    const L = el.parentElement, src = L.querySelector(`.inst[data-id="${CSS.escape(S.dragging)}"]`);
    if (!src) return;
    const r = el.getBoundingClientRect();
    L.insertBefore(src, e.clientY < r.top + r.height / 2 ? el : el.nextSibling);
  });
  el.addEventListener("drop", (e) => e.preventDefault());
  el.addEventListener("dragend", async () => {
    el.classList.remove("dragging");
    const L = el.parentElement;
    const order = L ? [...L.querySelectorAll(".inst[data-id]")].map((x) => x.dataset.id) : [];
    const before = S.devStatus.map((d) => d.id);
    S.dragging = null;
    if (!order.length || order.join("\n") === before.join("\n")) return renderSide();
    const j = await act(api("reorder_devices", { order }, { quiet: true }));
    if (j) {
      S.devices = j.devices;
      S.devStatus = order.map((i) => S.devStatus.find((d) => d.id === i)).filter(Boolean).map((d, k) => ({ ...d, idx: k + 1 }));
      toast("Order saved.", "ok");
    }
    renderSide();
    if (S.sel === "control" && S.tab === "dashboard") rerender();
  });
}
// Paused instances count down to their automatic restart; one 1-second ticker updates every timer in place
function fmtLeft(sec) { sec = Math.max(0, Math.round(sec)); const m = Math.floor(sec / 60), s = sec % 60; return m + ":" + String(s).padStart(2, "0"); }
function tickPauseTimers() {
  document.querySelectorAll(".ctimer").forEach((e) => {          // timers inside the instance-list chips
    const now = Date.now() / 1000;
    const left = Number(e.dataset.at) - now;
    e.textContent = " " + (e.dataset.since ? fmtLeft(now - Number(e.dataset.since)) : left > 0 ? fmtLeft(left) : "now");
  });
  document.querySelectorAll(".ptimer").forEach((e) => {
    const left = Number(e.dataset.at) - Date.now() / 1000;
    const pre = e.dataset.pre || "";
    e.textContent = left > 0 ? (e.parentElement && e.parentElement.tagName === "SMALL" ? " \u00b7 " : "") + pre + (pre ? "" : "\u23F8 ") + fmtLeft(left) : (pre ? pre : " ") + "resuming\u2026";
  });
}
setInterval(tickPauseTimers, 1000);
// Start / Pause / Stop: the button that matches the current state lights up in its own colour (green / amber / red)
// Control settings top bar: the buttons follow how many instances run.
//   none running -> Start all is the main button, Pause all / Stop all greyed
//   some running -> "Start the rest" + "2/8 running", Pause / Stop active
//   all running  -> Start shows a green "Running" (not clickable), Pause / Stop active
//   just pressed -> "Starting..." until every instance runs (instances start one by one; gives up after 3 min)
function paintRibbonRun() {
  const rib = document.getElementById("ribbon");
  if (!rib || S.sel !== "control") return;
  const total = S.devStatus.length, run = S.devStatus.filter((d) => d.state === "run").length;
  const busy = run + S.devStatus.filter((d) => d.state === "pause").length;
  const starting = S.startingAt && Date.now() - S.startingAt < 180000 && run < total;
  if (!starting) S.startingAt = 0;
  rib.querySelectorAll("[data-act]").forEach((b) => {
    const a = b.dataset.act, lbl = b.querySelector(".runlbl");
    b.classList.remove("isrunning", "starting", "greyed");
    b.className = b.className.replace(/\bcur-\w+/g, "").replace(/\s+/g, " ").trim();
    if (a === "start" && lbl) {
      if (starting) { b.classList.add("starting"); lbl.textContent = "Starting\u2026"; }
      else if (total && run === total) { b.classList.add("isrunning"); lbl.textContent = "Running"; }
      else if (allPaused()) lbl.textContent = "Resume all";              // everything paused: the main button carries on
      else lbl.textContent = busy ? "Start the rest" : "Start all";
    }
    if ((a === "pause" || a === "stop") && busy === 0 && !starting) b.classList.add("greyed");
    if (a === "pause" && allPaused()) b.classList.add("greyed");          // nothing left to pause: Resume all is on the Start button
  });
  const c = document.getElementById("ribCount");
  if (c) { c.textContent = run && run < total ? run + "/" + total + " running" : ""; }
}
setInterval(() => { if (S.startingAt) paintRibbonRun(); }, 3000);
// every running instance paused -> the Pause buttons turn into Resume (otherwise there is no way to carry on)
function allPaused() { const sts = S.devStatus.map((d) => d.state); return sts.includes("pause") && !sts.includes("run"); }
function pauseOrResumeAll() { return act(allPaused() ? api("resume") : api("pause", { serial: "all" })); }
function paintPauseLabels() {
  const ap = allPaused();
  const sp = document.getElementById("sidePause");
  if (sp) { sp.textContent = ap ? "Resume all" : "Pause all"; sp.title = ap ? "Carry on with every paused instance" : "Pause every running instance"; }
  if (S.sel !== "control") document.querySelectorAll('.instbar [data-act="pause"]').forEach((b) => {      // one instance: Pause <-> Resume
    b.textContent = (S.devStatus.find((x) => x.id === S.sel) || {}).state === "pause" ? "Resume" : "Pause";
  });
}
function paintRunState() {
  let st = null;
  if (S.sel !== "control") { const d = S.devStatus.find((x) => x.id === S.sel); st = d ? d.state : null; }
  else { const sts = new Set(S.devStatus.map((d) => d.state)); st = sts.size === 1 ? [...sts][0] : null; }
  const map = { run: "start", pause: "pause", stop: "stop" };
  document.querySelectorAll("[data-act]").forEach((b) => {
    b.className = b.className.replace(/\bcur-\w+/g, "").replace(/\s+/g, " ").trim();
    if (st !== "run" && map[st] && map[st] === b.dataset.act) b.classList.add("cur-" + st);   // only Paused / Stopped stay lit; running = all normal
    // buttons that do nothing right now are greyed out (one instance only): Start while running, Pause and Stop while stopped
    const off = S.sel !== "control" && ((st === "run" && b.dataset.act === "start") || ((st === "stop" || st === "idle" || st === "error") && (b.dataset.act === "pause" || b.dataset.act === "stop")));
    b.classList.toggle("greyed", !!off);
  });
  paintRibbonRun();
  paintPauseLabels();
}
function ribbonLabels() {
  const all = S.sel === "control";
  const rib = document.getElementById("ribbon"); if (rib) rib.style.display = all ? "" : "none";      // the top button bar belongs to Control settings; each instance has its own bar
  document.querySelectorAll(".runlbl").forEach((e) => { e.textContent = e.dataset.base + (all ? " all" : ""); });
  paintRunState();
}
const TAB_GROUPS = [
  ["dashboard", "Dashboard", null],
  ["presets", "Presets & generals", null],
  ["monsters", "Monsters", null],
  ["tasks", "Tasks", null],
  ["duties", "Duties", null],
  ["market", "Market", null],
  ["logs", "Logs", null],
  ["setup", "Setup", ["settings", "reliability", "schedule"]],
  ["app", "App", ["updates", "telegram", "license"]],
];
function closeTabMenu() { const m = document.getElementById("tabmenu"); if (m) m.remove(); document.removeEventListener("mousedown", tabMenuOutside, true); document.removeEventListener("keydown", tabMenuKey, true); }
function tabMenuOutside(e) { const m = document.getElementById("tabmenu"); if (m && !m.contains(e.target) && !e.target.closest(".tab.grp")) closeTabMenu(); }
function tabMenuKey(e) { if (e.key === "Escape") closeTabMenu(); }
function pickTab(k) { S.tab = k; try { localStorage.setItem("rf_ctl_tab", k); } catch (e) {} closeTabMenu(); renderTabs(); renderPage(); }
function openTabMenu(btn, items) {
  const was = document.getElementById("tabmenu") && document.getElementById("tabmenu").dataset.for;
  closeTabMenu();
  if (was === btn.dataset.g) return;
  const r = btn.getBoundingClientRect();
  const m = h("div", { id: "tabmenu", class: "tabmenu", role: "menu" });
  m.dataset.for = btn.dataset.g;
  for (const k of items) {
    const l = (TABS.find((x) => x[0] === k) || [k, k])[1];
    m.appendChild(h("button", { class: "tmi" + (S.tab === k ? " on" : ""), role: "menuitem", onclick: () => pickTab(k) }, ico(TAB_ICO[k] || "grid"), l));
  }
  m.style.left = Math.max(8, Math.min(r.left, innerWidth - 230)) + "px";
  m.style.top = (r.bottom + 6) + "px";
  document.body.appendChild(m);
  document.addEventListener("mousedown", tabMenuOutside, true);
  document.addEventListener("keydown", tabMenuKey, true);
}
function renderTabs() {
  ribbonLabels();
  closeTabMenu();
  const t = $("#tabs");
  if (!t) return;
  t.innerHTML = "";
  t.style.display = S.sel === "control" ? "" : "none";
  const ok = (k) => k !== "license" || (S.license && S.license.required);
  for (const [g, label, kids] of TAB_GROUPS) {
    if (!kids) {
      t.appendChild(h("button", { class: "tab" + (S.tab === g ? " on" : ""), onclick: () => pickTab(g) }, ico(TAB_ICO[g] || "grid"), label));
      continue;
    }
    const items = kids.filter(ok);
    if (!items.length) continue;
    const here = items.includes(S.tab);
    const cur = here ? (TABS.find((x) => x[0] === S.tab) || [0, label])[1] : "";
    const b = h("button", { class: "tab grp" + (here ? " on" : ""), "aria-haspopup": "menu", onclick: (e) => openTabMenu(e.currentTarget, items) },
      ico(TAB_ICO[here ? S.tab : items[0]] || "grid"), h("span", { class: "gl" }, label), here ? h("span", { class: "gcur" }, cur) : null, h("span", { class: "chev" }, "\u25BE"));
    b.dataset.g = g;
    t.appendChild(b);
  }
}
let lastPageSel = null;                                                 // which page was shown before (to keep the scroll position between two instances)
function renderPage() {
  const p = $("#pages");
  if (!p) return;
  logViews = []; actViews = [];
  // going from one instance to another keeps the scroll position: the same setting is at the same place, no scrolling down again
  const keep = (S.sel !== "control" && lastPageSel && lastPageSel !== "control" && lastPageSel !== S.sel) ? p.scrollTop : 0;
  p.innerHTML = "";
  p.scrollTop = 0;
  const ap = $("#app"); if (ap) ap.classList.toggle("inst-mode", S.sel !== "control");
  if (S.sel === "control") {
    const fn = PAGES[S.tab] || PAGES.dashboard;
    p.appendChild(fn());
  } else {
    p.appendChild(instancePage(S.sel));
  }
  lastPageSel = S.sel;
  if (keep) { p.scrollTop = keep; requestAnimationFrame(() => { p.scrollTop = keep; }); setTimeout(() => { p.scrollTop = keep; }, 250); }
  paintRunState();
  stickyUpdate();
}
// the pinned instance header turns into a bar only after the page is scrolled; the jump buttons show when there is something to jump to
function stickyUpdate() {
  const p = $("#pages"); if (!p) return;
  const sh = p.querySelector(".stickyhead");
  if (sh) sh.classList.toggle("stuck", p.scrollTop > 6);
  let box = $("#jumpbox");
  if (!box) {
    box = h("div", { id: "jumpbox", class: "jumpbox" },
      h("button", { class: "jump", id: "jumpUp", title: "Go to the top", onclick: () => { const q = $("#pages"); q.scrollTo({ top: 0, behavior: "smooth" }); } }, "\u2191"),
      h("button", { class: "jump", id: "jumpDown", title: "Go to the bottom", onclick: () => { const q = $("#pages"); q.scrollTo({ top: q.scrollHeight, behavior: "smooth" }); } }, "\u2193"));
    (document.querySelector(".main") || document.body).appendChild(box);
  }
  const max = p.scrollHeight - p.clientHeight;
  $("#jumpUp").classList.toggle("on", p.scrollTop > 160);
  $("#jumpDown").classList.toggle("on", max > 200 && p.scrollTop < max - 80);
}
document.addEventListener("scroll", (e) => { if (e.target && e.target.id === "pages") stickyUpdate(); }, true);
setInterval(stickyUpdate, 700);
function rerender() { const p = $("#pages"); const y = p.scrollTop; renderPage(); p.scrollTop = y; }

// ------------------------------------------------------------------ logs
function lineClass(t) { return t.startsWith("[!]") ? "e" : t.startsWith("[+]") ? "g" : t.startsWith("[-]") ? "w" : ""; }
function addLine(view, l) {
  const el = view.el;
  const stick = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  const text = view.serial === null && l.s ? `[${l.n}] ${l.t}` : l.t;
  el.appendChild(h("div", { class: lineClass(l.t) }, text));
  const cap = S.cfg.low_memory ? 300 : 700;
  while (el.childNodes.length > cap) el.removeChild(el.firstChild);
  if (stick) el.scrollTop = el.scrollHeight;
}
function logBox(serial, tall) {
  const el = h("div", { class: "log" + (tall ? " tall" : "") });
  const view = { el, serial };
  logViews.push(view);
  for (const l of S.logs.slice(-300)) if (serial === null || l.s === serial) addLine(view, l);
  return el;
}
// ---- Activity screen: what the bot is doing, and how each rally went
S.acts = []; S.now = {};
const esc = (t) => String(t == null ? "" : t);
// what an instance is doing right now (from its latest step), in a few words for the instance list; "" = nothing (idle)
function busyTask(serial) {
  const t = ((S.now && S.now[serial]) || {}).text || "";
  const cut = (x) => (x.length > 16 ? x.slice(0, 15) + "\u2026" : x);
  let m;
  if (!t || /^(Looking|Idle|Auto-Join is on)/.test(t)) return "";
  if ((m = /^(?:Duty|City task): (.+)$/.exec(t))) return cut(m[1]);
  if ((m = /^Joining (.+?) rally/.exec(t))) return cut("joining " + m[1]);
  if (/^(Trying preset|Choosing generals|Marching)/.test(t)) return "joining";
  if (/^Checking stamina/.test(t)) return "stamina";
  if (/Auto-Join/.test(t)) return "Auto-Join";
  if (/^Taking the game back/.test(t)) return "taking back";
  if (/^KICKED/.test(t)) return "";
  if ((m = /^(Champions|Crazy Eggs)/.exec(t))) return m[1];
  return cut(t.split(/[:\-(]/)[0].trim());
}
// ---- Today card: turn the "today" API answer into what the card shows (safe defaults when an instance just started)
function fmtHM(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60);
  return hh ? `${hh}h ${mm}m` : mm ? `${mm}m` : `${sec % 60}s`;
}
function hhmm(t) { return t ? new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "-"; }
function todayModel(j, serial) {
  j = j || {};
  const nowText = ((S.now && S.now[serial]) || {}).text || "";
  const skew = (j.now || Date.now() / 1000) - Date.now() / 1000;             // server clock vs this window
  const why = (m) => /city map|City Mapper/i.test(m || "") ? "no city map" : (m || "failed 3 times").replace(/^the /, "");
  const order = { run: 0, due: 1, waiting: 2, retry: 2, gaveup: 3, done: 4 };
  const items = (j.queue || []).map((q) => {
    let k = q.status, sub;
    if (nowText === "Duty: " + q.title) k = "run";
    if (k === "run") sub = "running now";
    else if (k === "done") sub = "done " + hhmm(q.last_ok);
    else if (k === "waiting" || k === "retry") sub = (k === "retry" ? "retry " : "next ~") + String(q.text || "").replace(/^(Next|Retrying)\s*/, "");
    else if (k === "gaveup") sub = "skipped - " + why(q.msg);
    else sub = q.msg ? "next - after a failed try" : "next - waiting its turn";
    return { k, title: q.title, sub, icon: { run: "▶", done: "✓", gaveup: "!" }[k] || "○" };
  }).sort((a, b) => (order[a.k] ?? 2) - (order[b.k] ?? 2));
  const total = items.length, left = items.filter((x) => x.k !== "done").length;
  const resetIn = (j.reset_at || 0) - (Date.now() / 1000 + skew);
  let summary;
  if (!total) summary = "No daily duty is switched on for this instance.";
  else if (!left) summary = `All ${total} duties done for today`;
  else {
    const need = left * 180;                                                // about 3 minutes per duty
    summary = `${left} of ${total} duties left · ` + (need < resetIn ? `should all finish ${fmtHM(resetIn - need)} before reset` : "may not all finish before reset");
  }
  const r = j.results || {}, mons = r.monsters || {}, w = j.week || {};
  const monList = Object.entries(mons).sort((a, b) => b[1] - a[1]);
  return {
    resetAt: j.reset_at || 0, skew, resetLocal: hhmm(j.reset_at), items, summary,
    results: [
      ["Rallies joined", r.joins || 0, ""],
      ["Skipped (late)", r.late || 0, "Rallies left alone because too little time was left for the march"],
      ["Monsters hit", monList.length, monList.map(([m, n]) => `${m} ×${n}`).join(", ") || "none yet"],
      ["Duties done", total ? `${r.duties_done || 0}/${total}` : String(r.duties_done || 0), ""],
      ["Restarts", r.restarts || 0, "Evony or the emulator restarted by the bot"],
    ],
    week: w.days ? `Last ${w.days} day${w.days > 1 ? "s" : ""}: ${w.joins || 0} joins · ${((w.restarts || 0) / w.days).toFixed(1).replace(/\.0$/, "")} restarts/day avg` : "Last 7 days: no data yet",
  };
}
function actRow(v, l) {
  const a = l.a, k = a.k;
  const chip = k === "join" ? ["Joined", "g"] : k === "fail" ? ["Failed", "e"] : k === "renew" ? ["Renewed", "g"] : ["Skipped", "w"];
  const took = a.took != null ? a.took + "s" : "";
  const cells = [a.t, v.serial === null ? l.n : null, k === "renew" ? "Auto-Join" : a.mon, a.rally != null ? a.rally + "s" : "", h("span", { class: "chip " + chip[1] }, chip[0]),
    k === "join" ? "Preset " + a.preset + (a.n ? "  (#" + a.n + ")" : "") : k === "renew" ? "Game Auto-Join restarted - now " + a.left + " left" : a.why, took];
  const tr = h("tr", { title: typeof cells[5] === "string" ? cells[5] : "" }, ...cells.filter((c, i) => !(i === 1 && c === null)).map((c) => h("td", {}, c)));
  // one row per rally: same instance + monster + (almost) the same end time = the same rally
  if (a.rally != null && a.t && a.mon) {
    const [hh, mm, ss] = String(a.t).split(":").map(Number);
    tr.dataset.end = String(hh * 3600 + mm * 60 + ss + Number(a.rally));
    tr.dataset.mon = String(a.mon).replace(/\s+\d+$/, "") + "|" + l.s;
    tr.dataset.k = k;
  }
  return tr;
}
// Put the newest result of a rally at the top and drop the older row of the same rally (a JOINED row is never replaced by a skip)
function placeActRow(body, row) {
  if (row.dataset.end) {
    for (const old of [...body.children]) {
      if (old.dataset.mon === row.dataset.mon && Math.abs(Number(old.dataset.end) - Number(row.dataset.end)) <= 15) {
        if (old.dataset.k === "join" && row.dataset.k !== "join") return;
        old.remove();
      }
    }
  }
  body.insertBefore(row, body.firstChild);
}
function actStats(serial) {
  const list = S.acts.filter((l) => serial === null || l.s === serial);
  const n = (k) => list.filter((l) => l.a.k === k).length;
  const joins = list.filter((l) => l.a.k === "join" && l.a.took != null);
  const avg = joins.length ? (joins.reduce((t, l) => t + l.a.took, 0) / joins.length).toFixed(1) + "s" : "-";
  return `Joined ${n("join")}  ·  Skipped ${n("skip")}  ·  Failed ${n("fail")}  ·  Average join time ${avg}`;
}
function paintStats(el, serial) {
  const list = S.acts.filter((l) => serial === null || l.s === serial);
  const n = (k) => list.filter((l) => l.a.k === k).length;
  const joins = list.filter((l) => l.a.k === "join" && l.a.took != null);
  const avg = joins.length ? (joins.reduce((t, l) => t + l.a.took, 0) / joins.length).toFixed(1) + " s" : "-";
  el.innerHTML = "";
  [["Joined", n("join")], ["Skipped", n("skip")], ["Failed", n("fail")], ["Average join", avg]].forEach(([l, v]) =>
    el.appendChild(h("div", { class: "stile" }, h("b", {}, String(v)), h("span", {}, l))));
}
function actNow(serial) {
  if (serial !== null) { const x = S.now[serial]; return x ? `${x.t}  ${x.text}` : "Waiting for the bot to start..."; }
  const rows = Object.entries(S.now).map(([s, x]) => `${x.n}: ${x.text}`);
  return rows.length ? rows.join("\n") : "Waiting for the bot to start...";
}
function activityBox(serial, title) {
  const now = h("div", { class: "actnow" }), stats = h("div", { class: "actstats" });
  const head = ["Time", serial === null ? "Instance" : null, "Monster", "Rally left", "Result", "Details", "Took"].filter(Boolean);
  const body = h("tbody", {});
  const wrap = h("div", { class: "actwrap" }, h("table", { class: "acttable" }, h("thead", {}, h("tr", {}, ...head.map((t) => h("th", {}, t)))), body));
  const view = { now, stats, body, wrap, serial };
  actViews.push(view);
  for (const l of S.acts.slice(-200)) if (l.a.k !== "step" && (serial === null || l.s === serial)) placeActRow(body, actRow(view, l));
  now.textContent = actNow(serial); paintStats(stats, serial);
  return h("div", { class: "actsplit" },
    h("div", { class: "actleft" }, h("h3", {}, title || "Activity"), h("div", { class: "actbar" }, h("span", { class: "actlab" }, "Now"), now), stats),
    h("div", { class: "actright" }, h("h3", {}, "Monster joins"), wrap));
}
function pushActs(list) {
  for (const l of list || []) {
    if (l.a.k === "step") { S.now[l.s] = { text: l.a.text, t: l.a.t, n: l.n }; continue; }
    S.acts.push(l);
    for (const v of actViews) if (v.serial === null || v.serial === l.s) {
      placeActRow(v.body, actRow(v, l));
      while (v.body.childNodes.length > 200) v.body.removeChild(v.body.lastChild);
    }
  }
  if (S.acts.length > 600) S.acts.splice(0, S.acts.length - 600);
  for (const v of actViews) { v.now.textContent = actNow(v.serial); paintStats(v.stats, v.serial); }
}
function pushLogs(list) {
  for (const l of list) {
    S.logs.push(l);
    for (const v of logViews) if (v.serial === null || v.serial === l.s) addLine(v, l);
  }
  const cap = S.cfg.low_memory ? 400 : 1500;
  if (S.logs.length > cap) S.logs.splice(0, S.logs.length - cap);
}

// ------------------------------------------------------------------ polling
let lastState = "";
async function poll() {
  try {
    const j = await api("poll", { since: S.seq }, { quiet: true });
    $("#lostBanner")?.remove();
    S.seq = j.seq;
    S.ram = j.ram || {};
    S.tg = j.tg;
    S.update = j.update || null;
    S.genScan = j.gen_scan;
    S.catScan = j.catalog || { running: false, count: 0 };
    S.city = j.city;
    S.cityProg = j.city_progress || null;
    S.cityJobs = j.city_jobs || {};
    const ck = Object.keys(S.cityJobs).join("|");
    if (ck !== S.cityKey) { S.cityKey = ck; renderSide(); }
    if (j.lic) {
      const wasOk = S.license && S.license.valid;
      const bad = j.lic.required && !j.lic.valid;
      S.licBad = bad ? (S.licBad || 0) + 1 : 0;           // only react to a login problem that lasts (two polls in a row)
      if (!bad || S.licBad >= 2 || !wasOk) S.license = j.lic;
      if (bad && wasOk && S.licBad >= 2) showLogin(j.lic.error);
    }
    const key = JSON.stringify(j.devs);
    if (key !== lastState) { lastState = key; S.devStatus = j.devs; renderSide(); refreshInstanceControls(); paintRunState(); }
    pushLogs(j.logs);
    const bk = (S.devStatus || []).map((d) => busyTask(d.id)).join("|");
    if (bk !== S.busyKey) { S.busyKey = bk; renderSide(); }             // a duty started or ended: update the instance list
    pushActs(j.acts);
    for (const ev of j.events) handleEvent(ev);
    updateSummary();
  } catch (e) {
    if (!$("#lostBanner")) document.body.appendChild(h("div", { id: "lostBanner", style: { position: "fixed", top: 0, left: 0, right: 0, padding: "8px", textAlign: "center", background: "var(--bad)", color: "#fff", zIndex: 99 } }, "Lost contact with the app. If you closed it, start RallyForge Bot again."));
  }
  setTimeout(poll, document.hidden ? 4000 : 1000);
}
function updateSummary() {
  const c = { run: 0, pause: 0, error: 0 };
  S.devStatus.forEach((d) => { if (c[d.state] !== undefined) c[d.state]++; });
  const parts = [c.run + " running"];
  if (c.pause) parts.push(c.pause + " paused");
  if (c.error) parts.push(c.error + " with a problem");
  const s = $("#sum"); if (s) s.textContent = parts.join("  |  ");
  const r = $("#sbRun"); if (r) r.textContent = c.run + " running";
  const dt = $("#sbDot"); if (dt) dt.classList.toggle("on", c.run > 0);
  const who = $("#whoami");
  if (who) {
    const L = S.license || {};
    const nm = L.required ? (L.valid ? (L.user || L.buyer || "Logged in") : "Not logged in") : "Free edition";
    const sub = L.required && L.valid && L.days_left != null ? (L.days_left > 0 ? L.days_left + (L.days_left === 1 ? " day" : " days") + " left" : "expires today") : (L.required ? "" : "no login needed");
    who.innerHTML = ""; who.title = L.required && L.valid ? "Logged in as " + nm : "";
    who.append(h("span", { class: "wav" }, (nm[0] || "?").toUpperCase()), h("span", { class: "wtx" }, h("b", {}, nm), sub ? h("small", {}, sub) : null));
  }
  const lc = $("#sbLic");
  if (lc) {
    const L = S.license || {};
    if (L.required && L.valid && L.days_left != null) {
      lc.textContent = L.days_left > 0 ? "Account: " + L.days_left + (L.days_left === 1 ? " day" : " days") + " left" : "Account: expires today";
      lc.style.color = L.days_left <= 3 ? "var(--bad)" : "";
      lc.style.fontWeight = L.days_left <= 3 ? "700" : "";
      lc.title = "Valid until " + L.expires;
    } else lc.textContent = "";
  }
  const m = $("#sbRam");
  if (m && S.ram && S.ram.total_mb) { m.textContent = `${(S.ram.proc_mb || 0).toFixed(0)} MB`; m.title = `The app uses ${(S.ram.proc_mb || 0).toFixed(0)} MB \u00b7 ${S.ram.free_mb.toFixed(0)} MB free on this PC`; }
  const t = $("#sbTg"); if (t) t.textContent = "Telegram: " + (S.tg.ready ? "Connected" : "Not connected");
  const u = $("#sbUpd");
  if (u) {
    const U = S.update;
    u.style.display = U ? "" : "none";
    if (U) {
      const wait = U.waiting_for && U.waiting_for.length ? ` (waiting for ${U.waiting_for.join(", ")} to finish a join)` : "";
      u.textContent = S.restartNow ? `Restarting now\u2026 the window closes and opens again in about 20\u201330 s` : U.needs_restart ? `\u2714 Version ${U.version} installed \u2013 click to restart now` : U.restarting ? `\u2714 Version ${U.version} installed \u2013 restarting in ${U.restart_in || 0} s${wait} \u2013 click to restart now`
        : U.installing ? `Installing ${U.version}\u2026 please wait` : `\u2B06 Update ${U.version} available \u2013 click to install`;
      u.title = (U.notes || "") + (U.installing ? "" : U.restarting ? "\nClick: restart now; the running instances start again by themselves" : "\nClick: install it, restart the app and start the running instances again");
      u.onclick = S.restartNow ? null : U.needs_restart ? (() => { S.restartNow = true; act(api("restart")); }) : U.restarting ? (() => { S.restartNow = true; act(api("restart_update_now")); }) : U.installing ? null : updateNow;
    }
  }
  const ts = $("#tgStatus"); if (ts) ts.textContent = S.tg.status;
  const ram = $("#ramInfo");
  if (ram && S.ram.total_mb) ram.textContent = `PC memory: ${(S.ram.total_mb - S.ram.free_mb).toFixed(0)} MB used of ${S.ram.total_mb.toFixed(0)} MB (${S.ram.free_mb.toFixed(0)} MB free)  |  this app: ${(S.ram.proc_mb || 0).toFixed(0)} MB`;
  const cs = $("#catScanInfo"); if (cs && S.catScan.running) cs.textContent = `Recorded ${S.catScan.count} generals so far... (see the log for the region and filter being read)`;
  if (window.drawCityProgress) drawCityProgress();
  const gs = $("#genScanInfo"); if (gs && S.genScan.running) gs.textContent = `Found ${S.genScan.count} different generals so far...`;
}

async function handleEvent(ev) {
  const d = ev.d || {};
  if (ev.k === "toast") toast(d.msg, d.kind);
  else if (ev.k === "devices") { const j = await act(api("state")); if (j) { S.devices = j.devices; if (S.sel === "control" && S.tab === "dashboard") rerender(); } }
  else if (ev.k === "bosses") { const j = await act(api("state", {}, { quiet: true })); if (j) { S.bosses = j.bosses; if (window.refreshBosses) refreshBosses(); } }
  else if (ev.k === "city_done") { const sr = ev.d && ev.d.serial; if (sr) { S.cityDone = { ...(S.cityDone || {}), [sr]: true }; if (S.cityJobs) delete S.cityJobs[sr]; if (window.drawCityProgress) drawCityProgress(); } else { closeScanBox(); if (window.showCityResult) showCityResult(); } const j = await act(api("state")); if (j) { S.cityInfo = j.city_info; if (window.refreshCityInfo) refreshCityInfo(); } toast("City mapper finished.", "ok"); }
  else if (ev.k === "scan_done") scanGeneralsDone(d);
  else if (ev.k === "catalog_done") catalogDone(d);
  else if (ev.k === "update_none") toast(`You are up to date (version ${d.version}).`, "ok");
  else if (ev.k === "update_found" && d.manual) {
    const ok = await confirmBox("Update available", `Version ${d.version} is available (you have ${S.version}).${d.notes ? "\n\n" + d.notes : ""}\n\nInstall it now?`, "Install");
    if (ok) updateNow();
  } else if (ev.k === "update_installed") {
    if (S.updClicked) toast(`Version ${d.version} is installed. The app restarts in a moment and the running instances start again.`, "ok");
    else if (d.auto) toast(`Version ${d.version} is installed. The app restarts by itself in a moment and the running instances carry on.`, "ok");
    else if (await confirmBox("Update installed", `Version ${d.version} is installed. Restart the app now?`, "Restart")) act(api("restart"));
  }
}

// ------------------------------------------------------------------ data refresh
async function reloadMeta() {
  const j = await act(api("state"));
  if (!j) return;
  S.generals = j.generals; S.monsters = j.monsters; S.catalog = j.catalog || S.catalog || []; S.profiles = j.profiles; S.cityInfo = j.city_info; S.bosses = j.bosses;
}

async function boot() {
  const j = await api("state");
  S.version = j.version;
  S.author = j.author;
  S.copyright = j.copyright;
  S.license = j.license || { required: false };
  S.dev = !!j.dev_tools;
  if (S.license.required && !S.license.valid) setTimeout(() => showLogin(S.license.error), 300);
  S.cfg = j.cfg;
  S.devices = j.devices;
  S.firstRun = j.first_run;
  S.generals = j.generals; S.monsters = j.monsters; S.catalog = j.catalog || S.catalog || []; S.profiles = j.profiles; S.cityInfo = j.city_info; S.bosses = j.bosses;
  S.meta = { buildings: j.building_names, icons: j.icons, examples: j.example_tasks, botPresets: j.default_bot_presets };
  S.devStatus = (await api("poll", { since: 0 })).devs;
  S.cfg.ui_pal = S.cfg.ui_pal || "ivory";
  S.cfg.ui_mode = S.cfg.ui_mode || "light";
  applyTheme();
  document.title = "RallyForge Bot " + S.version;
  layout();
  poll();
  checkWhatsNew();
  setTimeout(() => { try { maybeStartTour(); } catch (e) {} }, 2500);
  if (S.firstRun && !S.devices.length) S.devices.push({ id: "", name: "" });
  setTimeout(() => detectDevices(!S.firstRun), 900);          // every start: look for instances so the "Found instances" panel is ready (quiet unless something new turns up)
}

// Every change is saved by itself about 2 seconds after you stop editing (Save still works, and Start saves too),
// so nothing you set on an instance or on the presets is lost when the app restarts.
const AUTO = { saved: null, seen: null, busy: false, err: "" };
function cfgSnap() { try { return JSON.stringify({ c: S.cfg, d: S.devices }); } catch (e) { return null; } }
setInterval(async () => {
  if (!S.cfg || AUTO.busy) return;
  const now = cfgSnap();
  if (now === null) return;
  if (AUTO.saved === null) { AUTO.saved = AUTO.seen = now; return; }          // first look after start: nothing changed yet
  if (now === AUTO.saved) { AUTO.seen = now; return; }
  if (now !== AUTO.seen) { AUTO.seen = now; return; }                           // still being edited: wait until it is quiet
  AUTO.busy = true;
  try {
    await api("save", { cfg: S.cfg, devices: S.devices, auto: true }, { quiet: true });
    AUTO.saved = now; AUTO.err = "";
  } catch (e) {
    const m = e && !(e instanceof TypeError) ? String(e.message || "") : "";      // a setting that is not valid yet (not a lost connection)
    if (m && m !== AUTO.err) { AUTO.err = m; toast("Not saved yet: " + m, "error"); }
  } finally { AUTO.busy = false; }
}, 1000);

// long help paragraphs fold to two lines (hover shows the whole text, a click opens / closes it - no extra links)
(function () {
  const fold = (n) => {
    if (n.dataset.fold || !n.isConnected) return;
    const t = (n.textContent || "").trim();
    if (t.length < 110 || n.closest(".warnbar,.modal,.toasts")) return;
    n.dataset.fold = "1"; n.classList.add("clamp", "foldable"); n.title = t;
    n.onclick = () => n.classList.toggle("clamp");
  };
  const scan = (root) => (root || document).querySelectorAll(".pages .note").forEach(fold);
  const start = () => {
    const p = document.getElementById("pages"); if (!p) return setTimeout(start, 300);
    new MutationObserver(() => scan(p)).observe(p, { childList: true, subtree: true });
    scan(p);
  };
  start();
})();
