"""Pure-logic tests for RallyForge Bot (no emulator, no PC needed). Run: python -m unittest discover -s tests
Every test writes duty state to a temporary file, never to the real duties_state.json."""
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np            # noqa: E402
import rally_bot as rb        # noqa: E402

CROPS = Path(__file__).parent / "crops"     # optional saved timer pictures: name = expected seconds, e.g. 3723_join_button.png


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p_file = mock.patch.object(rb, "DUTY_FILE", self.tmp / "duties_state.json")
        self.p_file.start()
        self._clear_cache()
        self.old_reset = rb._RESET_MIN[0]
        rb._RESET_MIN[0] = 0                        # game day = UTC day, so tests do not depend on Evony's reset
        self.day = rb._game_day()

    @staticmethod
    def _clear_cache():                             # duty state is kept in memory since T68: make the next read come from the temp file
        if hasattr(rb, "_DUTY_CACHE"):
            rb._DUTY_CACHE[0] = None

    def tearDown(self):
        self.p_file.stop()
        self._clear_cache()
        rb._RESET_MIN[0] = self.old_reset
        shutil.rmtree(self.tmp, ignore_errors=True)

    def state(self, rows, serial="S1"):
        rb.DUTY_FILE.write_text(json.dumps({serial: rows}), encoding="utf-8")
        self._clear_cache()

    @staticmethod
    def cfg(**duties):
        return {"duties": {k: {"on": True, **v} for k, v in duties.items()}}


# ---------------------------------------------------------------- merge_device_cfg
class MergeDeviceCfg(unittest.TestCase):
    BP = [{"n": 1, "enabled": True, "g": "A"}, {"n": 2, "enabled": True, "g": "B"}, {"n": 3, "enabled": False, "g": "C"}]

    def test_no_override_keeps_globals(self):
        self.assertEqual(rb.merge_device_cfg({"a": 1}, "S1"), {"a": 1})

    def test_override_wins(self):
        cfg = {"a": 1, "b": 2, "per_device": {"S1": {"a": 9}}}
        m = rb.merge_device_cfg(cfg, "S1")
        self.assertEqual((m["a"], m["b"]), (9, 2))
        self.assertEqual(rb.merge_device_cfg(cfg, "S2")["a"], 1)

    def test_presets_only_switch_bot_presets(self):
        cfg = {"bot_presets": self.BP, "per_device": {"S1": {"presets": [2, 3]}}}
        m = rb.merge_device_cfg(cfg, "S1")
        self.assertEqual([bp["enabled"] for bp in m["bot_presets"]], [False, True, True])
        self.assertEqual([bp["g"] for bp in m["bot_presets"]], ["A", "B", "C"])     # generals stay

    def test_own_bot_presets_not_touched(self):
        own = [{"n": 1, "enabled": False}]
        m = rb.merge_device_cfg({"bot_presets": self.BP, "per_device": {"S1": {"presets": [1], "bot_presets": own}}}, "S1")
        self.assertEqual(m["bot_presets"], own)

    def test_input_not_changed(self):
        cfg = {"bot_presets": [dict(b) for b in self.BP], "per_device": {"S1": {"presets": [3]}}}
        rb.merge_device_cfg(cfg, "S1")
        self.assertTrue(cfg["bot_presets"][0]["enabled"])


# ---------------------------------------------------------------- duty_status
class DutyStatus(Base):
    def test_off_when_switched_off_or_unknown(self):
        self.assertEqual(rb.duty_status("S1", "vip_claim", {"duties": {"vip_claim": {"on": False}}})[0], "off")
        self.assertEqual(rb.duty_status("S1", "no_such_duty", self.cfg(no_such_duty={}))[0], "off")

    def test_due_with_no_history(self):
        self.assertEqual(rb.duty_status("S1", "vip_claim", self.cfg(vip_claim={}))[0], "due")

    def test_done_day(self):
        self.state({"vip_claim": {"done_day": self.day}})
        self.assertEqual(rb.duty_status("S1", "vip_claim", self.cfg(vip_claim={}))[0], "done")

    def test_once_a_day_done_today(self):
        self.state({"vip_claim": {"last_ok": time.time() - 30}})
        self.assertEqual(rb.duty_status("S1", "vip_claim", self.cfg(vip_claim={}))[0], "done")

    def test_once_a_day_due_after_reset(self):
        self.state({"vip_claim": {"last_ok": time.time() - 2 * 86400}})
        self.assertEqual(rb.duty_status("S1", "vip_claim", self.cfg(vip_claim={}))[0], "due")

    def test_recurring_done_then_due(self):
        self.state({"mail": {"last_ok": time.time() - 60}})
        self.assertEqual(rb.duty_status("S1", "mail", self.cfg(mail={}))[0], "done")
        self.state({"mail": {"last_ok": time.time() - 181 * 60}})
        self.assertEqual(rb.duty_status("S1", "mail", self.cfg(mail={}))[0], "due")

    def test_user_every_min_wins(self):
        self.state({"mail": {"last_ok": time.time() - 20 * 60}})
        self.assertEqual(rb.duty_status("S1", "mail", self.cfg(mail={"every_min": 10}))[0], "due")

    def test_failed_once_is_retry(self):
        self.state({"vip_claim": {"day": self.day, "tries": 1, "last_try": time.time() - 60}})
        self.assertEqual(rb.duty_status("S1", "vip_claim", self.cfg(vip_claim={}))[0], "retry")

    def test_failed_three_times_is_gaveup(self):
        self.state({"vip_claim": {"day": self.day, "tries": rb.DUTY_MAX_TRIES, "last_try": time.time() - 60}})
        st, text = rb.duty_status("S1", "vip_claim", self.cfg(vip_claim={}))
        self.assertEqual(st, "gaveup")
        self.assertIn("next try", text)

    def test_failed_long_ago_is_due_again(self):
        self.state({"vip_claim": {"day": self.day, "tries": 5, "last_try": time.time() - (rb.DUTY_RETRY_MIN + 1) * 60}})
        self.assertEqual(rb.duty_status("S1", "vip_claim", self.cfg(vip_claim={}))[0], "due")

    def test_yesterdays_failures_ignored(self):
        self.state({"vip_claim": {"day": "2000-01-01", "tries": 5, "last_try": time.time() - 60}})
        self.assertEqual(rb.duty_status("S1", "vip_claim", self.cfg(vip_claim={}))[0], "due")

    def test_timer_duty_waiting_then_all_done(self):
        self.state({"crazy_eggs": {"next_at": time.time() + 600}})
        self.assertEqual(rb.duty_status("S1", "crazy_eggs", self.cfg(crazy_eggs={}))[0], "waiting")
        self.state({"crazy_eggs": {"all_done_day": self.day}})
        self.assertEqual(rb.duty_status("S1", "crazy_eggs", self.cfg(crazy_eggs={}))[0], "done")

    def test_black_market_all_day(self):
        cfg = {**self.cfg(black_market={}), "market_refresh_mode": "all", "market_paid_refresh": True}
        self.state({"black_market": {"last_ok": time.time() - 30}})
        self.assertEqual(rb.duty_status("S1", "black_market", cfg)[0], "due")      # keeps going after a success
        self.state({"black_market": {"end_day": self.day, "done_day": self.day}})    # owner 24 Oct: never done for the day
        self.assertEqual(rb.duty_status("S1", "black_market", cfg)[0], "due")
        self.state({"black_market": {"wait_gems": True, "next_at": time.time() + 3600}})
        st = rb.duty_status("S1", "black_market", cfg)
        self.assertEqual(st[0], "waiting")
        self.assertIn("Waiting for gems", st[1])

    def test_other_instance_state_not_used(self):
        self.state({"vip_claim": {"done_day": self.day}}, serial="S2")
        self.assertEqual(rb.duty_status("S1", "vip_claim", self.cfg(vip_claim={}))[0], "due")


# ---------------------------------------------------------------- city_tasks_due
class CityTasksDue(Base):
    def names(self, cfg, last_run=None):
        return [t["name"] for t in rb.city_tasks_due(cfg, last_run or {}, "S1")]

    def test_run_now_goes_first_alone(self):
        rb.run_now_set("S1", ["duty:mail"])
        cfg = {**self.cfg(vip_claim={}), "city_enabled": True, "city_tasks": [{"name": "t1", "steps": ["x"]}]}
        self.assertEqual(self.names(cfg), ["duty:mail"])

    def test_tasks_off_only_duties(self):
        cfg = {**self.cfg(vip_claim={}), "city_enabled": False, "city_tasks": [{"name": "t1", "steps": ["x"]}]}
        self.assertEqual(self.names(cfg), ["duty:vip_claim"])

    def test_every_min(self):
        cfg = {"city_enabled": True, "city_tasks": [{"name": "t1", "steps": ["x"], "every_min": 30}]}
        self.assertEqual(self.names(cfg, {"t1": time.time() - 31 * 60}), ["t1"])
        self.assertEqual(self.names(cfg, {"t1": time.time() - 60}), [])

    def test_disabled_and_old_market_task_skipped(self):
        cfg = {"city_enabled": True, "city_tasks": [{"name": "off", "steps": ["x"], "enabled": False},
                                                    {"name": "bm", "steps": ["market"]}]}
        self.assertEqual(self.names(cfg), [])

    def test_fixed_time_once_per_slot(self):
        hm = time.strftime("%H:%M")
        cfg = {"city_enabled": True, "city_tasks": [{"name": "t1", "steps": ["x"], "at": "bad, " + hm}]}
        self.assertEqual(self.names(cfg), ["t1"])
        self.assertEqual(self.names(cfg, {"t1": time.time()}), [])


# ---------------------------------------------------------------- read_seconds
class ReadSeconds(unittest.TestCase):
    HIT = (100, 50)

    def setUp(self):
        rb._ocr_cache.clear()
        self.screen = np.random.RandomState(1).randint(0, 255, (200, 300, 3)).astype(np.uint8)

    def read(self, text):
        with mock.patch.object(rb.pytesseract, "image_to_string", return_value=text) as ocr:
            return rb.read_seconds(self.screen, self.HIT), ocr

    def test_hours_minutes_seconds(self):
        self.assertEqual(self.read("1:02:03")[0], 3723)

    def test_minutes_seconds(self):
        self.assertEqual(self.read(" 05:07\n")[0], 307)

    def test_unreadable_is_none(self):
        self.assertIsNone(self.read("::")[0])

    def test_empty_crop_is_none(self):
        self.assertIsNone(rb.read_seconds(self.screen, (1000, 1000)))

    def test_same_picture_read_once(self):
        self.read("00:10")
        secs, ocr = self.read("99:99")
        self.assertEqual(secs, 10)
        ocr.assert_not_called()

    @unittest.skipUnless(CROPS.is_dir() and shutil.which("tesseract"), "no saved crops or no Tesseract")
    def test_saved_crops(self):
        """tests/crops/<seconds>_<source>.png = the timer box itself (cut by TIMER_BOXES)."""
        import cv2
        for p in sorted(CROPS.glob("*.png")):
            want, source = p.stem.split("_", 1)
            img = cv2.imread(str(p))
            x1, y1, *_ = rb.TIMER_BOXES[source]
            screen = np.zeros((img.shape[0] + 400, img.shape[1] + 400, 3), np.uint8)
            screen[200:200 + img.shape[0], 200:200 + img.shape[1]] = img
            got = next((s for s in (rb.read_seconds(screen, (200 - x1, 200 - y1), source, a) for a in (0, 1, 2)) if s is not None), None)
            self.assertEqual(got, int(want), p.name)


# ---------------------------------------------------------------- quiet_err
class QuietErr(unittest.TestCase):
    def test_logs_once_a_minute(self):
        rb._quiet_seen.clear()
        with mock.patch.object(rb, "log_to_file") as lf:
            rb.quiet_err("here", ValueError("x"))
            rb.quiet_err("here", ValueError("y"))
            rb.quiet_err("there", ValueError("z"))
        self.assertEqual(lf.call_count, 2)
        self.assertIn("[dbg] here: ValueError: x", lf.call_args_list[0][0][0])



class CultureMatchTest(unittest.TestCase):
    def test_europe_view_matches_europe(self):
        import cv2
        d = rb.culture_dir("europe")
        if d is None:
            self.skipTest("no culture maps")
        meta = json.loads((d / "map.json").read_text(encoding="utf-8"))
        v = meta["keyframes"][len(meta["keyframes"]) // 2]
        x, y, w, h = rb.CITY_VIEW
        g = cv2.resize(cv2.imread(str(d / v["file"]), cv2.IMREAD_GRAYSCALE), (w, h))
        shot = np.zeros((960, 540, 3), np.uint8)
        shot[y:y + h, x:x + w] = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
        r = rb.match_culture(shot)
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "europe")
        self.assertLess(abs(r[1] - v["x"]) + abs(r[2] - v["y"]), 20)

class BmNameMatch(unittest.TestCase):
    """Owner 28 Oct: a new Black Market item is named from the purchase question + its tile's pack size; a wrong
    name is worse than no name, so anything unclear stays unnamed."""
    def test_clear_matches(self):
        self.assertEqual(rb.bm_name_match("Tribute", 5), "Tribute x5")
        self.assertEqual(rb.bm_name_match("Tribu", 2), "Tribute x2")           # only part of the name read
        self.assertEqual(rb.bm_name_match("Stamina 50", 10), "Stamina 50 x10")
        self.assertEqual(rb.bm_name_match("Stamina 50", 1), "Stamina 50")
        self.assertEqual(rb.bm_name_match("Food 200M", 1), "Food 200M")        # must not become Food 20M

    def test_unclear_stays_unnamed(self):
        self.assertIsNone(rb.bm_name_match("Tribute", None))     # pack size unreadable
        self.assertEqual(rb.bm_name_match("Tribute", 3), "Tribute x3")   # full name + pack read: a pack the list lacks
        self.assertIsNone(rb.bm_name_match("Tribu", 3))          # part of the name + unknown pack
        self.assertIsNone(rb.bm_name_match("General EXP", 5))    # several amounts fit
        self.assertIsNone(rb.bm_name_match("xyz", 1))
        self.assertIsNone(rb.bm_name_match("", 1))
        self.assertIsNone(rb.bm_name_match("Courage Horn", 1))  # gear level not read

    def test_game_wording(self):
        self.assertEqual(rb.bm_name_match("2K General EXP", 2), "General EXP 2000 x2")
        self.assertEqual(rb.bm_name_match("100 Chips", 1), "Chips 100 x1")
        self.assertEqual(rb.bm_name_match("50 Stamina", 10), "Stamina 50 x10")
        self.assertEqual(rb.bm_name_match("24 Hour Farm Speedup", 2), "24h Farm Speed-up x2")
        self.assertEqual(rb.bm_name_match("Fighter's Ring", 1), "Fighters Ring")
        self.assertEqual(rb.bm_name_match("Courage Horn", 1, 3), "Gear Lv3 Courage Horn")


if __name__ == "__main__":
    unittest.main()


class FailureEvidence(unittest.TestCase):
    """Owner 29 Oct: permanent failure-evidence store (USER/failures), separate from the 60-picture debug pruning."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.p_user = mock.patch.object(rb, "USER", self.tmp)
        self.p_user.start()
        rb._EV_LAST.clear()

    def tearDown(self):
        self.p_user.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dev(self):
        class D:
            serial = "127.0.0.1:16544"
            def screenshot(self):
                return np.zeros((960, 540, 3), np.uint8)
        d = D()
        rb._ev_tap(d, ("tap", 10, 20))
        return d

    def test_saves_picture_and_context(self):
        d = self._dev()
        rb.ev_note_log(d.serial, "[*] Duty: Crazy Eggs")
        rb.ev_step("Crazy Eggs: reaching it")
        rb.ev_note_ocr([("Activities", []), ("Events", [])])
        p = rb.failure_evidence(d, "crazy_eggs", "the Crazy Eggs screen did not open", pics={"start": d.screenshot()})
        info = json.loads(p.read_text())
        self.assertEqual(info["duty"], "crazy_eggs")
        self.assertEqual(info["instance"], d.serial)
        self.assertEqual(info["step"], "Crazy Eggs: reaching it")
        self.assertEqual(info["taps"][-1][1:], ["tap", 10, 20])
        self.assertIn(["Activities", "Events"], info["ocr"])
        self.assertTrue(info["log"][-1].endswith("[*] Duty: Crazy Eggs"))
        self.assertEqual(len(info["pictures"]), 2)
        for fn in info["pictures"]:
            self.assertTrue((p.parent / fn).exists())

    def test_rate_limit(self):
        d = self._dev()
        self.assertIsNotNone(rb.failure_evidence(d, "chat_check", "x", every_s=300, key="a"))
        self.assertIsNone(rb.failure_evidence(d, "chat_check", "x", every_s=300, key="a"))
        self.assertIsNotNone(rb.failure_evidence(d, "chat_check", "y", every_s=300, key="b"))

    def test_keeps_20_per_duty_per_instance(self):
        d = self._dev()
        for i in range(25):
            rb.failure_evidence(d, "wheel", f"fail {i}")
            time.sleep(0.002)
        folder = self.tmp / "failures" / "wheel"
        self.assertEqual(len(list(folder.glob("*.json"))), 20)
        self.assertEqual(len(list(folder.glob("*.png"))), 20)

    def test_housekeeping_leaves_evidence(self):
        d = self._dev()
        p = rb.failure_evidence(d, "wheel", "x")
        dbg = self.tmp / "debug"
        dbg.mkdir()
        for i in range(70):
            (dbg / f"d{i}.png").write_bytes(b"x")
        rb.ev_prune_all()
        self.assertTrue(p.exists())
        old = time.time() - 8 * 86400
        import os
        os.utime(p, (old, old))
        rb.ev_prune_all()
        self.assertFalse(p.exists())

    def test_never_raises(self):
        class Bad:
            serial = "x"
            def screenshot(self):
                raise RuntimeError("adb gone")
        p = rb.failure_evidence(Bad(), "duty", "why")
        self.assertIsNotNone(p)                      # json still written, no picture
        self.assertEqual(json.loads(p.read_text())["pictures"], [])
