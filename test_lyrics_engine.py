import unittest

from engine import apply_sync_offset, rotation_filter
from lyrics_engine import (
    _candidate_from_musixmatch_response,
    align_verified_lyrics,
    canonical_lines,
)


class LyricsEngineTests(unittest.TestCase):
    def test_hebrew_verified_alignment(self):
        lyrics = "שלום עולם\nזה רק טקסט בדיקה"
        heard = [
            {"text": "שלום", "start": 1.0, "end": 1.4},
            {"text": "עולם", "start": 1.5, "end": 1.9},
            {"text": "זה", "start": 2.5, "end": 2.7},
            {"text": "רק", "start": 2.75, "end": 2.9},
            {"text": "טקסט", "start": 3.0, "end": 3.3},
            {"text": "בדיקה", "start": 3.35, "end": 3.8},
        ]
        result = align_verified_lyrics(
            lyrics, heard, "he", "unit-test-provider"
        )
        self.assertTrue(result.verified)
        self.assertGreaterEqual(result.confidence, 0.95)
        self.assertEqual([x["text"] for x in result.lines], [
            "שלום עולם",
            "זה רק טקסט בדיקה",
        ])

    def test_long_lines_split_only_when_needed(self):
        short = "שורה קצרה וטבעית"
        self.assertEqual(canonical_lines(short, "he"), [short])

    def test_musixmatch_parser_accepts_complete_response(self):
        data = {
            "message": {
                "header": {"status_code": 200},
                "body": {
                    "lyrics": {
                        "lyrics_body": "first invented line\nsecond invented line",
                        "restricted": 0,
                    }
                },
            }
        }
        candidate = _candidate_from_musixmatch_response(
            data, "Test Song", "Test Artist"
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.source, "musixmatch")

    def test_musixmatch_parser_rejects_truncated_response(self):
        data = {
            "message": {
                "header": {"status_code": 200},
                "body": {
                    "lyrics": {
                        "lyrics_body": (
                            "first invented line\n"
                            "******* This Lyrics is NOT for Commercial use *******"
                        ),
                        "restricted": 0,
                    }
                },
            }
        }
        self.assertIsNone(
            _candidate_from_musixmatch_response(
                data, "Test Song", "Test Artist"
            )
        )

    def test_rotation_filter_mapping(self):
        self.assertIsNone(rotation_filter(0))
        self.assertEqual(rotation_filter(90), "transpose=clock")
        self.assertEqual(rotation_filter(180), "hflip,vflip")
        self.assertEqual(rotation_filter(270), "transpose=cclock")

    def test_sync_offset_moves_words_and_lines(self):
        lines = [{
            "start": 3.0,
            "end": 5.0,
            "text": "invented words",
            "words": [
                {"text": "invented", "start": 3.0, "end": 4.0},
                {"text": "words", "start": 4.0, "end": 5.0},
            ],
        }]
        adjusted = apply_sync_offset(lines, -2.0)
        self.assertAlmostEqual(adjusted[0]["start"], 1.0)
        self.assertAlmostEqual(adjusted[0]["end"], 3.0)
        self.assertAlmostEqual(adjusted[0]["words"][0]["start"], 1.0)
        self.assertAlmostEqual(adjusted[0]["words"][1]["end"], 3.0)


if __name__ == "__main__":
    unittest.main()
