// RallyForge iScout Link - passes the list to RallyForge on this PC (127.0.0.1 only, never the internet).
const URL_RF = "http://127.0.0.1:47321";

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "list") {
    chrome.storage.local.get({ code: "" }, async ({ code }) => {
      if (!code) return chrome.storage.local.set({ status: { ok: false, text: "Paste the link code from RallyForge", t: Date.now() } });
      try {
        const r = await fetch(URL_RF + "/iscout", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code, items: msg.items }) });
        const j = await r.json().catch(() => ({}));
        chrome.storage.local.set({ status: r.ok
          ? { ok: true, text: "Sent " + (j.n ?? msg.items.length) + " monsters", t: Date.now() }
          : { ok: false, text: r.status === 403 ? "Wrong link code" : "RallyForge said no (" + r.status + ")", t: Date.now() } });
      } catch (e) {
        chrome.storage.local.set({ status: { ok: false, text: "RallyForge is not running on this PC", t: Date.now() } });
      }
    });
  }
  if (msg.type === "test") {
    fetch(URL_RF + "/ping?code=" + encodeURIComponent(msg.code)).then((r) => reply({ ok: r.ok, code: r.status }))
      .catch(() => reply({ ok: false, code: 0 }));
    return true;
  }
});
