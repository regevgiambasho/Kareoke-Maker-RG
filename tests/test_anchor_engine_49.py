import inspect
import unittest
import engine


class AnchorEngine49Tests(unittest.TestCase):
    def test_primary_build_does_not_use_forced_alignment_worker(self):
        source = inspect.getsource(engine.build_karaoke)
        self.assertNotIn("forced_align_worker(", source)
        self.assertIn("transcribe_worker(", source)

    def test_fast_mode_supports_faster_whisper_anchors(self):
        perf = engine.PERFORMANCE_PRESETS["Fast"]
        self.assertEqual(perf["transcribe_engine"], "faster-whisper")
        self.assertFalse(perf["align"])


if __name__ == "__main__":
    unittest.main()
