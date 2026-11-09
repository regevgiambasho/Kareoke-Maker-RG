import unittest

from lyrics_engine import parse_musixmatch_url_hint, _track_match_score


class MusixmatchIdentityTests(unittest.TestCase):
    def test_public_url_identity_hint(self):
        parsed = parse_musixmatch_url_hint(
            "https://www.musixmatch.com/lyrics/Ravid-Plotnik/%D7%99%D7%95%D7%9D-%D7%97%D7%93%D7%A9-%D7%A0%D7%A6-%D7%99-%D7%A0%D7%A6"
        )
        self.assertEqual(parsed["artist"], "Ravid Plotnik")
        self.assertTrue(parsed["title"].startswith("יום חדש"))

    def test_track_match_prefers_exact_identity(self):
        exact = {
            "track_name": "יום חדש",
            "artist_name": "Ravid Plotnik",
            "has_lyrics": 1,
            "instrumental": 0,
        }
        wrong = {
            "track_name": "יום ישן",
            "artist_name": "Someone Else",
            "has_lyrics": 1,
            "instrumental": 0,
        }
        self.assertGreater(
            _track_match_score(exact, "יום חדש", "Ravid Plotnik"),
            _track_match_score(wrong, "יום חדש", "Ravid Plotnik"),
        )


if __name__ == "__main__":
    unittest.main()
