#!/usr/bin/env python3
"""VisitStore senaryoları — kamera/IFS yok."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from visits import (
    REVIEW_INVALID_NET,
    REVIEW_MULTIPLE_OPEN,
    REVIEW_OPEN_NOT_FOUND,
    JsonVisitRepository,
    VisitStatus,
    VisitStore,
)


def store_at(folder: Path) -> VisitStore:
    return VisitStore(
        JsonVisitRepository(folder / "visits.json", folder / "audit.json")
    )


class VisitStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = store_at(self.tmp)

    def test_entry_exit_net_and_duplicate(self) -> None:
        a = self.store.on_entry(plate="77 ADN 856", raw_plate="77ADN856", camera="153", full_weight=42680)
        self.assertEqual(a["action"], "create")
        vid = a["visit"]["visit_id"]
        self.assertEqual(a["visit"]["full_weight"], 42680)
        for _ in range(10):
            again = self.store.on_entry(plate="77ADN856", camera="153", full_weight=42680)
            self.assertEqual(again["action"], "reuse")
            self.assertEqual(again["visit"]["visit_id"], vid)
        self.assertEqual(len(self.store.open_visits("77 ADN 856")), 1)
        self.store.attach_irsaliye(visit_id=vid, irsaliye_id="IRS-12345", irsaliye_no="IRS-12345", irsaliye_source="secim")
        nxt = self.store.on_exit(plate="77 ADN 856", camera="165", empty_weight=15240)
        self.assertEqual(nxt["action"], "complete")
        v = nxt["visit"]
        self.assertEqual(v["empty_weight"], 15240)
        self.assertEqual(v["net_weight"], 27440)
        self.assertEqual(v["status"], VisitStatus.COMPLETED)
        self.assertEqual(v["gate"], "COMPLETED")

    def test_exit_without_open(self) -> None:
        r = self.store.on_exit(plate="16 ABC 123", camera="165", empty_weight=15240)
        self.assertEqual(r["reason"], REVIEW_OPEN_NOT_FOUND)
        self.assertEqual(r["visit"]["status"], VisitStatus.MANUAL_REVIEW)
        self.assertEqual(r["visit"]["empty_weight"], 15240)

    def test_exit_truncated_matches_open(self) -> None:
        created = self.store.on_entry(
            plate="16 BLZ 931", raw_plate="16BLZ931", camera="153", full_weight=17620
        )
        self.assertEqual(created["action"], "create")
        vid = created["visit"]["visit_id"]
        nxt = self.store.on_exit(plate="16 BLZ 93", camera="165", empty_weight=13680)
        self.assertEqual(nxt["action"], "complete")
        self.assertEqual(nxt["visit"]["visit_id"], vid)
        self.assertEqual(nxt["visit"]["empty_weight"], 13680)
        self.assertEqual(nxt["visit"]["status"], VisitStatus.COMPLETED)

    def test_exit_letter_stem_matches_open(self) -> None:
        created = self.store.on_entry(
            plate="16 NUB 82", raw_plate="16NUB82", camera="153", full_weight=47020
        )
        vid = created["visit"]["visit_id"]
        nxt = self.store.on_exit(plate="16NUB", camera="165", empty_weight=15240)
        self.assertEqual(nxt["action"], "complete")
        self.assertEqual(nxt["visit"]["visit_id"], vid)
        self.assertEqual(nxt["visit"]["plate"], "16 NUB 82")

    def test_multiple_open_no_auto_pick(self) -> None:
        self.store.on_entry(plate="77 ADN 856", camera="153", full_weight=41920)
        # force a second open by completing none and injecting
        visits = self.store.all_visits()
        extra = dict(visits[0])
        extra["visit_id"] = visits[0]["visit_id"] + "-b"
        extra["full_weight"] = 42680
        extra["status"] = VisitStatus.IN_FACILITY
        visits.append(extra)
        self.store.repo.save_visits(visits)
        r = self.store.on_exit(plate="77 ADN 856", camera="165", empty_weight=15240)
        self.assertEqual(r["reason"], REVIEW_MULTIPLE_OPEN)
        self.assertEqual(r["action"], "manual_review")
        chosen = r["visit"]["visit_id"]
        done = self.store.resolve_multiple_exit(chosen_id=chosen)
        self.assertIsNotNone(done)
        self.assertIn(done["status"], {VisitStatus.COMPLETED, VisitStatus.MANUAL_REVIEW})

    def test_invalid_net(self) -> None:
        self.store.on_entry(plate="34 ABC 01", camera="153", full_weight=10000)
        r = self.store.on_exit(plate="34 ABC 01", camera="165", empty_weight=12000)
        self.assertEqual(r["reason"], REVIEW_INVALID_NET)
        self.assertEqual(r["visit"]["full_weight"], 10000)
        self.assertEqual(r["visit"]["empty_weight"], 12000)

    def test_plate_conflict(self) -> None:
        a = self.store.on_entry(plate="16 AAA 11", camera="153", full_weight=1000)
        self.store.on_entry(plate="16 BBB 22", camera="153", full_weight=2000)
        res = self.store.change_plate(visit_id=a["visit"]["visit_id"], new_plate="16 BBB 22")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "PLATE_CONFLICT")
        still = self.store.get(a["visit"]["visit_id"])
        self.assertEqual(still["plate"], "16 AAA 11")

    def test_corrupt_json_not_wiped(self) -> None:
        path = self.tmp / "visits.json"
        path.write_text("{not json", encoding="utf-8")
        loaded = self.store.all_visits()
        self.assertEqual(loaded, [])
        self.assertTrue(any(self.tmp.glob("visits.json.corrupt-*")))
        self.store.on_entry(plate="77 ADN 856", camera="153", full_weight=1)
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["visits"]), 1)

    def test_reload_open_visit(self) -> None:
        self.store.on_entry(plate="77 ADN 856", camera="153", full_weight=42680)
        other = store_at(self.tmp)
        opens = other.open_visits("77ADN856")
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["full_weight"], 42680)

    def test_prune_old_visits_keeps_today(self) -> None:
        from datetime import date, timedelta

        today = self.store.on_entry(
            plate="77 ADN 856", camera="153", full_weight=1000
        )["visit"]
        old = dict(today)
        old["visit_id"] = "V-OLD"
        old["entry_time"] = (date.today() - timedelta(days=1)).isoformat() + "T10:00:00"
        old["created_at"] = old["entry_time"]
        old["updated_at"] = old["entry_time"]
        visits = self.store.repo.load_visits()
        visits.append(old)
        self.store.repo.save_visits(visits)
        self.assertGreaterEqual(len(self.store.repo.load_visits()), 2)
        removed = self.store.prune_old_visits()
        self.assertEqual(removed, 1)
        ids = {v["visit_id"] for v in self.store.all_visits()}
        self.assertIn(today["visit_id"], ids)
        self.assertNotIn("V-OLD", ids)


class SaveChoiceTests(unittest.TestCase):
    def test_visit_dedupe(self) -> None:
        import irsaliye

        folder = Path(tempfile.mkdtemp())
        irsaliye.CHOICE_PATH = folder / "secimler.json"
        irsaliye.save_choice("77 ADN 856", "IRS-1", "secim", "153", extra={"visit_id": "V-1"})
        irsaliye.save_choice("77 ADN 856", "IRS-1", "secim", "153", extra={"visit_id": "V-1"})
        irsaliye.save_choice("77 ADN 856", "IRS-1", "secim", "153", extra={"visit_id": "V-2"})
        rows = json.loads(irsaliye.CHOICE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    raise SystemExit(unittest.main(verbosity=2))
