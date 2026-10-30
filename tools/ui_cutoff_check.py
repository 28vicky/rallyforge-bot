# Publish gate (owner 30 Oct 23:28, START_HERE rule 26): screenshots at 900/1366/1920 + cut-off check of a side panel.
# Run the app with web_app.py --no-window (fake instance in userdata/config.json), then:
#   python3 tools/ui_cutoff_check.py "<url>" <out_dir>   -> prints {width-mode: [problems]}; any problem = NOT ready.
# This version opens Troop training; copy the open-the-panel lines for other screens.
import sys, json
from playwright.sync_api import sync_playwright
url, out = sys.argv[1], sys.argv[2]
CHECK = """() => {
  const p = document.querySelector('.sidep'); if (!p) return ['no panel'];
  const bad = [];
  const pr = p.getBoundingClientRect();
  p.querySelectorAll('*').forEach(e => {
    const r = e.getBoundingClientRect(); if (!r.width || getComputedStyle(e).visibility==='hidden') return;
    if (r.right > pr.right + 1 || r.left < pr.left - 1) bad.push('outside: ' + (e.textContent||e.tagName).trim().slice(0,40));
    if (e.scrollWidth > e.clientWidth + 1 && ['auto','scroll','hidden'].includes(getComputedStyle(e).overflowX)) bad.push('overflow: ' + e.className);
  });
  return [...new Set(bad)];
}"""
res = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    for w in (900, 1366, 1920):
        pg = b.new_page(viewport={"width": w, "height": 900 if w < 1920 else 1080})
        pg.goto(url); pg.wait_for_timeout(2500)
        pg.get_by_text("Kiravik", exact=True).first.click(); pg.wait_for_timeout(800)
        pg.locator("button.tab:visible", has_text="Duties").first.click(); pg.wait_for_timeout(1200)
        row = pg.locator('.dset[data-id="train_troops"]')
        if not row.count():
            pg.get_by_text("All (", exact=False).first.click(); pg.wait_for_timeout(800)
        pg.locator('.dset[data-id="train_troops"]').first.click(); pg.wait_for_timeout(1000)
        for mode in ("Once a day", "Every…", "Until a goal", "Troops"):
            if mode != "Once a day":
                pg.locator(".sidep .segb", has_text=mode).first.click(); pg.wait_for_timeout(1200)
            res[f"{w}-{mode}"] = pg.evaluate(CHECK)
            pg.screenshot(path=f"{out}/train_{w}_{mode.replace(' ','_').replace('…','')}.png")
        pg.locator(".sidep .segb", has_text="Once a day").first.click(); pg.wait_for_timeout(800)
        pg.close()
    b.close()
print(json.dumps(res, indent=1))
