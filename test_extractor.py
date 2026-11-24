
import re
import unittest
from pathlib import Path

from extractor import extract_profile, clean, match, class_tag

BASE = Path(__file__).resolve().parent
HTML_DIR = BASE / "data" / "players"


class TestRegexHelpers(unittest.TestCase):

    def test_match(self):
        self.assertEqual(
            match(r"Rating:\s*(\d+)", "Rating: 2823"),
            "2823"
        )

    def test_match_missing(self):
        self.assertEqual(
            match(r"Rating:\s*(\d+)", "No rating"),
            ""
        )

    def test_clean_html(self):
        self.assertEqual(
            clean("<span>Magnus &amp; Carlsen</span>"),
            "Magnus & Carlsen"
        )

    def test_class_tag(self):
        html = '<span class="other prc-rank-n">#1</span>'
        self.assertEqual(
            class_tag(html, "span", "prc-rank-n"),
            "#1"
        )


class TestPlayerProfiles(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not HTML_DIR.is_dir():
            raise FileNotFoundError(
                f"HTML priecinok neexistuje: {HTML_DIR}"
            )

        cls.files = sorted(HTML_DIR.glob("*.html"))[:20]

        if len(cls.files) < 20:
            raise AssertionError(
                "treba aspon 20 profilov."
            )

    def test_20_html_profiles(self):
        for path in self.files:
            with self.subTest(profile=path.name):
                raw_html = path.read_text(encoding="utf-8")
                player, history = extract_profile(raw_html)

                self.assertEqual(
                    player["fide_id"],
                    path.stem,
                    "FIDE ID nesuhlasi s nazvom suboru"
                )

                self.assertTrue(
                    player["name"].strip(),
                    "Chyba meno hraca"
                )

                self.assertRegex(
                    player["fide_id"],
                    r"^\d+$"
                )

                if player["federation"]:
                    self.assertRegex(
                        player["federation"],
                        r"^[A-Z]{3}$"
                    )

                ratings = [
                    player["standard"],
                    player["rapid"],
                    player["blitz"]
                ]

                self.assertTrue(
                    any(ratings),
                    "Chybaju vsetky ratingy"
                )

                for rating in ratings:
                    if rating:
                        self.assertRegex(
                            rating,
                            r"^\d+$",
                            "Rating nema ciselny format"
                        )

                for row in history:
                    self.assertEqual(
                        row["fide_id"],
                        player["fide_id"]
                    )
                    self.assertRegex(
                        row["list_date"],
                        r"^\d{4}-\d{2}$"
                    )

    def test_missing_player_section(self):
        with self.assertRaises(ValueError):
            extract_profile("<html><body>Empty</body></html>")

    def test_missing_ratings(self):
        raw_html = """
        <section id="view-player"></section>
        <h2 id="playerName">Test Player</h2>
        <div class="profile-info-line">
          <a href="https://ratings.fide.com/profile/123456">
            123456
          </a>
        </div>
        """

        with self.assertRaises(ValueError):
            extract_profile(raw_html)


if __name__ == "__main__":
    unittest.main()
