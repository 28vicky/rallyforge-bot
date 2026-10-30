"""Owner 30 Oct (Rob/K misses 12:38-12:45): instances of one culture share one map object - many threads locating
at the same moment must each get the same answer as one thread alone."""
import threading
from pathlib import Path

import cv2
import numpy as np

import rally_bot as rb

ROOT = Path(__file__).resolve().parents[1]


def _screens(m, n=6):
    x, y, w, h = rb.CITY_VIEW
    out = []
    for i in range(0, len(m.kf), max(1, len(m.kf) // n))[:n]:
        g = m.kf[i][2]
        shot = np.zeros((960, 540, 3), np.uint8)
        shot[y:y + h, x:x + w] = cv2.cvtColor(cv2.resize(g, (w, h)), cv2.COLOR_GRAY2BGR)
        out.append(shot)
    return out


def test_parallel_locate_matches_single():
    m = rb.CityMap(ROOT / "cultures" / "europe" / "map.png")
    assert m.ok() and m.index is not None
    shots = _screens(m)
    alone = [m.locate(s) for s in shots]
    assert sum(r is not None for r in alone) >= len(shots) - 1
    got, errs = {}, []

    def run(k):
        try:
            for _ in range(3):
                for i, s in enumerate(shots):
                    got.setdefault((k, i), []).append(m.locate(s))
        except Exception as e:                                 # pragma: no cover
            errs.append(e)
    th = [threading.Thread(target=run, args=(k,)) for k in range(6)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert not errs
    for (k, i), rs in got.items():
        for r in rs:
            if alone[i] is None:
                continue
            assert r is not None, f"thread {k} missed screen {i}"
            assert abs(r[0] - alone[i][0]) < 3 and abs(r[1] - alone[i][1]) < 3
    assert getattr(m._tl, "err", "") == ""
