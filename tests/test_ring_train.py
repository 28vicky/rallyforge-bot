"""Owner 30 Oct 14:33-14:37: ring buttons by picture (never by place / text) + Train troops tier steps."""
import threading
from pathlib import Path

import cv2
import pytest

import rally_bot as rb

S = Path(__file__).resolve().parent / "screens"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _repo_templates(monkeypatch):
    monkeypatch.setattr(rb, "template_path", lambda n: (ROOT / f"{n}.png") if (ROOT / f"{n}.png").exists() else None)
    rb._cache.clear()


@pytest.mark.parametrize("screen,item,xy", [
    ("tavern_ring_stalds.png", "wheel", (136, 314)),
    ("tavern_ring_stalds.png", "gchest", (367, 271)),
    ("dome_ring_kiravik.png", "rare_books", (195, 313)),
    ("barracks_ring_phone.png", "train", (132, 442)),
    ("barracks_ring_training_phone.png", "train", (210, 365)),     # training: the same swords are "View", ring changed
])
def test_ring_item_found_by_picture(screen, item, xy):
    r = rb.ring_icon(cv2.imread(str(S / screen)), item)
    assert r and abs(r[0] - xy[0]) < 15 and abs(r[1] - xy[1]) < 15


@pytest.mark.parametrize("screen,item", [("tavern_ring_stalds.png", "train"), ("dome_ring_kiravik.png", "wheel"),
                                         ("barracks_ring_training_phone.png", "gchest"), ("wheel_page_manhattan.png", "rare_books")])
def test_ring_item_not_found_where_it_is_not(screen, item):
    assert rb.ring_icon(cv2.imread(str(S / screen)), item) is None


def test_train_page_selected_troop():
    assert abs(rb.train_selected_x(cv2.imread(str(S / "train_page_stalds.png"))) - 280) <= 10


class Row:
    """A fake tier row: circles 118 px apart, the game re-centres the chosen one when it can (recentre=True)."""
    def __init__(self, recentre, locked_from=99):
        self.off, self.sel, self.recentre, self.locked = 0, 14, recentre, locked_from   # opens on T14
        self.taps = []

    def x_of(self, t):
        return rb.TRAIN_FIRST_X + (t - 1) * rb.TRAIN_SLOT + self.off

    def screenshot(self):
        return {"sel_x": self.x_of(self.sel), "off": self.off}

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.off = min(0, self.off + (x2 - x1))           # cannot scroll past T1

    def slow_drag(self, x1, y1, x2, y2, *a, **k):
        self.off += x2 - x1

    def tap(self, x, y):
        self.taps.append((x, y))
        for t in range(1, 17):
            if abs(self.x_of(t) - x) <= 30:
                if t >= self.locked:
                    return
                self.sel = t
                if self.recentre:                          # slide so the choice sits in the middle (not past T1)
                    self.off = min(0, 278 - (rb.TRAIN_FIRST_X + (t - 1) * rb.TRAIN_SLOT))
                return


@pytest.mark.parametrize("recentre", [False, True])
@pytest.mark.parametrize("tier", [1, 2, 5, 9, 16])
def test_pick_tier(monkeypatch, recentre, tier):
    dev = Row(recentre)
    monkeypatch.setattr(rb, "train_selected_x", lambda s: s["sel_x"] if 0 < s["sel_x"] < 540 else None)
    monkeypatch.setattr(rb, "train_row_shift", lambda a, b: b["off"] - a["off"])
    monkeypatch.setattr(rb, "_nap", lambda *a: None)
    assert rb.train_pick_tier(dev, threading.Event(), lambda *a: None, tier)
    assert dev.sel == tier


def test_pick_tier_stops_on_locked(monkeypatch):
    dev = Row(True, locked_from=15)
    monkeypatch.setattr(rb, "train_selected_x", lambda s: s["sel_x"] if 0 < s["sel_x"] < 540 else None)
    monkeypatch.setattr(rb, "train_row_shift", lambda a, b: b["off"] - a["off"])
    monkeypatch.setattr(rb, "_nap", lambda *a: None)
    assert not rb.train_pick_tier(dev, threading.Event(), lambda *a: None, 16)


def test_target_follows_the_building():
    assert rb.train_target({"duties": {"train_troops": {"building": "Stable (mounted)"}}}) == "go:Troop Training|Stable"
    assert rb.train_target({}) == "go:Troop Training|Barracks"


def test_carriage_plus_slots_by_picture():
    spots = rb.carriage_plus_spots(cv2.imread(str(S / "carriage_page_phone.png")))
    assert len(spots) == 4
    for x, y in [(128, 678), (219, 1005), (290, 787), (421, 611)]:
        assert any(abs(x - a) < 8 and abs(y - b) < 8 for a, b in spots)


def test_carriage_march_buttons_by_colour():
    pw = cv2.imread(str(S / "carriage_march_power_phone.png"))
    rs = cv2.imread(str(S / "carriage_march_reset_phone.png"))
    H = pw.shape[0]
    assert rb.find_green_button(pw, x_min=70, x_max=300, y_min=H - 140, y_max=H)            # Power First
    assert rb.find_green_button(rs, x_min=70, x_max=300, y_min=H - 140, y_max=H) is None    # Reset is red
    m = rb.find_colour_button(pw, *rb.ORANGE, x_min=300, x_max=540, y_min=H - 140, y_max=H)
    assert m and m[0] > 330                                                                  # March (orange, right)


def test_carriage_one_on_the_road_three_slots():
    spots = rb.carriage_plus_spots(cv2.imread(str(S / "carriage_page_one_out_phone.png")))   # owner 14:50: 3 left
    assert len(spots) == 3


def test_invite_icon_and_thief_attack_by_picture():
    assert rb.find_invite_icon(cv2.imread(str(S / "city_invite_kiravik.png")))          # city
    assert rb.find_invite_icon(cv2.imread(str(S / "thief_ring_map_kiravik.png")))       # map, march lines over it
    assert rb.find_invite_icon(cv2.imread(str(S / "thief_event_kiravik.png"))) is None
    assert rb.find_invite_icon(cv2.imread(str(S / "wheel_page_manhattan.png"))) is None
    a = rb.ring_icon(cv2.imread(str(S / "thief_ring_map_kiravik.png")), "attack")
    assert a and abs(a[0] - 140) < 12 and abs(a[1] - 378) < 12
    assert rb.ring_icon(cv2.imread(str(S / "tavern_ring_stalds.png")), "attack") is None
