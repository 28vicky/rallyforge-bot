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
