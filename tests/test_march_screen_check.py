"""owner 29 Oct 23:03: the march screen is the final join check (Warhorse / Manhattan joined a Pan read as Pumpkin)."""
from pathlib import Path
import cv2
import pytest
import rally_bot as r

PIC = Path(__file__).parent / "fixtures" / "march_screen_pumpkin.png"
PAN_OFF = {"monster_skip": {k: [1, 2, 3, 4, 5, 6, 7] for k in r.PAN_KINDS},
           "monster_mode": "off", "monster_unknown": "skip", "monster_list": []}


@pytest.mark.skipif(not r.pytesseract or not hasattr(r, "march_screen_monster"), reason="needs OCR")
def test_reads_pumpkin_on_march_screen():
    name, kind, lv, pw = r.march_screen_monster(cv2.imread(str(PIC)))
    assert name == "Pumpkin Monster" and kind is None and pw == 53.6e6 and lv


def test_pan_switched_off_is_not_marched():
    assert not r.march_screen_allowed(PAN_OFF, "Pan", "Ranged Pan", 6, 361e6)[0]
    assert not r.march_screen_allowed(PAN_OFF, "Pan", None, None, 361e6)[0]     # type not readable: no guessing
    assert r.march_screen_allowed(PAN_OFF, "Pumpkin Monster", None, 5, 53.6e6)[0]


def test_hunt_prefers_preset_used_longest_ago():
    """owner 29 Oct 23:40: same monster on every preset -> the preset used longest ago goes first (not always preset 1)."""
    pairs = [{"preset": p, "mode": "rally", "rank": 0, "top": 0} for p in ("I", "II", "III")]
    last = {"I": 1000.0, "II": 3000.0, "III": 2000.0}
    assert min(pairs, key=lambda e: r.mr_pair_order(e, last))["preset"] == "I"
    last["I"] = 4000.0                                    # preset 1 just marched -> preset 3 (oldest) goes next
    assert min(pairs, key=lambda e: r.mr_pair_order(e, last))["preset"] == "III"
    assert min(pairs, key=lambda e: r.mr_pair_order(e, {}))["preset"] == "I"


def test_recorder_reads_bluestacks_style_events():
    """owner 30 Oct: recording did not work on BlueStacks - read every touch device, plain ABS_X/ABS_Y too."""
    rec = r.FlowRecorder.__new__(r.FlowRecorder)
    rec.devs = {"/dev/input/event2": (1080, 1920), "/dev/input/event5": (32767, 32767)}
    rec.maxx, rec.maxy = 1080, 1920
    rec.stop_ev = r.threading.Event()
    got = []
    rec._step = lambda a, b, dev=None: got.append((rec._to_screen(a[1], a[2], dev), dev))
    lines = ["[ 1.0] /dev/input/event5: EV_KEY BTN_TOUCH DOWN\n",
             "[ 1.0] /dev/input/event5: EV_ABS ABS_X 00004000\n",
             "[ 1.0] /dev/input/event5: EV_ABS ABS_Y 00004000\n",
             "[ 1.0] /dev/input/event5: EV_SYN SYN_REPORT 00000000\n",
             "[ 1.1] /dev/input/event5: EV_KEY BTN_TOUCH UP\n",
             "[ 1.1] /dev/input/event5: EV_SYN SYN_REPORT 00000000\n"]
    rec.proc = type("P", (), {"stdout": iter(lines)})()
    rec._events_loop()
    assert len(got) == 1 and got[0][1] == "/dev/input/event5"
    x, y = got[0][0]
    assert abs(x - r.EXPECTED_SIZE[0] // 2) <= 2 and abs(y - r.EXPECTED_SIZE[1] // 2) <= 2
