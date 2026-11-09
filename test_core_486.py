import unittest
from engine import normalize_karaoke_line_timing


class Core486Tests(unittest.TestCase):
    def test_overlap_shrinks_previous_without_moving_next(self):
        lines = [
            {"start": 1.0, "end": 4.0, "text": "a"},
            {"start": 3.0, "end": 5.0, "text": "b"},
        ]
        fixed = normalize_karaoke_line_timing(lines)
        self.assertEqual(fixed[1]["start"], 3.0)
        self.assertLessEqual(fixed[0]["end"], fixed[1]["start"])

    def test_word_outside_line_is_clamped_not_line_expanded(self):
        lines = [{
            "start": 2.0,
            "end": 4.0,
            "text": "hello",
            "words": [{"text": "hello", "start": 1.0, "end": 5.0}],
        }]
        fixed = normalize_karaoke_line_timing(lines)
        self.assertEqual(fixed[0]["start"], 2.0)
        self.assertEqual(fixed[0]["end"], 4.0)
        self.assertGreaterEqual(fixed[0]["words"][0]["start"], 2.0)
        self.assertLessEqual(fixed[0]["words"][0]["end"], 4.0)


if __name__ == "__main__":
    unittest.main()
