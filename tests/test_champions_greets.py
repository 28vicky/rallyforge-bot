"""Owner 30 Oct: testers' Champions greets stopped at 0-1 of 10 ('no Greet button found' while the 1-min greet timer
showed) and the duty said Done for the whole day; 10 greets with 1-min timers ran past the 8-min stall limit.
Run: python -m pytest tests/test_champions_greets.py"""
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import rally_bot as rb        # noqa: E402


class FakeDev:
    serial = "t:1"

    def screenshot(self):
        return None

    def tap(self, *a):
        pass


class Greets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p = [mock.patch.object(rb, "DUTY_FILE", self.tmp / "duties_state.json"),
                  mock.patch.object(rb, "_nap", lambda *a, **k: None),
                  mock.patch.object(rb, "ch_dialogue", lambda *a, **k: None),
                  mock.patch.object(rb, "rd_snap", lambda *a, **k: None)]
        for x in self.p:
            x.start()
        rb._DUTY_CACHE[0] = None
        self.stop = threading.Event()
        self.stop.wait = lambda t=None: False                # no real waiting in tests
        self.log = []

    def tearDown(self):
        for x in self.p:
            x.stop()
        rb._DUTY_CACHE[0] = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_greets(self, screens, want=3):
        """screens: list of (spot_or_None, label dict) the page shows, one per look."""
        it = iter(screens)
        cur = {}

        def find(_img):
            cur["s"] = next(it)
            return (cur["s"][0], (0, 0, 10, 10)) if cur["s"][0] else (None, None)
        with mock.patch.object(rb, "ch_find_greet", side_effect=find), \
                mock.patch.object(rb, "ch_greet_label", side_effect=lambda *_: cur["s"][1]):
            dev = FakeDev()
            n = rb.ch_do_greets(dev, self.stop, self.log.append, "Ariadne", want, dev.serial, rb._game_day())
        return n, dev

    def test_after_a_greet_it_leaves_for_the_timer(self):
        lab0 = {"pack": False, "n": None, "wait": 0}
        timer = {"pack": False, "n": None, "wait": 55}
        n, dev = self.run_greets([((10, 10), lab0), (None, timer)])
        self.assertEqual(n, 1)
        self.assertEqual(dev._ch_later, 55, "back to the rallies, return when the 1-min timer is over")
        self.assertFalse(any("waiting" in x for x in self.log))

    def test_long_timer_means_come_back_later(self):
        n, dev = self.run_greets([(None, {"pack": False, "n": None, "wait": 14400})])
        self.assertEqual(n, 0)
        self.assertEqual(dev._ch_later, 14400)

    def test_no_button_three_times_means_try_again_later(self):
        none = {"pack": False, "n": None, "wait": 0}
        n, dev = self.run_greets([(None, none)] * 3)
        self.assertEqual(n, 0)
        self.assertEqual(dev._ch_later, 1800)

    def test_status_waiting_until_next_try_then_done(self):
        cfg = {"duties": {"champions": {"on": True}}}
        rb.ch_save("t:1", next_at=time.time() + 600, all_done_day="")
        self.assertEqual(rb.duty_status("t:1", "champions", cfg)[0], "waiting")
        rb.ch_save("t:1", next_at=0, all_done_day=rb._game_day())
        self.assertEqual(rb.duty_status("t:1", "champions", cfg)[0], "done")


if __name__ == "__main__":
    unittest.main()
