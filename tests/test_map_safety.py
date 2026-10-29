"""Owner 30 Oct: a mapped city is locked, a failed Map my city puts the old map back, mapping is never 'Due' by the day,
and Find missing only adds to the city picture. Run: python -m pytest tests/test_map_safety.py"""
import json
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
import numpy as np            # noqa: E402
import rally_bot as rb        # noqa: E402


class MapSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(rb, "LEARN_DIR", self.tmp / "city_learn"),
                        mock.patch.object(rb, "USER", self.tmp),
                        mock.patch.object(rb, "DUTY_FILE", self.tmp / "duties_state.json")]
        for p in self.patches:
            p.start()
        self.dev = types.SimpleNamespace(serial="127.0.0.1:16416")
        d = rb._map_dir(self.dev)
        d.mkdir(parents=True)
        (d / "culture.json").write_text(json.dumps({"culture": "europe"}))
        (d / "barracks.json").write_text(json.dumps({"map": [1168, 553], "by": "user"}))
        (d / "real_city.jpg").write_bytes(b"old-picture")
        rb.mymap_save(self.dev, running=False, complete=True, found=13, total=13, missing=[], at="2026-10-28 11:46")
        self.log = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mapped_city_is_locked_until_unlock(self):
        self.assertTrue(rb.city_locked(self.dev))
        rb.city_unlock(self.dev)
        self.assertFalse(rb.city_locked(self.dev))
        rb.city_relock(self.dev)
        self.assertTrue(rb.city_locked(self.dev))

    def test_unmapped_city_is_not_locked(self):
        rb.mymap_save(self.dev, complete=False)
        self.assertFalse(rb.city_locked(self.dev))

    def test_locked_city_never_maps(self):
        rb.map_request_set(self.dev)
        with mock.patch.object(rb, "_run_map_city") as run:
            rb.run_map_city(self.dev, {}, self.log.append, threading.Event())
        run.assert_not_called()
        self.assertEqual(json.loads((rb._map_dir(self.dev) / "barracks.json").read_text())["map"], [1168, 553])
        self.assertTrue(any("locked" in x for x in self.log))

    def test_locked_city_never_finds_missing(self):
        with mock.patch.object(rb, "run_map_movable") as run, mock.patch.object(rb, "leave_to_city"), \
                mock.patch.object(rb, "zoom_out_full"), mock.patch.object(rb, "_pin_of", return_value=None):
            rb.run_map_missing(self.dev, {}, self.log.append, threading.Event())
        run.assert_not_called()

    def test_failed_run_puts_the_old_map_back(self):
        rb.city_unlock(self.dev)
        rb.map_request_set(self.dev)
        with mock.patch.object(rb, "_run_map_city", return_value=False):
            rb.run_map_city(self.dev, {}, self.log.append, threading.Event())
        d = rb._map_dir(self.dev)
        self.assertEqual(json.loads((d / "barracks.json").read_text()).get("map"), [1168, 553])
        self.assertEqual((d / "real_city.jpg").read_bytes(), b"old-picture")
        self.assertTrue(rb.mymap_info(self.dev).get("complete"))
        self.assertTrue(rb.city_locked(self.dev), "the unlock is used up by the run")

    def test_run_that_crashes_puts_the_old_map_back(self):
        rb.city_unlock(self.dev)
        rb.map_request_set(self.dev)
        with mock.patch.object(rb, "_run_map_city", side_effect=RuntimeError("adb gone")):
            with self.assertRaises(RuntimeError):
                rb.run_map_city(self.dev, {}, self.log.append, threading.Event())
        self.assertEqual(json.loads((rb._map_dir(self.dev) / "barracks.json").read_text()).get("map"), [1168, 553])

    def test_app_start_never_wipes_a_map(self):
        rb.migrate_city_maps_v2()                              # no clean-start marker on purpose (a new tester's PC)
        d = rb._map_dir(self.dev)
        self.assertEqual(json.loads((d / "barracks.json").read_text()).get("map"), [1168, 553])
        self.assertTrue((d / "real_city.jpg").exists())
        self.assertTrue(rb.mymap_info(self.dev).get("complete"))

    def test_map_duty_is_never_due_by_the_day(self):
        cfg = {"duties": {"map_city": {"on": True}, "map_missing": {"on": True}}}
        self.assertEqual(rb.duty_status(self.dev.serial, "map_city", cfg)[0], "done")
        rb.mymap_save(self.dev, complete=False)
        self.assertNotEqual(rb.duty_status(self.dev.serial, "map_city", cfg)[0], "due")
        self.assertNotEqual(rb.duty_status(self.dev.serial, "map_missing", cfg)[0], "due")

    def test_find_missing_only_adds_to_the_picture(self):
        old = np.full((40, 60, 3), 24, np.uint8)               # dark canvas ...
        old[:, :30] = 200                                      # ... left half already photographed
        photo = rb.RealCityPhoto.__new__(rb.RealCityPhoto)
        photo.dev, photo.done, photo._saved, photo.culture = self.dev, 1, 0, "europe"
        photo.img = old.copy()
        photo.base, photo.base_ok = old, ~rb._canvas_mask(old)
        photo.filled = photo.base_ok.copy()
        photo.img[:, :] = 90                                   # the new sweep saw everything differently
        photo.filled[:, :] = True
        photo.save()
        import cv2
        out = cv2.imread(str(rb.real_city_paths(self.dev)[0]))
        self.assertGreater(int(out[20, 10].mean()), 180, "a photographed part is never replaced")
        self.assertLess(abs(int(out[20, 50].mean()) - 90), 15, "a dark part is filled")


if __name__ == "__main__":
    unittest.main()
