#!/usr/bin/env python3
"""GİB UBL-TR e-İrsaliye (DespatchAdvice) — plakadan belge eşle."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
IRSALIYE_DIR = ROOT / "irsaliye"
USED_PATH = IRSALIYE_DIR / ".used.json"
CHOICE_PATH = IRSALIYE_DIR / "secimler.json"
_DOC_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})_")


def keep_days() -> int:
    cfg_path = ROOT / "irsaliye_config.json"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return max(1, int(data.get("days") or 1))
    except Exception:
        return 1


def _file_doc_day(path: Path) -> date | None:
    m = _DOC_DAY.match(path.name)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    return None


_LAST_PRUNE = 0.0


def prune_old_docs(folder: Path | None = None, days: int | None = None) -> int:
    """Bugünden eski XML/PDF/HTML irsaliyeleri sil — Getir eski belge getirmez."""
    global _LAST_PRUNE
    now = time.monotonic()
    if days is None and now - _LAST_PRUNE < 60.0:
        return 0
    _LAST_PRUNE = now
    folder = folder or IRSALIYE_DIR
    keep = days if days is not None else keep_days()
    cutoff = date.today() - timedelta(days=keep - 1)
    removed = 0
    if not folder.is_dir():
        return 0
    for path in list(folder.iterdir()):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in {".xml", ".txt", ".pdf", ".html"}:
            continue
        day = _file_doc_day(path)
        if day is None or day >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return " ".join(el.text.split())


def _child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for ch in parent:
        if _local(ch.tag) == name:
            return ch
    return None


def _children(parent: ET.Element | None, name: str) -> list[ET.Element]:
    if parent is None:
        return []
    return [ch for ch in parent if _local(ch.tag) == name]


def plate_key(text: str) -> str:
    t = (text or "").upper().replace("İ", "I").replace("ı", "I")
    return re.sub(r"[^A-Z0-9]", "", t)


def format_plate(text: str) -> str:
    """16HS151 ve 16 HS 151 aynı plakadır; gösterim her zaman boşluklu."""
    t = plate_key(text)
    m = re.fullmatch(r"(\d{2})([A-Z]{1,3})(\d{2,4})", t)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"
    return t


def plate_extends(short: str, longer: str) -> bool:
    """16NUB → 16NUB82, 16BLZ93 → 16BLZ931: kesilmiş okuma."""
    a, b = plate_key(short), plate_key(longer)
    if not a or not b or a == b or len(a) < 5 or not b.startswith(a):
        return False
    extra = b[len(a) :]
    return extra.isdigit() and 1 <= len(extra) <= 4


def complete_open_prefix(text: str, open_keys: set[str]) -> str | None:
    """16NUB tek açık 16NUB82 ise tamamla. Birden fazla aday belirsiz kalır."""
    key = plate_key(text)
    if len(key) < 5:
        return None
    hits = [k for k in open_keys if k == key or plate_extends(key, k)]
    if len(hits) == 1:
        return format_plate(hits[0])
    return None


_VALID_CITY = {f"{i:02d}" for i in range(1, 82)}
_PLATE_IN_TEXT = re.compile(r"(?<![A-Z0-9])(\d{2})\s*([A-Z]{1,3})\s*(\d{2,4})(?![A-Z0-9])")
_MAX_PLATE_CHUNK = 1500
_SKIP_PLATE_TAGS = {
    "UBLExtensions",
    "UBLExtension",
    "ExtensionContent",
    "Signature",
    "SignedInfo",
    "SignatureValue",
    "KeyInfo",
    "X509Data",
    "X509Certificate",
    "X509IssuerSerial",
    "EmbeddedDocumentBinaryObject",
    "DigestValue",
    "DigestMethod",
    "Modulus",
    "Exponent",
    "CanonicalizationMethod",
    "SignatureMethod",
    "Transform",
    "Transforms",
    "Reference",
    "SignedProperties",
}
_STRIP_XML_BLOCKS = re.compile(
    r"<(?:[\w.-]+:)?(?:UBLExtensions|Signature|EmbeddedDocumentBinaryObject)\b"
    r"[\s\S]*?</(?:[\w.-]+:)?(?:UBLExtensions|Signature|EmbeddedDocumentBinaryObject)>",
    re.I,
)


def plates_in_text(text: str) -> list[str]:
    """16HS151 ve 16 HS 151 — XML'in herhangi bir metninde."""
    raw = (text or "").upper().replace("İ", "I").replace("ı", "I")
    if len(raw) > _MAX_PLATE_CHUNK:
        return []
    found: list[str] = []
    for m in _PLATE_IN_TEXT.finditer(raw):
        city, letters, digits = m.group(1), m.group(2), m.group(3)
        if city not in _VALID_CITY:
            continue
        key = f"{city}{letters}{digits}"
        if key not in found:
            found.append(key)
    return found


def collect_plates_from_xml(root: ET.Element, xml_text: str = "") -> list[str]:
    """İrsaliye XML'inin iş metninde plaka ara: LicensePlateID, Note, satır, özellik."""
    official: list[str] = []
    rest: list[str] = []

    def _add(target: list[str], key: str) -> None:
        if key and key not in target and key not in official:
            target.append(key)

    for el in root.iter():
        name = _local(el.tag)
        if name in _SKIP_PLATE_TAGS:
            continue
        chunks = [_text(el), el.tail or ""]
        chunks.extend(str(v) for v in el.attrib.values() if v)
        keys: list[str] = []
        for chunk in chunks:
            if len(chunk) > _MAX_PLATE_CHUNK:
                continue
            keys.extend(plates_in_text(chunk))
        if name == "LicensePlateID":
            raw_key = plate_key(_text(el))
            if raw_key and raw_key not in keys:
                keys.insert(0, raw_key)
            for key in keys:
                _add(official, key)
        else:
            for key in keys:
                _add(rest, key)
    if xml_text:
        business = _STRIP_XML_BLOCKS.sub(" ", xml_text)
        plain = re.sub(r"<[^>]+>", " ", business)
        plain = re.sub(r"&[a-zA-Z]+;", " ", plain)
        overlap = 32
        step = max(1, _MAX_PLATE_CHUNK - overlap)
        for i in range(0, len(plain), step):
            piece = plain[i : i + _MAX_PLATE_CHUNK]
            if not piece:
                break
            for key in plates_in_text(piece):
                _add(rest, key)
    return official + [k for k in rest if k not in official]


def official_plate_keys(root: ET.Element) -> list[str]:
    """Sadece LicensePlateID — nottaki başka plaka belgeyi çalmaz."""
    found: list[str] = []
    for el in root.iter():
        if _local(el.tag) != "LicensePlateID":
            continue
        raw = plate_key(_text(el))
        keys = plates_in_text(_text(el))
        if raw and raw not in keys:
            keys.insert(0, raw)
        for key in keys:
            if key and key not in found:
                found.append(key)
    return found


def plate_matches_doc(vehicle: str, rec: dict) -> bool:
    """Araç plakası belgenin resmi plakasıyla aynı (veya tek uzantı)."""
    v = plate_key(vehicle)
    if not v:
        return False
    official = [plate_key(p) for p in (rec.get("official_plates") or []) if p]
    if not official:
        first = plate_key(str(rec.get("plate") or ""))
        official = [first] if first else []
    if v in official:
        return True
    return any(plate_extends(v, o) or plate_extends(o, v) for o in official)


def clean_time(text: str) -> str:
    t = (text or "").split("+")[0].split("Z")[0].strip()
    if "." in t:
        t = t.split(".", 1)[0]
    if len(t) == 5:
        t += ":00"
    return t[:8]


def issue_dt(rec: dict) -> datetime | None:
    d = str(rec.get("date") or "")[:10]
    t = clean_time(str(rec.get("time") or "00:00:00")) or "00:00:00"
    try:
        return datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            return None


_DESPATCH_OPEN = re.compile(r"<((?:[A-Za-z_][\w.-]*:)?DespatchAdvice)\b", re.I)


def _extract_xml(raw: str) -> str | None:
    m = _DESPATCH_OPEN.search(raw or "")
    if not m:
        return None
    tag = m.group(1)
    start = m.start()
    close = f"</{tag}>"
    end = raw.find(close, start)
    if end < 0:
        for alt in ("</DespatchAdvice>", "</despatchAdvice>"):
            end = raw.find(alt, start)
            if end >= 0:
                return raw[start : end + len(alt)]
        return raw[start:]
    return raw[start : end + len(close)]


def parse_despatch(root: ET.Element) -> dict:
    rec: dict = {
        "id": _text(_child(root, "ID")),
        "uuid": _text(_child(root, "UUID")),
        "profile": _text(_child(root, "ProfileID")),
        "type": _text(_child(root, "DespatchAdviceTypeCode")),
        "date": _text(_child(root, "IssueDate")),
        "time": _text(_child(root, "IssueTime")),
        "notes": [_text(n) for n in _children(root, "Note") if _text(n)],
        "supplier": "",
        "customer": "",
        "plates": [],
        "driver": "",
        "lines": [],
    }
    rec["supplier"] = _text(
        _child(
            _child(_child(_child(root, "DespatchSupplierParty"), "Party"), "PartyName"),
            "Name",
        )
    )
    rec["customer"] = _text(
        _child(
            _child(_child(_child(root, "DeliveryCustomerParty"), "Party"), "PartyName"),
            "Name",
        )
    )

    plates: list[str] = []
    driver = ""
    for el in root.iter():
        name = _local(el.tag)
        if name == "DriverPerson" and not driver:
            first = _text(_child(el, "FirstName"))
            last = _text(_child(el, "FamilyName"))
            driver = " ".join(p for p in (first, last) if p)
    rec["plates"] = []
    rec["driver"] = driver

    for line in _children(root, "DespatchLine"):
        item = _child(line, "Item")
        qty_el = _child(line, "DeliveredQuantity")
        rec["lines"].append(
            {
                "id": _text(_child(line, "ID")),
                "name": _text(_child(item, "Name")),
                "sku": _text(_child(_child(item, "SellersItemIdentification"), "ID")),
                "qty": _text(qty_el),
                "unit": (qty_el.get("unitCode") if qty_el is not None else "") or "",
            }
        )
    return rec


def parse_file(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    xml = _extract_xml(raw)
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    if _local(root.tag) != "DespatchAdvice":
        return None
    rec = parse_despatch(root)
    rec["file"] = str(path)
    rec["plates"] = collect_plates_from_xml(root, xml)
    rec["official_plates"] = official_plate_keys(root)
    show = rec["official_plates"] or rec["plates"]
    rec["plate"] = format_plate(show[0]) if show else ""
    return rec


def summarize(rec: dict | None) -> str:
    if not rec:
        return ""
    mal = rec.get("lines") or []
    names = [x.get("name") or "" for x in mal if x.get("name")]
    if len(names) > 1:
        mal_s = f"{names[0]} + {len(names) - 1} kalem"
    elif names:
        mal_s = names[0]
    else:
        mal_s = ""
    bits = [rec.get("id") or ""]
    if rec.get("customer"):
        bits.append(rec["customer"])
    if rec.get("driver"):
        bits.append(rec["driver"])
    if mal_s:
        bits.append(mal_s)
    return " · ".join(b for b in bits if b)


def public_record(rec: dict | None) -> dict | None:
    """Arayüze gidecek alanlar — TCKN yok."""
    if not rec:
        return None
    return {
        "id": rec.get("id") or "",
        "date": rec.get("date") or "",
        "time": clean_time(str(rec.get("time") or "")),
        "supplier": rec.get("supplier") or "",
        "customer": rec.get("customer") or "",
        "driver": rec.get("driver") or "",
        "plate": rec.get("plate") or "",
        "plates": list(rec.get("plates") or []),
        "official_plates": list(rec.get("official_plates") or []),
        "notes": rec.get("notes") or [],
        "lines": rec.get("lines") or [],
        "summary": summarize(rec),
        "file": rec.get("file") or "",
        "uuid": rec.get("uuid") or "",
    }


def save_original(
    ubl: bytes, pdf: bytes | None = None, dest_dir: Path | None = None
) -> Path | None:
    """Gelen orijinal UBL (+ PDF görüntü) — sadeleştirilmez."""
    dest_dir = dest_dir or IRSALIYE_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f"_in_{datetime.now():%H%M%S%f}.xml"
    tmp.write_bytes(ubl)
    rec = parse_file(tmp)
    if not rec or not rec.get("id"):
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    plate = rec["plates"][0] if rec.get("plates") else "NOPLAKA"
    dest = dest_dir / f"{rec['date']}_{rec['id']}_{plate}.xml".replace(" ", "")
    if dest.exists():
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    else:
        try:
            tmp.replace(dest)
        except Exception:
            dest.write_bytes(ubl)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    if pdf and pdf[:4] == b"%PDF":
        dest.with_suffix(".pdf").write_bytes(pdf)
    return dest


def view_path(rec: dict | None) -> Path | None:
    """Orijinal PDF/HTML — kendi ürettiğimiz sayfa değil."""
    if not rec:
        return None
    xml = Path(str(rec.get("file") or ""))
    cands: list[Path] = []
    if str(xml).lower().endswith(".xml"):
        cands.append(xml.with_suffix(".pdf"))
        cands.append(xml.with_suffix(".html"))
    doc_id = str(rec.get("id") or "")
    if doc_id:
        cands.extend(sorted(IRSALIYE_DIR.glob(f"*{doc_id}*.pdf")))
        cands.extend(sorted(IRSALIYE_DIR.glob(f"*{doc_id}*.html")))
    seen: set[str] = set()
    for p in cands:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.suffix.lower() not in {".pdf", ".html", ".htm"}:
            continue
        if p.exists() and p.stat().st_size > 100:
            return p
    return None


def ensure_view(rec: dict | None) -> Path | None:
    """Yoksa Digital Planet'ten orijinal PDF indir."""
    found = view_path(rec)
    if found:
        return found
    if not rec:
        return None
    uuid = str(rec.get("uuid") or "")
    xml = Path(str(rec.get("file") or ""))
    if not uuid and xml.exists():
        full = parse_file(xml)
        uuid = str((full or {}).get("uuid") or "")
    if not uuid:
        return None
    try:
        from digitalplanet import fetch_pdf, ticket
        from eportal import load_config

        cfg = load_config()
        pdf = fetch_pdf(cfg, ticket(cfg), uuid)
    except Exception as exc:
        logging.getLogger("kantar").warning(
            "operation=ensure_view plate= error=%s uuid=%s", exc, uuid
        )
        return None
    if not pdf:
        return None
    dest = xml.with_suffix(".pdf") if xml.exists() else IRSALIYE_DIR / f"{rec.get('id') or uuid}.pdf"
    dest.write_bytes(pdf)
    return dest


def save_choice(
    plate: str,
    irs_id: str,
    source: str,
    camera: str = "",
    extra: dict | None = None,
) -> dict:
    """Kantarcının aldığı / elle yazdığı irsaliye no.

    Aynı visit_id + irsaliye tekrar Al'da duplicate satır yazılmaz.
    Geçmiş kayıtlar silinmez. Plaka tek başına global tekilleştirmez.
    """
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "plate": (plate or "").strip(),
        "irsaliye": (irs_id or "").strip().upper(),
        "source": source,
        "camera": camera or "",
    }
    if extra:
        rec.update({k: v for k, v in extra.items() if v is not None})
    rows: list = []
    try:
        loaded = json.loads(CHOICE_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            rows = loaded
    except (OSError, json.JSONDecodeError) as exc:
        logging.getLogger("kantar").warning(
            "operation=save_choice error=%s path=%s", exc, CHOICE_PATH
        )
        rows = []
    visit_id = str(rec.get("visit_id") or "")
    irs = rec["irsaliye"]
    if visit_id and irs:
        for existing in rows:
            if (
                str(existing.get("visit_id") or "") == visit_id
                and str(existing.get("irsaliye") or "").strip().upper() == irs
            ):
                return existing
    rows.append(rec)
    CHOICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHOICE_PATH.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return rec


def write_preview(rec: dict | None, dest: Path | None = None) -> Path | None:
    """Okunabilir e-irsaliye HTML sayfası — tarayıcıda açılır."""
    if not rec:
        return None
    doc_id = str(rec.get("id") or "irsaliye")
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", doc_id) or "irsaliye"
    dest = dest or (ROOT / ".tmp" / f"irsaliye_{safe}.html")
    dest.parent.mkdir(parents=True, exist_ok=True)
    date = " ".join(p for p in (rec.get("date") or "", rec.get("time") or "") if p)
    notes = [escape(str(n)) for n in (rec.get("notes") or []) if n]
    rows = []
    for ln in rec.get("lines") or []:
        rows.append(
            "<tr>"
            f"<td>{escape(str(ln.get('id') or ''))}</td>"
            f"<td>{escape(str(ln.get('name') or ln.get('sku') or ''))}</td>"
            f"<td>{escape(str(ln.get('qty') or ''))} {escape(str(ln.get('unit') or ''))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='3'>Kalem yok</td></tr>")
    html = f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>e-İrsaliye {escape(doc_id)}</title>
<style>
body {{ font-family: 'Segoe UI', sans-serif; background:#0B1220; color:#F1F5F9; margin:0; }}
.sheet {{ max-width:880px; margin:28px auto; background:#121C2C; padding:36px 44px;
  border-radius:14px; border:1px solid #243044; }}
.kicker {{ color:#C9A227; letter-spacing:.14em; font-size:12px; font-weight:700; }}
h1 {{ margin:8px 0 20px; font-size:26px; }}
.plate {{ display:inline-block; background:#F4F1E8; color:#141414; font-weight:700;
  font-size:22px; padding:8px 18px; border-radius:6px; letter-spacing:.08em; }}
.meta {{ display:grid; grid-template-columns:140px 1fr; gap:8px 16px; margin:22px 0; }}
.k {{ color:#8B9BB0; font-size:12px; }}
table {{ width:100%; border-collapse:collapse; margin-top:12px; }}
th {{ text-align:left; color:#8B9BB0; font-size:11px; letter-spacing:.08em;
  border-bottom:1px solid #243044; padding:8px 6px; }}
td {{ padding:10px 6px; border-bottom:1px solid #1A2536; }}
.note {{ color:#C5D0DC; margin:6px 0; }}
</style>
</head>
<body>
<div class="sheet">
<div class="kicker">BURSA ÇİMENTO · E-İRSALİYE</div>
<h1>{escape(doc_id)}</h1>
<div class="plate">{escape(str(rec.get('plate') or '—'))}</div>
<div class="meta">
<div class="k">Tarih</div><div>{escape(date or '—')}</div>
<div class="k">Gönderen</div><div>{escape(str(rec.get('supplier') or '—'))}</div>
<div class="k">Alıcı</div><div>{escape(str(rec.get('customer') or '—'))}</div>
<div class="k">Sürücü</div><div>{escape(str(rec.get('driver') or '—'))}</div>
</div>
<h2 style="font-size:13px;color:#8B9BB0;letter-spacing:.1em;">KALEMLER</h2>
<table>
<thead><tr><th>NO</th><th>MAL / HİZMET</th><th>MİKTAR</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
<h2 style="font-size:13px;color:#8B9BB0;letter-spacing:.1em;margin-top:28px;">NOTLAR</h2>
{''.join(f'<div class="note">{n}</div>' for n in notes) or '<div class="note">—</div>'}
</div>
</body>
</html>
"""
    dest.write_text(html, encoding="utf-8")
    return dest


class IrsaliyeIndex:
    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or IRSALIYE_DIR
        self.folder.mkdir(parents=True, exist_ok=True)
        self.by_plate: dict[str, list[dict]] = {}
        self.by_official: dict[str, list[dict]] = {}
        self.docs: list[dict] = []
        self._stamp: tuple = ()
        self._lock = threading.RLock()

    def _files(self) -> list[Path]:
        cutoff = date.today() - timedelta(days=keep_days() - 1)
        out: list[Path] = []
        for p in sorted(self.folder.iterdir()):
            if not p.is_file() or p.suffix.lower() not in {".xml", ".txt"}:
                continue
            day = _file_doc_day(p)
            if day is not None and day < cutoff:
                continue
            out.append(p)
        return out

    def refresh(self, force: bool = False) -> int:
        prune_old_docs(self.folder)
        files = self._files()
        stamp = tuple((p.name, p.stat().st_mtime_ns) for p in files)
        with self._lock:
            if not force and stamp == self._stamp:
                return len(self.docs)
        docs: list[dict] = []
        by_plate: dict[str, list[dict]] = {}
        by_official: dict[str, list[dict]] = {}
        for path in files:
            rec = parse_file(path)
            if not rec or not rec.get("id"):
                continue
            rec_day = str(rec.get("date") or "")[:10]
            if rec_day and rec_day < (date.today() - timedelta(days=keep_days() - 1)).isoformat():
                continue
            docs.append(rec)
            for raw in rec.get("plates") or []:
                by_plate.setdefault(raw, []).append(rec)
            for raw in rec.get("official_plates") or []:
                by_official.setdefault(raw, []).append(rec)
        with self._lock:
            self._stamp = stamp
            self.docs = docs
            self.by_plate = by_plate
            self.by_official = by_official
            return len(docs)

    def lookup_all(self, plate: str, *, official: bool = True) -> list[dict]:
        """Getir: resmi plaka. official=False nottaki plakayı da tarar."""
        self.refresh()
        key = plate_key(plate)
        if not key:
            return []
        with self._lock:
            mapping = dict(self.by_official if official else self.by_plate)
        hits = list(mapping.get(key) or [])
        if not hits:
            cands = [p for p in mapping if plate_extends(key, p) or plate_extends(p, key)]
            if len(cands) == 1:
                hits = list(mapping.get(cands[0]) or [])
        out: list[dict] = []
        seen: set[str] = set()
        for rec in hits:
            if official and not plate_matches_doc(key, rec):
                continue
            pub = public_record(rec)
            if not pub:
                continue
            did = str(pub.get("id") or "")
            if did in seen:
                continue
            seen.add(did)
            out.append(pub)
        return out

    def has_plate(self, plate: str) -> bool:
        """OCR skorunda refresh yok — kilit altında anlık bakış."""
        key = plate_key(plate)
        if not key:
            return False
        with self._lock:
            mapping = dict(self.by_plate)
        if key in mapping:
            return True
        if len(key) < 5:
            return False
        return any(p.startswith(key) and len(p) > len(key) for p in mapping)

    def complete_plate(self, plate: str) -> str | None:
        """Eksik OCR (34FV675) tek irsaliye plakasıyla (34FV6754) tamamlanır."""
        key = plate_key(plate)
        if len(key) < 5:
            return None
        with self._lock:
            mapping = dict(self.by_plate)
        if key in mapping:
            return format_plate(key)
        cands = [p for p in mapping if p.startswith(key) and len(p) > len(key)]
        if len(cands) == 1:
            return format_plate(cands[0])
        return None

    def _used_today(self) -> set[str]:
        today = date.today().isoformat()
        try:
            data = json.loads(USED_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if str(data.get("date") or "") != today:
            return set()
        return {str(x) for x in (data.get("ids") or []) if x}

    def _mark_used(self, doc_id: str) -> None:
        today = date.today().isoformat()
        ids = self._used_today()
        ids.add(doc_id)
        USED_PATH.parent.mkdir(parents=True, exist_ok=True)
        USED_PATH.write_text(
            json.dumps({"date": today, "ids": sorted(ids)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def today_docs(self, plate: str) -> list[dict]:
        today = date.today().isoformat()
        return [h for h in self.lookup_all(plate) if str(h.get("date") or "")[:10] == today]

    def docs_on_date(self, plate: str, day: date | None = None) -> list[dict]:
        """Sadece o günün irsaliyesi — 14'teki belge bugün gelmez."""
        day_s = (day or date.today()).isoformat()
        return [h for h in self.lookup_all(plate) if str(h.get("date") or "")[:10] == day_s]

    def docs_for_when(self, plate: str, when: datetime | None = None) -> list[dict]:
        """Yalnızca bugünün irsaliyesi; tartımdan önceki en yakın saat üstte."""
        when = when or datetime.now()
        today = date.today()
        clock = when if when.date() == today else datetime.now()
        return self.rank_by_time(self.docs_on_date(plate, today), clock)

    def rank_by_time(self, hits: list[dict], when: datetime | None) -> list[dict]:
        """Sadece tartımdan / çıkıştan ÖNCEki belgeler; en yakın üstte."""
        if not hits:
            return []
        if when is None:
            when = datetime.now()
        before: list[tuple[float, dict]] = []
        for h in hits:
            dt = issue_dt(h)
            rec = dict(h)
            if dt is None:
                continue
            lag = (when - dt).total_seconds()
            if lag < 0:
                continue
            rec["time_delta_sec"] = int(lag)
            rec["time_match"] = lag <= 4 * 3600
            before.append((lag, rec))
        before.sort(key=lambda x: x[0])
        return [r for _lag, r in before]

    def find_by_id(self, doc_id: str) -> dict | None:
        self.refresh()
        key = (doc_id or "").strip().upper().replace(" ", "")
        if not key:
            return None
        with self._lock:
            docs = list(self.docs)
        for rec in docs:
            if str(rec.get("id") or "").upper().replace(" ", "") == key:
                return public_record(rec)
        return None

    def lookup(self, plate: str) -> dict | None:
        hits = self.docs_for_when(plate)
        return hits[0] if hits else None


def import_xml(src: Path, dest_dir: Path | None = None) -> Path | None:
    """Tarayıcı çıktısından sade e-irsaliye XML üretir (imza/XSLT yok)."""
    rec = parse_file(src)
    if not rec:
        return None
    dest_dir = dest_dir or IRSALIYE_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    plate = rec["plates"][0] if rec["plates"] else "NOPLAKA"
    name = f"{rec['date']}_{rec['id']}_{plate}.xml".replace(" ", "")
    dest = dest_dir / name

    def el(tag: str, text: str = "", **attrs) -> ET.Element:
        node = ET.Element(tag, attrib=attrs)
        if text:
            node.text = text
        return node

    root = el("DespatchAdvice")
    root.append(el("ProfileID", rec["profile"] or "TEMELIRSALIYE"))
    root.append(el("ID", rec["id"]))
    root.append(el("UUID", rec["uuid"]))
    root.append(el("IssueDate", rec["date"]))
    root.append(el("IssueTime", rec["time"]))
    root.append(el("DespatchAdviceTypeCode", rec["type"] or "SEVK"))
    for note in rec["notes"]:
        root.append(el("Note", note))

    def party(tag: str, name: str) -> ET.Element:
        wrap = el(tag)
        p = el("Party")
        pn = el("PartyName")
        pn.append(el("Name", name))
        p.append(pn)
        wrap.append(p)
        return wrap

    root.append(party("DespatchSupplierParty", rec["supplier"]))
    root.append(party("DeliveryCustomerParty", rec["customer"]))
    ship = el("Shipment")
    stage = el("ShipmentStage")
    means = el("TransportMeans")
    road = el("RoadTransport")
    for p in rec["plates"]:
        road.append(el("LicensePlateID", p, schemeID="PLAKA"))
    means.append(road)
    stage.append(means)
    if rec["driver"]:
        parts = rec["driver"].split(" ", 1)
        person = el("DriverPerson")
        person.append(el("FirstName", parts[0]))
        if len(parts) > 1:
            person.append(el("FamilyName", parts[1]))
        stage.append(person)
    ship.append(stage)
    root.append(ship)
    for line in rec["lines"]:
        dl = el("DespatchLine")
        dl.append(el("ID", line.get("id") or ""))
        qty = el("DeliveredQuantity", line.get("qty") or "")
        if line.get("unit"):
            qty.set("unitCode", line["unit"])
        dl.append(qty)
        item = el("Item")
        item.append(el("Name", line.get("name") or ""))
        if line.get("sku"):
            sid = el("SellersItemIdentification")
            sid.append(el("ID", line["sku"]))
            item.append(sid)
        dl.append(item)
        root.append(dl)
    ET.indent(root, space="  ")
    dest.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode"),
        encoding="utf-8",
    )
    return dest


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    idx = IrsaliyeIndex()
    if args and args[0] in {"import", "al"}:
        if len(args) < 2:
            print("Kullanım: python irsaliye.py import <xml>")
            return 2
        dest = import_xml(Path(args[1]))
        print(dest if dest else "XML okunamadı")
        return 0 if dest else 1
    n = idx.refresh(force=True)
    print(f"{n} irsaliye · {len(idx.by_plate)} plaka · klasör: {idx.folder}")
    q = " ".join(args).strip()
    if q:
        hit = idx.lookup(q)
        print(json.dumps(hit, ensure_ascii=False, indent=2) if hit else "eşleşme yok")
        return 0 if hit else 1
    for rec in idx.docs:
        plates = ", ".join(format_plate(p) for p in rec.get("plates") or [])
        print(f"  {rec['id']}  {plates}  {rec.get('customer') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
