/* Copyright (c) 2026 Kiravik. All rights reserved. RallyForge Bot was created by Kiravik. */
/* RallyForge Bot - dialogs: modal, prompts, screenshot pickers, general manager, scans, task editor. */
"use strict";

function modal(o) {
  const ov = h("div", { class: "ov" });
  const body = h("div", { class: "mb" }, o.body);
  const foot = h("div", { class: "mf" });
  const md = h("div", { class: "md" + (o.wide ? " wide" : "") }, h("div", { class: "mh" }, o.title || ""), body, foot);
  ov.appendChild(md);
  const close = () => { document.removeEventListener("keydown", onKey, true); ov.remove(); };
  const cancel = () => { if (o.oncancel) o.oncancel(); close(); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); cancel(); } };
  document.addEventListener("keydown", onKey, true);
  for (const b of o.buttons || [{ label: "OK", cls: "pri" }]) {
    foot.appendChild(h("button", { class: "btn " + (b.cls || ""), onclick: async () => {
      let r;
      if (b.onclick) r = await b.onclick();
      if (r !== false && !b.keep) close();
    } }, b.label));
  }
  document.body.appendChild(ov);
  const first = md.querySelector("input[type=text],input[type=number],textarea,select");
  if (first && o.focus !== false) setTimeout(() => first.focus(), 30);
  return { close, el: md, body };
}

function confirmBox(title, text, okLabel, noLabel) {
  return new Promise((res) => {
    const buttons = [];
    buttons.push({ label: "Cancel", onclick: () => res(noLabel ? null : false) });
    if (noLabel) buttons.push({ label: noLabel, onclick: () => res(false) });
    buttons.push({ label: okLabel || "OK", cls: "pri", onclick: () => res(true) });
    modal({ title, body: h("div", { style: { whiteSpace: "pre-wrap", maxWidth: "460px" } }, text), buttons, oncancel: () => res(noLabel ? null : false), focus: false });
  });
}
function infoBox(title, text) {
  return new Promise((res) => modal({ title, body: h("div", { style: { whiteSpace: "pre-wrap", maxWidth: "460px" } }, text), buttons: [{ label: "OK", cls: "pri", onclick: () => res() }], oncancel: res, focus: false }));
}
function promptBox(title, label, def, multiline) {
  return new Promise((res) => {
    const inp = multiline ? h("textarea", { value: def || "" }) : h("input", { type: "text", value: def || "" });
    const done = () => res(inp.value.trim());
    if (!multiline) inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { done(); document.querySelector(".ov")?.remove(); } });
    modal({ title, body: h("div", { class: "field", style: { minWidth: "380px" } }, h("label", { style: { whiteSpace: "pre-line" } }, label), inp),
      buttons: [{ label: "Cancel", onclick: () => res(null) }, { label: "OK", cls: "pri", onclick: done }], oncancel: () => res(null) });
  });
}
function choiceBox(title, items) {
  return new Promise((res) => {
    let sel = 0;
    const list = h("div", { style: { minWidth: "380px" } });
    const draw = () => { list.innerHTML = ""; items.forEach((t, i) => list.appendChild(h("div", { class: "li" + (i === sel ? " sel" : ""), onclick: () => { sel = i; draw(); }, ondblclick: () => { m.close(); res(i); } }, t))); };
    draw();
    const m = modal({ title, body: list, buttons: [{ label: "Cancel", onclick: () => res(null) }, { label: "OK", cls: "pri", onclick: () => res(sel) }], oncancel: () => res(null), focus: false });
  });
}
async function chooseDevice(title) {
  const ids = S.devices.filter((d) => d.id.trim());
  if (!ids.length) { toast("Add an instance first.", "error"); return null; }
  if (ids.length === 1) return ids[0].id;
  const i = await choiceBox(title, ids.map((d) => `${d.name || d.id}   (${d.id})`));
  return i === null ? null : ids[i].id;
}

// ------------------------------------------------------------------ screenshots
const readFile = (f) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f); });

function pickScreenshot(title) {
  return new Promise((res) => {
    const ids = S.devices.filter((d) => d.id.trim());
    const sel = h("select", {}, ids.map((d) => h("option", { value: d.id }, `${d.name || d.id}   (${d.id})`)));
    const file = h("input", { type: "file", accept: "image/*", style: { display: "none" } });
    let m;
    file.onchange = async () => {
      const f = file.files[0];
      if (!f) return;
      const j = await act(api("shot_upload", { data: await readFile(f) }));
      if (j) { m.close(); res(j); }
    };
    const buttons = [{ label: "Cancel", onclick: () => res(null) }, { label: "Choose image file...", keep: true, onclick: () => file.click() }];
    if (ids.length) buttons.push({ label: "Take from instance", cls: "pri", keep: true, onclick: async (e) => {
      const j = await act(api("shot_take", { serial: sel.value }));
      if (j) { m.close(); res(j); }
    } });
    m = modal({ title, oncancel: () => res(null), focus: false, buttons,
      body: h("div", { style: { minWidth: "420px" } },
        h("p", { class: "note" }, "Use a screenshot from an instance right now (exact size, best for matching) or load an image file."),
        ids.length ? h("div", { class: "field" }, h("label", {}, "Instance"), sel) : h("p", { class: "note" }, "No instance is added yet - choose an image file."), file) });
  });
}

// A screenshot on a canvas. mode: "click" | "region" | "portrait". Resolves with the result or null.
function pickOnShot(shot, hint, mode, boxSize) {
  return new Promise((res) => {
    const SC = 0.7, W = Math.round(shot.w * SC), H = Math.round(shot.h * SC);
    const img = h("img", { src: `/api/shot?token=${shot.token}&k=${window.KEY}`, width: W, height: H, draggable: false });
    const cv = h("canvas", { width: W, height: H });
    const ctx = cv.getContext("2d");
    const draw = (x, y, w, hh, ok) => {
      ctx.clearRect(0, 0, W, H);
      if (w === undefined) return;
      ctx.lineWidth = 2; ctx.strokeStyle = ok === false ? "#ef4444" : "#22c55e";
      ctx.strokeRect(x * SC, y * SC, w * SC, hh * SC);
    };
    const pt = (e) => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) / SC, (e.clientY - r.top) / SC]; };
    let start = null, dragging = false, last = null, busy = false, timer = null;
    let m;
    const finish = (v) => { m.close(); res(v); };
    cv.addEventListener("mousemove", async (e) => {
      const [x, y] = pt(e);
      if (mode === "click") draw(x - boxSize[0] / 2, y - boxSize[1] / 2, boxSize[0], boxSize[1]);
      else if (start) {
        if (Math.abs(x - start[0]) > 6 / SC * SC || Math.abs(y - start[1]) > 6) dragging = true;
        if (dragging) draw(Math.min(start[0], x), Math.min(start[1], y), Math.abs(x - start[0]), Math.abs(y - start[1]));
      } else if (mode === "portrait" && !busy) {
        busy = true;
        try {
          const j = await api("portrait_rect", { token: shot.token, x, y }, { quiet: true });
          last = j.rect;
          if (j.rect) draw(...j.rect); else draw(x - 30, y - 30, 60, 60, false);
        } catch (er) {} finally { setTimeout(() => (busy = false), 60); }
      }
    });
    cv.addEventListener("mousedown", (e) => { if (mode !== "click") { start = pt(e); dragging = false; } });
    cv.addEventListener("mouseup", async (e) => {
      const [x, y] = pt(e);
      if (mode === "click") return finish({ x: Math.round(x), y: Math.round(y) });
      if (!start) return;
      const s = start; start = null;
      if (dragging) {
        const w = Math.abs(x - s[0]), hh = Math.abs(y - s[1]);
        const min = mode === "portrait" ? 20 : 6;
        if (w < min || hh < min) { draw(); return; }
        return finish([Math.round(Math.min(s[0], x)), Math.round(Math.min(s[1], y)), Math.round(w), Math.round(hh)]);
      }
      if (mode === "portrait") {
        const j = await act(api("portrait_rect", { token: shot.token, x, y }, { quiet: true }));
        if (j && j.rect) finish(j.rect);
      }
    });
    m = modal({ title: "Pick on the screenshot", oncancel: () => res(null), focus: false, buttons: [{ label: "Cancel", onclick: () => res(null) }],
      body: h("div", { style: { textAlign: "center" } }, h("p", { class: "note", style: { maxWidth: W + "px", margin: "0 auto 10px", textAlign: "left" } }, hint),
        h("div", { class: "picker" }, img, cv)) });
  });
}

// ------------------------------------------------------------------ generals: choose list, manager, add, scan
function genFilter(onchange) {
  const troops = ["All troop types", ...new Set(S.generals.flatMap((g) => g.troops || []))];
  const regions = ["All regions", ...new Set(S.generals.flatMap((g) => g.region || []))];
  const cats = ["All categories", ...new Set(S.generals.map((g) => g.cat).filter(Boolean)), "(no category)"];
  const st = { troop: troops[0], region: regions[0], cat: cats[0] };
  const mk = (arr, key) => h("select", { onchange: (e) => { st[key] = e.target.value; onchange(); } }, arr.map((x) => h("option", {}, x)));
  const hasCat = cats.length > 2;
  const el = (troops.length > 1 || regions.length > 1 || hasCat) ? h("div", { class: "row", style: { margin: "6px 0" } }, troops.length > 1 ? mk(troops, "troop") : null, regions.length > 1 ? mk(regions, "region") : null, hasCat ? mk(cats, "cat") : null) : h("span");
  st.ok = (g) => (st.troop === troops[0] || (g.troops || []).includes(st.troop)) && (st.region === regions[0] || (g.region || []).includes(st.region))
    && (st.cat === cats[0] || (st.cat === "(no category)" ? !g.cat : g.cat === st.cat));
  st.el = el;
  return st;
}

// ---- general lists: pictured generals first, search box, grid / list switch (remembered on this PC)
function genViewGet() { try { return localStorage.getItem("rf_genview") === "grid" ? "grid" : "list"; } catch (e) { return "list"; } }
function genViewSet(v) { try { localStorage.setItem("rf_genview", v); } catch (e) {} }
function genSearch(onTerm) {
  const inp = h("input", { type: "text", placeholder: "Type to search generals (works in List and Grid)", oninput: () => { clr.hidden = !inp.value; onTerm(inp.value.trim().toLowerCase()); } });
  const clr = h("button", { type: "button", class: "gsclr", title: "Clear the search", onclick: () => { inp.value = ""; clr.hidden = true; onTerm(""); inp.focus(); } }, "\u2715");
  clr.hidden = true;
  return h("div", { class: "gsearch" }, h("span", { class: "gsico", "aria-hidden": "true" }), inp, clr);
}
function genSorted(arr) { return arr.slice().sort((a, b) => (b.pic !== false) - (a.pic !== false) || a.name.toLowerCase().localeCompare(b.name.toLowerCase())); }
function genViewBar(onchange) {                     // List / Grid of the generals lists: a segmented control like every pick-one choice
  const st = { v: genViewGet() };
  return segPick(st, "v", [["list", "List", "Every general, one per line"], ["grid", "Grid", "Only generals that have a picture, shown as thumbnails"]],
    { small: true, label: "Show generals as", after: (v) => { genViewSet(v); onchange(); } });
}

function chooseGenerals(title, current) {
  return new Promise((res) => {
    let chosen = [...current];
    const A = h("div", { class: "lst" }), C = h("div", { class: "lst gorder" });
    const flt = genFilter(() => draw());
    let term = "";
    const search = genSearch((t) => { term = t; draw(); });
    const viewBar = genViewBar(() => draw());
    const gimg = (n) => h("img", { src: `/api/general_img?name=${encodeURIComponent(n)}&k=${window.KEY}`, onerror: (e) => (e.target.style.visibility = "hidden") });
    const toggle = (n) => { chosen = chosen.includes(n) ? chosen.filter((x) => x !== n) : [...chosen, n]; draw(); };
    const clear = h("button", { type: "button", class: "linkbtn", onclick: () => { chosen = []; draw(); } }, "Clear all");
    const leftCard = (n, kids, tip) => h("div", { class: "li" + (chosen.includes(n) ? " added" : ""), draggable: "true", title: tip,
      ondragstart: (e) => { e.dataTransfer.setData("text/plain", n); e.dataTransfer.effectAllowed = "copy"; }, onclick: () => toggle(n) },
      chosen.includes(n) ? h("span", { class: "gadded" }, "\u2713") : null, ...kids);
    // ---- reorder by dragging the handle (pointer events: works with mouse, pen and touch)
    let drag = null;
    const rowAt = (y) => [...C.querySelectorAll(".gorow")].findIndex((r) => { const b = r.getBoundingClientRect(); return y < b.top + b.height / 2; });
    const renumber = () => C.querySelectorAll(".gorow").forEach((r, k) => { r.dataset.i = k; r.querySelector(".gonum").textContent = k + 1; });
    const startDrag = (e, row) => {
      e.preventDefault(); row.setPointerCapture(e.pointerId); row.classList.add("dragging"); drag = { row };
      row.onpointermove = (ev) => {
        let k = rowAt(ev.clientY); const rows = [...C.querySelectorAll(".gorow")];
        if (k < 0) k = rows.length; const from = +row.dataset.i;
        if (k === from || k === from + 1) return;
        C.insertBefore(row, rows[k] || null);
        const n = chosen.splice(from, 1)[0]; chosen.splice(k > from ? k - 1 : k, 0, n); renumber();
      };
      row.onpointerup = row.onpointercancel = row.onlostpointercapture = () => { if (!drag) return; row.onpointermove = row.onpointerup = row.onpointercancel = row.onlostpointercapture = null; drag = null; draw(); };
    };
    const draw = () => {
      if (drag) return;
      const keepA = A.scrollTop, keepC = C.scrollTop;          // clicking a card must not jump the list back to the top
      A.innerHTML = ""; C.innerHTML = "";
      A.className = "lst" + (genViewGet() === "grid" ? " grid" : "");
      const cnt = {};
      S.generals.forEach((g) => { if (g.cat) cnt[g.cat] = (cnt[g.cat] || 0) + 1; });
      Object.keys(cnt).sort().forEach((c) => A.appendChild(leftCard("@" + c, [h("b", {}, "Category: " + c), h("span", { class: "hint", style: { marginLeft: "8px" } }, cnt[c] + (cnt[c] === 1 ? " general" : " generals"))], "Every general in this category is tried (the first free one is used). Click to add or remove.")));
      genSorted(S.generals.filter((g) => flt.ok(g) && (!term || g.name.toLowerCase().includes(term)) && (genViewGet() === "list" || g.pic !== false))).forEach((g) =>
        A.appendChild(leftCard(g.name, [genViewGet() === "grid" ? gimg(g.name) : null, h("span", { class: "gname" }, g.name), g.pic === false ? h("span", { class: "hint", style: { marginLeft: "6px" } }, "(by name)") : null],
          (chosen.includes(g.name) ? "Added - click to take it out" : "Click to add") + (g.pic === false ? ". No picture yet: the bot finds this general by its name" : ""))));
      if (term && !A.querySelector(".li")) A.appendChild(h("div", { class: "empty" }, "No general matches '" + term + "'."));
      if (!S.generals.length) A.appendChild(h("div", { class: "empty" }, "No saved generals yet. Use 'Scan generals' or 'Add general from screenshot'."));
      if (!chosen.length) C.appendChild(h("div", { class: "empty gempty" }, "No generals added yet. Click a general on the left (or drag it here) to add it."));
      chosen.forEach((n, i) => {
        const row = h("div", { class: "gorow", "data-i": i });
        row.append(h("span", { class: "gohandle", title: "Drag to change the order", onpointerdown: (e) => startDrag(e, row) }, "\u22EE\u22EE"),
          n.startsWith("@") ? h("span", { class: "goimg cat" }, "\u2261") : gimg(n),
          h("span", { class: "goname" }, n.startsWith("@") ? "Category: " + n.slice(1) : n),
          h("span", { class: "gonum" }, i + 1),
          h("button", { type: "button", class: "gox", title: "Remove", onclick: () => toggle(n) }, "\u2715"));
        C.appendChild(row);
      });
      clear.disabled = !chosen.length;
      A.scrollTop = keepA; C.scrollTop = keepC;
    };
    C.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; C.classList.add("drop"); });
    C.addEventListener("dragleave", () => C.classList.remove("drop"));
    C.addEventListener("drop", (e) => { e.preventDefault(); C.classList.remove("drop"); const n = e.dataTransfer.getData("text/plain"); if (n && !chosen.includes(n)) { chosen.push(n); draw(); } });
    draw();
    modal({ title, wide: true, oncancel: () => res(null), focus: false, buttons: [{ label: "Cancel", onclick: () => res(null) }, { label: "OK", cls: "pri", onclick: () => res(chosen) }],
      body: h("div", { class: "two" },
        h("div", {}, h("div", { class: "ghead" }, h("div", { class: "lab" }, "All generals"), viewBar), h("div", { class: "gbox" }, search, flt.el, A)),
        h("div", {}, h("div", { class: "ghead" }, h("div", { class: "lab" }, "Tried in this order (top first)"), clear), h("div", { class: "gbox" }, C))) });
  });
}

async function addGeneralFlow(name) {
  const shot = await pickScreenshot("Screenshot of the general list (grid view)");
  if (!shot) return;
  const rect = await pickOnShot(shot, "Move over the general's card: the green box is the whole portrait that will be saved (without the stars, level and 'Idle' text). Click to use it, or press and drag to draw your own box. A red box means that card is cut off by the edge of the list: scroll it fully into view and take a new screenshot.", "portrait");
  if (!rect) return;
  name = name || (await promptBox("General name", "Name of this general (e.g. Spartacus):", ""));
  if (!name) return;
  const j = await act(api("general_add", { token: shot.token, rect, name }));
  if (j) { S.generals = j.generals; toast(`Saved general picture ${j.name}`, "ok"); }
}

function openGeneralManager() {
  const m = { tab: "", fav: "" };                    // the chosen general's own settings ("" = use the Main / Assistant setting)
  const COPIES = ["Any (best match)", "Most stars", "1st on screen", "2nd on screen", "3rd on screen", "4th on screen", "5th on screen", "6th on screen"];
  let cur = null, term = "";
  const list = h("div", { class: "lst", style: { maxHeight: "430px", minHeight: "430px" } });
  const search = genSearch((t) => { term = t; drawList(); });
  const name = h("input", { type: "text" });
  const tabHold = h("div", {}), favHold = h("div", {});
  const drawSegs = () => {                           // pick-one choices = segmented controls, same as the Generals card; a change saves at once
    tabHold.replaceChildren(segPick(m, "tab", [["", "Default", "Use the Main / Assistant 'Search in' setting"], ...GEN_TABS.slice(1)], { small: true, label: "Search in", after: save }));
    favHold.replaceChildren(segPick(m, "fav", [["", "Default", "Use the Main / Assistant favourites setting"], ...GEN_FAVS], { small: true, label: "Favourites filter", after: save }));
  };
  const copy = h("select", { onchange: () => save() }, COPIES.map((t) => h("option", {}, t)));
  const catList = h("datalist", { id: "genCats" });
  const catIn = h("input", { type: "text", list: "genCats", placeholder: "e.g. Ground, Mounted, Ranged - or leave empty", onchange: () => save() });
  const fillCats = () => { catList.innerHTML = ""; [...new Set(S.generals.map((g) => g.cat).filter(Boolean))].sort().forEach((c) => catList.appendChild(h("option", { value: c }))); };
  const pic = h("img", { class: "gen-pic", style: { display: "none" } });
  const msg = h("div", { class: "hint" }, "Changes save by themselves. The picture must come from a screenshot of the thumbnail view, because the bot finds the general by matching it.");
  name.addEventListener("change", () => save());
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); name.blur(); } });
  const find = (n) => S.generals.find((g) => g.name === n);
  const flt = genFilter(() => drawList());
  function drawList() {
    list.innerHTML = "";
    list.className = "lst" + (genViewGet() === "grid" ? " grid" : "");
    const items = genSorted(S.generals.filter((g) => (!term || g.name.toLowerCase().includes(term)) && flt.ok(g) && (genViewGet() === "list" || g.pic !== false)));
    items.forEach((g) => list.appendChild(h("div", { class: "li" + (g.name === cur ? " sel" : ""), onclick: () => select(g.name) },
      genViewGet() === "grid" ? h("img", { src: `/api/general_img?name=${encodeURIComponent(g.name)}&k=${window.KEY}&t=${Date.now() % 100000}`, onerror: (e) => (e.target.style.display = "none") }) : null,
      h("span", { class: "gname", style: { flex: 1 } }, g.name), h("span", { class: "hint gmeta", style: { color: "inherit", opacity: .7 } }, `${g.cat ? g.cat + " · " : ""}${g.tab == null || Number(g.tab) === 0 ? (g.cat_tab ? TAB_CHOICES[g.cat_tab] + " (auto)" : "Auto") : TAB_CHOICES[Number(g.tab)]}${g.fav ? " · favourites " + g.fav : ""}`))));
    if (!items.length) list.appendChild(h("div", { class: "empty" }, "No generals. Use 'Scan from the game' or 'Add from screenshot'."));
  }
  function select(n) {
    cur = n; const g = find(n);
    if (!g) return;
    name.value = n;
    detail.style.display = ""; pickHint.style.display = "none"; delBtn.disabled = false;
    m.tab = g.tab == null || Number(g.tab) === 0 ? "" : Number(g.tab);
    m.fav = g.fav || "";
    drawSegs();
    catIn.value = g.cat || ""; fillCats();
    const c = g.copy;
    copy.selectedIndex = c === "stars" ? 1 : c === "any" || c == null ? 0 : (Number(c) >= 1 && Number(c) <= 6 ? 1 + Number(c) : 0);
    pic.src = `/api/general_img?name=${encodeURIComponent(n)}&k=${window.KEY}&t=${Date.now()}`; pic.style.display = "";
    drawList();
  }
  async function save() {
    if (!cur) return;
    if (typeof saveState === "function") saveState("saving");
    const t = m.tab === "" ? null : Number(m.tab);
    const f = m.fav === "" ? null : m.fav;
    const ci = copy.selectedIndex;
    const j = await act(api("general_meta", { name: cur, new_name: name.value.trim(), tab: t, fav: f, copy: ci === 0 ? null : ci === 1 ? "stars" : ci - 1, cat: catIn.value.trim() }));
    if (!j) { if (typeof saveState === "function") saveState("err", "general settings - see the message"); return; }
    if (j.renamed) swapGeneral(cur, j.name);
    S.generals = j.generals; cur = j.name; drawList(); select(cur); msg.textContent = `Saved '${j.name}'.`; if (typeof saveState === "function") saveState("saved");
  }
  async function del() {
    if (!cur || !(await confirmBox("Delete general", `Delete '${cur}' and its picture?`))) return;
    const j = await act(api("general_delete", { name: cur }));
    if (j) { swapGeneral(cur, null); S.generals = j.generals; cur = null; pic.style.display = "none"; name.value = ""; detail.style.display = "none"; pickHint.style.display = ""; delBtn.disabled = true; drawList(); }
  }
  const delBtn = h("button", { class: "btn sm", onclick: del, disabled: true, title: "Delete the chosen general" }, "Delete");
  async function recapture() { if (cur) { await addGeneralFlow(cur); drawList(); select(cur); } }
  const left = h("div", {}, h("div", { class: "row", style: { marginBottom: "8px" } },
    h("button", { class: "btn sm pri", onclick: async () => { await addGeneralFlow(); drawList(); } }, "Add from screenshot\u2026"),
    h("button", { class: "btn sm", onclick: () => scanGeneralsFlow() }, "Scan from the game\u2026"),
    h("button", { class: "btn sm", onclick: () => tavernPortraitsFlow() }, "Scan Tavern portraits\u2026"),
    delBtn, genViewBar(() => drawList())), h("div", { class: "gbox", style: { height: "470px" } }, search, flt.el, list));
  const detail = h("div", { class: "field", style: { gap: "9px", display: "none" } }, h("label", {}, "General name"), name, h("label", {}, "Search in (tab of 'Select a General')"), tabHold,
    h("label", {}, "Favourites filter (heart)"), favHold, h("label", {}, "If you own several copies of it"), copy,
    h("label", {}, "Category (your own group, e.g. Ground / Mounted)"), catIn, catList,
    h("label", {}, "Picture the bot looks for"), pic,
    h("button", { class: "btn sm", onclick: recapture }, "New picture from a screenshot\u2026"), msg);
  const pickHint = h("div", { class: "empty" }, h("b", {}, "No general chosen"), h("span", {}, "Click a general on the left to see and change its settings."));
  const right = h("div", {}, pickHint, detail);
  drawList();
  modal({ title: "General Manager", wide: true, focus: false, buttons: [{ label: "Close", cls: "pri" }], body: h("div", { class: "gen-body" }, left, right) });
}

// a general was renamed (new = its new name) or deleted (new = null): fix every place that used it
function swapGeneral(old, neu) {
  const fix = (arr) => (arr || []).map((n) => (n === old ? neu : n)).filter(Boolean);
  const c = S.cfg;
  c.general_main = fix(c.general_main); c.general_assistant = fix(c.general_assistant);
  (c.bot_presets || []).forEach((b) => { b.main = fix(b.main); b.assistant = fix(b.assistant); });
  Object.values(c.per_device || {}).forEach((ov) => { if (ov.general_main) ov.general_main = fix(ov.general_main); if (ov.general_assistant) ov.general_assistant = fix(ov.general_assistant); });
}

let scanBox = null;
function closeScanBox() { if (scanBox) { scanBox.close(); scanBox = null; } }
async function scanGeneralsFlow() {
  const serial = await chooseDevice("Scan with which instance?");
  if (!serial) return;
  const m = await choiceBox("How should the bot scan?", ["Automatic (recommended): the bot opens Generals from the city, switches to thumbnail view and scans all 5 tabs", "Only the list that is open right now (you open it, in GRID view)"]);
  if (m === null) return;
  const mode = m === 0 ? "all" : "current";
  const ok = await confirmBox("Scan generals", mode === "all"
    ? "On that instance, show the CITY screen (no pop-ups open).\n\nThe bot taps the round 3-dots button, opens Generals, switches to thumbnail view and scans All, Military, Development, Duty and Subordinate City. It remembers which tab each general is in. This takes a few minutes; do not touch the instance."
    : "In the game, on that instance:\n\n1. Open the list of generals.\n2. Switch it to GRID (thumbnail) view with the little button under the tabs.\n3. Pick the tab you want to scan.\n\nThe bot then scrolls through the whole list. Press OK when the list is open.", "Start the scan");
  if (!ok) return;
  if (!(await act(api("scan_generals", { serial, mode })))) return;
  scanBox = modal({ title: "Scanning generals", focus: false, buttons: [{ label: "Stop and use what was found", cls: "pri", onclick: () => { act(api("scan_generals_stop")); return false; } }],
    body: h("div", { id: "genScanInfo", style: { minWidth: "360px" } }, "Scrolling through the general list...") });
}
async function scanGeneralsDone(d) {
  closeScanBox();
  if (d.error) return toast(d.error, "error");
  if (!d.n) return toast("No generals were found.");
  const j = await act(api("scan_found"));
  if (!j) return;
  const items = j.items.map((it) => ({ ...it, pick: !it.exists }));
  const tab = h("select", {}, [["auto", "Detected automatically (recommended)"], ["0", "None - leave unset"], ["1", "1 - All"], ["2", "2 - Military"], ["3", "3 - Development"], ["4", "4 - Duty"], ["5", "5 - Subordinate City"]].map(([v, l]) => h("option", { value: v }, l)));
  const TN = ["", "All", "Military", "Development", "Duty", "Subordinate City"];
  const rep = h("input", { type: "checkbox" });
  const box = h("div", { class: "lst", style: { maxHeight: "400px", minHeight: "260px" } });
  const draw = () => {
    box.innerHTML = "";
    items.forEach((it) => box.appendChild(h("div", { class: "li", style: { gap: "10px" } },
      h("input", { type: "checkbox", checked: it.pick, onchange: (e) => (it.pick = e.target.checked) }),
      h("img", { src: `/api/scan_img?i=${it.i}&k=${window.KEY}`, style: { width: "36px", height: "39px" } }),
      h("input", { type: "text", value: it.name, style: { flex: 1 }, oninput: (e) => (it.name = e.target.value) }),
      h("span", { class: "hint" }, (it.stars ? it.stars + "★ · " : "") + ((it.tabs || []).length ? "in: " + it.tabs.map((t) => TN[t]).join(", ") + " · " : "") + (it.exists ? "already saved" : "new")))));
  };
  draw();
  modal({ title: "Generals found in the game", wide: true, focus: false, buttons: [
    { label: "Select all", keep: true, onclick: () => { items.forEach((i) => (i.pick = true)); draw(); } },
    { label: "Select none", keep: true, onclick: () => { items.forEach((i) => (i.pick = false)); draw(); } },
    { label: "Close" },
    { label: "Add selected to Manage generals", cls: "pri", onclick: async () => {
        const r = await act(api("scan_add", { items: items.filter((i) => i.pick).map((i) => ({ i: i.i, name: i.name })), tab: tab.value, replace: rep.checked }));
        if (!r) return false;
        S.generals = r.generals;
        let msg = `Added ${r.added.length} general(s).`;
        if (r.skipped.length) msg += `\nSkipped ${r.skipped.length} you already have (tick 'Replace pictures' to overwrite).`;
        if (r.problems.length) msg += "\n\nProblems:\n" + r.problems.join("\n");
        await infoBox("Add generals", msg);
        if (!r.added.length) return false;
      } }],
    body: h("div", {}, h("p", { class: "note" }, `${items.length} different generals found (copies of the same general are listed once). Tick the ones to add. Names are read from the game and may need a touch-up: just edit the name.`),
      box, h("div", { class: "row", style: { marginTop: "10px" } }, h("span", { class: "lab" }, "Tab the bot uses to find them"), tab,
        h("label", { class: "tgl" }, rep, h("span", { class: "sw" }), "Replace pictures of generals I already have"))) });
}

// ---- map the city by hand: every building gets a numbered plot (slot) from a drop-down; "Go" shows that plot
async function manualMapFlow(arg) {
  const serial = typeof arg === "string" ? arg : await chooseDevice("Map the city by hand on which instance?");
  if (!serial) return;
  if (!(await act(api("manual_open", { serial })))) return;
  let info = await act(api("slots_info", { serial }));
  if (!info) { act(api("manual_close", {}, { quiet: true })); return; }
  const W = 270, H = 480;
  const img = h("img", { width: W, height: H, draggable: false, style: { display: "block" } });
  const status = h("div", { class: "note" }, "Going to the top-left corner of the city...");
  const box = h("div", { class: "lst", style: { maxHeight: "480px", minWidth: "360px" } });
  let st = null, alive = true, shotN = 0, ready = false;
  const reload = () => { img.src = `/api/manual_shot?k=${window.KEY}&t=${++shotN}`; };
  const slotLabel = (p) => `Slot ${p.n}  (usually ${p.ref})`;
  const draw = () => {
    box.replaceChildren(...info.names.map((n) => {
      const opt = info.optional.includes(n);
      const cur = info.assigned[n] || 0;
      const sel = h("select", { style: { width: "210px" }, onchange: async (e) => {
        const j = await act(api("slot_assign", { serial, building: n, slot: Number(e.target.value) }));
        if (j) { info = j; draw(); }
      } }, h("option", { value: 0 }, opt ? "Not built / unknown" : "Unknown"), ...info.slots.map((p) => { const o = h("option", { value: p.n }, slotLabel(p)); if (p.n === cur) o.selected = true; return o; }));
      const go = h("button", { class: "btn", disabled: !cur, onclick: async () => {
        if (!ready || (st && st.busy)) return;
        status.textContent = "Moving the camera...";
        await act(api("manual_goto", { slot: Number(sel.value) }));
        reload();
      } }, "Go");
      return h("div", { class: "cr " + (cur ? "ok" : opt ? "opt" : "no"), style: { alignItems: "center", gap: "8px" } },
        h("span", { style: { width: "130px" } }, (cur ? "\u2713 " : opt ? "- " : "\u00b7 ") + n), sel, go);
    }));
  };
  const poll = async () => {
    while (alive) {
      const j = await act(api("manual_state", {}, { quiet: true }));
      if (j) {
        st = j;
        const wasReady = ready;
        ready = j.status === "Ready" && !j.busy;
        status.textContent = j.status !== "Ready" ? j.status : j.busy ? "Moving..." : "Pick a slot for each building. Press Go to see that slot in the picture and check it.";
        if (ready && !wasReady && !img.src) reload();
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  };
  const stopAll = () => { alive = false; act(api("manual_close", {}, { quiet: true })); };
  modal({ title: "Map the city by hand", wide: true, focus: false, oncancel: stopAll,
    buttons: [{ label: "Refresh picture", keep: true, onclick: () => reload() }, { label: "Done", cls: "pri", onclick: () => { stopAll(); setTimeout(() => showCityResult(serial), 100); } }],
    body: h("div", {}, status, h("div", { class: "row", style: { alignItems: "flex-start", gap: "16px" } }, img, box)) });
  draw();
  poll();
}

async function scanCityFlow(arg) {
  const again = typeof arg === "string" ? arg : null;
  const serial = again || await chooseDevice("Map the city with which instance?");
  if (!serial) return;
  if (!again && !(await confirmBox("City mapper", "Open the CITY screen (not the rally list) on that instance. The instance must be stopped; the other instances and this app stay free while it sweeps the map.", "OK, start"))) return;
  await act(api("save", { cfg: S.cfg, devices: S.devices }, { quiet: true }));
  if (!(await act(api("scan_city", { serial })))) return;
  S.cityJobs = { ...(S.cityJobs || {}), [serial]: { running: true, progress: null } };
  toast("Mapping started on " + cityName(serial) + ". You can keep working - progress is in the corner.", "ok");
  drawCityProgress();
}
function cityName(serial) {
  const d = (S.devStatus || []).find((x) => x.id === serial);
  return d ? (d.name || serial) : serial;
}
function drawCityProgress() {
  // A small card per instance in the bottom-right corner: it never covers the app, so other instances can be worked on meanwhile.
  let dock = document.getElementById("cityDock");
  const jobs = Object.entries(S.cityJobs || {}).filter(([, j]) => j && j.running);
  const done = Object.keys(S.cityDone || {});
  if (!jobs.length && !done.length) { if (dock) dock.remove(); return; }
  if (!dock) {
    dock = h("div", { id: "cityDock", style: { position: "fixed", right: "14px", bottom: "40px", zIndex: 60, display: "flex", flexDirection: "column", gap: "8px", maxWidth: "340px" } });
    document.body.appendChild(dock);
  }
  const cards = [];
  for (const [serial, j] of jobs) {
    const p = j.progress;
    let txt = "Starting...", pct = 2;
    if (p) {
      pct = Math.max(2, Math.min(100, p.pct != null ? p.pct : Math.round(100 * p.stops / p.total)));
      const m = Math.floor(p.seconds / 60), sec = String(p.seconds % 60).padStart(2, "0");
      const left = p.left == null ? "" : (p.left >= 60 ? ` - about ${Math.ceil(p.left / 60)} min left` : ` - ${p.left} s left`);
      txt = `${p.text || "Reading the city"}\n${pct}%  -  ${p.mapped.length} of ${p.expected.length} buildings found\n${m}:${sec}${left}`;
    }
    cards.push(h("div", { class: "card", style: { padding: "10px 12px", boxShadow: "0 6px 24px rgba(0,0,0,.35)" } },
      h("div", { style: { fontWeight: "600", marginBottom: "4px" } }, "Mapping city - " + cityName(serial)),
      h("div", { class: "note", style: { whiteSpace: "pre-line" } }, txt),
      h("div", { class: "pbar" }, h("div", { class: "pfill", style: { width: pct + "%" } })),
      h("div", { style: { marginTop: "6px", textAlign: "right" } }, h("button", { class: "btn sm", onclick: () => { act(api("scan_city_stop", { serial })); } }, "Stop and save"))));
  }
  for (const serial of done) {
    cards.push(h("div", { class: "card", style: { padding: "10px 12px", boxShadow: "0 6px 24px rgba(0,0,0,.35)" } },
      h("div", { style: { fontWeight: "600", marginBottom: "6px" } }, "City mapping finished - " + cityName(serial)),
      h("div", { style: { textAlign: "right" } },
        h("button", { class: "btn sm", onclick: () => { delete S.cityDone[serial]; drawCityProgress(); } }, "Dismiss"), " ",
        h("button", { class: "btn sm pri", onclick: () => { delete S.cityDone[serial]; drawCityProgress(); showCityResult(serial); } }, "See result"))));
  }
  dock.replaceChildren(...cards);
}
async function showCityResult(given) {
  const serial = given || S.cityScanSerial;
  S.cityScanSerial = null;
  if (!serial) return;
  const r = await act(api("city_result", { serial }));
  if (!r) return;
  const rowsAll = r.rows.filter((x) => x.found || !x.optional);       // optional buildings that were not seen are not counted
  const found = rowsAll.filter((x) => x.found).length;
  const secs = r.seconds || 0;
  const rows = r.rows.map((x) => h("div", { class: "cr " + (x.found ? "ok" : x.optional ? "opt" : "no") },
    h("span", {}, (x.found ? "\u2713 " : x.optional ? "- " : "\u2717 ") + x.name), h("span", { class: x.found ? "mute" : x.optional ? "mute" : "bad" }, x.found ? ("Ready" + (x.level ? "  -  level " + x.level : "")) : x.optional ? "Not built (optional)" : "Not seen")));
  modal({ title: "City map result", wide: true, focus: false, buttons: [{ label: "Close" }, { label: "Map by hand", onclick: () => { setTimeout(() => manualMapFlow(serial), 50); } }, { label: "Retry", cls: "pri", onclick: () => { setTimeout(() => scanCityFlow(serial), 50); } }],
    body: h("div", {}, h("p", { class: "note" }, `${found} of ${rowsAll.length} buildings mapped in ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}.` + (found < rowsAll.length ? " The ones with a cross were not seen - run the mapper again or map them by hand." : " The city map is complete.")),
      h("div", { class: "lst", style: { maxHeight: "420px" } }, rows)) });
}

async function detectDevices(quiet) {
  const j = await act(api("detect"));
  if (!j) return;
  S.devices = j.devices;
  S.avail = j.available || [];
  if (quiet === true && !(j.found && S.avail.length)) { if (S.sel === "control" && S.tab === "dashboard") rerender(); return; }
  if (j.found === null) toast("ADB not found. Set the ADB path in Settings.", "error");
  else if (!j.found.length) toast("No instances found. Start them and enable ADB.", "error");
  else toast(S.avail.length ? `Found ${j.found.length} instance(s). ${S.avail.length} are not in your list yet - press Add next to the ones you want.` : `Found ${j.found.length} instance(s) - all of them are already in your list.`, "ok");
  if (S.sel === "control" && S.tab === "dashboard") rerender();
}

// ------------------------------------------------------------------ city task editor
function taskDialog(task) {
  return new Promise((res) => {
    const t = task ? clone(task) : { name: "", enabled: true, target: "icon:Mail", every_min: 60, count: 1, steps: [] };
    const targets = Object.keys(S.meta.icons).map((n) => "icon:" + n).concat(S.meta.buildings.map((n) => "building:" + n));
    const name = h("input", { type: "text", value: t.name });
    const tgt = h("input", { type: "text", value: t.target, list: "targets" });
    const every = h("input", { type: "number", class: "short", value: t.every_min });
    const count = h("input", { type: "number", class: "short", value: t.count || 1 });
    const on = h("input", { type: "checkbox", checked: t.enabled !== false });
    const at = h("input", { type: "text", value: t.at || "", placeholder: "e.g. 09:00, 21:30  (empty = use Repeat every)" });
    const steps = h("textarea", { style: { minHeight: "170px" } }); steps.value = (t.steps || []).join("\n");
    modal({ title: "City task", focus: true, oncancel: () => res(null), buttons: [{ label: "Cancel", onclick: () => res(null) }, { label: "Save", cls: "pri", onclick: () => {
      const nm = name.value.trim(), tg = tgt.value.trim();
      if (!nm || !tg.includes(":")) { toast("Give the task a name and an 'Open' target like icon:Mail.", "error"); return false; }
      const ev = Math.max(1, Math.round(Number(every.value))), ct = Math.max(1, Math.round(Number(count.value)));
      if (!isFinite(ev) || !isFinite(ct)) { toast("Repeat and How many must be numbers.", "error"); return false; }
      const atv = at.value.split(/[ ,;]+/).filter(Boolean);
      if (atv.some((x) => !/^([01]?\d|2[0-3]):[0-5]\d$/.test(x))) { toast("'Run at' needs times like 09:00 or 21:30, separated by commas.", "error"); return false; }
      res({ name: nm, enabled: on.checked, target: tg, every_min: ev, count: ct, at: atv.join(", "), steps: steps.value.split("\n").map((s) => s.trim()).filter(Boolean) });
    } }],
      body: h("div", { class: "field", style: { gap: "9px", minWidth: "460px" } },
        h("datalist", { id: "targets" }, targets.map((x) => h("option", { value: x }))),
        h("label", {}, "Name"), name, h("label", {}, "Open"), tgt,
        h("div", { class: "hint" }, "icon:Name = right-side button, building:Name = from the city map, xy:100,200 = a spot"),
        h("div", { class: "row" }, h("div", { class: "field" }, h("label", {}, "Repeat every (min)"), every), h("div", { class: "field" }, h("label", {}, "How many buildings"), count),
          h("label", { class: "tgl", style: { marginTop: "18px" } }, on, h("span", { class: "sw" }), "Enabled")),
        h("label", {}, "Run at these times each day (PC clock, optional)"), at,
        h("div", { class: "hint" }, "With times set, the task runs once at each time (within the next hour if the bot is busy) and 'Repeat every' is ignored."),
        h("label", {}, "Steps (one per line)"), steps,
        h("pre", { class: "hint", style: { fontFamily: "var(--mono)", whiteSpace: "pre-wrap" } },
          "tap 270,500            tap a spot\ntap_text Claim|Collect  tap the first of these words found on screen\nrepeat_text Claim       keep tapping while the word is found (max 10)\ntap_img name            tap a template picture from the templates folder\nwait 2                  wait 2 seconds\nswipe 270,700,270,300   swipe\nback                    press Back")) });
  });
}

function iconsDialog() {
  const icons = Object.assign({}, S.meta.icons, S.cfg.city_icons || {});
  const ta = h("textarea", { style: { minHeight: "240px" } });
  ta.value = Object.entries(icons).map(([k, v]) => `${k} = ${v[0]},${v[1]}`).join("\n");
  modal({ title: "Right-side icon positions", buttons: [{ label: "Cancel" }, { label: "Save", cls: "pri", onclick: () => {
    const out = {};
    for (const line of ta.value.split("\n")) {
      if (!line.includes("=")) continue;
      const [k, v] = line.split("=");
      const n = v.replace(/\s/g, "").split(",").slice(0, 2).map(Number);
      if (n.length < 2 || n.some((x) => !isFinite(x))) { toast("Cannot read: " + line, "error"); return false; }
      out[k.trim()] = n.map(Math.round);
    }
    S.cfg.city_icons = out;
  } }], body: h("div", { class: "field" }, h("p", { class: "note" }, "One icon per line: Name = x,y   (screen is 540 wide, 960 high). Use these names in a task as icon:Name. Add your own lines for other buttons."), ta) });
}

// ------------------------------------------------------------------ developer tool: build the general catalogue (Tavern > Portraits)
async function catalogFlow() {
  const serial = await chooseDevice("Record the catalogue with which instance?");
  if (!serial) return;
  const ok = await confirmBox("Build the general catalogue", "Open the Tavern (Recruit General) yourself and leave it there. If the instance is on the city screen instead, the bot opens Tavern > Portraits by itself, but only when this instance already has a city map.\n\nIt then goes through every region tab (Europe, America, Japan, Korea, ...) and every troop-type filter, scrolls each list and records each general's name and picture. It takes several minutes; do not touch the instance.\n\nWhen it is done, copy the 'catalog' folder (button 'Open catalogue folder') next to rally_bot.py and rebuild.", "Start");
  if (!ok) return;
  if (!(await act(api("catalog_crawl", { serial })))) return;
  scanBox = modal({ title: "Recording the catalogue", focus: false, buttons: [{ label: "Stop and save what was found", cls: "pri", onclick: () => { act(api("catalog_stop")); return false; } }],
    body: h("div", { id: "catScanInfo", style: { minWidth: "380px" } }, "Starting...") });
}
// General Manager > Scan Tavern portraits: same crawl, but it only ADDS to the general library (nothing is wiped)
async function tavernPortraitsFlow() {
  const serial = await chooseDevice("Scan the Tavern portraits with which instance?");
  if (!serial) return;
  const ok = await confirmBox("Scan Tavern portraits", "1. Pause that instance (so it does not join rallies meanwhile).\n2. In its game open the Tavern > Recruit General > Portraits page and leave it there.\n\nThe bot then goes through every region tab and troop-type filter and adds every general's picture and name to your general library. Nothing is recruited and Refresh is never pressed. It takes several minutes; do not touch the instance.\n\nThe Portraits page shows every general of the game, the same on every account, so one account is enough.", "Start");
  if (!ok) return;
  if (!(await act(api("catalog_crawl", { serial, merge: true })))) return;
  scanBox = modal({ title: "Scanning Tavern portraits", focus: false, buttons: [{ label: "Stop and save what was found", cls: "pri", onclick: () => { act(api("catalog_stop")); return false; } }],
    body: h("div", { id: "catScanInfo", style: { minWidth: "380px" } }, "Starting...") });
}
async function catalogDone(d) {
  closeScanBox();
  if (d.added !== undefined && d.added !== null && !d.error) {
    const j = await act(api("state"));
    if (j) { S.generals = j.generals; }
    return infoBox("Tavern portraits", `Read ${d.n} generals - ${d.added} new picture(s) added to your general library.`);
  }
  if (d.error) return infoBox("Catalogue", "The crawl stopped: " + d.error + "\n\nWhat was read before that is not saved. Send me the log if you need help.");
  const j = await act(api("state"));
  if (j) { S.generals = j.generals; }
  infoBox("Catalogue saved", `Recorded ${d.n} generals.\n\nSaved in:\n${d.path}\n\nThey are already in your general lists now. To ship them, copy that 'catalog' folder next to rally_bot.py and rebuild.`);
}

// ------------------------------------------------------------------ developer tool: record the city frames
async function cityCaptureFlow() {
  const serial = await chooseDevice("Record the city frames with which instance?");
  if (!serial) return;
  const ok = await confirmBox("Record the city frames", "Leave that instance on the CITY screen and do not touch it. The bot drags the city around and saves screenshots of every part (also while dragging, when the building names show).\n\nIt takes several minutes. Afterwards use 'Open city capture folder' and send me the folder.", "Start");
  if (!ok) return;
  if (!(await act(api("city_capture", { serial })))) return;
  scanBox = modal({ title: "Recording the city", focus: false, buttons: [{ label: "Stop and save what was found", cls: "pri", onclick: () => { act(api("scan_city_stop")); return false; } }],
    body: h("div", { style: { minWidth: "380px" } }, "The bot is dragging the city around... Progress is in the log on the Dashboard.") });
}


// ------------------------------------------------------------------ developer tool: record a flow played by hand
async function flowRecordFlow() {
  const serial = await chooseDevice("Record a flow on which instance?");
  if (!serial) return;
  const nameBox = h("input", { type: "text", placeholder: "e.g. Alliance treasure", style: { width: "100%" } });
  const ok = await confirmBox("Record a flow", h("div", {}, h("p", {}, "Name the flow, press Start, then play it in the game by hand. Every tap, long press and swipe is saved with a small picture before and after it. The instance must be stopped."), nameBox), "Start");
  if (!ok) return;
  if (!(await act(api("flow_start", { serial, name: nameBox.value || "flow" })))) return;
  const info = h("div", { style: { minWidth: "360px" } }, "Recording... play the flow in the game now.");
  const timer = setInterval(async () => {
    const st = await api("flow_state", {}, { quiet: true });
    if (!st || !st.ok) return;
    const l = st.last;
    info.textContent = `Recording... ${st.steps} step(s)` + (l ? `  -  last: ${l.kind} at ${l.x},${l.y}` : "") + (st.error ? `  (${st.error})` : "");
  }, 1000);
  modal({ title: "Recording a flow", focus: false, body: info, buttons: [{ label: "Stop and save", cls: "pri", onclick: async () => {
    clearInterval(timer);
    const d = await act(api("flow_stop", {}));
    if (d) infoBox("Recording saved", `${d.steps} step(s) saved in:\n${d.path}\n\nRecord the next task, or press 'Save recordings as zip' (Control settings > Setup > Settings) and send the zip to the developer.`);
  } }] });
}

// ---- What's new: shown once after an update, and from Updates > What's new
function vtuple(v) { return String(v || "0").split(".").map((x) => parseInt(x, 10) || 0); }
function vnewer(a, b) { const x = vtuple(a), y = vtuple(b); for (let i = 0; i < 4; i++) { const p = x[i] || 0, q = y[i] || 0; if (p !== q) return p > q; } return false; }
function whatsNewBody(list) {
  const box = h("div", { style: { maxWidth: "520px", maxHeight: "60vh", overflow: "auto" } });
  list.forEach((e) => {
    box.appendChild(h("h3", { style: { margin: "14px 0 6px" } }, "Version " + e.v + (e.date ? "  \u00b7  " + e.date : "")));
    if ((e.new || []).length) { box.appendChild(h("b", {}, "New")); box.appendChild(h("ul", { style: { margin: "4px 0 8px 18px" } }, e.new.map((t) => h("li", {}, t)))); }
    if ((e.fixed || []).length) { box.appendChild(h("b", {}, "Fixed")); box.appendChild(h("ul", { style: { margin: "4px 0 8px 18px" } }, e.fixed.map((t) => h("li", {}, t)))); }
  });
  return box;
}
function showWhatsNew(all, seenOverride) {
  const log = (window.CHANGELOG || []).slice();
  if (!log.length) return;
  const seen = seenOverride !== undefined ? seenOverride : ((S.cfg || {}).last_seen_version || "");
  const list = all ? log.slice(0, 8) : (seen ? log.filter((e) => vnewer(e.v, seen) && !vnewer(e.v, S.version)).slice(0, 5) : log.slice(0, 1));
  if (!list.length) return;
  modal({ title: "What's new in RallyForge Bot", body: whatsNewBody(list), wide: true, buttons: [{ label: "OK", cls: "pri" }] });
}
// No pop-up after an update: the "What's new" button in the top bar gets a dot, and the list opens only when it is clicked.
function paintNewDot() {
  const b = document.getElementById("newBtn");
  if (b) b.classList.toggle("hasnew", !!S.unseenNew);
}
function openWhatsNew() {
  const seen = (S.cfg || {}).last_seen_version || "";
  if (S.unseenNew) showWhatsNew(false, seen); else showWhatsNew(true);
  if (S.unseenNew) {
    S.unseenNew = false; paintNewDot();
    S.cfg.last_seen_version = S.version;
    api("save", { cfg: { last_seen_version: S.version } }).catch(() => {});
  }
}
function checkWhatsNew() {
  try {
    const seen = (S.cfg || {}).last_seen_version || "";
    if (seen === S.version) { S.unseenNew = false; paintNewDot(); return; }
    if (S.firstRun) {                                   // a fresh install has nothing "new" to show
      S.cfg.last_seen_version = S.version;
      api("save", { cfg: { last_seen_version: S.version } }).catch(() => {});
      return;
    }
    S.unseenNew = true; paintNewDot();
  } catch (e) {}
}


// ---- login screen (licensed edition): cannot be closed until the account is accepted
function showLogin(msg) {
  if (document.getElementById("loginOv")) return;
  const id = h("input", { type: "text", placeholder: "ID", autocomplete: "username", style: { width: "100%" } });
  const pw = h("input", { type: "password", placeholder: "Password", autocomplete: "current-password", style: { width: "100%" } });
  const err = h("div", { style: { color: "var(--bad)", minHeight: "20px", marginTop: "8px", fontWeight: "600" } }, msg && msg !== "Please log in." ? msg : "");
  const go = async () => {
    err.textContent = "Checking...";
    try {
      const j = await api("login", { id: id.value, pw: pw.value }, { quiet: true });
      S.license = j.license;
      ov.remove();
      updateSummary();
      renderTabs();
    } catch (e) { err.textContent = e.message || "Could not log in."; }
  };
  const btn = h("button", { class: "btn pri", style: { width: "100%", marginTop: "14px" }, onclick: go }, "Log in");
  [id, pw].forEach((x) => x.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); }));
  const ov = h("div", { class: "ov", id: "loginOv", style: { zIndex: 500 } },
    h("div", { class: "md", style: { maxWidth: "380px" } },
      h("div", { class: "mh" }, "Log in to RallyForge Bot"),
      h("div", { class: "mb" },
        h("p", { class: "note", style: { marginTop: 0 } }, "Enter the ID and password you received. The app needs the internet to check your account."),
        id, h("div", { style: { height: "8px" } }), pw, err, btn)));
  document.body.appendChild(ov);
  setTimeout(() => id.focus(), 50);
}
