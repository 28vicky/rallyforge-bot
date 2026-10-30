"""Train troops: a building that is still training is 'busy' (waiting), never a failure and never done (owner 30 Oct 18:43)."""
import time
from pathlib import Path
import cv2
import rally_bot as rb

SHOT = Path(__file__).parent / "screens" / "train_busy_stalds.png"


def test_timer_reader():
    assert rb.rd_hms_left("04:51:19") == 4 * 3600 + 51 * 60 + 19
    assert rb.rd_hms_left("1d 02:10:05") == 86400 + 2 * 3600 + 10 * 60 + 5
    assert rb.rd_hms_left("nothing") is None


def test_busy_page_read():
    img = cv2.imread(str(SHOT))
    assert rb.rd_find(img, ["Training Troops", "Training"], region=(0, 770, 540, 90))
    left = rb.rd_hms_left(" ".join(t for t, _ in rb.rd_lines(img, (230, 790, 160, 60))))
    assert left == 4 * 3600 + 51 * 60 + 19


def test_busy_is_waiting_not_done(tmp_path, monkeypatch):
    state = {}
    monkeypatch.setattr(rb, "load_duty_state", lambda: state)
    monkeypatch.setattr(rb, "save_duty_state", lambda d: None)
    cfg = {"duties": {"train_troops": {"on": True}}}
    rb.duty_record("s1", "train_troops", True, wait_until=time.time() + 3600)
    st, text = rb.duty_status("s1", "train_troops", cfg)
    assert st == "waiting" and "next try" in text
    state["s1"]["train_troops"]["next_at"] = time.time() - 1
    assert rb.duty_status("s1", "train_troops", cfg)[0] == "due"
