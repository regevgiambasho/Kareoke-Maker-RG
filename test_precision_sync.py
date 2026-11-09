import unittest
from pathlib import Path

import engine


class PrecisionSyncTests(unittest.TestCase):
    def test_forced_alignment_worker_exists(self):
        self.assertTrue(Path(engine.__file__).with_name("forced_align_worker.py").exists())

    def test_fast_mode_stays_fast(self):
        perf = engine.PERFORMANCE_PRESETS["Fast"]
        self.assertEqual(perf["separation_model"], "UVR_MDXNET_KARA_2.onnx")
        self.assertFalse(perf["align"])


if __name__ == "__main__":
    unittest.main()
