import unittest
from pathlib import Path


class AutoRenderFlowTests(unittest.TestCase):
    def test_main_app_does_not_stop_before_render(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        text = app_path.read_text(encoding="utf-8")
        self.assertNotIn("stop_before_render=True", text)

    def test_review_stage_removed_from_stagebar(self):
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        text = app_path.read_text(encoding="utf-8")
        self.assertNotIn(
            '"Sync", "Review lyrics", "Subtitles"',
            text,
        )


if __name__ == "__main__":
    unittest.main()
