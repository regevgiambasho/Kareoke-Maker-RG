import unittest
from unittest.mock import patch

import lyrics_engine
from lyrics_engine import (
    LyricsCandidate,
    automatic_provider_order,
    fetch_lyricsovh_lyrics,
    _lyricsovh_suggestion_score,
)


class LyricsOvhProviderTests(unittest.TestCase):
    def test_hebrew_order_without_paid_provider(self):
        order = automatic_provider_order(
            title="יום חדש",
            artist="רביד פלוטניק",
            language="he",
            musixmatch_configured=False,
            generic_configured=False,
        )
        self.assertEqual(order, ["lyrics.ovh", "lrclib"])

    def test_hebrew_order_with_musixmatch(self):
        order = automatic_provider_order(
            title="יום חדש",
            artist="רביד פלוטניק",
            language="he",
            musixmatch_configured=True,
            generic_configured=False,
        )
        self.assertEqual(order[0], "musixmatch")
        self.assertEqual(order[1:], ["lyrics.ovh", "lrclib"])

    def test_suggestion_score_prefers_matching_identity(self):
        good = {
            "title": "יום חדש",
            "artist": {"name": "Ravid Plotnik"},
        }
        bad = {
            "title": "Something Else",
            "artist": {"name": "Different Artist"},
        }
        self.assertGreater(
            _lyricsovh_suggestion_score(good, "יום חדש", "Ravid Plotnik"),
            _lyricsovh_suggestion_score(bad, "יום חדש", "Ravid Plotnik"),
        )

    @patch("lyrics_engine._lyricsovh_exact")
    def test_exact_hit_returns_immediately(self, exact):
        exact.return_value = LyricsCandidate(
            lyrics="line one\nline two",
            title="Song",
            artist="Artist",
            source="lyrics.ovh",
            licensed=False,
        )
        result = fetch_lyricsovh_lyrics("Song", "Artist")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.provider, "lyrics.ovh")
        self.assertIsNotNone(result.candidate)


if __name__ == "__main__":
    unittest.main()
