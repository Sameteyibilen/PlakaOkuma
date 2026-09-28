#!/usr/bin/env python3
"""153 takılmasın: okuma süresi dolunca yavaş yedek kesilir."""

from __future__ import annotations

import time
import unittest

import numpy as np

from live_plate_ocr import read_plates


class OcrDeadlineTests(unittest.TestCase):
    def test_deadline_returns_quickly(self) -> None:
        rng = np.random.default_rng(7)
        frame = rng.integers(30, 200, size=(480, 640, 3), dtype=np.uint8)
        t0 = time.time()
        got = read_plates(None, frame, seated=False, deadline=0.05)
        self.assertLess(time.time() - t0, 1.2)
        self.assertIsInstance(got, list)

    def test_approach_skips_slow_path(self) -> None:
        rng = np.random.default_rng(9)
        frame = rng.integers(20, 220, size=(720, 960, 3), dtype=np.uint8)
        t0 = time.time()
        read_plates(None, frame, seated=False, deadline=0.25)
        self.assertLess(time.time() - t0, 2.0)
