const $ = (id) => document.getElementById(id);
const ago = (t) => { const s = Math.round((Date.now() - t) / 1000); return s < 60 ? s + " s ago" : Math.round(s / 60) + " min ago"; };
const paint = (st) => {
  $("dot").className = "dot" + (st ? (st.ok ? " ok" : " bad") : "");
  $("st").textContent = st ? st.text + " · " + ago(st.t) : "Not linked yet";
};
const minRow = () => { $("minrow").style.display = $("mode").value === "off" ? "none" : "flex"; };
chrome.storage.local.get({ code: "", reloadMode: "smart", reloadMin: 3, status: null }, (o) => {
  $("code").value = o.code; $("mode").value = o.reloadMode; $("min").value = o.reloadMin; paint(o.status); minRow();
});
$("mode").onchange = minRow;
$("save").onclick = () => chrome.storage.local.set({ code: $("code").value.trim().toUpperCase(), reloadMode: $("mode").value,
  reloadMin: Math.max(1, Math.min(60, Number($("min").value) || 3)) }, () => paint({ ok: true, text: "Saved", t: Date.now() }));
$("test").onclick = () => chrome.runtime.sendMessage({ type: "test", code: $("code").value.trim().toUpperCase() }, (r) =>
  paint(r && r.ok ? { ok: true, text: "Linked to RallyForge", t: Date.now() }
    : { ok: false, text: r && r.code === 403 ? "Wrong link code" : "RallyForge is not running on this PC", t: Date.now() }));
chrome.storage.onChanged.addListener((ch) => { if (ch.status) paint(ch.status.newValue); });
