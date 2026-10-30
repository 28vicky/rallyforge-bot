import threading, types
import rally_bot as rb


class Dev:
    serial = "x"
    def __init__(self): self.taps, self.swipes = [], []
    def screenshot(self): return None
    def tap(self, x, y): self.taps.append((x, y))
    def swipe(self, *a): self.swipes.append(a)


def _setup(mp, hands, pin=(100, 200), reach=True):
    logs = []
    seq = iter(hands)
    mp.setattr(rb, "ensure_city_zoom", lambda *a: None)
    mp.setattr(rb, "_settled_shot", lambda d, s: None)
    mp.setattr(rb, "_nap", lambda *a: None)
    mp.setattr(rb, "rd_snap", lambda *a: None)
    mp.setattr(rb, "find_warehouse_hand", lambda shot, t=0.75: next(seq, None))
    mp.setattr(rb, "_pin_of", lambda d, b: pin)
    mp.setattr(rb, "culture_pin", lambda d, b: None)
    mp.setattr(rb, "city_map", lambda d: object() if pin else None)
    mp.setattr(rb, "CityNav", lambda *a, **k: types.SimpleNamespace())
    mp.setattr(rb, "goto_on_map", lambda *a, **k: (270, 480) if reach else None)
    return logs


def test_goes_to_warehouse_pin_and_taps_hand(monkeypatch):
    logs = _setup(monkeypatch, [None, (250, 400, 0.9), None])
    d = Dev()
    rb.run_collect_resources(d, {}, logs.append, threading.Event())
    assert d.taps == [(250, 400)] and not d.swipes
    assert "by the city map" in logs[-1]


def test_no_hand_at_pin_means_nothing_ready_no_roaming(monkeypatch):
    logs = _setup(monkeypatch, [None, None])
    d = Dev()
    rb.run_collect_resources(d, {}, logs.append, threading.Event())
    assert not d.taps and not d.swipes
    assert "at the Warehouse, no hand" in logs[-1]


def test_no_pin_falls_back_to_looking_around(monkeypatch):
    logs = _setup(monkeypatch, [None] * 20, pin=None)
    d = Dev()
    rb.run_collect_resources(d, {}, logs.append, threading.Event())
    assert len(d.swipes) == len(rb.HAND_SWEEP)
    assert "no Warehouse pin" in logs[0]
