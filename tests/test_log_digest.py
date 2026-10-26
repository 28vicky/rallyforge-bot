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


if __name__ == "__main__":
    unittest.main()
