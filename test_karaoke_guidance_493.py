import inspect
import json
import tempfile
import unittest
from pathlib import Path

import engine
import version


class KaraokeGuidance493Tests(unittest.TestCase):
    def _ass(self, lines, language="en"):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "karaoke.ass"
            engine.create_ass(lines, target, language, dict(engine.STYLE_PRESETS["Classic"]))
            return target.read_text(encoding="utf-8")

    def test_version_is_493(self):
        self.assertEqual(version.APP_VERSION, "2.0.0-alpha.4.9.3")

    def test_highlight_leads_first_word_by_280ms(self):
        ass = self._ass([{
            "start": 5.0, "end": 6.0, "text": "Hello world",
            "words": [
                {"text": "Hello", "start": 5.0, "end": 5.5},
                {"text": "world", "start": 5.5, "end": 6.0},
            ],
        }])
        self.assertIn("Dialogue: 1,0:00:04.72,0:00:05.22,Karaoke", ass)
        self.assertIn("Dialogue: 1,0:00:05.22,0:00:05.72,Karaoke", ass)

    def test_next_line_preview_is_present_for_whole_active_line(self):
        ass = self._ass([
            {
                "start": 1.0, "end": 4.0, "text": "current line",
                "words": [
                    {"text": "current", "start": 1.0, "end": 2.0},
                    {"text": "line", "start": 2.0, "end": 4.0},
                ],
            },
            {
                "start": 4.3, "end": 6.0, "text": "next line",
                "words": [
                    {"text": "next", "start": 4.3, "end": 5.0},
                    {"text": "line", "start": 5.0, "end": 6.0},
                ],
            },
        ])
        self.assertIn(
            "Dialogue: 0,0:00:00.72,0:00:04.00,KaraokePreview,,0,0,0,,{\\an5\\pos(640.0,635.0)}next line",
            ass,
        )

    def test_intro_countdown_is_visible_for_three_second_intro(self):
        ass = self._ass([{
            "start": 6.5, "end": 8.0, "text": "start now",
            "words": [
                {"text": "start", "start": 6.5, "end": 7.1},
                {"text": "now", "start": 7.1, "end": 8.0},
            ],
        }])
        events = [line for line in ass.splitlines() if ",KaraokeCountdown," in line]
        self.assertEqual(len(events), 3)
        self.assertTrue(events[0].endswith("}3"))
        self.assertTrue(events[1].endswith("}2"))
        self.assertTrue(events[2].endswith("}1"))
        self.assertIn("0:00:06.22,KaraokeCountdown", events[-1])

    def test_bad_zero_word_timestamp_cannot_disable_intro_countdown(self):
        ass = self._ass([{
            "start": 6.5, "end": 8.0, "text": "start now",
            "words": [
                {"text": "start", "start": 0.0, "end": 0.4},
                {"text": "now", "start": 7.1, "end": 8.0},
            ],
        }])
        self.assertEqual(sum(",KaraokeCountdown," in x for x in ass.splitlines()), 3)

    def test_renderer_signature_invalidates_old_subtitle_cache(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            output = project / "karaoke.ass"
            output.write_text("old", encoding="utf-8")
            (project / "manifest.json").write_text(json.dumps({
                "stages": {"subtitles": {"status": "done", "signature": "old-renderer"}}
            }), encoding="utf-8")
            self.assertFalse(engine._stage_done(
                project, "subtitles", [output],
                expected_signature=engine.SUBTITLE_RENDERER_SIGNATURE,
            ))

    def test_current_renderer_signature_allows_cache(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            output = project / "karaoke.ass"
            output.write_text("new", encoding="utf-8")
            (project / "manifest.json").write_text(json.dumps({
                "stages": {"subtitles": {
                    "status": "done",
                    "signature": engine.SUBTITLE_RENDERER_SIGNATURE,
                }}
            }), encoding="utf-8")
            self.assertTrue(engine._stage_done(
                project, "subtitles", [output],
                expected_signature=engine.SUBTITLE_RENDERER_SIGNATURE,
            ))

    def test_startup_audio_has_no_fade(self):
        source = inspect.getsource(engine.render_video)
        self.assertNotIn("afade=", source)
        self.assertIn("aresample=async=1:first_pts=0", source)
        self.assertIn("setpts=PTS-STARTPTS", source)

    def test_video_fidelity_settings_are_high_quality(self):
        for preset in engine.PERFORMANCE_PRESETS.values():
            self.assertEqual(preset["render_preset"], "medium")
            self.assertEqual(preset["render_crf"], 14)
        source = inspect.getsource(engine.render_video)
        self.assertIn('"-tune", "film"', source)
        self.assertNotIn('"-r",', source)
        self.assertNotIn('scale=', source)

    def test_493_signatures_force_visual_rebuild(self):
        self.assertIn("4.9.3", engine.SUBTITLE_RENDERER_SIGNATURE)
        self.assertIn("4.9.3", engine.VIDEO_RENDER_SIGNATURE)


if __name__ == "__main__":
    unittest.main()
