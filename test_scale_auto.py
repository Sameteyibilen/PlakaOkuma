#!/usr/bin/env python3
"""ScaleLane senaryoları 36–43 — kamera/IFS yok."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scale_auto
from scale_auto import DIRECTION, ScaleLane, ScaleState
from visits import JsonVisitRepository, VisitStatus, VisitStore

SETTINGS = {
    "empty": 250.0,
    "clear_sec": 2.0,
    "stable_n": 3,
    "stable_tol": 80.0,
    "stable_sec": 1.5,
    "ocr_window": 20.0,
    "ocr_timeout": 45.0,
    "ocr_min": 2,
    "ocr_min_conf": 0.0,
    "match_window": 45.0,
    "stab_timeout": 90.0,
    "enter_timeout": 60.0,
    "clear_timeout": 180.0,
    "debug": True,
}


def kg(value: float) -> dict:
    return {"kg": value, "empty": value <= SETTINGS["empty"]}


class ScaleAutoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = patch.object(scale_auto, "_settings", lambda: SETTINGS)
        self.patcher.start()
        self.lane = ScaleLane("153")

    def tearDown(self) -> None:
        self.patcher.stop()

    def _boot(self, t: float = 0.0) -> float:
        self.lane.on_weight(kg(0), t)
        self.assertEqual(self.lane.state, ScaleState.SCALE_EMPTY)
        self.assertTrue(self.lane.started)
        return t

    def _plate(self, plate: str, t: float, conf: float | None = 0.96) -> None:
        self.lane.on_plate(plate=plate, raw=plate.replace(" ", ""), confidence=conf, image_path="", now=t)

    def _rise_and_stable(self, start: float, seated: float = 42680) -> tuple[float, list]:
        seq = [4200, 12600, 28100, 40100, 42610, 42660, seated, seated - 10, seated, seated]
        collected: list = []
        t = start
        for i, value in enumerate(seq):
            t = start + i * 0.4
            collected.extend(self.lane.on_weight(kg(value), t))
        t += 1.6
        collected.extend(self.lane.on_weight(kg(seated), t))
        return t, collected

    def test_direction_reuses_existing_cameras(self) -> None:
        self.assertEqual(DIRECTION["153"], "ENTRY")
        self.assertEqual(DIRECTION["165"], "EXIT")
        self.assertEqual(self.lane.direction, "ENTRY")
        self.assertEqual(ScaleLane("165").direction, "EXIT")

    def test_36_normal_entry_one_capture(self) -> None:
        t = self._boot()
        self._plate("77 ADN 856", t + 1, 0.96)
        self._plate("77 ADN 856", t + 2, 0.94)
        self._plate("77 ADN 858", t + 3, 0.61)
        self._plate("77 ADN 856", t + 4, 0.97)
        self.assertEqual(self.lane.state, ScaleState.PLATE_CANDIDATE)
        t, acts = self._rise_and_stable(t + 5)
        captures = [a for a in acts if a.kind == "capture"]
        self.assertEqual(len(captures), 1)
        cap = captures[0]
        self.assertEqual(cap.plate, "77 ADN 856")
        self.assertAlmostEqual(cap.kg or 0, 42680, delta=20)
        self.assertTrue(self.lane.captured)
        self.assertIn(self.lane.state, {ScaleState.WAITING_SCALE_CLEAR, ScaleState.WEIGHT_CAPTURED})
        cycle = self.lane.cycle_id
        extra = []
        for i in range(8):
            extra.extend(self.lane.on_weight(kg(42680), t + 1 + i * 0.3))
        self.assertEqual(sum(1 for a in extra if a.kind == "capture"), 0)
        self.assertEqual(self.lane.cycle_id, cycle)
        leave = [30000, 15000, 4200, 300, 100, 0, 0, 0]
        t2 = t + 6
        for i, value in enumerate(leave):
            self.lane.on_weight(kg(value), t2 + i)
        self.lane.on_weight(kg(0), t2 + len(leave) + 2.1)
        self.assertEqual(self.lane.state, ScaleState.SCALE_EMPTY)
        self.assertFalse(self.lane.captured)
        self.assertNotEqual(self.lane.cycle_id, cycle)

    def test_37_normal_exit_net(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        store = VisitStore(JsonVisitRepository(tmp / "visits.json", tmp / "audit.json"))
        created = store.on_entry(plate="77 ADN 856", raw_plate="77ADN856", camera="153", full_weight=42680)
        vid = created["visit"]["visit_id"]
        lane = ScaleLane("165")
        lane.open_visit_keys = lambda: store.open_plate_keys()
        lane.on_weight(kg(0), 0.0)
        lane.on_plate(plate="77 ADN 856", raw="77ADN856", confidence=0.95, image_path="", now=1.0)
        lane.on_plate(plate="77 ADN 856", raw="77ADN856", confidence=0.94, image_path="", now=2.0)
        t, acts = _rise(lane, 5.0, 15240)
        captures = [a for a in acts if a.kind == "capture"]
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].plate, "77 ADN 856")
        self.assertAlmostEqual(captures[0].kg or 0, 15240, delta=20)
        res = store.on_exit(
            plate=captures[0].plate or "",
            raw_plate=captures[0].raw_plate,
            camera="165",
            empty_weight=captures[0].kg,
        )
        visit = res["visit"]
        self.assertEqual(visit["visit_id"], vid)
        self.assertEqual(visit["empty_weight"], captures[0].kg)
        self.assertEqual(visit["net_weight"], round(42680 - float(captures[0].kg), 1))
        self.assertEqual(visit["status"], VisitStatus.COMPLETED)

    def test_38_plate_missing_waits_then_review(self) -> None:
        self._boot()
        t, acts = self._rise_and_stable(1.0)
        self.assertEqual(sum(1 for a in acts if a.kind == "review"), 0)
        self.assertEqual(self.lane.state, ScaleState.WEIGHT_STABLE)
        later = self.lane.on_weight(kg(42680), t + 46.0)
        later += self.lane.tick(t + 46.0)
        reviews = [a for a in later if a.kind == "review"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].reason, "PLATE_NOT_DETECTED")
        self.assertAlmostEqual(reviews[0].kg or 0, 42680, delta=20)
        self.assertEqual(self.lane.state, ScaleState.MANUAL_REVIEW)
        self.assertEqual(sum(1 for a in later if a.kind == "capture"), 0)

    def test_leave_before_ocr_timeout_still_reviews(self) -> None:
        self._boot()
        t, acts = self._rise_and_stable(1.0)
        self.assertEqual(self.lane.state, ScaleState.WEIGHT_STABLE)
        self.assertEqual(sum(1 for a in acts if a.kind == "review"), 0)
        leave = [30000, 15000, 4200, 300, 80, 0]
        later: list = []
        for i, value in enumerate(leave):
            later.extend(self.lane.on_weight(kg(value), t + 1.0 + i))
        later.extend(self.lane.on_weight(kg(0), t + 1.0 + len(leave) + 2.1))
        reviews = [a for a in later if a.kind == "review"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].reason, "PLATE_NOT_DETECTED")
        self.assertAlmostEqual(reviews[0].kg or 0, 42680, delta=80)
        self.assertEqual(self.lane.state, ScaleState.SCALE_EMPTY)

    def test_plate_arrives_after_stable_captures(self) -> None:
        self._boot()
        t, acts = self._rise_and_stable(1.0)
        self.assertEqual(self.lane.state, ScaleState.WEIGHT_STABLE)
        self.assertEqual(sum(1 for a in acts if a.kind == "review"), 0)
        more = self.lane.on_plate(
            plate="77 ADN 856",
            raw="77ADN856",
            confidence=0.95,
            image_path="",
            now=t + 2.0,
        )
        captures = [a for a in more if a.kind == "capture"]
        self.assertEqual(len(captures), 1)
        self.assertEqual(captures[0].plate, "77 ADN 856")

    def test_39_plate_without_vehicle_expires(self) -> None:
        self._boot()
        self._plate("16 ABC 123", 1.0)
        self.assertEqual(self.lane.state, ScaleState.PLATE_CANDIDATE)
        cycle = self.lane.cycle_id
        acts = self.lane.on_weight(kg(0), 1.0 + 46.0)
        acts += self.lane.tick(1.0 + 46.0)
        self.assertTrue(any(a.kind == "reset" for a in acts) or self.lane.state == ScaleState.SCALE_EMPTY)
        self.assertEqual(self.lane.state, ScaleState.SCALE_EMPTY)
        self.assertEqual(sum(1 for a in acts if a.kind == "capture"), 0)
        self.assertNotEqual(self.lane.cycle_id, cycle)

    def test_40_comm_error_keeps_kg(self) -> None:
        self._boot()
        self._plate("77 ADN 856", 1.0)
        self._plate("77 ADN 856", 2.0)
        self.lane.on_weight(kg(4200), 3.0)
        self.lane.on_weight(kg(18000), 4.0)
        self.lane.on_weight(kg(42000), 5.0)
        self.assertNotEqual(self.lane.state, ScaleState.SCALE_EMPTY)
        acts = self.lane.on_weight(None, 6.0)
        self.assertEqual(self.lane.state, ScaleState.COMM_ERROR)
        self.assertEqual(self.lane.last_ok_kg, 42000)
        self.assertIsNone(self.lane.last_kg)
        self.assertTrue(any(a.kind == "comm_error" for a in acts))
        self.lane.on_weight(kg(42000), 7.0)
        self.assertNotEqual(self.lane.state, ScaleState.COMM_ERROR)
        self.assertNotEqual(self.lane.state, ScaleState.SCALE_EMPTY)
        self.assertEqual(self.lane.last_ok_kg, 42000)

    def test_41_restart_occupied_no_auto_capture(self) -> None:
        lane = ScaleLane("153")
        acts = lane.on_weight(kg(42680), 0.0)
        self.assertEqual(lane.state, ScaleState.RECOVERY)
        self.assertEqual(lane.review_reason, "RECOVERY_OCCUPIED")
        self.assertEqual(sum(1 for a in acts if a.kind == "capture"), 0)
        self.assertEqual(sum(1 for a in acts if a.kind == "review"), 1)
        self.assertEqual(acts[0].reason, "RECOVERY_OCCUPIED")
        self.assertTrue(lane.want_ocr())

    def test_want_ocr_during_comm_error(self) -> None:
        t = self._boot()
        acts = self.lane.on_weight(None, t + 2)
        self.assertEqual(self.lane.state, ScaleState.COMM_ERROR)
        self.assertTrue(any(a.kind == "comm_error" for a in acts))
        self.assertTrue(self.lane.want_ocr())
        self.assertFalse(self.lane.captured)

    def test_42_ambiguous_two_plates(self) -> None:
        self._boot()
        self._plate("77 ADN 856", 1.0, 0.95)
        self._plate("77 ADN 856", 1.5, 0.94)
        self._plate("16 ABC 123", 1.2, 0.93)
        self._plate("16 ABC 123", 1.7, 0.92)
        t, acts = self._rise_and_stable(3.0)
        reviews = [a for a in acts if a.kind == "review"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].reason, "AMBIGUOUS_PLATE")
        self.assertEqual(sum(1 for a in acts if a.kind == "capture"), 0)
        self.assertEqual(self.lane.state, ScaleState.MANUAL_REVIEW)

    def test_43_ocr_repeat_single_cycle(self) -> None:
        self._boot()
        for i in range(30):
            self._plate("77 ADN 856", 1.0 + i * 0.05, 0.9)
        scored = self.lane._score_candidates(now=3.0)
        self.assertEqual(len(scored), 1)
        t, acts = self._rise_and_stable(4.0)
        self.assertEqual(sum(1 for a in acts if a.kind == "capture"), 1)
        self.assertEqual(len({self.lane.cycle_id}), 1)
        for i in range(6):
            more = self.lane.on_weight(kg(42680), t + 1 + i * 0.2)
            self.assertEqual(sum(1 for a in more if a.kind == "capture"), 0)

    def test_empty_not_single_zero(self) -> None:
        self._boot()
        self._plate("77 ADN 856", 1.0)
        self._plate("77 ADN 856", 1.5)
        self.lane.on_weight(kg(12000), 2.0)
        self.lane.on_weight(kg(20000), 3.0)
        self.lane.on_weight(kg(0), 3.2)
        self.assertNotEqual(self.lane.state, ScaleState.SCALE_EMPTY)
        self.lane.on_weight(kg(20000), 3.4)
        self.assertIn(
            self.lane.state,
            {ScaleState.VEHICLE_ENTERING, ScaleState.WEIGHT_RISING, ScaleState.WEIGHT_STABILIZING},
        )


def _rise(lane: ScaleLane, start: float, seated: float) -> tuple[float, list]:
    seq = [4200, 8000, 12000, seated - 40, seated - 20, seated, seated, seated]
    collected: list = []
    t = start
    for i, value in enumerate(seq):
        t = start + i * 0.4
        collected.extend(lane.on_weight(kg(value), t))
    t += 1.6
    collected.extend(lane.on_weight(kg(seated), t))
    return t, collected


if __name__ == "__main__":
    unittest.main()
