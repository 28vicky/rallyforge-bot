"""Owner 30 Oct: instances that read the same rallies are one alliance group (learned, no setup); in a group a list that
misses the others' rallies is broken; an instance with no group is alone on its server.
Run: python -m pytest tests/test_alliance_groups.py"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import rally_bot as rb        # noqa: E402

CFG = {"monster_mode": "off", "monster_list": []}


class Groups(unittest.TestCase):
    def setUp(self):
        rb._RALLY_ENDS.clear()
        rb._ALLIES.clear()
        rb._RALLY_LEVEL.clear()
        self.p = mock.patch.object(rb, "monster_allowed", lambda *a, **k: (True, ""))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def share(self, a, b, n, mon="Pumpkin Monster"):
        for i in range(n):
            rb.note_rally(a, mon, 200 + i * 40)
            rb.note_rally(b, mon, 200 + i * 40)

    def test_one_shared_rally_is_not_a_group(self):
        self.share("rob", "k", 1)
        self.assertEqual(rb.allies_of("rob"), set())

    def test_three_shared_rallies_make_a_group(self):
        self.share("rob", "k", 3)
        self.assertEqual(rb.allies_of("rob"), {"k"})
        self.assertEqual(rb.allies_of("rory"), set(), "an instance on another server stays alone")

    def test_a_rally_the_group_reads_but_this_list_does_not_is_missed(self):
        self.share("rob", "k", 3)
        rb.note_rally("k", "Lava Turtle", 250)
        miss = rb.missed_rallies("rob", CFG)
        self.assertEqual([m for m, _ in miss], ["Lava Turtle"])
        rb.note_rally("rob", "Lava Turtle", 250)
        self.assertEqual(rb.missed_rallies("rob", CFG), [])

    def test_nearly_over_rally_is_not_counted(self):
        self.share("rob", "k", 3)
        rb.note_rally("k", "Lava Turtle", 30)                  # under 45 s left: may close before this list refreshes
        self.assertEqual(rb.missed_rallies("rob", CFG), [])


if __name__ == "__main__":
    unittest.main()
