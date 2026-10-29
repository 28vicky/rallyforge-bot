"""Monster filter safety + Pan troop names (owner 29 Oct). Run: python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rally_bot as rb  # noqa: E402


def cfg(**kw):
    c = {"monster_mode": "off", "monster_list": [], "monster_unknown": "join", "monster_skip": {}}
    c.update(kw)
    return c


class TroopNameTest(unittest.TestCase):
    def test_troop_suffix_dropped(self):
        for s in ("Pan (Ranged Troop)", "Pan (Ground Troop)", "Pan (Mounted Troop)", "Pan Ranged Troop", "Pan Troop"):
            self.assertEqual(rb.strip_troop_suffix(s), "Pan", s)
        for s in ("Pan", "Cerberus", "Pumpkin Monster", "Troop"):
            self.assertEqual(rb.strip_troop_suffix(s), s)

    def test_picture_names_count_as_pan(self):
        self.assertEqual(rb._base_monster("Pan (Ranged Troop)"), "Pan")
        self.assertEqual(rb._base_monster("Pan Mounted Troop #2"), "Pan")
        self.assertEqual(rb._base_monster("Pan #3"), "Pan")
        self.assertEqual(rb._base_monster("Kraken #2"), "Kraken")

    def test_aliases(self):
        for a in ("Pan Ranged Troop", "Pan Ground Troop", "Pan Mounted Troop"):
            self.assertEqual(rb._MONSTER_ALIASES[a], "Pan")


class UnknownMonsterSafetyTest(unittest.TestCase):
    def test_filters_active(self):
        self.assertFalse(rb.monster_filters_active(cfg()))
        self.assertFalse(rb.monster_filters_active(cfg(monster_skip={"Pan": []})))
        self.assertTrue(rb.monster_filters_active(cfg(monster_skip={"Pan": [1, 2, 3, 4, 5, 6, 7]})))
        self.assertTrue(rb.monster_filters_active(cfg(monster_skip={"Zombie": [1]})))
        self.assertTrue(rb.monster_filters_active(cfg(monster_mode="only", monster_list=["Witch"])))
        self.assertTrue(rb.monster_filters_active(cfg(monster_mode="skip", monster_list=["Witch"])))
        self.assertFalse(rb.monster_filters_active(cfg(monster_mode="skip", monster_list=[])))

    def test_unknown_skipped_while_filters_active(self):
        off = {"Pan": [1, 2, 3, 4, 5, 6, 7], "Cerberus": [1, 2, 3, 4, 5]}
        for mode, unk in (("off", "join"), ("skip", "join"), ("skip", "skip"), ("only", "join")):
            ok, why = rb.monster_allowed(None, cfg(monster_mode=mode, monster_unknown=unk, monster_skip=off))
            self.assertFalse(ok, (mode, unk))
            self.assertEqual(why, rb.UNKNOWN_FILTERED)

    def test_unknown_unchanged_without_filters(self):
        self.assertTrue(rb.monster_allowed(None, cfg(monster_mode="off"))[0])
        self.assertTrue(rb.monster_allowed(None, cfg(monster_mode="skip", monster_unknown="join"))[0])
        self.assertFalse(rb.monster_allowed(None, cfg(monster_mode="skip", monster_unknown="skip"))[0])

    def test_known_monsters_unchanged(self):
        off = {"Pan": [1, 2, 3, 4, 5, 6, 7]}
        self.assertFalse(rb.monster_allowed("Pan", cfg(monster_skip=off))[0])
        self.assertTrue(rb.monster_allowed("Pumpkin Monster", cfg(monster_skip=off), 8)[0])
        self.assertFalse(rb.monster_allowed("hidden", cfg(monster_skip=off))[0])


if __name__ == "__main__":
    unittest.main()
