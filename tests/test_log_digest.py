"""Log digest grouping (pure logic). Run: python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rally_bot as rb  # noqa: E402


class DigestTest(unittest.TestCase):
    def test_groups_by_text_ignoring_numbers_and_times(self):
        lines = [
            "10:00:01 [Rob] Joined rally 12 on Hydra (3m20s)",
            "10:05:09 [Rob] Joined rally 99 on Hydra (1m05s)",
            "10:06:00 [Rob] Idle",
            "10:07:00 [Ann] Joined rally 5 on Hydra (2m00s)",
            "  Traceback continued line",
            "10:09:00 [Rob] Joined rally 7 on Hydra (9m10s)",
        ]
        g = rb.group_log_lines(lines)
        self.assertEqual(len(g), 3)                      # Rob-joined, Rob-idle, Ann-joined; trace line skipped
        self.assertEqual((g[0]["n"], g[0]["who"], g[0]["first"], g[0]["last"]), (3, "Rob", "10:00:01", "10:09:00"))
        self.assertEqual({x["n"] for x in g[1:]}, {1})

    def test_top_limit_and_empty(self):
        self.assertEqual(rb.group_log_lines([]), [])
        lines = [f"10:00:00 [A] line{chr(97 + i)}" for i in range(10)]
        self.assertEqual(len(rb.group_log_lines(lines, top=4)), 4)

    def test_problem_filter_real_lines(self):
        stuck = "00:00:15 [Manhattan] [*] Stuck on the march screen - pressed Back"
        page = "00:00:02 [Rob] [*] Unknown page with a Back arrow - tapped the Back arrow"
        routine = "00:00:03 [Rob] [*] No rally to join here"
        g = rb.group_log_lines([routine, routine, routine, stuck, page, page])
        self.assertEqual([x["problem"] for x in g], [True, True, False])     # problems first, even though routine is most frequent
        self.assertEqual(g[0]["n"], 2)                                      # problems sorted by count
        self.assertTrue(rb.is_problem_line("[*] Stuck on the march screen - pressed Back"))
        self.assertTrue(rb.is_problem_line("[*] UNKNOWN PAGE with a Back arrow"))
        self.assertFalse(rb.is_problem_line("[*] Joined rally 5 on Hydra"))

    def test_days_kept(self):
        g = rb.group_log_lines([("2026-10-24", "23:00:00 [A] x"), ("2026-10-26", "01:00:00 [A] x")])
        self.assertEqual((g[0]["first_day"], g[0]["last_day"]), ("2026-10-24", "2026-10-26"))


if __name__ == "__main__":
    unittest.main()
