// First-run walkthrough: a dimmed screen with the current control lit up and a short note beside it.
// It starts by itself only on a fresh install (no instances yet) and never again once it is finished or skipped.
// "Take the tour" under Setup > Settings starts it again by hand.
const TOUR_STEPS = [
  { title: "Welcome to RallyForge", text: "A two-minute tour of where to click to get the bot joining rallies. You can skip it at any time and open it again later from More ▾ in the top bar." },
  { sel: "#instList", title: "1. Your instances", text: "Every emulator you run Evony in is an “instance” and shows up in this list. Click one to open its own page; its dot shows if it is running. Hover a name to get Start / Pause / Stop for just that one.", side: "right" },
  { tab: "dashboard", sel: "#pages", title: "2. Add your emulators", text: "On the Dashboard press “Update instance list” (or Detect) so the bot finds your MuMu / BlueStacks windows, then add the ones you want and give each a name. Evony must be open and logged in inside that emulator, screen 540×960.", side: "top" },
  { tab: "presets", sel: "#pages", title: "3. Set up your presets", text: "First save your march presets (I–VIII) inside Evony: troops and a general in each. Then tap the numbers here to arm the ones you saved (1–6 is typical). “Game default” marches with what the preset already holds; “Listed” lets the bot pick the generals you name. “Rotate” uses the armed presets in turn.", side: "top" },
  { sel: "#ribbon .grp:nth-child(2)", title: "4. Teach it your generals", text: "Open Tools > Generals to pick which generals the bot may use as main and assistant, best first. Do this once; every instance can use the same list or its own.", side: "bottom" },
  { tab: "monsters", sel: "#pages", title: "5. Choose what to join", text: "Tick the monsters you want the bot to join and untick the ones to skip. Open a monster to choose single levels. Anything unticked is ignored, so start with the ones you want most.", side: "top" },
  { tab: "duties", sel: "#pages", title: "6. Daily duties (optional)", text: "Duties are the extras: greeting champions, claiming gifts and so on. This page holds the defaults for new instances; each instance switches its own under Routines. Building duties need the city mapped once: Tools › Scan city.", side: "top" },
  { sel: "#ribbon .grp:first-child", title: "7. Start it", text: "Select an instance on the left, then press Start. Open “All instances” (bottom left) first and Start runs every instance at once. Pause holds the bot, Stop ends it. Watch the Activity and Log tabs to see what it is doing.", side: "bottom" },
  { title: "You are ready", text: "That is all. Settings, Reliability and Telegram alerts are under Setup and App when you want them. This tour will not show again; Setup › Settings › Take the tour opens it whenever you need it." },
];
const TOUR = { i: 0, on: false };

function tourDone() { try { localStorage.setItem("rf_tour_done", "1"); } catch (e) {} if (S.cfg) { S.cfg.tour_done = true; api("save", { cfg: { tour_done: true } }, { quiet: true }).catch(() => {}); } }
function tourSeen() { try { if (localStorage.getItem("rf_tour_done")) return true; } catch (e) {} return !!(S.cfg && S.cfg.tour_done); }
// Everybody (new installs and existing ones) gets one small question the first time: "would you like a quick walkthrough?".
// Yes starts it, No (or the cross) just closes it. Either way it is never asked again.
function maybeStartTour() { if (!tourSeen()) tourAsk(); }
function tourAsk() {
  if (document.getElementById("tourAsk")) return;
  const close = () => { const e = document.getElementById("tourAsk"); if (e) e.remove(); };
  document.body.append(h("div", { id: "tourAsk", class: "tour-ask", role: "dialog" },
    h("div", { class: "tour-t" }, "Would you like a quick walkthrough?"),
    h("div", { class: "tour-p" }, "Two minutes: where to click, how to set up your presets and start the bot."),
    h("div", { class: "tour-b", style: { marginTop: "12px" } },
      h("button", { class: "btn sm pri", type: "button", onclick: () => { close(); startTour(); } }, "Yes, show me"),
      h("button", { class: "btn sm", type: "button", onclick: () => { close(); tourDone(); } }, "No thanks"))));
}

function startTour() {
  if (TOUR.on) return;
  TOUR.on = true; TOUR.i = 0;
  S.sel = "control";
  renderSide(); renderTabs(); renderPage();
  document.body.append(h("div", { id: "tourShade", class: "tour-shade" }), h("div", { id: "tourHole", class: "tour-hole" }), h("div", { id: "tourBox", class: "tour-box", role: "dialog" }));
  window.addEventListener("resize", tourPlace);
  document.addEventListener("keydown", tourKey, true);
  tourShow();
}
function tourEnd() {
  TOUR.on = false;
  ["tourShade", "tourHole", "tourBox"].forEach((id) => { const e = document.getElementById(id); if (e) e.remove(); });
  window.removeEventListener("resize", tourPlace);
  document.removeEventListener("keydown", tourKey, true);
  tourDone();
}
function tourKey(e) { if (e.key === "Escape") { e.stopPropagation(); tourEnd(); } else if (e.key === "ArrowRight") tourGo(1); else if (e.key === "ArrowLeft") tourGo(-1); }
function tourGo(d) { const n = TOUR.i + d; if (n < 0) return; if (n >= TOUR_STEPS.length) return tourEnd(); TOUR.i = n; tourShow(); }

function tourShow() {
  const s = TOUR_STEPS[TOUR.i];
  if (s.tab && S.tab !== s.tab) { S.sel = "control"; pickTab(s.tab); }
  const box = document.getElementById("tourBox");
  if (!box) return;
  const last = TOUR.i === TOUR_STEPS.length - 1;
  box.innerHTML = "";
  box.append(
    h("div", { class: "tour-t" }, s.title),
    h("div", { class: "tour-p" }, s.text),
    h("div", { class: "tour-dots" }, ...TOUR_STEPS.map((_, k) => h("i", { class: k === TOUR.i ? "on" : "" }))),
    h("div", { class: "tour-b" },
      last ? null : h("button", { class: "btn sm", onclick: tourEnd }, "Skip tour"),
      h("span", { style: { flex: "1" } }),
      TOUR.i > 0 ? h("button", { class: "btn sm", onclick: () => tourGo(-1) }, "Back") : null,
      h("button", { class: "btn sm pri", onclick: () => tourGo(1) }, last ? "Finish" : (TOUR.i === 0 ? "Start the tour" : "Next"))));
  setTimeout(tourPlace, 60);                      // let the tab draw first
}

function tourPlace() {
  if (!TOUR.on) return;
  const s = TOUR_STEPS[TOUR.i], box = document.getElementById("tourBox"), hole = document.getElementById("tourHole"), shade = document.getElementById("tourShade");
  if (!box) return;
  const el = s.sel ? document.querySelector(s.sel) : null;
  const vw = innerWidth, vh = innerHeight, bw = Math.min(360, vw - 24);
  box.style.width = bw + "px";
  if (!el) {                                      // no target: centred card on a plain dim screen
    hole.style.display = "none"; shade.style.display = "block";
    box.style.left = Math.max(12, (vw - bw) / 2) + "px"; box.style.top = Math.max(12, (vh - box.offsetHeight) / 2.4) + "px";
    return;
  }
  shade.style.display = "none"; hole.style.display = "block";
  const r = el.getBoundingClientRect(), pad = 6;
  const hr = { x: Math.max(0, r.left - pad), y: Math.max(0, r.top - pad), w: Math.min(vw, r.width + pad * 2), h: Math.min(vh, r.height + pad * 2) };
  Object.assign(hole.style, { left: hr.x + "px", top: hr.y + "px", width: hr.w + "px", height: hr.h + "px" });
  const bh = box.offsetHeight;
  let x, y;
  if (s.side === "right" && hr.x + hr.w + bw + 20 < vw) { x = hr.x + hr.w + 14; y = hr.y + 10; }
  else if (s.side === "bottom" && hr.y + hr.h + bh + 20 < vh) { x = hr.x; y = hr.y + hr.h + 14; }
  else if (hr.h > vh * 0.5) { x = vw - bw - 24; y = vh - bh - 24; }            // a big target (a whole page): card in the lower right corner over it
  else if (hr.y + hr.h + bh + 20 < vh) { x = hr.x; y = hr.y + hr.h + 14; }
  else { x = hr.x; y = Math.max(12, hr.y - bh - 14); }
  box.style.left = Math.max(12, Math.min(x, vw - bw - 12)) + "px";
  box.style.top = Math.max(12, Math.min(y, vh - bh - 12)) + "px";
}
