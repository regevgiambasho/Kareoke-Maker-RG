import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class UploadHelperTests(unittest.TestCase):
    def test_route_module_helpers(self):
        import upload_routes

        self.assertTrue(upload_routes._safe_filename("../hello.mp4").endswith("hello.mp4"))
        self.assertFalse(upload_routes._valid_upload_id("not-an-id"))

    def test_resolve_rejects_invalid_id(self):
        import upload_routes

        self.assertIsNone(upload_routes.resolve_completed_upload("bad"))


if __name__ == "__main__":
    unittest.main()
