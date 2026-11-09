import unittest
from pathlib import Path
from unittest.mock import patch

import lyrics_engine
from engine import filename_identity_hint, normalize_karaoke_line_timing
from lyrics_engine import LyricsCandidate, fetch_lyricsovh_title_only


class Stability485Tests(unittest.TestCase):
    def test_title_only_filename_is_preserved(self):
        hint = filename_identity_hint(Path("Yellow.mp3"))
        self.assertEqual(hint["title"], "Yellow")
        self.assertIsNone(hint["artist"])

    def test_line_timing_does_not_overlap(self):
        lines = [
            {"start": 1.0, "end": 4.0, "text": "line 1"},
            {"start": 3.2, "end": 5.0, "text": "line 2"},
            {"start": 4.8, "end": 6.0, "text": "line 3"},
        ]
        fixed = normalize_karaoke_line_timing(lines)
        for a, b in zip(fixed, fixed[1:]):
            self.assertLessEqual(a["end"], b["start"])

    def test_word_timing_is_clamped_to_line(self):
        lines = [{
            "start": 2.0,
            "end": 4.0,
            "text": "hello world",
            "words": [
                {"text": "hello", "start": 1.0, "end": 3.0},
                {"text": "world", "start": 2.5, "end": 5.0},
            ],
        }]
        fixed = normalize_karaoke_line_timing(lines)
        words = fixed[0]["words"]
        self.assertGreaterEqual(words[0]["start"], 2.0)
        self.assertLessEqual(words[-1]["end"], 4.0)

    @patch("lyrics_engine._lyricsovh_exact")
    @patch("lyrics_engine._lyricsovh_suggest")
    def test_title_only_can_infer_artist(self, suggest, exact):
        suggest.return_value = [{
            "title": "Yellow",
            "artist": {"name": "Coldplay"},
        }]
        exact.return_value = LyricsCandidate(
            lyrics="look at the stars",
            title="Yellow",
            artist="Coldplay",
            source="lyrics.ovh",
            licensed=False,
        )
        result = fetch_lyricsovh_title_only("Yellow")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.candidate.artist, "Coldplay")


if __name__ == "__main__":
    unittest.main()
