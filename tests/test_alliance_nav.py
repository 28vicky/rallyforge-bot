"""Owner 30 Oct: Alliance help opened Mail, Alliance Science opened Alliance War, a bot sat 15 min on an opened mail.
Real screenshots from the owner PC. Run: python -m pytest tests/test_alliance_nav.py"""
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cv2                    # noqa: E402
import rally_bot as rb        # noqa: E402

FX = Path(__file__).parent / "fixtures"


@unittest.skipUnless(shutil.which("tesseract"), "needs tesseract")
class AllianceNav(unittest.TestCase):
    def img(self, name):
        return cv2.imread(str(FX / name))

    def test_row_under_the_bottom_bar_is_not_tapped(self):
        page = self.img("alliance_page_science_hidden.png")   # 'Alliance Science' peeks out under Mail / Invite / Members
        self.assertIsNone(rb.find_alliance_row(page, ["Alliance Science"]))

    def test_rows_are_told_apart(self):
        page = self.img("alliance_page_science_hidden.png")
        war, help_, treasure = (rb.find_alliance_row(page, [w]) for w in ("Alliance War", "Alliance Help", "Alliance Treasure"))
        self.assertTrue(war and help_ and treasure)
        self.assertGreater(help_[1], treasure[1])
        self.assertGreater(treasure[1], war[1])

    def test_page_title_check(self):
        self.assertFalse(rb.rd_seen(self.img("alliance_war_page.png"), ["Alliance Science", "Science"], region=(0, 0, 540, 120)))
        self.assertTrue(rb.rd_seen(self.img("alliance_help_page.png"), ["Alliance Help"], region=(0, 0, 540, 120)))

    def test_alliance_button_from_its_word(self):
        city = self.img("city_right_column.png")
        x, y = rb.alliance_button_pos(city, {})
        self.assertLess(abs(y - 719), 12)

    def test_alliance_button_from_mail_when_its_word_is_unreadable(self):
        city = self.img("city_right_column.png")
        a = rb.find_text(city, ["Alliance"], region=(400, 200, 140, 740), cutoff=0.8)
        cv2.rectangle(city, (a[0] - 40, a[1] - 10), (a[0] + 40, a[1] + 10), (0, 0, 0), -1)
        x, y = rb.alliance_button_pos(city, {})
        m = rb.find_text(city, ["Mail"], region=(400, 300, 140, 660), cutoff=0.8)
        self.assertLess(y, m[1] - 60, "never the Mail button")
        self.assertLess(abs(y - 719), 25)

    def test_alliance_pages_are_not_mail(self):
        for f in ("alliance_page_science_hidden.png", "alliance_war_page.png", "alliance_help_page.png"):
            self.assertFalse(rb.on_mail_page(self.img(f)), f)


if __name__ == "__main__":
    unittest.main()
