"""Owner 30 Oct 19:25-19:31: screen guard on every tap, red wifi waits, Train troops on several buildings."""
import sys, time, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import rally_bot as rb


def fake_dev():
    d = rb.Device.__new__(rb.Device)
    rb.Device.__init__(d, "x", "adb")
    d.taps = []
    d._run = lambda *a, **k: d.taps.append(a)
    return d


def look(d, img):
    d._sig = img
    d._shot_t = time.time()
    time.sleep(0.002)


def test_four_dead_taps_flag_the_screen():
    d = fake_dev()
    rb._TL.duty_deadline = None
    same = np.zeros((240, 135), np.uint8)
    for _ in range(rb.DEAD_TAP_LIMIT + 1):
        look(d, same)
        d.tap(10, 10)
    assert d.screen_stuck


def test_a_reacting_screen_is_never_flagged():
    d = fake_dev()
    rb._TL.duty_deadline = None
    for i in range(12):
        img = np.zeros((240, 135), np.uint8)
        img[(i % 2) * 120:(i % 2) * 120 + 120] = 200
        look(d, img)
        d.tap(10, 10)
    assert not d.screen_stuck


def test_taps_without_a_look_in_between_do_not_count():
    d = fake_dev()
    rb._TL.duty_deadline = None
    look(d, np.zeros((240, 135), np.uint8))
    for _ in range(10):
        d.tap(10, 10)
    assert not d.screen_stuck


def test_inside_a_duty_the_duty_stops():
    d = fake_dev()
    rb._TL.duty_deadline = time.time() + 600
    same = np.zeros((240, 135), np.uint8)
    try:
        for _ in range(rb.DEAD_TAP_LIMIT_DUTY + 1):
            look(d, same)
            d.tap(10, 10)
        raise AssertionError("not stopped")
    except RuntimeError as e:
        assert "screen guard" in str(e)
    finally:
        rb._TL.duty_deadline = None


def test_red_wifi_waits_and_needs_10s_calm(monkeypatch):
    d = fake_dev()
    lines, waits = [], []
    stop = threading.Event()
    stop.wait = lambda s: waits.append(s) or False
    d.guard = ({}, lines.append, stop)
    seq = iter([True, True, False, False])               # seen, still red after 30 s, gone, still gone after 10 s
    monkeypatch.setattr(rb, "lag_icon", lambda img: next(seq, False))
    monkeypatch.setattr(d, "screenshot", lambda: np.zeros((960, 540, 3), np.uint8))
    d._lag_busy = False
    rb.Device._lag_wait(d, "first")                       # the first icon was seen on this picture
    assert waits[:3] == [rb.LAG_CHECK_S, rb.LAG_CHECK_S, rb.LAG_CALM_S]
    assert not d.lag_restart_due


def test_red_wifi_5_min_asks_for_a_restart(monkeypatch):
    d = fake_dev()
    stop = threading.Event()
    clock = [1000.0]
    stop.wait = lambda s: clock.__setitem__(0, clock[0] + s) or False
    d.guard = ({}, lambda m: None, stop)
    monkeypatch.setattr(rb, "lag_icon", lambda img: True)
    monkeypatch.setattr(rb.time, "time", lambda: clock[0])
    monkeypatch.setattr(d, "screenshot", lambda: np.zeros((960, 540, 3), np.uint8))
    rb._TL.duty_deadline = None
    rb.Device._lag_wait(d, "first")
    assert d.lag_restart_due and clock[0] - 1000 >= rb.LAG_MAX_S


def test_train_building_ticks_keep_old_setups():
    assert rb.train_buildings({}) == ["Barracks (ground)"]
    assert rb.train_buildings({"building": "Stable (mounted)"}) == ["Stable (mounted)"]
    assert rb.train_buildings({"building": "Stable (mounted)", "b_archer": True}) == ["Stable (mounted)", "Archer Camp (ranged)"]
    assert rb.train_buildings({"b_barracks": False, "b_workshop": True}) == ["Workshop (siege)"]


def test_busy_building_waits_others_due():
    cfg = {"duties": {"train_troops": {k: True for k in rb.TRAIN_BKEY.values()}}}
    rb._TRAIN_NEXT[("s1", "Barracks (ground)")] = time.time() + 3600
    try:
        due = rb.train_due_buildings(cfg, "s1")
        assert "Barracks (ground)" not in due and len(due) == 3
        assert rb.train_target(cfg, "s1") == "go:Troop Training|Stable"
    finally:
        rb._TRAIN_NEXT.clear()


def test_quiet_taps_are_not_counted():
    d = fake_dev()
    rb._TL.duty_deadline = None
    same = np.zeros((240, 135), np.uint8)
    with rb.quiet_taps(d):
        for _ in range(10):
            look(d, same)
            d.tap(10, 10)
    assert not d.screen_stuck


def test_troop_at_game_limit_is_read():
    import cv2
    p = Path(__file__).parent / "screens" / "train_stable_max_kiravik.png"
    if p.exists():
        assert rb.train_own(cv2.imread(str(p))) >= rb.TRAIN_OWN_MAX


def test_alliance_page_with_promotion_is_not_a_popup():
    """Rory 30 Oct 17:39-19:50: the Alliance page (Super Promotion banner) was learned as a pop-up and closed with Back."""
    import cv2
    img = cv2.imread(str(Path(__file__).parent / "screens" / "alliance_page_promo_rory.png"))
    assert rb.is_alliance_page(img)
    assert rb._poplib_seen(rb._pop_hash(img), img, lambda m: None) is None


def test_war_shortcut_icon_found_only_when_shown():
    import cv2
    t = cv2.imread(str(Path(__file__).resolve().parents[1] / "war_shortcut.png"))
    for f, want in (("rory_city_war_icon.png", True), ("rory_city_no_war_icon.png", False)):
        img = cv2.imread(str(Path(__file__).parent / "screens" / f))
        mv = cv2.minMaxLoc(cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED))[1]
        assert (mv >= 0.8) == want
