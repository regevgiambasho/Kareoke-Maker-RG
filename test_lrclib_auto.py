import unittest
from pathlib import Path

from engine import filename_identity_hint
from lyrics_engine import _lrclib_match_score, _lrclib_candidate_from_record


class LRCLIBAutoTests(unittest.TestCase):
    def test_filename_identity_hebrew(self):
        hint = filename_identity_hint(Path("עזבת_ת_בית - פלד.mp4"))
        self.assertEqual(hint["title"], "עזבת ת בית")
        self.assertEqual(hint["artist"], "פלד")

    def test_filename_identity_strips_karaoke_suffix(self):
        hint = filename_identity_hint(Path("יום חדש - רביד פלוטניק - קריוקי.mp4"))
        self.assertEqual(hint["title"], "יום חדש")
        self.assertEqual(hint["artist"], "רביד פלוטניק")

    def test_lrclib_candidate(self):
        record = {
            "id": 123,
            "trackName": "יום חדש",
            "artistName": "רביד פלוטניק",
            "duration": 200,
            "instrumental": False,
            "plainLyrics": "שורה אחת\\nשורה שתיים",
            "syncedLyrics": "[00:10.00] שורה אחת\\n[00:13.00] שורה שתיים",
        }
        candidate = _lrclib_candidate_from_record(record)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.source, "lrclib")
        self.assertIn("שורה אחת", candidate.lyrics)

    def test_match_score_rewards_duration(self):
        record = {
            "trackName": "יום חדש",
            "artistName": "רביד פלוטניק",
            "duration": 200,
            "instrumental": False,
        }
        close = _lrclib_match_score(record, "יום חדש", "רביד פלוטניק", 201)
        far = _lrclib_match_score(record, "יום חדש", "רביד פלוטניק", 260)
        self.assertGreater(close, far)


if __name__ == "__main__":
    unittest.main()
