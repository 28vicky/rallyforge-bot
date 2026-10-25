"""
Publish a new version of the bot.

  1. Put the new rally_bot.py, web_app.py and the ui folder in this folder (next to this file).
  2. Run:  python make_update.py
     (optional)  python make_update.py --notes "What is new" --key MY_SECRET --templates new_pictures_folder
  3. Upload rally_bot.py, web_app.py, the ui folder and the new version.json to the web address you typed into the app
     ("Update address" = the link to version.json, e.g. https://raw.githubusercontent.com/you/repo/main/version.json).

With --key (or the UPDATE_KEY environment variable) the version.json is signed. Type the same key into the
"Update key" box of the app and it will only accept updates signed with it.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent


def canonical(manifest):
    return json.dumps({k: v for k, v in manifest.items() if k != "sig"}, sort_keys=True, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="")
    ap.add_argument("--key", default=os.environ.get("UPDATE_KEY", ""))
    ap.add_argument("--templates", default="", help="folder with .png pictures to publish too")
    ap.add_argument("--monsters", default="", help="folder with monster .png pictures to publish too")
    ap.add_argument("--extra", nargs="*", default=[], help="more .py files that belong to the update")
    ap.add_argument("--auto", action="store_true", help="let apps whose owner chose 'automatic updates' install this one by themselves (default: they only show 'Update available' in the status bar)")
    ap.add_argument("--cultures", default="", help="the culture city maps folder (cultures/<culture>/map.json, feats.npz, kf_NN.jpg, map.jpg) to publish too, e.g. cultures")
    ap.add_argument("--catalog", default="", help="the general catalogue folder (catalog.json + art) to publish too, e.g. %%APPDATA%%\\RallyForgeBot\\catalog")
    a = ap.parse_args()

    src = (HERE / "rally_bot.py").read_text(encoding="utf-8")
    m = re.search(r'(?m)^VERSION\s*=\s*"([^"]+)"', src)
    if not m:
        sys.exit("rally_bot.py has no VERSION = \"x.y.z\" line.")
    files = {}
    for name in ["rally_bot.py", "web_app.py", "rf_license.py", *sorted(f"ui/{x.name}" for x in (HERE / "ui").glob("*") if x.suffix in (".js", ".css", ".html")), *a.extra]:
        p = HERE / name
        if not p.exists():
            sys.exit(f"{name} is missing from this folder.")
        if name.endswith(".py"): compile(p.read_bytes(), name, "exec")                      # refuse to publish a file with a syntax error
        files[name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    pics = {}
    if a.templates:
        for p in sorted(Path(a.templates).glob("*.png")):
            pics[p.name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    mons = {}
    if a.monsters:
        from urllib.parse import quote
        for p in sorted(Path(a.monsters).glob("*.png")):
            mons[p.name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "url": "monsters/" + quote(p.name)}
    cat = {}
    if a.catalog:
        from urllib.parse import quote
        root = Path(a.catalog)
        if (root / "catalog.json").exists():
            cat["catalog.json"] = {"sha256": hashlib.sha256((root / "catalog.json").read_bytes()).hexdigest(), "url": "catalog/catalog.json"}
        for p in sorted((root / "art").glob("*.png")):
            cat["art/" + p.name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "url": "catalog/art/" + quote(p.name)}
    cul = {}
    if a.cultures:
        from urllib.parse import quote
        for p in sorted(Path(a.cultures).glob("*/*")):
            if p.suffix in (".json", ".npz", ".jpg"):
                rel = f"{p.parent.name}/{p.name}"
                cul[rel] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "url": "cultures/" + quote(rel)}
    manifest = {"version": m.group(1), "notes": a.notes, "files": files, "mode": "auto" if a.auto else "ask"}
    if cul:
        manifest["cultures"] = cul
    if cat:
        try:                                               # catalog_replaces.json: {"art/X.png": [sha256 of the outdated published picture]}
            for k, v in json.loads((HERE / "catalog_replaces.json").read_text(encoding="utf-8")).items():
                if k in cat:
                    cat[k]["replaces"] = list(v)
        except FileNotFoundError:
            pass
        manifest["catalog"] = cat
    if mons:
        manifest["monsters"] = mons
    if pics:
        manifest["templates"] = pics
    if a.key:
        manifest["sig"] = hmac.new(a.key.encode("utf-8"), canonical(manifest).encode("utf-8"), hashlib.sha256).hexdigest()
    (HERE / "version.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"version.json written for version {m.group(1)}" + (" (signed)" if a.key else " (NOT signed)")
          + (" - may install by itself where the user chose automatic updates" if a.auto else " - shown as 'Update available' in the status bar"))
    print("Upload:", ", ".join(list(files) + ["version.json"] + list(pics)))
    if cat:
        print(f"Also upload the catalogue folder as catalog/ (catalog.json + art/, {len(cat)} files) next to version.json.")
    if cul:
        print(f"Also upload the culture maps folder as cultures/ ({len(cul)} files) next to version.json.")


if __name__ == "__main__":
    main()
