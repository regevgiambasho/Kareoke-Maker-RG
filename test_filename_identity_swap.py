import unittest
from pathlib import Path

from engine import filename_identity_hint


class FilenameIdentitySwapTests(unittest.TestCase):
    def test_filename_parser_keeps_two_parts_available(self):
        hint = filename_identity_hint(Path("Coldplay - Yellow.mp3"))
        # Initial interpretation remains Song - Artist; the build pipeline
        # now retries the swapped interpretation if lookup fails.
        self.assertEqual(hint["title"], "Coldplay")
        self.assertEqual(hint["artist"], "Yellow")


if __name__ == "__main__":
    unittest.main()
