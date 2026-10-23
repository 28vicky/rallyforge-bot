/* Copyright (c) 2026 Kiravik. All rights reserved. RallyForge Bot was created by Kiravik. */
/* RallyForge Bot - the tabs and the per-instance page. */
"use strict";

// ------------------------------------------------------------------ value converters
const asList = (v) => (Array.isArray(v) ? v.slice() : String(v || "").split(/[;,\n]/).map((x) => x.trim()).filter((x) => x && x !== "(none)"));
const parsePresets = (t) => (String(t).match(/\d/g) || []).map(Number).filter((n) => n >= 1 && n <= 8);
const parseNames = (t) => String(t).split(",").map((x) => x.trim()).filter(Boolean);
function parseRange(t) {
  const n = (String(t).match(/\d+(?:\.\d+)?/g) || []).map(Number);
  if (!n.length) throw new Error("needs one or two numbers, e.g. 1,2");
  return n[n.length - 1] >= n[0] ? [n[0], n[n.length - 1]] : [n[n.length - 1], n[0]];
}
function parseMonPresets(t) {
  const out = {};
  for (const part of String(t).split(/[;\n]/)) {
    if (!part.includes(":")) continue;
    const [name, nums] = part.split(":", 2);
    const p = parsePresets(nums);
    if (name.trim() && p.length) out[name.trim()] = p;
  }
  return out;
}
const xyText = (v) => (Array.isArray(v) ? v.join(",") : "");
function parseXY(t, def) {
  const v = String(t).replace(/\s/g, "").split(",").filter(Boolean).map(Number);
  return v.length >= 2 && v.every(isFinite) ? v.slice(0, 2).map(Math.round) : def;
}

// ------------------------------------------------------------------ field builders (bound to an object)
function panel(title, ...kids) { return h("div", { class: "panel" }, title ? h("h3", {}, title) : null, kids); }
function subset(title, ...kids) { return h("div", { class: "subset" }, h("div", { class: "subtitle" }, title), kids); }   // settings that belong to a switched-on row: shown inside the same card
function note(t) { return h("p", { class: "note" }, t); }
// small round "i": hover shows the text, click pins it open (details out of the way, one place)
function infoTip(title, text) {
  const b = h("button", { type: "button", class: "infoi", "aria-label": title, title: text }, "i");
  const pop = h("span", { class: "infopop", role: "tooltip" }, h("b", {}, title), text);
  const w = h("span", { class: "infow" }, b, pop);
  b.onclick = (e) => { e.stopPropagation(); document.querySelectorAll(".infow.open").forEach((o) => { if (o !== w) o.classList.remove("open"); }); w.classList.toggle("open"); };
  if (!infoTip.bound) { infoTip.bound = true; document.addEventListener("click", (e) => { if (!e.target.closest(".infow")) document.querySelectorAll(".infow.open").forEach((o) => o.classList.remove("open")); }); }
  return w;
}
function fld(label, control, hint) { return h("div", { class: "field" }, h("label", {}, label), control, hint ? h("div", { class: "hint" }, hint) : null); }

function inp(obj, key, o) {
  o = o || {};
  const num = o.type === "int" || o.type === "float";
  const el = h("input", { type: num ? "number" : o.type || "text", class: o.cls || "", placeholder: o.ph || "", step: o.type === "float" ? "any" : null, min: o.min });
  el.value = o.get ? o.get(obj[key]) : obj[key] ?? "";
  el.addEventListener("input", () => {
    const v = el.value;
    if (o.set) { obj[key] = o.set(v); return; }
    if (o.type === "int") obj[key] = v === "" ? 0 : parseInt(v, 10);
    else if (o.type === "float") obj[key] = v === "" ? 0 : parseFloat(v);
    else obj[key] = v;
  });
  return el;
}
function sel(obj, key, opts, o) {
  o = o || {};
  const el = h("select", {}, opts.map((x) => { const [v, l] = Array.isArray(x) ? x : [x, x]; return h("option", { value: String(v) }, l); }));
  const cur = o.get ? o.get(obj[key]) : obj[key];
  el.value = String(cur ?? (Array.isArray(opts[0]) ? opts[0][0] : opts[0]));
  el.addEventListener("change", () => { obj[key] = o.set ? o.set(el.value) : o.num ? Number(el.value) : el.value; if (o.after) o.after(); });
  return el;
}
function tog(obj, key, label, o) {
  o = o || {};
  const i = h("input", { type: "checkbox", checked: !!obj[key] });
  i.addEventListener("change", () => { obj[key] = i.checked; if (o.after) o.after(i.checked); });
  return h("label", { class: "tgl" }, i, h("span", { class: "sw" }), label);
}
// pick-one choice as a segmented control (rule 15). opts = [[value, label, hint?], ...]; values compare as text, the saved type is kept
function segPick(obj, key, opts, o) {
  o = o || {};
  const cur = () => String(obj[key] == null || obj[key] === "" ? (o.def ?? "") : obj[key]);
  const g = h("div", { class: "seg" + (o.small ? " small" : ""), role: "radiogroup", "aria-label": o.label || key });
  const paint = () => [...g.children].forEach((b) => { const on = b.dataset.v === cur(); b.classList.toggle("on", on); b.setAttribute("aria-checked", on); b.tabIndex = on ? 0 : -1; });
  const choose = (v) => { obj[key] = v; paint(); if (o.after) o.after(v); };
  opts.forEach(([v, l, tip], i) => g.appendChild(h("button", { class: "segb", type: "button", role: "radio", "data-v": String(v), title: tip || "",
    onclick: () => choose(v),
    onkeydown: (e) => {                                    // arrow keys move between the choices, like a radio group
      const d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
      if (!d) return;
      e.preventDefault();
      const n = (i + d + opts.length) % opts.length;
      choose(opts[n][0]); g.children[n].focus();
    } }, l)));
  paint();
  return g;
}
const GEN_TABS = [[0, "Auto", "The general's own tab when it is known, otherwise the tab that is open - then All if it is not there"], [1, "All"], [2, "Military"], [3, "Development"], [4, "Duty"], [5, "Sub City", "Subordinate City"]];
const GEN_FAVS = [["leave", "Leave"], ["on", "On"], ["off", "Off"]];
// the generals the bot tries, best first: chips with the general's picture, drag to reorder, x to remove, Choose... opens the picker
function genField(obj, key, title) {
  const wrap = h("div", { class: "gchips" });
  const set = (arr) => { obj[key] = arr; draw(); };
  let dragFrom = null;
  function draw() {
    const arr = asList(obj[key]);
    wrap.innerHTML = "";
    if (!arr.length) wrap.appendChild(h("span", { class: "gnone" }, "None - the game's own choice"));
    arr.forEach((n, i) => {
      const cat = n.startsWith("@");
      const chip = h("span", { class: "gchip", draggable: "true", title: (i + 1) + ". " + (cat ? "every general in your category " + n.slice(1) : n) + (arr.length > 1 ? " - drag to change the order" : ""),
        ondragstart: (e) => { dragFrom = i; e.dataTransfer.effectAllowed = "move"; chip.classList.add("dragging"); },
        ondragend: () => chip.classList.remove("dragging"),
        ondragover: (e) => { e.preventDefault(); chip.classList.add("over"); },
        ondragleave: () => chip.classList.remove("over"),
        ondrop: (e) => { e.preventDefault(); chip.classList.remove("over"); if (dragFrom == null || dragFrom === i) return; const a = [...arr]; const [m] = a.splice(dragFrom, 1); a.splice(i, 0, m); dragFrom = null; set(a); } },
        h("span", { class: "gnum" }, String(i + 1)),
        cat ? null : h("img", { src: `/api/general_img?name=${encodeURIComponent(n)}&k=${window.KEY}`, alt: "", onerror: (e) => e.target.remove() }),
        h("span", { class: "gnm" }, cat ? "Category: " + n.slice(1) : n),
        h("button", { class: "gx", type: "button", "aria-label": "Remove " + n, title: "Remove", onclick: () => set(arr.filter((_, k) => k !== i)) }, "\u2715"));
      wrap.appendChild(chip);
    });
    wrap.appendChild(h("button", { class: "btn sm gadd", type: "button", onclick: async () => {
      const r = await chooseGenerals(title, asList(obj[key]));
      if (r !== null) set(r);
    } }, arr.length ? "Change\u2026" : "Choose\u2026"));
  }
  draw();
  return wrap;
}
const BTN_ICON = { Start: "play", Pause: "pause", Stop: "stop", "Take game back now": "refresh" };
const btn = (label, fn, cls, icon) => {
  const run = label === "Start" || label === "Pause" || label === "Stop";     // same plain raised look as the top bar
  const ic = icon || BTN_ICON[label];
  return h("button", { class: "btn ib " + (run ? "runb rb-" + label.toLowerCase() : (cls || "")), onclick: fn }, ic ? ico(ic) : null, label);
};
const grid2 = (...k) => h("div", { class: "grid2" }, k);

// selectable table
function listTable(cols, rows, onSel, selected) {
  let sel = selected ?? null;
  const tb = h("tbody");
  const draw = () => {
    tb.innerHTML = "";
    rows.forEach((r, i) => tb.appendChild(h("tr", { class: i === sel ? "sel" : "", onclick: () => { sel = i; draw(); if (onSel) onSel(i); }, ondblclick: () => r._dbl && r._dbl() }, r.cells.map((c) => h("td", {}, c)))));
  };
  draw();
  const wrap = h("div", { class: "tlist" }, h("table", {}, h("thead", {}, h("tr", {}, cols.map((c) => h("th", {}, c)))), tb));
  if (!rows.length) wrap.appendChild(h("div", { class: "empty" }, "Nothing here yet."));
  return { el: wrap, get: () => sel, set: (i) => { sel = i; draw(); } };
}


// A "Maximize" switch for a panel: the other panels on the page are hidden and this one fills the whole right section (in place, no popup); press again to bring everything back.
function maxToggle(panelEl) {
  if (!document.getElementById("maxStyle")) {
    const st = document.createElement("style"); st.id = "maxStyle";
    st.textContent = ".pmax-host>.panel:not(.pmax){display:none}.panel.pmax{display:flex;flex-direction:column;box-sizing:border-box;margin-bottom:0}" +
      ".hint{font-size:12.5px;opacity:.7;margin:2px 0 8px;line-height:1.4}.tgview{display:none}.tgview.on{display:block}.panel.pmax .tgviews{flex:1;min-height:0;display:flex;flex-direction:column}.panel.pmax .tgview.on{flex:1;min-height:0;display:flex;flex-direction:column}" +
      ".panel.pmax .tlist{max-height:none;flex:1;min-height:0}.panel.pmax .pmax-fill{flex:1;min-height:0;display:flex;flex-direction:column}.panel.pmax .pmax-fill>div{flex:1;min-height:0;display:flex;flex-direction:column}";
    document.head.appendChild(st);
  }
  const fit = () => {
    const m = document.querySelector(".main");
    if (!panelEl.classList.contains("pmax") || !m) return;
    m.scrollTop = 0;
    panelEl.style.height = Math.max(200, m.getBoundingClientRect().bottom - panelEl.getBoundingClientRect().top - 24) + "px";
  };
  window.addEventListener("resize", fit);
  const b = h("button", { class: "btn" }, "\u2922 Maximize");
  b.onclick = () => {
    const host = panelEl.parentElement;
    const on = panelEl.classList.toggle("pmax");
    if (host) host.classList.toggle("pmax-host", on);
    if (on) fit(); else panelEl.style.height = "";
    b.textContent = on ? "\u2921 Minimize" : "\u2922 Maximize";
  };
  return b;
}

const PAGES = {};

// ====================================================================== dashboard
PAGES.dashboard = function () {
  // ---- your instances (ui-simple): one row each, name and address edited in place, x removes; Detect is the main action
  const rows = h("div", { class: "ilist" });
  const applyDevs = () => act(api("set_devices", { devices: S.devices.filter((d) => d.id) }, { quiet: true })).then(() => poll1());
  const draw = () => {
    rows.innerHTML = "";
    if (!S.devices.some((d) => d.id) && !S.devices.length) rows.appendChild(h("div", { class: "empty" },
      h("b", {}, "No instances yet"), h("span", {}, "Open your emulators with Evony, then press Detect emulators."), btn("Detect emulators", detectDevices, "pri")));
    S.devices.forEach((d, i) => {
      rows.appendChild(h("div", { class: "irow" },
        h("span", { class: "inum" }, String(i + 1)),
        h("input", { type: "text", class: "iname", value: d.name, placeholder: "Name", "aria-label": "Name", oninput: (e) => (d.name = e.target.value.trim()), onchange: applyDevs }),
        h("input", { type: "text", class: "iaddr", value: d.id, placeholder: "127.0.0.1:5565", "aria-label": "Address", oninput: (e) => (d.id = e.target.value.trim()), onchange: applyDevs }),
        h("button", { class: "ix", type: "button", title: "Remove " + (d.name || d.id || "this row"), "aria-label": "Remove", onclick: () => { S.devices.splice(i, 1); draw(); applyDevs(); } }, "\u2715")));
    });
  };
  draw();
  const dev = panel(null,
    h("div", { class: "cardhead" }, h("h3", {}, "Your instances"),
      infoTip("Instances", "Detect lists the emulators it finds; you add the ones you want. Drag them in the left list to change the order. Instances you remove stay removed, even after a restart."),
      h("span", { style: { flex: "1" } }),
      h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); S.devices.push({ id: "", name: "" }); draw(); const ins = rows.querySelectorAll(".iaddr"); if (ins.length) ins[ins.length - 1].focus(); } }, "Add by address"),
      h("a", { href: "#", class: "lnk", onclick: async (e) => { e.preventDefault(); if (!(await confirmBox("Removed instances", "Instances you removed are never added again by Detect. Clear that list so the next Detect can add them?"))) return; const j = await act(api("restore_removed")); if (j) toast(j.restored ? `${j.restored} instance(s) can be detected again. Press Detect emulators.` : "No instances were removed.", "ok"); } }, "Show removed again"),
      btn("Detect emulators", detectDevices, "pri sm")),
    rows);
  // ---- first run: three steps, each with one action (shown until the first instance is added)
  const none = !S.devices.some((d) => d.id);
  const step = (n, title, sub, action, done) => h("div", { class: "fstep" + (done ? " done" : "") },
    h("span", { class: "fnum" }, done ? "\u2713" : String(n)), h("div", { class: "ftx" }, h("b", {}, title), h("small", {}, sub)), action);
  const first = none ? panel(null, h("div", { class: "cardhead" }, h("h3", {}, "Get started")),
    h("div", { class: "fsteps" },
      step(1, "Find your emulators", "Open them with Evony on screen.", h("div", { class: "row", style: { gap: "12px" } },
        h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); S.devices.push({ id: "", name: "" }); rerender(); } }, "Add by address"),
        btn("Detect emulators", detectDevices, "pri sm")), false),
      step(2, "Pick your presets", "Which game presets the bot may use.", btn("Open presets", () => pickTab("presets"), "sm"), false),
      step(3, "Start", "The bot joins rallies and does your duties.", h("button", { class: "btn sm", disabled: true, title: "Add an instance first" }, "Start all"), false))) : null;
  // ---- instances found by Detect but not added: one click each (nothing is added by itself)
  const addFound = async (ids) => {
    const j = await act(api("add_found", { ids }));
    if (j) { S.devices = j.devices; S.avail = j.available; toast(ids.length > 1 ? `${ids.length} instances added.` : "Instance added.", "ok"); rerender(); poll1(); }
  };
  const avail = (S.avail || []).filter((a) => !S.devices.some((d) => d.id === a.id));
  const found = avail.length ? panel("Found instances (not in your list yet)",
    note("Detect only shows what it found. Press Add for each instance you want the bot to use."),
    ...avail.map((a) => h("div", { class: "row", style: { marginBottom: "6px", gap: "14px" } },
      h("span", { style: { minWidth: "170px", fontFamily: "var(--mono)" } }, a.id),
      h("span", { class: "mute", style: { minWidth: "140px" } }, (a.name || "") + (a.removed ? "  (removed earlier)" : "")),
      btn("Add", () => addFound([a.id]), "sm pri"))),
    avail.length > 1 ? h("div", { class: "row", style: { marginTop: "8px" } }, btn("Add all " + avail.length, () => addFound(avail.map((a) => a.id)), "sm")) : null) : null;
  const log = panel(null, activityBox(null, "Activity - all instances"));
  return none ? h("div", {}, first, found, S.devices.length ? dev : null) : h("div", {}, renderEventCards(), renderTodayAll(), found, dev, log);
};
async function poll1() { try { const j = await api("poll", { since: S.seq }, { quiet: true }); S.devStatus = j.devs; renderSide(); } catch (e) {} }

const TAB_CHOICES = ["Leave as is", "All", "Military", "Development", "Duty", "Subordinate City"];   // index = tab number in the game (0 = do not switch)
// ====================================================================== per-instance page
const INSTANCE_FIELDS = [
  ["presets", "Presets (1-8, e.g. 1,2)", "presets"], ["preset_mode", "Preset mode", "choice", ["rotate", "random", "fixed"]],
  ["safety_margin", "Join if spare time >= (s)", "int"], ["max_rally_seconds", "Skip rally with more than (s)", "int"],
  ["max_joins", "Max joins (0 = no limit)", "int"], ["monster_mode", "Monster filter", "choice", ["off", "skip", "only"]],
  ["monster_list", "Ticked monsters (comma separated)", "names"], ["general_main", "Main generals, best first", "generallist"],
  ["general_assistant", "Assistant generals, best first", "generallist"], ["general_idle_only", "Only pick generals that are Idle", "bool"],
  ["general_main_tab", "Main general: category tab", "tab"], ["general_main_fav", "Main general: favourites", "choice", ["leave", "on", "off"]],
  ["general_assistant_tab", "Assistant: category tab", "tab"], ["general_assistant_fav", "Assistant: favourites", "choice", ["leave", "on", "off"]],
  ["general_view", "General list view while joining", "choice", ["grid", "list"]],
  ["general_on_fail", "If choosing a general fails", "choice", ["skip", "march"]], ["monster_unknown", "Monster with no picture", "choice", ["join", "skip"]], ["game_auto_join", "Use the game's Auto-Join (bot only renews it)", "bool"], ["general_assistant_optional", "No assistant available: join without it", "bool"], ["auto_join_renew_min", "Auto-Join: renew when less than (min) is left", "int"], ["auto_join_check_min", "Auto-Join: check every (min)", "int"], ["save_unknown_monsters", "Beta: save pictures of unknown monsters", "bool"], ["zoom_out", "Zoom the city out before finding buildings", "bool"], ["zoom_foreground", "Allow momentary foreground mouse focus for emulator zoom", "bool"],
  ["rally_nav_steps", "Buttons to reach rally list", "names"], ["lost_seconds", "Restart Evony if lost for (s, 0 = never)", "int"], ["reclaim_elsewhere", "Reclaim when I play elsewhere", "bool"], ["reclaim_grace_min", "Wait this long after I play elsewhere (min)", "int"], ["settle_seconds", "Wait after the game loads (s, 0 = off)", "int"],
  ["click_delay_ms", "Click delay (ms)", "int"], ["adaptive_delay", "Slow down after failures", "bool"],
  ["frozen_seconds", "Restart Evony if screen frozen (s)", "int"], ["stuck_seconds", "Restart instance if stuck (s)", "int"],
  ["stop_cmd", "Command that closes this instance", "text"], ["close_emulator_on_stop", "Close the instance when stopped", "bool"],
  ["schedule", "Schedule (blank = universal one)", "text"], ["cpu_level", "Load level (1 fastest - 4 lightest)", "choice", ["1", "2", "3", "4"]],
  ["screenshot_mode", "Screenshot method", "choice", ["auto", "png"]], ["rally_timer_source", "Rally countdown read from", "choice", ["progress_bar", "join_button"]],
  ["march_timer_source", "March time read from", "choice", ["join_button", "progress_bar", "none"]],
  ["poll_seconds", "Pause between scans (s, e.g. 1,2)", "range"], ["cooldown_after_join", "Pause after a join (s, e.g. 4,7)", "range"],
  ["bad_preset_seconds", "Bench a failing preset for (s)", "int"], ["verify_march", "Check the march really started", "bool"],
  ["game_reset_utc", "Game daily reset (auto = Evony's own time)", "text"],
  ["rally_enabled", "Join rallies (off = only do the tasks)", "bool"], ["city_enabled", "Do the tasks when no rally is waiting", "bool"], ["tasks_first", "A task that is due goes before rallies (then back to rallies)", "bool"],
  ["general_max_scrolls", "Scrolls when looking for a general", "int"], ["monster_presets", "Presets per monster (Witch:1,2; Pan:3)", "monpresets"],
  ["log_timers", "Log the timers it reads", "bool"], ["auto_launch_emulator", "Start instance automatically", "bool"],
  ["auto_launch_game", "Start Evony and wait for it", "bool"], ["launch_cmd", "Command that starts this instance", "text"],
  ["evony_package", "Evony package name", "text"],
];
const FIELD_HELP = {
  presets: "Which game presets this instance uses, e.g. 1,2,3. Blank = the universal list.",
  preset_mode: "rotate = take presets in turn, random = any armed one, fixed = always the lowest.",
  safety_margin: "Join only if the rally has at least this many seconds to spare after the march.",
  march_guess: "How long troops take to walk to a rally when the bot cannot read it (default 10 s).",
  max_rally_seconds: "Ignore rallies that still have more time than this (0 = no limit).",
  max_joins: "Stop this instance after this many joins (0 = never stop).",
  monster_mode: "off = join everything, skip = skip the ticked monsters, only = join only the ticked ones.",
  monster_list: "Monster names separated by commas. Easier: use the Monsters tab.",
  general_main: "Main generals the bot tries in order. Use the ... button to pick from your generals.",
  general_assistant: "Assistant generals tried in order. Use the ... button to pick.",
  general_idle_only: "Yes = skip generals that are busy with another march.",
  general_main_tab: "Category tab of the Select a General list to open before searching.",
  general_main_fav: "Turn the favourites filter on or off while searching for the main general.",
  general_assistant_tab: "Category tab to open when searching for the assistant general.",
  general_assistant_fav: "Favourites filter while searching for the assistant general.",
  general_view: "grid (thumbnails) is faster and more reliable than list.",
  general_on_fail: "skip = abandon the rally, march = march anyway with whatever the preset holds.",
  monster_unknown: "What to do with a monster the bot has no picture of.",
  cerberus: "Cerberus rallies are very big. skip = never join them.",
  game_auto_join: "The game joins by itself (needs the Rally Monthly Card); the bot only renews it.",
  zoom_out: "Before going to a building the bot zooms the city out once per game start, so building names can be read.",
  zoom_foreground: "Needed for MuMu: for about 2 seconds the emulator window comes to the front and the mouse scrolls over it (Ctrl + wheel), then the mouse goes back. Leave off if you use the PC while the bot runs.",
  general_assistant_optional: "Yes = if no assistant general is free, march with the main general only.",
  auto_join_renew_min: "Press Start on the game's Auto-Join again when less than this many minutes are left.",
  auto_join_check_min: "How often (minutes) the bot looks at the Auto-Join time left.",
  save_unknown_monsters: "Saves a picture of monsters the bot does not know, so you can name them later.",
  rally_nav_steps: "Names of the buttons (templates) the bot taps to get from the city to the rally list.",
  reclaim_elsewhere: "If you open the game on your phone the instance gets kicked off. ON = the bot waits out the grace time, then reloads the game and carries on. OFF = ignore.",
  reclaim_grace_min: "How many minutes to leave the game alone after you took it over on another device (default 10).",
  lost_seconds: "Restart Evony if the bot cannot tell which screen it is on for this long (0 = never).",
  settle_seconds: "Seconds to wait after the game finishes loading before starting.",
  click_delay_ms: "Pause after every tap in milliseconds. Raise it on a slow instance.",
  adaptive_delay: "Yes = the bot slows its taps down by itself after failures.",
  frozen_seconds: "Restart Evony if the screen has not changed for this long.",
  stuck_seconds: "Restart the whole instance if nothing worked for this long.",
  stop_cmd: "Optional command that closes this instance when the bot stops.",
  close_emulator_on_stop: "Yes = close the instance when the bot stops.",
  schedule: "Optional run times for this instance, e.g. 08:00-23:00. Blank = the universal schedule.",
  cpu_level: "1 = fastest but heaviest on the PC, 4 = slowest and lightest.",
  screenshot_mode: "auto is fastest; png is a slower fallback if screenshots look wrong.",
  rally_timer_source: "Where the bot reads the rally countdown from.",
  march_timer_source: "Leave on none: the game shows no march time on the rally list. If a rally leader is far away, raise 'Join if spare time >=' instead.",
  poll_seconds: "Pause between scans of the rally list, as min,max seconds (e.g. 1,2).",
  cooldown_after_join: "Pause after a successful join, as min,max seconds (e.g. 4,7).",
  bad_preset_seconds: "A preset that failed is left out for this many seconds.",
  verify_march: "Yes = check on screen that the march really started.",
  rally_enabled: "No = do not join rallies, only run the city tasks.",
  city_enabled: "Yes = do the city tasks when no rally is waiting.",
  game_reset_utc: "Leave it on auto: the bot follows Evony's daily reset (midnight US Pacific time, which moves by one hour with daylight saving). Only type a time (HH:MM, UTC) if your server resets at a different time. At the reset every daily duty, task and the Today numbers start again.",
  tasks_first: "Yes = when a task or duty is due (an egg timer ended, a daily task) the bot first joins any rally already on screen, then leaves the rally list, does the task and comes back. No = tasks only run when no rally is waiting.",
  general_max_scrolls: "How many times to scroll the general list while looking for a general.",
  monster_presets: "Different presets per monster, e.g. Witch:1,2; Pan:3.",
  log_timers: "Yes = write the timers it reads into the log (useful for checking).",
  auto_launch_emulator: "Yes = start the instance by itself when the bot starts.",
  auto_launch_game: "Yes = start Evony and wait until it is loaded.",
  launch_cmd: "Optional command that starts this instance (needed for auto start on some instances).",
  evony_package: "Android package name of Evony. Leave blank unless you use a different version.",
};
function globalText(key, kind) {
  const v = S.cfg[key];
  if (v === undefined || v === null || v === "") return "not set";
  if (kind === "bool") return v ? "yes" : "no";
  if (kind === "tab") return TAB_CHOICES[Number(v)] || String(v);
  if (kind === "presets" || kind === "names" || kind === "generallist") return asList(v).join(", ") || "none";
  if (kind === "range" && Array.isArray(v)) return v.map((x) => +Number(x).toFixed(2)).join(",");
  if (kind === "monpresets" && v && typeof v === "object") return Object.entries(v).map(([n, p]) => `${n}:${p.join(",")}`).join("; ") || "none";
  if (key === "march_timer_source" && v === "") return "none";
  return String(v);
}
const PKEYS0 = new Set(["preset_mode", "general_main", "general_assistant", "general_main_tab", "general_assistant_tab", "general_main_fav", "general_assistant_fav", "general_on_fail", "general_idle_only", "general_assistant_optional", "general_view"]);
const IF_KEYS = new Set(INSTANCE_FIELDS.map((f) => f[0]));

function instancePage(serial) {
  const d = S.devStatus.find((x) => x.id === serial) || { state: "idle", idx: 0, name: "" };
  const dev = S.devices.find((x) => x.id === serial) || { name: "" };
  S.cfg.per_device = S.cfg.per_device || {};
  const ov = (S.cfg.per_device[serial] = S.cfg.per_device[serial] || {});
  const status = h("span", { id: "instStatus", class: "state-pill" });
  const saveInstProfile = async () => {
    const name = await promptBox("Save profile", "Name for these settings of this instance:", "");
    if (!name) return;
    const j = await act(api("inst_profile_save", { serial, name, settings: S.cfg.per_device[serial] || {} }));
    if (j) toast(`Profile '${j.name}' saved. Load it on any instance with \u201cLoad profile...\u201d.`, "ok");
  };
  const loadInstProfile = async () => {
        const j = await act(api("inst_profiles"));
        if (!j) return;
        if (!j.profiles.length) return toast("No instance profiles yet - press \u201cSave profile...\u201d on an instance first.");
        let chosen = j.profiles[0], names = j.profiles;
        const list = h("div", {});
        const draw = () => { list.innerHTML = ""; names.forEach((n) => list.appendChild(h("div", { class: "li" + (n === chosen ? " sel" : ""), onclick: () => { chosen = n; draw(); } }, n))); };
        draw();
        modal({ title: "Load profile into this instance", body: h("div", {}, h("p", { class: "note" }, "This replaces the settings of THIS instance with the saved ones (its generals, presets and own settings). Other instances are not changed."), list), buttons: [
          { label: "Delete", onclick: async () => { if (await confirmBox("Delete profile", `Delete profile '${chosen}'?`)) await act(api("inst_profile_delete", { name: chosen })); } },
          { label: "Cancel" },
          { label: "Load", cls: "pri", onclick: async () => {
              if (!chosen) return;
              AUTO.busy = true;
              const r = await act(api("inst_profile_load", { serial, name: chosen })).finally(() => { AUTO.busy = false; });
              if (r) { S.cfg.per_device[serial] = r.settings || {}; AUTO.saved = AUTO.seen = cfgSnap(); rerender(); toast(`Profile '${chosen}' loaded into this instance.`, "ok"); }
            } }] });
  };
  const stBtn = (t, fn, cls) => h("button", { class: "btn" + (cls ? " " + cls : ""), "data-act": t.toLowerCase(), onclick: fn }, t);
  const moreBtn = btn("More \u25BE", (e) => menu(e.currentTarget, [
    { label: "Save profile...", run: saveInstProfile }, { label: "Load profile...", run: loadInstProfile }]), "");
  const bar = h("div", { class: "row instbar" },
    btn("Take game back now", () => act(api("takeback", { serial }))),
    stBtn("Start", () => collectAndRun("start", { serial }), "pri"), stBtn("Pause", () => act(api("pause", { serial })), "warnb"), stBtn("Stop", () => act(api("stop", { serial })), "bad"), moreBtn);
  const head = h("div", { class: "row insthead", style: { margin: "0 0 12px", gap: "14px", alignItems: "center" } },
    sideBtn(), h("h2", { style: { fontSize: Math.max(18, Math.min(40, parseInt(S.cfg.ui_name, 10) || 24)) + "px", lineHeight: "1.1", fontWeight: "800", margin: "0" } }, dev.name || "Instance " + d.idx),
    status, h("span", { class: "mute", style: { fontSize: "13px" } }, serial), h("span", { style: { flex: "1" } }), saveStEl(), bar);
  const warn = h("div", { id: "instWarn", class: "warnbar", style: { display: "none" } },
    h("b", {}, "\u26A0 Out of stamina. "), "This instance has no stamina left and no usable stamina items, so it cannot join rallies. It is still checking, and starts joining again by itself as soon as it has stamina (wait for it to refill, or add stamina items to the account).");

  // ---- overrides
  const fields = h("div", { class: "grid2" });
  const moreFields = h("div", { class: "grid2" });
  const BASIC = new Set(["game_reset_utc", "rally_enabled", "city_enabled", "tasks_first", "max_joins", "monster_mode", "game_auto_join", "auto_join_renew_min", "auto_join_check_min", "auto_launch_emulator", "auto_launch_game", "reclaim_elsewhere", "reclaim_grace_min"]);
  const marks = {};
  const setOv = (key, val, el) => { if (val === undefined) delete ov[key]; else ov[key] = val; if (el) el.style.outline = ""; };
  for (const [key, label, kind, choices] of INSTANCE_FIELDS) {
    if (key === "reclaim_elsewhere" || key === "reclaim_grace_min" || key === "game_auto_join" || key === "auto_join_renew_min" || key === "auto_join_check_min") continue;      // Auto-Join has its own panel;      // shown in its own panel at the top
    let control;
    const cur = ov[key];
    if (kind === "choice" || kind === "bool") {
      const opts = kind === "bool" ? ["(universal)", "yes", "no"] : ["(universal)", ...choices];
      control = h("select", {}, opts.map((o) => (o === "(universal)" ? h("option", { value: "(universal)" }, `(universal: ${globalText(key, kind)})`) : h("option", {}, o))));
      control.value = cur === undefined ? "(universal)" : kind === "bool" ? (cur ? "yes" : "no") : key === "march_timer_source" && cur === "" ? "none" : String(cur);
      control.addEventListener("change", () => {
        const v = control.value;
        if (v === "(universal)") setOv(key);
        else if (kind === "bool") setOv(key, v === "yes");
        else if (key === "march_timer_source" && v === "none") setOv(key, "");
        else if (key === "cpu_level") setOv(key, Number(v));
        else setOv(key, v);
      });
    } else if (kind === "tab") {
      control = h("select", {}, ["(universal)", ...TAB_CHOICES].map((o) => (o === "(universal)" ? h("option", { value: "(universal)" }, `(universal: ${globalText(key, kind)})`) : h("option", {}, o))));
      control.value = cur === undefined ? "(universal)" : TAB_CHOICES[Number(cur)] || TAB_CHOICES[0];
      control.addEventListener("change", () => { const v = control.value; if (v === "(universal)") setOv(key); else setOv(key, TAB_CHOICES.indexOf(v)); });
    } else if (kind === "generallist") {
      const e = h("input", { type: "text", readOnly: true, placeholder: "Universal: " + globalText(key, kind), value: cur === undefined ? "" : asList(cur).join(", ") || "(none)" });
      control = h("div", { class: "row", style: { flexWrap: "nowrap" } }, e,
        btn("...", async () => { const r = await chooseGenerals(label, asList(ov[key])); if (r !== null) { setOv(key, r); e.value = r.join(", ") || "(none)"; } }, "sm"),
        btn("x", () => { setOv(key); e.value = ""; }, "sm"));
    } else {
      const toText = (v) => (v === undefined ? "" : kind === "presets" ? v.join(",") : kind === "names" ? v.join(", ") : kind === "range" ? v.map((x) => +x.toFixed(2)).join(",")
        : kind === "monpresets" ? Object.entries(v).map(([n, p]) => `${n}:${p.join(",")}`).join("; ") : String(v));
      control = h("input", { type: kind === "int" ? "number" : "text", value: toText(cur), placeholder: "Using universal (" + globalText(key, kind) + ") \u2013 type to change" });
      control.addEventListener("input", () => {
        const t = control.value.trim();
        if (t === "" || t === "(universal)") return setOv(key);
        try {
          let v;
          if (kind === "int") { v = parseInt(t, 10); if (!(v >= 0)) throw new Error(); }
          else if (kind === "presets") { v = parsePresets(t); if (!v.length) throw new Error(); }
          else if (kind === "names") v = parseNames(t);
          else if (kind === "range") v = parseRange(t);
          else if (kind === "monpresets") v = parseMonPresets(t);
          else v = t;
          setOv(key, v, control);
        } catch (e) { control.style.outline = "2px solid var(--bad)"; }
      });
    }
    const tipText = FIELD_HELP[key] || "";
    if (tipText) { control.title = tipText; if (control.querySelector) control.querySelectorAll("input,select").forEach((c) => (c.title = tipText)); }
    const fe = fld(label, control);
    if (tipText) fe.appendChild(h("div", { class: "hint", style: { fontSize: "11.5px", opacity: ".65", marginTop: "3px", lineHeight: "1.35" } }, tipText));
    (BASIC.has(key) ? fields : moreFields).appendChild(fe);
  }
  const HIDDEN_X = new Set(["bot_presets", "max_troops"]);
  const extras = Object.fromEntries(Object.entries(ov).filter(([k]) => !IF_KEYS.has(k) && !HIDDEN_X.has(k)));
  let extraKeys = Object.keys(extras);
  const adv = h("textarea", { placeholder: '{"threshold": 0.8, "march_tap": [410, 910]}' });
  adv.value = extraKeys.length ? JSON.stringify(extras) : "";
  adv.addEventListener("input", () => {
    const t = adv.value.trim();
    try {
      const o = t ? JSON.parse(t) : {};
      if (typeof o !== "object" || Array.isArray(o)) throw new Error();
      extraKeys.forEach((k) => delete ov[k]);
      Object.assign(ov, o);
      extraKeys = Object.keys(o);
      adv.style.outline = "";
    } catch (e) { adv.style.outline = "2px solid var(--bad)"; }
  });
  const copyMenu = (e) => {
    const others = S.devices.filter((x) => x.id && x.id !== serial);
    if (!others.length) return toast("There is no other instance.");
    menu(e.currentTarget, others.map((o) => ({ label: o.name || o.id, run: () => { S.cfg.per_device[serial] = clone(S.cfg.per_device[o.id] || {}); rerender(); } })));
  };
  const panelEl = panel("Instance settings", note("Blank or (universal) = use the universal setting from Control settings."), fields,
    h("div", { class: "row", style: { marginTop: "14px" } }, btn("Copy settings from...", copyMenu, "sm"),
      btn("Use these on ALL instances", () => { for (const x of S.devices) if (x.id && x.id !== serial) S.cfg.per_device[x.id] = clone(S.cfg.per_device[serial] || {}); toast("Copied to all other instances.", "ok"); }, "sm"),
      btn("Clear (use universal)", () => { S.cfg.per_device[serial] = {}; rerender(); }, "sm")));
  // ---- own presets & generals (stored in this instance's overrides; blank = the universal ones)
  const PKEYS = ["bot_presets", "preset_mode", "general_main", "general_assistant", "general_main_tab", "general_assistant_tab", "general_main_fav",
    "general_assistant_fav", "general_on_fail", "general_idle_only", "general_assistant_optional", "general_view", "max_troops"];
  const customP = "bot_presets" in ov;
  const pview = new Proxy(ov, { get: (t, k) => (k in t ? t[k] : S.cfg[k]), set: (t, k, v) => { t[k] = v; return true; } });
  const swp = h("input", { type: "checkbox", checked: customP, "aria-label": "Own presets and generals for this instance" });
  swp.addEventListener("change", () => {
    if (swp.checked) { ensureBotPresets(S.cfg); PKEYS.forEach((k) => { if (S.cfg[k] !== undefined) ov[k] = clone(S.cfg[k]); }); }
    else PKEYS.forEach((k) => delete ov[k]);
    rerender();
  });
  const ownP = [panel("Presets and generals for this instance",
    h("label", { class: "tgl" }, swp, h("span", { class: "sw" }), "Use its own presets and generals (otherwise it uses the ones on the Presets & generals tab)"),
    customP ? null : note("Now using the universal presets and generals. Switch this on to give this instance its own set; it starts as a copy of the universal one."))];
  if (customP) ownP.push(...presetPanels(pview, true));
  // ---- phone / shared device (important, so it sits right under Activity)
  const eff = (k, dflt) => (ov[k] !== undefined ? ov[k] : S.cfg[k] !== undefined ? S.cfg[k] : dflt);
  const rcOn = h("input", { type: "checkbox", checked: !!eff("reclaim_elsewhere", true) });
  rcOn.addEventListener("change", () => { ov.reclaim_elsewhere = rcOn.checked; });
  const rcMin = h("input", { type: "number", min: 0, class: "short", placeholder: "Universal " + eff("reclaim_grace_min", 10) + " \u2013 type to change" });
  rcMin.value = ov.reclaim_grace_min === undefined ? "" : String(ov.reclaim_grace_min);
  rcMin.addEventListener("input", () => { const t = rcMin.value.trim(); if (t === "") delete ov.reclaim_grace_min; else ov.reclaim_grace_min = Math.max(0, parseInt(t, 10) || 0); });
  const phone = panel("When I Play on Another Device",
    h("div", { class: "hint", style: { marginBottom: "10px" } }, "Opening the game on another device kicks this instance off for your main device. With this option ON, the bot shows a countdown of the time remaining before it taps \u201cRestart\u201d inside the game (the Evony pop-up that says the account was opened elsewhere), then carries on. Choose the number of minutes to wait, or leave it blank to use the universal value (currently " + (S.cfg.reclaim_grace_min !== undefined ? S.cfg.reclaim_grace_min : 10) + " minutes)."),
    h("div", { class: "row", style: { gap: "22px", alignItems: "center" } },
      h("label", { class: "tgl" }, rcOn, h("span", { class: "sw" }), "Wait, then take the game back"),
      h("span", {}, "Wait "), rcMin, h("span", {}, " minutes"),
      btn("Take game back now", () => act(api("takeback", { serial })))));
  // ---- the game's own Auto-Join (needs the Rally Monthly Card): per instance, right under the "game kicked" box
  const numOv = (key, dflt) => { const e = h("input", { type: "number", min: 0, class: "short", placeholder: "Universal " + eff(key, dflt) + " \u2013 type to change" });
    e.value = ov[key] === undefined ? "" : String(ov[key]);
    e.addEventListener("input", () => { const t = e.value.trim(); if (t === "") delete ov[key]; else ov[key] = Math.max(0, parseInt(t, 10) || 0); }); return e; };
  const ajOn = h("input", { type: "checkbox", checked: !!eff("game_auto_join", false) });
  ajOn.addEventListener("change", () => { ov.game_auto_join = ajOn.checked; });
  const autoJoin = panel("Auto-Join (the game's own, needs the Rally Monthly Card)",
    h("div", { class: "hint", style: { marginBottom: "10px" } }, "On: the bot stops joining rallies itself. It opens Alliance War > Auto-Join, reads the time left and presses Start again when it falls under your limit. Only turn it on for accounts that have the card. Leave the minutes blank to use the universal value."),
    h("div", { class: "row", style: { gap: "22px", alignItems: "center", flexWrap: "wrap" } },
      h("label", { class: "tgl" }, ajOn, h("span", { class: "sw" }), "Use the game's Auto-Join"),
      h("span", {}, "Renew when less than "), numOv("auto_join_renew_min", 120), h("span", {}, " min are left"),
      h("span", {}, "Check every "), numOv("auto_join_check_min", 10), h("span", {}, " min")),
    note("Time left and the next renew show on this instance's Overview, in the Today card."));
  const asOn = h("input", { type: "checkbox", checked: !!eff("auto_start", false) });
  asOn.addEventListener("change", () => { ov.auto_start = asOn.checked; });
  const autoStart = panel("Start by itself",
    h("div", { class: "hint", style: { marginBottom: "10px" } }, "ON: this instance starts on its own whenever the app opens (after a restart or update too). OFF: it waits until you press Start. Each instance has its own switch."),
    h("div", { class: "row", style: { gap: "22px", alignItems: "center" } },
      h("label", { class: "tgl" }, asOn, h("span", { class: "sw" }), "Start this instance automatically when the app opens")));
  // ---- tabs (same look as the Control settings tabs). The chosen tab is remembered, also when another instance is opened.
  // section switcher inside one screen: pills on top, one section at a time (less scrolling)
  const sections = (key, list) => {
    S.sec = S.sec || {};
    if (!list.some((x) => x[0] === S.sec[key])) S.sec[key] = list[0][0];
    const wrapS = h("div", {}), pills = h("div", { class: "subnav" }), area = h("div", {});
    const show = (k) => {
      S.sec[key] = k;
      [...pills.children].forEach((x) => x.classList.toggle("on", x.dataset.k === k));
      area.innerHTML = "";
      const el = list.find((x) => x[0] === k)[2]();
      for (const e of (Array.isArray(el) ? el : [el])) if (e) area.appendChild(e);
    };
    list.forEach(([k, l]) => pills.appendChild(h("button", { class: "sub", "data-k": k, onclick: () => show(k) }, l)));
    wrapS.append(pills, area); show(S.sec[key]);
    return [wrapS];
  };
  const mapPanel = () => panel("Map the city",
    note("The bot needs to know where the buildings are. Scan city reads the names while it holds a finger on the screen. Map by hand is the manual way when scanning does not fit your city style."),
    h("div", { class: "row", style: { gap: "8px", flexWrap: "wrap" } }, btn("Scan city", () => scanCityFlow(serial)), btn("Map by hand", () => manualMapFlow(serial))));
  // Settings tab: section menu on the left (Advanced dimmed, last), Save / Discard bar pinned to the bottom
  const settingsTab = () => {
    const secs = [
      ["phone", "Phone and start", () => [phone, autoStart]],
      ["inst", "Instance settings", () => [panelEl]],
      ["adv", "Advanced", () => [panel("Advanced (rarely needed)", moreFields,
        h("div", { class: "field", style: { marginTop: "14px" } }, h("label", {}, "Any other setting as JSON"), adv,
          h("div", { class: "hint" }, "Only for settings that have no field above, e.g. {\"threshold\": 0.8}. Leave empty if unsure.")))]],
    ];
    S.sec = S.sec || {};
    if (!secs.some((x) => x[0] === S.sec.settings)) S.sec.settings = "phone";
    const menuEl = h("nav", { class: "setnav" }), area = h("div", { class: "setarea" });
    const show = (k) => {
      S.sec.settings = k;
      [...menuEl.children].forEach((x) => x.classList.toggle("on", x.dataset.k === k));
      area.innerHTML = "";
      for (const e of secs.find((x) => x[0] === k)[2]()) area.appendChild(e);
    };
    secs.forEach(([k, l]) => menuEl.appendChild(h("button", { class: "setlnk" + (k === "adv" ? " dim" : ""), "data-k": k, onclick: () => show(k) }, l)));
    show(S.sec.settings);
    const bar = h("p", { class: "hint", style: { marginTop: "12px" } }, "These settings apply to " + (dev.name || short(serial)) + " only.");   // saving + Undo: top bar
    return [h("div", { class: "setgrid" }, menuEl, h("div", {}, area, bar))];
  };
  const ITABS = [
    ["ov", "Overview", () => {
      const a = activityBox(serial, "Now executing");
      const L = a.querySelector(".actleft"), R = a.querySelector(".actright");
      const st = (S.devStatus.find((x) => x.id === serial) || {}).state;
      L.querySelector("h3").replaceWith(h("div", { class: "cardhead" },
        h("h3", {}, h("span", { class: "livedot" + (st === "run" ? " on" : "") }), "Now executing"), h("span", { class: "livetag" }, "Live")));
      const lab = L.querySelector(".actlab"); if (lab) lab.remove();
      R.querySelector("h3").replaceWith(h("div", { class: "cardhead" },
        h("div", {}, h("h3", {}, "Monster joins"), h("small", { class: "sub" }, "Live rally history log")),
        h("a", { href: "#", class: "viewall", onclick: (e) => { e.preventDefault(); if (S.gotoInstTab) S.gotoInstTab("logs"); } }, "View all \u2192")));
      return [renderTodayCard(serial), h("div", { class: "dutygrid ovgrid" }, h("div", { class: "dcol" }, panel(null, L), panel(null, R)), h("div", { class: "dcol" }, PAGES.duties(serial, null, true)))];
    }],
    ["routines", "Routines", () => [routinesCards(serial, ov, {
      duties: () => [PAGES.duties(serial)], tasks: () => [PAGES.tasks(serial)], market: () => [PAGES.market()],
      aj: () => [autoJoin], map: () => [mapPanel()], rally: () => { if (S.gotoInstTab) S.gotoInstTab("rally"); return null; } })]],
    ["rally", "Rally setup", () => sections("rally", [
      ["mon", "Monsters", () => [PAGES.monsters(serial)]],
      ["gen", "March presets & generals", () => ownP],
      ["sta", "Stamina", () => [staminaPanel(ov, S.cfg)]]])],
    ["mrally", "Monster rallies", () => [monsterRallyTab(serial)]],
    ["logs", "Itinerary & Logs", () => [h("div", { class: "loggrid" }, instItinerary(serial), instHistory(serial))]],
    ["settings", "Settings", settingsTab],
  ];
  const OLD_TAB = { dash: "ov", activities: "routines", rallies: "rally", city: "routines", itinerary: "logs", history: "logs" };
  if (!S.instTab) { try { S.instTab = localStorage.getItem("rf_inst_tab") || ""; } catch (e) { S.instTab = ""; } }
  S.instTab = OLD_TAB[S.instTab] || S.instTab;
  if (!ITABS.some((t) => t[0] === S.instTab)) S.instTab = "ov";
  const body = h("div", {});
  const tabRow = h("div", { class: "tabs", style: { margin: "4px 0 0" } });
  const showTab = (k) => {
    S.instTab = k;
    try { localStorage.setItem("rf_inst_tab", k); } catch (e) {}
    [...tabRow.children].forEach((x) => x.classList.toggle("on", x.dataset.k === k));
    body.innerHTML = "";
    if (k !== "ov") {
      const nv = h("span", { class: "nowtxt" }, actNow(serial));
      body.appendChild(h("div", { class: "nowstrip" }, h("span", { class: "actlab" }, "Now"), nv));
      const iv = setInterval(() => { if (!nv.isConnected) clearInterval(iv); else nv.textContent = actNow(serial); }, 2000);
    }
    const t = ITABS.find((x) => x[0] === k);
    for (const el of t[2]()) if (el) body.appendChild(el);
  };
  for (const [k, l] of ITABS) tabRow.appendChild(h("button", { class: "tab", "data-k": k, onclick: () => showTab(k) }, l));
  const wrap = h("div", {}, h("div", { class: "stickyhead" }, h("div", { class: "stickybox" }, head, warn, tabRow)), body);
  S.gotoInstTab = showTab;
  showTab(S.instTab);
  setTimeout(refreshInstanceControls, 0);
  return wrap;
}
// ====================================================================== per-instance Itinerary and Log history (read only)
function instItinerary(serial) {
  const root = h("div", {});
  const fmtT = (t) => new Date(t * 1000).toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  const grp = (title, rows, bad) => rows.length ? h("div", { class: "itgrp" + (bad ? " bad" : "") }, h("div", { class: "ithead" }, title + " (" + rows.length + ")"),
    ...rows.map((r) => h("div", { class: "itrow" }, h("span", { class: "itwhen" }, r.when), h("span", { class: "itname" }, r.name)))) : null;
  const load = async () => {
    if (!root.isConnected) return;
    const j = await api("task_history", {}, { quiet: true }).catch(() => null);
    if (!j) return;
    const row = (j.matrix || []).find((m) => m.id === serial);
    const d = (row && row.duties) || [];
    const mk = (f) => d.filter(f).map((x) => ({ when: x.text, name: x.title }));
    const mine = (j.events || []).filter((e) => row && e.emu === row.emu).slice(0, 12).map((e) => ({ when: fmtT(e.t), name: e.task + (e.ok ? "" : " - failed" + (e.msg ? ": " + e.msg : "")) }));
    root.innerHTML = "";
    const parts = [grp("Overdue - waiting to run", mk((x) => x.status === "due" || x.status === "retry"), true), grp("Failing - tried again every hour", mk((x) => x.status === "gaveup"), true),
      grp("Coming up", mk((x) => x.status === "waiting")), grp("Done today", mk((x) => x.status === "done")), grp("Latest things the bot did here", mine)].filter(Boolean);
    root.appendChild(panel("Itinerary",
      note("What this instance has to do, in plain order. Red = late or given up. Switch things on or off under Duties and Tasks; changes are picked up without stopping the instance."),
      row && !row.tasks ? h("div", { class: "chip pill-bad", style: { margin: "6px 0" } }, "Tasks are off for this instance") : null,
      ...(parts.length ? parts : [note("Nothing is switched on yet. Turn duties on under Duties and they appear here.")])));
  };
  root.appendChild(note("Loading..."));
  setTimeout(load, 0);
  const t = setInterval(() => { if (!root.isConnected) clearInterval(t); else load(); }, 10000);
  return root;
}
function instHistory(serial) {
  const root = h("div", {});
  const q = h("input", { type: "search", placeholder: "Search this instance's log...  (for example: stamina, joined, kicked)", style: { width: "100%", marginBottom: "8px" } });
  const only = h("label", { class: "tgl", style: { marginBottom: "8px" } }, h("input", { type: "checkbox" }), h("span", { class: "sw" }), "Only problems");
  const body = h("div", { class: "histlog" });
  const draw = () => {
    if (!root.isConnected) return;
    const f = q.value.trim().toLowerCase(), bad = only.querySelector("input").checked;
    const rows = S.logs.filter((l) => l.s === serial && (!f || l.t.toLowerCase().includes(f)) && (!bad || l.t.startsWith("[!]") || l.t.startsWith("[-]"))).slice(-400).reverse();
    body.innerHTML = "";
    if (!rows.length) { body.appendChild(note("No lines yet" + (f || bad ? " for this search." : "."))); return; }
    for (const l of rows) body.appendChild(h("div", { class: "hrow " + lineClass(l.t) }, l.t));
  };
  q.addEventListener("input", draw); only.querySelector("input").addEventListener("change", draw);
  const dev = S.devices.find((d) => d.id === serial) || {};
  root.appendChild(panel("Log history - this instance", saveLogRow(dev.name || short(serial)), h("div", { style: { marginTop: "12px" } }, q, only), body));
  draw();
  const t = setInterval(() => { if (!root.isConnected) clearInterval(t); else draw(); }, 3000);
  return root;
}

function refreshInstanceControls() {
  const s = $("#instStatus");
  if (!s || S.sel === "control") return;
  const d = S.devStatus.find((x) => x.id === S.sel);
  if (d) {
    const G = { run: "\u25B6 ", pause: "\u275A\u275A ", stop: "\u25A0 ", error: "! ", idle: "\u25CB " };
    const kk = d.state === "run" && d.kicked_at;
    const ns = d.state === "run" && d.no_stamina && !kk;
    s.className = "state-pill st-" + ((ns || kk) ? "pause" : d.state);
    s.textContent = kk ? "\u26A0 Kicked \u00b7 game opened on another device" : ns ? "\u26A0 No stamina" + (d.joins ? ` \u00b7 joins ${d.joins}` : "") : (G[d.state] || "") + STATE_TXT[d.state] + (d.queued ? " (queued)" : "") + (d.joins ? ` \u00b7 joins ${d.joins}` : "");
    const w = $("#instWarn"); if (w) w.style.display = ns ? "" : "none";
    if (kk) { s.appendChild(h("span", { class: "ptimer", "data-at": String(d.kicked_at), "data-pre": " \u00b7 taking it back in " })); if (typeof tickPauseTimers === "function") tickPauseTimers(); }
    if (d.state === "pause" && d.resume_at) { s.appendChild(h("span", { class: "ptimer", "data-at": String(d.resume_at), "data-pre": " - runs again in " })); if (typeof tickPauseTimers === "function") tickPauseTimers(); }
  }
  if (typeof paintRunState === "function") paintRunState();
}

// ====================================================================== presets & generals
function ensureBotPresets(c) {
  c = c || S.cfg;
  const saved = {};
  (c.bot_presets || []).forEach((b, i) => { if (b && typeof b === "object") saved[b.n || i + 1] = b; });
  if (!Object.keys(saved).length) {
    const on = new Set(c.presets || [1]);
    S.meta.botPresets.forEach((b) => (saved[b.n] = { ...b, enabled: on.has(b.n) }));
  }
  c.bot_presets = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => {
    const b = saved[n] || {};
    return { n, enabled: b.enabled ?? n === 1, skip_if_no_general: b.skip_if_no_general ?? true, pick_generals: b.pick_generals ?? false, assistant_optional: b.assistant_optional ?? false,
      max_troops: b.max_troops ?? "", main: asList(b.main), assistant: asList(b.assistant) };
  });
}
function presetPanels(c, inst) {
  ensureBotPresets(c);
  if (c.general_main_tab == null || c.general_main_tab === "") c.general_main_tab = c.general_tab || 0;
  if (c.general_assistant_tab == null || c.general_assistant_tab === "") c.general_assistant_tab = c.general_tab || 0;
  if (!c.general_main_fav) c.general_main_fav = c.general_favourites || "leave";
  if (!c.general_assistant_fav) c.general_assistant_fav = c.general_favourites || "leave";
  const armed = c.bot_presets.filter((b) => b.enabled).length;
  // ---- one card per preset that is on; the ones that are off are small numbered chips (click = on)
  const card = (b) => h("div", { class: "pcard" },
    h("div", { class: "phead" }, h("span", { class: "pbadge" }, String(b.n)), h("b", {}, "Preset " + b.n),
      h("button", { class: "px", type: "button", title: "Turn preset " + b.n + " off", "aria-label": "Turn preset " + b.n + " off", onclick: () => { b.enabled = false; rerender(); } }, "\u2715")),
    segPick(b, "pick_generals", [[false, "Game's generals", "March with the generals saved in the game preset - nothing is searched"], [true, "My list", "Use the generals you list here"]], { small: true, label: "Generals for preset " + b.n, after: () => rerender() }),
    b.pick_generals
      ? h("div", { class: "pgens" }, h("div", { class: "lab" }, "Main"), genField(b, "main", `Preset ${b.n} - main general`),
          h("div", { class: "lab" }, "Assistant"), genField(b, "assistant", `Preset ${b.n} - assistant general`),
          tog(b, "assistant_optional", "Join without an assistant if none is free"))
      : h("p", { class: "hint pnote" }, "Marches with the generals already saved in this game preset."),
    tog(b, "skip_if_no_general", "Skip if a general slot is empty"));
  const off = c.bot_presets.filter((b) => !b.enabled);
  const offRow = off.length ? h("div", { class: "poff" }, h("span", { class: "lab" }, armed ? "Turn on" : "Turn on a preset"),
    ...off.map((b) => h("button", { class: "pchip", type: "button", title: "Turn preset " + b.n + " on", onclick: () => { b.enabled = true; rerender(); } }, "+ " + b.n)),
    off.length > 1 ? h("button", { class: "lnkb", type: "button", onclick: () => { c.bot_presets.forEach((b) => (b.enabled = true)); rerender(); } }, "Turn all on") : null) : null;
  const PRESET_HELP = "The bot marches with these game presets. 'Start from first' always tries the lowest preset that is on first; 'Rotate' takes them in turn; 'Random' picks any. 'Game's generals' marches with the generals saved in the game preset; 'My list' searches for the generals you list (if the preset already holds one of them it just marches). 'Skip if a general slot is empty' moves on to the next preset.";
  const bp = panel(null,
    h("div", { class: "cardhead" }, h("h3", {}, "Join presets"), infoTip("Presets", PRESET_HELP), h("span", { style: { flex: "1" } }), h("span", { class: "armed" }, `${armed} of 8 on`)),
    h("div", { class: "dhead", style: { marginBottom: "10px" } }, tog(c, "game_auto_join", "Use the game's Auto-Join", { after: () => rerender() }),
      infoTip("Game's Auto-Join", "Needs the Rally Monthly Card. When on, the bot stops joining rallies itself: it opens Alliance War > Auto-Join, reads the time left and presses Start again when it falls under your limit (Start resets it to 8 hours). Only some accounts have the card? Switch it on per instance on that instance's own page.")),
    c.game_auto_join ? h("div", { class: "fgrid", style: { marginBottom: "14px" } },
      fld("Renew when less than (min) is left", inp(c, "auto_join_renew_min", { type: "int" })),
      fld("Check every (min)", inp(c, "auto_join_check_min", { type: "int" }))) : null,
    h("div", { class: "dhead", style: { marginBottom: "12px" } }, h("span", { class: "lab" }, "Order"),
      segPick(c, "preset_mode", [["fixed", "Start from first"], ["rotate", "Rotate"], ["random", "Random"]], { def: "rotate", label: "Preset order" })),
    armed ? h("div", { class: "pgrid" }, c.bot_presets.filter((b) => b.enabled).map(card))
      : h("div", { class: "empty" }, h("b", {}, "No preset is on"), h("span", {}, "Turn on at least one preset below - the bot marches with it.")),
    offRow,
    h("div", { class: "fgrid", style: { marginTop: "16px" } }, fld("Skip a preset that already holds more than this many troops (0 = off)", inp(c, "max_troops", { type: "int" }))));
  // ---- how the bot finds generals in the game's 'Select a General' list
  const GEN_HELP = "If the first general is not in the list, is busy or cannot be selected, the bot tries the next one. Generals are found by their picture (taken in the thumbnail view of 'Select a General'). Search in: the bot opens that tab of the list; Auto uses the tab each general belongs to. If a general is not in that tab, the bot also looks in All.";
  const gen = panel(null,
    h("div", { class: "cardhead" }, h("h3", {}, "Generals"), infoTip("Generals", GEN_HELP)),
    grid2(fld("Main generals, best first", genField(c, "general_main", "Main generals")), fld("Assistant generals, best first", genField(c, "general_assistant", "Assistant generals"))),
    h("div", { class: "gsearch2" },
      h("span", {}), h("span", { class: "lab" }, "Search in"), h("span", { class: "lab" }, "Favourites filter"),
      h("span", { class: "lab rowlab" }, "Main"), segPick(c, "general_main_tab", GEN_TABS, { small: true, def: 0, label: "Main generals: search in" }), segPick(c, "general_main_fav", GEN_FAVS, { small: true, def: "leave", label: "Main generals: favourites filter" }),
      h("span", { class: "lab rowlab" }, "Assistant"), segPick(c, "general_assistant_tab", GEN_TABS, { small: true, def: 0, label: "Assistant generals: search in" }), segPick(c, "general_assistant_fav", GEN_FAVS, { small: true, def: "leave", label: "Assistant generals: favourites filter" })),
    h("div", { class: "fgrid2" },
      fld("If none of the listed generals works", segPick(c, "general_on_fail", [["skip", "Skip the rally"], ["march", "March anyway", "March with the generals the preset holds"]], { small: true, def: "skip" })),
      fld("Find generals by", segPick(c, "general_view", [["grid", "Picture", "Thumbnail view - faster and more reliable"], ["list", "Name", "List view - reads the names"]], { small: true, def: "grid" }))),
    h("div", { class: "row", style: { marginTop: "14px", gap: "26px" } }, tog(c, "general_idle_only", "Only pick generals that are Idle"),
      tog(c, "general_assistant_optional", "No assistant free: join with the main general only")),
    inst ? null : h("div", { class: "cardfoot" }, btn("Manage generals\u2026", openGeneralManager), S.dev ? btn("Scan generals from the game\u2026", scanGeneralsFlow) : null,
      S.dev ? btn("Build general catalogue (developer)\u2026", catalogFlow) : null, S.dev ? btn("Open catalogue folder", () => act(api("catalog_open")), "sm") : null,
      S.dev ? btn("Record city frames (developer)\u2026", cityCaptureFlow) : null, S.dev ? btn("Open city capture folder", () => act(api("city_capture_open")), "sm") : null,
      S.dev ? btn("Add general from screenshot\u2026", () => addGeneralFlow()) : null, S.dev ? btn("Capture a button / pop-up from screenshot\u2026", captureTemplate) : null));
  return [bp, gen];
}
PAGES.presets = function () { return h("div", {}, ...presetPanels(S.cfg, false)); };
async function captureTemplate() {
  const shot = await pickScreenshot("Screenshot containing the button");
  if (!shot) return;
  const rect = await pickOnShot(shot, "Drag a box tightly around the button or the pop-up's close button, then let go.", "region");
  if (!rect) return;
  const name = await promptBox("Template name", "Name for this picture.\n\npopup_anything  = a pop-up button the bot taps whenever it sees it (e.g. popup_ok)\nrally_icon, alliance_button, war_button = steps to reach the rally list\nassistant_card = the Assistant General slot on the march screen\nmarch_screen, city_screen, rally_screen = screens the bot can recognise\nfav_off = the heart filter when it is OFF\nno_general = the EMPTY general slot (presets showing it are skipped)\npopup_exit_cancel = Cancel on the 'quit game' dialog", "");
  if (!name) return;
  const j = await act(api("template_add", { token: shot.token, rect, name }));
  if (j) toast(`Saved template '${j.name}'`, "ok");
}

// ====================================================================== settings (+ low RAM)
function staminaPanel(store, base) {
  // base = where the value comes from when this instance has none of its own (the universal settings)
  const g = (k, d) => (store[k] !== undefined ? store[k] : base[k] !== undefined ? base[k] : d);
  const S_ = (k, opts, d) => sel(store, k, opts, { get: () => g(k, d) });
  return panel("Stamina (when a march needs more than you have)",
    h("div", { class: "hint", style: { marginBottom: "10px" } }, "The bot uses stamina ITEMS only (never gems). Choose which items it may use, in which order, and how many."),
    h("div", { style: { marginBottom: "10px" } }, tog({ stamina_auto: g("stamina_auto", true) }, "stamina_auto", "Use stamina items automatically", { after: (v) => { store.stamina_auto = v; } })),
    (() => {
      const cntFld = fld("Exact number of items", inp(store, "stamina_count", { type: "int", cls: "short", get: (v) => g("stamina_count", 2) }));
      const showCnt = () => { cntFld.style.display = g("stamina_amount", "full (Max)") === "exact number" ? "" : "none"; };
      const amt = sel(store, "stamina_amount", [["full (Max)", "Full - slide to the end (up to 9,999)"], "game default", "exact number"],
                      { get: () => g("stamina_amount", "full (Max)"), after: showCnt });
      showCnt();
      return grid2(
        fld("Items it may use", S_("stamina_items", ["all", "100 only", "50 only", "10 only", "50 + 100"], "all")),
        fld("Order", S_("stamina_order", ["large first", "small first"], "large first")),
        fld("How many", amt),
        cntFld);
    })(),
    note("Full = slides the bar to the very end, so stamina fills up to the game's limit (9,999) - the default. The game's own Max button is not used (it stops at 100). Game default = the amount the game suggests (2 items). Exact number = the number of items you type."));
}
PAGES.settings = function () {
  const r = PAGES.settings0(); r.appendChild(panel("Tour", note("A short walkthrough of where to click and how to set up your presets and start the bot."), h("div", { class: "row" }, btn("Take the tour", () => startTour())))); return r;                  // themes live on their own Themes tab only
};
PAGES.settings0 = function () {
  const c = S.cfg;
  // (ui-simple) plain groups; same settings keys and saved values - only the labels, order and choice names changed
  const sec = (title, ...kids) => h("div", { class: "sgroup" }, h("h4", {}, title), ...kids);
  const g = panel("Settings for every instance",
    h("p", { class: "hint", style: { margin: "-4px 0 6px" } }, "An instance with its own settings (on its page) keeps those."),
    sec("Joining rallies",
      h("div", { class: "fgrid" },
        fld("Join only with this much time to spare (s)", inp(c, "safety_margin", { type: "int" })),
        fld("Skip rallies longer than (s, 0 = any)", inp(c, "max_rally_seconds", { type: "int" })),
        fld("Most joins per instance (0 = no limit)", inp(c, "max_joins", { type: "int" })),
        fld("My march time if it can't be read (s)", inp(c, "march_guess", { type: "int", get: (v) => (v === undefined ? 10 : v) })),
        fld("Read the rally countdown from", sel(c, "rally_timer_source", [["progress_bar", "Progress bar"], ["join_button", "Join button"]])),
        fld("Read my march time from", sel(c, "march_timer_source", [["join_button", "Join button"], ["progress_bar", "Progress bar"], ["none", "Don't read it"]], { get: (v) => v || "none", set: (v) => (v === "none" ? "" : v) })))),
    sec("Speed",
      h("div", { class: "fgrid" },
        fld("Load on the PC", sel(c, "cpu_level", [[1, "Fastest"], [2, "Balanced"], [3, "Light"], [4, "Lightest"]], { num: true, get: (v) => v || 2 })),
        fld("Wait after each tap (ms)", inp(c, "click_delay_ms", { type: "int" })),
        fld("Pause lasts (min, 0 = until I resume)", inp(c, "pause_minutes", { type: "int", get: (v) => (v === undefined ? 15 : v) })))),
    sec("Starting up",
      h("div", { class: "tgls" }, tog(c, "auto_launch_emulator", "Start the emulator if it is closed"), tog(c, "auto_launch_game", "Open Evony and wait until it has loaded"))),
    h("details", { class: "adv" }, h("summary", {}, "Advanced"),
      h("div", { class: "fgrid", style: { marginTop: "12px" } },
        fld("ADB path", inp(c, "adb_path")), fld("Evony package name", inp(c, "evony_package")),
        fld("Presets when no join presets are set (1-8)", inp(c, "presets", { get: (v) => (v || []).join(","), set: (t) => parsePresets(t) || [1] })),
        fld("Preset order", sel(c, "preset_mode", [["fixed", "Start from first"], ["rotate", "Rotate presets"], ["random", "Random"]])),
        fld("Buttons to tap to reach the rally list", inp(c, "rally_nav_steps", { get: (v) => (v || []).join(", "), set: (t) => parseNames(t) })),
        fld("Screenshot method", sel(c, "screenshot_mode", [["auto", "Automatic"], ["png", "PNG (slower, safer)"]]))),
      h("div", { class: "tgls", style: { marginTop: "12px" } }, tog(c, "log_timers", "Write the timers it reads to the log")),
      note("Spare time = rally countdown minus your march time. 'Buttons to tap' are template names (default rally_icon); add more, e.g. alliance_button, war_button, if the rally list needs several taps. Any template named popup_something is tapped whenever it appears. Click delay scales every pause after a click; below ~500 ms the bot may click before a screen has loaded. A lighter load level scans less often.")));
  const lr = panel("Running many instances", h("div", { id: "ramInfo", class: "hint", style: { marginBottom: "10px" } }, "PC memory: ..."),
    tog(c, "low_memory", "Low-memory mode for a weak PC"),
    h("div", { style: { marginBottom: "8px" } }, tog(c, "keep_running_on_close", "Keep the bots running when I close this window")),
    h("div", { style: { marginBottom: "12px" } }, tog(c, "auto_start", "Start all instances when the app opens")),
    h("div", { class: "fgrid" }, fld("Wait between instance starts (s)", inp(c, "start_gap_seconds", { type: "int", cls: "short" })),
      fld("Most instances at once (0 = no limit)", inp(c, "max_running_emulators", { type: "int", cls: "short" })),
      fld("Free RAM needed to start one (MB, 0 = off)", inp(c, "min_free_ram_mb", { type: "int", cls: "short" }))),
    h("div", { style: { marginTop: "12px" } }, tog(c, "close_emulator_on_stop", "Close the emulator when its instance stops (frees RAM)")),
    note("The bot itself needs roughly 100 MB; the instances are what fill the RAM (about 1.5-4 GB each). Extra instances beyond the limit wait in a queue and start when another one stops. Set the instances to 540x960, 2 GB or less of memory and 1-2 CPU cores in their own settings. Closing this window frees even more memory: the bots keep running."));
  // Beta: every user can record a task (taps, swipes + pictures) and send it, so new duties can be built from it
  const rec = panel("Record a task (beta)", h("div", { class: "hint", style: { marginBottom: "10px" } },
      "Play a task by hand in the game while this records your taps and swipes with a small picture of each step. Give it a clear title (for example \u201cGold Levy\u201d or \u201cAlliance treasure\u201d), then send the zip to the developer."),
    h("div", { class: "row" }, btn("Record a task...", flowRecordFlow, "sm pri"),
      btn("Save recordings as zip", async () => { const j = await act(api("flow_zip")); if (j) toast(`Saved ${j.flows} recording(s) in Downloads: ${j.path}`, "ok"); }, "sm"),
      btn("Open recordings folder", () => act(api("flow_open")), "sm")));
  setTimeout(updateSummary, 0);
  return h("div", {}, g, staminaPanel(c, c), lr, rec);
};


// ---- per-instance scope for the Monsters / Tasks pages: returns {c, bar, own, locked}
S.scope = S.scope || {};
function scopeBar(page, keys, what, fixed) {
  const serial = fixed !== undefined ? fixed : (S.scope[page] || "");
  S.cfg.per_device = S.cfg.per_device || {};
  const ov = serial ? (S.cfg.per_device[serial] = S.cfg.per_device[serial] || {}) : null;
  const own = !!ov && keys.some((k) => ov[k] !== undefined);
  const pick = h("select", { onchange: (e) => { S.scope[page] = e.target.value; rerender(); } },
    [h("option", { value: "", selected: serial === "" }, "All instances (default)")].concat((S.devices || []).filter((d) => d.id).map((d) => h("option", { value: d.id, selected: d.id === serial }, d.name || d.id))));
  const items = fixed !== undefined ? [] : [h("span", { class: "lab" }, "Settings for"), pick];
  if (serial) {
    const toOwn = () => { keys.forEach((k) => { if (S.cfg[k] !== undefined) ov[k] = clone(S.cfg[k]); }); rerender(); };
    const toGlobal = async () => { if (await confirmBox("Use the universal " + what, "This instance's own " + what + " will be discarded and it will use the universal one again. Continue?")) { keys.forEach((k) => delete ov[k]); rerender(); } };
    items.push(h("div", { class: "seg ownseg" },
      h("button", { class: own ? "" : "on", onclick: () => { if (own) toGlobal(); } }, "Universal " + what),
      h("button", { class: own ? "on" : "", onclick: () => { if (!own) toOwn(); } }, "Own " + what + " for this instance")));
  }
  const msg = !serial ? "These are the defaults for every instance that has no own " + what + "."
    : own ? "Changes here apply to this instance only."
    : "This instance uses the universal " + what + " (shown below, read-only). Press the button to give it its own.";
  const bar = fixed !== undefined ? h("div", {}, h("div", { class: "row", style: { marginBottom: "8px" } }, ...items), note(msg)) : panel("Which instance?", h("div", { class: "row" }, ...items), note(msg));
  return { c: own ? ov : S.cfg, bar, locked: !!serial && !own };
}
function lockedWrap(el, locked) { if (locked) { el.style.opacity = "0.55"; el.style.pointerEvents = "none"; } return el; }

// ====================================================================== monsters
PAGES.monsters = function (fixed) {
  if (fixed === undefined) return h("div", {}, note("These are the default monster selections for all instances unless an instance has its own monster list selected."), monstersPanel(S.cfg));
  const sc = scopeBar("monsters", ["monster_skip", "monster_presets", "monster_unknown"], "monster list", fixed);
  return h("div", {}, sc.bar, lockedWrap(monstersPanel(sc.c), sc.locked));
};
function monstersPanel(c) {
  c.monster_skip = c.monster_skip || {};
  c.monster_presets = c.monster_presets || {};
  const cat = S.catalog || [];
  const known = new Set((S.monsters || []).map((n) => n.toLowerCase()));
  S.monTab = S.monTab || "All";
  S.monQ = S.monQ || "";
  const skipSet = (n) => new Set((c.monster_skip[n] || []).map(Number));
  const setSkip = (n, set) => { if (set.size) c.monster_skip[n] = [...set].sort((a, b) => a - b); else delete c.monster_skip[n]; };
  const state = (n, lv) => { const s = skipSet(n); const all = lv.map((x) => x[0]); const k = all.filter((x) => s.has(x)).length; return k === 0 ? "join" : (k === all.length ? "skip" : "mixed"); };
  const body = h("div", {});
  const draw = () => {
    body.innerHTML = "";
    const q = S.monQ.trim().toLowerCase();
    cat.forEach(([cname, mons]) => {
      if (S.monTab !== "All" && S.monTab !== cname) return;
      const list = mons.filter(([n]) => !q || n.toLowerCase().includes(q));
      if (!list.length) return;
      const head = h("div", { class: "mon-head" }, h("h3", {}, cname),
        btn("Join all", () => { list.forEach(([n]) => setSkip(n, new Set())); draw(); }, "sm"),
        btn("Skip all", () => { list.forEach(([n, lv]) => setSkip(n, new Set(lv.map((x) => x[0])))); draw(); }, "sm"));
      const rows = h("div", { class: "mon-list" });
      S.monOpen = S.monOpen || {};
      list.forEach(([n, lv]) => {
        const card = h("div", { class: "mon-card" });
        const st = state(n, lv);
        const box = h("button", { class: "tri " + st, type: "button", role: "checkbox", "aria-checked": st === "mixed" ? "mixed" : String(st === "join"), "aria-label": n + ": " + (st === "join" ? "join" : st === "skip" ? "skip" : "some levels")}, st === "join" ? "✓" : st === "skip" ? "" : "−");
        box.addEventListener("click", () => { setSkip(n, st === "join" ? new Set(lv.map((x) => x[0])) : new Set()); draw(); });
        const pre = h("input", { type: "text", class: "short", placeholder: "preset", "aria-label": n + " preset", value: (c.monster_presets[n] || []).join(",") });
        pre.addEventListener("input", () => { const p = parsePresets(pre.value); if (p.length) c.monster_presets[n] = p; else delete c.monster_presets[n]; });
        const multi = lv.length > 1;
        const row = h("div", { class: "mon-row" },
          box, h("span", { class: "mon-name" }, (cname === "Normal monsters" && lv.length === 1) ? h("b", { class: "mon-lvnum" }, String(lv[0][0])) : null, n, known.has(n.toLowerCase()) ? h("span", { class: "mon-pic", title: "The bot has a picture of this monster" }, "pic") : null),
          h("span", { class: "muted mon-sum" }, multi ? (st === "join" ? "all levels" : st === "skip" ? "all skipped" : (lv.length - skipSet(n).size) + " of " + lv.length + " levels") : ""),
          pre);
        card.appendChild(row);
        rows.appendChild(card);
        const isOpen = S.monOpen[n] !== undefined ? S.monOpen[n] : lv.length <= 8;
        if (multi) {
          row.appendChild(h("button", { type: "button", class: "btn sm", title: "Show or hide the levels", onclick: () => { S.monOpen[n] = !isOpen; draw(); } }, isOpen ? "Levels \u25B4" : "Levels \u25BE"));
        }
        if (multi && isOpen) {
          const chips = h("div", { class: "mon-lvls" });
          const many = lv.length > 12;                                   // Vikings: 50 levels = bands of 10, or every level
          S.monAll = S.monAll || {};
          const flip = (set, l) => { if (set.has(l)) set.delete(l); else set.add(l); };
          if (many && !S.monAll[n]) {
            for (let i = 0; i < lv.length; i += 10) {
              const grp = lv.slice(i, i + 10), ids = grp.map((x) => x[0]), s2 = skipSet(n);
              const offN = ids.filter((l) => s2.has(l)).length;
              const cls = offN === 0 ? " on" : offN === ids.length ? "" : " part";
              const ch = h("button", { type: "button", class: "lvchip band" + cls, "aria-pressed": String(offN === 0), title: "Levels " + ids[0] + " to " + ids[ids.length - 1] + (offN && offN < ids.length ? " (some skipped)" : "") }, h("b", {}, ids[0] + "\u2013" + ids[ids.length - 1]));
              ch.addEventListener("click", () => { const s3 = skipSet(n); if (offN === 0) ids.forEach((l) => s3.add(l)); else ids.forEach((l) => s3.delete(l)); setSkip(n, s3); draw(); });
              chips.appendChild(ch);
            }
          } else {
            lv.forEach(([l, pw]) => {
              const off = skipSet(n).has(l);
              const ch = h("button", { type: "button", class: "lvchip" + (off ? "" : " on"), "aria-pressed": String(!off), title: "Level " + l }, h("b", {}, "Lv" + l));
              ch.addEventListener("click", () => { const s2 = skipSet(n); flip(s2, l); setSkip(n, s2); draw(); });
              chips.appendChild(ch);
            });
          }
          if (many) chips.appendChild(h("button", { type: "button", class: "btn sm", onclick: () => { S.monAll[n] = !S.monAll[n]; draw(); } }, S.monAll[n] ? "Show bands of 10" : "Show every level (1-" + lv.length + ")"));
          card.appendChild(chips);
        }
      });
      body.appendChild(head); body.appendChild(rows);
    });
    if (!body.children.length) body.appendChild(h("div", { class: "empty" }, "No monster matches."));
  };
  const tabs = h("div", { class: "seg", role: "tablist" });
  ["All", ...cat.map((x) => x[0])].forEach((t) => {
    const b = h("button", { type: "button", class: "segb" + (S.monTab === t ? " on" : ""), role: "tab", "aria-selected": String(S.monTab === t) }, t);
    b.addEventListener("click", () => { S.monTab = t; [...tabs.children].forEach((x) => { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-selected", String(on)); }); draw(); });
    tabs.appendChild(b);
  });
  const search = h("input", { type: "search", placeholder: "Search monster", value: S.monQ, "aria-label": "Search monster" });
  search.addEventListener("input", () => { S.monQ = search.value; draw(); });
  draw();
  return panel("Monsters",
    note("Ticked = the bot joins it. Untick a monster to skip it, or open its levels and untick only the levels you do not want. The level is read from the rally card; if it cannot be read the bot joins."),
    h("div", { class: "row", style: { marginBottom: "12px" } }, tabs, search),
    h("div", { class: "row", style: { marginBottom: "14px" } }, h("span", { class: "lab" }, "Monster with no picture yet"), sel(c, "monster_unknown", ["join", "skip"]),
      S.dev ? btn("Open unknown-monster folder", () => act(api("open_unknown")), "sm") : null),
    body,
    h("div", { class: "row", style: { marginTop: "14px" } }, S.dev ? btn("Add monster from screenshot...", addMonster) : null, btn("Refresh list", async () => { await reloadMeta(); rerender(); })));
}
async function addMonster() {
  const shot = await pickScreenshot("Screenshot of the rally list");
  if (!shot) return;
  const pos = await pickOnShot(shot, "Click the CENTRE of the monster's round portrait. The green box shows what will be saved.", "click", [76, 76]);
  if (!pos) return;
  const name = await promptBox("Monster name", "Name for this monster (e.g. Witch):", "");
  if (!name) return;
  const j = await act(api("monster_add", { token: shot.token, x: pos.x, y: pos.y, name }));
  if (j) { S.monsters = j.monsters; if (S.tab === "monsters") rerender(); }
}

// ====================================================================== tasks
PAGES.tasks = function (fixed) {
  const sc = scopeBar("tasks", ["city_tasks", "rally_enabled", "city_enabled", "tasks_first"], "task list", fixed);
  const c = sc.c;
  c.city_tasks = c.city_tasks || [];
  let selIdx = null;
  const holder = h("div", {});
  const draw = () => {
    const t = listTable(["Task", "Opens", "Every (min)", "On"], c.city_tasks.map((x, i) => ({ cells: [x.name, x.target, (x.at ? "at " + x.at : (x.every_min ?? 60)), x.enabled !== false ? "yes" : "no"], _dbl: () => edit() })), (i) => (selIdx = i), selIdx);
    holder.innerHTML = ""; holder.appendChild(t.el);
  };
  const edit = async () => { if (selIdx === null) return; const r = await taskDialog(c.city_tasks[selIdx]); if (r) { c.city_tasks[selIdx] = r; draw(); } };
  const addExample = async () => {
    const have = new Set(c.city_tasks.map((t) => t.name));
    const todo = S.meta.examples.filter((t) => !have.has(t.name));
    if (!todo.length) return toast("All ready-made tasks are already in the list.");
    const i = await choiceBox("Add which task?", todo.map((t) => `${t.name}   (every ${t.every_min} min)`));
    if (i === null) return;
    const t = clone(todo[i]); t.enabled = true; c.city_tasks.push(t); draw();
    if (t.name === "Black Market") toast("Now add the items to buy on the Market tab.");
  };
  draw();
  const cityInfo = h("span", { class: "hint", id: "cityInfo" });
  window.refreshCityInfo = () => { cityInfo.textContent = "Saved: " + (S.cityInfo.length ? S.cityInfo.join(";  ") : "nothing yet"); };
  refreshCityInfo();
  const tasks = panel("City tasks (buildings and the right-side buttons)",
    h("div", { class: "row", style: { gap: "28px" } }, tog(c, "rally_enabled", "Join rallies (always first)"), tog(c, "city_enabled", "Do the tasks below (and duties)"), tog(c, "tasks_first", "A task that is due goes before rallies, then the bot comes back")),
    note("Tip for best results: on each instance, open the city once and pinch it fully out (two fingers together). The bot finds buildings by reading the names under them, and a zoomed-in city shows too few names."),
    note("Rallies come first: the bot only leaves the rally list when no rally is available, runs the tasks that are due (top of the list first) and goes back. Switch 'Join rallies' off to make the bot do only the tasks. Building tasks need the city mapper to have run once."),
    holder,
    h("div", { class: "row", style: { marginTop: "12px" } }, btn("Add ready-made task...", addExample, "pri"), btn("Add custom", async () => { const r = await taskDialog(null); if (r) { c.city_tasks.push(r); draw(); } }),
      btn("Edit", edit), btn("On / Off", () => { if (selIdx === null) return; c.city_tasks[selIdx].enabled = !(c.city_tasks[selIdx].enabled !== false); draw(); }),
      btn("Up", () => { if (selIdx > 0) { [c.city_tasks[selIdx - 1], c.city_tasks[selIdx]] = [c.city_tasks[selIdx], c.city_tasks[selIdx - 1]]; selIdx--; draw(); } }, "sm"),
      btn("Down", () => { if (selIdx !== null && selIdx < c.city_tasks.length - 1) { [c.city_tasks[selIdx + 1], c.city_tasks[selIdx]] = [c.city_tasks[selIdx], c.city_tasks[selIdx + 1]]; selIdx++; draw(); } }, "sm"),
      btn("Delete", () => { if (selIdx !== null) { c.city_tasks.splice(selIdx, 1); selIdx = null; draw(); } })));
  const xy = (key, def) => inp(c, key, { cls: "short", get: xyText, set: (t) => parseXY(t, def) });
  const mapper = panel("City mapper",
    note("The bot drags the map to the top-left corner, sweeps the whole city, reads the building names and saves where each one is. Open the city screen on the instance first. It takes a few minutes."),
    h("div", { class: "row", style: { marginBottom: "14px" } }, btn("Scan the city now", scanCityFlow, "pri"), cityInfo),
    grid2(fld("Tap to leave the rally list for the city (x,y)", xy("city_to_tap", [35, 35])),
      fld("Tap to go back from the city to the rally list (x,y, if no rally_icon picture)", xy("rally_nav_tap", [])),
      fld("Empty ground to tap so names show (x,y, optional)", xy("city_label_tap", []))),
    h("div", { style: { marginTop: "14px" } }, btn("Right-side icon positions...", iconsDialog)));
  return fixed !== undefined ? h("div", {}, sc.bar, lockedWrap(tasks, sc.locked)) : h("div", {}, sc.bar, lockedWrap(tasks, sc.locked), mapper);
};

// ====================================================================== market
const PAY = [["gems", "Gems only"], ["resources", "Resources only"], ["both", "Gems or resources"]];
const REFRESH = [["none", "Do not refresh"], ["count", "Refresh this many times"], ["all", "Until the gem floor (keeps refreshing and buying)"]];
// Black Market pictures that ship with the app (templates mk_BM_NN.png, cut from a real shop recording): plain name + category.
// The item keeps its "BM NN" name in the settings (that is the picture the bot looks for); only the label shown changes.
const BM_CATS = [["all", "All"], ["stamina", "Stamina"], ["res", "Resources"], ["speed", "Speed-ups"], ["chest", "Chests"], ["gear", "Gear & materials"], ["other", "Other"]];
const BM_INFO = {
  "BM 44": ["Stamina 50 x10", "stamina"],
  "BM 04": ["Gold 100K", "res"], "BM 08": ["Food 200M", "res"], "BM 21": ["Food 500M", "res"], "BM 19": ["Lumber 200M", "res"], "BM 17": ["Resource 50M", "res"],
  "BM 13": ["Stone 200M", "res"], "BM 42": ["Stone 200M (2)", "res"], "BM 22": ["Ore 500M", "res"],
  "BM 01": ["Chest VI x5", "chest"], "BM 25": ["Chest V x5", "chest"], "BM 03": ["Chest IV x5", "chest"], "BM 07": ["Red chest x20", "chest"], "BM 10": ["Red chest x1", "chest"],
  "BM 27": ["Gold chest x1", "chest"], "BM 29": ["Jewel chest x50", "chest"],
  "BM 06": ["Green jewel Lv.4", "gear"], "BM 11": ["Gear: axe", "gear"], "BM 15": ["Gear: boots", "gear"], "BM 30": ["Gear: horn Lv.4", "gear"], "BM 32": ["Gear: spear", "gear"],
  "BM 39": ["Gear: legs", "gear"], "BM 12": ["Blue crystal x25", "gear"], "BM 41": ["Blue crystal x50", "gear"], "BM 14": ["Orange shard x50", "gear"],
  "BM 45": ["Rainbow crystal x20", "gear"], "BM 48": ["Purple crystal x20", "gear"],
  "BM 05": ["EXP 5000 x10", "other"], "BM 09": ["EXP 20000", "other"], "BM 16": ["EXP 10000", "other"], "BM 02": ["Medals x500", "other"],
  "BM 31": ["Scroll x10", "other"], "BM 51": ["Scroll x20", "other"],
};
// captured items: category from the name (the owner can rename an item by capturing it again)
const bmGuess = (n) => /stam|vit/i.test(n) ? "stamina" : /speed/i.test(n) ? "speed" : /chest|box/i.test(n) ? "chest" : /food|lumber|wood|stone|ore|iron|gold|silver|res/i.test(n) ? "res" : "other";
const bmInfo = (n) => BM_INFO[n] ? { label: BM_INFO[n][0], cat: BM_INFO[n][1] } : { label: n, cat: bmGuess(n) };
PAGES.market = function () {
  const c = S.cfg;
  c.market_items = c.market_items || [];
  let cat = "all", q = "", names = [];
  const holder = h("div", {});
  const sum = h("div", { class: "bmsum" });
  const chosen = (n) => c.market_items.find((i) => i.name === n);
  const pick = (n) => {
    if (chosen(n)) c.market_items = c.market_items.filter((i) => i.name !== n);
    else c.market_items.push({ name: n, pay: c.market_pay || "gems" });
    draw();
  };
  const pills = h("div", { class: "rtnpills" });
  const search = h("input", { type: "search", placeholder: "Search, e.g. stamina, food, chest", oninput: (e) => { q = e.target.value.trim().toLowerCase(); draw(); } });
  const draw = () => {
    const all = [...new Set([...names, ...c.market_items.map((i) => i.name)])];
    pills.innerHTML = "";
    for (const [k, l] of BM_CATS) {
      const n = k === "all" ? all.length : all.filter((x) => bmInfo(x).cat === k).length;
      pills.appendChild(h("button", { type: "button", class: "rtnpill" + (cat === k ? " on" : ""), onclick: () => { cat = k; draw(); } }, l + " " + n));
    }
    const list = all.filter((n) => { const f = bmInfo(n); return (cat === "all" || f.cat === cat) && (!q || (f.label + " " + n + " " + f.cat).toLowerCase().includes(q)); })
      .sort((a, b) => (!!chosen(b) - !!chosen(a)) || bmInfo(a).label.localeCompare(bmInfo(b).label));
    holder.innerHTML = "";
    holder.appendChild(list.length ? h("div", { class: "bmgrid" }, ...list.map((n) => {
      const it = chosen(n), f = bmInfo(n);
      const pay = it ? h("select", { "aria-label": "Pay with", onclick: (e) => e.stopPropagation(), onchange: (e) => { it.pay = e.target.value; } },
        ...PAY.map(([v, l]) => h("option", { value: v, selected: (it.pay || "gems") === v }, l))) : null;
      return h("div", { class: "bmcard" + (it ? " on" : ""), title: n, onclick: () => pick(n) },
        h("img", { src: `/api/market_img?name=${encodeURIComponent(n)}&k=${window.KEY}`, alt: "", onerror: (e) => (e.target.style.visibility = "hidden") }),
        h("div", { class: "bmname" }, f.label), it ? pay : h("div", { class: "bmsub" }, "Tap to buy"));
    })) : note("Nothing matches."));
    const k = c.market_items.length;
    sum.textContent = k ? "Buying " + k + ": " + c.market_items.map((i) => bmInfo(i.name).label).join(", ") : "Nothing chosen yet - the bot buys nothing in the Black Market.";
  };
  api("market_pictures", {}, { quiet: true }).then((j) => { names = (j && j.names) || []; draw(); }).catch(() => draw());
  draw();
  const items = panel("Black Market: what to buy",
    note("Tap the pictures you want; a ticked card is bought, everything else is ignored. Then add the 'Black Market' city task so it visits the shop (Tasks tab)."),
    sum,
    h("div", { class: "row", style: { margin: "10px 0" } },
      btn("Stamina only", () => { c.market_items = c.market_items.filter((i) => bmInfo(i.name).cat === "stamina").map((i) => ({ ...i, pay: "gems" }));
        for (const n of names) if (bmInfo(n).cat === "stamina" && !chosen(n)) c.market_items.push({ name: n, pay: "gems" }); cat = "stamina"; draw(); }, "sm"),
      btn("Clear all", () => { c.market_items = []; draw(); }, "sm"),
      btn("Add item from screenshot...", async () => {
        const shot = await pickScreenshot("Screenshot of the Black Market");
        if (!shot) return;
        const rect = await pickOnShot(shot, "Drag a box around just the item's PICTURE (the icon in the middle, without the number in the corner), then release.", "region");
        if (!rect) return;
        const name = await promptBox("Black Market item", "Name of this item (e.g. Stamina 50):", "");
        if (!name) return;
        const j = await act(api("market_item_add", { token: shot.token, rect, name }));
        if (!j) return;
        if (!names.includes(j.name)) names.push(j.name);
        if (!chosen(j.name)) c.market_items.push({ name: j.name, pay: c.market_pay || "gems" });
        draw();
      })),
    h("div", { class: "bmbar" }, pills, search),
    holder);
  const opts = panel("Buying and refreshing",
    grid2(fld("Pay with (for new items)", sel(c, "market_pay", PAY)), fld("When the shop has none of them", sel(c, "market_refresh_mode", REFRESH)),
      fld("How many refreshes", inp(c, "market_refresh_n", { type: "int", cls: "short" })), fld("Most gems to spend per visit (0 = keep going until the gems run out)", inp(c, "market_max_gems", { type: "int", cls: "short" }))),
    grid2(fld("Keep at least this many gems (0 = no floor)", inp(c, "market_min_gems", { type: "int", cls: "short" })),
      fld("Keep at least this much gold (0 = no floor)", inp(c, "market_min_gold", { type: "int", cls: "short" }))),
    h("div", { style: { margin: "14px 0" } }, tog(c, "market_paid_refresh", "Also refresh with gems when the free refreshes are used up (costs gems!)")));
  return h("div", {}, items, opts);
};

// ====================================================================== telegram
PAGES.telegram = function () {
  const c = S.cfg;
  const panel = (title, ...kids) => h("div", { class: "tgview" }, title ? h("h3", {}, title) : null, kids);   // one view of the single Telegram window
  const code = h("input", { type: "text", class: "short" });
  const pw = h("input", { type: "password" });
  const run = (name, extra) => act(api(name, Object.assign({ cfg: c }, extra || {})));
  const login = panel(null,
    note("1) On my.telegram.org -> 'API development tools' create an app and copy the api_id and api_hash. 2) Enter them with your phone number (with country code, e.g. +911234567890) and press 'Send code'. 3) Type the code Telegram sends you (and your two-step password if you have one) and press 'Log in'. You only do this once: the login is saved on this PC."),
    grid2(fld("api_id", inp(c, "tg_api_id")), fld("api_hash", inp(c, "tg_api_hash", { type: "password" })), fld("Phone number", inp(c, "tg_phone", { ph: "+911234567890" })),
      fld("Login code", code), fld("Two-step password (if any)", pw)),
    h("div", { class: "row", style: { margin: "14px 0 8px" } }, btn("Connect", () => run("tg_connect")), btn("Send code", () => run("tg_send_code")),
      btn("Log in", () => run("tg_login", { code: code.value, password: pw.value }), "pri"),
      btn("Log out", async () => { if (await confirmBox("Telegram", "Log this PC out of Telegram and delete the saved login?")) act(api("tg_logout")); })),
    h("div", { id: "tgStatus", style: { fontWeight: 600 } }, S.tg.status || "Not connected"));
  const bosses = h("div", {});
  let selB = null;
  window.refreshBosses = () => {
    const t = listTable(["Level", "Boss", "X", "Y", "km"], S.bosses.map((b) => ({ cells: [b.level || "", b.name, b.x, b.y, b.km ?? ""] })), (i) => (selB = i), selB);
    bosses.innerHTML = ""; bosses.appendChild(t.el);
  };
  refreshBosses();
  const boss = panel(null,
    grid2(fld("Bot to read", inp(c, "tg_boss_bot")), fld("Message to send it", inp(c, "tg_boss_command")),
      fld("Ask again every (min, 0 = only on the button)", inp(c, "tg_auto_min", { type: "int", cls: "short" })),
      fld("Tasks use bosses of level at least", h("div", { class: "row" }, inp(c, "tg_min_level", { type: "int", cls: "short" }), sel(c, "tg_pick", ["nearest", "highest"])))),
    note("Type the message exactly as you do in the bot's chat (a command or a button's text, e.g. 'Barb Lv7'). Every answer the bot sends is read for lines like 'Lv7 Witch (xy: 719 539) 94km'. In a task you can use {boss_x}, {boss_y}, {boss_level} and {boss_name} in a step, and the step 'text 123' types a number into the game."),
    h("div", { class: "row", style: { marginBottom: "12px" } }, btn("Ask now", () => run("tg_fetch"), "pri"),
      btn("Copy selected X Y", async () => { if (selB === null) return; const b = S.bosses[selB]; try { await navigator.clipboard.writeText(`${b.x} ${b.y}`); toast("Copied.", "ok"); } catch (e) { toast(`${b.x} ${b.y}`); } })), bosses);
  // live list of every coordinate that shows up in the chats you watch
  const feedBox = h("div", {});
  const feedChats = h("input", { type: "text", value: c.tg_feed_chats || "", placeholder: "@channel, group name as in your Telegram list", onchange: () => { c.tg_feed_chats = feedChats.value; act(api("coords_chats", { chats: feedChats.value }, { quiet: true })); } });
  const keepOpts = [[10, "10 minutes"], [20, "20 minutes"], [30, "30 minutes"], [60, "1 hour"], [180, "3 hours"], [0, "Until I clear the list"]];
  const keepSel = h("select", { onchange: async (e) => { c.tg_keep_min = Number(e.target.value); const j = await act(api("coords_keep", { minutes: c.tg_keep_min })); if (j) { drawFeed(j); toast("Saved.", "ok"); } } },
    ...keepOpts.map(([v, t]) => h("option", { value: v, selected: Number(c.tg_keep_min ?? 60) === v }, t)));
  const drawFeed = (j) => {
    if (!j) return;
    const ago = (t) => { const s = Math.max(0, Math.round(j.now - t)); return s < 60 ? s + " s" : s < 3600 ? Math.round(s / 60) + " min" : Math.round(s / 3600) + " h"; };
    const t = listTable(["Seen", "Chat", "Level", "Name", "X", "Y", "km"], j.items.map((b) => ({ cells: [ago(b.t) + " ago", b.chat || "", b.level || "", b.name, b.x, b.y, b.km ?? ""] })), null, null);
    feedBox.innerHTML = "";
    feedBox.appendChild(h("div", { class: "row", style: { gap: "8px", flexWrap: "wrap", margin: "6px 0", alignItems: "center" } },
      h("span", { class: "chip" }, "messages read: " + j.stats.messages), h("span", { class: "chip" }, "with coordinates: " + j.stats.with_coords),
      h("span", { class: "chip" }, "watching: " + ((j.stats.chats || []).join(", ") || "nothing yet")),
      btn("Clear list", async () => { drawFeed(await act(api("coords_clear"))); })));
    feedBox.appendChild(t.el);
  };
  const libBox = h("div", {});
  const drawLib = (j) => {
    if (!j) return;
    const ago = (t) => { const s = Math.max(0, Math.round(j.now - t)); return s < 60 ? s + " s" : s < 3600 ? Math.round(s / 60) + " min" : s < 86400 ? Math.round(s / 3600) + " h" : Math.round(s / 86400) + " d"; };
    libBox.innerHTML = "";
    libBox.appendChild(h("div", { class: "row", style: { gap: "10px", flexWrap: "wrap", margin: "8px 0" } },
      h("span", { class: "chip" }, "saved: " + j.items.length),
      btn("Copy all X Y", async () => { try { await navigator.clipboard.writeText(j.items.map((b) => `${b.name} ${b.x} ${b.y}`).join("\n")); toast("Copied.", "ok"); } catch (e) { toast("Could not copy."); } }),
      btn("Clear library", async () => { if (confirm("Delete every saved coordinate?")) drawLib(await act(api("coords_library_clear"))); })));
    libBox.appendChild(listTable(["First seen", "Last seen", "Seen", "Level", "Name", "X", "Y"], j.items.map((b) => ({ cells: [ago(b.first) + " ago", ago(b.last) + " ago", (b.n || 1) + "x", b.level || "", b.name, b.x, b.y] })), null, null).el);
  };
  let libTick = 0;
  const loadLib = async () => { if (!libBox.isConnected) return; const j = await api("coords_library", {}, { quiet: true }).catch(() => null); drawLib(j); };
  const loadFeed = async () => { if (!feedBox.isConnected) return; const j = await api("coords", {}, { quiet: true }).catch(() => null); drawFeed(j); if (j && j.stats.messages !== libTick) { libTick = j.stats.messages; loadLib(); } };
  const feedTimer = setInterval(() => { if (!feedBox.isConnected && feedBox.dataset.shown) clearInterval(feedTimer); else loadFeed(); }, 3000);
  setTimeout(() => { feedBox.dataset.shown = "1"; loadFeed(); }, 300);
  const live = panel(null,
    h("div", { class: "row", style: { gap: "10px", alignItems: "flex-end", flexWrap: "wrap", marginBottom: "6px" } },
      h("div", { style: { flex: "1 1 260px" } }, fld("Chats / channels to watch (comma separated)", feedChats)),
      h("div", { style: { flex: "0 0 190px" } }, fld("Keep coordinates for", keepSel)),
      btn("Start watching", async () => { c.tg_feed_chats = feedChats.value; const j = await act(api("coords_watch", { cfg: c })); if (j) { drawFeed(j); toast("Watching.", "ok"); } }, "pri")),
    h("div", { class: "hint" }, "Use the chat's name as in your Telegram list or its @username (log in on the Login tab first). Every new or edited message is read for lines like 'Lv7 Witch (xy: 719 539) 94km'. Older coordinates stay saved in the Library."),
    h("div", { class: "pmax-fill" }, feedBox));
  const lib = panel(null, h("div", { class: "hint" }, "Every coordinate ever seen, kept for good so tasks can use them later."), h("div", { class: "pmax-fill" }, libBox));
  setTimeout(loadLib, 400);
  // ONE window: buttons on top switch between the views; Maximize makes the whole window fill the right section
  const views = [["live", "Live coordinates", live], ["lib", "Library", lib], ["boss", "Boss finder", boss], ["login", "Login", login]];
  let cur = "live";
  try { cur = localStorage.getItem("rf_tg_view") || "live"; } catch (e) {}
  if (!views.some((v) => v[0] === cur)) cur = "live";
  const win = h("div", { class: "panel" });
  const bar = h("div", { class: "row", style: { gap: "8px", flexWrap: "wrap", marginBottom: "12px", alignItems: "center" } });
  const area = h("div", { class: "tgviews" });
  const show = (id) => {
    cur = id;
    try { localStorage.setItem("rf_tg_view", id); } catch (e) {}
    views.forEach(([k, , el]) => el.classList.toggle("on", k === id));
    [...bar.querySelectorAll("button[data-v]")].forEach((b) => b.classList.toggle("pri", b.dataset.v === id));
  };
  views.forEach(([k, t, el]) => { const b = btn(t, () => show(k)); b.dataset.v = k; bar.appendChild(b); area.appendChild(el); });
  bar.appendChild(h("div", { style: { flex: 1 } }));
  bar.appendChild(maxToggle(win));
  win.appendChild(bar); win.appendChild(area);
  show(cur);
  return h("div", {}, win);
};

// ====================================================================== reliability / schedule & alerts / updates
PAGES.reliability = function () {
  const c = S.cfg;
  return panel("Reliability: slow down after failures, restart when stuck", tog(c, "adaptive_delay", "Slow down after a failed click, speed up again after a success"),
    h("div", { style: { height: "14px" } }),
    grid2(fld("Slowest it goes (x click delay)", inp(c, "adaptive_max", { type: "float", cls: "short" })), fld("Restart Evony if the screen is frozen for (s, 0 = off)", inp(c, "frozen_seconds", { type: "int", cls: "short" })),
      fld("Restart the instance if stuck / not answering for (s, 0 = off)", inp(c, "stuck_seconds", { type: "int", cls: "short" })), fld("Give up after this many instance restarts per hour", inp(c, "max_emulator_restarts", { type: "int", cls: "short" })),
      fld("Restart Evony if the bot cannot tell where it is for (s, 0 = never)", inp(c, "lost_seconds", { type: "int", cls: "short" }))),
    note("'Frozen' = Join buttons or a non-rally screen that has not changed at all. 'Stuck' = no screenshot from the instance, a frozen screen that came back after restarting Evony, or not getting back to the rally list. The instance is closed with its own command (MuMu and LDPlayer are known; otherwise set 'Command that closes this instance' for that instance), then started again."));
};
PAGES.schedule = function () {
  const c = S.cfg;
  const sched = panel("Schedules: start and stop instances by the clock", h("div", { class: "row", style: { gap: "20px" } }, tog(c, "schedules_on", "Enable schedules"), h("div", { style: { flex: 1, minWidth: "260px" } }, fld("Default schedule", inp(c, "schedule")))),
    note("Examples:  08:00-12:00, 18:00-23:30   |   Mon-Fri 08:00-22:00; Sat,Sun 10:00-14:00   |   22:00-02:00 (past midnight). An instance can have its own schedule in its page. The bot starts an instance when its window opens and stops it when the window ends; starting or stopping it yourself in between is respected."));
  const alerts = panel("Alerts to your phone (Telegram and / or Discord)",
    grid2(fld("Telegram bot token", inp(c, "telegram_token", { type: "password" })), fld("Telegram chat id", inp(c, "telegram_chat", { get: (v) => v ?? "" })), fld("Discord webhook URL", inp(c, "discord_webhook", { type: "password" }))),
    h("div", { class: "row", style: { margin: "14px 0", gap: "26px" } }, tog(c, "notify_problems", "Message me about problems (restarts, stuck instance, stopped instance)"), tog(c, "notify_joins", "Message me for every joined rally")),
    btn("Send a test message", async () => { const j = await act(api("test_alert", { cfg: c })); if (j) toast(j.msg, j.ok_send ? "ok" : "error"); }),
    note("The token and webhook are saved in your settings file. They are removed when build_exe.bat bundles your settings into the exe."));
  return h("div", {}, sched, alerts);
};
PAGES.updates = function () {
  const c = S.cfg;
  const modes = [["off", "Off"], ["ask", "Ask me"], ["auto", "Automatic"]];
  const seg = h("div", { class: "seg", role: "radiogroup" }, ...modes.map(([v, l]) => h("button", { class: "segb" + ((c.update_mode || "ask") === v ? " on" : ""), role: "radio", onclick: () => { c.update_mode = v; rerender(); } }, l)));
  const tip = { off: "Never checks for updates.", ask: "Shows \u201cUpdate available\u201d - you choose when to install.", auto: "Installs new versions by itself when no instance is in the middle of a join." }[c.update_mode || "ask"];
  // the update address and key are for the developer build only - users never see where updates come from
  const dev = S.dev ? panel("Developer", grid2(fld("Update address (link to version.json)", inp(c, "update_url")), fld("Update key", inp(c, "update_key", { type: "password" })))) : null;
  return h("div", {}, panel(`Updates`,
    h("div", { class: "row", style: { justifyContent: "space-between", alignItems: "center", gap: "16px", flexWrap: "wrap" } },
      h("div", {}, h("b", {}, "Version " + S.version), h("small", { class: "sub", style: { display: "block", color: "var(--mute)" } }, "The app checks when it opens and every 30 minutes.")),
      h("div", { class: "row", style: { gap: "10px" } }, btn("What's new", () => showWhatsNew(true)), btn("Check for updates now", async () => { if (await act(api("check_updates", { cfg: c }))) toast("Checking for updates..."); }, "pri"))),
    h("div", { style: { marginTop: "18px" } }, h("div", { class: "lab", style: { marginBottom: "8px" } }, "When a new version is found"), seg, note(tip + " A new version starts after the app restarts; running instances are started again by themselves."))), dev);
};

// ====================================================================== themes
PAGES.themes = function () {
  const c = S.cfg;
  const mode = () => c.ui_mode || "light";
  const segs = h("div", { class: "seg" });
  const drawSeg = () => { segs.innerHTML = ""; [["dark", "Dark"], ["light", "Light"], ["auto", "Follow Windows"]].forEach(([m, l]) => segs.appendChild(h("button", { class: mode() === m ? "on" : "", onclick: () => { setTheme(null, m); renderModeIcon(); drawSeg(); drawThumbs(); } }, l))); };
  const thumbs = h("div", { class: "thumbs" });
  const PALS = [["ivory", "Ivory", "Warm and calm, clay accent"]];                 // Midnight, Amber and Sage are hidden for now
  const drawThumbs = () => {
    thumbs.innerHTML = "";
    const m = resolvedMode();
    PALS.forEach(([p, name, sub]) => thumbs.appendChild(h("div", { class: "thumb" + ((c.ui_pal || "ivory") === p ? " on" : ""), onclick: () => { setTheme(p, null); drawThumbs(); } },
      h("div", { class: "pv", "data-t": `${p}-${m}` }, h("i", { class: "a" }), h("i", { class: "b" }), h("i", { class: "c" }), h("i", { class: "d" })), h("b", {}, name), h("small", {}, sub))));
  };
  drawSeg(); drawThumbs();
  // accent colour + look
  const sw = h("div", { class: "swatches" });
  const hexIn = h("input", { type: "text", class: "short", placeholder: "#d9a566", maxlength: 7 });
  const picker = h("input", { type: "color", class: "colorpick" });
  const look = h("div", {});
  const drawLook = () => {
    const cur = (c.ui_accent || "").toLowerCase();
    sw.innerHTML = "";
    sw.appendChild(h("button", { class: "swatch auto" + (cur ? "" : " on"), title: "Use the theme's own colour", onclick: () => { setLook({ ui_accent: "" }); drawLook(); } }, "Auto"));
    ACCENTS.forEach(([hx, nm]) => sw.appendChild(h("button", { class: "swatch" + (cur === hx ? " on" : ""), title: nm, style: { background: hx }, onclick: () => { setLook({ ui_accent: hx }); drawLook(); } })));
    picker.value = /^#[0-9a-f]{6}$/i.test(cur) ? cur : "#d9a566";
    hexIn.value = cur;
    look.innerHTML = "";
    const segOf = (key, opts, def) => { const g = h("div", { class: "seg" }); opts.forEach(([v, l]) => g.appendChild(h("button", { class: (c[key] || def) === v ? "on" : "", onclick: () => { setLook({ [key]: v }); drawLook(); } }, l))); return g; };
    const row = (lab, g, hint) => h("div", { class: "row", style: { gap: "16px", margin: "10px 0" } }, h("span", { class: "lab", style: { minWidth: "110px" } }, lab), g, h("span", { class: "hint" }, hint));
    look.append(
      row("Buttons", segOf("ui_btn", [["raised", "Plain"], ["tinted", "Accent tint"]], "raised"), "Tint the buttons with the accent colour."),
      row("Borders", segOf("ui_border", [["subtle", "Subtle"], ["accent", "Accent"]], "subtle"), "Outline of cards, buttons and boxes."),
      row("Page size", (() => {
        const cur = Math.max(80, Math.min(200, parseInt(c.ui_text, 10) || 100));
        const lab = h("b", { class: "pzval" }, cur + "%");
        const sl = h("input", { type: "range", min: 80, max: 200, step: 5, value: cur, class: "pzrange" });
        sl.addEventListener("input", () => { lab.textContent = sl.value + "%"; });
        sl.addEventListener("change", () => { setLook({ ui_text: parseInt(sl.value, 10) }); });
        const pre = h("div", { class: "row", style: { gap: "6px" } }, [100, 125, 150, 200].map((v) => h("button", { class: "btn sm", onclick: () => { setLook({ ui_text: v }); drawLook(); } }, v + "%")));
        return h("div", { class: "row", style: { gap: "14px", alignItems: "center", flexWrap: "wrap" } }, sl, lab, pre);
      })(), "Makes everything in the window bigger or smaller."),
      row("Instance name", segOf("ui_name", [[20, "Small"], [24, "Medium"], [30, "Large"], [38, "Extra Large"]], 24), "Size of the name at the top of each instance page."),
      row("Corners", segOf("ui_round", [["sharp", "Sharp"], ["normal", "Soft"], ["round", "Round"]], "normal"), "How round the cards and buttons are."));
  };
  picker.oninput = () => { setLook({ ui_accent: picker.value }); hexIn.value = picker.value; };
  hexIn.onchange = () => { let v = hexIn.value.trim(); if (v && v[0] !== "#") v = "#" + v; if (/^#[0-9a-f]{6}$/i.test(v)) { setLook({ ui_accent: v.toLowerCase() }); drawLook(); } else toast("Type a colour like #d9a566.", "bad"); };
  drawLook();
  return h("div", {},
    panel("Appearance", DARK_ON ? h("div", { class: "row", style: { gap: "16px" } }, h("span", { class: "lab" }, "Appearance"), segs, h("span", { class: "hint" }, "Light, dark, or follow Windows.")) : null,
      note("The sun / moon button at the top right flips between dark and light from any tab.")),
    panel("Accent colour", note("Pick a colour for the highlights: the active tab, switches, the selected instance and the focus outlines. The text colour on top of it is chosen automatically so it stays readable."),
      h("div", { class: "row", style: { gap: "12px" } }, sw, picker, hexIn)),
    panel("Look", look,
      h("div", { class: "row", style: { marginTop: "6px" } }, btn("Reset look", () => { setLook({ ui_accent: "", ui_btn: "raised", ui_border: "subtle", ui_round: "normal", ui_text: 100, ui_name: 24 }); drawLook(); }, "", "refresh"))));
};

// ====================================================================== license (sold edition only)
PAGES.license = function () {
  const L = S.license || {};
  const left = L.days_left;
  return h("div", {}, panel("Account",
    h("div", { style: { fontWeight: 600, marginBottom: "10px", color: L.valid ? "var(--accent2)" : "var(--bad)" } },
      L.valid ? "Logged in as " + (L.user || L.buyer || "") : (L.error || "Not logged in.")),
    L.valid ? h("div", { class: "hint", style: { marginBottom: "12px" } },
      `Up to ${L.instances} instance${L.instances > 1 ? "s" : ""} running at the same time` + (L.expires ? `  |  valid until ${L.expires}` + (left != null ? ` (${left} day${left === 1 ? "" : "s"} left)` : "") : "  |  no expiry") + `  |  instances in your list: ${S.devices.filter((d) => d.id).length}`) : null,
    h("div", { class: "row", style: { margin: "12px 0" } },
      btn("Log out", async () => { if (await confirmBox("Log out", "Log out? All bots will stop.")) { const j = await act(api("logout")); if (j) { S.license = j.license; showLogin(); } } }),
      L.buy_url ? btn("Buy / extend", () => window.open(L.buy_url, "_blank")) : null),
    note("The app checks your account about once an hour. If it was closed or has expired, the bots stop and you need a new ID and password from the seller. Without internet the app keeps working for up to 48 hours.")));
};


// ====================================================================== Today card (one instance)
function renderTodayCard(serial) {
  const head = h("div", { class: "tdreset" }), list = h("div", { class: "tdq" }), sum = h("div", { class: "tdsum" });
  const res = h("div", { class: "tdres" }), week = h("small", { class: "tdweek" });
  // the game's Auto-Join (ui-simple): time left, when the bot renews it, and a warning when it has not been checked for a while
  const aj = h("div", { class: "ajline", hidden: true, role: "button", tabindex: "0", title: "Open the Auto-Join settings",
    onclick: () => { S.sec = S.sec || {}; S.sec.routines = "aj"; if (S.gotoInstTab) S.gotoInstTab("routines"); } });
  const ajEff = (k, d) => { const o = ((S.cfg.per_device || {})[serial]) || {}; return o[k] !== undefined ? o[k] : S.cfg[k] !== undefined ? S.cfg[k] : d; };
  const ajPaint = async () => {
    if (!ajEff("game_auto_join", false)) { aj.hidden = true; return; }
    aj.hidden = false;
    const r = await api("aj_status", { serial }, { quiet: true }).catch(() => null);
    const renewMin = +ajEff("auto_join_renew_min", 120) || 120, checkMin = +ajEff("auto_join_check_min", 10) || 10;
    const run = (S.devStatus.find((d) => d.id === serial) || {}).state === "run";
    aj.innerHTML = "";
    const dot = (k) => h("span", { class: "ajdot " + k });
    if (!r || r.left_s === undefined) { aj.append(dot("mute"), h("b", {}, "Auto-Join"), h("span", {}, run ? "time left not read yet - the bot checks it shortly" : "on - checked when the bot runs")); return; }
    const left = Math.max(0, r.left_s - r.age_s), at = new Date(Date.now() + Math.max(0, left - renewMin * 60) * 1000);
    const stale = run && r.age_s > Math.max(3 * checkMin * 60, 30 * 60);
    const hm = (t) => Math.floor(t / 3600) + "h " + String(Math.floor(t % 3600 / 60)).padStart(2, "0") + "m";
    aj.append(dot(stale || left === 0 ? "bad" : "ok"), h("b", {}, "Auto-Join"), h("span", {}, hm(left) + " left"),
      h("span", { class: "ajsub" }, left <= renewMin * 60 ? "renews now" : "renews at " + at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })),
      stale ? h("span", { class: "ajwarn" }, "Not checked for " + Math.round(r.age_s / 60) + " min") : h("span", { class: "ajsub" }, "checked " + (r.age_s < 60 ? "just now" : Math.round(r.age_s / 60) + " min ago")));
  };
  const card = h("div", { class: "panel today" },
    h("div", { class: "cardhead" }, h("h3", {}, "Today"), head),
    aj,
    h("div", { class: "tdgrid" },
      h("div", { class: "tdcol" }, h("div", { class: "tdlab" }, "Duty queue"), list, sum),
      h("div", { class: "tdcol" }, h("div", { class: "tdlab" }, "Today's results"), res, week)));
  let m = todayModel({}, serial), last = "";
  const tick = () => {
    if (!m.resetAt) { head.textContent = "Daily reset -"; return; }
    head.textContent = `Daily reset in ${fmtHM(m.resetAt - (Date.now() / 1000 + m.skew))} · ${m.resetLocal} your time`;
  };
  const paint = () => {
    const key = JSON.stringify([m.items, m.summary, m.results, m.week, ajEff("game_auto_join", false)]);
    if (key === last) return;                                               // nothing changed: keep the DOM as it is (no flicker)
    last = key;
    list.innerHTML = "";
    m.items.forEach((x) => list.appendChild(h("div", { class: "tdrow " + x.k },
      h("span", { class: "tdi" }, x.icon), h("span", { class: "tdt" }, x.title), h("small", {}, x.sub))));
    sum.textContent = m.summary;
    res.innerHTML = "";
    const gameJoins = ajEff("game_auto_join", false);
    m.results.forEach(([l, v, tip]) => { if (gameJoins && l === "Rallies joined") { l = "Rallies joined"; v = "By the game"; tip = "The game's Auto-Join is on, so the game joins rallies itself"; }
      res.appendChild(h("div", { class: "stile" + (v === "By the game" ? " txt" : ""), title: tip || "" }, h("b", {}, String(v)), h("span", {}, l))); });
    week.textContent = m.week;
  };
  const load = async () => {
    if (!card.isConnected) return;
    const j = await api("today", { serial }, { quiet: true }).catch(() => null);
    if (j) m = todayModel(j, serial);
    tick(); paint();
    ajPaint();
  };
  paint(); tick();
  const t1 = setInterval(() => { if (!card.isConnected) { clearInterval(t1); return; } tick(); }, 1000);
  const t2 = setInterval(() => { if (!card.isConnected) { clearInterval(t2); return; } load(); }, 5000);
  setTimeout(load, 0);
  return card;
}

// ====================================================================== Today - all instances (Dashboard, nothing selected)
// ---- event cards on the Dashboard (ui-simple, plan claude/event_calendar_plan_22oct.md): Monarch, SvS / KE, Bubble, Ending soon.
// Look only for now: the data comes from the bot's daily Event Center scan ("events" request), which is not built yet -
// until it answers, nothing is shown. Times are absolute (unix seconds) so the countdowns run on their own.
function renderEventCards() {
  const wrap = h("div", { class: "evcards", hidden: true });
  const left = (t) => { const s = Math.max(0, t - Date.now() / 1000), d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), mm = Math.floor(s % 3600 / 60);
    return d ? `${d}d ${hh}h` : hh ? `${hh}h ${String(mm).padStart(2, "0")}m` : `${mm}m`; };
  const card = (title, sub, ...rows) => h("div", { class: "evcard" }, h("div", { class: "evh" }, h("b", {}, title), sub ? h("small", {}, sub) : null), ...rows);
  const line = (name, val, k) => h("div", { class: "evrow" }, h("span", {}, name), h("span", { class: "evv " + (k || "") }, val));
  let data = null;
  const paint = () => {
    wrap.innerHTML = "";
    const cards = (data && data.cards) || [];
    if (!cards.length) { wrap.hidden = true; return; }
    wrap.hidden = false;
    for (const c of cards) {
      if (c.kind === "monarch") wrap.appendChild(card(c.title || "Monarch", c.stage || "", line("Ends in", left(c.ends_at), "next")));
      else if (c.kind === "svs") wrap.appendChild(card(c.title || "SvS / KE", c.text || "", line(c.label || "Begins in", left(c.at), "next")));
      else if (c.kind === "bubble") {
        const off = (c.rows || []).filter((r) => !r.ends_at || r.ends_at < Date.now() / 1000).length;
        wrap.appendChild(card("Bubble", off ? off + " without a shield" : "All shielded",
          ...(c.rows || []).slice(0, 5).map((r) => r.ends_at && r.ends_at > Date.now() / 1000 ? line(r.name, left(r.ends_at)) : line(r.name, "No shield", "bad"))));
      } else if (c.kind === "ending") wrap.appendChild(card("Ending soon", "before the next reset",
          ...(c.items || []).slice(0, 5).map((it) => line(it.name, left(it.ends_at), "due"))));
    }
  };
  const load = async () => { if (!wrap.isConnected && data) return; const j = await api("events", {}, { quiet: true }).catch(() => null); data = j && j.ok !== false ? j : null; paint(); };
  load();
  const t = setInterval(() => { if (!wrap.isConnected) clearInterval(t); else paint(); }, 30000);
  const t2 = setInterval(() => { if (!wrap.isConnected) clearInterval(t2); else load(); }, 300000);
  return wrap;
}
function renderTodayAll() {
  const head = h("div", { class: "tdreset" }), ce = h("div", { class: "tdce" }), body = h("tbody", {});
  const card = h("div", { class: "panel today" },
    h("div", { class: "cardhead" }, h("h3", {}, "Today \u00b7 all instances"), head), ce,
    h("table", { class: "tdall" },
      h("thead", {}, h("tr", {}, ...["Instance", "Shield", "Buff", "Duties", "Joins", "Skipped (late)"].map((c) => h("th", {}, c)))), body));
  let j = null, last = "";
  const skew = () => (j ? j.now - Date.now() / 1000 : 0);
  const tick = () => {
    if (!j) { head.textContent = "Daily reset -"; return; }
    head.textContent = `Daily reset in ${fmtHM(j.reset_at - (Date.now() / 1000 + skew()))} \u00b7 ${hhmm(j.reset_at)} your time`;
  };
  const open = (serial) => { S.sel = serial; S.instTab = "ov"; renderSide(); renderTabs(); renderPage(); };
  const turnOn = async (e) => {
    e.preventDefault();
    const r = await act(api("crazy_eggs_on_all"));
    if (r) { j = r; last = ""; paint(); toast("Crazy Eggs switched on for every instance.", "ok"); }
  };
  const paint = () => {
    if (!j) return;
    const st = {}; (S.devStatus || []).forEach((d) => (st[d.id] = d.state));
    const key = JSON.stringify([j.rows, j.crazy_eggs, st]);
    if (key === last) return;                                               // nothing changed: no flicker
    last = key;
    const c = j.crazy_eggs || { total: 0, off: [] };
    ce.innerHTML = "";
    ce.className = "tdce" + (c.off.length ? " off" : "");
    ce.append(h("b", {}, "Crazy Eggs"), " ");
    if (!c.total) ce.append(h("span", {}, "no instances yet"));
    else if (!c.off.length) ce.append(h("span", {}, `on for all ${c.total} instance${c.total > 1 ? "s" : ""}`));
    else ce.append(h("span", { title: "Off on: " + c.off.join(", ") }, `\u26a0 off on ${c.off.length} of ${c.total}`), " \u00b7 ",
      h("a", { href: "#", title: "Switch the Crazy Eggs duty on for every instance", onclick: turnOn }, "turn on"));
    body.innerHTML = "";
    if (!j.rows.length) body.appendChild(h("tr", {}, h("td", { colspan: "6", class: "mute" }, "No instances in your list yet.")));
    j.rows.forEach((r) => {
      const off = !["run", "pause"].includes(st[r.serial]);
      const all = r.duties_total && r.duties_done >= r.duties_total;
      body.appendChild(h("tr", { class: off ? "off" : "", title: "Open " + r.name, onclick: () => open(r.serial) },
        h("td", {}, h("b", {}, r.name), off ? h("small", {}, " offline") : null),
        h("td", { title: "Not read from the game yet" }, "-"), h("td", { title: "Not read from the game yet" }, "-"),
        h("td", { class: all ? "ok" : "" }, r.duties_total ? `${r.duties_done}/${r.duties_total}` + (all ? " \u2713" : "") : "-"),
        h("td", {}, String(r.joins)), h("td", {}, String(r.late))));
    });
  };
  const load = async () => {
    if (!card.isConnected) return;
    const r = await api("today_all", {}, { quiet: true }).catch(() => null);
    if (r) j = r;
    tick(); paint();
  };
  tick();
  const t1 = setInterval(() => { if (!card.isConnected) { clearInterval(t1); return; } tick(); }, 1000);
  const t2 = setInterval(() => { if (!card.isConnected) { clearInterval(t2); return; } load(); }, 5000);
  setTimeout(load, 0);
  return card;
}

// ====================================================================== duties
PAGES.duties = function (fixed, only, compact) {
  const root = h("div", {});
  let serial = fixed || "";                       // "" = all instances (the default); an instance id = that instance's own copy
  let rows = [], own = {};
  const GROUPS = ["Reward claims", "Daily activities", "Common tasks"];
  // a duty's own settings (Resource Tax resource, training amount, speed-ups ...), saved with the duty
  const dutyOpts = (r) => h("div", { class: "row", style: { flexWrap: "wrap", gap: "14px", padding: "4px 0 10px 46px" } },
    r.opts.map((o) => {
      if (o.type === "rules") return h("a", { href: "#", class: "lab", onclick: (e) => { e.preventDefault(); if (S.gotoInstTab && serial) S.gotoInstTab("mrally"); } }, serial ? "Monsters and presets: Monster rallies tab \u2192" : "Monsters and presets: on each instance's Monster rallies tab");
      const save = async (v) => { const j = await act(api("duty_set", { serial, id: r.id, opt: { [o.k]: v } })); if (j) { rows = j.duties; own = j.own || {}; draw(); } };
      if (o.type === "bool") return h("label", { class: "tgl" }, h("input", { type: "checkbox", checked: !!o.val, onchange: (e) => save(e.target.checked) }), h("span", { class: "sw" }), h("span", {}, o.label));
      if (o.type === "select") return h("label", { class: "row", style: { gap: "6px" } }, h("span", { class: "lab" }, o.label),
        h("select", { onchange: (e) => save(e.target.value) }, o.choices.map((c) => h("option", { value: c, selected: c === o.val }, c))));
      return h("label", { class: "row", style: { gap: "6px" } }, h("span", { class: "lab" }, o.label),
        h("input", { type: "number", min: o.min, max: o.max, value: o.val, style: { width: "110px" }, onchange: (e) => save(e.target.value) }));
    }));
  // (ui-simple) a duty's settings: in the Routines page they sit in its settings card; on the Duties list they open in a side panel
  const SET_IDS = ["crazy_eggs", "champions", "wheel"];
  const hasSet = (r) => r.on && ((r.opts && r.opts.length) || SET_IDS.includes(r.id));
  const setBody = (r) => r.on ? [(r.opts && r.opts.length) ? dutyOpts(r) : null,
    r.id === "crazy_eggs" ? ce : null, r.id === "champions" ? ch : null, r.id === "wheel" ? wh : null].filter(Boolean) : null;
  let sideId = null;
  const side = h("div", { class: "sideov", onclick: (e) => { if (e.target === side) closeSide(); } });
  if (window.watchNotes) window.watchNotes(side);
  const sideKey = (e) => { if (e.key === "Escape") closeSide(); };
  const closeSide = () => {
    const was = sideId; sideId = null; side.remove(); document.removeEventListener("keydown", sideKey);
    const b = was && root.querySelector('.dset[data-id="' + was + '"]'); if (b) { b.classList.remove("on"); b.focus(); }
  };
  const drawSide = () => {
    const r = rows.find((x) => x.id === sideId);
    if (!r || !hasSet(r) || !root.isConnected) { closeSide(); return; }
    const BADGE = { done: "ok", due: "due", waiting: "next", retry: "next", gaveup: "bad" };
    side.innerHTML = "";
    side.appendChild(h("aside", { class: "sidep", role: "dialog", "aria-modal": "true", "aria-label": r.title + " settings" },
      h("div", { class: "sph" },
        h("div", { class: "spt" }, h("h3", {}, r.title), h("small", {}, (serial ? "This instance" : "All instances") + (r.schedule ? " · " + r.schedule : ""))),
        r.status_text ? h("span", { class: "dbadge " + (BADGE[r.status] || "") }, r.status_text) : null,
        h("button", { class: "spx", type: "button", "aria-label": "Close", title: "Close (Esc)", onclick: closeSide }, "×")),
      h("div", { class: "spb" }, setBody(r)),
      h("div", { class: "spf" }, h("span", { class: "hint" }, "Changes save as you make them."), h("button", { class: "btn pri", type: "button", onclick: closeSide }, "Done"))));
  };
  const openSide = (id) => {
    sideId = id;
    root.querySelectorAll(".dset").forEach((b) => b.classList.toggle("on", b.dataset.id === id));
    drawSide();
    if (sideId && !side.isConnected) { document.body.appendChild(side); document.addEventListener("keydown", sideKey); const x = side.querySelector(".spx"); if (x) x.focus(); }
  };
  const draw = () => {
    root.innerHTML = "";
    if (sideId) setTimeout(drawSide, 0);             // keep the side panel in step (duty switched off, own-settings opts saved)
    const pick = h("select", { onchange: (e) => { serial = e.target.value; load(); loadCe(); loadCh(); loadWh(); loadRn(); } },
      [h("option", { value: "", selected: serial === "" }, "All instances (default)")].concat(S.devices.map((d) => h("option", { value: d.id, selected: d.id === serial }, d.name || short(d.id)))));
    if (compact) {
      root.appendChild(rn);
      const onRows = rows.filter((r) => r.on);
      const badge = { done: "ok", due: "due", waiting: "next", retry: "next", gaveup: "bad" };
      root.appendChild(h("div", { class: "panel ovduties" },
        h("div", { class: "cardhead" }, h("h3", {}, "Daily duties"), h("span", { class: "cnt" }, onRows.length + " active")),
        h("div", { class: "dlist" }, onRows.slice(0, 7).map((r) => h("div", { class: "drow" },
          h("div", { class: "dtx" }, h("div", { class: "dname" }, h("b", {}, r.title), h("span", { class: "dbadge " + (badge[r.status] || "") }, r.status_text)),
            h("small", {}, (r.note || "") + (r.schedule ? " \u00b7 " + r.schedule : "")),
            r.week && (r.week.ok || r.week.fail) ? h("small", { class: "dweek", title: r.week.last_error ? "Last problem: " + r.week.last_error : "" },
              "Last 7 days: " + r.week.ok + " done" + (r.week.fail ? " \u00b7 " + r.week.fail + " failed" : "")) : null),
          h("label", { class: "tgl" }, h("input", { type: "checkbox", checked: r.on, onchange: async (e) => { const j = await act(api("duty_set", { serial, id: r.id, on: e.target.checked })); if (j) { rows = j.duties; own = j.own || {}; draw(); } } }), h("span", { class: "sw" })))).concat(onRows.length ? [] : [note("No duty is switched on yet.")]),
          onRows.length > 7 ? [h("small", { class: "hint" }, "+" + (onRows.length - 7) + " more switched on")] : []),
        h("button", { class: "btn manageall", onclick: () => { S.sec = S.sec || {}; S.sec.routines = "duties"; if (S.gotoInstTab) S.gotoInstTab("routines"); } }, "Manage all duties \u2192")));
      return;
    }
    if (!only) root.appendChild(panel("Duties",
      h("div", { class: "dhead" },
        h("span", { class: "hint" }, "What the bot does between rallies."),
        infoTip("How duties work", "Each duty runs once per game day (reset 00:00 UTC) or every few minutes. If one fails, the bot goes back to rallies and tries again every hour until it is done. After 3 failures you get an alert and a report is saved."),
        h("span", { style: { flex: "1" } }),
        fixed !== undefined ? null : pick,
        serial && own.duties ? h("span", { class: "chip own" }, "own settings") : null,
        serial && own.duties ? h("button", { class: "btn sm", onclick: async () => { const j = await act(api("scope_reset", { serial, key: "duties" })); if (j) { rows = j.duties; own = j.own || {}; draw(); } } }, "Use the universal setting") : null,
        serial ? infoTip("This instance only", "Changes here apply to this instance only. Pick 'All instances' to change the default for every instance without its own settings.") : null),
    ));
    if (!only) root.appendChild(rn);
    const BADGE = { done: "ok", due: "due", waiting: "next", retry: "next", gaveup: "bad" };
    const item = (r) => [h("div", { class: "dline" + (only ? " only" : "") + (r.on ? "" : " off") },
        only ? null : h("label", { class: "tgl" }, h("input", { type: "checkbox", checked: r.on, "aria-label": r.title, onchange: async (e) => { const j = await act(api("duty_set", { serial, id: r.id, on: e.target.checked })); if (j) { rows = j.duties; own = j.own || {}; draw(); } } }), h("span", { class: "sw" })),
        h("div", { class: "dtx", title: r.note || "" }, only ? null : h("b", {}, r.title), h("small", {}, r.schedule || r.note || "")),
        S.dev && !r.verified ? h("span", { class: "dbadge", title: "Developer: steps not yet watched on a real instance" }, "untested") : null,
        r.on && r.status_text ? h("span", { class: "dbadge " + (BADGE[r.status] || "") }, r.status_text) : null,
        r.status === "gaveup" ? h("button", { class: "btn sm", onclick: async () => { const j = await act(api("duty_retry", { serial, id: r.id })); if (j) { rows = j.duties; own = j.own || {}; draw(); toast("It runs again shortly.", "ok"); } } }, "Retry") : null,
        // (ui-simple) settings open in the side panel instead of under the row (half columns were too narrow)
        (!only && hasSet(r)) ? h("button", { class: "btn sm dset" + (sideId === r.id ? " on" : ""), type: "button", "data-id": r.id, "aria-label": r.title + " settings", onclick: () => openSide(r.id) }, "Settings") : null),
        only ? setBody(r) : null,
        (only && !r.on) ? note("Switch it on at the top and its settings open here. " + (serial ? "This instance can have its own settings." : "")) : null].filter(Boolean);
    if (only) {                                   // one duty inside the Routines page: its switch and name are in that page's header, so just the settings here
      const list = rows.filter((r) => r.id === only);
      if (list.length) root.appendChild(panel("Settings", list.flatMap(item)));
      return;
    }
    // (ui-simple) one card: On / All + group pills on top, duties in two columns - no long scroll, no empty middle
    const onCount = rows.filter((r) => r.on).length;
    if (!S.dutyView && rows.length) S.dutyView = { show: onCount ? "on" : "all", group: "" };   // decided once the list has loaded
    const v = S.dutyView || { show: "all", group: "" };
    if (v.show === "on" && !onCount) v.show = "all";
    const segBtn = (lab, on, fn) => h("button", { class: "segb" + (on ? " on" : ""), type: "button", onclick: fn }, lab);
    const pills = h("div", { class: "dfilter" },
      h("div", { class: "seg small" }, segBtn("On (" + onCount + ")", v.show === "on", () => { v.show = "on"; draw(); }), segBtn("All (" + rows.length + ")", v.show === "all", () => { v.show = "all"; draw(); })),
      h("div", { class: "rtnpills" }, [["", "All groups"]].concat(GROUPS.map((g) => [g, g])).map(([g, l]) =>
        h("button", { class: "rtnpill" + (v.group === g ? " on" : ""), type: "button", onclick: () => { v.group = g; draw(); } }, l, h("span", { class: "pc" }, String(rows.filter((r) => (!g || r.group === g) && (v.show === "all" || r.on)).length))))));
    const shown = rows.filter((r) => (v.show === "all" || r.on) && (!v.group || r.group === v.group));
    const body = [];
    for (const g of GROUPS) {
      const list = shown.filter((r) => r.group === g);
      if (!list.length) continue;
      if (!v.group) body.push(h("div", { class: "dgrouph" }, g));
      body.push(h("div", { class: "dgrid" }, list.map((r) => h("div", { class: "dcell" + (r.on ? " on" : "") }, item(r)))));
    }
    root.appendChild(panel(null, pills, body.length ? body : h("div", { class: "empty" }, v.show === "on" ? "No duty is on here yet. Pick All to switch some on." : "No duties in this group.")));
    if (fixed === undefined) root.appendChild(hist);           // what the bot did: below the list, not above it
  };
  // what the bot did: today's state per instance + the latest task events
  const hist = h("div", {});
  const fmtT = (t) => { const d = new Date(t * 1000); return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };
  const loadHist = async () => {
    if (!root.isConnected) return;
    const j = await api("task_history", {}, { quiet: true }).catch(() => null);
    if (!j) return;
    hist.innerHTML = "";
    hist.appendChild(panel("Today at a glance",
      note("One line per instance. A duty shows done, waiting (with the time it comes back), due, retrying or gave up. If an instance has 'Do the tasks' switched off, nothing runs there."),
      j.matrix.map((r) => h("div", { class: "row", style: { padding: "6px 0", borderTop: "1px solid var(--line)", flexWrap: "wrap", gap: "6px" } },
        h("b", { style: { minWidth: "110px" } }, r.emu),
        r.tasks ? null : h("span", { class: "chip pill-bad" }, "tasks are off"),
        r.duties.length ? r.duties.map((d) => h("span", { class: "chip " + (d.status === "done" ? "pill-ok" : d.status === "gaveup" ? "pill-bad" : "") }, d.title + ": " + d.text)) : (r.tasks ? h("span", { class: "hint" }, "no duty switched on") : null)))));
    hist.appendChild(panel("What the bot did (latest first)",
      j.events.length ? j.events.slice(0, 60).map((e) => h("div", { class: "row", style: { padding: "5px 0", borderTop: "1px solid var(--line)", gap: "10px" } },
        h("span", { class: "hint", style: { minWidth: "48px" } }, fmtT(e.t)), h("b", { style: { minWidth: "90px" } }, e.emu),
        h("span", { style: { flex: "1" } }, e.task), h("span", { class: "chip " + (e.ok ? "pill-ok" : "pill-bad") }, e.ok ? "done" : "failed" + (e.msg ? " - " + e.msg : ""))))
        : note("Nothing yet. Tasks and duties are listed here as the bot does them or fails.")));
  };
  const histTimer = setInterval(() => { if (!root.isConnected) clearInterval(histTimer); else loadHist(); }, 15000);
  const load = async () => { const j = await act(api("duties", { serial })); if (j) { rows = j.duties; own = j.own || {}; draw(); } };
  // Crazy Eggs settings: per-egg switch + daily limit, which egg gets the Lucky Hammer, hammers to keep
  const ce = h("div", {});
  const EGG_TIME = { "3": "30 min", "6": "1 h", "8": "2 h", "12": "4 h" };
  const drawCe = (j) => {
    ce.innerHTML = "";
    const c = j.conf;
    const set = async (o) => { const r = await act(api("crazy_eggs_set", { serial, ...o })); if (r) drawCe(r); };
    ce.appendChild(subset(serial ? "Crazy Eggs \u2013 this instance" : "Crazy Eggs \u2013 all instances",
      serial && j.own && j.own.crazy_eggs ? h("div", { class: "row" }, h("span", { class: "chip own" }, "own settings"),
        h("button", { class: "btn sm", onclick: async () => { await act(api("scope_reset", { serial, key: "crazy_eggs" })); loadCe(); } }, "Use the universal setting")) : null,
      note("Event Center > Activities > Crazy Eggs. The bot smashes every ready egg you switch on, closes the reward panel, and comes back when the next timer ends. The Lucky Hammer (1 a day) skips an egg's wait, so give it to the egg with the longest wait."),
      j.costs.map((k) => {
        const e = c.eggs[k] || { on: true, daily: 0 };
        const lucky = c.lucky === k || (c.lucky === "max" && k === j.costs[j.costs.length - 1]);
        return h("div", { class: "eggrow" + (e.on ? "" : " off") },
          h("label", { class: "tgl" }, h("input", { type: "checkbox", checked: e.on, "aria-label": k + " hammers egg", onchange: (ev) => set({ egg: k, on: ev.target.checked }) }), h("span", { class: "sw" })),
          h("div", { class: "eggtx" }, h("b", {}, k + " hammers"),
            h("small", {}, "Waits about " + EGG_TIME[k] + " after a miss" + (j.smashes && j.smashes[k] ? " \u00b7 smashed today: " + j.smashes[k] : ""))),
          h("label", { class: "eggmax", title: "Empty = no limit" }, h("span", {}, "Max a day"),
            h("input", { type: "number", min: 0, max: 99, value: e.daily || "", placeholder: "\u221e", onchange: (ev) => set({ egg: k, daily: ev.target.value }) })),
          h("button", { class: "luckyb" + (lucky ? " on" : ""), type: "button", "aria-pressed": lucky ? "true" : "false", title: lucky ? "The daily Lucky Hammer goes to this egg (click to switch it off)" : "Give the daily Lucky Hammer to this egg", onclick: () => set({ lucky: lucky ? "" : k }) }, lucky ? "\u2605 Lucky" : "\u2606 Lucky"));
      }),
      h("div", { class: "fgrid eggf" },
        h("label", {}, h("span", { class: "lab" }, "Visit at most every"),
          h("div", { class: "unit" }, h("input", { type: "number", min: 0, max: 600, value: c.min_gap == null ? 30 : c.min_gap, onchange: (ev) => set({ min_gap: ev.target.value }) }), h("span", {}, "min")),
          h("small", { class: "hint" }, "Ready eggs are smashed together. 0 = as soon as one is ready.")),
        h("label", {}, h("span", { class: "lab" }, "Hammers to keep"),
          h("input", { type: "number", min: 0, value: c.min_hammers || "", placeholder: "0", onchange: (ev) => set({ min_hammers: ev.target.value }) }),
          h("small", { class: "hint" }, "Never go below this many."))),
      h("div", { class: "row", style: { marginTop: "4px" } }, h("span", { class: "chip" }, j.lucky_used ? "Lucky Hammer used today" : (c.lucky ? "Lucky Hammer ready" : "Lucky Hammer off")),
        h("span", { class: "hint" }, "When all four eggs are done, it stops for the day."))));
  };
  const loadCe = async () => { const j = await act(api("crazy_eggs", { serial })); if (j) drawCe(j); };
  // Run a task now: the bot does the picked task first, before everything else, on this instance (or on all)
  const rn = h("div", {});
  let rnPick = "";
  const drawRn = (j) => {
    rn.innerHTML = "";
    const set = async (name, o) => { const r = await act(api(name, { serial, ...o })); if (r) drawRn(r); };
    const sel = h("select", { onchange: (e) => { rnPick = e.target.value; } }, h("option", { value: "" }, "Pick a task\u2026"),
      j.tasks.map((t) => h("option", { value: t.id, selected: t.id === rnPick }, t.group + " \u2013 " + t.title)));
    if (compact) rn.className = "ovrun";
    rn.appendChild(panel("Run a task now",
      compact ? h("small", { class: "sub" }, "Runs once, before anything else, then the bot carries on.") : note("Pick any task and the bot does it next, before everything else (even before a waiting rally), once. It works even if the task is switched off or already done today, and also when 'Do the tasks' is off. It starts as soon as the instance is running" + (serial ? "." : ", on every instance.")),
      h("div", { class: compact ? "runstack" : "row", style: { gap: "8px" } }, sel,
        h("button", { class: "btn primary" + (compact ? " ib" : ""), onclick: () => { if (rnPick) set("run_now_add", { id: rnPick }); } }, "Run now", compact ? ico("bolt") : null)),
      j.queue.length ? h("div", { class: "row", style: { gap: "6px", flexWrap: "wrap", marginTop: "8px" } }, h("span", { class: "lab" }, "Waiting:"),
        j.queue.map((q, i) => h("span", { class: "chip" }, (i + 1) + ". " + q.title + " ",
          h("a", { href: "#", title: "Cancel", onclick: (e) => { e.preventDefault(); set("run_now_remove", { id: q.id }); } }, "\u00d7")))) : null));
  };
  let rnLast = "";
  const loadRn = async () => { const j = await act(api("run_now", { serial })); if (j) { rnLast = JSON.stringify(j.queue); drawRn(j); } };
  // the queue empties when the bot starts the task: refresh it every 10 s (redraw only when it changed, so an open picker is not reset)
  const rnTick = setInterval(async () => {
    if (!document.body.contains(rn)) { clearInterval(rnTick); return; }
    const j = await api("run_now", { serial }, { quiet: true }).catch(() => null);
    if (j && j.queue && JSON.stringify(j.queue) !== rnLast) { rnLast = JSON.stringify(j.queue); drawRn(j); }
  }, 10000);
  // Wheel of Fortune: which spin button, how many spins, coins to keep
  const wh = h("div", {});
  const drawWh = (j) => {
    wh.innerHTML = "";
    const c = j.conf;
    const set = async (o) => { const r = await act(api("wheel_set", { serial, ...o })); if (r) drawWh(r); };
    wh.appendChild(subset(serial ? "Wheel of Fortune \u2013 this instance" : "Wheel of Fortune \u2013 all instances",
      serial && j.own && j.own.wheel ? h("div", { class: "row" }, h("span", { class: "chip own" }, "own settings"),
        h("button", { class: "btn sm", onclick: async () => { await act(api("scope_reset", { serial, key: "wheel" })); loadWh(); } }, "Use the universal setting")) : null,
      note("Tavern > Wheel of Fortune. Each spin costs coins. The bot presses only the Spin buttons and closes the reward panel after every spin. With a big button it still finishes with smaller ones so the number of spins is exact."),
      h("div", { class: "fgrid eggf" },
        h("div", { class: "wide" }, h("span", { class: "lab" }, "Spin button"),
          h("div", { class: "seg small" }, [1, 10, 100].map((b) => h("button", { class: "segb" + (c.button === b ? " on" : ""), type: "button", onclick: () => set({ button: b }) }, "Spin " + (b === 1 ? "once" : "\u00d7" + b))))),
        h("label", {}, h("span", { class: "lab" }, "Number of spins"),
          h("input", { type: "number", min: 0, value: c.spins || "", placeholder: "Until out of coins", onchange: (ev) => set({ spins: ev.target.value }) }),
          h("small", { class: "hint" }, "Empty or 0 = until the coins run out.")),
        h("label", {}, h("span", { class: "lab" }, "Coins to keep"),
          h("input", { type: "number", min: 0, value: c.reserve || "", placeholder: "0", onchange: (ev) => set({ reserve: ev.target.value }) }),
          h("small", { class: "hint" }, "Never go below this many.")),
        h("div", { class: "wide" }, h("span", { class: "lab" }, "Add chips first"),
          h("div", { class: "seg small" }, [["off", "Off"], ["needed", "Only what's needed"], ["all", "All owned"]].map(([v, t]) => h("button", { class: "segb" + ((c.chips || "off") === v ? " on" : ""), type: "button", onclick: () => set({ chips: v }) }, t))),
          h("small", { class: "hint" }, "Uses 100 / 500 / 1K chip items you own before spinning. Never buys with gems.")))));
  };
  const loadWh = async () => { const j = await act(api("wheel", { serial })); if (j) drawWh(j); };
  // Champions: how many of the daily 15 taps and 10 greetings each champion gets (this instance or all)
  const ch = h("div", {});
  const drawCh = (j) => {
    ch.innerHTML = "";
    const set = async (o) => { const r = await act(api("champions_set", { serial, ...o })); if (r) drawCh(r); };
    const sumT = Object.values(j.plan).reduce((a, v) => a + (v.taps || 0), 0), sumG = Object.values(j.plan).reduce((a, v) => a + (v.greets || 0), 0);
    const usedT = Object.values(j.taps || {}).reduce((a, v) => a + v, 0), usedG = Object.values(j.greets || {}).reduce((a, v) => a + v, 0);
    ch.appendChild(subset(serial ? "Champions – this instance" : "Champions – all instances",
      serial && j.own && j.own.champions ? h("div", { class: "row" }, h("span", { class: "chip own" }, "own settings"),
        h("button", { class: "btn sm", onclick: async () => { await act(api("scope_reset", { serial, key: "champions" })); loadCh(); } }, "Use the universal setting")) : null,
      note("Keep > Champions. Every account has the same champions but only a few are active; the bot skips the ones that are not (grey button at the bottom). The daily limit is " + j.max_taps + " taps and " + j.max_greets + " greetings for all champions together - put them all on one champion or share them out. Switch a champion on to make it the main one: it gets all the taps and greetings of the day in one click (the numbers can still be edited). Leave everything empty to use the champion the page opens on. Turn the 'Champions' duty on above. Needs the city map (the Keep)."),
      h("div", { class: "row", style: { gap: "8px", flexWrap: "wrap" } },
        h("span", { class: "chip " + (sumT > j.max_taps ? "pill-bad" : "") }, "taps planned " + sumT + " / " + j.max_taps),
        h("span", { class: "chip " + (sumG > j.max_greets ? "pill-bad" : "") }, "greets planned " + sumG + " / " + j.max_greets),
        h("span", { class: "chip" }, "done today: " + usedT + " taps, " + usedG + " greets"),
        Object.keys(j.plan).length ? h("button", { class: "btn sm", onclick: () => set({ clear: true }) }, "Clear all") : null),
      h("div", { class: "chrow chhead" }, h("span", {}, "Main"), h("span", {}, "Champion"), h("span", {}, "Taps"), h("span", {}, "Greets")),
      j.names.map((n) => {
        const e = j.plan[n] || {};
        const full = (e.taps || 0) >= j.max_taps && (e.greets || 0) >= j.max_greets;
        const done = (j.taps && j.taps[n] ? "tapped " + j.taps[n] : "") + (j.greets && j.greets[n] ? (j.taps && j.taps[n] ? " \u00b7 " : "") + "greeted " + j.greets[n] : "");
        return h("div", { class: "chrow" + ((e.taps || e.greets) ? " set" : "") },
          h("label", { class: "tgl", title: "Main champion: one click gives this champion all " + j.max_taps + " taps and " + j.max_greets + " greetings every day (the numbers can still be changed)" },
            h("input", { type: "checkbox", checked: full, "aria-label": n + " main champion", onchange: (ev) => set({ name: n, main: ev.target.checked }) }), h("span", { class: "sw" })),
          h("div", { class: "eggtx" }, h("b", {}, n), done ? h("small", {}, done + " today") : null),
          h("input", { type: "number", min: 0, max: j.max_taps, value: e.taps || "", placeholder: "0", "aria-label": n + " taps", onchange: (ev) => set({ name: n, taps: ev.target.value }) }),
          h("input", { type: "number", min: 0, max: j.max_greets, value: e.greets || "", placeholder: "0", "aria-label": n + " greets", onchange: (ev) => set({ name: n, greets: ev.target.value }) }));
      })));
  };
  const loadCh = async () => { const j = await act(api("champions", { serial })); if (j) drawCh(j); };
  draw(); load(); loadCe(); loadCh(); loadWh(); loadRn(); loadHist();
  return root;
};


// ====================================================================== monster rallies (instance tab): start Alliance War rallies on Telegram coordinates
// The game has 8 fixed presets and many monsters, so the page is built around the presets: one lane per preset (I-VIII)
// with its rally time, holding the monsters that preset attacks. Monsters come from the library on the right: drag one
// onto a lane, or pick a lane and click the monster. A monster can sit in two lanes with different levels (Ymir 1-4 -> II, 5-6 -> III).
// Below: the rules (waiting, free marches, stamina) and the coordinates Telegram has right now, with what the bot would do.
const MR_PRESETS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"];
const MR_TIMES = ["5", "10", "30", "60"];
function monsterRallyTab(serial) {
  const root = h("div", { class: "mr" });
  let duty = null, rules = [], coords = { items: [], now: Date.now() / 1000 };
  let lane = "I", q = "", editing = -1, drag = null;           // picked lane, library search, chip whose levels are open, what is dragged
  const ruleFor = (name, lvl) => {
    const low = String(name || "").toLowerCase().trim();
    if (!low) return -1;
    return rules.findIndex((r) => r.on !== false && r.name && (low.includes(r.name.toLowerCase()) || r.name.toLowerCase().includes(low)) && (!lvl || (lvl >= (r.min || 1) && lvl <= (r.max || 99))));
  };
  const optVal = (k, d) => { const o = duty && (duty.opts || []).find((x) => x.k === k); return o && o.val !== undefined && o.val !== null ? o.val : d; };
  const setOpt = async (opt) => { const j = await act(api("duty_set", { serial, id: "monster_rally", opt })); if (j) take(j, false); };
  const saveRules = () => setOpt({ rules });
  const changed = () => { saveRules(); paintLanes(); paintPool(); paintCoords(); };
  const take = (j, redraw = true) => {
    duty = (j.duties || []).find((d) => d.id === "monster_rally") || null;
    const v = optVal("rules", null);
    if (redraw) rules = (Array.isArray(v) ? v.map((r) => ({ ...r })) : [{ name: "Ymir", min: 1, max: 99, preset: "I", time: "5", on: true }]).filter((r) => !isBoss(r.name));
    if (redraw) draw(); else paintHead();
  };
  const laneTime = (p) => String((rules.find((r) => r.preset === p) || {}).time || "5");
  const add = (name, p) => {
    p = p || lane;
    if (isBoss(name)) { toast(name + " is a world boss - nobody can join it, so it cannot be rallied here.", "error"); return; }
    if (rules.some((r) => r.preset === p && r.name.toLowerCase() === name.toLowerCase())) { toast(name + " is already on preset " + p + ".", "ok"); return; }
    rules.push({ name, min: 1, max: 99, preset: p, time: laneTime(p), on: true });
    changed();
  };
  const move = (i, p) => { if (rules[i].preset === p) return; rules[i].preset = p; rules[i].time = laneTime(p); changed(); };
  // world bosses nobody can join (rally_bot NEVER_JOIN) are left out of this page altogether (owner 23 Oct 08:24)
  const isBoss = (n) => (S.neverJoin || []).includes(String(n || "").toLowerCase().trim());
  const lvTxt = (r) => (r.min || 1) <= 1 && (r.max || 99) >= 99 ? "all levels" : (r.min === r.max ? "Lv" + r.min : "Lv" + (r.min || 1) + "–" + (r.max || 99));
  // ---- head: switch, state, Run now
  const head = h("div", { class: "panel mrhead" });
  const paintHead = () => {
    head.innerHTML = "";
    if (!duty) return;
    const st = { done: "ok", due: "due", waiting: "next", retry: "next", gaveup: "bad" }[duty.status] || "";
    head.append(
      h("label", { class: "tgl" }, h("input", { type: "checkbox", checked: duty.on, onchange: async (e) => { const j = await act(api("duty_set", { serial, id: "monster_rally", on: e.target.checked })); if (j) take(j, false); } }), h("span", { class: "sw" })),
      h("div", { class: "mrtitle" }, h("h2", {}, "Monster rallies"),
        h("small", { class: "sub" }, "Starts an Alliance War rally on monsters from the Telegram section, with the preset each monster sits on. Not tested in the game yet.")),
      duty.on ? h("span", { class: "rtnst " + st }, duty.status_text) : h("span", { class: "rtnst" }, "off"),
      h("button", { class: "btn", disabled: !duty.on, title: duty.on ? "" : "Switch it on first", onclick: async () => { if (await act(api("run_now_add", { serial, id: "duty:monster_rally" }))) toast("Monster rallies run next on this instance.", "ok"); } }, "Run now"));
  };
  // ---- preset lanes
  const lanes = h("div", { class: "mrlanes" });
  const dropOn = (el, p) => {
    el.addEventListener("dragover", (e) => { if (!drag) return; e.preventDefault(); el.classList.add("over"); });
    el.addEventListener("dragleave", () => el.classList.remove("over"));
    el.addEventListener("drop", (e) => { e.preventDefault(); el.classList.remove("over"); if (!drag) return;
      if (drag.i !== undefined) move(drag.i, p); else add(drag.name, p); drag = null; });
  };
  const paintLanes = () => {
    lanes.innerHTML = "";
    for (const p of MR_PRESETS) {
      const mine = rules.map((r, i) => [r, i]).filter(([r]) => r.preset === p);
      const box = h("div", { class: "mrlane" + (p === lane ? " sel" : "") + (mine.length ? "" : " empty"), onclick: (e) => { if (e.target.closest("button,input,select,.mrchip")) return; lane = p; paintLanes(); paintPool(); } },
        h("div", { class: "mrlhead" }, h("b", {}, "Preset " + p),
          mine.length ? h("select", { class: "mrtime", "aria-label": "Rally time for preset " + p, title: "Rally time",
            onchange: (e) => { mine.forEach(([r]) => (r.time = e.target.value)); saveRules(); paintCoords(); } },
            MR_TIMES.map((v) => h("option", { value: v, selected: v === laneTime(p) }, v + " min"))) : null),
        h("div", { class: "mrchips" },
          mine.length ? mine.map(([r, i]) => {
            const chip = h("span", { class: "mrchip" + (editing === i ? " edit" : ""), draggable: "true", title: "Drag to another preset \u00b7 click to set levels" },
              h("button", { class: "mrcl", onclick: () => { editing = editing === i ? -1 : i; paintLanes(); } }, r.name, h("small", {}, lvTxt(r))),
              editing === i ? h("span", { class: "mrlvl" }, "Lv",
                h("input", { type: "number", min: 1, max: 99, value: r.min || 1, "aria-label": r.name + " lowest level", onchange: (e) => { r.min = Math.max(1, Math.min(99, +e.target.value || 1)); if (r.min > (r.max || 99)) r.max = r.min; saveRules(); paintCoords(); } }), "–",
                h("input", { type: "number", min: 1, max: 99, value: r.max || 99, "aria-label": r.name + " highest level", onchange: (e) => { r.max = Math.max(1, Math.min(99, +e.target.value || 99)); if (r.max < (r.min || 1)) r.min = r.max; saveRules(); paintCoords(); } }),
                h("button", { class: "mrok", onclick: () => { editing = -1; paintLanes(); } }, "Done")) : null,
              h("button", { class: "mrx", "aria-label": "Remove " + r.name + " from preset " + p, title: "Remove", onclick: () => { rules.splice(i, 1); editing = -1; changed(); } }, "×"));
            chip.addEventListener("dragstart", (e) => { drag = { i }; try { e.dataTransfer.setData("text/plain", r.name); } catch (x) {} });
            chip.addEventListener("dragend", () => { drag = null; });
            return chip;
          }) : h("span", { class: "mrdrop" }, "Click or drag monsters here")));
      dropOn(box, p);
      lanes.appendChild(box);
    }
  };
  // ---- monster library (pick from it)
  const pool = h("div", { class: "mrpool" });
  const poolHead = h("div", { class: "mrpoolhead" });
  const names = () => [...new Set([...(S.monsters || []), ...(coords.items || []).map((c) => c.name).filter(Boolean)])].filter((n) => !isBoss(n)).sort((a, b) => a.localeCompare(b));
  const paintPool = () => {
    poolHead.innerHTML = "";
    const clr = h("button", { class: "mrclr", type: "button", title: "Clear the search", "aria-label": "Clear the search", hidden: !q,
      onclick: () => { q = ""; inp.value = ""; clr.hidden = true; paintChips(); inp.focus(); } }, "\u2715");
    const inp = h("input", { type: "text", class: "mrsearch", placeholder: "Search monsters", "aria-label": "Search monsters", value: q,
      oninput: (e) => { q = e.target.value; clr.hidden = !q; paintChips(); },
      onkeydown: (e) => { if (e.key === "Escape" && q) { e.preventDefault(); clr.click(); } } });
    poolHead.append(h("div", { class: "mrsbox" }, inp, clr),
      h("small", { class: "hint" }, "Click adds to ", h("b", {}, "Preset " + lane), " · or drag onto any preset"));
    paintChips();
  };
  const paintChips = () => {
    pool.innerHTML = "";
    const list = names().filter((n) => !q || n.toLowerCase().includes(q.toLowerCase()));
    if (!list.length) { pool.appendChild(h("div", { class: "mrempty" }, h("b", {}, q ? "No monster matches" : "No monsters known yet"), q ? h("button", { class: "btn sm", onclick: () => add(q.trim()) }, "Add “" + q.trim() + "” to Preset " + lane) : null)); return; }
    for (const n of list) {
      const on = rules.filter((r) => r.name.toLowerCase() === n.toLowerCase()).map((r) => r.preset);
      const chip = h("button", { class: "mrpchip" + (on.length ? " used" : ""), draggable: "true",
        title: on.length ? "On preset " + on.join(", ") + " \u00b7 click to add to Preset " + lane : "Add to Preset " + lane, onclick: () => add(n) },
        n, on.length ? h("span", { class: "mrtag" }, on.join(" ")) : null);
      chip.addEventListener("dragstart", (e) => { drag = { name: n }; try { e.dataTransfer.setData("text/plain", n); } catch (x) {} });
      chip.addEventListener("dragend", () => { drag = null; });
      pool.appendChild(chip);
    }
  };
  // ---- rules
  const num = (k, label, hint, d, max) => h("label", { class: "mrfield" }, h("span", { class: "lab" }, label),
    h("input", { type: "number", min: 0, max, value: optVal(k, d), onchange: (e) => setOpt({ [k]: e.target.value }) }), h("small", { class: "hint" }, hint));
  const rulesCard = () => panel(null, h("div", { class: "cardhead" }, h("h3", {}, "Rules")),
    h("div", { class: "mrfields" },
      num("max_wait", "Give up on a monster after", "minutes without a free march \u00b7 0 = stay until it is rallied", 0, 120),
      num("keep_free", "Marches kept free", "for joining other rallies", 0, 5),
      num("keep_stamina", "Keep stamina", "never start a rally under this", 100, 9999),
      num("max_age", "Coordinates up to", "minutes old", 30, 600)),
    h("label", { class: "tgl", style: { marginTop: "12px" } }, h("input", { type: "checkbox", checked: optVal("skip_busy", true) !== false, onchange: (e) => setOpt({ skip_busy: e.target.checked }) }), h("span", { class: "sw" }),
      h("span", {}, "Skip a monster that a march is already going to")));
  // ---- coordinates Telegram has now
  const cbox = h("div", { class: "mrcoords" });
  const ago = (t) => { const s = Math.max(0, Math.round(coords.now - t)); return s < 60 ? s + " s" : s < 3600 ? Math.round(s / 60) + " min" : Math.round(s / 3600) + " h"; };
  const paintCoords = () => {
    cbox.innerHTML = "";
    const maxAge = (+optVal("max_age", 30) || 30) * 60;
    const items = (coords.items || []).filter((c) => !isBoss(c.name)).slice(0, 40);
    if (!items.length) {
      cbox.appendChild(h("div", { class: "mrempty" }, h("b", {}, "No coordinates from Telegram yet"),
        h("span", {}, "Link Telegram and pick the chats to watch, then monsters show up here."),
        h("button", { class: "btn sm", onclick: () => { S.sel = "control"; S.tab = "telegram"; renderSide(); renderTabs(); renderPage(); } }, "Open Telegram settings")));
      return;
    }
    for (const c of items) {
      const i = ruleFor(c.name, c.level), r = rules[i], old = coords.now - c.t > maxAge;
      cbox.appendChild(h("div", { class: "mrc" + (i < 0 || old ? " dim" : "") },
        h("b", {}, (c.level ? "Lv" + c.level + " " : "") + (c.name || "?")),
        h("span", { class: "mrxy" }, "X " + c.x + "  Y " + c.y + " \u00b7 " + ago(c.t) + " ago" + (c.km != null ? " \u00b7 " + c.km + " km" : "")),
        i >= 0 && !old ? h("span", { class: "rtnst ok" }, "Preset " + r.preset + " · " + r.time + " min")
          : old ? h("span", { class: "rtnst" }, "too old")
          : rules.some((x) => x.on === false && x.name && String(c.name || "").toLowerCase().includes(x.name.toLowerCase())) ? h("span", { class: "rtnst" }, "off in your list")
          : h("button", { class: "btn sm", title: "Add this monster to Preset " + lane, onclick: () => add(c.name) }, "+ Preset " + lane)));
    }
  };
  const loadCoords = async () => { const j = await api("coords", {}, { quiet: true }).catch(() => null); if (j) { coords = j; paintCoords(); } };
  const draw = () => {
    root.innerHTML = "";
    paintHead(); paintLanes(); paintPool(); paintCoords();
    root.append(head, h("div", { class: "mrgrid" },
      h("div", { class: "mrcol" },
        panel(null, h("div", { class: "cardhead" }, h("div", {}, h("h3", {}, "Presets"), h("small", { class: "sub" }, "Each preset attacks the monsters on it. A monster can be on two presets with different levels."))), lanes),
        rulesCard()),
      h("div", { class: "mrcol" },
        panel(null, h("div", { class: "cardhead" }, h("div", {}, h("h3", {}, "Monsters"), h("small", { class: "sub" }, "Your monster library and the names Telegram sends"))), poolHead, pool),
        panel(null, h("div", { class: "cardhead" }, h("div", {}, h("h3", {}, "From Telegram now"), h("small", { class: "sub" }, "What the bot would do with each coordinate"))), cbox))));
  };
  act(api("duties", { serial })).then((j) => { if (j) take(j); });
  loadCoords();
  const tick = setInterval(async () => {
    if (!root.isConnected) { clearInterval(tick); return; }
    loadCoords();
    const j = await api("duties", { serial }, { quiet: true }).catch(() => null);
    if (j) take(j, false);
  }, 20000);
  return root;
}


// ====================================================================== routines (instance tab): master-detail around the game's daily activities
// Left: one row per activity of Tavern > Activity (points it gives, today's state, a switch). Right: the picked activity's own settings.
// UI only: every switch and setting is the same one as before (duty_set / the instance's own values), just shown per activity.
const ACT_GROUPS = ["City", "Market & Tavern", "Alliance", "Army", "Events"];
// [group, title, points, duty id | special, coming soon]
const ACTIVITIES = [
  ["City", "Troop Training", 3, "train_troops"], ["City", "Troop Healing", 2, "troop_healing"], ["City", "Trap Building", 2, "build_traps"],
  ["City", "Labor", 3, "labor"], ["City", "Resource Collecting", 1, "collect_resources"], ["City", "Gold Levy", 1, "gold_levy"],
  ["City", "Resource Tax", 1, "resource_tax"], ["City", "Offering", 3, "offering_tributes"], ["City", "City tasks", 0, "@tasks"],
  ["City", "Construction", 8, null, true], ["City", "Research", 8, null, true], ["City", "Material Composing", 1, null, true], ["City", "General Enhancing", 3, null, true],
  ["Market & Tavern", "Black Market", 4, "black_market"], ["Market & Tavern", "Wheel of Fortune", 3, "wheel"], ["Market & Tavern", "Trade Carriage", 3, "trade_carriage"],
  ["Market & Tavern", "Greet Champions", 2, "champions"], ["Market & Tavern", "Ranger Knight Order", 3, "ranger_knight"],
  ["Alliance", "Alliance Help", 1, "alliance_help"], ["Alliance", "Alliance Donation", 1, "alliance_science"],
  ["Army", "Monster Killing", 3, "@rally"], ["Army", "Stamina", 3, "@stamina"], ["Army", "Use the game's Auto-Join", 0, "@aj"],
  ["Army", "Patrol", 1, null, true], ["Army", "Relic Exploration", 5, null, true], ["Army", "Resource Gathering", 1, null, true],
  ["Events", "Crazy Eggs", 0, "crazy_eggs"], ["Events", "Wisdom Dome tokens", 0, "wisdom_dome_tokens"],
];
const ACT_MAX = 145;                   // the game's last Activity chest
const ACT_HIDDEN = new Set(["monster_rally", "reach_keep_tavern", "market_capture", "tavern_catalog", "reach_tavern", "reach_market", "view_reset_test", "reach_embassy"]);   // test duties: full duty list only; Monster rallies has its own tab
function routinesCards(serial, ov, details) {
  const root = h("div", { class: "rtn rtnmd" });
  let filter = "all", sel = null, duties = [];
  const eff = (k) => (ov && ov[k] !== undefined ? ov[k] : S.cfg[k]);
  const setCfg = (k, v) => { ov[k] = v; paintList(); };     // the page's autosave writes it within 2 s
  const byId = (id) => duties.find((d) => d.id === id);
  const nowText = () => (((S.now && S.now[serial]) || {}).text || "").toLowerCase();
  const STC = { done: "ok", due: "due", waiting: "next", retry: "next", gaveup: "bad" };
  // one item per row: activities first, then reward claims / other duties that are not an activity
  const items = () => {
    const d = S.devStatus.find((x) => x.id === serial) || {};
    const used = new Set(), out = [];
    for (const [g, t, pts, ref, soon] of ACTIVITIES) {
      const it = { key: "a:" + t, g, t, pts, soon: !!soon };
      if (soon) { it.n = "Coming soon"; out.push(it); continue; }
      if (ref === "@rally" || ref === "@stamina") {
        Object.assign(it, { on: eff("rally_enabled") !== false, set: (v) => setCfg("rally_enabled", v), open: ref === "@rally" ? "mon" : "sta",
          n: ref === "@rally" ? "Done by joining monster rallies" + (d.joins ? " · " + d.joins + " joined today" : "") : "Used by joining rallies",
          st: "by rallies", stc: "" });
      } else if (ref === "@aj") {
        Object.assign(it, { on: !!eff("game_auto_join"), set: (v) => setCfg("game_auto_join", v), open: "aj",
          n: "Needs the Rally Monthly Card · renew under " + (eff("auto_join_renew_min") || 120) + " min" });
      } else if (ref === "@tasks") {
        Object.assign(it, { on: eff("city_enabled") !== false, set: (v) => setCfg("city_enabled", v), open: "tasks", n: "Your own tap lists, run when no rally is waiting" });
      } else {
        const r = byId(ref);
        if (!r) continue;
        used.add(ref);
        Object.assign(it, dutyBits(r));
      }
      out.push(it);
    }
    for (const r of duties) {
      if (used.has(r.id) || ACT_HIDDEN.has(r.id)) continue;
      out.push(Object.assign({ key: "d:" + r.id, g: r.group === "Reward claims" ? "Reward claims" : "Other duties", t: r.title, pts: 0, claim: true }, dutyBits(r)));
    }
    return out;
  };
  const dutyBits = (r) => {
    const running = r.on && nowText() && nowText().includes(r.title.toLowerCase());
    const sum = (r.opts || []).filter((o) => o.val !== undefined && o.val !== null && o.val !== "").slice(0, 2)
      .map((o) => o.label + ": " + (o.type === "bool" ? (o.val ? "on" : "off") : o.val)).join(" · ");
    return { id: r.id, on: r.on, n: sum || r.note || r.schedule, st: !r.on ? "" : running ? "running now" : r.status_text, stc: running ? "run" : STC[r.status] || "",
      done: r.status === "done", gems: !!r.gems || r.id === "black_market", sched: r.schedule, status: r.status,
      set: async (v) => { const j = await act(api("duty_set", { serial, id: r.id, on: v })); if (j) { duties = j.duties; paintList(); } } };
  };
  const pass = (it) => filter === "all" ? !it.claim : filter === "on" ? !it.claim && it.on && !it.soon
    : filter === "todo" ? !it.claim && it.on && !it.soon && !it.done && it.st !== "by rallies" : filter === "soon" ? it.soon : filter === "claims" ? it.claim : true;
  const pills = h("div", { class: "rtnpills" });
  const listEl = h("div", { class: "rtnlist" });
  const head = h("div", { class: "rtnhead" });
  const detail = h("div", { class: "rtndetail panel" });
  root.append(h("div", { class: "rtnleft panel" }, head, pills, listEl), detail);
  const visible = () => items().filter(pass);
  const paintHead = (all) => {
    const pts = all.filter((i) => i.pts && (i.done)).reduce((t, i) => t + i.pts, 0);
    head.innerHTML = "";
    head.append(h("div", {}, h("h3", {}, "Dailies"), h("small", { class: "sub" }, "Tavern › Activity")),
      h("div", { class: "rtnpts", title: "Points from activities the bot finished today (rally kills not counted)" }, h("b", {}, String(pts)), " / " + ACT_MAX + " points today"));
    pills.innerHTML = "";
    const nClaims = all.filter((i) => i.claim).length;
    for (const [k, l] of [["all", "All"], ["on", "On"], ["todo", "Not done"], ["soon", "Coming soon"], ["claims", "Reward claims (" + nClaims + ")"]])
      pills.appendChild(h("button", { class: "rtnpill" + (filter === k ? " on" : ""), onclick: () => { filter = k; sel = null; paintList(true); } }, l));
  };
  const statusEl = (it) => it.soon ? h("span", { class: "rtnst" }, "soon")
    : !it.on ? h("span", { class: "rtnst" }, "off") : it.st ? h("span", { class: "rtnst " + it.stc }, it.st) : h("span", { class: "rtnst ok" }, "on");
  const paintList = (pickFirst) => {
    const all = items(), shown = all.filter(pass);
    paintHead(all);
    if (pickFirst || !shown.some((i) => i.key === sel)) { const f = shown.find((i) => !i.soon); sel = f ? f.key : null; }
    listEl.innerHTML = "";
    const groups = filter === "claims" ? ["Reward claims", "Other duties"] : ACT_GROUPS;
    for (const g of groups) {
      const mine = all.filter((i) => i.g === g), rows = shown.filter((i) => i.g === g);
      if (!rows.length) continue;
      listEl.appendChild(h("div", { class: "rtngrp" }, h("span", {}, g), h("span", { class: "cnt" }, mine.filter((i) => i.on && !i.soon).length + "/" + mine.filter((i) => !i.soon).length + " on")));
      for (const it of rows) {
        const row = h("div", { class: "rtnitem" + (it.soon ? " soon" : "") + (it.key === sel ? " sel" : ""), tabindex: it.soon ? null : "0", "data-key": it.key,
          onclick: (e) => { if (it.soon || e.target.closest(".tgl")) return; pick(it.key); } },
          h("label", { class: "tgl" + (it.soon ? " dis" : "") }, h("input", { type: "checkbox", checked: !!it.on, disabled: it.soon, onchange: (e) => it.set(e.target.checked) }), h("span", { class: "sw" })),
          h("div", { class: "rtntx" }, h("b", {}, it.t), h("small", { title: it.n || "" }, it.n || "")),
          statusEl(it), h("span", { class: "rtnpt" }, it.pts ? "+" + it.pts : ""), h("span", { class: "rtnchev" }, it.soon ? "" : "›"));
        listEl.appendChild(row);
      }
    }
    if (!shown.length) listEl.appendChild(note(filter === "todo" ? "Everything switched on is done for today." : "Nothing here."));
    paintDetail();
  };
  const pick = (key) => {
    if (key === sel) return;
    sel = key;
    listEl.querySelectorAll(".rtnitem").forEach((x) => x.classList.toggle("sel", x.dataset.key === key));
    paintDetail();
  };
  // keyboard: up / down moves the selection through the rows that can be opened
  root.addEventListener("keydown", (e) => {
    if ((e.key !== "ArrowDown" && e.key !== "ArrowUp") || !e.target.closest(".rtnlist")) return;
    const keys = [...listEl.querySelectorAll(".rtnitem:not(.soon)")].map((x) => x.dataset.key);
    const i = keys.indexOf(sel), n = keys[Math.max(0, Math.min(keys.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (!n) return;
    e.preventDefault(); pick(n);
    const el = listEl.querySelector('[data-key="' + CSS.escape(n) + '"]'); if (el) { el.focus(); el.scrollIntoView({ block: "nearest" }); }
  });
  const tile = (v, l, c) => h("div", { class: "stile" + (c ? " " + c : "") }, h("b", {}, v), h("span", {}, l));
  const paintDetail = () => {
    detail.innerHTML = "";
    if (filter === "soon") { detail.appendChild(panel(null, h("h3", {}, "Coming soon"), note("These activities are not done by the bot yet. They will get their own settings here when they are added."))); return; }
    const it = items().find((i) => i.key === sel);
    if (!it) { detail.appendChild(panel(null, note("Pick an activity on the left to see its settings."))); return; }
    const body = h("div", { class: "rtnbody" });
    const sub = [it.g, it.pts ? "+" + it.pts + " activity points" : null, it.st || null].filter(Boolean).join(" · ");
    body.appendChild(h("div", { class: "rtntop" },
      h("label", { class: "tgl" }, h("input", { type: "checkbox", checked: !!it.on, onchange: (e) => it.set(e.target.checked) }), h("span", { class: "sw" })),
      h("div", {}, h("h2", {}, it.t), h("small", { class: "sub" + (it.stc === "run" ? " run" : "") }, sub)),
      it.gems ? h("span", { class: "dbadge spend" }, "Spend guard") : null));
    if (it.id) body.appendChild(h("div", { class: "rtntiles" }, tile(it.on ? (it.st || "on") : "off", "Today", it.stc), tile(it.sched || "-", "Schedule"), tile(it.pts ? "+" + it.pts : "-", "Activity points")));
    // the activity's own settings, exactly as they were before
    const parts = [];
    if (it.id) {
      if (it.id === "black_market") parts.push(PAGES.duties(serial, "black_market"), ...details.market());
      else if (it.id === "collect_resources") parts.push(PAGES.duties(serial, it.id), ...details.map());
      else parts.push(PAGES.duties(serial, it.id));
    } else if (it.open === "mon" || it.open === "sta") {
      parts.push(panel(null, note(it.open === "mon" ? "Monster Killing points come from the rallies the bot joins. Pick the monsters, presets and generals on the Rally setup tab." : "Stamina points come from the stamina spent on rallies. The stamina floor and top-up are on the Rally setup tab."),
        btn("Open Rally setup →", () => { S.sec = S.sec || {}; S.sec.rally = it.open; if (S.gotoInstTab) S.gotoInstTab("rally"); }, "sm")));
    } else if (it.open === "aj") parts.push(...details.aj());
    else if (it.open === "tasks") parts.push(...details.tasks());
    for (const p of parts) if (p) body.appendChild(p);
    const runBtn = it.id ? h("button", { class: "btn", onclick: async () => { if (await act(api("run_now_add", { serial, id: "duty:" + it.id }))) toast(it.t + " runs next on this instance.", "ok"); } }, "Run now") : null;
    detail.append(body, h("div", { class: "rtnbar2" }, runBtn));
  };
  let touched = false;                     // the owner clicked a row: keep it selected when the duties arrive
  root.addEventListener("click", (e) => { if (e.target.closest(".rtnitem,.rtnpill")) touched = true; }, true);
  paintList(true);
  requestAnimationFrame(() => { const sh = document.querySelector(".stickyhead"); if (sh) root.style.setProperty("--shh", sh.offsetHeight + "px"); });
  act(api("duties", { serial })).then((j) => { if (j) { duties = j.duties; paintList(!touched); } });
  // keep today's states fresh (every 20 s) without rebuilding the open settings
  const paintStates = () => {
    const all = items(); paintHead(all);
    for (const el of listEl.querySelectorAll(".rtnitem")) {
      const it = all.find((i) => i.key === el.dataset.key); if (!it) continue;
      const old = el.querySelector(".rtnst"); if (old) old.replaceWith(statusEl(it));
      const cb = el.querySelector("input"); if (cb) cb.checked = !!it.on;
    }
  };
  const tick = setInterval(async () => {
    if (!root.isConnected) { clearInterval(tick); return; }
    const j = await api("duties", { serial }, { quiet: true }).catch(() => null);
    if (j && j.duties) { duties = j.duties; paintStates(); }
  }, 20000);
  return root;
}


// ====================================================================== logs (Control settings > Logs): save the log right here, then the live log
// ====================================================================== reports: who joined what, per day / week / month
PAGES.reports = function () {
  S.repDays = S.repDays || 1;
  const ranges = [[1, "Today"], [7, "7 days"], [30, "30 days"]];
  const seg = h("div", { class: "seg", role: "tablist" }, ...ranges.map(([d, l]) => h("button", { class: "segb" + (S.repDays === d ? " on" : ""), role: "tab", onclick: () => { S.repDays = d; rerender(); } }, l)));
  const body = h("div", { class: "repbody" }, h("div", { class: "empty" }, "Loading..."));
  const tile = (v, l) => h("div", { class: "stile" }, h("b", {}, String(v)), h("span", {}, l));
  const chips = (mons, max) => { const e = Object.entries(mons).sort((a, b) => b[1] - a[1]); return h("div", { class: "repchips" }, ...e.slice(0, max || 99).map(([m, n]) => h("span", { class: "repchip" }, m, h("b", {}, n))), max && e.length > max ? h("span", { class: "repmore" }, "+" + (e.length - max) + " more") : null); };
  (async () => {
    const j = await act(api("joins_report", { days: S.repDays }, { quiet: true }));
    body.innerHTML = "";
    if (!j) { body.appendChild(h("div", { class: "empty" }, "Could not load the report.")); return; }
    const rows = j.rows || [];
    const all = { joins: 0, late: 0, restarts: 0, monsters: {} };
    rows.forEach((r) => { all.joins += r.joins; all.late += r.late; all.restarts += r.restarts; Object.entries(r.monsters).forEach(([m, n]) => (all.monsters[m] = (all.monsters[m] || 0) + n)); });
    if (!all.joins && !all.late) { body.appendChild(h("div", { class: "empty repempty" }, S.repDays === 1 ? "No rallies joined yet this game day. Joins show here as the instances join them - they start from 0 after the game's daily reset." : "No joins recorded in this period yet.")); return; }
    body.appendChild(h("div", { class: "reptiles" }, tile(all.joins, "Rallies joined"), tile(Object.keys(all.monsters).length, "Monster types"), tile(all.late, "Skipped (too late)"), tile(all.restarts, "Game restarts")));
    const open = S.repOpen || (S.repOpen = {});
    const list = h("div", { class: "replist" });
    const top = Math.max(1, ...rows.map((r) => r.joins));
    rows.forEach((r) => {
      const det = h("div", { class: "repdet", hidden: !open[r.id] }, Object.keys(r.monsters).length ? chips(r.monsters) : h("span", { class: "hint" }, "No joins in this period."));
      const row = h("div", { class: "reprow" + (open[r.id] ? " open" : ""), onclick: () => { open[r.id] = !open[r.id]; det.hidden = !open[r.id]; row.classList.toggle("open", !!open[r.id]); } },
        h("div", { class: "repname" }, h("b", {}, r.name), h("small", {}, r.late ? r.late + " too late" + (r.restarts ? " \u00b7 " + r.restarts + " restarts" : "") : (r.restarts ? r.restarts + " restarts" : "\u00a0"))),
        h("div", { class: "repbar" }, h("span", { style: { width: (100 * r.joins / top).toFixed(1) + "%" } })),
        h("div", { class: "repnum" }, r.joins), chips(r.monsters, 3), h("span", { class: "rtnchev" }, "\u203A"));
      list.append(row, det);
    });
    body.append(panel("By instance", note("Click an instance to see every monster it joined."), list),
      panel("By monster - all instances", chips(all.monsters)));
  })();
  return h("div", {}, panel(null, h("div", { class: "rephead" }, h("div", {}, h("h3", {}, "Rally report"), h("small", { class: "sub" }, "Which instance joined which monsters. Today counts from the game's daily reset; it survives app restarts.")), seg)), body);
};

PAGES.logs = function () {
  const save = panel("Save the log", h("div", { class: "hint", style: { marginBottom: "10px" } }, "Pick how much and which instance, then save a small zip in Downloads to send for support."),
    saveLogRow(),
    h("div", { class: "row", style: { marginTop: "12px" } }, btn("Copy last lines", copyLog, "sm"), btn("Report a problem...", saveReportFlow, "sm"),
      btn("Open logs folder", () => act(api("open_logs")), "sm")));
  const live = panel("Live log - all instances", h("div", { class: "row", style: { marginBottom: "10px" } },
      btn("Clear", async () => { await act(api("clear_log")); S.logs = []; logViews.forEach((v) => (v.el.innerHTML = "")); }, "sm")), logBox(null, true));
  return h("div", {}, save, live);
};
