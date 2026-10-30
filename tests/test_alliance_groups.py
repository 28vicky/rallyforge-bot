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


class RestartOnlyAfterEmptyList(unittest.TestCase):
    """Owner 30 Oct: restart only after 30 min of a completely empty list; chat lines never mean 'not in an alliance';
    Rory and GK-P are each their own alliance."""

    def test_joined_the_alliance_chat_line_is_not_the_join_page(self):
        lines = [("Kiravik joined the alliance", []), ("2 mins ago", []), ("Alliance War started", [])]
        self.assertFalse(rb.not_in_alliance(lines))

    def test_join_page_without_messages(self):
        self.assertTrue(rb.not_in_alliance([("You are not in an alliance", []), ("Join an alliance", [])]))

    def test_join_words_beside_chat_messages_do_not_count(self):
        self.assertFalse(rb.not_in_alliance([("join an alliance", []), ("5 secs ago", [])]))

    def test_alone_instances_are_never_grouped(self):
        import json, tempfile
        from pathlib import Path
        old_user, old = rb.USER, dict(rb._ALONE)
        try:
            with tempfile.TemporaryDirectory() as t:
                rb.USER = Path(t)
                (Path(t) / "alliance_groups.json").write_text(json.dumps({"alone": ["R"]}), encoding="utf-8")
                rb._ALONE.update({"t": 0.0, "set": set()})
                rb._ALLIES.clear()
                rb._RALLY_ENDS.clear()
                for i in range(3):
                    for s in ("A", "B", "R"):
                        rb.note_rally(s, f"M{i}", 300 + i * 40)
                self.assertEqual(rb.allies_of("A"), {"B"})
                self.assertEqual(rb.allies_of("R"), set())
        finally:
            rb.USER = old_user
            rb._ALONE.clear()
            rb._ALONE.update(old)
            rb._ALLIES.clear()
            rb._RALLY_ENDS.clear()

    def test_no_group_restart_code_left(self):
        import inspect
        src = inspect.getsource(rb)
        self.assertNotIn('reason="rally list misses the alliance\'s rallies"', src)
        self.assertNotIn('reason="chat check failed"', src)
