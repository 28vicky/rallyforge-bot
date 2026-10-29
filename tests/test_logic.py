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

    def _bad(self):
        class Bad:
            serial = "x"
            def _grab(self):
                raise RuntimeError("adb gone")
            def screenshot(self):
                raise AssertionError("evidence must never use the waiting/tapping screenshot()")
        return Bad()

    def test_screenshot_fails(self):
        p = rb.failure_evidence(self._bad(), "duty", "why")
        info = json.loads(p.read_text())             # json still written, no picture
        self.assertEqual(info["pictures"], [])
        self.assertIn("screenshot failed", info["notes"])

    def test_picture_write_fails(self):
        with mock.patch.object(rb.cv2, "imwrite", side_effect=OSError("denied")):
            p = rb.failure_evidence(self._dev(), "duty", "why")
        self.assertEqual(json.loads(p.read_text())["pictures"], [])

    def test_json_write_fails(self):
        with mock.patch.object(rb.Path, "write_text", side_effect=OSError("disk full")):
            self.assertIsNone(rb.failure_evidence(self._dev(), "duty", "why"))

    def test_folder_permission_fails(self):
        with mock.patch.object(rb.Path, "mkdir", side_effect=PermissionError("no")):
            self.assertIsNone(rb.failure_evidence(self._dev(), "duty", "why"))

    def test_disk_almost_full(self):
        from collections import namedtuple
        du = namedtuple("du", "total used free")(100, 99, 10 * 1024 * 1024)
        with mock.patch.object(rb.shutil, "disk_usage", return_value=du):
            p = rb.failure_evidence(self._dev(), "duty", "why")
        info = json.loads(p.read_text())
        self.assertEqual(info["pictures"], [])
        self.assertTrue(any("pictures skipped" in n for n in info["notes"]))

    def test_time_cap_drops_extra_pictures(self):
        d = self._dev()
        clock = iter([0.0] + [100.0] * 50)            # start, then far past EV_MAX_SECONDS
        with mock.patch.object(rb.time, "time", side_effect=lambda: next(clock)):
            p = rb.failure_evidence(d, "duty", "why", shot=d.screenshot(), pics={"a": d.screenshot()})
        self.assertEqual(json.loads(p.read_text())["pictures"], [])


class CrazyEggsLuckyConfirm(unittest.TestCase):
    """Owner screenshot 29 Oct: the Confirm label is not readable; the button is found by colour inside the dialog."""
    SHOT = Path(__file__).parent / "screens" / "ce_lucky_dialog_29oct.png"

    def setUp(self):
        import cv2
        if not self.SHOT.exists():
            self.skipTest("screenshot missing")
        self.img = cv2.imread(str(self.SHOT))
        self.tmp = Path(tempfile.mkdtemp())
        self.p_user = mock.patch.object(rb, "USER", self.tmp)
        self.p_user.start()

    def tearDown(self):
        self.p_user.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_button_found_in_dialog(self):
        p, info = rb.ce_confirm_button(self.img)
        self.assertIsNotNone(p, info)
        self.assertLess(abs(p[0] - 358), 15)
        self.assertLess(abs(p[1] - 566), 15)

    def test_second_green_button_in_dialog_means_no_tap(self):
        import cv2
        img = self.img.copy()
        cv2.rectangle(img, (300, 606), (420, 640), (40, 160, 40), -1)     # another green button inside the search box
        p, info = rb.ce_confirm_button(img)
        self.assertIsNone(p)
        self.assertIn("need exactly 1", info["why"])

    def test_green_outside_dialog_ignored(self):
        import cv2
        img = self.img.copy()
        cv2.rectangle(img, (300, 840), (430, 880), (40, 160, 40), -1)     # green under the dialog (View Rewards row)
        cv2.rectangle(img, (110, 545), (250, 585), (40, 160, 40), -1)     # green on the LEFT side of the dialog
        p, _ = rb.ce_confirm_button(img)
        self.assertIsNotNone(p)
        self.assertLess(abs(p[1] - 566), 15)

    def _dev(self, shots):
        class D:
            serial = "test"
            taps = []
            def __init__(s):
                s.q = list(shots)
            def screenshot(s):
                return s.q.pop(0) if len(s.q) > 1 else s.q[0]
            def tap(s, x, y):
                s.taps.append((x, y))
        return D()

    def test_confirmed_only_when_dialog_gone(self):
        import numpy as np
        gone = np.zeros_like(self.img)
        with mock.patch.object(rb, "_nap"), mock.patch.object(rb, "ce_snap"):
            d = self._dev([self.img, gone])
            self.assertEqual(rb.ce_lucky_confirm(d, None), "confirmed")
            self.assertEqual(len(d.taps), 1)
            d = self._dev([self.img, self.img])
            self.assertEqual(rb.ce_lucky_confirm(d, None), "still_open")
        self.assertTrue(list((self.tmp / "failures" / "crazy_eggs").glob("*.json")))

    def test_no_button_no_tap(self):
        import cv2
        img = self.img.copy()
        cv2.rectangle(img, (280, 540), (440, 590), (30, 30, 30), -1)      # button hidden
        with mock.patch.object(rb, "_nap"), mock.patch.object(rb, "ce_snap"):
            d = self._dev([img])
            self.assertEqual(rb.ce_lucky_confirm(d, None), "no_button")
            self.assertEqual(d.taps, [])


class HuntsService(Base):
    """Owner 29 Oct: Monster hunts = background service; solo presets hunt while the rally preset is out."""
    S = "127.0.0.1:16544"

    def setUp(self):
        super().setUp()
        self.p_dec = mock.patch.object(rb, "HUNT_DEC_FILE", self.tmp / "hunt_decisions.json")
        self.p_dec.start()
        self.p_user = mock.patch.object(rb, "USER", self.tmp)
        self.p_user.start()
        self.cfg = {"duties": {"monster_rally": {
            "rules": [{"name": "Pumpkin", "min": 1, "max": 8, "preset": "I", "time": "5"},
                      {"name": "Cerberus", "min": 1, "max": 2, "preset": "II", "time": "5"},
                      {"name": "Cerberus", "min": 1, "max": 2, "preset": "III", "time": "5"}],
            "presets": {"I": {"mode": "rally"}, "II": {"mode": "solo"}, "III": {"mode": "solo"}}}}}
        for a in ("hunt_t0", "hunt_chk", "hunt_handoff", "hunt_alive", "hunt_map_ok", "city_last"):
            if hasattr(rb._TL, a):
                delattr(rb._TL, a)

    def tearDown(self):
        self.p_dec.stop()
        self.p_user.stop()
        super().tearDown()

    def test_group_until(self):
        now = time.time()
        self.assertEqual(rb.mr_group_until(self.cfg, self.S, "rally", now), 0)
        rb.mr_save(self.S, busy={"I": now - 60})
        self.assertAlmostEqual(rb.mr_group_until(self.cfg, self.S, "rally", now), now - 60 + 300, delta=1)
        self.assertEqual(rb.mr_group_until(self.cfg, self.S, "solo", now), 0)           # II and III free
        rb.mr_save(self.S, busy={"I": now - 60, "II": now - 10})
        self.assertEqual(rb.mr_group_until(self.cfg, self.S, "solo", now), 0)           # III still free
        rb.mr_save(self.S, solo_full_until=now + 50)
        self.assertEqual(rb.mr_group_until(self.cfg, self.S, "solo", now), now + 50)     # no march for solo: held out 1 min

    def test_rally_eta_march_panel_wins(self):
        now = time.time()
        rb.mr_save(self.S, busy={"I": now - 280}, rback=now + 200)
        self.assertEqual(rb.mr_rally_eta(self.cfg, self.S, now), now + 200)
        rb.mr_save(self.S, rback=0)
        self.assertAlmostEqual(rb.mr_rally_eta(self.cfg, self.S, now), now + 20, delta=1)

    def _dev(self):
        class D:
            serial = "127.0.0.1:16544"
            aj_next = 0
            _guard_busy = False
            def screenshot(self):
                import numpy as np
                return np.zeros((960, 540, 3), np.uint8)
            def tap(self, x, y):
                pass
        return D()

    def test_handoff_reasons(self):
        d = self._dev()
        with mock.patch.object(rb, "city_tasks_due", return_value=[]):
            self.assertIsNone(rb.hunt_handoff_reason(d, self.cfg, self.S, time.time()))
            self.assertEqual(rb.hunt_handoff_reason(d, self.cfg, self.S, time.time() - 31 * 60), "check-in")
            d.aj_next = time.time() - 1
            self.assertEqual(rb.hunt_handoff_reason(d, self.cfg, self.S, time.time()), "Auto-Join renewal")
        d.aj_next = 0
        with mock.patch.object(rb, "city_tasks_due", return_value=[{"name": "duty:monster_rally"}, {"name": "duty:crazy_eggs"}]):
            self.assertEqual(rb.hunt_handoff_reason(d, self.cfg, self.S, time.time()), "Crazy Eggs")

    def test_alive_line_only_when_map_seen(self):
        d, lines = self._dev(), []
        rb._TL.hunt_chk = time.time()
        with mock.patch.object(rb, "view_of", return_value="city"):
            rb.hunt_tick(d, self.cfg, lines.append, None, self.S, "waiting on X")
        self.assertEqual(lines, [])                                  # not the map: a dead game must not look alive
        with mock.patch.object(rb, "view_of", return_value="world"):
            rb.hunt_tick(d, self.cfg, lines.append, None, self.S, "waiting on X")
            rb.hunt_tick(d, self.cfg, lines.append, None, self.S, "waiting on X")
        self.assertEqual(len(lines), 1)                              # at most one a minute

    def _run_service(self, side_effect):
        import threading
        d, logs = self._dev(), []
        stop = threading.Event()
        with mock.patch.object(rb, "_run_monster_rally", side_effect=side_effect), \
                mock.patch.object(rb, "mr_to_map"), mock.patch.object(rb, "failure_evidence"), \
                mock.patch.object(rb, "city_tasks_due", return_value=[]):
            rb.run_monster_rally(d, self.cfg, logs.append, stop)
        return logs

    def test_error_is_not_a_failure(self):
        def boom(*a):
            raise RuntimeError("no March button (the troop page is gone)")
        t = time.time()
        logs = self._run_service(boom)                               # must NOT raise
        row = rb.mr_row(self.S)
        self.assertGreaterEqual(row["next_at"], t + 55)              # retry in 1 min
        self.assertEqual(row["err_n"], 1)
        self.assertTrue(any("trying again in 1 min" in l for l in logs))

    def test_five_errors_pause_five_minutes(self):
        rb.mr_save(self.S, err_streak=4)
        def boom(*a):
            raise RuntimeError("x")
        t = time.time()
        self._run_service(boom)
        self.assertGreaterEqual(rb.mr_row(self.S)["next_at"], t + 295)

    def test_lost_control_still_raises(self):
        def boom(*a):
            rb.TASK_ABORT.add(self.S)
            raise RuntimeError("watchdog")
        try:
            with self.assertRaises(RuntimeError):
                self._run_service(boom)
        finally:
            rb.TASK_ABORT.discard(self.S)

    def test_handoff_comes_straight_back(self):
        def handoff(*a):
            rb._TL.hunt_handoff = "Crazy Eggs"
        t = time.time()
        logs = self._run_service(handoff)
        self.assertLessEqual(rb.mr_row(self.S)["next_at"], time.time() + 1)
        self.assertGreaterEqual(rb.mr_row(self.S)["next_at"], t - 1)
        self.assertTrue(any("Crazy Eggs is due" in l for l in logs))

    def _pass(self, targets, flown):
        import threading
        d = self._dev()
        def search(dev, stop, x, y):
            flown.append(f"{x},{y}")
            return False                                             # stop right after "flying" (later)
        with mock.patch.object(rb, "mr_targets", return_value=targets), \
                mock.patch.object(rb, "ensure_view", return_value=True), \
                mock.patch.object(rb, "_wait_view", return_value=d.screenshot()), \
                mock.patch.object(rb, "mr_search", side_effect=search), \
                mock.patch.object(rb, "mr_return_secs", return_value=200):
            rb._run_monster_rally(d, self.cfg, lambda m: None, threading.Event())

    def _tg(self, name, x, preset, mode, rank=0):
        return {"name": name, "level": 1, "x": x, "y": 1, "km": 10, "t": time.time(), "rank": rank, "mode": mode,
                "rule": {"name": name, "min": 1, "max": 8, "preset": preset, "time": "5"}}

    def test_rally_out_solo_goes_first_and_rally_target_held(self):
        rb.mr_save(self.S, busy={"I": time.time() - 30})            # rally preset just left (~4.5 min out)
        flown = []
        self._pass([self._tg("Pumpkin", 100, "I", "rally"), self._tg("Cerberus", 200, "II", "solo", 1)], flown)
        self.assertEqual(flown, ["200,1"])                           # the rally monster was NOT flown to
        self.assertEqual(rb.mr_row(self.S).get("hold"), "100,1")    # but it is held for the rally preset

    def test_rally_free_rally_first(self):
        flown = []
        self._pass([self._tg("Pumpkin", 100, "I", "rally"), self._tg("Cerberus", 200, "II", "solo", 1)], flown)
        self.assertEqual(flown, ["100,1"])

    def test_no_solo_targets_parks_as_before(self):
        rb.mr_save(self.S, busy={"I": time.time() - 30})
        flown = []
        self._pass([self._tg("Pumpkin", 100, "I", "rally")], flown)
        self.assertEqual(flown, ["100,1"])                           # 25 Oct behaviour kept when there is no solo work

    def test_rally_due_soon_no_new_solo(self):
        rb.mr_save(self.S, busy={"I": time.time() - 270}, hold="100,1")   # back by the clock in ~30 s
        flown = []
        # held target dropped out of this pass's list order on purpose: a solo target comes first
        self._pass([self._tg("Cerberus", 200, "II", "solo", 0), self._tg("Pumpkin", 100, "I", "rally", 1)], flown)
        self.assertNotIn("200,1", flown[:1])                         # no solo attack started when the rally is due


class DutyStallBudget(unittest.TestCase):
    """Owner 29 Oct: duty limit = stall timer (activity extends it), hard ceiling, same-line loops still time out."""
    def tearDown(self):
        rb.duty_budget_end()

    def test_progress_extends(self):
        rb.duty_budget_start(8, 30)
        rb._TL.duty_deadline = time.time() + 5                     # nearly over
        rb.duty_note_progress("[*] Wheel: spin 3")
        self.assertGreater(rb._TL.duty_deadline, time.time() + 7 * 60)

    def test_ceiling_caps(self):
        rb.duty_budget_start(8, 30)
        rb._TL.duty_ceiling = time.time() + 60                     # 1 min left of the run
        rb.duty_note_progress("[*] still working")
        self.assertLessEqual(rb._TL.duty_deadline, time.time() + 61)
        rb._TL.duty_ceiling = time.time() - 1
        self.assertIn("limit for one run", rb.duty_budget_reason())

    def test_same_line_is_not_progress(self):
        rb.duty_budget_start(8, 30)
        for i in range(rb.DUTY_SAME_LINE_MAX):
            rb.duty_note_progress(f"[*] Champions: no Greet button found ({i} s)")   # numbers ignored
        rb._TL.duty_deadline = time.time() + 5
        rb.duty_note_progress("[*] Champions: no Greet button found (99 s)")
        self.assertLess(rb._TL.duty_deadline, time.time() + 10)    # not pushed
        self.assertIn("same step", rb.duty_budget_reason())
        rb.duty_note_progress("[*] Champions: greeted Ada")         # a different line counts again
        self.assertGreater(rb._TL.duty_deadline, time.time() + 7 * 60)

    def test_real_limit_in_message(self):
        rb.duty_budget_start(50, 60)
        self.assertIn("no progress for 50 min", rb.duty_budget_reason())

    def test_no_duty_running_no_effect(self):
        rb.duty_budget_end()
        rb.duty_note_progress("[*] rally list")
        self.assertFalse(rb._TL.duty_deadline)

    def test_deadline_raises_real_reason(self):
        class D(rb.Device):
            def __init__(self):
                self.adb, self.serial = "adb", "x"
        rb.duty_budget_start(8, 30)
        rb._TL.duty_deadline = time.time() - 1
        with mock.patch.object(rb, "halt_check"):
            with self.assertRaises(RuntimeError) as cm:
                D()._run("shell", "input", "tap", "1", "1")
        self.assertIn("no progress for 8 min", str(cm.exception))


class WheelZero(unittest.TestCase):
    """Owner 29 Oct: 0 wheel coins = 'No wheel coins available.', a normal end, not a failure."""
    def _lines(self, txt):
        return mock.patch.object(rb, "ocr_lines", return_value=[(txt, [])])

    def test_reads_zero_and_one_digit(self):
        import numpy as np
        img = np.zeros((960, 540, 3), np.uint8)
        with self._lines("0"):
            self.assertEqual(rb.wheel_coins(img), 0)
        with self._lines("7"):
            self.assertEqual(rb.wheel_coins(img), 7)
        with self._lines("27,337,900"):
            self.assertEqual(rb.wheel_coins(img), 27337900)

    def test_chips_still_need_two_digits(self):
        import numpy as np
        img = np.zeros((960, 540, 3), np.uint8)
        with self._lines("0"), mock.patch.object(rb.pytesseract, "image_to_string", return_value=""):
            self.assertIsNone(rb.wheel_coins(img, min_digits=2))

    def _run(self, reads):
        import threading, numpy as np
        class D:
            serial = "w"
            taps = []
            def screenshot(self):
                return np.zeros((960, 540, 3), np.uint8)
            def tap(self, x, y):
                self.taps.append((x, y))
        d, logs, it = D(), [], iter(reads)
        with mock.patch.object(rb, "wheel_on_page", return_value=True), \
                mock.patch.object(rb, "wheel_reward_panel", return_value=False), \
                mock.patch.object(rb, "wheel_coins", side_effect=lambda *a, **k: next(it)), \
                mock.patch.object(rb, "wheel_conf", return_value={"button": 100, "spins": 0, "reserve": 0, "chips": "off"}), \
                mock.patch.object(rb, "wheel_spun_today", return_value=0), mock.patch.object(rb, "rd_snap"):
            r = rb.run_wheel(d, {}, logs.append, threading.Event())
        return r, logs, d.taps

    def test_zero_coins_normal_end(self):
        r, logs, taps = self._run([0, 0, 0])
        self.assertEqual(r, 0)
        self.assertIn("[i]   Wheel: No wheel coins available.", logs)
        self.assertEqual(taps, [])                                  # never pressed a spin button

    def test_zero_not_confirmed_is_not_trusted(self):
        with self.assertRaises(RuntimeError):                        # 0 then 5: reads disagree -> no decision on a bad read
            self._run([0, 5, 0])
