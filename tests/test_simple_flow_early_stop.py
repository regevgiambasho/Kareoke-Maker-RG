import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
from lyrics_engine import LyricsLookupResult


class SimpleFlowEarlyStopTests(unittest.TestCase):
    def test_no_reliable_lyrics_stops_before_separation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "song.mp4"
            source.write_bytes(b"dummy")
            project = root / "project"

            cfg = engine.BuildConfig(
                performance_mode="Fast",
                song_title="Test Song",
                artist_name="Test Artist",
                use_verified_lyrics=True,
                manual_lyrics_text=None,
            )

            def fake_extract(_source, target):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"wav")

            with (
                patch.object(engine, "ensure_tools"),
                patch.object(engine, "file_sha256", return_value="abc123"),
                patch.object(engine, "extract_audio", side_effect=fake_extract),
                patch.object(engine, "probe_media_identity", return_value={"title": None, "artist": None}),
                patch.object(engine, "filename_identity_hint", return_value={"title": None, "artist": None}),
                patch.object(engine, "_duration_seconds", return_value=180.0),
                patch.object(
                    engine,
                    "fetch_licensed_lyrics_with_status",
                    return_value=LyricsLookupResult(
                        candidate=None,
                        provider="lyrics.ovh",
                        status="not_found",
                        message="not found",
                    ),
                ),
                patch.object(engine, "upsert_project"),
                patch.object(engine, "_is_video", return_value=False),
                patch.object(engine, "separate_stems") as separate,
            ):
                result = engine.build_karaoke(
                    source,
                    project,
                    cfg,
                    stop_before_render=True,
                )

            self.assertEqual(result.lyrics_source, "none")
            separate.assert_not_called()
            manifest = engine._load_manifest(project)
            self.assertEqual(manifest.get("status"), "awaiting_lyrics_input")


if __name__ == "__main__":
    unittest.main()
