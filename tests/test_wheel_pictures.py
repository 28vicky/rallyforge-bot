"""Owner 30 Oct 13:42: the Wheel reward window and page are found by picture (Manhattan screenshots)."""
from pathlib import Path
import cv2
import rally_bot as rb

S = Path(__file__).resolve().parent / "screens"


def test_reward_window_by_picture(monkeypatch):
    root = Path(__file__).resolve().parents[1]                  # the repo-root pictures ship as templates (--templates .)
    monkeypatch.setattr(rb, "template_path", lambda n: (root / f"{n}.png") if (root / f"{n}.png").exists() else None)
    rb._cache.clear()
    panel = cv2.imread(str(S / "wheel_reward_manhattan.png"))
    page = cv2.imread(str(S / "wheel_page_manhattan.png"))
    assert rb._wheel_pic(panel, "wheel_reward_banner", (30, 140, 480, 140), 0.7) is True
    assert rb._wheel_pic(page, "wheel_reward_banner", (30, 140, 480, 140), 0.7) is False
    assert rb._wheel_pic(page, "wheel_page_title", (100, 0, 340, 70), 0.8) is True
    assert rb.wheel_reward_panel(panel) and not rb.wheel_reward_panel(page)
    assert rb.wheel_on_page(page)


def test_x100_follows_the_reward_window(monkeypatch):
    """Press -> nothing until the window shows (late) -> tap outside -> window gone -> next press. No extra presses."""
    import threading
    state = {"panel_in": None, "panel_left": 0}
    taps = []

    class Dev:
        serial = "t"
        def screenshot(self):
            if state["panel_in"] is not None:
                state["panel_in"] -= 1
                if state["panel_in"] <= 0:
                    state["panel_in"] = None
                    state["panel_left"] = 1
            return state
        def tap(self, x, y):
            taps.append((x, y))
            if (x, y) == rb.WHEEL_BTN[100]:
                state["panel_in"] = 7                     # the window shows after 7 looks (slow PC)
            elif (x, y) == (270, 110) and state["panel_left"]:
                state["panel_left"] = 0

    monkeypatch.setattr(rb, "wheel_on_page", lambda s: True)
    monkeypatch.setattr(rb, "wheel_reward_panel", lambda s: bool(s["panel_left"]))
    monkeypatch.setattr(rb, "wheel_coins", lambda s, min_digits=1: 100000)
    monkeypatch.setattr(rb, "wheel_spun_today", lambda serial, add=0: 0)
    monkeypatch.setattr(rb, "duty_progress", lambda *a: None)
    cfg = {"wheel": {"button": 100, "spins": 3, "reserve": 0}}
    done = rb.run_wheel(Dev(), cfg, lambda *a: None, threading.Event())
    assert done == 300
    spin = rb.WHEEL_BTN[100]
    assert taps == [spin, (270, 110), spin, (270, 110), spin, (270, 110)]


def test_tavern_ring_items_from_detail_icon():
    """Owner 30 Oct 14:10: Stalds' open Tavern ring - Wheel and Great General Chest from the Detail icon."""
    shot = cv2.imread(str(S / "tavern_ring_stalds.png"))
    w = rb.tavern_ring_spot(shot, "wheel")
    g = rb.tavern_ring_spot(shot, "gchest")
    assert w and abs(w[0] - 141) < 25 and 280 < w[1] < 340          # Wheel of Fortune icon / label
    assert g and abs(g[0] - 372) < 30 and 240 < g[1] < 300          # Great General Chest icon


def test_dome_gem_button_is_green():
    shot = cv2.imread(str(S / "dome_honor_token_kiravik.png"))
    assert rb.btn_colour(shot, *rb.DOME_GEM_BTN) == "green"


def test_dome_buy_button_by_colour():
    conf = cv2.imread(str(S / "dome_buy_confirm_phone.png"))         # owner's phone screenshot, scaled to 540 wide
    page = cv2.imread(str(S / "dome_honor_token_kiravik.png"))
    b = rb.find_green_button(conf, x_min=280, x_max=480, y_min=360, y_max=840)
    assert b and 330 < b[0] < 390                                      # the green Buy (right), never the red Cancel
    assert rb.find_green_button(page, x_min=280, x_max=480, y_min=360, y_max=840) is None
