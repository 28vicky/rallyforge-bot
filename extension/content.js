// RallyForge iScout Link - reads the monster table on the iScout page (only what this browser already shows).
(() => {
  const DEF = { reloadMode: "smart", reloadMin: 3 };
  let opts = { ...DEF }, lastSig = "", lastChange = Date.now(), lastSent = 0;

  const readRows = () => {
    const out = [];
    document.querySelectorAll("tbody tr").forEach((tr) => {
      const b = tr.querySelector('button[aria-label^="Copy "]');
      const txt = tr.textContent.replace(/\s+/g, " ");
      const xy = txt.match(/S:\s*(\d+)\s*X:\s*(\d+)\s*Y:\s*(\d+)/);
      if (!b || !xy) return;
      const lv = txt.match(/Lv\s*(\d+)/);
      out.push({ name: b.getAttribute("aria-label").slice(5).trim(), level: lv ? lv[1] : "",
        server: xy[1], x: xy[2], y: xy[3] });
    });
    return out;
  };

  const send = (items) => {
    lastSent = Date.now();
    try { chrome.runtime.sendMessage({ type: "list", items }); } catch (e) { /* extension reloaded: next page load reconnects */ }
  };

  const check = () => {
    const items = readRows();
    const sig = items.map((i) => i.server + ":" + i.x + ":" + i.y + ":" + i.name + i.level).join("|");
    if (sig !== lastSig) { lastSig = sig; lastChange = Date.now(); send(items); }
    else if (Date.now() - lastSent > 30000) send(items);          // still listed = still alive (keeps RallyForge's list fresh)
    const mins = Math.max(1, Number(opts.reloadMin) || 3) * 60000;
    if (items.length || document.querySelector("tbody")) {
      if (opts.reloadMode === "smart" && Date.now() - lastChange > mins) location.reload();
      if (opts.reloadMode === "every" && Date.now() - performance.timeOrigin > mins) location.reload();
    }
  };

  chrome.storage.local.get(DEF, (o) => { opts = { ...DEF, ...o }; });
  chrome.storage.onChanged.addListener((ch) => { for (const k in ch) opts[k] = ch[k].newValue; });
  let t = null;
  new MutationObserver(() => { clearTimeout(t); t = setTimeout(check, 800); }).observe(document.body, { childList: true, subtree: true });
  setInterval(check, 10000);
  setTimeout(check, 2000);
})();
