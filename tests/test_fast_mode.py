import unittest

from engine import BuildConfig, PERFORMANCE_PRESETS


class FastModeTests(unittest.TestCase):
    def test_fast_is_karaoke_separator(self):
        perf = PERFORMANCE_PRESETS["Fast"]
        self.assertEqual(perf["separation_model"], "UVR_MDXNET_KARA_2.onnx")

    def test_fast_uses_direct_faster_whisper(self):
        perf = PERFORMANCE_PRESETS["Fast"]
        self.assertEqual(perf["transcribe_engine"], "faster-whisper")
        self.assertFalse(perf["align"])
        self.assertGreaterEqual(perf["asr_batch_size"], 4)

    def test_fast_render_uses_high_fidelity_encoder(self):
        resolved = BuildConfig(performance_mode="Fast").resolved()
        self.assertEqual(resolved["performance"]["render_preset"], "medium")
        self.assertEqual(resolved["performance"]["render_crf"], 14)
        self.assertGreaterEqual(resolved["performance"]["threads"], 2)


if __name__ == "__main__":
    unittest.main()
