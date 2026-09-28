#!/usr/bin/env python3
"""İrsaliye XML'inin tamamında plaka arama — 16HS151 / 16 HS 151."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from irsaliye import (
    IrsaliyeIndex,
    _extract_xml,
    collect_plates_from_xml,
    complete_open_prefix,
    format_plate,
    official_plate_keys,
    parse_file,
    plate_extends,
    plate_key,
    plate_matches_doc,
    plates_in_text,
    save_original,
)


SAMPLE = """<?xml version="1.0"?>
<DespatchAdvice xmlns="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2">
  <ID>IRS-1</ID>
  <UUID>u-1</UUID>
  <IssueDate>2026-09-17</IssueDate>
  <IssueTime>10:31:00</IssueTime>
  <Note>Sevk: 16HS151 dorse dahil</Note>
  <DespatchSupplierParty><Party><PartyName><Name>Tedarik</Name></PartyName></Party></DespatchSupplierParty>
  <DeliveryCustomerParty><Party><PartyName><Name>Musteri</Name></PartyName></Party></DeliveryCustomerParty>
  <DespatchLine>
    <ID>1</ID>
    <Item>
      <Name>Cimento</Name>
      <AdditionalItemProperty>
        <Name>Plaka</Name>
        <Value>77 ADN 856</Value>
      </AdditionalItemProperty>
    </Item>
  </DespatchLine>
</DespatchAdvice>
"""

SAMPLE_FIELD = """<?xml version="1.0"?>
<DespatchAdvice xmlns="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2">
  <ID>IRS-2</ID>
  <UUID>u-2</UUID>
  <IssueDate>2026-09-17</IssueDate>
  <IssueTime>10:31:00</IssueTime>
  <Shipment>
    <TransportHandlingUnit>
      <TransportEquipment>
        <RoadTransport>
          <LicensePlateID schemeID="PLAKA">16HS151</LicensePlateID>
        </RoadTransport>
      </TransportEquipment>
    </TransportHandlingUnit>
  </Shipment>
</DespatchAdvice>
"""


class PlateXmlTests(unittest.TestCase):
    def test_compact_and_spaced_same_key(self) -> None:
        self.assertEqual(plate_key("16HS151"), "16HS151")
        self.assertEqual(plate_key("16 HS 151"), "16HS151")
        self.assertEqual(format_plate("16HS151"), "16 HS 151")
        self.assertEqual(plates_in_text("Sevk 16HS151 dorse"), ["16HS151"])
        self.assertEqual(plates_in_text("Sevk 16 HS 151 dorse"), ["16HS151"])
        self.assertTrue(plate_extends("16BLZ93", "16BLZ931"))
        self.assertTrue(plate_extends("16NUB", "16NUB82"))
        self.assertTrue(plate_extends("16NUB8", "16NUB82"))
        self.assertFalse(plate_extends("16AB", "16AB123"))
        self.assertFalse(plate_extends("16BLZ93", "16BLZ94"))
        self.assertFalse(plate_extends("16ABC123", "34ABC123"))
        self.assertEqual(complete_open_prefix("16NUB", {"16NUB82"}), "16 NUB 82")
        self.assertIsNone(complete_open_prefix("16NUB", {"16NUB82", "16NUB99"}))

    def test_scan_entire_xml_note_and_property(self) -> None:
        root = ET.fromstring(SAMPLE)
        keys = collect_plates_from_xml(root, SAMPLE)
        self.assertIn("16HS151", keys)
        self.assertIn("77ADN856", keys)

    def test_getir_uses_official_plate_not_note(self) -> None:
        note_other = """<?xml version="1.0"?>
<DespatchAdvice>
  <ID>IRS-NOTE</ID>
  <UUID>u-n</UUID>
  <IssueDate>2026-09-28</IssueDate>
  <IssueTime>09:00:00</IssueTime>
  <Note>Ref 16 RAU 55</Note>
  <Shipment><TransportHandlingUnit><TransportEquipment><RoadTransport>
    <LicensePlateID>16BVM55</LicensePlateID>
  </RoadTransport></TransportEquipment></TransportHandlingUnit></Shipment>
</DespatchAdvice>
"""
        official = """<?xml version="1.0"?>
<DespatchAdvice>
  <ID>EIR-RAU</ID>
  <UUID>u-r</UUID>
  <IssueDate>2026-09-28</IssueDate>
  <IssueTime>10:00:00</IssueTime>
  <Shipment><TransportHandlingUnit><TransportEquipment><RoadTransport>
    <LicensePlateID>16RAU55</LicensePlateID>
  </RoadTransport></TransportEquipment></TransportHandlingUnit></Shipment>
</DespatchAdvice>
"""
        root_n = ET.fromstring(note_other)
        self.assertEqual(official_plate_keys(root_n), ["16BVM55"])
        self.assertIn("16RAU55", collect_plates_from_xml(root_n, note_other))
        folder = Path(tempfile.mkdtemp())
        (folder / "n.xml").write_text(note_other, encoding="utf-8")
        (folder / "r.xml").write_text(official, encoding="utf-8")
        idx = IrsaliyeIndex(folder)
        ids = [h.get("id") for h in idx.lookup_all("16 RAU 55")]
        self.assertEqual(ids, ["EIR-RAU"])
        self.assertTrue(plate_matches_doc("16 RAU 55", {"official_plates": ["16RAU55"]}))
        self.assertFalse(plate_matches_doc("16 RAU 55", {"official_plates": ["16BVM55"], "plate": "16 BVM 55"}))

    def test_license_plate_compact_field(self) -> None:
        root = ET.fromstring(SAMPLE_FIELD)
        keys = collect_plates_from_xml(root, SAMPLE_FIELD)
        self.assertEqual(keys[0], "16HS151")

    def test_parse_file_finds_note_plate(self) -> None:
        folder = Path(tempfile.mkdtemp())
        path = folder / "irs.xml"
        path.write_text(SAMPLE, encoding="utf-8")
        rec = parse_file(path)
        self.assertIsNotNone(rec)
        self.assertIn("16HS151", rec["plates"])
        self.assertIn("77ADN856", rec["plates"])

    def test_signature_blob_does_not_block_or_fake_plate(self) -> None:
        blob = ("MIIB" + "16HS999" + "ABCD" * 8000)
        xml = f"""<?xml version="1.0"?>
<DespatchAdvice>
  <ID>IRS-3</ID>
  <UUID>u-3</UUID>
  <IssueDate>2026-09-17</IssueDate>
  <IssueTime>10:31:00</IssueTime>
  <Note>Dorse 16 HS 151</Note>
  <Signature><SignatureValue>{blob}</SignatureValue></Signature>
  <EmbeddedDocumentBinaryObject>{blob}</EmbeddedDocumentBinaryObject>
  <Shipment><TransportHandlingUnit><TransportEquipment><RoadTransport>
    <LicensePlateID>16KD131</LicensePlateID>
  </RoadTransport></TransportEquipment></TransportHandlingUnit></Shipment>
</DespatchAdvice>
"""
        root = ET.fromstring(xml)
        keys = collect_plates_from_xml(root, xml)
        self.assertEqual(keys[0], "16KD131")
        self.assertIn("16HS151", keys)
        self.assertNotIn("16HS999", keys)

    def test_prefixed_despatch_extracts(self) -> None:
        raw = (
            '<q1:DespatchAdvice xmlns:q1="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2">'
            "<ID>BRK2026000005999</ID><UUID>u-pref</UUID>"
            "<IssueDate>2026-09-28</IssueDate>"
            "</q1:DespatchAdvice>"
        )
        xml = _extract_xml(raw)
        self.assertIsNotNone(xml)
        self.assertTrue(xml.startswith("<q1:DespatchAdvice"))
        dest = save_original(raw.encode("utf-8"), dest_dir=Path(tempfile.mkdtemp()))
        self.assertIsNotNone(dest)
        rec = parse_file(dest)
        self.assertEqual(rec["id"], "BRK2026000005999")

    def test_prune_deletes_old_keeps_today(self) -> None:
        from datetime import date, timedelta

        from irsaliye import prune_old_docs

        folder = Path(tempfile.mkdtemp())
        today = date.today()
        old = folder / f"{(today - timedelta(days=3)).isoformat()}_OLD_16AA111.xml"
        keep = folder / f"{today.isoformat()}_NEW_16AA111.xml"
        old.write_text("<DespatchAdvice/>", encoding="utf-8")
        keep.write_text("<DespatchAdvice/>", encoding="utf-8")
        n = prune_old_docs(folder, days=1)
        self.assertEqual(n, 1)
        self.assertFalse(old.exists())
        self.assertTrue(keep.exists())


if __name__ == "__main__":
    unittest.main()
