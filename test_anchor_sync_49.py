import unittest

from lyrics_engine import (
    align_verified_lyrics,
    parse_synced_lyrics_lines,
    synced_lyrics_compatible,
)


class AnchorSync49Tests(unittest.TestCase):
    def test_synced_duration_compatibility(self):
        self.assertTrue(synced_lyrics_compatible(240.0, 241.5))
        self.assertFalse(synced_lyrics_compatible(237.0, 254.0))

    def test_parse_lrc_preserves_line_start(self):
        lrc = (
            "[00:10.00] first line\n"
            "[00:14.50] second line\n"
            "[00:19.00] third line"
        )
        lines = parse_synced_lyrics_lines(lrc, media_duration=30.0, language="en")
        self.assertEqual(len(lines), 3)
        self.assertAlmostEqual(lines[0]["start"], 10.0, places=2)
        self.assertAlmostEqual(lines[1]["start"], 14.5, places=2)
        self.assertLess(lines[0]["end"], lines[1]["start"])

    def test_asr_words_are_only_timing_anchors(self):
        lyrics = "wake me up inside\ncall my name and save me"
        words = [
            {"text": "wake", "start": 10.0, "end": 10.3},
            {"text": "me", "start": 10.35, "end": 10.5},
            {"text": "inside", "start": 10.8, "end": 11.2},
            {"text": "call", "start": 14.0, "end": 14.25},
            {"text": "my", "start": 14.3, "end": 14.45},
            {"text": "name", "start": 14.5, "end": 14.8},
            {"text": "save", "start": 15.2, "end": 15.5},
            {"text": "me", "start": 15.55, "end": 15.7},
        ]
        result = align_verified_lyrics(lyrics, words, "en", source="manual", minimum_confidence=0.15)
        self.assertTrue(result.lines)
        self.assertEqual(result.lines[0]["text"], "wake me up inside")
        self.assertEqual(result.lines[1]["text"], "call my name and save me")
        self.assertGreater(result.lines[1]["start"], result.lines[0]["start"])


if __name__ == "__main__":
    unittest.main()
