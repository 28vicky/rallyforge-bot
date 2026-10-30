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


def test_ready_page_found_by_colour():
    """Kiravik 18:52: 'Train' over its timer is not read as a word - the page is found by the button colours."""
    img = cv2.imread(str(Path(__file__).parent / "screens" / "train_ready_kiravik.png"))
    assert rb.train_page(img)
    assert not rb.rd_find(img, ["Training Troops", "Training"], region=(0, 770, 540, 90))
    assert not rb.train_page(cv2.imread(str(SHOT)))


def test_champion_greet_read_with_stray_letter():
    """Skip 30 Oct (all day 'no Greet button'): OCR read 'gGreet' - the Greet button must still be found."""
    img = cv2.imread(str(Path(__file__).parent / "screens" / "champ_nogreet_skip.png"))
    assert rb.ch_greet_word(img) is not None


def test_champion_greet_found_gkp():
    """GK-P 30 Oct (0 greets all day): 'Greet4/10' over the bright floor - found by the thin-strip read."""
    img = cv2.imread(str(Path(__file__).parent / "screens" / "champ_nogreet_gkp.png"))
    assert rb.ch_greet_word(img) is not None
