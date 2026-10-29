"""Pan troop names (owner 29 Oct). Run: python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rally_bot as rb  # noqa: E402


class PanNameTest(unittest.TestCase):
    def test_troop_suffix_dropped(self):
        for s in ("Pan (Ranged Troop)", "Pan (Ground Troop)", "Pan (Mounted Troop)", "Pan Ranged Troop", "Pan Troop"):
            self.assertEqual(rb.strip_troop_suffix(s), "Pan", s)
        for s in ("Pan", "Cerberus", "Pumpkin Monster", "Knight Bayard", "Troop"):
            self.assertEqual(rb.strip_troop_suffix(s), s)

    def test_picture_names(self):
        self.assertEqual(rb._base_monster("Pan (Ranged Troop)"), "Pan")
        self.assertEqual(rb._base_monster("Pan Mounted Troop #2"), "Pan")
        self.assertEqual(rb._base_monster("Kraken #2"), "Kraken")
        self.assertEqual(rb._base_monster("Senior Hydra"), "Hydra")

    def test_aliases_and_filter(self):
        for a in ("Pan Ranged Troop", "Pan Ground Troop", "Pan Mounted Troop"):
            self.assertEqual(rb._MONSTER_ALIASES[a], "Pan")
        off = {"monster_mode": "off", "monster_list": [], "monster_unknown": "join", "monster_skip": {"Pan": [1, 2, 3, 4, 5, 6, 7]}}
        self.assertFalse(rb.monster_allowed("Pan", off)[0])
        on = dict(off, monster_skip={"Cerberus": [1, 2, 3, 4, 5]})
        self.assertTrue(rb.monster_allowed("Pan", on)[0])

    def test_bad_picture_known(self):
        self.assertIn("Pan.png", rb._BAD_MONSTER_PICS)


if __name__ == "__main__":
    unittest.main()
