"""Owner tool: pack the culture city maps (one 'Map my city' backup per culture) into cultures/<culture>/ for shipping.

    python make_cultures.py <backup_root>
        <backup_root> holds folders _culture_<culture>_<anything> (kf_*.png + map.json from 'Map my city'),
        e.g. %APPDATA%\\RallyForgeBot\\city_learn

Writes per culture: map.json (places, half=true, feats), feats.npz (ORB points + descriptors computed on the FULL-size
pictures, exactly as CityMap.locate computes them on a screenshot), kf_NN.jpg (grey, half size - only for the pixel check)
and map.jpg (half-size stitched picture for the City map card / pinning). Pins (pins.json) are added later on the card.
"""
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
CULTURES = ("europe", "russia", "china", "korea", "japan", "arabia", "america")
ORB_N = 2500            # same as rally_bot._kf_feats
JPG_Q = 80
KEEP = 1200           # strongest feature points kept per view (the views overlap ~half, so the map still has plenty)


def pack(src, culture, out):
    meta = json.loads((src / "map.json").read_text(encoding="utf-8"))
    kfs = meta.get("keyframes") or []
    orb = cv2.ORB_create(ORB_N)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("kf_*.jpg"):
        old.unlink()
    pts, desc, lab, keep = [], [], [], []
    vw = vh = None
    for v in kfs:
        g = cv2.imread(str(src / v["file"]), cv2.IMREAD_GRAYSCALE)
        if g is None:
            continue
        vh, vw = g.shape
        k, d = orb.detectAndCompute(g, None)
        if d is None:
            continue
        if len(k) > KEEP:
            top = sorted(range(len(k)), key=lambda i: -k[i].response)[:KEEP]
            k, d = [k[i] for i in top], d[top]
        n = len(keep)
        pts.append(np.float32([q.pt for q in k]))
        desc.append(d)
        lab.append(np.full(len(k), n, np.uint16))
        name = f"kf_{n + 1:02d}.jpg"
        half = cv2.resize(g, (vw // 2, vh // 2), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(out / name), half, [cv2.IMWRITE_JPEG_QUALITY, JPG_Q])
        keep.append({"file": name, "x": round(float(v["x"]), 2), "y": round(float(v["y"]), 2)})
    if len(keep) < 2:
        raise SystemExit(f"{culture}: fewer than 2 usable keyframes in {src}")
    np.savez_compressed(str(out / "feats.npz"), pts=np.vstack(pts), desc=np.vstack(desc), label=np.concatenate(lab))
    W = int(max(k["x"] for k in keep)) + vw + 1
    H = int(max(k["y"] for k in keep)) + vh + 1
    canvas = np.zeros((H // 2 + 1, W // 2 + 1), np.uint8)
    for k in keep:
        im = cv2.imread(str(out / k["file"]), cv2.IMREAD_GRAYSCALE)
        X, Y = int(round(k["x"] / 2)), int(round(k["y"] / 2))
        canvas[Y:Y + im.shape[0], X:X + im.shape[1]] = im
    col = cv2.imread(str(src / "map.png"))                 # the backup's own colour picture (same places) for the card
    if col is not None and abs(col.shape[1] - W) <= 2 and abs(col.shape[0] - H) <= 2:
        canvas = cv2.resize(col, (canvas.shape[1], canvas.shape[0]), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out / "map.jpg"), canvas, [cv2.IMWRITE_JPEG_QUALITY, JPG_Q])
    m = {"culture": culture, "from": src.name, "at": meta.get("at"), "views": len(keep), "size": [W, H],
         "view": [vw, vh], "half": True, "feats": "feats.npz", "picture": "map.jpg", "picture_scale": 0.5,
         "orb": ORB_N, "keep": KEEP, "cv2": cv2.__version__, "keyframes": keep}
    (out / "map.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    size = sum(p.stat().st_size for p in out.iterdir())
    print(f"{culture}: {len(keep)} views, {len(np.vstack(desc))} features, {size / 1e6:.1f} MB  (from {src.name})")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    root = Path(sys.argv[1])
    found = {}
    for d in sorted(root.glob("_culture_*")):
        m = re.match(r"_culture_([a-z]+)", d.name)
        if m and m.group(1) in CULTURES and (d / "map.json").exists():
            found[m.group(1)] = d
    for c in CULTURES:
        if c in found:
            pack(found[c], c, HERE / "cultures" / c)
        else:
            print(f"{c}: no backup folder _culture_{c}_* in {root}")


if __name__ == "__main__":
    main()
