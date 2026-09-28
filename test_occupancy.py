#!/usr/bin/env python3
"""Boş kantar / sis / ışık araç sayılmasın."""

from __future__ import annotations

import unittest

import numpy as np

from live_plate_ocr import (
    COVER_MIN,
    deck_occupied,
    deck_view,
    frame_usable,
    gray_usable,
    vehicle_cover,
)


def _bgr(h: int, w: int, v: int) -> np.ndarray:
    return np.full((h, w, 3), v, dtype=np.uint8)


def _concrete(h: int = 240, w: int = 420, base: int = 110) -> np.ndarray:
    rng = np.random.default_rng(3)
    noise = rng.integers(-7, 8, size=(h, w, 1), dtype=np.int16)
    g = np.clip(np.full((h, w, 1), base, dtype=np.int16) + noise, 0, 255).astype(
        np.uint8
    )
    return np.repeat(g, 3, axis=2)


class OccupancyTests(unittest.TestCase):
    def test_flat_gray_not_usable(self) -> None:
        flat = np.full((80, 120), 128, dtype=np.uint8)
        self.assertFalse(gray_usable(flat))

    def test_blank_frame_rejected(self) -> None:
        self.assertFalse(frame_usable(_bgr(200, 300, 250)))

    def test_same_empty_not_vehicle(self) -> None:
        empty = _concrete()
        ref = deck_view(empty, "165")
        cover, hit = deck_occupied(empty, ref, "165")
        self.assertLess(cover, COVER_MIN["165"])
        self.assertFalse(hit)

    def test_exposure_flicker_not_vehicle(self) -> None:
        empty = _concrete(base=100)
        bright = np.clip(empty.astype(np.int16) + 45, 0, 255).astype(np.uint8)
        ref = deck_view(empty, "165")
        cover, hit = deck_occupied(bright, ref, "165")
        self.assertFalse(hit)
        self.assertLess(vehicle_cover(bright, ref, "165"), 40.0)

    def test_truck_blob_is_vehicle(self) -> None:
        empty = _concrete()
        truck = empty.copy()
        truck[70:210, 40:380] = 28
        truck[80:120, 60:160] = 200
        truck[150:190, 200:340] = 18
        ref = deck_view(empty, "165")
        cover, hit = deck_occupied(truck, ref, "165")
        self.assertGreaterEqual(cover, COVER_MIN["165"])
        self.assertTrue(hit)

    def test_small_person_not_vehicle(self) -> None:
        empty = _concrete()
        scene = empty.copy()
        scene[100:130, 200:220] = 20
        ref = deck_view(empty, "165")
        _cover, hit = deck_occupied(scene, ref, "165")
        self.assertFalse(hit)


if __name__ == "__main__":
    unittest.main()
