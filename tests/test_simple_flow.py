import unittest
from pathlib import Path

import engine


class SimpleFlowTests(unittest.TestCase):
    def test_default_requires_reliable_lyrics(self):
        cfg = engine.BuildConfig()
        self.assertTrue(cfg.require_verified_lyrics)
        self.assertTrue(cfg.use_verified_lyrics)

    def test_forced_alignment_worker_exists(self):
        self.assertTrue(
            Path(engine.__file__).with_name("forced_align_worker.py").exists()
        )

    def test_fast_mode_still_uses_karaoke_separator(self):
        perf = engine.PERFORMANCE_PRESETS["Fast"]
        self.assertEqual(perf["separation_model"], "UVR_MDXNET_KARA_2.onnx")


if __name__ == "__main__":
    unittest.main()
