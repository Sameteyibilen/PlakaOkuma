"""Camera previews must be atomically published as readable JPEG files."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import live_plate_ocr as ocr


class WatchPreviewTests(unittest.TestCase):
    def test_preview_file_is_readable_and_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "frames"
            with patch.object(ocr, "OUT_DIR", out):
                ocr.write_watch("153", np.full((1080, 1920, 3), 80, np.uint8))
            path = out / "watch_153.jpg"
            self.assertTrue(path.is_file())
            image = cv2.imread(str(path))
            self.assertEqual(image.shape[:2], (540, 960))
            self.assertEqual(list(out.glob("*.tmp.jpg")), [])

    def test_missing_frame_does_not_overwrite_last_image(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            with patch.object(ocr, "OUT_DIR", out):
                ocr.write_watch("165", np.full((100, 200, 3), 80, np.uint8))
                original = (out / "watch_165.jpg").read_bytes()
                ocr.write_watch("165", None)
                self.assertEqual((out / "watch_165.jpg").read_bytes(), original)
