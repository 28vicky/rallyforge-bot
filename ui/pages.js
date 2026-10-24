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
  const pairs = opts.map((x) => (Array.isArray(x) ? x : [x, x]));
  const cap = (l) => (typeof l === "string" && /^[a-z]/.test(l) ? l[0].toUpperCase() + l.slice(1) : l);   // never show a raw lowercase value
  if (!o.dropdown && pairs.length >= 2 && pairs.length <= 5 && pairs.every(([, l]) => String(l).length <= 22)) {
    // a short pick-one list is a segmented control (rule 15); same value conversions as the dropdown
    const st = { v: String(o.get ? o.get(obj[key]) : obj[key] ?? pairs[0][0]) };
    return segPick(st, "v", pairs.map(([v, l]) => [String(v), cap(l)]), { small: true, label: o.label || key,
      after: (v) => { obj[key] = o.set ? o.set(v) : o.num ? Number(v) : v; if (o.after) o.after(); } });
  }
  const el = h("select", {}, pairs.map(([v, l]) => h("option", { value: String(v) }, cap(l))));
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
// ---- Auto-Join (the game's own, needs the Rally Monthly Card): its own top-level switch per instance, never part of presets (owner 24 Oct).
// Per instance it is per_device[serial].game_auto_join; the global switch (Control bar, next to Start all) sets every instance at once.
function ajIsOn(serial) { const o = ((S.cfg.per_device || {})[serial]) || {}; return !!(o.game_auto_join !== undefined ? o.game_auto_join : S.cfg.game_auto_join); }
function ajSetOne(serial, v) { S.cfg.per_device = S.cfg.per_device || {}; (S.cfg.per_device[serial] = S.cfg.per_device[serial] || {}).game_auto_join = !!v; paintAjAll(); }
const AJ_TIP = "The game's own Auto-Join (needs the Rally Monthly Card). On: the game joins rallies by itself and the bot keeps it running. No presets needed.";
function ajSwitch(serial, o) {
  o = o || {};
  const i = h("input", { type: "checkbox", checked: ajIsOn(serial), "aria-label": "Auto-Join" });
  i.addEventListener("change", () => { ajSetOne(serial, i.checked); if (o.after) o.after(i.checked); });
  return h("label", { class: "tgl ajsw" + (o.small ? " sm" : ""), title: AJ_TIP, onclick: (e) => e.stopPropagation() }, i, h("span", { class: "sw" }), o.label === undefined ? "Auto-Join" : o.label);
}
function ajSetAll(v) { S.cfg.game_auto_join = !!v; S.devices.forEach((d) => { if (d.id) ajSetOne(d.id, v); }); paintAjAll(); }
function paintAjAll() {
  const g = document.getElementById("ajAll");
  if (!g) return;
  const ids = S.devices.filter((d) => d.id).map((d) => d.id), n = ids.length, on = ids.filter(ajIsOn).length;
  const i = g.querySelector("input"); i.checked = n > 0 && on === n; i.disabled = !n;
  g.classList.toggle("greyed", !n);
  g.querySelector(".ajcnt").textContent = !n ? "" : on === n ? "on for all" : on ? on + " of " + n + " on" : "off";
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
  // ---- your instances (Apple pass 24 Oct): the Dashboard is a status screen, so the list folds into one line
  // "N instances · Manage" that opens it in the right side panel. Name and address are edited in place there,
  // x asks before it removes, Detect is the panel's main action. New users keep the "Get started" steps.
  const applyDevs = () => act(api("set_devices", { devices: S.devices.filter((d) => d.id) }, { quiet: true })).then(() => poll1());
  const instRows = (rows, after) => {
    const draw = () => {
      rows.innerHTML = "";
      if (!S.devices.length) rows.appendChild(h("div", { class: "empty" },
        h("b", {}, "No instances yet"), h("span", {}, "Open your emulators with Evony, then press Detect emulators.")));
      S.devices.forEach((d, i) => {
        const label = d.name || d.id || "this row";
        rows.appendChild(h("div", { class: "irow" },
          h("span", { class: "inum" }, String(i + 1)),
          h("input", { type: "text", class: "iname", value: d.name, placeholder: "Name", "aria-label": "Name", oninput: (e) => (d.name = e.target.value.trim()), onchange: applyDevs }),
          h("input", { type: "text", class: "iaddr", value: d.id, placeholder: "127.0.0.1:5565", "aria-label": "Address", oninput: (e) => (d.id = e.target.value.trim()), onchange: applyDevs }),
          h("button", { class: "ix", type: "button", title: "Remove " + label, "aria-label": "Remove " + label, onclick: async () => {
            if (d.id && !(await confirmBox("Remove " + label + "?", "The bot stops using this instance. Detect will not add it again until you choose Show removed again. Its settings are kept.", "Remove"))) return;
            S.devices.splice(i, 1); draw(); applyDevs(); if (after) after();
          } }, "✕")));
      });
    };
    draw();
    return draw;
  };
  const addRow = (rows, draw) => { S.devices.push({ id: "", name: "" }); draw(); const ins = rows.querySelectorAll(".iaddr"); if (ins.length) ins[ins.length - 1].focus(); };
  const showRemoved = async () => { if (!(await confirmBox("Removed instances", "Instances you removed are never added again by Detect. Clear that list so the next Detect can add them?"))) return; const j = await act(api("restore_removed")); if (j) toast(j.restored ? `${j.restored} instance(s) can be detected again. Press Detect emulators.` : "No instances were removed.", "ok"); };
  // the side panel (same look as a duty's settings panel)
  const side = h("div", { class: "sideov", onclick: (e) => { if (e.target === side) closeSide(); } });
  let opener = null;
  const sideKey = (e) => { if (e.key === "Escape" && !document.querySelector(".ov")) closeSide(); };
  const closeSide = () => { side.remove(); document.removeEventListener("keydown", sideKey); rerender(); const m = document.querySelector(".imanage"); if (m) m.focus(); else if (opener) opener.focus(); };
  const openSide = (e) => {
    opener = e && e.currentTarget;
    const rows = h("div", { class: "ilist" });
    const cnt = h("small", {});
    const count = () => { const n = S.devices.filter((d) => d.id).length; cnt.textContent = n + (n === 1 ? " instance" : " instances") + " · changes save as you make them"; };
    const draw = instRows(rows, count);
    count();
    side.innerHTML = "";
    side.appendChild(h("aside", { class: "sidep", role: "dialog", "aria-modal": "true", "aria-label": "Your instances" },
      h("div", { class: "sph" },
        h("div", { class: "spt" }, h("h3", {}, "Your instances"), cnt),
        h("button", { class: "spx", type: "button", "aria-label": "Close", title: "Close (Esc)", onclick: closeSide }, "×")),
      h("div", { class: "spb" },
        rows,
        h("div", { class: "row ilinks" },
          h("a", { href: "#", class: "lnk", onclick: (ev) => { ev.preventDefault(); addRow(rows, draw); } }, "Add by address"),
          h("a", { href: "#", class: "lnk", onclick: (ev) => { ev.preventDefault(); showRemoved(); } }, "Show removed again")),
        note("Drag instances in the left list to change the order. Instances you remove stay removed, even after a restart.")),
      h("div", { class: "spf" }, btn("Detect emulators", detectDevices, "pri"), h("span", { style: { flex: "1" } }), h("button", { class: "btn", type: "button", onclick: closeSide }, "Done"))));
    if (!side.isConnected) { document.body.appendChild(side); document.addEventListener("keydown", sideKey); }
    if (window.watchNotes) window.watchNotes(side);
    const f = side.querySelector(".iname"); (f || side.querySelector(".spx")).focus();
  };
  // ---- first run: three steps, each with one action (shown until the first instance is added)
  const none = !S.devices.some((d) => d.id);
  const step = (n, title, sub, action, done) => h("div", { class: "fstep" + (done ? " done" : "") },
    h("span", { class: "fnum" }, done ? "✓" : String(n)), h("div", { class: "ftx" }, h("b", {}, title), h("small", {}, sub)), action);
  const firstRows = h("div", { class: "ilist" });
  const first = none ? panel(null, h("div", { class: "cardhead" }, h("h3", {}, "Get started")),
    h("div", { class: "fsteps" },
      step(1, "Find your emulators", "Open them with Evony on screen.", h("div", { class: "row", style: { gap: "12px" } },
        h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); S.devices.push({ id: "", name: "" }); rerender(); } }, "Add by address"),
        btn("Detect emulators", detectDevices, "pri sm")), false),
      step(2, "Auto-Join", "Have the Rally Monthly Card? Switch Auto-Join on at the top of each instance - no presets needed. Otherwise the bot joins with your presets.", null, false),
      step(3, "Start", "The bot joins rallies and does your duties.", h("button", { class: "btn sm", disabled: true, title: "Add an instance first" }, "Start all"), false)),
    S.devices.length ? firstRows : null) : null;
  if (none && S.devices.length) instRows(firstRows);       // an address row the user just added: typed in place, under the steps
  // ---- instances found by Detect but not added: one click each (nothing is added by itself). Plain buttons: Start is the page's one main button
  const addFound = async (ids) => {
    const j = await act(api("add_found", { ids }));
    if (j) { S.devices = j.devices; S.avail = j.available; toast(ids.length > 1 ? `${ids.length} instances added.` : "Instance added.", "ok"); rerender(); poll1(); }
  };
  const avail = (S.avail || []).filter((a) => !S.devices.some((d) => d.id === a.id));
  const found = avail.length ? panel("Found instances (not in your list yet)",
    note("Detect only shows what it found. Press Add for each instance you want the bot to use."),
    ...avail.map((a) => h("div", { class: "row", style: { marginBottom: "6px", gap: "14px" } },
      h("span", { style: { minWidth: "170px", fontFamily: "var(--mono)" } }, a.id),
      h("span", { class: "mute", style: { minWidth: "140px" } }, (a.name || "") + (a.removed ? " (removed earlier)" : "")),
      btn("Add", () => addFound([a.id]), "sm"))),
    avail.length > 1 ? h("div", { class: "row", style: { marginTop: "8px" } }, btn("Add all " + avail.length, () => addFound(avail.map((a) => a.id)), "sm")) : null) : null;
  const n = S.devices.filter((d) => d.id).length;
  const line = h("div", { class: "instline" },
    h("span", { id: "instN" }, n + (n === 1 ? " instance" : " instances")), h("span", { class: "sep" }, "·"),
    h("button", { class: "lnkb imanage", type: "button", title: "Names, addresses, add or remove instances", onclick: openSide }, "Manage"));
  const log = panel(null, activityBox(null, "Activity - all instances"));
  return none ? h("div", {}, first, found) : h("div", {}, renderEventCards(), renderTodayAll(), runNowCard(""), line, found, log);
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
        modal({ title: "Load profile into this instance", body: h("div", {}, h("p", { class: "note" }, "This replaces the settings of this instance with the saved ones (its generals, presets and own settings). Other instances are not changed."), list), buttons: [
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
    { label: "Take game back now", run: () => act(api("takeback", { serial })) },
    { label: "Save profile\u2026", run: saveInstProfile }, { label: "Load profile\u2026", run: loadInstProfile }]), "");
  const bar = h("div", { class: "row instbar" },
    stBtn("Start", () => collectAndRun("start", { serial }), "pri"), stBtn("Pause", () => act(api("pause", { serial })), "pri"), stBtn("Stop", () => act(api("stop", { serial })), "stopb"), moreBtn);
  const head = h("div", { class: "row insthead", style: { margin: "0 0 12px", gap: "14px", alignItems: "center" } },
    sideBtn(), h("h2", { title: serial, style: { fontSize: Math.max(18, Math.min(40, parseInt(S.cfg.ui_name, 10) || 24)) + "px", lineHeight: "1.1", fontWeight: "800", margin: "0" } }, dev.name || "Instance " + d.idx),
    status, ajSwitch(serial, { after: () => rerender() }), h("span", { class: "mute iserial", style: { fontSize: "13px" } }, serial), h("span", { style: { flex: "1" } }), saveStEl(), bar);
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
  const ifOwn = [...IF_KEYS].some((k) => ov[k] !== undefined);
  const panelEl = panel("Instance settings",
    ownLine(ifOwn, () => toast("Pick a value in any field below - only that one becomes this instance's own."),
      async () => { if (await confirmBox("Reset to defaults", "Every field on this card goes back to the universal setting.", "Reset")) { IF_KEYS.forEach((k) => delete ov[k]); rerender(); } }),
    fields,
    h("div", { class: "row", style: { marginTop: "14px" } }, btn("Copy settings from\u2026", copyMenu, "sm"),
      btn("Use these on all instances\u2026", async () => { if (!(await confirmBox("Use on all instances", "Every other instance gets a copy of this instance's own settings. Their own settings are replaced.", "Copy"))) return;
        for (const x of S.devices) if (x.id && x.id !== serial) S.cfg.per_device[x.id] = clone(S.cfg.per_device[serial] || {}); toast("Copied to all other instances.", "ok"); }, "sm")));
  // ---- own presets & generals (stored in this instance's overrides; blank = the universal ones)
  const PKEYS = ["bot_presets", "preset_mode", "general_main", "general_assistant", "general_main_tab", "general_assistant_tab", "general_main_fav",
    "general_assistant_fav", "general_on_fail", "general_idle_only", "general_assistant_optional", "general_view", "max_troops"];
  const customP = "bot_presets" in ov;
  const pview = new Proxy(ov, { get: (t, k) => (k in t ? t[k] : S.cfg[k]), set: (t, k, v) => { t[k] = v; return true; } });
  const ownP = [panel("March presets & generals",
    ownLine(customP, () => { ensureBotPresets(S.cfg); PKEYS.forEach((k) => { if (S.cfg[k] !== undefined) ov[k] = clone(S.cfg[k]); }); rerender(); },
      async () => { if (await confirmBox("Reset to defaults", "This instance drops its own presets and generals and uses the universal ones again.", "Reset")) { PKEYS.forEach((k) => delete ov[k]); rerender(); } }),
    customP ? null : note("Make own to give this instance its own presets and generals; it starts as a copy of the universal set (Control settings \u203a Presets & generals)."))];
  if (customP) ownP.push(...presetPanels(pview, true));
  // ---- phone / shared device (important, so it sits right under Activity)
  const eff = (k, dflt) => (ov[k] !== undefined ? ov[k] : S.cfg[k] !== undefined ? S.cfg[k] : dflt);
  const rcOn = h("input", { type: "checkbox", checked: !!eff("reclaim_elsewhere", true) });
  rcOn.addEventListener("change", () => { ov.reclaim_elsewhere = rcOn.checked; });
  const rcMin = h("input", { type: "number", min: 0, class: "short", "aria-label": "Minutes to wait" });
  rcMin.value = String(eff("reclaim_grace_min", 10));
  rcMin.addEventListener("input", () => { const t = rcMin.value.trim(); if (t === "") delete ov.reclaim_grace_min; else ov.reclaim_grace_min = Math.max(0, parseInt(t, 10) || 0); });
  const PH_KEYS = ["reclaim_elsewhere", "reclaim_grace_min"];
  const phone = panel("When I play on another device",
    ownLine(PH_KEYS.some((k) => ov[k] !== undefined), () => { PH_KEYS.forEach((k) => { ov[k] = eff(k, k === "reclaim_elsewhere" ? true : 10); }); rerender(); },
      () => { PH_KEYS.forEach((k) => delete ov[k]); rerender(); }),
    h("div", { class: "hint", style: { marginBottom: "10px" } }, "Opening the game on another device kicks this instance off. With this on, the bot counts down, then taps \u201cRestart\u201d in the game's pop-up and carries on."),
    h("div", { class: "row", style: { gap: "22px", alignItems: "center" } },
      h("label", { class: "tgl" }, rcOn, h("span", { class: "sw" }), "Wait, then take the game back"),
      h("span", {}, "Wait "), rcMin, h("span", {}, " minutes"),
      btn("Take game back now", () => act(api("takeback", { serial })))));
  // ---- the game's own Auto-Join (needs the Rally Monthly Card): per instance, right under the "game kicked" box
  const numOv = (key, dflt) => { const e = h("input", { type: "number", min: 0, class: "short", "aria-label": key === "auto_join_renew_min" ? "Renew minutes" : "Check minutes" });
    e.value = String(eff(key, dflt));
    e.addEventListener("input", () => { const t = e.value.trim(); if (t === "") delete ov[key]; else ov[key] = Math.max(0, parseInt(t, 10) || 0); }); return e; };
  const ajOn = h("input", { type: "checkbox", checked: !!eff("game_auto_join", false) });
  ajOn.addEventListener("change", () => { ov.game_auto_join = ajOn.checked; });
  const AJ_KEYS = ["auto_join_renew_min", "auto_join_check_min"];
  const autoJoin = panel("Auto-Join (the game's own, needs the Rally Monthly Card)",
    ownLine(AJ_KEYS.some((k) => ov[k] !== undefined), () => { ov.auto_join_renew_min = eff("auto_join_renew_min", 120); ov.auto_join_check_min = eff("auto_join_check_min", 10); rerender(); },
      () => { AJ_KEYS.forEach((k) => delete ov[k]); rerender(); }),
    h("div", { class: "hint", style: { marginBottom: "10px" } }, "On: the bot stops joining rallies itself. It opens Alliance War > Auto-Join, reads the time left and presses Start again when it falls under your limit. Only for accounts that have the card."),
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
  const sections = (key, list) => [pillSections(key, list)];
  const mapPanel = () => panel("Map the city",
    note("The bot needs to know where the buildings are. Scan city reads the names while it holds a finger on the screen. Map by hand is the manual way when scanning does not fit your city style."),
    h("div", { class: "row", style: { gap: "8px", flexWrap: "wrap" } }, btn("Scan city", () => scanCityFlow(serial)), btn("Map by hand", () => manualMapFlow(serial))));
  // Settings tab: sections as pills on top (C5), same as Rally setup; saving + Undo are in the header
  const settingsTab = () => [...sections("settings", [
    ["phone", "Phone & Auto-Join", () => [phone, autoJoin, autoStart]],
    ["inst", "Instance settings", () => [panelEl, mapPanel()]],
    ["adv", "Advanced", () => [panel("Advanced (rarely needed)", moreFields,
      h("div", { class: "field", style: { marginTop: "14px" } }, h("label", {}, "Any other setting as JSON"), adv,
        h("div", { class: "hint" }, "Only for settings that have no field above, e.g. {\"threshold\": 0.8}. Leave empty if unsure.")))]],
  ]), h("p", { class: "hint", style: { marginTop: "12px" } }, "These settings apply to " + (dev.name || short(serial)) + " only.")];
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
      // a new instance (no own settings yet) leads with Auto-Join: one question, one click; nothing is written until the user picks
      const how = { m: "" };
      const lead = Object.keys(ov).length ? null : panel(null,
        h("div", { class: "cardhead" }, h("h3", {}, "How should " + (dev.name || "this instance") + " join rallies?")),
        segPick(how, "m", [["game", "The game's Auto-Join", AJ_TIP], ["bot", "The bot joins with your presets", "The bot opens each rally and marches with your presets."]],
          { label: "How it joins", after: (v) => { ajSetOne(serial, v === "game"); rerender(); } }),
        h("p", { class: "hint", style: { marginTop: "8px" } }, "Have the Rally Monthly Card? Pick Auto-Join - no presets needed. You can change it any time with the Auto-Join switch at the top."));
      return [lead, renderTodayCard(serial), h("div", { class: "dutygrid ovgrid" }, h("div", { class: "dcol" }, panel(null, L), panel(null, R)), h("div", { class: "dcol" }, cityMapCard(serial), runNowCard(serial)))];
    }],
    ["routines", "Routines", () => [PAGES.duties(serial)]],
    ["rally", "Rally setup", () => sections("rally", [
      ["mon", "Monsters", () => [PAGES.monsters(serial)]],
      ["gen", "March presets & generals", () => ownP],
      ["sta", "Stamina", () => { const sc = scopeBar("stamina", STAMINA_KEYS, "stamina settings", serial);
        const el = staminaPanel(sc.own ? ov : S.cfg, S.cfg, sc.bar);
        if (sc.locked) [...el.children].forEach((x) => { if (x !== sc.bar && x.tagName !== "H3") lockedWrap(x, true); });   // shows the universal values, read-only until Make own
        return [el]; }]])],
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
    ...rows.map((r) => h("div", { class: "itrow" }, r.st ? dutyBadge(r.st, r.when) : h("span", { class: "itwhen" }, r.when), h("span", { class: "itname" }, r.name)))) : null;
  const load = async () => {
    if (!root.isConnected) return;
    const j = await api("task_history", {}, { quiet: true }).catch(() => null);
    if (!j) return;
    const row = (j.matrix || []).find((m) => m.id === serial);
    const d = (row && row.duties) || [];
    const mk = (f) => d.filter(f).map((x) => ({ when: x.text, st: x.status, name: dutyName(x.id, x.title) }));
    const mine = (j.events || []).filter((e) => row && e.emu === row.emu).slice(0, 12).map((e) => ({ when: fmtT(e.t), name: e.task + (e.ok ? "" : " - failed" + (e.msg ? ": " + e.msg : "")) }));
    root.innerHTML = "";
    const parts = [grp("Due now", mk((x) => x.status === "due" || x.status === "retry"), true), grp("Gave up today - still tried every hour", mk((x) => x.status === "gaveup"), true),
      grp("Coming up", mk((x) => x.status === "waiting")), grp("Done today", mk((x) => x.status === "done")), grp("Latest things the bot did here", mine)].filter(Boolean);
    root.appendChild(panel("Itinerary",
      h("small", { class: "sub" }, "What this instance has to do today" + (row && row.count && row.count.on ? " \u00b7 " + row.count.done + " / " + row.count.on + " done" : "") + ". Changes in Routines are picked up without stopping it."),
      ...(parts.length ? parts : [h("div", { class: "empty" }, "No duty is switched on for this instance. ", h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); if (S.gotoInstTab) S.gotoInstTab("routines"); } }, "Switch some on in Routines"))])));
  };
  root.appendChild(note("Loading\u2026"));
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
const PRESET_KEYS = ["bot_presets", "preset_mode", "general_main", "general_assistant", "general_main_tab", "general_assistant_tab", "general_main_fav",
  "general_assistant_fav", "general_on_fail", "general_idle_only", "general_assistant_optional", "general_view", "max_troops"];
PAGES.presets = function () {
  const sc = scopeBar("presets", PRESET_KEYS, "presets and generals");
  return h("div", {}, panel(null, sc.bar), ...presetPanels(S.cfg, false));
};
async function captureTemplate() {
  const shot = await pickScreenshot("Screenshot containing the button");
  if (!shot) return;
  const rect = await pickOnShot(shot, "Drag a box tightly around the button or the pop-up's close button, then let go.", "region");
  if (!rect) return;
  const name = await promptBox("Template name", "Name for this picture.\n\npopup_anything  = a pop-up button the bot taps whenever it sees it (e.g. popup_ok)\nrally_icon, alliance_button, war_button = steps to reach the rally list\nassistant_card = the Assistant General slot on the march screen\nmarch_screen, city_screen, rally_screen = screens the bot can recognise\nfav_off = the heart filter when it is off\nno_general = the empty general slot (presets showing it are skipped)\npopup_exit_cancel = Cancel on the 'quit game' dialog", "");
  if (!name) return;
  const j = await act(api("template_add", { token: shot.token, rect, name }));
  if (j) toast(`Saved template '${j.name}'`, "ok");
}

// ====================================================================== settings (+ low RAM)
const STAMINA_KEYS = ["stamina_auto", "stamina_count", "stamina_amount", "stamina_items", "stamina_order"];
function staminaPanel(store, base, top) {
  // base = where the value comes from when this instance has none of its own (the universal settings)
  const g = (k, d) => (store[k] !== undefined ? store[k] : base[k] !== undefined ? base[k] : d);
  const S_ = (k, opts, d) => sel(store, k, opts, { get: () => g(k, d) });
  return panel("Stamina (when a march needs more than you have)", top || null,
    h("div", { class: "hint", style: { marginBottom: "10px" } }, "The bot uses stamina items only (never gems). Choose which items it may use, in which order, and how many."),
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
PAGES.settings = function () { return PAGES.settings0(); };            // themes live on the Appearance page only
// T71: start the app with Windows - a Windows setting, saved at once (not part of Save / Discard)
function winStartRow() {
  const box = h("input", { type: "checkbox", disabled: true });
  const row = h("div", { style: { marginBottom: "8px" } }, h("label", { class: "tgl" }, box, h("span", { class: "sw" }), "Start RallyForge when Windows starts"));
  api("win_startup").then((j) => {
    if (!j || !j.supported) { row.style.display = "none"; return; }
    box.checked = !!j.on; box.disabled = false;
  }).catch(() => { row.style.display = "none"; });
  box.addEventListener("change", async () => {
    box.disabled = true;
    const j = await act(api("win_startup_set", { on: box.checked }));
    if (j) { box.checked = !!j.on; toast(j.on ? "RallyForge will start with Windows." : "RallyForge will no longer start with Windows.", "ok"); }
    else box.checked = !box.checked;
    box.disabled = false;
  });
  return row;
}
PAGES.settings0 = function () {
  const c = S.cfg;
  // (ui-simple) plain groups; same settings keys and saved values - only the labels, order and choice names changed
  const sec = (title, ...kids) => h("div", { class: "sgroup" }, h("h4", {}, title), ...kids);
  const joinSec = () => panel(null,
    sec("Joining rallies",
      h("div", { class: "fgrid" },
        fld("Join only with this much time to spare (s)", inp(c, "safety_margin", { type: "int" })),
        fld("Skip rallies longer than (s, 0 = any)", inp(c, "max_rally_seconds", { type: "int" })),
        fld("Most joins per instance (0 = no limit)", inp(c, "max_joins", { type: "int" })),
        fld("My march time if it can't be read (s)", inp(c, "march_guess", { type: "int", get: (v) => (v === undefined ? 10 : v) })),
        fld("Read the rally countdown from", sel(c, "rally_timer_source", [["progress_bar", "Progress bar"], ["join_button", "Join button"]])),
        fld("Read my march time from", sel(c, "march_timer_source", [["join_button", "Join button"], ["progress_bar", "Progress bar"], ["none", "Don't read it"]], { get: (v) => v || "none", set: (v) => (v === "none" ? "" : v) })))));
  const speedSec = () => panel(null,
    sec("Speed",
      h("div", { class: "fgrid" },
        fld("Load on the PC", sel(c, "cpu_level", [[1, "Fastest"], [2, "Balanced"], [3, "Light"], [4, "Lightest"]], { num: true, get: (v) => v || 2 })),
        fld("Wait after each tap (ms)", inp(c, "click_delay_ms", { type: "int" })),
        fld("Pause lasts (min, 0 = until I resume)", inp(c, "pause_minutes", { type: "int", get: (v) => (v === undefined ? 15 : v) })))),
    sec("Starting up",
      h("div", { class: "tgls" }, tog(c, "auto_launch_emulator", "Start the emulator if it is closed"), tog(c, "auto_launch_game", "Open Evony and wait until it has loaded"))),
    );
  const xy = (key, def) => inp(c, key, { cls: "short", get: xyText, set: (t) => parseXY(t, def) });
  const advSec = () => panel(null,
    sec("Advanced",
      h("div", { class: "fgrid" },
        fld("ADB path", inp(c, "adb_path")), fld("Evony package name", inp(c, "evony_package")),
        fld("Presets when no join presets are set (1-8)", inp(c, "presets", { get: (v) => (v || []).join(","), set: (t) => parsePresets(t) || [1] })),
        fld("Preset order", sel(c, "preset_mode", [["fixed", "Start from first"], ["rotate", "Rotate presets"], ["random", "Random"]])),
        fld("Buttons to tap to reach the rally list", inp(c, "rally_nav_steps", { get: (v) => (v || []).join(", "), set: (t) => parseNames(t) })),
        fld("Screenshot method", sel(c, "screenshot_mode", [["auto", "Automatic"], ["png", "PNG (slower, safer)"]]))),
      h("div", { class: "tgls", style: { marginTop: "12px" } }, tog(c, "log_timers", "Write the timers it reads to the log")),
      h("div", { class: "fgrid", style: { marginTop: "12px" } },
        fld("Tap to leave the rally list for the city (x,y)", xy("city_to_tap", [35, 35])),
        fld("Tap to go back from the city to the rally list (x,y)", xy("rally_nav_tap", [])),
        fld("Empty ground to tap so names show (x,y)", xy("city_label_tap", []))),
      h("div", { style: { marginTop: "12px" } }, btn("Right-side icon positions\u2026", iconsDialog, "sm")),
      note("Spare time = rally countdown minus your march time. 'Buttons to tap' are template names (default rally_icon); add more, e.g. alliance_button, war_button, if the rally list needs several taps. Any template named popup_something is tapped whenever it appears. Click delay scales every pause after a click; below ~500 ms the bot may click before a screen has loaded. A lighter load level scans less often.")));
  const lr = panel("Running many instances", h("div", { id: "ramInfo", class: "hint", style: { marginBottom: "10px" } }, "PC memory: ..."),
    tog(c, "low_memory", "Low-memory mode for a weak PC"),
    h("div", { style: { marginBottom: "8px" } }, tog(c, "keep_running_on_close", "Keep the bots running when I close this window")),
    winStartRow(),
    h("div", { style: { marginBottom: "12px" } }, tog(c, "auto_start", "Start all instances when the app opens")),
    h("div", { class: "fgrid" }, fld("Wait between instance starts (s)", inp(c, "start_gap_seconds", { type: "int", cls: "short" })),
      fld("Most instances at once (0 = no limit)", inp(c, "max_running_emulators", { type: "int", cls: "short" })),
      fld("Free RAM needed to start one (MB, 0 = off)", inp(c, "min_free_ram_mb", { type: "int", cls: "short" }))),
    h("div", { style: { marginTop: "12px" } }, tog(c, "close_emulator_on_stop", "Close the emulator when its instance stops (frees RAM)")),
    note("The bot itself needs roughly 100 MB; the instances are what fill the RAM (about 1.5-4 GB each). Extra instances beyond the limit wait in a queue and start when another one stops. Set the instances to 540x960, 2 GB or less of memory and 1-2 CPU cores in their own settings. Closing this window frees even more memory: the bots keep running."));
  // Beta: every user can record a task (taps, swipes + pictures) and send it, so new duties can be built from it
  const rec = panel("Record a task (beta)", h("div", { class: "hint", style: { marginBottom: "10px" } },
      "Play a task by hand in the game while this records your taps and swipes with a small picture of each step. Give it a clear title (for example \u201cGold Levy\u201d or \u201cAlliance treasure\u201d), then send the zip to the developer."),
    h("div", { class: "row" }, btn("Record a task\u2026", flowRecordFlow, "sm pri"),
      btn("Save recordings as zip", async () => { const j = await act(api("flow_zip")); if (j) toast(`Saved ${j.flows} recording(s) in Downloads: ${j.path}`, "ok"); }, "sm"),
      btn("Open recordings folder", () => act(api("flow_open")), "sm")));
  setTimeout(updateSummary, 0);
  const sc = scopeBar("settings", [...IF_KEYS], "settings");
  return h("div", {}, panel(null, sc.bar), pillSections("ctlset", [
    ["join", "Joining rallies", joinSec], ["speed", "Speed & starting up", speedSec], ["stamina", "Stamina", () => staminaPanel(c, c)],
    ["many", "Many instances", () => lr], ["adv", "Advanced", advSec], ["rec", "Record a task", () => [rec, panel("Tour", note("A short walkthrough of where to click and how to set up your presets and start the bot."), h("div", { class: "row" }, btn("Take the tour", () => startTour())))]]]));
};


// ---- universal vs own for a group of settings keys: returns {c, bar, own, locked}
// fixed undefined = Control settings (the universal defaults + Apply to...); fixed = an instance (own / default line, C4)
function scopeBar(page, keys, what, fixed, redraw) {
  const again = redraw || rerender;
  S.cfg.per_device = S.cfg.per_device || {};
  if (fixed === undefined) return { c: S.cfg, own: false, locked: false,
    bar: defaultsHead(what, async (ids) => { for (const s of ids) { const o = S.cfg.per_device[s]; if (o) keys.forEach((k) => delete o[k]); } again(); }) };
  const ov = (S.cfg.per_device[fixed] = S.cfg.per_device[fixed] || {});
  const own = keys.some((k) => ov[k] !== undefined);
  const toOwn = () => { keys.forEach((k) => { if (S.cfg[k] !== undefined) ov[k] = clone(S.cfg[k]); }); again(); };
  const toGlobal = async () => { if (await confirmBox("Reset to defaults", "This instance drops its own " + what + " and uses the universal one again.", "Reset")) { keys.forEach((k) => delete ov[k]); again(); } };
  return { c: own ? ov : S.cfg, own, locked: !own, bar: ownLine(own, toOwn, toGlobal) };
}
// sections of one screen as pills on top (C5) - one section shown at a time, the choice is remembered
function pillSections(key, list) {
  S.sec = S.sec || {};
  if (!list.some((x) => x[0] === S.sec[key])) S.sec[key] = list[0][0];
  const pills = h("div", { class: "subnav" }), area = h("div", {});
  const show = (k) => {
    S.sec[key] = k;
    [...pills.children].forEach((x) => x.classList.toggle("on", x.dataset.k === k));
    area.innerHTML = "";
    const el = list.find((x) => x[0] === k)[2]();
    for (const e of (Array.isArray(el) ? el : [el])) if (e) area.appendChild(e);
  };
  list.forEach(([k, l]) => pills.appendChild(h("button", { class: "sub", type: "button", "data-k": k, onclick: () => show(k) }, l)));
  show(S.sec[key]);
  return h("div", {}, pills, area);
}
function lockedWrap(el, locked) { if (locked) { el.style.opacity = "0.55"; el.style.pointerEvents = "none"; } return el; }

// ====================================================================== monsters
PAGES.monsters = function (fixed) {
  const sc = scopeBar("monsters", ["monster_skip", "monster_presets", "monster_unknown"], "monster list", fixed);
  if (fixed === undefined) return monstersPanel(S.cfg, sc.bar);
  if (sc.own) return monstersPanel(sc.c, sc.bar);
  const cat = (S.catalog || []).flatMap((x) => x[1]), skip = S.cfg.monster_skip || {};
  const joins = cat.filter(([n, lv]) => (skip[n] || []).length < lv.length).length;
  return panel("Monsters", sc.bar, h("p", { class: "sumline" }, h("b", {}, "Joins " + joins + " of " + cat.length + " monsters"), " with the universal list (Control settings \u203a Monsters)."),
    note("Make own to pick different monsters or levels for this instance."));
};
function monstersPanel(c, top) {
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
  // C6: chips filter the list (segmented is for choosing a value)
  const tabs = h("div", { class: "rtnpills" });
  ["All", ...cat.map((x) => x[0])].forEach((t) => {
    const n = t === "All" ? cat.reduce((a, x) => a + x[1].length, 0) : (cat.find((x) => x[0] === t) || [0, []])[1].length;
    const b = h("button", { type: "button", class: "rtnpill" + (S.monTab === t ? " on" : ""), "aria-pressed": String(S.monTab === t) }, t, h("span", { class: "pc" }, String(n)));
    b.addEventListener("click", () => { S.monTab = t; [...tabs.children].forEach((x) => { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-pressed", String(on)); }); draw(); });
    tabs.appendChild(b);
  });
  const search = h("input", { type: "search", placeholder: "Search monster", value: S.monQ, "aria-label": "Search monster" });
  search.addEventListener("input", () => { S.monQ = search.value; draw(); });
  draw();
  return panel("Monsters", top || null,
    note("Ticked = the bot joins it. Untick a monster to skip it, or open its levels and untick only the levels you do not want. The level is read from the rally card; if it cannot be read the bot joins."),
    h("div", { class: "row monbar" }, tabs, search),
    h("div", { class: "row", style: { marginBottom: "14px" } }, h("span", { class: "lab" }, "Monster with no picture yet"), sel(c, "monster_unknown", ["join", "skip"]),
      S.dev ? btn("Open unknown-monster folder", () => act(api("open_unknown")), "sm") : null),
    body,
    h("div", { class: "row", style: { marginTop: "14px" } }, S.dev ? btn("Add monster from screenshot\u2026", addMonster) : null, btn("Refresh list", async () => { await reloadMeta(); rerender(); })));
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
PAGES.tasks = function (fixed, inPanel, redraw) {
  const sc = scopeBar("tasks", ["city_tasks", "rally_enabled", "city_enabled", "tasks_first"], "task list", fixed, redraw);
  const c = sc.c;
  // read the list without writing it; an instance that is own only through a switch gets its own copy on the first edit
  const L = () => c.city_tasks || S.cfg.city_tasks || [];
  const M = () => { if (c.city_tasks === undefined) c.city_tasks = clone(S.cfg.city_tasks || []); return c.city_tasks; };
  // one row per task (Rule 0): grip to drag the order, switch, name + what it opens, edit and remove on the row
  const holder = h("div", { class: "tlist" });
  let dragFrom = null;
  const editAt = async (i) => { if (sc.locked) return; const r = await taskDialog(L()[i]); if (r) { M()[i] = r; draw(); } };
  const draw = () => {
    holder.innerHTML = "";
    if (!L().length) { holder.appendChild(h("div", { class: "empty" }, h("b", {}, "No tap lists yet"), h("span", {}, " Add one with Add task: the steps the bot taps, and how often."))); return; }
    L().forEach((x, i) => {
      const on = x.enabled !== false;
      const sw = h("input", { type: "checkbox", checked: on, "aria-label": "Task " + x.name + " on", onchange: (e) => { M()[i].enabled = e.target.checked; draw(); } });
      holder.appendChild(h("div", { class: "trow" + (on ? "" : " off"), draggable: sc.locked ? "false" : "true", title: "Drag to change the order - tasks higher up run first",
        ondragstart: (e) => { dragFrom = i; e.dataTransfer.effectAllowed = "move"; },
        ondragover: (e) => { e.preventDefault(); e.currentTarget.classList.add("over"); },
        ondragleave: (e) => e.currentTarget.classList.remove("over"),
        ondrop: (e) => { e.preventDefault(); e.currentTarget.classList.remove("over"); if (dragFrom == null || dragFrom === i || sc.locked) return; const [m] = M().splice(dragFrom, 1); M().splice(i, 0, m); dragFrom = null; draw(); },
        ondblclick: () => editAt(i) },
        h("span", { class: "grip", "aria-hidden": "true" }, "\u22ee\u22ee"),
        h("label", { class: "tgl" }, sw, h("span", { class: "sw" })),
        h("div", { class: "tname" }, h("b", {}, x.name), h("small", {}, (x.target ? "Opens " + String(x.target).replace(/^building:/, "").replace(/^icon:(.*)$/, "the $1 button").replace(/^(go|via|learned):/, "") + " \u00b7 " : "") + (x.at ? "at " + x.at : "every " + (x.every_min ?? 60) + " min"))),
        h("button", { class: "btn sm", type: "button", onclick: () => editAt(i) }, "Edit\u2026"),
        h("button", { class: "ix", type: "button", "aria-label": "Remove " + x.name, title: "Remove", onclick: async () => { if (!(await confirmBox("Remove task", `Remove '${x.name}' from the list?`, "Remove"))) return; M().splice(i, 1); draw(); } }, "\u2715")));
    });
  };
  draw();
  const tasks = panel(inPanel ? null : "My tasks", inPanel && fixed === undefined ? null : sc.bar,
    lockedWrap(h("div", { class: "tgls" }, inPanel ? null : tog(c, "city_enabled", "Run my tasks (and duties)"), tog(c, "rally_enabled", "Join rallies (always first)"), tog(c, "tasks_first", "A task that is due goes before rallies, then the bot comes back")), sc.locked),
    note("Your own tap lists. Rallies come first: the bot runs the tasks that are due (top of the list first) when no rally is waiting, and goes back. Building tasks need the city map."),
    lockedWrap(holder, sc.locked),
    sc.locked ? null : h("div", { class: "cardfoot" }, btn("Add task\u2026", async () => { const r = await taskDialog(null); if (r) { M().push(r); draw(); } }, "pri")));
  return tasks;
};

// ====================================================================== market
const PAY = [["gems", "Gems only"], ["resources", "Resources only"], ["both", "Gems or resources"]];
const REFRESH = [["none", "Never"], ["count", "A set number of times"], ["all", "Until the gem floor"]];
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
  // Layout (24 Oct): summary line, Spending card, Your picks (pay per item + "Set all to"), Add items (folded).
  // The old global "Pay with" is gone; market_pay stays in the settings as the default for newly added items
  // and as the bot's fallback for items saved without their own pay.
  const c = S.cfg;
  c.market_items = c.market_items || [];
  let cat = "all", q = "", names = [];
  const PAY_SHORT = [["gems", "Gems"], ["resources", "Resources"], ["both", "Either"]];
  const fmt = (v) => Number(v || 0).toLocaleString("en-US");
  const chosen = (n) => c.market_items.find((i) => i.name === n);
  const addItem = (n) => { if (!chosen(n)) c.market_items.push({ name: n, pay: c.market_pay || "gems" }); };
  const pick = (n) => { if (chosen(n)) c.market_items = c.market_items.filter((i) => i.name !== n); else addItem(n); draw(); };
  // ---- summary line
  const sum = h("div", { class: "bmsum" });
  const drawSum = () => {
    const k = c.market_items.length;
    if (!k) { sum.textContent = "Nothing picked yet - the bot opens the Black Market but buys nothing."; return; }
    const m = c.market_refresh_mode || "none", rn = Number(c.market_refresh_n || 0);
    const parts = ["Buys " + k + " item" + (k > 1 ? "s" : "") + " when the shop has them",
      m === "none" ? "no refresh" : m === "count" ? "refreshes up to " + rn + " time" + (rn === 1 ? "" : "s") : "refreshes until the gem floor",
      c.market_paid_refresh && m !== "none" ? "paid refresh on" : null,
      Number(c.market_min_gems) > 0 ? "keeps " + fmt(c.market_min_gems) + " gems" : "no gem floor",
      Number(c.market_max_gems) > 0 ? "max " + fmt(c.market_max_gems) + " gems a visit" : null];
    sum.textContent = parts.filter(Boolean).join(" · ");
  };
  // ---- 1. Spending
  const nInp = inp(c, "market_refresh_n", { type: "int", cls: "short" });
  const paidTog = tog(c, "market_paid_refresh", "Paid refresh with gems after the free ones", { after: () => draw() });
  const lockSpend = () => {
    const m = c.market_refresh_mode || "none";
    const nIn = nInp.tagName === "INPUT" ? nInp : nInp.querySelector("input");
    if (nIn) { nIn.disabled = m !== "count"; nIn.title = m === "count" ? "" : "Only used with 'A set number of times'"; }
    const pIn = paidTog.querySelector("input"); if (pIn) pIn.disabled = m === "none";
    paidTog.classList.toggle("dim", m === "none");
  };
  const spend = panel("Spending",
    grid2(fld("Refresh the shop", sel(c, "market_refresh_mode", REFRESH, { dropdown: true, after: () => draw() })),
      fld("How many times", nInp)),
    h("div", { class: "bmtog" }, paidTog),
    grid2(fld("Gem floor", inp(c, "market_min_gems", { type: "int", cls: "short" }), "Never go below this. 0 = no floor"),
      fld("Gold floor", inp(c, "market_min_gold", { type: "int", cls: "short" }), "Never go below this. 0 = no floor"),
      fld("Gem limit per visit", inp(c, "market_max_gems", { type: "int", cls: "short" }), "0 = no limit")));
  spend.addEventListener("input", () => drawSum());
  spend.addEventListener("change", () => drawSum());
  // ---- 2. Your picks
  const picksBody = h("div", {});
  const picksHead = h("div", { class: "bmhead" });
  const drawPicks = () => {
    const k = c.market_items.length;
    picksHead.innerHTML = "";
    picksHead.appendChild(h("h3", {}, "Your picks" + (k ? " (" + k + ")" : "")));
    if (k) {
      const same = c.market_items.every((i) => (i.pay || "gems") === (c.market_items[0].pay || "gems")) ? (c.market_items[0].pay || "gems") : "";
      const st = { v: same };
      picksHead.appendChild(h("div", { class: "bmsetall" }, h("span", { class: "hint" }, "Set all to"),
        segPick(st, "v", PAY_SHORT, { small: true, label: "Set all to", after: (v) => { c.market_items.forEach((i) => { i.pay = v; }); c.market_pay = v; draw(); } }),
        h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); c.market_items = []; draw(); } }, "Clear all")));
    }
    picksBody.innerHTML = "";
    if (!k) {
      picksBody.appendChild(h("div", { class: "empty" }, "No picks yet. Open Add items below and tap the pictures you want the bot to buy."));
      return;
    }
    picksBody.appendChild(h("div", { class: "bmpicks" }, ...c.market_items.map((it) => {
      const f = bmInfo(it.name), st = { v: it.pay || "gems" };
      return h("div", { class: "bmpick", title: it.name },
        h("img", { src: `/api/market_img?name=${encodeURIComponent(it.name)}&k=${window.KEY}`, alt: "", onerror: (e) => (e.target.style.visibility = "hidden") }),
        h("div", { class: "bmpn" }, f.label),
        segPick(st, "v", PAY_SHORT, { small: true, label: "Pay for " + f.label + " with", after: (v) => { it.pay = v; draw(); } }),
        h("button", { class: "bmx", type: "button", "aria-label": "Remove " + f.label, title: "Remove", onclick: () => pick(it.name) }, "×"));
    })));
  };
  const picks = panel(null, picksHead, picksBody);
  // ---- 3. Add items (folded; opens by itself while nothing is picked)
  const holder = h("div", {});
  const pills = h("div", { class: "rtnpills" });
  const search = h("input", { type: "search", placeholder: "Search, e.g. stamina, food, chest", oninput: (e) => { q = e.target.value.trim().toLowerCase(); draw(); } });
  const addSum = h("summary", {});
  const add = h("details", { class: "adv bmadd" }, addSum,
    h("div", { class: "bmaddb" },
      note("Tap a picture to add it to your picks; tap again to take it off. Picked ones are dimmed with a tick."),
      h("div", { class: "row", style: { margin: "4px 0 12px" } },
        btn("Stamina only", () => { c.market_items = c.market_items.filter((i) => bmInfo(i.name).cat === "stamina").map((i) => ({ ...i, pay: "gems" }));
          for (const n of names) if (bmInfo(n).cat === "stamina" && !chosen(n)) c.market_items.push({ name: n, pay: "gems" }); cat = "stamina"; draw(); }, "sm"),
        btn("Add item from screenshot…", async () => {
          const shot = await pickScreenshot("Screenshot of the Black Market");
          if (!shot) return;
          const rect = await pickOnShot(shot, "Drag a box around just the item's PICTURE (the icon in the middle, without the number in the corner), then release.", "region");
          if (!rect) return;
          const name = await promptBox("Black Market item", "Name of this item (e.g. Stamina 50):", "");
          if (!name) return;
          const j = await act(api("market_item_add", { token: shot.token, rect, name }));
          if (!j) return;
          if (!names.includes(j.name)) names.push(j.name);
          addItem(j.name);
          draw();
        }, "sm")),
      h("div", { class: "bmbar" }, pills, search),
      holder));
  let autoOpened = false;
  const draw = () => {
    const all = [...new Set([...names, ...c.market_items.map((i) => i.name)])];
    pills.innerHTML = "";
    for (const [k, l] of BM_CATS) {
      const n = k === "all" ? all.length : all.filter((x) => bmInfo(x).cat === k).length;
      pills.appendChild(h("button", { type: "button", class: "rtnpill" + (cat === k ? " on" : ""), onclick: () => { cat = k; draw(); } }, l + " " + n));
    }
    const list = all.filter((n) => { const f = bmInfo(n); return (cat === "all" || f.cat === cat) && (!q || (f.label + " " + n + " " + f.cat).toLowerCase().includes(q)); })
      .sort((a, b) => bmInfo(a).label.localeCompare(bmInfo(b).label));
    holder.innerHTML = "";
    holder.appendChild(list.length ? h("div", { class: "bmgrid" }, ...list.map((n) => {
      const it = chosen(n), f = bmInfo(n);
      return h("button", { type: "button", class: "bmcard" + (it ? " on" : ""), title: n, "aria-pressed": !!it, onclick: () => pick(n) },
        h("img", { src: `/api/market_img?name=${encodeURIComponent(n)}&k=${window.KEY}`, alt: "", onerror: (e) => (e.target.style.visibility = "hidden") }),
        h("div", { class: "bmname" }, f.label), h("div", { class: "bmsub" }, it ? "Picked" : "Tap to add"));
    })) : h("div", { class: "empty" }, q ? "Nothing matches “" + q + "”." : "No pictures in this group yet."));
    const left = all.filter((n) => !chosen(n)).length;
    addSum.textContent = "Add items" + (all.length ? " · " + left + " more to choose from" : "");
    if (!c.market_items.length && !autoOpened) { add.open = true; autoOpened = true; }
    drawPicks(); drawSum(); lockSpend();
  };
  api("market_pictures", {}, { quiet: true }).then((j) => { names = (j && j.names) || []; draw(); }).catch(() => draw());
  draw();
  return h("div", { class: "bmpage" }, sum, spend, picks, panel(null, add));
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
  };
  const pills = h("div", { class: "subnav", style: { margin: "0" } }, views.map(([k, t]) => h("button", { class: "sub" + (k === cur ? " on" : ""), type: "button", "data-k": k,
    onclick: (e) => { [...pills.children].forEach((x) => x.classList.toggle("on", x === e.currentTarget)); show(k); } }, t)));   // sections = pills (C5)
  bar.appendChild(pills);
  views.forEach(([, , el]) => area.appendChild(el));
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
    note("Examples: 08:00-12:00, 18:00-23:30 \u00b7 Mon-Fri 08:00-22:00; Sat,Sun 10:00-14:00 \u00b7 22:00-02:00 (past midnight). An instance can have its own schedule in its page. The bot starts an instance when its window opens and stops it when the window ends; starting or stopping it yourself in between is respected."));
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
      h("div", { class: "row", style: { gap: "10px" } }, btn("What's new", () => showWhatsNew(true)), btn("Check for updates now", async () => { if (await act(api("check_updates", { cfg: c }))) toast("Checking for updates\u2026"); }, "pri"))),
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
    DARK_ON ? panel("Light or dark", segs,
      note("Follow Windows switches by itself when Windows does. The sun / moon button at the bottom of the sidebar flips it from any screen.")) : null,
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
      (L.instances ? `Up to ${L.instances} instance${L.instances > 1 ? "s" : ""} running at the same time` : "No limit on running instances") + (L.expires ? ` \u00b7 valid until ${L.expires}` + (left != null ? ` (${left} day${left === 1 ? "" : "s"} left)` : "") : " \u00b7 no expiry") + ` \u00b7 instances in your list: ${S.devices.filter((d) => d.id).length}`) : null,
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
    onclick: () => { S.sec = S.sec || {}; S.sec.settings = "phone"; if (S.gotoInstTab) S.gotoInstTab("settings"); } });
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
      h("div", { class: "tdcol" }, h("div", { class: "tdlab" }, "Duties today",
        h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); if (S.gotoInstTab) S.gotoInstTab("routines"); } }, "Edit in Routines \u2192")), list, sum),
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
    m.items.slice(0, 9).forEach((x) => list.appendChild(h("div", { class: "tdrow " + x.k },
      h("span", { class: "tdt", title: x.tip || "" }, x.title), dutyBadge(x.k, x.text, x.tip))));
    if (m.items.length > 9) list.appendChild(h("small", { class: "hint" }, "+" + (m.items.length - 9) + " more in Routines"));
    if (!m.items.length) list.appendChild(h("div", { class: "empty sm" }, "No duty is switched on. ", h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); if (S.gotoInstTab) S.gotoInstTab("routines"); } }, "Switch some on in Routines")));
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
  const head = h("div", { class: "tdreset" }), ce = h("div", { class: "tdce" }), body = h("tbody", {}), hrow = h("tr", {});
  let cols = "";
  const card = h("div", { class: "panel today" },
    h("div", { class: "cardhead" }, h("h3", {}, "Today \u00b7 all instances"), head), ce,
    h("table", { class: "tdall" },
      h("thead", {}, hrow), body));
  let j = null, last = "";
  const skew = () => (j ? j.now - Date.now() / 1000 : 0);
  const tick = () => {
    if (!j) { head.textContent = "Daily reset -"; return; }
    head.textContent = `Daily reset in ${fmtHM(j.reset_at - (Date.now() / 1000 + skew()))} \u00b7 ${hhmm(j.reset_at)} your time`;
  };
  const open = (serial) => { S.sel = serial; S.instTab = "ov"; renderSide(); renderTabs(); renderPage(); };
  // C10: duties are switched in each instance's Routines - the alert links there instead of switching from here
  const toRoutines = (serial) => { S.dutyView = { show: "all", group: "Events" }; S.sel = serial; S.instTab = "routines"; renderSide(); renderTabs(); renderPage(); };
  const paint = () => {
    if (!j) return;
    const st = {}; (S.devStatus || []).forEach((d) => (st[d.id] = d.state));
    const key = JSON.stringify([j.rows, j.crazy_eggs, st, j.rows.map((r) => ajIsOn(r.serial))]);
    if (key === last) return;                                               // nothing changed: no flicker
    last = key;
    const c = j.crazy_eggs || { total: 0, off: [] };
    ce.innerHTML = "";
    ce.className = "tdce" + (c.off.length ? " off" : "");
    ce.append(h("b", {}, "Crazy Eggs"), " ");
    if (!c.total) ce.append(h("span", {}, "no instances yet"));
    else if (!c.off.length) ce.append(h("span", {}, `on for all ${c.total} instance${c.total > 1 ? "s" : ""}`));
    else {
      ce.append(h("span", {}, `\u26a0 off on ${c.off.length} of ${c.total}: `));
      c.off.forEach((n, i) => { const r = j.rows.find((x) => x.name === n); ce.append(i ? ", " : "", r ? h("a", { href: "#", class: "lnk", title: "Open " + n + " \u203a Routines", onclick: (e) => { e.preventDefault(); toRoutines(r.serial); } }, n) : n); });
    }
    // Shield / Buff appear only once the bot reads them from the game (a row carries shield / buff)
    const sb = j.rows.some((r) => r.shield != null || r.buff != null);
    const want = ["Instance"].concat(sb ? ["Shield", "Buff"] : [], ["Auto-Join", "Duties", "Joins", "Skipped (late)"]);
    if (cols !== want.join()) { cols = want.join(); hrow.innerHTML = ""; want.forEach((c) => hrow.appendChild(h("th", {}, c))); }
    body.innerHTML = "";
    if (!j.rows.length) body.appendChild(h("tr", {}, h("td", { colspan: String(want.length), class: "mute" }, "No instances in your list yet.")));
    const WORD = { error: "problem", stop: "stopped", idle: "stopped" };
    j.rows.forEach((r) => {
      const off = !["run", "pause"].includes(st[r.serial]);
      const all = r.duties_total && r.duties_done >= r.duties_total;
      const tx = (v) => v == null || v === "" ? "-" : String(v);
      body.appendChild(h("tr", { class: off ? "off" : "", title: "Open " + r.name, onclick: () => open(r.serial) },
        h("td", {}, h("b", {}, r.name), off ? h("small", { class: st[r.serial] === "error" ? "bad" : "" }, " " + (WORD[st[r.serial]] || "stopped")) : null),
        sb ? h("td", {}, tx(r.shield)) : null, sb ? h("td", {}, tx(r.buff)) : null,
        h("td", {}, ajSwitch(r.serial, { label: "", small: true, after: () => { last = ""; paint(); } })),
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

// ====================================================================== duties: ONE list wherever duties are switched (rules C1-C4, C7, C8)
// Instance > Routines = this instance's duties (the main editor). Control settings > Duties = the universal defaults new
// instances start with, plus "Apply to...". Same names, groups, rows, badges and side panel in both places.
const DUTY_GROUPS = ["City", "Market & Tavern", "Alliance", "Army", "Events", "Reward claims", "My tasks"];
// id: [group, name, Activity points]. Names follow the game's Activity screen; the saved settings still use the ids.
const DUTY_UI = {
  train_troops: ["City", "Troop training", 3], troop_healing: ["City", "Troop healing", 2], build_traps: ["City", "Trap building", 2],
  labor: ["City", "Labor", 3], collect_resources: ["City", "Resource collecting", 1], gold_levy: ["City", "Gold levy", 1],
  resource_tax: ["City", "Resource tax", 1], offering_tributes: ["City", "Offering", 3],
  black_market: ["Market & Tavern", "Black Market", 4], wheel: ["Market & Tavern", "Wheel of Fortune", 3], trade_carriage: ["Market & Tavern", "Trade Carriage", 3],
  champions: ["Market & Tavern", "Greet champions", 2], ranger_knight: ["Market & Tavern", "Ranger Knight Order", 3], tavern_catalog: ["Market & Tavern", "General library", 0],
  alliance_help: ["Alliance", "Alliance help", 1], alliance_science: ["Alliance", "Alliance donation", 1],
  monster_rally: ["Army", "Monster rallies", 0],
  crazy_eggs: ["Events", "Crazy Eggs", 0], wisdom_dome_tokens: ["Events", "Wisdom Dome tokens", 0], royal_thief: ["Events", "Royal Thief", 0],
  login_gifts: ["Reward claims", "Login gifts", 0], mail: ["Reward claims", "Mail rewards", 0], alliance_gift: ["Reward claims", "Alliance gift", 0],
  alliance_treasure: ["Reward claims", "Alliance treasure", 0], general_chest: ["Reward claims", "Great General chest", 0], vip_claim: ["Reward claims", "VIP daily chest", 0],
  recruit_citizens: ["Reward claims", "Recruit citizens", 0], civilization_card: ["Reward claims", "Civilization card pack", 0], empire_depot: ["Reward claims", "Empire Depot", 0],
  lucky_raffle: ["Reward claims", "Lucky Raffle", 0], refining_card: ["Reward claims", "Refining weekly card", 0], gate_visitors: ["Reward claims", "Gate visitors", 0],
  activity_chests: ["Reward claims", "Activity chests", 0],
};
const DUTY_COMING = ["Construction", "Research", "Material composing", "General enhancing", "Patrol", "Relic exploration", "Resource gathering"];
const DUTY_TEST = new Set(["reach_keep_tavern", "market_capture", "reach_tavern", "reach_market", "view_reset_test", "reach_embassy", "test_learned_keep", "test_activity_go"]);   // developer builds only (C12)
const ACT_MAX = 145;                                                     // the game's last Activity chest
const DUTY_ORDER = (id) => { const i = Object.keys(DUTY_UI).indexOf(id); return i < 0 ? 999 : i; };   // the game's Activity order
const dutyName = (id, t) => (DUTY_UI[id] ? DUTY_UI[id][1] : t);
const dutyGroup = (id, g) => (DUTY_UI[id] ? DUTY_UI[id][0] : g === "Reward claims" ? g : "City");
const dutyShown = (id) => S.dev || !DUTY_TEST.has(id);
const RUN_SETUP = new Set(["map_city"]);              // one-time jobs: Run now lists them under Setup, on top
// C7: one status badge, one vocabulary (Done 07:23 · Done for today · Due · Next 08:01 · Retrying 08:30 · Gave up · Running now)
const BADGE_CLS = { done: "ok", due: "due", waiting: "next", retry: "next", gaveup: "bad", run: "run" };
function dutyBadge(status, text, tip) {
  if (!status || status === "off") return null;
  const t = status === "gaveup" ? "Gave up" : status === "run" ? "Running now" : (text || status);
  return h("span", { class: "dbadge " + (BADGE_CLS[status] || ""), title: tip || (status === "gaveup" ? text : "") }, t);
}
// C4: the one own / default line at the top of an instance card that has a universal default
function ownLine(own, onMake, onReset) {
  const a = (t, fn) => h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); fn(); } }, t);
  return own ? h("div", { class: "ownline" }, h("span", { class: "chip own" }, "Own settings"), a("Reset to defaults", onReset))
    : h("div", { class: "ownline" }, h("span", { class: "mute" }, "Using universal defaults"), h("span", { class: "mute" }, "·"), a("Make own", onMake));
}
// C3: Control settings pages are the universal defaults; "Apply to..." makes picked instances drop their own copy (explicit, never silent)
function applyToDialog(what, run) {
  const devs = (S.devices || []).filter((d) => d.id);
  if (!devs.length) return toast("There are no instances yet.");
  const picked = new Set();
  const list = h("div", { class: "applylist" }, devs.map((d) => h("label", { class: "tgl" },
    h("input", { type: "checkbox", onchange: (e) => { if (e.target.checked) picked.add(d.id); else picked.delete(d.id); okBtn.disabled = !picked.size; } }), h("span", { class: "sw" }), d.name || short(d.id))));
  // C14: more than 8 instances -> a search box above the list (names or addresses)
  const find = devs.length > 8 ? h("input", { type: "search", class: "dsearch", placeholder: "Search instances", "aria-label": "Search instances", autocomplete: "off",
    style: { width: "100%", marginBottom: "8px" },
    oninput: (e) => { const t = e.target.value.trim().toLowerCase(); [...list.children].forEach((el, i) => { el.hidden = !!t && !((devs[i].name || "") + " " + devs[i].id).toLowerCase().includes(t); }); } }) : null;
  const m = modal({ title: "Apply the universal " + what, body: h("div", {},
    h("p", { class: "note" }, "The instances you pick drop their own " + what + " and use these defaults from now on. Other instances are not changed."), find, list),
    buttons: [{ label: "Cancel" }, { label: "Apply", cls: "pri", onclick: async () => { if (!picked.size) return false; await run([...picked]); toast("Applied to " + picked.size + " instance" + (picked.size > 1 ? "s" : "") + ".", "ok"); } }] });
  const okBtn = m.el.querySelector(".mf .btn.pri"); okBtn.disabled = true;
}
function defaultsHead(what, run) {
  return h("div", { class: "defhead" },
    h("div", {}, h("b", {}, "Universal defaults"), h("small", { class: "hint" }, "New instances start with these " + what + ". An instance with its own " + what + " keeps them.")),
    btn("Apply to…", () => applyToDialog(what, run), "sm"));
}
const OWN_DUTY_KEYS = ["duties", "crazy_eggs", "champions", "wheel"];

PAGES.duties = function (fixed) {
  const serial = fixed || "";                     // "" = the universal defaults (Control settings); an instance id = its Routines
  const root = h("div", { class: "dutypage" });
  let rows = [], own = {}, count = null, lastKey = "";
  const ovGet = () => (serial ? ((S.cfg.per_device || {})[serial] || null) : null);     // read only: opening the page writes nothing
  const ovMake = () => ((S.cfg.per_device = S.cfg.per_device || {})[serial] = S.cfg.per_device[serial] || {});
  const take = (j) => { if (!j) return; rows = j.duties; own = j.own || {}; count = j.count || null; draw(); };
  const nowText = () => (((S.now && S.now[serial]) || {}).text || "").toLowerCase();
  // My tasks (your own tap lists) is one more row in the same list; its switch is the old 'Do the tasks' (city_enabled)
  const tasksOn = () => { const ov = ovGet(); return (ov && ov.city_enabled !== undefined ? ov.city_enabled : S.cfg.city_enabled) !== false; };
  const nTasks = () => { const ov = ovGet(); return ((ov && ov.city_tasks) || S.cfg.city_tasks || []).length; };
  const list = () => rows.filter((r) => dutyShown(r.id) && !RUN_SETUP.has(r.id)).map((r) => {     // one-time jobs live in Run now, not as switches
    const run = serial && r.on && nowText() && nowText().includes(String(r.title).toLowerCase());
    return Object.assign({}, r, { g: dutyGroup(r.id, r.group), t: dutyName(r.id, r.title), pts: DUTY_UI[r.id] ? DUTY_UI[r.id][2] : 0, status: run ? "run" : r.status });
  }).sort((a, b) => DUTY_ORDER(a.id) - DUTY_ORDER(b.id)).concat([{ id: "@tasks", g: "My tasks", t: "My tasks", pts: 0, on: tasksOn(), status: "off",
    schedule: nTasks() ? nTasks() + " tap list" + (nTasks() > 1 ? "s" : "") + ", run when no rally is waiting" : "Your own tap lists - none yet" }]);
  const setOn = async (r, v) => {
    if (r.id === "@tasks") { if (serial) ovMake().city_enabled = v; else S.cfg.city_enabled = v; draw(); return; }   // page autosave
    take(await act(api("duty_set", { serial, id: r.id, on: v })));
  };
  // a duty's own settings (Resource Tax resource, training amount, speed-ups ...), saved with the duty
  const dutyOpts = (r) => h("div", { class: "fgrid" },
    r.opts.map((o) => {
      if (o.type === "rules") return h("div", { class: "wide" }, h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); closeSide(); if (S.gotoInstTab && serial) S.gotoInstTab("mrally"); } },
        serial ? "Monsters and presets: Monster rallies tab →" : "Monsters and presets are set on each instance's Monster rallies tab."));
      const save = async (v) => take(await act(api("duty_set", { serial, id: r.id, opt: { [o.k]: v } })));
      if (o.type === "bool") return h("div", { class: "wide" }, h("label", { class: "tgl" }, h("input", { type: "checkbox", checked: !!o.val, onchange: (e) => save(e.target.checked) }), h("span", { class: "sw" }), h("span", {}, o.label)));
      if (o.type === "select") return h("label", {}, h("span", { class: "lab" }, o.label), ((st) => sel(st, "v", o.choices, { label: o.label, after: () => save(st.v) }))({ v: o.val }));
      return h("label", {}, h("span", { class: "lab" }, o.label), h("input", { type: "number", min: o.min, max: o.max, value: o.val, onchange: (e) => save(e.target.value) }));
    }));
  const SET_IDS = ["crazy_eggs", "champions", "wheel", "black_market", "@tasks"].concat(serial ? ["collect_resources"] : []);
  const hasSet = (r) => !!((r.opts && r.opts.length) || SET_IDS.includes(r.id));
  const setBody = (r) => [
    (r.opts && r.opts.length) ? dutyOpts(r) : null,
    r.id === "crazy_eggs" ? ce : null, r.id === "champions" ? ch : null, r.id === "wheel" ? wh : null,
    r.id === "black_market" ? PAGES.market() : null,
    r.id === "@tasks" ? PAGES.tasks(serial || undefined, true, () => drawSide()) : null,
    r.id === "collect_resources" && serial ? h("div", { class: "subset" }, h("div", { class: "subtitle" }, "City map"),
      note("Resource collecting taps the farms the city map knows. Scan city reads the building names; Map by hand is the manual way."),
      h("div", { class: "row" }, btn("Scan city", () => scanCityFlow(serial), "sm"), btn("Map by hand", () => manualMapFlow(serial), "sm"))) : null,
  ].filter(Boolean);
  // ---- side panel (C2): Settings on the row opens it; Esc, Done or a click outside closes; changes save as they are made
  let sideId = null;
  const side = h("div", { class: "sideov", onclick: (e) => { if (e.target === side) closeSide(); } });
  if (window.watchNotes) window.watchNotes(side);
  const sideKey = (e) => { if (e.key === "Escape" && !document.querySelector(".ov")) closeSide(); };
  const closeSide = () => {
    const was = sideId; sideId = null; side.remove(); document.removeEventListener("keydown", sideKey);
    const b = was && root.querySelector('.dset[data-id="' + was + '"]'); if (b) { b.classList.remove("on"); b.focus(); }
  };
  const drawSide = () => {
    const r = list().find((x) => x.id === sideId);
    if (!r || !root.isConnected) { closeSide(); return; }
    side.innerHTML = "";
    const runBtn = serial && r.id !== "@tasks" ? h("button", { class: "btn", type: "button", onclick: async () => { if (await act(api("run_now_add", { serial, id: "duty:" + r.id }))) toast(r.t + " runs next on this instance.", "ok"); } }, "Run now") : null;
    side.appendChild(h("aside", { class: "sidep", role: "dialog", "aria-modal": "true", "aria-label": r.t + " settings" },
      h("div", { class: "sph" },
        h("label", { class: "tgl" }, h("input", { type: "checkbox", checked: !!r.on, "aria-label": r.t, onchange: async (e) => { await setOn(r, e.target.checked); drawSide(); } }), h("span", { class: "sw" })),
        h("div", { class: "spt" }, h("h3", {}, r.t), h("small", {}, [serial ? "This instance" : "Universal default", r.g, r.schedule, r.pts ? "+" + r.pts + " activity points" : ""].filter(Boolean).join(" · "))),
        serial && r.on ? dutyBadge(r.status, r.status_text) : null,
        h("button", { class: "spx", type: "button", "aria-label": "Close", title: "Close (Esc)", onclick: closeSide }, "×")),
      h("div", { class: "spb" }, setBody(r)),
      h("div", { class: "spf" }, h("span", { class: "hint" }, "Changes save as you make them."), runBtn, h("button", { class: "btn pri", type: "button", onclick: closeSide }, "Done"))));
  };
  const openSide = (id) => {
    sideId = id;
    root.querySelectorAll(".dset").forEach((b) => b.classList.toggle("on", b.dataset.id === id));
    drawSide();
    if (sideId && !side.isConnected) { document.body.appendChild(side); document.addEventListener("keydown", sideKey); const x = side.querySelector(".spx"); if (x) x.focus(); }
  };
  // ---- header: instance = own / default line, points, done / on, Copy from; control = Universal defaults + Apply to...
  const resetOwn = async () => {
    if (!(await confirmBox("Reset to defaults", "This instance drops its own duties and their settings and uses the universal defaults again.", "Reset"))) return;
    for (const k of OWN_DUTY_KEYS) await act(api("scope_reset", { serial, key: k }));
    load(); loadCe(); loadCh(); loadWh();
  };
  const makeOwn = async () => { const r = rows.find((x) => dutyShown(x.id)); if (r) take(await act(api("duty_set", { serial, id: r.id }))); };
  const copyFrom = (e) => {
    const others = S.devices.filter((x) => x.id && x.id !== serial);
    if (!others.length) return toast("There is no other instance.");
    menu(e.currentTarget, others.map((o) => ({ label: o.name || short(o.id), run: async () => {
      if (!(await confirmBox("Copy duties", "Give this instance the duties of " + (o.name || o.id) + ", with their settings (Crazy Eggs, Champions, Wheel). Its own duties are replaced.", "Copy"))) return;
      take(await act(api("duties_copy", { serial, from: o.id }))); loadCe(); loadCh(); loadWh(); } })));
  };
  const head = () => {
    if (!serial) return defaultsHead("duties", async (ids) => { for (const s of ids) for (const k of OWN_DUTY_KEYS) await api("scope_reset", { serial: s, key: k }); });
    const L = list(), pts = L.filter((r) => r.today && r.pts).reduce((t, r) => t + r.pts, 0);
    return h("div", { class: "dhead" },
      ownLine(own.duties, makeOwn, resetOwn),
      h("span", { style: { flex: "1" } }),
      count ? h("span", { class: "dstat", title: "Duties switched on that finished at least once this game day" }, h("b", {}, count.done + " / " + count.on), " done today") : null,
      h("span", { class: "dstat", title: "Activity points from duties the bot finished today (rallies not counted)" }, h("b", {}, String(pts)), " / " + ACT_MAX + " points"),
      btn("Copy from another instance…", copyFrom, "sm"));
  };
  // ---- the list (C1): On / All, group chips with counts, two columns, fits one screen
  const item = (r) => h("div", { class: "dline" + (r.on ? "" : " off") },
    h("label", { class: "tgl" }, h("input", { type: "checkbox", checked: !!r.on, "aria-label": r.t, onchange: (e) => setOn(r, e.target.checked) }), h("span", { class: "sw" })),
    h("div", { class: "dtx", title: r.note || "" }, h("b", {}, r.t, r.pts ? h("span", { class: "dpts", title: "Activity points" }, "+" + r.pts) : null), h("small", {}, r.schedule || r.note || "")),
    S.dev && r.verified === false ? h("span", { class: "dbadge", title: "Developer: steps not yet watched on a real instance" }, "untested") : null,
    serial && r.on ? dutyBadge(r.status, r.status_text, r.week && r.week.last_error ? "Last problem: " + r.week.last_error : "") : null,
    serial && r.on && r.status === "gaveup" ? h("button", { class: "btn sm", type: "button", onclick: async () => { take(await act(api("duty_retry", { serial, id: r.id }))); toast("It runs again shortly.", "ok"); } }, "Retry") : null,
    hasSet(r) ? h("button", { class: "btn sm dset" + (sideId === r.id ? " on" : ""), type: "button", "data-id": r.id, "aria-label": r.t + " settings", onclick: () => openSide(r.id) }, "Settings") : null);
  let q = "";                                     // C14: search, across every group (typing clears the group chip)
  const qHit = (r) => { const t = q.trim().toLowerCase(); return !t || (r.t + " " + (r.title || "") + " " + r.g).toLowerCase().includes(t); };
  const draw = () => {
    const had = document.activeElement && document.activeElement.classList.contains("dsearch") && root.contains(document.activeElement);
    root.innerHTML = "";
    const L = list(), onCount = L.filter((r) => r.on).length;
    const view = (S.dutyView = S.dutyView || {});
    if (!view.show && rows.length) view.show = onCount ? "on" : "all";
    if (view.show === "on" && !onCount) view.show = "all";
    if (view.group && !DUTY_GROUPS.includes(view.group)) view.group = "";
    const v = { show: view.show || "all", group: view.group || "" };
    const segBtn = (lab, on, fn) => h("button", { class: "segb" + (on ? " on" : ""), type: "button", onclick: fn }, lab);
    const pass = (r) => (q.trim() ? qHit(r) : v.show === "all" || r.on);
    const search = h("input", { type: "search", class: "dsearch", placeholder: "Search duties", "aria-label": "Search duties", value: q, autocomplete: "off",
      oninput: (e) => { q = e.target.value; if (q.trim()) view.group = ""; draw(); },
      onkeydown: (e) => { if (e.key === "Escape" && q) { e.preventDefault(); e.stopPropagation(); q = ""; draw(); } } });
    const filters = h("div", { class: "dfilter" }, search,
      h("div", { class: "seg small" }, segBtn("On (" + onCount + ")", v.show === "on", () => { view.show = "on"; draw(); }), segBtn("All (" + L.length + ")", v.show === "all", () => { view.show = "all"; draw(); })),
      h("div", { class: "rtnpills" }, [["", "All groups"]].concat(DUTY_GROUPS.map((g) => [g, g])).map(([g, l]) =>
        h("button", { class: "rtnpill" + (v.group === g ? " on" : ""), type: "button", onclick: () => { view.group = g; draw(); } }, l, h("span", { class: "pc" }, String(L.filter((r) => (!g || r.g === g) && pass(r)).length))))));
    const body = [];
    for (const g of DUTY_GROUPS) {
      if (v.group && v.group !== g) continue;
      const rs = L.filter((r) => r.g === g && pass(r));
      if (!rs.length) continue;
      if (!v.group) body.push(h("div", { class: "dgrouph" }, g));
      body.push(h("div", { class: "dgrid" }, rs.map((r) => h("div", { class: "dcell" + (r.on ? " on" : "") }, item(r)))));
    }
    const foot = [];
    if (v.show === "all" && (!v.group || v.group === "Army") && serial) foot.push("Monster killing and stamina points come from the rallies the bot joins (Rally setup).");
    if (v.show === "all" && !v.group) foot.push(DUTY_COMING.length + " more activities coming: " + DUTY_COMING.join(", ") + ".");
    root.appendChild(panel(null, head(), filters,
      body.length ? body : h("div", { class: "empty" }, q.trim() ? h("span", {}, "No duties match \u201c" + q.trim() + "\u201d. ", h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); q = ""; draw(); } }, "Clear the search"))
        : v.show === "on" ? h("span", {}, "Nothing is switched on here yet. ", h("a", { href: "#", class: "lnk", onclick: (e) => { e.preventDefault(); view.show = "all"; draw(); } }, "Show all duties")) : "No duties in this group."),
      foot.length ? h("div", { class: "dfoot" }, foot.map((t) => h("small", { class: "hint" }, t))) : null));
    lastKey = JSON.stringify(rows.map((r) => [r.on, r.status, r.status_text]));
    if (had) { search.focus({ preventScroll: true }); search.setSelectionRange(q.length, q.length); }
  };
  // "/" jumps to the search box, as in Mac apps (not while typing somewhere else)
  const slash = (e) => {
    if (!root.isConnected) { document.removeEventListener("keydown", slash); return; }
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || "") || document.querySelector(".ov, .pick")) return;
    const box = root.querySelector(".dsearch"); if (box) { e.preventDefault(); box.focus(); }
  };
  document.addEventListener("keydown", slash);
  const load = async () => take(await act(api("duties", { serial })));
  // keep today's states fresh (instances only) without touching an open side panel
  if (serial) {
    const tick = setInterval(async () => {
      if (!root.isConnected) { clearInterval(tick); return; }
      const j = await api("duties", { serial }, { quiet: true }).catch(() => null);
      if (!j || !j.duties) return;
      const k = JSON.stringify(j.duties.map((r) => [r.on, r.status, r.status_text]));
      if (k !== lastKey) { rows = j.duties; own = j.own || {}; count = j.count || null; draw(); }
    }, 20000);
  }
  // Crazy Eggs settings: per-egg switch + daily limit, which egg gets the Lucky Hammer, hammers to keep
  const ce = h("div", {});
  const EGG_TIME = { "3": "30 min", "6": "1 h", "8": "2 h", "12": "4 h" };
  const drawCe = (j) => {
    ce.innerHTML = "";
    const c = j.conf;
    const set = async (o) => { const r = await act(api("crazy_eggs_set", { serial, ...o })); if (r) drawCe(r); };
    ce.appendChild(subset("Crazy Eggs",
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
  // Wheel of Fortune: which spin button, how many presses of it, coins to keep
  const wh = h("div", {});
  const whTotal = (n, b) => { n = parseInt(n, 10) || 0; b = b || 1; return n > 0 ? "= " + (n * b).toLocaleString() + " spins \u00b7 " + (n * ({ 1: 100, 10: 900, 100: 9000 }[b] || 100)).toLocaleString() + " coins" : "Empty or 0 = until the coins run out."; };
  const drawWh = (j) => {
    wh.innerHTML = "";
    const c = j.conf;
    const set = async (o) => { const r = await act(api("wheel_set", { serial, ...o })); if (r) drawWh(r); };
    wh.appendChild(subset("Wheel of Fortune",
      note("Tavern > Wheel of Fortune. Each spin costs coins. The bot presses only the Spin buttons and closes the reward panel after every spin. Presses = how many times the chosen button is pressed (1 press of \u00d7100 = 100 spins). If the coins run short it finishes with smaller buttons."),
      h("div", { class: "fgrid eggf" },
        h("div", { class: "wide" }, h("span", { class: "lab" }, "Spin button"),
          h("div", { class: "seg small" }, [1, 10, 100].map((b) => h("button", { class: "segb" + (c.button === b ? " on" : ""), type: "button", onclick: () => set({ button: b }) }, "Spin " + (b === 1 ? "once" : "\u00d7" + b))))),
        h("label", {}, h("span", { class: "lab" }, "Presses"),
          h("input", { type: "number", min: 0, value: c.spins || "", placeholder: "Until out of coins", onchange: (ev) => set({ spins: ev.target.value }),
            oninput: (ev) => { const t = ev.target.parentNode.querySelector(".hint"); if (t) t.textContent = whTotal(ev.target.value, c.button); } }),
          h("small", { class: "hint" }, whTotal(c.spins, c.button))),
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
    ch.appendChild(subset("Champions",
      note("Keep > Champions. Every account has the same champions but only a few are active; the bot skips the ones that are not (grey button at the bottom). The daily limit is " + j.max_taps + " taps and " + j.max_greets + " greetings for all champions together - put them all on one champion or share them out. Switch a champion on to make it the main one: it gets all the taps and greetings of the day in one click (the numbers can still be edited). Leave everything empty to use the champion the page opens on. Needs the city map (the Keep)."),
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
  draw(); load(); loadCe(); loadCh(); loadWh();
  return root;
};

// ====================================================================== Run a task now (C10): one component - instance Overview (this
// instance) and Control > Dashboard (every instance). The bot does the picked task next, once, before everything else.
// ---- City map card (instance Overview): is the city mapped, mapping progress, and the buildings the duties need -
//      found (pinned on the map) / seen (picture only) / not yet - with a percentage. Buildings only, no small markers (owner 24 Oct).
function cityMapCard(serial) {
  const root = h("div", { class: "panel citymap" });
  let last = null;
  const nowText = () => (((S.now && S.now[serial]) || {}).text || "");
  const paint = (j) => {
    root.innerHTML = "";
    const m = /Mapping (\d+) views/.exec(nowText());
    const mapping = m ? parseInt(m[1], 10) : null;
    const total = j.buildings.length, found = j.buildings.filter((b) => b.state === "found").length;
    const pct = total ? Math.round(found * 100 / total) : 0;
    const head = h("div", { class: "cardhead" },
      h("div", {}, h("h3", {}, "City map"), h("small", { class: "sub" },
        mapping !== null ? "Mapping now" : j.built ? "Mapped " + (j.at || "") + " · " + j.views + " views" : "Not mapped yet")),
      mapping !== null ? h("span", { class: "dbadge run" }, "Mapping · " + mapping + " views")
        : j.built ? h("span", { class: "dbadge ok" }, found + " of " + total + " buildings · " + pct + "%")
        : h("span", { class: "dbadge" }, "Run Map my city"));
    root.appendChild(head);
    if (mapping !== null) {                               // a city at the widest zoom is ~50 views: the bar is a guide, not a promise
      const est = Math.min(96, Math.round(mapping * 100 / Math.max(50, mapping + 4)));
      root.appendChild(h("div", { class: "cmbar" }, h("div", { class: "cmfill", style: { width: est + "%" } })));
      root.appendChild(h("p", { class: "hint" }, "The bot walks over the whole city once (about 20 minutes). The buildings below are pinned as the daily tasks open them."));
    }
    if (j.built && j.img) {
      const img = h("img", { class: "cmimg", src: "/api/city_map_img?serial=" + encodeURIComponent(serial) + "&k=" + encodeURIComponent(window.KEY) + "&t=" + (j.at || ""), alt: "City map" });
      const wrap = h("div", { class: "cmwrap" }, img);
      if (j.size) for (const b of j.buildings) if (b.map) {
        wrap.appendChild(h("span", { class: "cmpin", title: b.name, style: { left: (b.map[0] * 100 / j.size[0]) + "%", top: (b.map[1] * 100 / j.size[1]) + "%" } }));
      }
      root.appendChild(wrap);
    }
    if (!total) root.appendChild(h("p", { class: "hint" }, "No duty needs a building yet."));
    else {
      const STATE = { found: ["Found", "ok"], seen: ["Seen", "next"], none: ["Not yet", ""] };
      root.appendChild(h("div", { class: "cmlist" }, j.buildings.map((b) => h("div", { class: "cmrow" + (b.on ? "" : " off"), title: "Needed by: " + b.duties.join(", ") + (b.on ? "" : " (switched off)") },
        h("span", { class: "cmname" }, b.name), h("span", { class: "dbadge " + STATE[b.state][1] }, STATE[b.state][0])))));
      root.appendChild(h("p", { class: "hint" }, found === total && total ? "Every building the duties need is on the map." :
        "Found = the bot knows where it is on the map and goes straight there. Not yet = pinned the first time a daily task opens it (the Activity tile does that)."));
    }
    const run = async (again) => {
      if (again && !(await confirmBox("Map again", "Map the city again? The current map and the found places are replaced (about 20 minutes)."))) return;
      if (await act(api("run_now_add", { serial, id: "duty:map_city" }))) toast("Map my city runs next on this instance.", "ok");
    };
    root.appendChild(h("div", { class: "row", style: { justifyContent: "flex-end", marginTop: "10px" } },
      mapping !== null ? null : btn(j.built ? "Map again" : "Map my city", () => run(j.built), j.built ? "sm" : "pri")));
  };
  const load = async () => {
    const j = await api("city_map_info", { serial }).catch(() => null);
    if (!j || !root.isConnected) return;
    last = JSON.stringify(j) + "|" + nowText();
    paint(j);
  };
  load();
  const tick = setInterval(async () => {                 // refresh while mapping or when the map changed
    if (!root.isConnected) { clearInterval(tick); return; }
    const j = await api("city_map_info", { serial }).catch(() => null);
    if (j && JSON.stringify(j) + "|" + nowText() !== last) { last = JSON.stringify(j) + "|" + nowText(); paint(j); }
  }, 15000);
  return root;
}

function runNowCard(serial) {
  const root = h("div", { class: "panel runnow" });
  let pick = "", last = "";
  const set = async (name, o) => { const r = await act(api(name, { serial, ...o })); if (r) { last = JSON.stringify(r.queue); draw(r); } };
  const draw = (j) => {
    root.innerHTML = "";
    const groups = {};
    for (const t of j.tasks || []) {
      const id = t.id.startsWith("duty:") ? t.id.slice(5) : "";
      if (id && !dutyShown(id)) continue;
      // one-time jobs on top (Setup), developer test duties in their own group at the bottom - never mixed into City
      const g = !id ? "My tasks" : RUN_SETUP.has(id) ? "Setup" : DUTY_TEST.has(id) ? "Testing (developer)" : dutyGroup(id, t.group);
      (groups[g] = groups[g] || []).push([t.id, id ? dutyName(id, t.title) : t.title]);
    }
    const go = h("button", { class: "btn", type: "button", disabled: !pick, onclick: () => { if (pick) { const id = pick; pick = ""; set("run_now_add", { id }); } } }, "Run now");
    const s = h("select", { "aria-label": "Task to run", onchange: (e) => { pick = e.target.value; go.disabled = !pick; } }, h("option", { value: "" }, "Pick a task\u2026"),
      ["Setup", ...DUTY_GROUPS, "Testing (developer)"].filter((g) => groups[g]).map((g) => h("optgroup", { label: g }, groups[g].map(([v, t]) => h("option", { value: v, selected: v === pick }, t)))));
    root.append(...[h("div", { class: "cardhead" }, h("h3", {}, "Run a task now")),
      h("small", { class: "sub" }, serial ? "Runs once, before anything else, then the bot carries on." : "Runs once on every instance, before anything else."),
      h("div", { class: "runrow" }, s, go),
      (j.queue || []).length ? h("div", { class: "row", style: { gap: "6px", flexWrap: "wrap", marginTop: "8px" } }, h("span", { class: "lab" }, "Waiting:"),
        j.queue.map((q, i) => { const id = String(q.id || "").startsWith("duty:") ? q.id.slice(5) : ""; return h("span", { class: "chip" }, (i + 1) + ". " + (id ? dutyName(id, q.title) : q.title) + " ",
          h("a", { href: "#", title: "Cancel", "aria-label": "Cancel", onclick: (e) => { e.preventDefault(); set("run_now_remove", { id: q.id }); } }, "\u00d7")); })) : null].filter(Boolean));
  };
  draw({ tasks: [], queue: [] });
  act(api("run_now", { serial })).then((j) => { if (j) { last = JSON.stringify(j.queue); draw(j); } });
  const tick = setInterval(async () => {          // the queue empties when the bot starts the task: redraw only when it changed
    if (!root.isConnected) { clearInterval(tick); return; }
    const j = await api("run_now", { serial }, { quiet: true }).catch(() => null);
    if (j && j.queue && JSON.stringify(j.queue) !== last && !root.contains(document.activeElement)) { last = JSON.stringify(j.queue); draw(j); }
  }, 10000);
  return root;
}


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
  // ---- head: state only - the switch is the duty in Routines > Army (C10), this tab holds the setup
  const head = h("div", { class: "panel mrhead" });
  const toRoutines = (e) => { e.preventDefault(); S.dutyView = { show: "all", group: "Army" }; if (S.gotoInstTab) S.gotoInstTab("routines"); };
  const paintHead = () => {
    head.innerHTML = "";
    if (!duty) return;
    head.append(
      h("div", { class: "mrtitle" }, h("h3", {}, "Monster rallies"),
        h("small", { class: "sub" }, "Starts an Alliance War rally on monsters from the Telegram section, with the preset each monster sits on. Not tested in the game yet.")),
      duty.on ? dutyBadge(duty.status, duty.status_text) : h("span", { class: "mute" }, "Off"),
      h("a", { href: "#", class: "lnk", onclick: toRoutines }, duty.on ? "Switch in Routines \u2192" : "Switch it on in Routines \u2192"));
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
          mine.length ? h("button", { class: "btn sm mrtime", type: "button", "aria-label": "Rally time for preset " + p, title: "Rally time for preset " + p,
            onclick: (e) => menu(e.currentTarget, MR_TIMES.map((v) => ({ label: v + " min" + (String(v) === laneTime(p) ? "  \u2713" : ""),
              run: () => { mine.forEach(([r]) => (r.time = String(v))); saveRules(); paintLanes(); paintCoords(); } }))) }, laneTime(p) + " min \u25BE") : null),
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


// ====================================================================== logs (Control settings > Logs): save the log right here, then the live log
// ====================================================================== reports: who joined what, per day / week / month
PAGES.reports = function () {
  S.repDays = S.repDays || 1;
  const ranges = [[1, "Today"], [7, "7 days"], [30, "30 days"]];
  const seg = h("div", { class: "seg", role: "tablist" }, ...ranges.map(([d, l]) => h("button", { class: "segb" + (S.repDays === d ? " on" : ""), role: "tab", onclick: () => { S.repDays = d; rerender(); } }, l)));
  const body = h("div", { class: "repbody" }, h("div", { class: "empty" }, "Loading\u2026"));
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
    h("div", { class: "row", style: { marginTop: "12px" } }, btn("Copy last lines", copyLog, "sm"), btn("Report a problem\u2026", saveReportFlow, "sm"),
      btn("Open logs folder", () => act(api("open_logs")), "sm")));
  const live = panel("Live log - all instances", h("div", { class: "row", style: { marginBottom: "10px" } },
      btn("Clear", async () => { await act(api("clear_log")); S.logs = []; logViews.forEach((v) => (v.el.innerHTML = "")); }, "sm")), logBox(null, true));
  return h("div", {}, save, live);
};
