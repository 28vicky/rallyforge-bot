"""Owner 30 Oct: Black Market pictures carry real item names; old picks + old picture copies move to the new names."""
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import rally_bot as rb


class BMRealNames(unittest.TestCase):
    def test_every_renamed_picture_ships(self):
        for old, new in rb.MK_RENAMED.items():
            self.assertTrue((rb.RES / (rb._mk_stem(new) + ".png")).exists(), new)
            self.assertFalse((rb.RES / (rb._mk_stem(old) + ".png")).exists(), old)

    def test_new_names_are_list_names(self):
        for new in set(rb.MK_RENAMED.values()):
            self.assertIn(new, rb.BM_ITEM_NAMES, new)

    def test_saved_picks_follow(self):
        cfg = {"market_items": [{"name": "BM 44", "pay": "gems"}, {"name": "Stamina 50 x10", "pay": "both"},
                                {"name": "Tribute x1", "pay": "resources"}],
               "per_device": {"a": {"market_items": [{"name": "VIT 50", "pay": "gems"}]}}}
        rb.migrate_cfg(cfg)
        self.assertEqual([i["name"] for i in cfg["market_items"]], ["Stamina 50 x10", "Tribute x10"])
        self.assertEqual(cfg["market_items"][1]["pay"], "resources")
        self.assertEqual(cfg["per_device"]["a"]["market_items"][0]["name"], "Stamina 50")
        orig = rb.has_template
        rb.has_template = lambda n: (rb.RES / (n + ".png")).exists()      # releases ship the repo's mk_ pictures into templates
        try:
            w = rb.market_wanted({"market_items": [{"name": "BM 44"}, {"name": "Stamina 50 x10"}], "market_pay": "gems"})
        finally:
            rb.has_template = orig
        self.assertEqual([x[0] for x in w], ["Stamina 50 x10"])

    def test_user_copies_migrate(self):
        tmp = Path(tempfile.mkdtemp())
        old_user = rb.USER
        try:
            rb.USER = tmp
            d = tmp / "templates"
            d.mkdir()
            (d / "mk_BM_44.png").write_bytes(b"a")                   # renamed
            (d / "mk_BM_04.png").write_bytes(b"b")
            (d / "mk_Gold_100K.png").write_bytes(b"own")              # the user's own capture under the new name stays
            (d / "mk_Lumber_20M.png").write_bytes(b"mine")            # a different (good) capture stays
            sha_dummy = b"x"
            rb.MK_REMOVED_SHA.add(hashlib.sha256(sha_dummy).hexdigest())
            (d / "mk_Dragon_Crystal_x10.png").write_bytes(sha_dummy)   # exact removed copy -> deleted
            rb._mk_migrated[0] = False
            rb.migrate_market_pictures()
            self.assertEqual((d / "mk_Stamina_50_x10.png").read_bytes(), b"a")
            self.assertFalse((d / "mk_BM_44.png").exists())
            self.assertFalse((d / "mk_BM_04.png").exists())
            self.assertEqual((d / "mk_Gold_100K.png").read_bytes(), b"own")
            self.assertTrue((d / "mk_Lumber_20M.png").exists())
            self.assertFalse((d / "mk_Dragon_Crystal_x10.png").exists())
        finally:
            rb.USER = old_user
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class NotReadyDuties(unittest.TestCase):
    """Owner 30 Oct 22:28: testers never run 'Not ready yet' duties; the owner's PC (admin.txt) does."""
    def test_tester_skips(self):
        import types
        d = rb.USER / "admin.txt"
        had = d.exists()
        try:
            if had:
                d.rename(d.with_suffix(".bak"))
            cfg = {"duties": {"trade_carriage": {"on": True}, "alliance_help": {"on": True}}}
            self.assertEqual(rb.duty_status("x", "trade_carriage", cfg)[0], "off")
            self.assertNotIn("duty:trade_carriage", [t["name"] for t in rb.duty_tasks_due(cfg, "x")])
            d.write_text("owner")
            self.assertTrue(rb.duty_ready("trade_carriage"))
        finally:
            if d.exists():
                d.unlink()
            if had:
                d.with_suffix(".bak").rename(d)

    def test_lists_match_ui(self):
        import re as _re
        js = (Path(rb.__file__).parent / "ui" / "pages.js").read_text(encoding="utf-8")
        ui = set(_re.findall(r'"(\w+)"', _re.search(r"DUTY_NOT_READY = new Set\(\[(.*?)\]\)", js).group(1)))
        self.assertEqual(ui, rb.NOT_READY_DUTIES)
        self.assertTrue(rb.NOT_READY_DUTIES <= set(rb.DUTY_BY_ID))
