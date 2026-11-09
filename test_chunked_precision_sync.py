import unittest
import numpy as np

from forced_align_worker import (
    group_lyrics,
    assign_chunk_windows,
)


class ChunkedPrecisionSyncTests(unittest.TestCase):
    def test_long_lyrics_split_into_multiple_chunks(self):
        lines = [
            f"this is lyric line number {i} with several words"
            for i in range(30)
        ]
        chunks = group_lyrics(lines, target_words=30, max_lines=5)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(c["words"] > 0 for c in chunks))

    def test_windows_are_bounded_and_ordered(self):
        sr = 16000
        duration = 120
        audio = np.zeros(sr * duration, dtype=np.float32)

        # Simulated vocals from 10s to 110s.
        audio[sr * 10:sr * 110] = 0.05

        chunks = [
            {"text": "one two three", "lines": ["one two three"], "words": 3},
            {"text": "four five six", "lines": ["four five six"], "words": 3},
            {"text": "seven eight nine", "lines": ["seven eight nine"], "words": 3},
        ]

        windows = assign_chunk_windows(chunks, audio, sr=sr, pad_seconds=5)
        self.assertEqual(len(windows), 3)
        self.assertGreaterEqual(windows[0]["start"], 0)
        self.assertLessEqual(windows[-1]["end"], duration)
        self.assertLess(
            windows[0]["rough_start"],
            windows[-1]["rough_start"],
        )


if __name__ == "__main__":
    unittest.main()
