"""Owner 30 Oct 15:37-15:49: Royal Thief never done / error (5 tries then a pause), Trade Carriage chests, screen guard,
app start marker."""
import threading
from pathlib import Path

import cv2
import pytest

import rally_bot as rb

S = Path(__file__).resolve().parent / "screens"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(rb, "template_path", lambda n: (ROOT / f"{n}.png") if (ROOT / f"{n}.png").exists() else None)
    rb._cache.clear()
    state = {}
    monkeypatch.setattr(rb, "load_duty_state", lambda: state)
    monkeypatch.setattr(rb, "save_duty_state", lambda d: None)
    for k in ("APP_STARTS", "DEPLOY_NOTE", "RESTART_WHY", "TASK_LOG"):
        monkeypatch.setattr(rb, k, tmp_path / k.lower())


def _big(name):
    return cv2.resize(cv2.imread(str(S / name)), (540, 960), interpolation=cv2.INTER_CUBIC)


def test_carriage_chest_found_by_picture():
    spots = rb.carriage_chests(_big("carriage_chests_rec_half.jpg"))   # owner tapped (334, 501) there
    assert any(abs(x - 334) < 25 and abs(y - 505) < 30 for x, y in spots)


def test_no_carriage_chest_after_claiming():
    assert rb.carriage_chests(_big("carriage_nochest_rec_half.jpg")) == []
    for f in ("carriage_page_phone.png", "train_page_stalds.png", "city_invite_kiravik.png"):
        assert rb.carriage_chests(cv2.imread(str(S / f))) == []


def test_royal_thief_five_misses_then_pause():
    logs = []
    mins = [rb.rt_missed("s1", logs.append, "no Attack") for _ in range(5)]
    assert mins[:4] == [1, 1, 1, 1]
    assert 15 <= mins[4] <= 30
    assert rb.rt_row("s1")["misses"] == 0                  # counting starts again after the pause


def test_royal_thief_never_failed():
    for _ in range(rb.DUTY_MAX_TRIES + 2):
        st = rb.duty_record("s1", "royal_thief", False, "boom")
    assert st == "retry"
    rb.duty_record("s1", "royal_thief", True, "", nothing=True)
    rb.duty_record("s1", "royal_thief", True, "", nothing=True)
    assert rb.rt_row("s1").get("done_day") is None             # never 'done for today'
    assert rb.rt_row("s1").get("status") != "gaveup"


def test_royal_thief_exception_is_a_miss(monkeypatch):
    monkeypatch.setattr(rb, "_run_royal_thief", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(rb, "leave_to_city", lambda *a, **k: True)
    logs = []
    rb.run_royal_thief(type("D", (), {"serial": "s2"})(), {}, logs.append, threading.Event())
    assert rb.rt_row("s2")["misses"] == 1 and any("try 2 of 5" in l for l in logs)


class _Dev:
    def __init__(self, shots):
        self.shots, self.taps, self.backs = list(shots), [], 0

    def screenshot(self):
        return self.shots.pop(0) if len(self.shots) > 1 else self.shots[0]

    def tap(self, x, y):
        self.taps.append((x, y))

    def back(self):
        self.backs += 1


def test_guard_goes_back_then_carries_on(monkeypatch):
    monkeypatch.setattr(rb, "_nap", lambda *a, **k: None)
    monkeypatch.setattr(rb, "rd_snap", lambda *a, **k: None)
    good, bad = cv2.imread(str(S / "train_page_stalds.png")), cv2.imread(str(S / "city_invite_kiravik.png"))
    dev = _Dev([bad, good])
    assert rb.task_guard(dev, threading.Event(), lambda m: None, "Train troops", rb.train_page)
    assert dev.backs == 1 and dev.taps == []               # no arrow on the city: phone Back, nothing tapped there


def test_guard_lost(monkeypatch):
    monkeypatch.setattr(rb, "_nap", lambda *a, **k: None)
    monkeypatch.setattr(rb, "rd_snap", lambda *a, **k: None)
    dev = _Dev([cv2.imread(str(S / "city_invite_kiravik.png"))])
    assert not rb.task_guard(dev, threading.Event(), lambda m: None, "Train troops", rb.train_page)


def test_app_start_marker(tmp_path):
    first = rb.app_start_marker()
    assert first["why"] == "first start"
    rb.restart_reason_set("restart for new code")
    rb.DEPLOY_NOTE.write_text("Royal Thief never done", encoding="utf-8")
    second = rb.app_start_marker()
    assert second["why"] == "restart for new code" and not second["code_changed"]
    assert not rb.RESTART_WHY.exists()
    assert "App start" in rb.TASK_LOG.read_text(encoding="utf-8")
